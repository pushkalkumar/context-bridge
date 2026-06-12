import json
import sqlite3
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
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
                event_type       TEXT NOT NULL DEFAULT 'checkpoint',
                data             TEXT NOT NULL
            )
            """
        )
        cols = {row[1] for row in con.execute("PRAGMA table_info(checkpoints)")}
        if "id" not in cols:
            con.execute("ALTER TABLE checkpoints ADD COLUMN id INTEGER")
            con.execute("UPDATE checkpoints SET id = rowid WHERE id IS NULL")
        if "event_type" not in cols:
            con.execute("ALTER TABLE checkpoints ADD COLUMN event_type TEXT NOT NULL DEFAULT 'checkpoint'")
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
            "INSERT INTO checkpoints (project_id, timestamp, stagnation_count, event_type, data) VALUES (?, ?, ?, ?, ?)",
            (
                data["project_id"],
                data["timestamp"],
                data.get("stagnation_count", 1),
                data.get("event_type") or "checkpoint",
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


# ── Analysis ──────────────────────────────────────────────────────────────────

def build_stagnation_report(project_id: str, stuck_task: str | None = None, n: int = 50) -> dict | None:
    """
    Root-cause analysis for a stuck task. Returns None if the project has no
    checkpoints. stuck_task defaults to the latest checkpoint's current_task.
    """
    checkpoints = get_recent_checkpoints(project_id, n=n)
    if not checkpoints:
        return None
    task = stuck_task if stuck_task is not None else checkpoints[0].get("current_task", "")
    matching = [
        c for c in checkpoints
        if _normalize(c.get("current_task", "")) == _normalize(task)
    ]
    if not matching:
        return None

    stuck_since = matching[-1]["timestamp"]  # newest-first, so last item is earliest
    try:
        started = datetime.fromisoformat(stuck_since)
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        elapsed_hours = round((datetime.now(timezone.utc) - started).total_seconds() / 3600, 1)
    except ValueError:
        elapsed_hours = 0.0

    blocker_counts = Counter(b for c in matching for b in c.get("blockers", []))
    primary_blocker = blocker_counts.most_common(1)[0][0] if blocker_counts else ""

    if primary_blocker:
        recommendation = (
            f"'{primary_blocker}' is the dominant blocker. Resolve it before writing "
            "more code — if it stems from an unmade decision, record an ADR "
            "(event_type=adr) first."
        )
    else:
        recommendation = (
            f"No recurring blocker recorded; '{task}' is likely underscoped. "
            "Break it into the smallest completable unit and checkpoint that instead."
        )

    return {
        "stuck_since": stuck_since,
        "elapsed_hours": elapsed_hours,
        "primary_blocker": primary_blocker,
        "recommendation": recommendation,
        "checkpoint_count": len(matching),
    }


def extract_patterns(project_id: str) -> dict:
    """
    Recurring signals across a project's checkpoints (one checkpoint ≈ one
    work unit): files modified in 3+ checkpoints, blockers seen 2+ times,
    tasks resubmitted 3+ times.
    """
    checkpoints = get_recent_checkpoints(project_id, n=10_000)

    file_counts: Counter = Counter()
    blocker_counts: Counter = Counter()
    task_counts: Counter = Counter()
    task_display: dict[str, str] = {}

    for c in checkpoints:
        state = c.get("current_state") or {}
        file_counts.update(set(state.get("files_modified", [])))
        blocker_counts.update(c.get("blockers", []))
        task = c.get("current_task", "")
        norm = _normalize(task)
        # The Stop hook posts "End of session" every session — not a real task.
        if norm and norm != "end of session":
            task_counts[norm] += 1
            task_display.setdefault(norm, task)

    return {
        "project_id": project_id,
        "hotspot_files": [
            {"path": path, "count": count}
            for path, count in file_counts.most_common()
            if count >= 3
        ],
        "recurring_blockers": [
            {"text": text, "count": count}
            for text, count in blocker_counts.most_common()
            if count >= 2
        ],
        "recurring_tasks": [
            {"text": task_display[norm], "count": count}
            for norm, count in task_counts.most_common()
            if count >= 3
        ],
    }


# Deterministic keyword list for extracting stack names from architecture notes.
_STACK_KEYWORDS = (
    "fastapi", "flask", "django", "express", "next.js", "nextjs", "react", "vue",
    "svelte", "tailwind", "typescript", "python", "node", "go", "rust",
    "postgres", "postgresql", "sqlite", "mysql", "mongodb", "redis", "supabase",
    "sqlalchemy", "alembic", "pydantic", "celery", "docker", "kubernetes",
    "ollama", "anthropic", "openai",
)


def build_profile() -> dict:
    """Cross-project developer profile aggregated from all stored checkpoints."""
    with _conn() as con:
        rows = con.execute("SELECT data FROM checkpoints").fetchall()
    checkpoints = [json.loads(r[0]) for r in rows]

    ext_counts: Counter = Counter()
    blocker_counts: Counter = Counter()
    tech_counts: Counter = Counter()
    rejected: list[dict] = []
    projects: set[str] = set()

    for c in checkpoints:
        projects.add(c.get("project_id", ""))
        state = c.get("current_state") or {}
        for f in state.get("files_modified", []):
            ext = Path(f).suffix.lower()
            if ext:
                ext_counts[ext] += 1
        blocker_counts.update(c.get("blockers", []))

        event_type = c.get("event_type", "checkpoint")
        event_data = c.get("event_data") or {}
        if event_type == "adr":
            notes = " ".join(
                str(v) for v in (state.get("architecture_notes", ""), *event_data.values())
            ).lower()
            for kw in _STACK_KEYWORDS:
                if kw in notes:
                    tech_counts[kw] += 1
        elif event_type == "failure":
            rejected.append({
                "attempted": str(event_data.get("attempted", c.get("current_task", ""))),
                "failed_because": str(event_data.get("failed_because", "")),
                "project_id": c.get("project_id", ""),
            })

    return {
        "project_count": len(projects),
        "checkpoint_count": len(checkpoints),
        "top_file_types": [
            {"text": ext, "count": n} for ext, n in ext_counts.most_common(5)
        ],
        "common_blockers": [
            {"text": b, "count": n} for b, n in blocker_counts.most_common(5)
        ],
        "tech_patterns": [
            {"text": kw, "count": n} for kw, n in tech_counts.most_common(10)
        ],
        "rejected_approaches": rejected,
    }


def project_exists(project_id: str) -> bool:
    with _conn() as con:
        row = con.execute(
            "SELECT 1 FROM checkpoints WHERE project_id = ? LIMIT 1", (project_id,)
        ).fetchone()
    return row is not None
