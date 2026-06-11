"""
Tests for the rule-based planner, stagnation detection, and response schema.
No API key required — all tests use the rule-based tier.
"""
import pytest

from server.models import SyncResponse
from server.planner import _rule_based, run_planner


def _cp(**overrides):
    base = {
        "project_id": "test-project",
        "user_goal": "Build a REST API",
        "current_task": "Implement /login endpoint",
        "progress_summary": "FastAPI skeleton done, /register works",
        "current_state": {"files_modified": ["main.py"], "code_summary": "", "architecture_notes": ""},
        "blockers": [],
        "next_intended_action": "Write the /login handler",
        "stagnation_count": 1,
    }
    base.update(overrides)
    return base


def test_rule_based_returns_sync_response():
    """Rule-based tier returns a valid SyncResponse with correct source."""
    result = _rule_based(_cp(), [], stagnation_count=1)
    assert isinstance(result, SyncResponse)
    assert result.source == "rule-based"
    assert result.next_instruction
    assert result.context_summary
    assert result.priority_focus
    assert result.stagnation_count == 1


def test_stagnation_triggers_at_count_3():
    """Planner overrides next_instruction when stagnation_count >= 3."""
    result = _rule_based(_cp(), [], stagnation_count=3)
    instruction_lower = result.next_instruction.lower()
    # Should mention breaking down or stagnation
    assert any(word in instruction_lower for word in ("break", "subtask", "3", "three", "consecutive"))
    assert result.stagnation_count == 3


def test_fallback_when_no_api_key(monkeypatch):
    """run_planner falls back to rule-based when no LLM is configured."""
    monkeypatch.setattr("server.planner.settings.anthropic_api_key", None)
    monkeypatch.setattr("server.planner.settings.ollama_host", None)
    result = run_planner(_cp(), [], stagnation_count=1)
    assert result.source == "rule-based"


def test_sync_response_validates():
    """SyncResponse validates all fields and rejects bad source literals."""
    r = SyncResponse(
        next_instruction="Implement /login using the JWT middleware in auth.py",
        context_summary="Auth API ~60% done. /register complete.",
        revised_plan="1. /login\n2. GET /me\n3. Token expiry",
        priority_focus="SECRET_KEY must come from env, never hardcoded",
        source="rule-based",
        stagnation_count=0,
    )
    assert r.source == "rule-based"
    assert r.stagnation_count == 0

    with pytest.raises(Exception):
        SyncResponse(
            next_instruction="x",
            context_summary="x",
            revised_plan="x",
            priority_focus="x",
            source="invalid-source",  # not a valid Literal
        )
