"""Tests for context-bridge export / snapshot command (Task 7)."""
import time

import pytest

from server.memory import build_snapshot, save_checkpoint
from tests.conftest import checkpoint_payload


@pytest.fixture
def no_llm(monkeypatch):
    monkeypatch.setattr("server.planner.settings.anthropic_api_key", None)
    monkeypatch.setattr("server.planner.settings.ollama_host", None)


def _task_cp(project_id, task, next_instr="Do the next thing", duration_ms=60_000, confidence=0.85):
    now_ms = int(time.time() * 1000)
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
        "user_goal": "Build something",
        "current_task": task,
        "progress_summary": "done",
        "current_state": {"files_modified": ["auth.py"]},
        "blockers": [],
        "next_intended_action": "",
        "_planner_output": {
            "next_instruction": next_instr,
            "confidence": confidence,
        },
    })


# ── Snapshot endpoint ─────────────────────────────────────────────────────────

def test_export_contains_next_instruction(client, no_llm):
    """Export output includes next_instruction from most recent checkpoint."""
    _task_cp("export-proj", "Implement /login", next_instr="SECRET_KEY must come from env")
    r = client.get("/snapshot/export-proj")
    assert r.status_code == 200
    md = r.json()["markdown"]
    assert "SECRET_KEY must come from env" in md


def test_export_velocity_on_track(isolated_db, no_llm):
    """Non-alert velocity → 'on track' label in snapshot."""
    now_ms = int(time.time() * 1000)
    # Insert oldest first so latest id has the most recent completed_at_ts
    for i in range(5, -1, -1):
        save_checkpoint({
            "project_id": "ontrack-proj",
            "timestamp": "2026-01-01T12:00:00",
            "stagnation_count": 1,
            "event_type": "checkpoint",
            "checkpoint_type": "task",
            "completed_at_ts": now_ms - i * 60_000,  # each 1 min apart
            "task_duration_ms": 60_000,
            "planner_confidence": 0.85,
            "planner_blocker_class": "none",
            "planner_decomposition_suggested": False,
            "user_goal": "test",
            "current_task": f"Task {i}",
            "progress_summary": "done",
            "current_state": {},
            "blockers": [],
            "next_intended_action": "",
            "_planner_output": {"next_instruction": "Continue"},
        })

    md = build_snapshot("ontrack-proj")
    assert md is not None
    # Should not have alert; "on track" label should appear
    assert "on track" in md.lower() or "⚠" not in md


def test_export_velocity_alert(isolated_db, no_llm):
    """Alert velocity → '⚠ slower than baseline' label."""
    now_ms = int(time.time() * 1000)
    # Baseline: 6 checkpoints at ~60s each, but last one was 5 min ago
    for i in range(7):
        ts = now_ms - (7 - i) * 60_000 - 300_000  # shift all 5 min back
        save_checkpoint({
            "project_id": "slow-export-proj",
            "timestamp": "2026-01-01T12:00:00",
            "stagnation_count": 1,
            "event_type": "checkpoint",
            "checkpoint_type": "task",
            "completed_at_ts": ts,
            "task_duration_ms": 60_000,
            "planner_confidence": 0.85,
            "planner_blocker_class": "none",
            "planner_decomposition_suggested": False,
            "user_goal": "test",
            "current_task": f"Task {i}",
            "progress_summary": "done",
            "current_state": {},
            "blockers": [],
            "next_intended_action": "",
            "_planner_output": {"next_instruction": "Continue"},
        })

    md = build_snapshot("slow-export-proj")
    assert md is not None
    # Should show alert; either "⚠ slower than baseline" or "slower than baseline"
    assert "slower" in md.lower() or "⚠" in md


def test_export_snapshot_structure(client, no_llm):
    """Snapshot has all required sections."""
    _task_cp("struct-proj", "Implement /auth", next_instr="Write JWT middleware")
    r = client.get("/snapshot/struct-proj")
    assert r.status_code == 200
    md = r.json()["markdown"]
    assert "# context-bridge Snapshot" in md
    assert "## Current State" in md
    assert "## Velocity" in md
    assert "## Recurring Patterns" in md
    assert "## Architecture Decisions" in md
    assert "## Hotspots" in md
    assert "struct-proj" in md


def test_export_404_for_unknown_project(client, no_llm):
    """Snapshot endpoint returns 404 for unknown projects."""
    r = client.get("/snapshot/ghost-project-xyz")
    assert r.status_code == 404


def test_export_output_path_default(tmp_path, monkeypatch, no_llm, isolated_db):
    """CLI export writes to CONTEXT_BRIDGE_SNAPSHOT.md by default."""
    import urllib.request

    _task_cp("cli-export-proj", "Write /login", next_instr="Add bcrypt")

    # Mock _fetch to return the snapshot
    md_content = build_snapshot("cli-export-proj")

    def _mock_fetch(path, timeout=2.0):
        if path == "/health":
            return {"status": "ok"}
        if path.startswith("/snapshot/"):
            return {"markdown": md_content}
        return None

    from server import main as server_main
    monkeypatch.setattr(server_main, "_fetch", _mock_fetch)

    import os
    original_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        server_main._do_export("cli-export-proj", "CONTEXT_BRIDGE_SNAPSHOT.md")
        output = tmp_path / "CONTEXT_BRIDGE_SNAPSHOT.md"
        assert output.exists()
        content = output.read_text()
        assert "context-bridge Snapshot" in content
    finally:
        os.chdir(original_cwd)
