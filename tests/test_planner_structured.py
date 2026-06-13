"""Tests for structured planner output (Task 3)."""
import json

import pytest

from server.models import SyncResponse
from server.planner import PlannerOutput, _classify_blocker, _rule_based, run_planner
from tests.conftest import checkpoint_payload


def _cp(**overrides):
    base = {
        "project_id": "test-project",
        "user_goal": "Build a REST API",
        "current_task": "Implement /login endpoint",
        "progress_summary": "FastAPI skeleton done",
        "current_state": {"files_modified": ["main.py"], "code_summary": "", "architecture_notes": ""},
        "blockers": [],
        "next_intended_action": "Write the /login handler",
        "stagnation_count": 1,
    }
    base.update(overrides)
    return base


@pytest.fixture
def no_llm(monkeypatch):
    monkeypatch.setattr("server.planner.settings.anthropic_api_key", None)
    monkeypatch.setattr("server.planner.settings.ollama_host", None)


# ── PlannerOutput shape ───────────────────────────────────────────────────────

def test_planner_output_shape_rule_based(no_llm):
    """Rule-based tier always returns valid PlannerOutput-equivalent fields on SyncResponse."""
    result = _rule_based(_cp(), [], stagnation_count=1)
    assert isinstance(result, SyncResponse)
    assert isinstance(result.confidence, float)
    assert 0.0 <= result.confidence <= 1.0
    assert isinstance(result.alternatives, list)
    assert isinstance(result.decomposition_suggested, bool)
    # blocker_class is a string or None
    assert result.blocker_class is None or isinstance(result.blocker_class, str)


def test_planner_output_shape_anthropic_tier(monkeypatch):
    """Mock Anthropic API response with valid JSON → SyncResponse has structured fields."""
    structured_response = json.dumps({
        "next_instruction": "Implement JWT auth middleware",
        "context_summary": "Auth API 30% done",
        "revised_plan": "1. Auth middleware\n2. /login\n3. GET /me",
        "priority_focus": "SECRET_KEY from env",
        "confidence": 0.91,
        "alternatives": ["Use sessions instead of JWT"],
        "blocker_class": "none",
        "decomposition_suggested": False,
    })

    class _FakeMsg:
        stop_reason = "end_turn"
        content = [type("C", (), {"text": structured_response})()]

    class _FakeClient:
        def __init__(self, *a, **kw): pass
        class messages:
            @staticmethod
            def create(**kw):
                return _FakeMsg()

    monkeypatch.setattr("server.planner.settings.anthropic_api_key", "sk-test")
    monkeypatch.setattr("server.planner.settings.ollama_host", None)
    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", _FakeClient)

    result = run_planner(_cp(), [], stagnation_count=1)
    assert result.confidence == pytest.approx(0.91)
    assert result.alternatives == ["Use sessions instead of JWT"]
    assert result.blocker_class == "none"
    assert result.decomposition_suggested is False


def test_planner_output_shape_ollama_tier(monkeypatch):
    """Mock Ollama API response with valid JSON → SyncResponse has structured fields."""
    structured_response = json.dumps({
        "next_instruction": "Add bcrypt hashing",
        "context_summary": "Auth partial",
        "revised_plan": "bcrypt → JWT → tests",
        "priority_focus": "Hashing first",
        "confidence": 0.78,
        "alternatives": [],
        "blocker_class": "unclear_spec",
        "decomposition_suggested": True,
    })

    class _FakeR:
        def raise_for_status(self): pass
        def json(self):
            return {"message": {"content": structured_response}}

    monkeypatch.setattr("server.planner.settings.anthropic_api_key", None)
    monkeypatch.setattr("server.planner.settings.ollama_host", "http://localhost:11434")
    import httpx
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: _FakeR())

    result = run_planner(_cp(), [], stagnation_count=1)
    assert result.confidence == pytest.approx(0.78)
    assert result.blocker_class == "unclear_spec"
    assert result.decomposition_suggested is True


def test_planner_json_parse_failure_falls_back(monkeypatch):
    """Anthropic returns malformed JSON → falls back to rule-based with confidence=0.3."""
    class _FakeMsg:
        stop_reason = "end_turn"
        content = [type("C", (), {"text": "This is NOT JSON at all!!!"})()]

    class _FakeClient:
        def __init__(self, *a, **kw): pass
        class messages:
            @staticmethod
            def create(**kw):
                return _FakeMsg()

    monkeypatch.setattr("server.planner.settings.anthropic_api_key", "sk-test")
    monkeypatch.setattr("server.planner.settings.ollama_host", None)
    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", _FakeClient)

    result = run_planner(_cp(), [], stagnation_count=1)
    assert result.confidence == pytest.approx(0.3)
    assert result.next_instruction  # still has an instruction from rule-based


# ── Stored columns ────────────────────────────────────────────────────────────

def test_planner_confidence_stored_in_checkpoint(client, no_llm):
    """After /sync, planner_confidence column is written and retrievable."""
    import sqlite3
    from server.memory import _DB
    r = client.post("/sync", json=checkpoint_payload())
    assert r.status_code == 200
    # Verify the column was written
    con = sqlite3.connect(_DB)
    row = con.execute("SELECT planner_confidence FROM checkpoints ORDER BY id DESC LIMIT 1").fetchone()
    con.close()
    assert row is not None
    assert row[0] is not None
    assert 0.0 <= row[0] <= 1.0


# ── Blocker class detection ───────────────────────────────────────────────────

def test_blocker_class_technical_debt_detection():
    """Same file in 3 consecutive diffs → blocker_class = 'technical_debt'."""
    repeated_file = "auth.py"
    history = [
        _cp(current_state={"files_modified": [repeated_file, "other1.py"]}),
        _cp(current_state={"files_modified": [repeated_file, "other2.py"]}),
        _cp(current_state={"files_modified": [repeated_file, "other3.py"]}),
    ]
    current = _cp(current_state={"files_modified": [repeated_file]})
    result = _classify_blocker(current, history)
    assert result == "technical_debt"


def test_blocker_class_dependency(no_llm):
    """Blocker text mentions 'waiting on' → blocker_class = 'dependency'."""
    cp = _cp(blockers=["waiting on external API team to unblock us"])
    result = _classify_blocker(cp, [])
    assert result == "dependency"


def test_blocker_class_unclear_spec(no_llm):
    """Blocker text mentions 'unclear' → blocker_class = 'unclear_spec'."""
    cp = _cp(blockers=["unclear what the acceptance criteria are"])
    result = _classify_blocker(cp, [])
    assert result == "unclear_spec"


def test_decomposition_flag_on_stagnation(no_llm):
    """Stagnation active (stagnation_count >= 3) → decomposition_suggested = True."""
    result = _rule_based(_cp(), [], stagnation_count=3)
    assert result.decomposition_suggested is True


def test_no_decomposition_without_stagnation(no_llm):
    """No stagnation, no scope creep blocker → decomposition_suggested = False."""
    result = _rule_based(_cp(), [], stagnation_count=1)
    assert result.decomposition_suggested is False


# ── Rule-based confidence ─────────────────────────────────────────────────────

def test_rule_based_confidence_high_without_stagnation(no_llm):
    result = _rule_based(_cp(), [], stagnation_count=1)
    assert result.confidence == pytest.approx(0.85)


def test_rule_based_confidence_low_with_stagnation(no_llm):
    result = _rule_based(_cp(), [], stagnation_count=3)
    assert result.confidence == pytest.approx(0.4)
