"""
FastAPI endpoint tests using TestClient (no running server needed).
Tests cover the full HTTP contract — status codes, response shape, error envelopes.
"""
import json
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Each test gets a fresh SQLite database."""
    db = tmp_path / "test.db"
    monkeypatch.setattr("server.config.settings.db_path", db)
    monkeypatch.setattr("server.memory._DB", str(db))
    from server import memory
    memory.init_db()
    yield db


@pytest.fixture
def client(isolated_db):
    from server.main import app
    from server.memory import init_db
    init_db()
    with TestClient(app) as c:
        yield c


def _checkpoint_payload(**overrides):
    base = {
        "user_goal": "Build a REST API",
        "current_task": "Implement /login endpoint",
        "progress_summary": "FastAPI skeleton done",
        "current_state": {"files_modified": ["main.py"]},
        "blockers": [],
        "next_intended_action": "Write /login handler",
        "project_id": "test-proj",
    }
    base.update(overrides)
    return base


# ── Health ────────────────────────────────────────────────────────────────────

def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "context-bridge"


# ── POST /checkpoint ──────────────────────────────────────────────────────────

def test_checkpoint_returns_ack(client):
    r = client.post("/checkpoint", json=_checkpoint_payload())
    assert r.status_code == 200
    body = r.json()
    assert "project_id" in body
    assert "stagnation_count" in body
    assert body["stagnation_count"] == 1


def test_checkpoint_assigns_project_id_when_empty(client):
    payload = _checkpoint_payload()
    payload["project_id"] = ""
    r = client.post("/checkpoint", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["project_id"]


def test_checkpoint_missing_user_goal_fails(client):
    payload = _checkpoint_payload()
    del payload["user_goal"]
    r = client.post("/checkpoint", json=payload)
    assert r.status_code == 422


# ── POST /sync ────────────────────────────────────────────────────────────────

def test_sync_returns_plan(client, monkeypatch):
    monkeypatch.setattr("server.planner.settings.anthropic_api_key", None)
    monkeypatch.setattr("server.planner.settings.ollama_host", None)
    r = client.post("/sync", json=_checkpoint_payload())
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "rule-based"
    assert body["next_instruction"]
    assert body["context_summary"]
    assert body["priority_focus"]
    assert "stagnation_count" in body


def test_sync_stagnation_increments(client, monkeypatch):
    monkeypatch.setattr("server.planner.settings.anthropic_api_key", None)
    monkeypatch.setattr("server.planner.settings.ollama_host", None)
    payload = _checkpoint_payload()
    client.post("/sync", json=payload)
    r = client.post("/sync", json=payload)
    body = r.json()
    assert body["stagnation_count"] == 2


def test_sync_stagnation_resets_on_task_change(client, monkeypatch):
    monkeypatch.setattr("server.planner.settings.anthropic_api_key", None)
    monkeypatch.setattr("server.planner.settings.ollama_host", None)
    payload = _checkpoint_payload()
    client.post("/sync", json=payload)
    client.post("/sync", json=payload)
    payload2 = _checkpoint_payload(current_task="Implement GET /me endpoint")
    r = client.post("/sync", json=payload2)
    assert r.json()["stagnation_count"] == 1


# ── GET /history ──────────────────────────────────────────────────────────────

def test_history_returns_checkpoints(client, monkeypatch):
    monkeypatch.setattr("server.planner.settings.anthropic_api_key", None)
    monkeypatch.setattr("server.planner.settings.ollama_host", None)
    client.post("/sync", json=_checkpoint_payload())
    r = client.get("/history/test-proj")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    assert items[0]["current_task"] == "Implement /login endpoint"


def test_history_with_slash_project_id(client, monkeypatch):
    """Real project IDs are reponame/branch — path routing must accept slashes."""
    monkeypatch.setattr("server.planner.settings.anthropic_api_key", None)
    monkeypatch.setattr("server.planner.settings.ollama_host", None)
    client.post("/checkpoint", json=_checkpoint_payload(project_id="my-app/main"))
    r = client.get("/history/my-app/main")
    assert r.status_code == 200
    assert len(r.json()) == 1
    r2 = client.get("/projects/my-app/main/patterns")
    assert r2.status_code == 200
    assert r2.json()["project_id"] == "my-app/main"


def test_history_404_for_unknown_project(client):
    r = client.get("/history/nonexistent-project-xyz")
    assert r.status_code == 404
    body = r.json()
    # FastAPI wraps detail in {"detail": ...}
    assert body["detail"]["error"] == "not_found"


def test_history_limit_respected(client, monkeypatch):
    monkeypatch.setattr("server.planner.settings.anthropic_api_key", None)
    monkeypatch.setattr("server.planner.settings.ollama_host", None)
    for i in range(5):
        payload = _checkpoint_payload(current_task=f"Task {i}")
        client.post("/checkpoint", json=payload)
    r = client.get("/history/test-proj?limit=3")
    assert r.status_code == 200
    assert len(r.json()) == 3


# ── GET /projects ─────────────────────────────────────────────────────────────

def test_projects_lists_all(client, monkeypatch):
    monkeypatch.setattr("server.planner.settings.anthropic_api_key", None)
    monkeypatch.setattr("server.planner.settings.ollama_host", None)
    client.post("/checkpoint", json=_checkpoint_payload(project_id="proj-a"))
    client.post("/checkpoint", json=_checkpoint_payload(project_id="proj-b"))
    r = client.get("/projects")
    assert r.status_code == 200
    ids = {p["project_id"] for p in r.json()}
    assert "proj-a" in ids
    assert "proj-b" in ids


def test_projects_includes_stagnation_count(client, monkeypatch):
    monkeypatch.setattr("server.planner.settings.anthropic_api_key", None)
    monkeypatch.setattr("server.planner.settings.ollama_host", None)
    client.post("/checkpoint", json=_checkpoint_payload())
    r = client.get("/projects")
    assert r.status_code == 200
    item = r.json()[0]
    assert "stagnation_count" in item
    assert "checkpoint_count" in item
    assert "last_active" in item


# ── GET /stats ────────────────────────────────────────────────────────────────

def test_stats_empty_db(client):
    r = client.get("/stats")
    assert r.status_code == 200
    body = r.json()
    assert body["total_projects"] == 0
    assert body["total_checkpoints"] == 0
    assert body["stagnation_events"] == 0


def test_stats_counts_correctly(client, monkeypatch):
    monkeypatch.setattr("server.planner.settings.anthropic_api_key", None)
    monkeypatch.setattr("server.planner.settings.ollama_host", None)
    for _ in range(3):
        client.post("/checkpoint", json=_checkpoint_payload())
    r = client.get("/stats")
    body = r.json()
    assert body["total_projects"] == 1
    assert body["total_checkpoints"] == 3


# ── DELETE /projects ──────────────────────────────────────────────────────────

def test_delete_project(client, monkeypatch):
    monkeypatch.setattr("server.planner.settings.anthropic_api_key", None)
    monkeypatch.setattr("server.planner.settings.ollama_host", None)
    client.post("/checkpoint", json=_checkpoint_payload())
    r = client.delete("/projects/test-proj")
    assert r.status_code == 200
    assert r.json()["deleted"] >= 1
    r2 = client.get("/history/test-proj")
    assert r2.status_code == 404


def test_delete_nonexistent_project_404(client):
    r = client.delete("/projects/ghost-project")
    assert r.status_code == 404


# ── GET /projects/{id}/export ─────────────────────────────────────────────────

def test_export_returns_json_download(client, monkeypatch):
    monkeypatch.setattr("server.planner.settings.anthropic_api_key", None)
    monkeypatch.setattr("server.planner.settings.ollama_host", None)
    client.post("/checkpoint", json=_checkpoint_payload())
    r = client.get("/projects/test-proj/export")
    assert r.status_code == 200
    assert "attachment" in r.headers.get("content-disposition", "")
    data = r.json()
    assert isinstance(data, list)
    assert len(data) == 1


def test_export_filename_safe_for_slash_project_id(client, monkeypatch):
    monkeypatch.setattr("server.planner.settings.anthropic_api_key", None)
    monkeypatch.setattr("server.planner.settings.ollama_host", None)
    client.post("/checkpoint", json=_checkpoint_payload(project_id="my-app/main"))
    r = client.get("/projects/my-app/main/export")
    assert r.status_code == 200
    disposition = r.headers["content-disposition"]
    assert 'filename="context-bridge-my-app-main.json"' in disposition


def test_export_nonexistent_project_404(client):
    r = client.get("/projects/ghost-project/export")
    assert r.status_code == 404


# ── Event types ───────────────────────────────────────────────────────────────

def test_checkpoint_defaults_event_type(client):
    client.post("/checkpoint", json=_checkpoint_payload())
    r = client.get("/history/test-proj")
    assert r.json()[0]["event_type"] == "checkpoint"


def test_sync_accepts_adr_event(client, monkeypatch):
    monkeypatch.setattr("server.planner.settings.anthropic_api_key", None)
    monkeypatch.setattr("server.planner.settings.ollama_host", None)
    payload = _checkpoint_payload(
        event_type="adr",
        event_data={
            "decision": "Use JWT with HS256",
            "reason": "Stateless",
            "tradeoff": "No revocation without blocklist",
        },
    )
    r = client.post("/sync", json=payload)
    assert r.status_code == 200
    item = client.get("/history/test-proj").json()[0]
    assert item["event_type"] == "adr"
    assert item["event_data"]["decision"] == "Use JWT with HS256"


def test_checkpoint_rejects_invalid_event_type(client):
    r = client.post("/checkpoint", json=_checkpoint_payload(event_type="not-a-type"))
    assert r.status_code == 422


# ── GET /projects/{id}/stagnation-report ─────────────────────────────────────

def test_stagnation_report_404_for_unknown_project(client):
    r = client.get("/projects/ghost-project/stagnation-report")
    assert r.status_code == 404


def test_stagnation_report_root_cause(client, monkeypatch):
    monkeypatch.setattr("server.planner.settings.anthropic_api_key", None)
    monkeypatch.setattr("server.planner.settings.ollama_host", None)
    payload = _checkpoint_payload(blockers=["Auth architecture uncertainty"])
    for _ in range(3):
        client.post("/sync", json=payload)
    r = client.get("/projects/test-proj/stagnation-report")
    assert r.status_code == 200
    body = r.json()
    assert body["checkpoint_count"] == 3
    assert body["primary_blocker"] == "Auth architecture uncertainty"
    assert body["stuck_since"]
    assert body["recommendation"]
    assert body["elapsed_hours"] >= 0


def test_stagnation_report_elapsed_hours_with_aware_timestamp(client, monkeypatch):
    """Timezone-aware client timestamps must not skew elapsed_hours."""
    from datetime import datetime, timedelta, timezone

    monkeypatch.setattr("server.planner.settings.anthropic_api_key", None)
    monkeypatch.setattr("server.planner.settings.ollama_host", None)
    two_hours_ago = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    client.post("/checkpoint", json=_checkpoint_payload(timestamp=two_hours_ago))
    body = client.get("/projects/test-proj/stagnation-report").json()
    assert 1.9 <= body["elapsed_hours"] <= 2.1


def test_sync_includes_stagnation_report_at_count_3(client, monkeypatch):
    monkeypatch.setattr("server.planner.settings.anthropic_api_key", None)
    monkeypatch.setattr("server.planner.settings.ollama_host", None)
    payload = _checkpoint_payload()
    r1 = client.post("/sync", json=payload)
    r2 = client.post("/sync", json=payload)
    r3 = client.post("/sync", json=payload)
    assert r1.json()["stagnation_report"] is None
    assert r2.json()["stagnation_report"] is None
    report = r3.json()["stagnation_report"]
    assert report is not None
    assert report["checkpoint_count"] == 2  # the two prior checkpoints with this task
    assert report["recommendation"]


# ── GET /projects/{id}/patterns ───────────────────────────────────────────────

def test_patterns_404_for_unknown_project(client):
    r = client.get("/projects/ghost-project/patterns")
    assert r.status_code == 404


def test_patterns_detects_recurring_signals(client, monkeypatch):
    monkeypatch.setattr("server.planner.settings.anthropic_api_key", None)
    monkeypatch.setattr("server.planner.settings.ollama_host", None)
    for i in range(3):
        client.post("/checkpoint", json=_checkpoint_payload(
            current_task="Fix auth flow",
            current_state={"files_modified": ["auth.py", f"other_{i}.py"]},
            blockers=["Missing env var"] if i < 2 else [],
        ))
    r = client.get("/projects/test-proj/patterns")
    assert r.status_code == 200
    body = r.json()
    assert body["project_id"] == "test-proj"
    assert {"path": "auth.py", "count": 3} in body["hotspot_files"]
    assert not any(h["path"].startswith("other_") for h in body["hotspot_files"])
    assert {"text": "Missing env var", "count": 2} in body["recurring_blockers"]
    assert {"text": "Fix auth flow", "count": 3} in body["recurring_tasks"]


def test_patterns_empty_for_single_checkpoint(client, monkeypatch):
    monkeypatch.setattr("server.planner.settings.anthropic_api_key", None)
    monkeypatch.setattr("server.planner.settings.ollama_host", None)
    client.post("/checkpoint", json=_checkpoint_payload())
    body = client.get("/projects/test-proj/patterns").json()
    assert body["hotspot_files"] == []
    assert body["recurring_blockers"] == []
    assert body["recurring_tasks"] == []


# ── GET /profile ──────────────────────────────────────────────────────────────

def test_profile_empty_db(client):
    r = client.get("/profile")
    assert r.status_code == 200
    body = r.json()
    assert body["project_count"] == 0
    assert body["checkpoint_count"] == 0
    assert body["rejected_approaches"] == []


def test_profile_aggregates_across_projects(client, monkeypatch):
    monkeypatch.setattr("server.planner.settings.anthropic_api_key", None)
    monkeypatch.setattr("server.planner.settings.ollama_host", None)
    client.post("/checkpoint", json=_checkpoint_payload(
        project_id="proj-a",
        current_state={"files_modified": ["main.py", "app.tsx"]},
        blockers=["Missing env var"],
    ))
    client.post("/checkpoint", json=_checkpoint_payload(
        project_id="proj-b",
        current_state={
            "files_modified": ["api.py"],
            "architecture_notes": "FastAPI backend with SQLite storage",
        },
        blockers=["Missing env var"],
        event_type="adr",
        event_data={"decision": "FastAPI + SQLite", "reason": "simple", "tradeoff": "single-node"},
    ))
    client.post("/checkpoint", json=_checkpoint_payload(
        project_id="proj-b",
        current_state={"files_modified": []},
        event_type="failure",
        event_data={"attempted": "Celery with Redis broker", "failed_because": "ops complexity"},
    ))
    body = client.get("/profile").json()
    assert body["project_count"] == 2
    assert body["checkpoint_count"] == 3
    assert {"text": ".py", "count": 2} in body["top_file_types"]
    assert {"text": "Missing env var", "count": 2} in body["common_blockers"]
    techs = {t["text"] for t in body["tech_patterns"]}
    assert "fastapi" in techs
    assert "sqlite" in techs
    rejected = body["rejected_approaches"]
    assert len(rejected) == 1
    assert rejected[0]["attempted"] == "Celery with Redis broker"
    assert rejected[0]["project_id"] == "proj-b"


# ── Dashboard ─────────────────────────────────────────────────────────────────

def test_dashboard_returns_html(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert b"context-bridge" in r.content.lower() or b"Context Bridge" in r.content
