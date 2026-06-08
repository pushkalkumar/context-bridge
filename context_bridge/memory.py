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
