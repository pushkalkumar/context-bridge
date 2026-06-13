"""Tests for checkpoint type hierarchy (Task 2)."""
import time

import pytest

from server.memory import (
    classify_checkpoint_type,
    purge_old_scratch_checkpoints,
    save_checkpoint,
)
from tests.conftest import checkpoint_payload


@pytest.fixture
def no_llm(monkeypatch):
    monkeypatch.setattr("server.planner.settings.anthropic_api_key", None)
    monkeypatch.setattr("server.planner.settings.ollama_host", None)


def _mk_checkpoint(project_id="test-proj", task="Test task", cp_type="task", completed_at_ts=None):
    now_ms = completed_at_ts or int(time.time() * 1000)
    return {
        "project_id": project_id,
        "timestamp": "2026-01-01T12:00:00",
        "stagnation_count": 1,
        "event_type": "checkpoint",
        "checkpoint_type": cp_type,
        "completed_at_ts": now_ms,
        "task_duration_ms": None,
        "planner_confidence": None,
        "planner_blocker_class": None,
        "planner_decomposition_suggested": False,
        "user_goal": "Build something",
        "current_task": task,
        "progress_summary": "done",
        "current_state": {"files_modified": []},
        "blockers": [],
        "next_intended_action": "",
    }


# ── Classification logic ──────────────────────────────────────────────────────

def test_scratch_classification_small_diff():
    """4-line diff → scratch."""
    state = {"git_diff_stat": " main.py | 4 ++++\n 1 file changed, 4 insertions(+)"}
    assert classify_checkpoint_type(state) == "scratch"


def test_task_classification_large_diff():
    """15-line diff → task."""
    state = {"git_diff_stat": " auth.py | 15 +++++++++++++++\n 1 file changed, 15 insertions(+)"}
    assert classify_checkpoint_type(state) == "task"


def test_task_classification_new_file():
    """New file detected via git_name_status → task regardless of line count."""
    state = {
        "git_diff_stat": " newfile.py | 3 +++\n 1 file changed, 3 insertions(+)",
        "git_name_status": "A\tnewfile.py",
    }
    assert classify_checkpoint_type(state) == "task"


def test_session_type_on_stop_hook(client, no_llm):
    """Stop hook payload with checkpoint_type='session' → stored as session type."""
    payload = checkpoint_payload(
        current_task="End of session",
        checkpoint_type="session",
        project_id="sess-proj",
    )
    r = client.post("/checkpoint", json=payload)
    assert r.status_code == 200
    history = client.get("/history/sess-proj").json()
    assert history[0]["checkpoint_type"] == "session"


def test_explicit_hint_overrides_detection():
    """If checkpoint_type hint is set explicitly, detection is skipped."""
    state = {"git_diff_stat": " main.py | 4 ++++"}
    # Despite small diff, explicit 'task' hint wins
    assert classify_checkpoint_type(state, hint="task") == "task"
    # Explicit 'scratch' hint wins even with no diff
    assert classify_checkpoint_type({}, hint="scratch") == "scratch"


def test_default_task_when_no_diff():
    """No git diff info → defaults to 'task' (backward compat, ADR-003)."""
    assert classify_checkpoint_type({}) == "task"
    assert classify_checkpoint_type({"git_diff_stat": ""}) == "task"
    assert classify_checkpoint_type({"git_diff_stat": "(no uncommitted changes)"}) == "task"


# ── Purge ─────────────────────────────────────────────────────────────────────

def test_scratch_purge_removes_old(isolated_db):
    """Scratch checkpoint with ts 25h ago → deleted by purge."""
    old_ts_ms = int(time.time() * 1000) - 25 * 3600 * 1000
    save_checkpoint(_mk_checkpoint(cp_type="scratch", completed_at_ts=old_ts_ms))
    deleted = purge_old_scratch_checkpoints()
    assert deleted == 1


def test_scratch_purge_preserves_recent(isolated_db):
    """Scratch checkpoint with ts 12h ago → NOT deleted by purge."""
    recent_ts_ms = int(time.time() * 1000) - 12 * 3600 * 1000
    save_checkpoint(_mk_checkpoint(cp_type="scratch", completed_at_ts=recent_ts_ms))
    deleted = purge_old_scratch_checkpoints()
    assert deleted == 0


def test_purge_does_not_touch_task_checkpoints(isolated_db):
    """Task and session checkpoints are never purged, even when old."""
    old_ts_ms = int(time.time() * 1000) - 48 * 3600 * 1000
    save_checkpoint(_mk_checkpoint(cp_type="task", completed_at_ts=old_ts_ms))
    save_checkpoint(_mk_checkpoint(cp_type="session", completed_at_ts=old_ts_ms))
    deleted = purge_old_scratch_checkpoints()
    assert deleted == 0


# ── Stagnation exclusion ──────────────────────────────────────────────────────

def test_scratch_excluded_from_stagnation(client, no_llm):
    """3 identical scratch checkpoints do NOT trigger stagnation counting."""
    for _ in range(3):
        r = client.post("/checkpoint", json=checkpoint_payload(
            project_id="scratch-stag-proj",
            current_task="Small fix",
            checkpoint_type="scratch",
        ))
        assert r.status_code == 200
    # stagnation_count is based on the most recent non-scratch checkpoint
    # With 0 non-scratch checkpoints, count should be 1
    r = client.post("/checkpoint", json=checkpoint_payload(
        project_id="scratch-stag-proj",
        current_task="Small fix",
        checkpoint_type="task",
    ))
    body = r.json()
    assert body["stagnation_count"] == 1


# ── Velocity exclusion ────────────────────────────────────────────────────────

def test_scratch_excluded_from_velocity(client, no_llm):
    """Scratch checkpoints do not appear in velocity baseline."""
    now_ms = int(time.time() * 1000)
    # Insert 8 scratch checkpoints that would distort baseline if included
    for i in range(8):
        save_checkpoint(_mk_checkpoint(
            project_id="vel-scratch-proj",
            task="Tiny edit",
            cp_type="scratch",
            completed_at_ts=now_ms - i * 1_000,  # 1s apart — very fast
        ))
    # Insert 0 task checkpoints
    r = client.get("/velocity/vel-scratch-proj")
    # With no task checkpoints, velocity should return no alert (no baseline)
    assert r.status_code == 200
    body = r.json()
    assert body["alert"] is False
    # avg_duration_ms from task checkpoints should be None (scratch excluded)
    assert body["avg_duration_ms"] is None


# ── Type breakdown in project list ────────────────────────────────────────────

def test_projects_list_includes_type_breakdown(client, no_llm):
    """GET /projects returns type_breakdown per project."""
    client.post("/checkpoint", json=checkpoint_payload(project_id="typed-proj", checkpoint_type="task"))
    client.post("/checkpoint", json=checkpoint_payload(project_id="typed-proj", checkpoint_type="scratch"))
    client.post("/checkpoint", json=checkpoint_payload(project_id="typed-proj", checkpoint_type="session"))
    r = client.get("/projects")
    assert r.status_code == 200
    proj = next((p for p in r.json() if p["project_id"] == "typed-proj"), None)
    assert proj is not None
    bd = proj.get("type_breakdown", {})
    assert bd.get("task", 0) == 1
    assert bd.get("scratch", 0) == 1
    assert bd.get("session", 0) == 1
