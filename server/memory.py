import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .config import settings

_DB = str(settings.db_path)


def _normalize(task: str) -> str:
    return " ".join(task.lower().split())


@contextmanager
def _conn():
    con = sqlite3.connect(_DB, check_same_thread=False)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA foreign_keys=ON")
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init_db() -> None:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    with _conn() as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS checkpoints (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id       TEXT NOT NULL,
                timestamp        TEXT NOT NULL,
                stagnation_count INTEGER NOT NULL DEFAULT 1,
                data             TEXT NOT NULL
            )
            """
        )
        con.execute("CREATE INDEX IF NOT EXISTS idx_project_ts ON checkpoints(project_id, timestamp DESC, id DESC)")


def compute_stagnation_count(project_id: str, new_task: str) -> int:
    """
    Returns the streak count for new_task.

    Count starts at 1 on first occurrence. Increments each time the same
    normalized task is submitted consecutively. Resets to 1 on task change.
    The planner flags stagnation at count >= 3.
    """
    prior = get_recent_checkpoints(project_id, n=1)
    if not prior:
        return 1
    prev = prior[0]
    if _normalize(new_task) == _normalize(prev.get("current_task", "")):
        return prev.get("stagnation_count", 1) + 1
    return 1


def save_checkpoint(data: dict) -> None:
    with _conn() as con:
        con.execute(
            "INSERT INTO checkpoints (project_id, timestamp, stagnation_count, data) VALUES (?, ?, ?, ?)",
            (
                data["project_id"],
                data["timestamp"],
                data.get("stagnation_count", 1),
                json.dumps(data),
            ),
        )


def delete_project(project_id: str) -> int:
    """Delete all checkpoints for a project. Returns the number of rows deleted."""
    with _conn() as con:
        cur = con.execute("DELETE FROM checkpoints WHERE project_id = ?", (project_id,))
        return cur.rowcount


def get_recent_checkpoints(project_id: str, n: int = 10) -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            """
            SELECT data FROM checkpoints
            WHERE project_id = ?
            ORDER BY timestamp DESC, id DESC
            LIMIT ?
            """,
            (project_id, n),
        ).fetchall()
    return [json.loads(r[0]) for r in rows]


def get_all_projects() -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            """
            SELECT
                project_id,
                COUNT(*) as checkpoint_count,
                MAX(timestamp) as last_active,
                MAX(stagnation_count) as max_stagnation
            FROM checkpoints
            GROUP BY project_id
            ORDER BY last_active DESC
            """
        ).fetchall()
    return [
        {
            "project_id": r[0],
            "checkpoint_count": r[1],
            "last_active": r[2],
            "stagnation_count": r[3],
        }
        for r in rows
    ]


def get_stats() -> dict:
    with _conn() as con:
        total_projects = con.execute("SELECT COUNT(DISTINCT project_id) FROM checkpoints").fetchone()[0]
        total_checkpoints = con.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0]
        stagnation_events = con.execute(
            "SELECT COUNT(*) FROM checkpoints WHERE stagnation_count >= 3"
        ).fetchone()[0]
    return {
        "total_projects": total_projects,
        "total_checkpoints": total_checkpoints,
        "stagnation_events": stagnation_events,
    }


def project_exists(project_id: str) -> bool:
    with _conn() as con:
        row = con.execute(
            "SELECT 1 FROM checkpoints WHERE project_id = ? LIMIT 1", (project_id,)
        ).fetchone()
    return row is not None
