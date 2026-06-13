"""Tests for velocity tracking (Task 1)."""
import time

import pytest
from fastapi.testclient import TestClient

from server.memory import _velocity_ratio, get_velocity
from tests.conftest import checkpoint_payload


@pytest.fixture
def no_llm(monkeypatch):
    monkeypatch.setattr("server.planner.settings.anthropic_api_key", None)
    monkeypatch.setattr("server.planner.settings.ollama_host", None)


def _post_task(client, project_id, task, duration_ms=None, completed_at_ts=None):
    """Post a checkpoint with explicit timing for velocity tests."""
    payload = checkpoint_payload(
        project_id=project_id,
        current_task=task,
        checkpoint_type="task",
    )
    if completed_at_ts is not None:
        payload["completed_at_ts"] = completed_at_ts
    return client.post("/checkpoint", json=payload)


# ── Unit test of the math function ────────────────────────────────────────────

def test_velocity_ratio_computation():
    """Direct unit test of _velocity_ratio math, not the endpoint."""
    baseline = [60_000, 65_000, 55_000, 70_000, 60_000]  # avg = 62000
    ratio, alert, reason = _velocity_ratio(current_ms=186_000, baseline=baseline)
    assert ratio == pytest.approx(3.0, rel=0.05)
    assert alert is True
    assert "3.0x" in reason or "slower" in reason

    # Normal pace
    ratio2, alert2, _ = _velocity_ratio(current_ms=70_000, baseline=baseline)
    assert alert2 is False

    # Insufficient baseline
    ratio3, alert3, _ = _velocity_ratio(current_ms=999_000, baseline=[60_000, 70_000])
    assert ratio3 is None
    assert alert3 is False


# ── Endpoint tests ────────────────────────────────────────────────────────────

def test_velocity_no_alert_insufficient_history(client, no_llm):
    """Fewer than 5 task checkpoints → no alert."""
    now_ms = int(time.time() * 1000)
    for i in range(3):
        _post_task(client, "sparse-proj", f"Task {i}", completed_at_ts=now_ms - (i + 1) * 60_000)

    r = client.get("/velocity/sparse-proj")
    assert r.status_code == 200
    body = r.json()
    assert body["alert"] is False


def test_velocity_no_alert_normal_pace(client, no_llm):
    """8 checkpoints, current at 1.2x avg → no alert."""
    now_ms = int(time.time() * 1000)
    # Plant 9 checkpoints spaced ~60s apart (each has task_duration_ms≈60000)
    for i in range(9):
        ts = now_ms - (9 - i) * 60_000
        _post_task(client, "fast-proj", f"Task {i}", completed_at_ts=ts)

    # Current duration ≈ 72s (1.2x avg of 60s) — within the last checkpoint
    r = client.get("/velocity/fast-proj")
    assert r.status_code == 200
    body = r.json()
    # velocity_ratio should be ~1.2 (time since last checkpoint) — well below 2.0
    # The actual ratio depends on when we query; just confirm no alert
    assert body["alert"] is False


def test_velocity_alert_triggered(client, no_llm):
    """8 checkpoints with ~1min avg, current open for >3 min → alert fires."""
    now_ms = int(time.time() * 1000)
    # 9 checkpoints, each 60s apart, last one was 4 minutes ago
    for i in range(9):
        ts = now_ms - (9 - i) * 60_000 - 240_000  # shift all back 4 min
        _post_task(client, "slow-proj", f"Task {i}", completed_at_ts=ts)

    r = client.get("/velocity/slow-proj")
    assert r.status_code == 200
    body = r.json()
    # current_duration_ms ≈ 4 min = 240s, avg ≈ 60s → ratio ≈ 4x → alert
    assert body["alert"] is True
    assert body["velocity_ratio"] is not None
    assert body["velocity_ratio"] >= 2.0
    assert body["alert_reason"]


def test_velocity_endpoint_shape(client, no_llm):
    """GET /velocity returns all required keys."""
    # Even with no data, shape should be consistent
    from server.memory import save_checkpoint
    import json
    save_checkpoint({
        "project_id": "shape-proj",
        "timestamp": "2026-01-01T00:00:00",
        "stagnation_count": 1,
        "event_type": "checkpoint",
        "checkpoint_type": "task",
        "completed_at_ts": int(time.time() * 1000) - 30_000,
        "task_duration_ms": None,
        "planner_confidence": None,
        "planner_blocker_class": None,
        "planner_decomposition_suggested": False,
        "user_goal": "test",
        "current_task": "Task A",
        "progress_summary": "done",
        "current_state": {"files_modified": []},
        "blockers": [],
        "next_intended_action": "",
    })
    r = client.get("/velocity/shape-proj")
    assert r.status_code == 200
    body = r.json()
    for key in ("avg_duration_ms", "current_duration_ms", "velocity_ratio", "alert", "alert_reason"):
        assert key in body, f"missing key: {key}"


def test_velocity_404_for_unknown_project(client):
    """Velocity endpoint returns 404 for unknown projects."""
    r = client.get("/velocity/nonexistent-xyz")
    assert r.status_code == 404
