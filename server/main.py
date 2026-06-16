import argparse
import asyncio
import json
import logging
import os
import shutil
import tempfile
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from .config import settings
from .memory import (
    _SQLITE_VEC_AVAILABLE,
    build_attempt_replay,
    build_profile,
    build_snapshot,
    build_stagnation_report,
    classify_checkpoint_type,
    compute_stagnation_count,
    compute_stagnation_from_history,
    compute_task_duration_ms,
    delete_project,
    detect_goal_drift,
    extract_patterns,
    find_similar_blocker,
    get_all_projects,
    get_diff_data,
    get_recent_checkpoints,
    get_stats,
    get_velocity,
    init_db,
    project_exists,
    purge_old_scratch_checkpoints,
    save_checkpoint,
    save_embedding,
    search_checkpoints,
)
from .models import (
    AttemptEntry,
    AttemptReplay,
    BlockerMatch,
    CheckpointAck,
    CheckpointIn,
    DeveloperProfile,
    DiffResponse,
    ErrorResponse,
    GoalDriftReport,
    PatternsReport,
    ProjectStats,
    ProjectSummary,
    SearchRequest,
    SearchResponse,
    SearchResult,
    StagnationReport,
    SyncResponse,
    VelocityReport,
)
from .planner import run_planner

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

_DASHBOARD = (Path(__file__).parent / "dashboard.html").read_text()
_SKILL_SRC = Path(__file__).resolve().parent.parent / "skill" / "CLAUDE.md"
_HOOK_SRC = Path(__file__).parent / "hook.py"
_CLAUDE_DIR = Path.home() / ".claude"
_SKILL_DEST = _CLAUDE_DIR / "context-bridge.md"
_HOOK_DEST = _CLAUDE_DIR / "context-bridge-hook.py"
_SETTINGS_PATH = _CLAUDE_DIR / "settings.json"
_CLAUDE_MD = _CLAUDE_DIR / "CLAUDE.md"
_IMPORT_LINE = f"@{_SKILL_DEST}"


def _not_found(project_id: str) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail=ErrorResponse(
            error="not_found",
            message=f"Project '{project_id}' has no checkpoints.",
        ).model_dump(),
    )


async def _purge_loop() -> None:
    """Background task: purge stale scratch checkpoints every 6 hours."""
    while True:
        await asyncio.sleep(6 * 3600)
        try:
            count = purge_old_scratch_checkpoints()
            if count:
                logger.info("purged %d stale scratch checkpoints", count)
        except Exception as exc:
            logger.warning("Scratch purge failed: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = asyncio.create_task(_purge_loop())
    logger.info("Context Bridge started  db=%s  port=%d", settings.db_path, settings.server_port)
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="Context Bridge",
    description="Stagnation detection, velocity tracking, and session continuity for Claude Code.",
    version="0.7.0",
    lifespan=lifespan,
)


def _prepare_checkpoint_data(cp: CheckpointIn) -> dict:
    """Convert CheckpointIn to a storage dict with all computed fields."""
    data = cp.model_dump(mode="json")
    if not data["timestamp"]:
        data["timestamp"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    if not data["project_id"]:
        data["project_id"] = str(uuid.uuid4())[:8]

    # Timing
    completed_at_ts = data.get("completed_at_ts") or int(datetime.now(timezone.utc).timestamp() * 1000)
    data["completed_at_ts"] = completed_at_ts
    data["task_duration_ms"] = compute_task_duration_ms(data["project_id"], completed_at_ts)

    # Checkpoint type classification
    state = data.get("current_state") or {}
    data["checkpoint_type"] = classify_checkpoint_type(state, data.get("checkpoint_type"))

    return data


def _embed_text_for(data: dict) -> str:
    task = data.get("current_task", "")
    diff_snippet = (data.get("current_state") or {}).get("git_diff_stat", "")[:500]
    return f"{task} {diff_snippet}".strip()


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def dashboard() -> HTMLResponse:
    return HTMLResponse(_DASHBOARD)


@app.post("/checkpoint", response_model=CheckpointAck)
async def checkpoint(cp: CheckpointIn) -> CheckpointAck:
    """Store a checkpoint without running the planner."""
    data = _prepare_checkpoint_data(cp)
    stag = compute_stagnation_count(data["project_id"], data["current_task"])
    data["stagnation_count"] = stag
    checkpoint_id = save_checkpoint(data)
    save_embedding(checkpoint_id, _embed_text_for(data))
    logger.info(
        "checkpoint  project=%s  task=%r  stagnation=%d  type=%s",
        data["project_id"], data["current_task"], stag, data["checkpoint_type"],
    )
    return CheckpointAck(project_id=data["project_id"], stagnation_count=stag)


@app.post("/sync", response_model=SyncResponse)
async def sync(cp: CheckpointIn, background_tasks: BackgroundTasks) -> SyncResponse:
    """Store a checkpoint and return an authoritative plan."""
    data = _prepare_checkpoint_data(cp)

    # Fetch history once — shared between stagnation computation and planner.
    # This eliminates the separate compute_stagnation_count DB read.
    history = get_recent_checkpoints(data["project_id"], n=10)
    stag = compute_stagnation_from_history(data["current_task"], history)
    data["stagnation_count"] = stag

    report = build_stagnation_report(data["project_id"], data["current_task"]) if stag >= 3 else None
    attempt_replay_data = build_attempt_replay(data["project_id"], data["current_task"]) if stag >= 2 else None
    result = run_planner(data, history, stag, report, attempt_replay_data)

    # Velocity alert — prepend warning to next_instruction when triggered (ADR-006)
    velocity = get_velocity(data["project_id"])
    if velocity and velocity["alert"]:
        avg_s = (velocity["avg_duration_ms"] or 0) / 1000
        cur_s = (velocity["current_duration_ms"] or 0) / 1000
        ratio = velocity["velocity_ratio"] or 0
        avg_min, avg_sec = divmod(int(avg_s), 60)
        cur_min, cur_sec = divmod(int(cur_s), 60)
        warning = (
            f"⚠ VELOCITY ALERT: This task is taking {ratio:.1f}x longer than your baseline on this branch.\n"
            f"  Last 10 tasks averaged {avg_min}m {avg_sec}s. Current task has been open {cur_min}m {cur_sec}s.\n"
            f"  Consider: Is this blocked? Is the scope larger than expected? Should it be decomposed?\n\n"
        )
        result.next_instruction = warning + result.next_instruction

    # Blocker history match — surface if current blockers recur from history
    if data.get("blockers"):
        match = find_similar_blocker(data["project_id"], data["blockers"][0])
        if match:
            result.blocker_match = match

    # Store structured planner output back into the blob
    data["_planner_output"] = result.model_dump()
    data["planner_confidence"] = result.confidence
    data["planner_blocker_class"] = result.blocker_class
    data["planner_decomposition_suggested"] = result.decomposition_suggested

    checkpoint_id = save_checkpoint(data)

    # Fire embedding in background — don't block the sync response on a network call.
    # BackgroundTasks are awaited by FastAPI after response delivery, unlike
    # asyncio.create_task which can be garbage-collected before completion.
    background_tasks.add_task(save_embedding, checkpoint_id, _embed_text_for(data))

    logger.info(
        "sync  project=%s  task=%r  source=%s  stagnation=%d  type=%s  confidence=%.2f",
        data["project_id"], data["current_task"], result.source, stag,
        data["checkpoint_type"], result.confidence,
    )
    return result


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "context-bridge", "port": settings.server_port}


@app.get("/stats", response_model=ProjectStats)
async def stats() -> ProjectStats:
    return ProjectStats(**get_stats())


@app.get("/projects", response_model=list[ProjectSummary])
async def projects() -> list[ProjectSummary]:
    return [ProjectSummary(**p) for p in get_all_projects()]


@app.delete("/projects/{project_id:path}")
async def delete(project_id: str) -> dict:
    if not project_exists(project_id):
        raise _not_found(project_id)
    count = delete_project(project_id)
    logger.info("deleted  project=%s  checkpoints=%d", project_id, count)
    return {"deleted": count}


@app.get("/history/{project_id:path}", response_model=list[dict])
async def history(project_id: str, limit: int = 50) -> list[dict]:
    if not project_exists(project_id):
        raise _not_found(project_id)
    return get_recent_checkpoints(project_id, n=min(max(limit, 0), 100))


@app.get("/projects/{project_id:path}/stagnation-report", response_model=StagnationReport)
async def stagnation_report(project_id: str) -> StagnationReport:
    if not project_exists(project_id):
        raise _not_found(project_id)
    report = build_stagnation_report(project_id)
    if report is None:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                error="no_stagnation",
                message=f"No stagnant task found for project '{project_id}'.",
            ).model_dump(),
        )
    return StagnationReport(**report)


@app.get("/projects/{project_id:path}/patterns", response_model=PatternsReport)
async def patterns(project_id: str) -> PatternsReport:
    if not project_exists(project_id):
        raise _not_found(project_id)
    return PatternsReport(**extract_patterns(project_id))


@app.get("/profile", response_model=DeveloperProfile)
async def profile() -> DeveloperProfile:
    return DeveloperProfile(**build_profile())


@app.get("/projects/{project_id:path}/export")
async def export(project_id: str) -> JSONResponse:
    """Download all checkpoints for a project as JSON."""
    if not project_exists(project_id):
        raise _not_found(project_id)
    data = get_recent_checkpoints(project_id, n=10_000)
    filename = f"context-bridge-{project_id.replace('/', '-')}.json"
    return JSONResponse(
        content=data,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Task 1: Velocity ──────────────────────────────────────────────────────────

@app.get("/velocity/{project_id:path}", response_model=VelocityReport)
async def velocity(project_id: str) -> VelocityReport:
    """Velocity metrics: baseline duration, current duration, alert when 2x+ slower."""
    if not project_exists(project_id):
        raise _not_found(project_id)
    data = get_velocity(project_id)
    if data is None:
        return VelocityReport(
            avg_duration_ms=None,
            current_duration_ms=None,
            velocity_ratio=None,
            alert=False,
            alert_reason="no task checkpoints with timing data",
        )
    return VelocityReport(**data)


# ── Task 4: Semantic Search ───────────────────────────────────────────────────

@app.post("/search", response_model=SearchResponse)
async def search(req: SearchRequest) -> SearchResponse:
    """Semantic KNN search over task/session checkpoints across projects."""
    results = search_checkpoints(req.query, req.limit, req.exclude_project_id)
    return SearchResponse(results=[SearchResult(**r) for r in results])


# ── Task 5: Diff ──────────────────────────────────────────────────────────────

@app.get("/diff/{project_id:path}", response_model=DiffResponse)
async def diff(project_id: str) -> DiffResponse:
    """Compare the two most recent task checkpoints: what changed in task, velocity, planner confidence."""
    if not project_exists(project_id):
        raise _not_found(project_id)
    data = get_diff_data(project_id)
    if data is None:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                error="insufficient_history",
                message=f"Project '{project_id}' has fewer than 2 task checkpoints. Run more sessions first.",
            ).model_dump(),
        )
    return DiffResponse(**{"from": data["from"], "to": data["to"], **{k: v for k, v in data.items() if k not in ("from", "to")}})


# ── Task 7: Snapshot / Markdown Export ────────────────────────────────────────

@app.get("/snapshot/{project_id:path}")
async def snapshot(project_id: str) -> JSONResponse:
    """Generate a CLAUDE.md-compatible Markdown snapshot of the project."""
    if not project_exists(project_id):
        raise _not_found(project_id)
    md = build_snapshot(project_id)
    if md is None:
        raise _not_found(project_id)
    return JSONResponse(content={"markdown": md})


# ── Attempt replay (v0.7.0) ───────────────────────────────────────────────────

@app.get("/projects/{project_id:path}/replay", response_model=AttemptReplay)
async def replay(project_id: str, task: str | None = None) -> AttemptReplay:
    """Chronological attempt history for the current stagnant task."""
    if not project_exists(project_id):
        raise _not_found(project_id)
    attempts_raw = build_attempt_replay(project_id, task=task)
    history = get_recent_checkpoints(project_id, n=1)
    task_name = task or (history[0].get("current_task", "") if history else "")
    attempts = [AttemptEntry(**a) for a in attempts_raw]
    return AttemptReplay(task=task_name, attempt_count=len(attempts), attempts=attempts)


@app.get("/projects/{project_id:path}/goal-drift", response_model=GoalDriftReport)
async def goal_drift(project_id: str) -> GoalDriftReport:
    """Goal drift analysis: detects frequent goal changes in recent sessions."""
    if not project_exists(project_id):
        raise _not_found(project_id)
    return GoalDriftReport(**detect_goal_drift(project_id))


@app.get("/projects/{project_id:path}/blocker-history", response_model=BlockerMatch | None)
async def blocker_history(project_id: str, q: str = "") -> BlockerMatch | None:
    """Find a similar historical blocker and surface whether it was resolved."""
    if not project_exists(project_id):
        raise _not_found(project_id)
    result = find_similar_blocker(project_id, q)
    return BlockerMatch(**result) if result else None


# ── Install command ───────────────────────────────────────────────────────────

_HOOK_EVENTS = ("SessionStart", "PreToolUse", "PostToolUse", "Stop")


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write JSON atomically: write to a sibling temp file then os.replace."""
    content = (json.dumps(data, indent=2) + "\n").encode()
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        os.write(fd, content)
        os.close(fd)
        os.replace(tmp, path)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _do_install() -> None:
    _CLAUDE_DIR.mkdir(parents=True, exist_ok=True)

    if not _SKILL_SRC.exists():
        print(f"ERROR: skill source missing at {_SKILL_SRC}")
        raise SystemExit(1)
    shutil.copy(_SKILL_SRC, _SKILL_DEST)

    if _CLAUDE_MD.exists():
        content = _CLAUDE_MD.read_text()
        if _IMPORT_LINE not in content:
            _CLAUDE_MD.write_text(content.rstrip() + f"\n\n{_IMPORT_LINE}\n")
    else:
        _CLAUDE_MD.write_text(f"{_IMPORT_LINE}\n")

    if _HOOK_SRC.exists():
        shutil.copy(_HOOK_SRC, _HOOK_DEST)
        _HOOK_DEST.chmod(0o755)

    _configure_hooks()

    hook_dest = str(_HOOK_DEST).replace(str(Path.home()), "~")
    w = 20
    print(f"Context Bridge installed\n")
    print(f"  {f'SessionStart hook':<{w}}→  {hook_dest}")
    print(f"  {f'PreToolUse hook':<{w}}→  {hook_dest}")
    print(f"  {f'PostToolUse hook':<{w}}→  {hook_dest}")
    print(f"  {f'Stop hook':<{w}}→  {hook_dest}")
    print(f"  {f'Skill':<{w}}→  CLAUDE.md ← {_SKILL_DEST.name}")


def _configure_hooks() -> None:
    hook_cmd = f"python3 {_HOOK_DEST}"
    try:
        settings_data = json.loads(_SETTINGS_PATH.read_text()) if _SETTINGS_PATH.exists() else {}
    except (ValueError, OSError):
        settings_data = {}

    hooks = settings_data.setdefault("hooks", {})
    entries = {
        "SessionStart": {"hooks": [{"type": "command", "command": hook_cmd}]},
        "PreToolUse": {"matcher": "", "hooks": [{"type": "command", "command": hook_cmd}]},
        "PostToolUse": {"matcher": "", "hooks": [{"type": "command", "command": hook_cmd}]},
        "Stop": {"hooks": [{"type": "command", "command": hook_cmd}]},
    }
    changed = False
    for event, entry in entries.items():
        existing = hooks.get(event, [])
        if not any(any(h.get("command") == hook_cmd for h in e.get("hooks", [])) for e in existing):
            hooks[event] = existing + [entry]
            changed = True

    if changed:
        _atomic_write_json(_SETTINGS_PATH, settings_data)


def _unconfigure_hooks() -> bool:
    hook_cmd = f"python3 {_HOOK_DEST}"
    try:
        settings_data = json.loads(_SETTINGS_PATH.read_text()) if _SETTINGS_PATH.exists() else {}
    except (ValueError, OSError):
        return False

    hooks = settings_data.get("hooks", {})
    changed = False
    for event in _HOOK_EVENTS:
        kept = [
            e for e in hooks.get(event, [])
            if not any(h.get("command") == hook_cmd for h in e.get("hooks", []))
        ]
        if kept != hooks.get(event, []):
            changed = True
            if kept:
                hooks[event] = kept
            else:
                hooks.pop(event, None)

    if changed:
        _atomic_write_json(_SETTINGS_PATH, settings_data)
    return changed


def _do_uninstall() -> None:
    removed: list[str] = []
    if _unconfigure_hooks():
        removed.append(f"hooks from {_SETTINGS_PATH}")
    for path, label in ((_HOOK_DEST, "hook script"), (_SKILL_DEST, "skill")):
        if path.exists():
            path.unlink()
            removed.append(str(path))
    if _CLAUDE_MD.exists():
        content = _CLAUDE_MD.read_text()
        if _IMPORT_LINE in content:
            _CLAUDE_MD.write_text(content.replace(f"\n\n{_IMPORT_LINE}\n", "\n").replace(f"{_IMPORT_LINE}\n", ""))
            removed.append(f"import line from {_CLAUDE_MD}")

    if removed:
        print("Context Bridge uninstalled\n")
        for item in removed:
            print(f"  ✗  {item}")
    else:
        print("Nothing to uninstall.")
    print("\n  Database at ~/.context-bridge/ was not touched.")


def _fmt_age(ts: str | int | None) -> str:
    """Human-readable age from an ISO timestamp string or unix-millisecond int."""
    if not ts:
        return "unknown"
    try:
        if isinstance(ts, int):
            dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        else:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        s = max((datetime.now(timezone.utc) - dt).total_seconds(), 0)
        if s < 60:
            return "just now"
        if s < 3600:
            return f"{int(s // 60)}m ago"
        if s < 86400:
            return f"{int(s // 3600)}h ago"
        return f"{int(s // 86400)}d ago"
    except Exception:
        return str(ts)


def _fmt_ms(ms: int | None) -> str:
    """Format milliseconds as 'Xm Ys'."""
    if ms is None:
        return "unknown"
    m, s = divmod(int(ms / 1000), 60)
    return f"{m}m {s}s"


def _fetch(path: str, timeout: float = 2.0):
    import urllib.request
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{settings.server_port}{path}", timeout=timeout
        ) as r:
            return json.loads(r.read())
    except Exception:
        return None


def _row(label: str, value: str, width: int = 11) -> str:
    return f"  {label:<{width}}{value}"


def _do_status() -> None:
    data = _fetch("/health")
    if not data:
        print(f"Context Bridge  ✗ not running")
        print(f"  Start with: context-bridge")
        return

    print(f"Context Bridge  ✓ running on port {data['port']}\n")
    print(_row("Database", str(settings.db_path)))

    s = _fetch("/stats")
    projects_data = _fetch("/projects") or []
    if s:
        tp, tc = s["total_projects"], s["total_checkpoints"]
        print(_row("Projects", f"{tp}  ·  Checkpoints  {tc}"))

    stagnant = [p for p in projects_data if p.get("stagnation_count", 0) >= 3]
    if stagnant:
        names = ", ".join(p["project_id"] for p in stagnant[:3])
        n = len(stagnant)
        print(_row("Stagnant", f"{n} project{'s' if n != 1 else ''}  →  context-bridge why  ({names})"))
    else:
        print(_row("Stagnant", "none"))

    planner = "rule-based (no LLM configured)"
    if settings.anthropic_api_key:
        planner = "Anthropic claude-sonnet-4-6"
    elif settings.resolved_ollama_host():
        planner = f"Ollama ({settings.ollama_model})"
    print(_row("Planner", planner))
    print(_row("Velocity", "enabled"))
    embed_status = (
        "enabled (voyageai)" if (settings.embedding_api_key() and _SQLITE_VEC_AVAILABLE)
        else "disabled  →  add VOYAGE_API_KEY and pip install voyageai"
        if _SQLITE_VEC_AVAILABLE
        else "disabled  →  pip install sqlite-vec"
    )
    print(_row("Embeddings", embed_status))


def _do_list() -> None:
    data = _fetch("/health")
    if not data:
        print("Backend not running. Start it with: context-bridge")
        return

    projects_data = _fetch("/projects")
    if not projects_data:
        print("No projects yet. Open Claude Code in a git repo and run a task.")
        return

    col_w = max(len(p["project_id"]) for p in projects_data) + 2
    for p in projects_data:
        pid = p["project_id"].ljust(col_w)
        n = p["checkpoint_count"]
        bd = p.get("type_breakdown", {})
        task_c = bd.get("task", 0)
        scratch_c = bd.get("scratch", 0)
        session_c = bd.get("session", 0)
        counts = f"{n} checkpoint{'s' if n != 1 else ''}  task:{task_c}  scratch:{scratch_c}  session:{session_c}"
        stag = p.get("stagnation_count", 0)
        stag_str = f"  ⚠ stagnant {stag}×" if stag >= 3 else ""
        age = _fmt_age(p.get("last_active", ""))
        print(f"  {pid}{counts:<52}{age}{stag_str}")


def _do_diff(project_id: str) -> None:
    data = _fetch("/health")
    if not data:
        print("Backend not running. Start it with: context-bridge")
        return

    result = _fetch(f"/diff/{project_id}")
    if result is None:
        print("Not enough task checkpoints to diff. Run more sessions first.")
        return
    if "detail" in result:
        detail = result["detail"]
        if isinstance(detail, dict) and detail.get("error") == "insufficient_history":
            print(detail.get("message", "Not enough task checkpoints to diff."))
        return

    from_cp = result.get("from") or result.get("from_checkpoint", {})
    to_cp = result.get("to") or result.get("to_checkpoint", {})

    from_age = _fmt_age(from_cp.get("completed_at_ts"))
    to_age = _fmt_age(to_cp.get("completed_at_ts"))
    from_dur = from_cp.get("task_duration_ms")
    to_dur = to_cp.get("task_duration_ms")

    from_conf = from_cp.get("planner_confidence")
    to_conf = to_cp.get("planner_confidence")
    if from_conf is not None and to_conf is not None:
        conf_dir = "↑" if to_conf > from_conf else "↓" if to_conf < from_conf else "→"
        conf_str = f"{from_conf:.2f} → {to_conf:.2f}  {conf_dir}"
    else:
        conf_str = "N/A"

    w = 22
    print(f"\n  {project_id}\n")
    print(f"  {'FROM':<{w}}{from_cp.get('task_summary', '')}  ({from_age})")
    print(f"  {'TO':<{w}}{to_cp.get('task_summary', '')}  ({to_age})")
    print()
    print(f"  {'Confidence':<{w}}{conf_str}")
    if from_dur is not None and to_dur is not None:
        vel_dir = "↑ faster" if to_dur < from_dur else "↓ slower" if to_dur > from_dur else "→ same"
        print(f"  {'Velocity':<{w}}{_fmt_ms(from_dur)} → {_fmt_ms(to_dur)}  {vel_dir}")
    bc_from = from_cp.get("planner_blocker_class") or "none"
    bc_to = to_cp.get("planner_blocker_class") or "none"
    print(f"  {'Blocker class':<{w}}{bc_from} → {bc_to}")
    decomp_from = "yes" if from_cp.get("planner_decomposition_suggested") else "no"
    decomp_to = "yes" if to_cp.get("planner_decomposition_suggested") else "no"
    print(f"  {'Decompose':<{w}}{decomp_from} → {decomp_to}")
    next_instr = result.get("next_instruction", "")
    if next_instr:
        print(f"\n  Next:")
        for line in next_instr.splitlines():
            if line.strip():
                print(f"    {line}")
    priority = result.get("priority_focus", [])
    if priority:
        print(f"  Priority: {', '.join(priority[:5])}")
    print()


def _do_export(project_id: str, output_path: str) -> None:
    data = _fetch("/health")
    if not data:
        print("Backend not running. Start it with: context-bridge")
        return

    result = _fetch(f"/snapshot/{project_id}")
    if result is None:
        print(f"Project '{project_id}' not found or has no checkpoints.")
        return

    md = result.get("markdown", "")
    out = Path(output_path)
    out.write_text(md)
    print(f"Snapshot written to {out}")


def _current_pid() -> str:
    """Derive project_id from git for CLI commands."""
    import subprocess as _sp
    try:
        remote = _sp.check_output(
            ["git", "remote", "get-url", "origin"], stderr=_sp.DEVNULL, text=True
        ).strip()
        name = remote.rstrip("/").split("/")[-1].removesuffix(".git")
    except Exception:
        name = Path.cwd().name or "unknown"
    try:
        branch = _sp.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], stderr=_sp.DEVNULL, text=True
        ).strip()
        return f"{name}/{branch}" if branch and branch not in ("", "HEAD") else name
    except Exception:
        return name


def _do_replay() -> None:
    """Show chronological attempt history for the current stagnant task."""
    pid = _current_pid()

    if not _fetch("/health"):
        print("Backend not running. Start it with: context-bridge")
        return

    replay = _fetch(f"/projects/{pid}/replay")
    if not replay or not replay.get("attempts"):
        print(f"No attempt history for {pid}.")
        print("Attempt replay requires the same task appearing in 2+ sessions.")
        return

    task = replay.get("task", "")
    count = replay.get("attempt_count", 0)
    print(f"\n  {pid}")
    print(f"  Task: '{task}'  ·  {count} attempt{'s' if count != 1 else ''}\n")

    for a in replay["attempts"]:
        ts = a.get("timestamp", "")[:16]
        files = ", ".join(a.get("files_modified", [])[:3]) or "none"
        blockers = a.get("blockers", [])
        dur = f"  {a['duration_min']}m" if a.get("duration_min") else ""
        bc = a.get("blocker_class") or ""
        bc_str = f"  [{bc}]" if bc and bc != "none" else ""
        plan = a.get("next_instruction", "")

        print(f"  Attempt {a['attempt']}  {ts}{dur}{bc_str}")
        print(f"    Files:   {files}")
        if blockers:
            print(f"    Blocker: {blockers[0][:100]}")
        if plan:
            print(f"    Plan:    {plan[:100]}")
        print()


def _do_why() -> None:
    """Show stagnation diagnosis and velocity for the current project."""
    pid = _current_pid()

    if not _fetch("/health"):
        print("Backend not running. Start it with: context-bridge")
        return

    print(f"\n  {pid}\n")

    w = 14
    history = _fetch(f"/history/{pid}?limit=1") or []
    current_stag = history[0].get("stagnation_count", 1) if history else 0

    if current_stag >= 3:
        stag = _fetch(f"/projects/{pid}/stagnation-report")
        if stag:
            hours = stag.get("elapsed_hours", 0)
            count = stag.get("checkpoint_count", 0)
            blocker = stag.get("primary_blocker") or "none recorded"
            rec = stag.get("recommendation", "")
            current_task = (history[0].get("current_task", "") if history else "")[:70]
            print(f"  ⚠ STAGNATING\n")
            if current_task:
                print(_row("Task", repr(current_task), w))
            print(_row("Stuck", f"{count} sessions · {hours}h", w))
            print(_row("Blocker", blocker, w))
            if rec:
                print(_row("Action", rec[:160], w))
        else:
            print("  ⚠ Stagnation data unavailable.")
    elif current_stag == 2:
        task = (history[0].get("current_task", "") if history else "")[:70]
        print(f"  ⚠ APPROACHING STAGNATION  (2 sessions on same task)\n")
        if task:
            print(_row("Task", repr(task), w))
        print(_row("Next", "one more repeat triggers forced decomposition", w))
    else:
        print(_row("Stagnation", "none — progressing normally", w))

    print()

    vel = _fetch(f"/velocity/{pid}")
    if vel and vel.get("avg_duration_ms") is not None:
        avg_s = (vel["avg_duration_ms"] or 0) / 1000
        cur_s = (vel.get("current_duration_ms") or 0) / 1000
        avg_m, avg_s2 = divmod(int(avg_s), 60)
        cur_m, cur_s2 = divmod(int(cur_s), 60)
        ratio = vel.get("velocity_ratio")
        alert = vel.get("alert", False)
        ratio_str = f"  {ratio:.1f}×" if ratio is not None else ""
        status = "  ⚠ slower than baseline" if alert else "  on track"
        print(_row("Velocity", f"{cur_m}m {cur_s2}s current  ·  {avg_m}m {avg_s2}s baseline{ratio_str}{status}", w))
    else:
        print(_row("Velocity", "insufficient history (5+ task checkpoints needed)", w))

    # Goal drift
    drift = _fetch(f"/projects/{pid}/goal-drift")
    if drift and drift.get("drifted"):
        goals = drift.get("goals", [])
        print(f"\n  ⚠ GOAL DRIFT  ({drift['distinct_count']} distinct goals in recent sessions)")
        for g in goals[-4:]:
            print(f"    → '{g}'")
        print("  Confirm the current goal before continuing.")

    # Attempt replay summary
    if current_stag >= 2:
        replay = _fetch(f"/projects/{pid}/replay")
        if replay and replay.get("attempts") and len(replay["attempts"]) >= 2:
            print(f"\n  Attempt history ({len(replay['attempts'])} sessions):")
            for a in replay["attempts"]:
                ts = a.get("timestamp", "")[:10]
                blockers = a.get("blockers", [])
                b_str = f"  → {blockers[0][:70]}" if blockers else ""
                dur = f" ({a['duration_min']}m)" if a.get("duration_min") else ""
                print(f"    Attempt {a['attempt']} ({ts}){dur}{b_str}")
            print("  Full details: context-bridge replay")

    print()


# ── Entry point ───────────────────────────────────────────────────────────────

def run() -> None:
    parser = argparse.ArgumentParser(
        prog="context-bridge",
        description="Stagnation detection and session continuity for Claude Code.",
    )
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("install", help="Install skill + lifecycle hooks to ~/.claude/")
    sub.add_parser("uninstall", help="Remove hooks and the installed skill")
    sub.add_parser("start", help="Start the backend server (default)")
    sub.add_parser("status", help="Check backend status and planner configuration")
    sub.add_parser("list", help="List all projects with checkpoint counts and type breakdown")
    sub.add_parser("why", help="Show stagnation diagnosis and velocity for the current project")
    sub.add_parser("replay", help="Show chronological attempt history for the current stagnant task")

    diff_p = sub.add_parser("diff", help="Show what changed between the two most recent task checkpoints")
    diff_p.add_argument("project_id", help="Project ID (reponame/branch)")
    diff_p.add_argument("--branch", help="Branch name (appended to project_id if provided)")

    export_p = sub.add_parser("export", help="Export a CLAUDE.md-compatible Markdown snapshot")
    export_p.add_argument("--project", default="", help="Project ID (defaults to current repo/branch)")
    export_p.add_argument("--output", default="CONTEXT_BRIDGE_SNAPSHOT.md", help="Output file path")

    args = parser.parse_args()

    if args.cmd == "install":
        _do_install()
    elif args.cmd == "uninstall":
        _do_uninstall()
    elif args.cmd == "status":
        _do_status()
    elif args.cmd == "list":
        _do_list()
    elif args.cmd == "why":
        _do_why()
    elif args.cmd == "replay":
        _do_replay()
    elif args.cmd == "diff":
        pid = args.project_id
        if hasattr(args, "branch") and args.branch:
            pid = f"{pid}/{args.branch}"
        _do_diff(pid)
    elif args.cmd == "export":
        _do_export(args.project or _current_pid(), args.output)
    else:
        settings.db_path.parent.mkdir(parents=True, exist_ok=True)
        w = 12
        print(f"Context Bridge  →  http://127.0.0.1:{settings.server_port}")
        print(_row("Dashboard", f"http://127.0.0.1:{settings.server_port}/", w))
        print(_row("Database", str(settings.db_path), w))
        print("Ctrl+C to stop\n")
        uvicorn.run("server.main:app", host="127.0.0.1", port=settings.server_port, log_level="warning")
