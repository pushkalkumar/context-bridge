import json
import sqlite3
from pathlib import Path

_DATA_DIR = Path.home() / ".context-bridge"
DB_PATH = str(_DATA_DIR / "context_bridge.db")


def init_db() -> None:
    _DATA_DIR.mkdir(exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS checkpoints (
                project_id TEXT NOT NULL,
                timestamp  TEXT NOT NULL,
                data       TEXT NOT NULL
            )
            """
        )
        conn.commit()


def save_checkpoint(checkpoint: dict) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO checkpoints (project_id, timestamp, data) VALUES (?, ?, ?)",
            (checkpoint["project_id"], checkpoint["timestamp"], json.dumps(checkpoint)),
        )
        conn.commit()


def get_recent_checkpoints(project_id: str, n: int = 10) -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT data FROM checkpoints
            WHERE project_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (project_id, n),
        ).fetchall()
    return [json.loads(row[0]) for row in rows]


def get_all_projects() -> list[dict]:
    """Return all project_ids with checkpoint count and latest timestamp, newest first."""
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT project_id, COUNT(*) as count, MAX(timestamp) as latest
            FROM checkpoints
            GROUP BY project_id
            ORDER BY latest DESC
            """
        ).fetchall()
    return [
        {"project_id": r[0], "checkpoint_count": r[1], "last_active": r[2]}
        for r in rows
    ]
