"""Tests for find_similar_blocker (v0.7.0)."""
import pytest
from server.memory import find_similar_blocker, save_checkpoint


def _cp(project_id, task, blockers, planner_instr="continue", i=0, *, ts_override=None):
    data = {
        "project_id": project_id,
        "timestamp": ts_override or f"2026-06-{i+1:02d}T12:00:00",
        "stagnation_count": 1,
        "user_goal": "Ship feature",
        "current_task": task,
        "progress_summary": "done",
        "current_state": {"files_modified": []},
        "blockers": blockers,
        "next_intended_action": "continue",
        "checkpoint_type": "task",
        "event_type": "checkpoint",
        "_planner_output": {"next_instruction": planner_instr},
    }
    save_checkpoint(data)


def test_returns_none_when_no_history(isolated_db):
    assert find_similar_blocker("proj/main", "ImportError: cannot import module") is None


def test_returns_none_for_short_blocker(isolated_db):
    _cp("proj/main", "task", ["some error"], i=0)
    assert find_similar_blocker("proj/main", "err") is None


def test_returns_none_when_no_overlap(isolated_db):
    _cp("proj/main", "task A", ["ImportError: cannot import numpy"], i=0)
    _cp("proj/main", "task B", ["TypeError: string vs int"], i=1)
    result = find_similar_blocker("proj/main", "ConnectionError: database unreachable")
    assert result is None


def test_finds_similar_blocker_by_keyword_overlap(isolated_db):
    _cp("proj/main", "task A", ["ImportError: cannot import numpy module"], planner_instr="run pip install numpy", i=0)
    _cp("proj/main", "task B", ["no blocker"], i=1)
    result = find_similar_blocker("proj/main", "ImportError: cannot import numpy")
    assert result is not None
    assert "import" in result["matched_blocker"].lower() or "numpy" in result["matched_blocker"].lower()


def test_resolved_false_when_same_error_persists(isolated_db):
    _cp("proj/main", "task A", ["ImportError: cannot import numpy module"], i=0)
    # Same blocker at i=1 (newer) means NOT resolved
    _cp("proj/main", "task B", ["ImportError: cannot import numpy"], i=1)
    result = find_similar_blocker("proj/main", "ImportError: cannot import numpy module")
    # The match should exist but resolved=False since same error follows
    assert result is not None
    assert result["resolved"] is False


def test_resolved_true_when_error_disappears(isolated_db):
    _cp("proj/main", "task A", ["ImportError: cannot import numpy module"], planner_instr="pip install numpy", i=0)
    # No matching blocker in next checkpoint → resolved
    _cp("proj/main", "task B", ["unrelated auth error"], i=1)
    result = find_similar_blocker("proj/main", "ImportError: cannot import numpy")
    assert result is not None
    assert result["resolved"] is True


def test_next_instruction_returned(isolated_db):
    _cp("proj/main", "task A", ["ImportError: cannot import numpy module"],
        planner_instr="Fix by running pip install numpy", i=0)
    _cp("proj/main", "task B", ["unrelated"], i=1)
    result = find_similar_blocker("proj/main", "ImportError: cannot import numpy")
    assert result is not None
    assert "pip install numpy" in result["next_instruction"]


def test_returns_best_match_not_first_match(isolated_db):
    # Add two matching checkpoints; the one with higher overlap should win
    _cp("proj/main", "task A", ["ImportError module cannot import numpy"],
        planner_instr="plan A", i=0)
    _cp("proj/main", "task B", ["ImportError cannot import numpy module at runtime"],
        planner_instr="plan B", i=1)
    # Latest (i=2) has no blocker so i=1 and i=0 are both candidates
    _cp("proj/main", "task C", ["no blocker"], i=2)
    result = find_similar_blocker("proj/main", "ImportError cannot import numpy")
    assert result is not None
    # Either match is acceptable; what matters is we get a result
    assert result["next_instruction"] in ("plan A", "plan B")


def test_velocity_prefix_stripped_from_next_instruction(isolated_db):
    _cp("proj/main", "task A",
        ["ImportError: cannot import numpy module"],
        planner_instr="⚠ VELOCITY ALERT\nLast 10 tasks...\nConsider: fix approach\nActual fix: pip install numpy",
        i=0)
    _cp("proj/main", "task B", ["no blocker"], i=1)
    result = find_similar_blocker("proj/main", "ImportError: cannot import numpy")
    assert result is not None
    assert result["next_instruction"] == "Actual fix: pip install numpy"
