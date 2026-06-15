"""Tests for PreToolUse stagnation warning (server/hook.py _on_pre_tool_use)."""
import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Load hook.py as a module (it's a pure-stdlib standalone script)
_HOOK_PATH = Path(__file__).resolve().parent.parent / "server" / "hook.py"


@pytest.fixture
def hook():
    spec = importlib.util.spec_from_file_location("hook_module", _HOOK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _pre_event(task: str) -> dict:
    return {
        "hook_event_name": "PreToolUse",
        "session_id": "test-session",
        "tool_name": "Task",
        "tool_input": {"description": task},
    }


# ── No-op paths ───────────────────────────────────────────────────────────────

def test_ignored_for_non_task_tool(hook, capsys):
    """Non-Task tools should never trigger a warning."""
    event = {"hook_event_name": "PreToolUse", "session_id": "s1",
              "tool_name": "Bash", "tool_input": {"command": "ls"}}
    with patch.object(hook, "_get", return_value=None):
        hook._on_pre_tool_use(event)
    assert capsys.readouterr().out == ""


def test_no_warning_when_task_is_new(hook, capsys):
    """Different incoming task (not in history) → no warning."""
    history = [{"current_task": "Write tests", "stagnation_count": 3,
                "blockers": [], "planner_blocker_class": "none", "_planner_output": {}}]
    with patch.object(hook, "_get", return_value=history), \
         patch.object(hook, "_read", return_value=""), \
         patch.object(hook, "_project_id", return_value="proj/main"):
        hook._on_pre_tool_use(_pre_event("Implement caching layer"))
    assert capsys.readouterr().out == ""


def test_no_warning_when_stagnation_count_low(hook, capsys):
    """Same task but stagnation_count == 1 → no warning (only 1 prior attempt)."""
    history = [{"current_task": "Implement /login", "stagnation_count": 1,
                "blockers": [], "planner_blocker_class": "none", "_planner_output": {}}]
    with patch.object(hook, "_get", return_value=history), \
         patch.object(hook, "_read", return_value=""), \
         patch.object(hook, "_project_id", return_value="proj/main"):
        hook._on_pre_tool_use(_pre_event("Implement /login"))
    assert capsys.readouterr().out == ""


def test_no_warning_when_history_empty(hook, capsys):
    """No history for project → no warning (cannot determine stagnation)."""
    with patch.object(hook, "_get", return_value=[]), \
         patch.object(hook, "_read", return_value=""), \
         patch.object(hook, "_project_id", return_value="proj/main"):
        hook._on_pre_tool_use(_pre_event("Implement /login"))
    assert capsys.readouterr().out == ""


def test_no_warning_when_backend_down(hook, capsys):
    """Backend unreachable → _get returns None → no warning, no crash."""
    with patch.object(hook, "_get", return_value=None), \
         patch.object(hook, "_read", return_value=""), \
         patch.object(hook, "_project_id", return_value="proj/main"):
        hook._on_pre_tool_use(_pre_event("Implement /login"))
    assert capsys.readouterr().out == ""


# ── Warning paths ─────────────────────────────────────────────────────────────

def test_warning_fired_at_stagnation_count_2(hook, capsys):
    """stagnation_count == 2 on latest checkpoint with matching task → warn."""
    history = [{
        "current_task": "Implement /login",
        "stagnation_count": 2,
        "blockers": ["error: cannot find module 'bcrypt'"],
        "planner_blocker_class": "technical_debt",
        "_planner_output": {"next_instruction": "Fix bcrypt first"},
    }]
    with patch.object(hook, "_get", return_value=history), \
         patch.object(hook, "_read", return_value=""), \
         patch.object(hook, "_project_id", return_value="proj/main"):
        hook._on_pre_tool_use(_pre_event("Implement /login"))

    out = capsys.readouterr().out
    assert "STAGNATION RISK" in out
    assert "2×" in out
    assert "bcrypt" in out
    assert "technical_debt" not in out  # mapped to human-readable label
    assert "same files" in out           # human-readable label for technical_debt
    assert "Fix bcrypt first" in out     # previous plan shown


def test_warning_includes_blocker_label(hook, capsys):
    """Each blocker class maps to a human-readable label in the warning."""
    for bc, expected_phrase in [
        ("dependency",    "blocked on something external"),
        ("unclear_spec",  "acceptance criteria are ambiguous"),
        ("scope_creep",   "grown beyond its original boundary"),
    ]:
        history = [{
            "current_task": "Write migration",
            "stagnation_count": 2,
            "blockers": [],
            "planner_blocker_class": bc,
            "_planner_output": {},
        }]
        with patch.object(hook, "_get", return_value=history), \
             patch.object(hook, "_read", return_value=""), \
             patch.object(hook, "_project_id", return_value="proj/main"):
            hook._on_pre_tool_use(_pre_event("Write migration"))

        out = capsys.readouterr().out
        assert expected_phrase in out, f"expected '{expected_phrase}' for blocker_class '{bc}'"


def test_warning_does_not_echo_velocity_alert_as_plan(hook, capsys):
    """Previous plan starting with '⚠' (velocity alert) is not echoed."""
    history = [{
        "current_task": "Run tests",
        "stagnation_count": 2,
        "blockers": [],
        "planner_blocker_class": "none",
        "_planner_output": {"next_instruction": "⚠ VELOCITY ALERT: ..."},
    }]
    with patch.object(hook, "_get", return_value=history), \
         patch.object(hook, "_read", return_value=""), \
         patch.object(hook, "_project_id", return_value="proj/main"):
        hook._on_pre_tool_use(_pre_event("Run tests"))

    out = capsys.readouterr().out
    assert "STAGNATION RISK" in out
    assert "VELOCITY ALERT" not in out  # should not be echoed as "Previous plan"


def test_warning_normalization_is_case_insensitive(hook, capsys):
    """Task matching is case- and whitespace-insensitive."""
    history = [{
        "current_task": "implement /Login endpoint",
        "stagnation_count": 2,
        "blockers": [],
        "planner_blocker_class": "none",
        "_planner_output": {},
    }]
    with patch.object(hook, "_get", return_value=history), \
         patch.object(hook, "_read", return_value=""), \
         patch.object(hook, "_project_id", return_value="proj/main"):
        hook._on_pre_tool_use(_pre_event("Implement /login endpoint"))

    out = capsys.readouterr().out
    assert "STAGNATION RISK" in out


# ── Main dispatcher ───────────────────────────────────────────────────────────

def test_main_dispatches_pre_tool_use(hook, monkeypatch, capsys):
    """hook.main() routes PreToolUse events to _on_pre_tool_use."""
    called = []

    def fake_pre(event):
        called.append(event)

    monkeypatch.setattr(hook, "_on_pre_tool_use", fake_pre)

    import json
    import io
    event = {"hook_event_name": "PreToolUse", "session_id": "s", "tool_name": "Task",
             "tool_input": {"description": "Write tests"}}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    hook.main()
    assert len(called) == 1
    assert called[0]["tool_name"] == "Task"
