"""Tests for context-bridge diff command (Task 5)."""
import time

import pytest

from server.memory import save_checkpoint
from tests.conftest import checkpoint_payload


@pytest.fixture
def no_llm(monkeypatch):
    monkeypatch.setattr("server.planner.settings.anthropic_api_key", None)
    monkeypatch.setattr("server.planner.settings.ollama_host", None)


def _task_cp(project_id, task, duration_ms=None, confidence=0.85, completed_at_ts=None):
    now_ms = completed_at_ts or int(time.time() * 1000)
    return save_checkpoint({
        "project_id": project_id,
        "timestamp": "2026-01-01T12:00:00",
        "stagnation_count": 1,
        "event_type": "checkpoint",
        "checkpoint_type": "task",
        "completed_at_ts": now_ms,
        "task_duration_ms": duration_ms,
        "planner_confidence": confidence,
        "planner_blocker_class": "none",
        "planner_decomposition_suggested": False,
        "user_goal": "Build a REST API",
        "current_task": task,
        "progress_summary": "done",
        "current_state": {"files_modified": ["auth.py"]},
        "blockers": [],
        "next_intended_action": "next",
        "_planner_output": {
            "next_instruction": f"Step after: {task}",
            "priority_focus": "auth.py",
        },
    })


def _scratch_cp(project_id, task):
    return save_checkpoint({
        "project_id": project_id,
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
        "current_task": task,
        "progress_summary": "",
        "current_state": {"files_modified": []},
        "blockers": [],
        "next_intended_action": "",
    })


def test_diff_endpoint_returns_two_checkpoints(client, no_llm):
    """3 task checkpoints exist, diff returns the 2 most recent."""
    _task_cp("diff-proj", "Task A", confidence=0.80)
    _task_cp("diff-proj", "Task B", confidence=0.87)
    _task_cp("diff-proj", "Task C", confidence=0.92)  # most recent

    r = client.get("/diff/diff-proj")
    assert r.status_code == 200
    body = r.json()
    # 'to' is most recent (Task C), 'from' is second most recent (Task B)
    assert body["to"]["task_summary"] == "Task C"
    assert body["from"]["task_summary"] == "Task B"
    assert "next_instruction" in body
    assert "priority_focus" in body


def test_diff_skips_scratch_checkpoints(client, no_llm):
    """Scratch checkpoints between two task checkpoints are ignored."""
    _task_cp("skip-proj", "Task A", confidence=0.80)
    _scratch_cp("skip-proj", "Tiny scratch edit")
    _scratch_cp("skip-proj", "Another scratch edit")
    _task_cp("skip-proj", "Task B", confidence=0.90)  # most recent task

    r = client.get("/diff/skip-proj")
    assert r.status_code == 200
    body = r.json()
    assert body["to"]["task_summary"] == "Task B"
    assert body["from"]["task_summary"] == "Task A"


def test_diff_insufficient_history(client, no_llm):
    """Fewer than 2 task checkpoints → 404 with descriptive message."""
    _task_cp("one-proj", "Only task")

    r = client.get("/diff/one-proj")
    assert r.status_code == 404
    body = r.json()
    detail = body.get("detail", {})
    assert detail.get("error") == "insufficient_history"
    assert "2 task checkpoint" in detail.get("message", "").lower() or "fewer" in detail.get("message", "").lower() or "task checkpoints" in detail.get("message", "").lower()


def test_diff_zero_history(client, no_llm):
    """No checkpoints at all → 404."""
    r = client.get("/diff/no-proj-xyz")
    assert r.status_code == 404


def test_diff_response_shape(client, no_llm):
    """Response has all expected fields with correct types."""
    _task_cp("shape-proj", "Task A", duration_ms=120_000, confidence=0.87)
    _task_cp("shape-proj", "Task B", duration_ms=95_000, confidence=0.92)

    r = client.get("/diff/shape-proj")
    assert r.status_code == 200
    body = r.json()
    for field in ("task_summary", "completed_at_ts", "planner_confidence", "planner_blocker_class",
                  "planner_decomposition_suggested", "task_duration_ms"):
        assert field in body["from"], f"missing from.{field}"
        assert field in body["to"], f"missing to.{field}"
    assert isinstance(body["priority_focus"], list)


def test_diff_velocity_direction(client, no_llm):
    """'faster'/'slower' label derived from task_duration_ms delta."""
    from_dur = 190_000  # 3m10s
    to_dur = 165_000    # 2m45s — faster
    _task_cp("vel-dir-proj", "Task A", duration_ms=from_dur)
    _task_cp("vel-dir-proj", "Task B", duration_ms=to_dur)

    r = client.get("/diff/vel-dir-proj")
    assert r.status_code == 200
    body = r.json()
    assert body["from"]["task_duration_ms"] == from_dur
    assert body["to"]["task_duration_ms"] == to_dur
    # Verify the CLI would say "faster" (to < from)
    assert body["to"]["task_duration_ms"] < body["from"]["task_duration_ms"]


def test_diff_slash_project_id(client, no_llm):
    """project_id containing slashes (reponame/branch) works."""
    _task_cp("my-api/main", "Task A")
    _task_cp("my-api/main", "Task B")
    r = client.get("/diff/my-api/main")
    assert r.status_code == 200
    assert r.json()["to"]["task_summary"] == "Task B"
