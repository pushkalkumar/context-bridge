"""Shared fixtures for all test modules."""
import pytest


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Each test gets a fresh SQLite database."""
    db = tmp_path / "test.db"
    monkeypatch.setattr("server.config.settings.db_path", db)
    monkeypatch.setattr("server.memory._DB", str(db))
    from server import memory
    memory.init_db()
    yield db


@pytest.fixture
def client(isolated_db):
    from server.main import app
    from server.memory import init_db
    from fastapi.testclient import TestClient
    init_db()
    with TestClient(app) as c:
        yield c


def checkpoint_payload(**overrides):
    base = {
        "user_goal": "Build a REST API",
        "current_task": "Implement /login endpoint",
        "progress_summary": "FastAPI skeleton done",
        "current_state": {"files_modified": ["main.py"]},
        "blockers": [],
        "next_intended_action": "Write /login handler",
        "project_id": "test-proj",
    }
    base.update(overrides)
    return base
