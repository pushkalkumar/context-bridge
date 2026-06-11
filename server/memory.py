import json
import sqlite3
from pathlib import Path

from .config import settings

_DB = str(settings.db_path)


def _normalize(task: str) -> str:
    return " ".join(task.lower().split())


def init_db() -> None:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(_DB) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS checkpoints (
                project_id      TEXT NOT NULL,
                timestamp       TEXT NOT NULL,
                stagnation_count INTEGER NOT NULL DEFAULT 0,
                data            TEXT NOT NULL
            )
            """
        )
        conn.commit()


def compute_stagnation_count(project_id: str, new_task: str) -> int:
    """
    Returns the streak count for new_task in this project.

    Count starts at 1 on first occurrence; increments each time the same
    normalized task is submitted consecutively; resets to 1 on task change.
    Stagnation is flagged by the planner when count >= 3.
    """
    prior = get_recent_checkpoints(project_id, n=1)
    if not prior:
        return 1
    prev = prior[0]
    if _normalize(new_task) == _normalize(prev.get("current_task", "")):
        return prev.get("stagnation_count", 1) + 1
    return 1


def save_checkpoint(data: dict) -> None:
    with sqlite3.connect(_DB) as conn:
        conn.execute(
            "INSERT INTO checkpoints (project_id, timestamp, stagnation_count, data) VALUES (?, ?, ?, ?)",
            (
                data["project_id"],
                data["timestamp"],
                data.get("stagnation_count", 1),
                json.dumps(data),
            ),
        )
        conn.commit()


def get_recent_checkpoints(project_id: str, n: int = 10) -> list[dict]:
    with sqlite3.connect(_DB) as conn:
        rows = conn.execute(
            """
            SELECT data FROM checkpoints
            WHERE project_id = ?
            ORDER BY timestamp DESC, rowid DESC
            LIMIT ?
            """,
            (project_id, n),
        ).fetchall()
    return [json.loads(r[0]) for r in rows]


def get_all_projects() -> list[dict]:
    with sqlite3.connect(_DB) as conn:
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
