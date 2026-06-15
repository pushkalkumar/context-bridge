"""Tests for build_attempt_replay (v0.7.0)."""
import pytest
from server.memory import build_attempt_replay, save_checkpoint


def _cp(project_id, task, files=None, blockers=None, planner_instr=None, i=0):
    data = {
        "project_id": project_id,
        "timestamp": f"2026-06-0{i+1}T12:00:00",
        "stagnation_count": i + 1,
        "user_goal": "Ship feature",
        "current_task": task,
        "progress_summary": "done",
        "current_state": {"files_modified": files or []},
        "blockers": blockers or [],
        "next_intended_action": "continue",
        "checkpoint_type": "task",
        "event_type": "checkpoint",
    }
    if planner_instr:
        data["_planner_output"] = {"next_instruction": planner_instr}
    save_checkpoint(data)


def test_empty_project_returns_empty(isolated_db):
    assert build_attempt_replay("no-proj/main") == []


def test_single_checkpoint_returns_one_entry(isolated_db):
    _cp("myrepo/main", "Fix login bug", i=0)
    result = build_attempt_replay("myrepo/main", "Fix login bug")
    assert len(result) == 1
    assert result[0]["attempt"] == 1


def test_multiple_attempts_ordered_oldest_first(isolated_db):
    for i in range(3):
        _cp("myrepo/main", "Fix login bug", blockers=[f"error {i}"], i=i)
    result = build_attempt_replay("myrepo/main", "Fix login bug")
    assert len(result) == 3
    attempts = [r["attempt"] for r in result]
    assert attempts == [1, 2, 3]


def test_only_matches_target_task(isolated_db):
    _cp("myrepo/main", "Fix login bug", i=0)
    _cp("myrepo/main", "Write tests", i=1)
    _cp("myrepo/main", "Fix login bug", i=2)
    result = build_attempt_replay("myrepo/main", "Fix login bug")
    assert len(result) == 2
    assert all(r["blockers"] is not None for r in result)


def test_scratch_checkpoints_excluded(isolated_db):
    data = {
        "project_id": "myrepo/main",
        "timestamp": "2026-06-01T12:00:00",
        "stagnation_count": 1,
        "user_goal": "Ship feature",
        "current_task": "Fix login bug",
        "progress_summary": "partial",
        "current_state": {"files_modified": []},
        "blockers": [],
        "next_intended_action": "continue",
        "checkpoint_type": "scratch",  # excluded
        "event_type": "checkpoint",
    }
    save_checkpoint(data)
    result = build_attempt_replay("myrepo/main", "Fix login bug")
    assert result == []


def test_files_and_blockers_populated(isolated_db):
    _cp("myrepo/main", "Fix login bug",
        files=["auth.py", "routes.py"],
        blockers=["ImportError: cannot import X"],
        i=0)
    result = build_attempt_replay("myrepo/main", "Fix login bug")
    assert result[0]["files_modified"] == ["auth.py", "routes.py"]
    assert result[0]["blockers"] == ["ImportError: cannot import X"]


def test_velocity_prefix_stripped_from_next_instruction(isolated_db):
    _cp("myrepo/main", "Fix login bug",
        planner_instr="⚠ VELOCITY ALERT\nLast 10 tasks...\nConsider: switch approach\nActual plan here",
        i=0)
    result = build_attempt_replay("myrepo/main", "Fix login bug")
    assert result[0]["next_instruction"] == "Actual plan here"


def test_task_matching_is_case_insensitive(isolated_db):
    _cp("myrepo/main", "Fix Login Bug", i=0)
    _cp("myrepo/main", "fix login bug", i=1)
    result = build_attempt_replay("myrepo/main", "Fix Login Bug")
    assert len(result) == 2


def test_uses_latest_task_when_no_task_arg(isolated_db):
    _cp("myrepo/main", "Deploy to prod", i=0)
    _cp("myrepo/main", "Deploy to prod", i=1)
    result = build_attempt_replay("myrepo/main")
    assert len(result) == 2
    assert result[0]["blockers"] == []


def test_respects_n_limit(isolated_db):
    for i in range(20):
        _cp("myrepo/main", "Fix login bug", i=i)
    result = build_attempt_replay("myrepo/main", "Fix login bug", n=5)
    assert len(result) <= 5
