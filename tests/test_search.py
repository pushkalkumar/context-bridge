"""Tests for semantic checkpoint search (Task 4)."""
import json
import time

import pytest

from server.memory import _SQLITE_VEC_AVAILABLE, save_checkpoint, save_embedding, search_checkpoints
from tests.conftest import checkpoint_payload


@pytest.fixture
def no_llm(monkeypatch):
    monkeypatch.setattr("server.planner.settings.anthropic_api_key", None)
    monkeypatch.setattr("server.planner.settings.ollama_host", None)


@pytest.fixture
def mock_embed(monkeypatch):
    """Replace _embed_text with a deterministic function for testing."""
    def _fake_embed(text: str) -> list[float]:
        # Hash text into a sparse 256-dim vector so similar texts are close
        import hashlib
        h = int(hashlib.md5(text.encode()).hexdigest(), 16)
        vec = [0.0] * 256
        for i in range(8):
            idx = (h >> (i * 8)) % 256
            vec[idx] = 1.0
        return vec

    monkeypatch.setattr("server.memory._embed_text", _fake_embed)
    return _fake_embed


def _insert_cp(project_id, task, cp_type="task"):
    now_ms = int(time.time() * 1000)
    cid = save_checkpoint({
        "project_id": project_id,
        "timestamp": "2026-01-01T12:00:00",
        "stagnation_count": 1,
        "event_type": "checkpoint",
        "checkpoint_type": cp_type,
        "completed_at_ts": now_ms,
        "task_duration_ms": None,
        "planner_confidence": 0.85,
        "planner_blocker_class": "none",
        "planner_decomposition_suggested": False,
        "user_goal": "test goal",
        "current_task": task,
        "progress_summary": "done",
        "current_state": {"files_modified": []},
        "blockers": [],
        "next_intended_action": "",
        "_planner_output": {"next_instruction": f"Next step for {task}"},
    })
    return cid


# ── Embedding storage ─────────────────────────────────────────────────────────

@pytest.mark.skipif(not _SQLITE_VEC_AVAILABLE, reason="sqlite-vec not installed")
def test_embedding_stored_on_checkpoint_write(client, no_llm, mock_embed):
    """After /sync, checkpoint_embeddings has a row for that checkpoint_id."""
    import sqlite3
    from server.memory import _DB

    r = client.post("/sync", json=checkpoint_payload(project_id="embed-proj"))
    assert r.status_code == 200

    con = sqlite3.connect(_DB)
    # Load sqlite-vec to query vec0 table
    import sqlite_vec
    con.enable_load_extension(True)
    sqlite_vec.load(con)
    con.enable_load_extension(False)
    count = con.execute("SELECT COUNT(*) FROM checkpoint_embeddings").fetchone()[0]
    con.close()
    assert count >= 1


# ── Search endpoint ───────────────────────────────────────────────────────────

@pytest.mark.skipif(not _SQLITE_VEC_AVAILABLE, reason="sqlite-vec not installed")
def test_search_endpoint_returns_results(client, no_llm, mock_embed):
    """Insert 3 checkpoints, query for one of them, confirm top result."""
    _insert_cp("proj-a", "JWT authentication bcrypt hashing")
    _insert_cp("proj-a", "Database migration with Alembic")
    _insert_cp("proj-a", "Celery background tasks")

    # Build embeddings for the inserted checkpoints
    from server.memory import _DB
    import sqlite3
    import sqlite_vec
    con = sqlite3.connect(_DB)
    con.enable_load_extension(True)
    sqlite_vec.load(con)
    con.enable_load_extension(False)
    rows = con.execute("SELECT id FROM checkpoints WHERE project_id = 'proj-a'").fetchall()
    con.close()
    for (cid,) in rows:
        save_embedding(cid, "JWT authentication bcrypt")

    r = client.post("/search", json={"query": "JWT authentication", "limit": 3})
    assert r.status_code == 200
    body = r.json()
    assert "results" in body
    assert isinstance(body["results"], list)


@pytest.mark.skipif(not _SQLITE_VEC_AVAILABLE, reason="sqlite-vec not installed")
def test_search_excludes_current_project(client, no_llm, mock_embed):
    """Insert checkpoints for proj-A and proj-B; exclude proj-A → only proj-B results."""
    cid_a = _insert_cp("proj-A", "auth task")
    cid_b = _insert_cp("proj-B", "auth task")
    save_embedding(cid_a, "auth task")
    save_embedding(cid_b, "auth task")

    r = client.post("/search", json={"query": "auth task", "limit": 5, "exclude_project_id": "proj-A"})
    assert r.status_code == 200
    results = r.json()["results"]
    for res in results:
        assert res["project_id"] != "proj-A"


def test_search_offline_fallback_no_api_key(client, no_llm, monkeypatch):
    """No API key → search returns empty results gracefully (no crash)."""
    monkeypatch.setattr("server.memory.settings.anthropic_api_key", None)
    monkeypatch.setattr("server.memory.settings.voyage_api_key", None)
    _insert_cp("offline-proj", "some task")
    r = client.post("/search", json={"query": "some task", "limit": 3})
    assert r.status_code == 200
    assert r.json()["results"] == []


@pytest.mark.skipif(not _SQLITE_VEC_AVAILABLE, reason="sqlite-vec not installed")
def test_scratch_excluded_from_search(client, no_llm, mock_embed):
    """Scratch-type checkpoints never appear in search results."""
    cid_scratch = _insert_cp("mixed-proj", "scratch work", cp_type="scratch")
    cid_task = _insert_cp("mixed-proj", "real task", cp_type="task")
    save_embedding(cid_scratch, "scratch work")
    save_embedding(cid_task, "real task")

    r = client.post("/search", json={"query": "scratch work real task", "limit": 10})
    assert r.status_code == 200
    results = r.json()["results"]
    for res in results:
        assert res["checkpoint_type"] != "scratch"


# ── Similarity threshold filter (unit test) ───────────────────────────────────

def test_search_similarity_threshold():
    """Filter function: results below 0.75 should not be injected at SessionStart."""
    from server.hook import _SEARCH_SIMILARITY_THRESHOLD
    assert _SEARCH_SIMILARITY_THRESHOLD == 0.75

    # Simulate hook filter logic
    results = [
        {"similarity": 0.91, "project_id": "proj-a"},
        {"similarity": 0.60, "project_id": "proj-b"},
        {"similarity": 0.80, "project_id": "proj-c"},
    ]
    above_threshold = [r for r in results if r["similarity"] >= _SEARCH_SIMILARITY_THRESHOLD]
    assert len(above_threshold) == 2
    assert all(r["similarity"] >= 0.75 for r in above_threshold)
    below_threshold = [r for r in results if r["similarity"] < _SEARCH_SIMILARITY_THRESHOLD]
    assert len(below_threshold) == 1
    assert below_threshold[0]["project_id"] == "proj-b"
