import json
import logging
import sqlite3
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .config import settings

logger = logging.getLogger(__name__)

_DB = str(settings.db_path)

# ── sqlite-vec extension (optional) ───────────────────────────────────────────

try:
    import sqlite_vec as _sqlite_vec
    _SQLITE_VEC_AVAILABLE = True
except ImportError:
    _sqlite_vec = None  # type: ignore[assignment]
    _SQLITE_VEC_AVAILABLE = False

_EMBEDDING_DIM = 256


def _normalize(task: str) -> str:
    return " ".join(task.lower().split())


@contextmanager
def _conn():
    con = sqlite3.connect(_DB, check_same_thread=False)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA foreign_keys=ON")
    if _SQLITE_VEC_AVAILABLE:
        try:
            con.enable_load_extension(True)
            _sqlite_vec.load(con)
            con.enable_load_extension(False)
        except Exception:
            pass
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
                id                           INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id                   TEXT NOT NULL,
                timestamp                    TEXT NOT NULL,
                stagnation_count             INTEGER NOT NULL DEFAULT 1,
                event_type                   TEXT NOT NULL DEFAULT 'checkpoint',
                checkpoint_type              TEXT NOT NULL DEFAULT 'task',
                completed_at_ts              INTEGER,
                task_duration_ms             INTEGER,
                planner_confidence           REAL,
                planner_blocker_class        TEXT,
                planner_decomposition_suggested INTEGER DEFAULT 0,
                data                         TEXT NOT NULL
            )
            """
        )
        # Migration-safe column additions for pre-0.5 databases
        cols = {row[1] for row in con.execute("PRAGMA table_info(checkpoints)")}
        migrations = {
            "id":                               "INTEGER",
            "event_type":                       "TEXT NOT NULL DEFAULT 'checkpoint'",
            "checkpoint_type":                  "TEXT NOT NULL DEFAULT 'task'",
            "completed_at_ts":                  "INTEGER",
            "task_duration_ms":                 "INTEGER",
            "planner_confidence":               "REAL",
            "planner_blocker_class":            "TEXT",
            "planner_decomposition_suggested":  "INTEGER DEFAULT 0",
        }
        for col, col_def in migrations.items():
            if col not in cols:
                con.execute(f"ALTER TABLE checkpoints ADD COLUMN {col} {col_def}")
                if col == "id":
                    con.execute("UPDATE checkpoints SET id = rowid WHERE id IS NULL")

        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_project_ts ON checkpoints(project_id, timestamp DESC, id DESC)"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_project_type ON checkpoints(project_id, checkpoint_type, id DESC)"
        )

        if _SQLITE_VEC_AVAILABLE:
            con.execute(
                f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS checkpoint_embeddings USING vec0(
                    checkpoint_id INTEGER PRIMARY KEY,
                    embedding FLOAT[{_EMBEDDING_DIM}]
                )
                """
            )


# ── Embedding ─────────────────────────────────────────────────────────────────

def _embed_text(text: str) -> list[float] | None:
    """Return a {_EMBEDDING_DIM}-dim float vector, or None when offline."""
    key = settings.embedding_api_key()
    if not key:
        return None  # offline fallback: semantic search disabled without API key
    try:
        import voyageai  # optional dep: pip install voyageai
        client = voyageai.Client(api_key=key)
        result = client.embed([text[:2000]], model="voyage-3-lite", output_dimension=_EMBEDDING_DIM)
        return result.embeddings[0]
    except Exception as exc:
        logger.warning("Embedding failed (%s: %s) — semantic search disabled", type(exc).__name__, exc)
        return None


def save_embedding(checkpoint_id: int, text: str) -> None:
    if not _SQLITE_VEC_AVAILABLE:
        return
    vec = _embed_text(text)
    if vec is None:
        # offline fallback: semantic search disabled without API key
        vec = [0.0] * _EMBEDDING_DIM
    vec_json = json.dumps(vec)
    try:
        with _conn() as con:
            con.execute(
                "INSERT OR REPLACE INTO checkpoint_embeddings(checkpoint_id, embedding) VALUES (?, json(?))",
                (checkpoint_id, vec_json),
            )
    except Exception as exc:
        logger.warning("Failed to save embedding for checkpoint %d: %s", checkpoint_id, exc)


def search_checkpoints(
    query: str,
    limit: int,
    exclude_project_id: str | None,
) -> list[dict]:
    """Semantic KNN search over task/session-type checkpoints. Returns [] when offline."""
    if not _SQLITE_VEC_AVAILABLE:
        return []
    query_vec = _embed_text(query)
    if query_vec is None:
        return []  # semantic search disabled without API key

    query_json = json.dumps(query_vec)
    try:
        with _conn() as con:
            rows = con.execute(
                f"""
                SELECT ce.checkpoint_id, ce.distance
                FROM checkpoint_embeddings ce
                WHERE ce.embedding MATCH json(?) AND k = ?
                ORDER BY ce.distance
                """,
                (query_json, limit * 3),  # over-fetch to allow post-filter
            ).fetchall()

            if not rows:
                return []

            checkpoint_ids = [r[0] for r in rows]
            distance_map = {r[0]: r[1] for r in rows}

            placeholders = ",".join("?" * len(checkpoint_ids))
            checkpoints = con.execute(
                f"""
                SELECT id, project_id, checkpoint_type, completed_at_ts, planner_confidence, data
                FROM checkpoints
                WHERE id IN ({placeholders})
                AND checkpoint_type IN ('task', 'session')
                """,
                checkpoint_ids,
            ).fetchall()
    except Exception as exc:
        logger.warning("Search query failed: %s", exc)
        return []

    results = []
    for row in checkpoints:
        cid, project_id, cp_type, completed_at_ts, _, data_json = row
        if exclude_project_id and project_id == exclude_project_id:
            continue
        data = json.loads(data_json)
        planner = data.get("_planner_output") or {}
        distance = distance_map.get(cid, 9999.0)
        similarity = round(1.0 / (1.0 + distance), 4)
        branch = project_id.rsplit("/", 1)[-1] if "/" in project_id else "main"
        results.append({
            "project_id": project_id,
            "branch": branch,
            "task_summary": data.get("current_task", ""),
            "checkpoint_type": cp_type,
            "similarity": similarity,
            "completed_at_ts": completed_at_ts,
            "planner_next_instruction": planner.get("next_instruction", ""),
        })

    # Sort by similarity descending, apply limit
    results.sort(key=lambda r: r["similarity"], reverse=True)
    return results[:limit]


# ── Checkpoint type classification ────────────────────────────────────────────

def classify_checkpoint_type(current_state: dict, hint: str | None = None) -> str:
    """Classify checkpoint as 'scratch', 'task', or 'session'.

    Defaults to 'task' when no diff info is available to preserve backward
    compatibility (see DECISIONS.md ADR-003).
    """
    if hint in ("scratch", "task", "session"):
        return hint

    diff_stat = (current_state.get("git_diff_stat") or "").strip()
    name_status = (current_state.get("git_name_status") or "").strip()

    if not diff_stat or diff_stat == "(no uncommitted changes)":
        return "task"  # no diff info → default to task (ADR-003)

    # New file detection via git diff --name-status output
    if any(line.startswith("A\t") for line in name_status.splitlines()):
        return "task"

    # Count total changed lines from git diff --stat output
    # Format: " file.py | 12 +++++-----"
    total_lines = 0
    for line in diff_stat.splitlines():
        if "|" in line:
            parts = line.split("|")
            if len(parts) >= 2:
                num_str = ""
                for ch in parts[1].strip():
                    if ch.isdigit():
                        num_str += ch
                    else:
                        break
                if num_str:
                    total_lines += int(num_str)

    return "task" if total_lines >= 10 else "scratch"


# ── Velocity ──────────────────────────────────────────────────────────────────

def _velocity_ratio(current_ms: int, baseline: list[int]) -> tuple[float | None, bool, str]:
    """Pure math: compute ratio and alert from a baseline list. Exposed for unit tests."""
    if len(baseline) < 5:
        return None, False, "insufficient history (need 5+ completed task checkpoints for baseline)"
    avg = sum(baseline) / len(baseline)
    if avg == 0:
        return None, False, ""
    ratio = current_ms / avg
    alert = ratio >= 2.0
    reason = f"current task is {ratio:.1f}x slower than your baseline for this branch" if alert else ""
    return round(ratio, 2), alert, reason


def _get_prev_completed_at_ts(project_id: str) -> int | None:
    """Return completed_at_ts of the most recent checkpoint for this project."""
    with _conn() as con:
        row = con.execute(
            "SELECT completed_at_ts FROM checkpoints WHERE project_id = ? AND completed_at_ts IS NOT NULL ORDER BY id DESC LIMIT 1",
            (project_id,),
        ).fetchone()
    return row[0] if row else None


def compute_task_duration_ms(project_id: str, current_ts_ms: int) -> int | None:
    prev = _get_prev_completed_at_ts(project_id)
    if prev is None:
        return None
    duration = current_ts_ms - prev
    return duration if duration > 0 else None


def get_velocity(project_id: str) -> dict | None:
    """Velocity metrics for the project. Returns None if no task checkpoints exist."""
    with _conn() as con:
        # Latest task checkpoint for current_duration_ms calculation
        latest = con.execute(
            "SELECT completed_at_ts FROM checkpoints WHERE project_id = ? AND checkpoint_type = 'task' ORDER BY id DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        # Last 10 task checkpoints with non-null task_duration_ms for baseline
        baseline_rows = con.execute(
            "SELECT task_duration_ms FROM checkpoints WHERE project_id = ? AND checkpoint_type = 'task' AND task_duration_ms IS NOT NULL ORDER BY id DESC LIMIT 10",
            (project_id,),
        ).fetchall()

    if not latest or not latest[0]:
        return None

    latest_ts = latest[0]
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    current_duration_ms = now_ms - latest_ts
    baseline = [r[0] for r in baseline_rows if r[0] is not None]

    ratio, alert, alert_reason = _velocity_ratio(current_duration_ms, baseline)
    avg_duration_ms = round(sum(baseline) / len(baseline)) if baseline else None

    return {
        "avg_duration_ms": avg_duration_ms,
        "current_duration_ms": current_duration_ms,
        "velocity_ratio": ratio,
        "alert": alert,
        "alert_reason": alert_reason,
    }


# ── Purge ─────────────────────────────────────────────────────────────────────

def purge_old_scratch_checkpoints() -> int:
    """Delete scratch checkpoints older than 24 hours. Returns count deleted."""
    cutoff_ms = int((datetime.now(timezone.utc).timestamp() - 86400) * 1000)
    with _conn() as con:
        cur = con.execute(
            "DELETE FROM checkpoints WHERE checkpoint_type = 'scratch' AND completed_at_ts IS NOT NULL AND completed_at_ts < ?",
            (cutoff_ms,),
        )
        return cur.rowcount


# ── Core CRUD ─────────────────────────────────────────────────────────────────

def compute_stagnation_count(project_id: str, new_task: str) -> int:
    """Stagnation streak for new_task (excludes scratch checkpoints)."""
    with _conn() as con:
        rows = con.execute(
            "SELECT data, stagnation_count FROM checkpoints WHERE project_id = ? AND checkpoint_type != 'scratch' ORDER BY timestamp DESC, id DESC LIMIT 1",
            (project_id,),
        ).fetchall()
    if not rows:
        return 1
    data = json.loads(rows[0][0])
    prev_stag = rows[0][1]
    if _normalize(new_task) == _normalize(data.get("current_task", "")):
        return prev_stag + 1
    return 1


def save_checkpoint(data: dict) -> int:
    """Insert a checkpoint row. Returns the rowid of the inserted row."""
    with _conn() as con:
        cur = con.execute(
            """INSERT INTO checkpoints
               (project_id, timestamp, stagnation_count, event_type, checkpoint_type,
                completed_at_ts, task_duration_ms,
                planner_confidence, planner_blocker_class, planner_decomposition_suggested,
                data)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data["project_id"],
                data["timestamp"],
                data.get("stagnation_count", 1),
                data.get("event_type") or "checkpoint",
                data.get("checkpoint_type") or "task",
                data.get("completed_at_ts"),
                data.get("task_duration_ms"),
                data.get("planner_confidence"),
                data.get("planner_blocker_class"),
                1 if data.get("planner_decomposition_suggested") else 0,
                json.dumps(data),
            ),
        )
        return cur.lastrowid


def delete_project(project_id: str) -> int:
    with _conn() as con:
        cur = con.execute("DELETE FROM checkpoints WHERE project_id = ?", (project_id,))
        return cur.rowcount


def get_recent_checkpoints(project_id: str, n: int = 10) -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT data FROM checkpoints WHERE project_id = ? ORDER BY timestamp DESC, id DESC LIMIT ?",
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
                MAX(stagnation_count) as max_stagnation,
                SUM(CASE WHEN checkpoint_type = 'task'    THEN 1 ELSE 0 END) as task_count,
                SUM(CASE WHEN checkpoint_type = 'scratch' THEN 1 ELSE 0 END) as scratch_count,
                SUM(CASE WHEN checkpoint_type = 'session' THEN 1 ELSE 0 END) as session_count
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
            "type_breakdown": {"task": r[4] or 0, "scratch": r[5] or 0, "session": r[6] or 0},
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


# ── Diff (Task 5) ─────────────────────────────────────────────────────────────

def get_diff_data(project_id: str) -> dict | None:
    """Return the two most recent task-type checkpoints as a diff payload.

    Returns None when fewer than 2 task checkpoints exist.
    """
    with _conn() as con:
        rows = con.execute(
            """SELECT data, task_duration_ms, planner_confidence, planner_blocker_class,
                      planner_decomposition_suggested, completed_at_ts
               FROM checkpoints
               WHERE project_id = ? AND checkpoint_type = 'task'
               ORDER BY id DESC
               LIMIT 2""",
            (project_id,),
        ).fetchall()

    if len(rows) < 2:
        return None

    def _parse_row(row: tuple) -> tuple[dict, dict]:
        data = json.loads(row[0])
        planner = data.get("_planner_output") or {}
        return {
            "task_summary": data.get("current_task", ""),
            "completed_at_ts": row[5],
            "planner_confidence": row[2],
            "planner_blocker_class": row[3],
            "planner_decomposition_suggested": bool(row[4]),
            "task_duration_ms": row[1],
        }, planner

    to_cp, to_planner = _parse_row(rows[0])
    from_cp, _ = _parse_row(rows[1])

    patterns = extract_patterns(project_id)
    priority_focus = [h["path"] for h in patterns["hotspot_files"][:5]]

    return {
        "from": from_cp,
        "to": to_cp,
        "next_instruction": to_planner.get("next_instruction", ""),
        "priority_focus": priority_focus,
    }


# ── Snapshot / Markdown export (Task 7) ───────────────────────────────────────

def build_snapshot(project_id: str) -> str | None:
    """Generate a CLAUDE.md-compatible Markdown snapshot for a project."""
    checkpoints = get_recent_checkpoints(project_id, n=1)
    if not checkpoints:
        return None

    latest = checkpoints[0]
    planner = latest.get("_planner_output") or {}
    patterns = extract_patterns(project_id)
    velocity = get_velocity(project_id)

    with _conn() as con:
        type_counts = dict(con.execute(
            "SELECT checkpoint_type, COUNT(*) FROM checkpoints WHERE project_id = ? GROUP BY checkpoint_type",
            (project_id,),
        ).fetchall())
        adr_rows = con.execute(
            "SELECT data FROM checkpoints WHERE project_id = ? AND event_type = 'adr' ORDER BY id DESC LIMIT 5",
            (project_id,),
        ).fetchall()
        blocker_class_rows = con.execute(
            """SELECT planner_blocker_class, COUNT(*) FROM checkpoints
               WHERE project_id = ? AND checkpoint_type = 'task' AND planner_blocker_class IS NOT NULL
               GROUP BY planner_blocker_class ORDER BY COUNT(*) DESC""",
            (project_id,),
        ).fetchall()

    task_count = type_counts.get("task", 0)
    scratch_count = type_counts.get("scratch", 0)
    session_count = type_counts.get("session", 0)
    total = sum(type_counts.values())

    adrs = [json.loads(r[0]).get("event_data", {}) for r in adr_rows]
    ts_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        "# context-bridge Snapshot",
        f"Generated: {ts_str}",
        f"Project: {project_id}",
        f"Checkpoints: {total} ({task_count} task, {scratch_count} scratch, {session_count} session)",
        "",
        "## Current State",
        f"**Last task:** {latest.get('current_task', '')}",
        f"**Next instruction:** {planner.get('next_instruction', '')}",
        f"**Confidence:** {planner.get('confidence', '')}",
        "",
        "## Velocity",
    ]

    if velocity and velocity["avg_duration_ms"] is not None:
        avg_s = velocity["avg_duration_ms"] / 1000
        m, s = divmod(int(avg_s), 60)
        lines.append(f"Average task duration: {m}m {s}s")
        if velocity["alert"]:
            lines.append(f"Current task: ⚠ slower than baseline ({velocity['velocity_ratio']:.1f}x)")
        else:
            lines.append("Current task: on track")
    else:
        lines.append("Insufficient history for velocity tracking")

    lines += ["", "## Recurring Patterns", "| Blocker | Count |", "|---------|-------|"]
    if blocker_class_rows:
        for bc, cnt in blocker_class_rows:
            lines.append(f"| {bc} | {cnt} |")
    else:
        lines.append("| none | 0 |")

    lines += ["", "## Architecture Decisions"]
    if adrs:
        for adr in adrs:
            decision = adr.get("decision", "")
            reason = adr.get("reason", "")
            if decision:
                lines.append(f"- **{decision}**: {reason}")
    else:
        lines.append("*(empty — no ADR events recorded)*")

    lines += ["", "## Hotspots"]
    if patterns["hotspot_files"]:
        for h in patterns["hotspot_files"]:
            lines.append(f"- {h['path']} ({h['count']}x)")
    else:
        lines.append("*(no hotspot files yet)*")

    return "\n".join(lines) + "\n"


# ── Analysis ──────────────────────────────────────────────────────────────────

def build_stagnation_report(project_id: str, stuck_task: str | None = None, n: int = 50) -> dict | None:
    checkpoints = get_recent_checkpoints(project_id, n=n)
    if not checkpoints:
        return None
    task = stuck_task if stuck_task is not None else checkpoints[0].get("current_task", "")
    matching = [c for c in checkpoints if _normalize(c.get("current_task", "")) == _normalize(task)]
    if not matching:
        return None

    stuck_since = matching[-1]["timestamp"]
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


_STACK_KEYWORDS = (
    "fastapi", "flask", "django", "express", "next.js", "nextjs", "react", "vue",
    "svelte", "tailwind", "typescript", "python", "node", "go", "rust",
    "postgres", "postgresql", "sqlite", "mysql", "mongodb", "redis", "supabase",
    "sqlalchemy", "alembic", "pydantic", "celery", "docker", "kubernetes",
    "ollama", "anthropic", "openai",
)

_EXT_TO_STACK: dict[str, str] = {
    ".py": "Python", ".ts": "TypeScript", ".tsx": "TypeScript", ".js": "JavaScript",
    ".jsx": "JavaScript", ".sql": "SQLite", ".rs": "Rust", ".go": "Go",
    ".java": "Java", ".rb": "Ruby", ".cs": "C#", ".cpp": "C++",
}


def build_profile() -> dict:
    """Cross-project developer profile aggregated from all stored checkpoints."""
    with _conn() as con:
        rows = con.execute("SELECT data FROM checkpoints").fetchall()
        # New computed fields from DB columns
        velocity_rows = con.execute(
            "SELECT task_duration_ms FROM checkpoints WHERE checkpoint_type = 'task' AND task_duration_ms IS NOT NULL"
        ).fetchall()
        blocker_class_rows = con.execute(
            "SELECT planner_blocker_class, COUNT(*) FROM checkpoints WHERE checkpoint_type = 'task' AND planner_blocker_class IS NOT NULL AND planner_blocker_class != 'none' GROUP BY planner_blocker_class ORDER BY COUNT(*) DESC"
        ).fetchall()
        total_task_checkpoints = con.execute(
            "SELECT COUNT(*) FROM checkpoints WHERE checkpoint_type = 'task'"
        ).fetchone()[0]

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

    # Compute avg velocity
    durations = [r[0] for r in velocity_rows if r[0]]
    avg_task_velocity_ms = round(sum(durations) / len(durations)) if durations else None

    # Compute preferred_stack from top file extensions
    preferred_stack: list[str] = []
    seen_stacks: set[str] = set()
    for ext, _ in ext_counts.most_common(20):
        stack = _EXT_TO_STACK.get(ext)
        if stack and stack not in seen_stacks:
            preferred_stack.append(stack)
            seen_stacks.add(stack)
        if len(preferred_stack) >= 5:
            break

    recurring_blocker_classes = [
        {"text": bc, "count": cnt} for bc, cnt in blocker_class_rows
    ]

    return {
        "project_count": len(projects),
        "checkpoint_count": len(checkpoints),
        "top_file_types": [{"text": ext, "count": n} for ext, n in ext_counts.most_common(5)],
        "common_blockers": [{"text": b, "count": n} for b, n in blocker_counts.most_common(5)],
        "tech_patterns": [{"text": kw, "count": n} for kw, n in tech_counts.most_common(10)],
        "rejected_approaches": rejected,
        # New v0.5.0 fields
        "avg_task_velocity_ms": avg_task_velocity_ms,
        "preferred_stack": preferred_stack,
        "recurring_blocker_classes": recurring_blocker_classes,
        "total_task_checkpoints": total_task_checkpoints,
        "total_projects": len(projects),
    }


def project_exists(project_id: str) -> bool:
    with _conn() as con:
        row = con.execute(
            "SELECT 1 FROM checkpoints WHERE project_id = ? LIMIT 1", (project_id,)
        ).fetchone()
    return row is not None
