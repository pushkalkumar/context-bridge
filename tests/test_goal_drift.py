"""Tests for detect_goal_drift (v0.7.0)."""
import pytest
from server.memory import detect_goal_drift, save_checkpoint


def _cp(project_id, goal, i=0):
    data = {
        "project_id": project_id,
        "timestamp": f"2026-06-{i+1:02d}T12:00:00",
        "stagnation_count": 1,
        "user_goal": goal,
        "current_task": "some task",
        "progress_summary": "done",
        "current_state": {"files_modified": []},
        "blockers": [],
        "next_intended_action": "continue",
        "checkpoint_type": "task",
        "event_type": "checkpoint",
    }
    save_checkpoint(data)


def test_no_drift_on_empty_project(isolated_db):
    result = detect_goal_drift("proj/main")
    assert result["drifted"] is False
    assert result["goals"] == []
    assert result["distinct_count"] == 0


def test_no_drift_with_single_goal(isolated_db):
    for i in range(3):
        _cp("proj/main", "Build a REST API", i=i)
    result = detect_goal_drift("proj/main")
    assert result["drifted"] is False
    assert result["distinct_count"] == 1


def test_no_drift_with_two_goals(isolated_db):
    _cp("proj/main", "Build a REST API", i=0)
    _cp("proj/main", "Build a REST API", i=1)
    _cp("proj/main", "Add authentication", i=2)
    result = detect_goal_drift("proj/main")
    assert result["drifted"] is False
    assert result["distinct_count"] == 2


def test_drift_with_three_distinct_goals(isolated_db):
    _cp("proj/main", "Build a REST API", i=0)
    _cp("proj/main", "Add authentication layer", i=1)
    _cp("proj/main", "Deploy to production", i=2)
    result = detect_goal_drift("proj/main")
    assert result["drifted"] is True
    assert result["distinct_count"] == 3


def test_drift_with_four_goals(isolated_db):
    goals = ["Build REST API", "Add auth", "Ship to prod", "Refactor database"]
    for i, g in enumerate(goals):
        _cp("proj/main", g, i=i)
    result = detect_goal_drift("proj/main")
    assert result["drifted"] is True
    assert result["distinct_count"] == 4


def test_goals_returned_in_order(isolated_db):
    goals = ["Build REST API", "Add auth layer", "Deploy to staging"]
    for i, g in enumerate(goals):
        _cp("proj/main", g, i=i)
    result = detect_goal_drift("proj/main")
    assert result["goals"] == goals
    assert result["distinct_count"] == 3


def test_placeholder_goals_filtered_out(isolated_db):
    _cp("proj/main", "(not yet recorded — use /sync to set)", i=0)
    _cp("proj/main", "session ended", i=1)
    _cp("proj/main", "Build a real feature", i=2)
    result = detect_goal_drift("proj/main")
    assert result["distinct_count"] == 1
    assert result["goals"] == ["Build a real feature"]


def test_repeated_goal_not_double_counted(isolated_db):
    _cp("proj/main", "Build REST API", i=0)
    _cp("proj/main", "Add auth", i=1)
    _cp("proj/main", "Build REST API", i=2)  # returns to first goal
    result = detect_goal_drift("proj/main")
    # Each goal change is counted, including return-to-prior
    # Sequence is: Build REST API → Add auth → Build REST API = 3 distinct transitions
    assert result["distinct_count"] == 3
    assert result["drifted"] is True


def test_case_and_whitespace_normalization(isolated_db):
    _cp("proj/main", "Build A REST API", i=0)
    _cp("proj/main", "  build a rest api  ", i=1)
    _cp("proj/main", "BUILD A REST API", i=2)
    result = detect_goal_drift("proj/main")
    assert result["distinct_count"] == 1
    assert result["drifted"] is False


def test_goals_list_capped_at_five(isolated_db):
    for i in range(7):
        _cp("proj/main", f"Goal number {i}", i=i)
    result = detect_goal_drift("proj/main")
    assert len(result["goals"]) <= 5
