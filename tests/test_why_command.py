"""Tests for the `context-bridge why` command (do_why)."""
import pytest

from server.cli import do_why


@pytest.fixture
def no_llm(monkeypatch):
    monkeypatch.setattr("server.planner.settings.anthropic_api_key", None)
    monkeypatch.setattr("server.planner.settings.ollama_host", None)


def _stub_fetch(responses: dict):
    """Return a _fetch replacement that maps path prefixes to canned values."""
    def _fetch(path: str, timeout: float = 2.0):
        for prefix, val in responses.items():
            if path.startswith(prefix):
                return val
        return None
    return _fetch


def test_why_no_stagnation(monkeypatch, capsys):
    """No stagnation: shows 'progressing normally' and velocity data."""
    monkeypatch.setattr("server.cli._fetch", _stub_fetch({
        "/health": {"status": "ok", "port": 7723},
        "/history/": [{"stagnation_count": 1, "current_task": "Write tests"}],
        "/velocity/": {
            "avg_duration_ms": 420_000,
            "current_duration_ms": 380_000,
            "velocity_ratio": 0.9,
            "alert": False,
        },
    }))
    do_why()
    out = capsys.readouterr().out
    assert "No stagnation" in out
    assert "on track" in out


def test_why_approaching_stagnation(monkeypatch, capsys):
    """stagnation_count == 2: shows 'approaching stagnation' warning."""
    monkeypatch.setattr("server.cli._fetch", _stub_fetch({
        "/health": {"status": "ok", "port": 7723},
        "/history/": [{"stagnation_count": 2, "current_task": "Implement /login"}],
        "/velocity/": {"avg_duration_ms": None, "current_duration_ms": None, "alert": False},
    }))
    do_why()
    out = capsys.readouterr().out
    assert "APPROACHING STAGNATION" in out
    assert "/login" in out
    assert "forced decomposition" in out


def test_why_full_stagnation(monkeypatch, capsys):
    """stagnation_count >= 3: shows full stagnation report with blocker and action."""
    monkeypatch.setattr("server.cli._fetch", _stub_fetch({
        "/health": {"status": "ok", "port": 7723},
        "/history/": [{"stagnation_count": 3, "current_task": "Implement /login"}],
        "/projects/": {
            "elapsed_hours": 4.2,
            "checkpoint_count": 3,
            "primary_blocker": "bcrypt import error",
            "recommendation": "Break this into smaller tasks.",
        },
        "/velocity/": {
            "avg_duration_ms": 420_000,
            "current_duration_ms": 1_100_000,
            "velocity_ratio": 2.6,
            "alert": True,
        },
    }))
    do_why()
    out = capsys.readouterr().out
    assert "STAGNATION DETECTED" in out
    assert "bcrypt import error" in out
    assert "Break this into smaller tasks" in out
    assert "slower than baseline" in out
    assert "2.6×" in out


def test_why_backend_not_running(monkeypatch, capsys):
    """Backend down: shows clear error message."""
    monkeypatch.setattr("server.cli._fetch", _stub_fetch({"/health": None}))
    do_why()
    out = capsys.readouterr().out
    assert "not running" in out


def test_why_velocity_insufficient_history(monkeypatch, capsys):
    """Fewer than 5 tasks: shows insufficient history message."""
    monkeypatch.setattr("server.cli._fetch", _stub_fetch({
        "/health": {"status": "ok", "port": 7723},
        "/history/": [{"stagnation_count": 1, "current_task": "Write tests"}],
        "/velocity/": {"avg_duration_ms": None, "current_duration_ms": None, "alert": False},
    }))
    do_why()
    out = capsys.readouterr().out
    assert "insufficient history" in out
