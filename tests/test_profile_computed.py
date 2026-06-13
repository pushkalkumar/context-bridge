"""Tests for computed developer profile (Task 6)."""
import time

import pytest

from server.memory import build_profile, save_checkpoint
from tests.conftest import checkpoint_payload


@pytest.fixture
def no_llm(monkeypatch):
    monkeypatch.setattr("server.planner.settings.anthropic_api_key", None)
    monkeypatch.setattr("server.planner.settings.ollama_host", None)


def _task_cp(project_id, task, files=None, blocker_class="none", duration_ms=None):
    now_ms = int(time.time() * 1000)
    return save_checkpoint({
        "project_id": project_id,
        "timestamp": "2026-01-01T12:00:00",
        "stagnation_count": 1,
        "event_type": "checkpoint",
        "checkpoint_type": "task",
        "completed_at_ts": now_ms,
        "task_duration_ms": duration_ms,
        "planner_confidence": 0.85,
        "planner_blocker_class": blocker_class,
        "planner_decomposition_suggested": False,
        "user_goal": "test",
        "current_task": task,
        "progress_summary": "done",
        "current_state": {"files_modified": files or []},
        "blockers": [],
        "next_intended_action": "",
    })


# ── Profile endpoint ──────────────────────────────────────────────────────────

def test_profile_empty_database(client, no_llm):
    """Zero checkpoints → returns zeros/empty arrays, no crash."""
    r = client.get("/profile")
    assert r.status_code == 200
    body = r.json()
    assert body["project_count"] == 0
    assert body["checkpoint_count"] == 0
    assert body["rejected_approaches"] == []
    assert body["avg_task_velocity_ms"] is None
    assert body["total_task_checkpoints"] == 0


def test_profile_preferred_stack_extraction(client, no_llm):
    """Insert checkpoints with Python + TypeScript files → detected in preferred_stack."""
    _task_cp("stack-proj", "Add auth", files=["auth.py", "models.py", "router.py"])
    _task_cp("stack-proj", "Add frontend", files=["app.tsx", "components.tsx"])
    _task_cp("stack-proj", "Add SQL schema", files=["schema.sql"])

    r = client.get("/profile")
    assert r.status_code == 200
    body = r.json()
    stack = body.get("preferred_stack", [])
    # .py files should map to Python, .tsx to TypeScript, .sql to SQLite
    assert "Python" in stack
    assert "TypeScript" in stack


def test_profile_recurring_blockers_aggregation(client, no_llm):
    """Insert checkpoints with known blocker_class values → aggregated correctly."""
    _task_cp("blocker-proj-a", "Task 1", blocker_class="technical_debt")
    _task_cp("blocker-proj-a", "Task 2", blocker_class="technical_debt")
    _task_cp("blocker-proj-b", "Task 3", blocker_class="unclear_spec")
    _task_cp("blocker-proj-b", "Task 4", blocker_class="technical_debt")

    r = client.get("/profile")
    assert r.status_code == 200
    body = r.json()
    blocker_classes = {bc["text"]: bc["count"] for bc in body.get("recurring_blocker_classes", [])}
    assert blocker_classes.get("technical_debt", 0) == 3
    assert blocker_classes.get("unclear_spec", 0) == 1


def test_profile_velocity_computed(client, no_llm):
    """avg_task_velocity_ms is the mean of task_duration_ms values."""
    _task_cp("vel-proj", "Task A", duration_ms=60_000)
    _task_cp("vel-proj", "Task B", duration_ms=90_000)
    _task_cp("vel-proj", "Task C", duration_ms=30_000)

    r = client.get("/profile")
    assert r.status_code == 200
    body = r.json()
    avg = body.get("avg_task_velocity_ms")
    assert avg is not None
    assert avg == pytest.approx(60_000, rel=0.01)  # (60+90+30)/3 = 60000


def test_profile_total_task_checkpoints(client, no_llm):
    """total_task_checkpoints only counts task-type checkpoints."""
    _task_cp("count-proj", "Task A")
    _task_cp("count-proj", "Task B")
    # Scratch and session don't count
    save_checkpoint({
        "project_id": "count-proj",
        "timestamp": "2026-01-01T12:00:00",
        "stagnation_count": 1,
        "event_type": "checkpoint",
        "checkpoint_type": "scratch",
        "completed_at_ts": int(time.time() * 1000),
        "task_duration_ms": None,
        "planner_confidence": None,
        "planner_blocker_class": None,
        "planner_decomposition_suggested": False,
        "user_goal": "test",
        "current_task": "Scratch edit",
        "progress_summary": "",
        "current_state": {},
        "blockers": [],
        "next_intended_action": "",
    })

    r = client.get("/profile")
    assert r.status_code == 200
    body = r.json()
    assert body["total_task_checkpoints"] == 2


def test_profile_injected_on_new_project(monkeypatch, no_llm):
    """SessionStart with unknown project → _profile_lines() returns content."""
    from server import hook

    call_log = {}

    def _mock_get(path: str):
        call_log[path] = True
        if path == "/health":
            return {"status": "ok"}
        if path.startswith("/history/"):
            return []  # no history = new project
        if path == "/profile":
            return {
                "checkpoint_count": 47,
                "total_task_checkpoints": 47,
                "total_projects": 3,
                "project_count": 3,
                "preferred_stack": ["Python", "TypeScript"],
                "tech_patterns": [],
                "recurring_blocker_classes": [{"text": "technical_debt", "count": 4}],
                "common_blockers": [],
                "avg_task_velocity_ms": 142_000,
                "rejected_approaches": [],
            }
        return None

    monkeypatch.setattr(hook, "_get", _mock_get)
    monkeypatch.setattr(hook, "_project_id", lambda: "new-project")

    lines = hook._profile_lines()
    combined = "\n".join(lines)
    assert "DEVELOPER PROFILE" in combined or "Preferred stack" in combined
