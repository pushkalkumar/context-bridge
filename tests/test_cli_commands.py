"""CLI sync/event commands: payload builders, validation, and dispatch."""
import pytest

from server import cli


# ── build_sync_payload ────────────────────────────────────────────────────────

def test_build_sync_payload_has_required_fields():
    payload = cli.build_sync_payload(
        "repo/main", "Ship auth", "Implement /login", "register done",
        "fix bcrypt import", ["error: no module bcrypt"], {},
    )
    assert payload["project_id"] == "repo/main"
    assert payload["user_goal"] == "Ship auth"
    assert payload["current_task"] == "Implement /login"
    assert payload["progress_summary"] == "register done"
    assert payload["next_intended_action"] == "fix bcrypt import"
    assert payload["blockers"] == ["error: no module bcrypt"]
    assert payload["timestamp"]


def test_build_sync_payload_parses_files_from_diff_stat():
    git_meta = {"git_diff_stat": " auth.py | 12 +++---\n main.py | 3 +-\n 2 files changed"}
    payload = cli.build_sync_payload("p/m", "g", "t", "p", "", [], git_meta)
    assert payload["current_state"]["files_modified"] == ["auth.py", "main.py"]
    assert payload["current_state"]["git_diff_stat"] == git_meta["git_diff_stat"]


# ── build_event_payload ───────────────────────────────────────────────────────

def test_build_event_payload_failure():
    payload = cli.build_event_payload(
        "failure", "repo/main",
        {"attempted": "session cookies", "because": "stateless requirement"},
        "Ship auth", "Implement /login",
    )
    assert payload["event_type"] == "failure"
    assert payload["event_data"]["attempted"] == "session cookies"
    assert payload["progress_summary"] == "Abandoned approach: session cookies"
    assert payload["user_goal"] == "Ship auth"


def test_build_event_payload_adr_and_outcome_summaries():
    adr = cli.build_event_payload("adr", "p", {"decision": "HS256"}, "g", "t")
    outcome = cli.build_event_payload("outcome", "p", {"result": "tests green"}, "g", "t")
    assert adr["progress_summary"] == "Decision: HS256"
    assert outcome["progress_summary"] == "Result: tests green"


# ── do_event validation ───────────────────────────────────────────────────────

def test_do_event_missing_required_flags_exits_before_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("must not touch backend on validation failure")
    monkeypatch.setattr(cli, "ensure_backend", boom)
    with pytest.raises(SystemExit) as exc:
        cli.do_event("failure", {"attempted": "x", "because": ""}, "", "")
    assert exc.value.code == 2


def test_do_event_filters_flags_to_kind(monkeypatch, capsys):
    captured = {}
    monkeypatch.setattr(cli, "ensure_backend", lambda: True)
    monkeypatch.setattr(cli, "current_pid", lambda: "repo/main")
    monkeypatch.setattr(cli, "_fetch", lambda *a, **k: [])
    monkeypatch.setattr(cli, "_post_json", lambda path, payload: captured.update(payload) or {"ok": True})

    cli.do_event(
        "failure",
        {"attempted": "x", "because": "y", "decision": "leak", "result": "leak"},
        "goal", "task",
    )
    assert captured["event_data"] == {"attempted": "x", "because": "y"}
    assert "Recorded failure event" in capsys.readouterr().out


def test_do_event_defaults_goal_task_from_history(monkeypatch):
    captured = {}
    monkeypatch.setattr(cli, "ensure_backend", lambda: True)
    monkeypatch.setattr(cli, "current_pid", lambda: "repo/main")
    monkeypatch.setattr(
        cli, "_fetch",
        lambda *a, **k: [{"user_goal": "Ship auth", "current_task": "Implement /login"}],
    )
    monkeypatch.setattr(cli, "_post_json", lambda path, payload: captured.update(payload) or {"ok": True})

    cli.do_event("outcome", {"result": "green", "impact": "done"}, "", "")
    assert captured["user_goal"] == "Ship auth"
    assert captured["current_task"] == "Implement /login"


# ── do_sync dispatch ──────────────────────────────────────────────────────────

def test_do_sync_posts_and_prints_plan(monkeypatch, capsys):
    captured = {}
    monkeypatch.setattr(cli, "ensure_backend", lambda: True)
    monkeypatch.setattr(cli, "current_pid", lambda: "repo/main")
    monkeypatch.setattr(cli, "_git_meta", lambda: {})
    monkeypatch.setattr(
        cli, "_post_json",
        lambda path, payload: captured.update({"path": path, **payload}) or {
            "next_instruction": "Fix the bcrypt import",
            "priority_focus": "env-based secrets",
            "source": "rule-based",
            "stagnation_count": 2,
            "confidence": 0.9,
            "decomposition_suggested": True,
        },
    )

    cli.do_sync("Ship auth", "Implement /login", "register done", "fix import", ["err"])

    assert captured["path"] == "/sync"
    assert captured["current_task"] == "Implement /login"
    out = capsys.readouterr().out
    assert "Fix the bcrypt import" in out
    assert "env-based secrets" in out
    assert "Decompose" in out


def test_do_sync_backend_down_exits(monkeypatch):
    monkeypatch.setattr(cli, "ensure_backend", lambda: False)
    with pytest.raises(SystemExit) as exc:
        cli.do_sync("g", "t", "p", "", [])
    assert exc.value.code == 1
