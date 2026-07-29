import argparse
import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from .cli import (
    current_pid,
    do_diff,
    do_event,
    do_export,
    do_forget,
    do_list,
    do_replay,
    do_status,
    do_sync,
    do_why,
)
from .config import settings
from .install import do_install, do_uninstall
from .memory import (
    _SQLITE_VEC_AVAILABLE,
    build_attempt_replay,
    build_profile,
    build_snapshot,
    build_stagnation_report,
    classify_checkpoint_type,
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
    version="0.8.0",
    lifespan=lifespan,
)


def _prepare_checkpoint_data(cp: CheckpointIn) -> dict:
    """Convert CheckpointIn to a storage dict with all computed fields."""
    data = cp.model_dump(mode="json")
    if not data["timestamp"]:
        data["timestamp"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    if not data["project_id"]:
        data["project_id"] = str(uuid.uuid4())[:8]

    completed_at_ts = data.get("completed_at_ts") or int(datetime.now(timezone.utc).timestamp() * 1000)
    data["completed_at_ts"] = completed_at_ts
    data["task_duration_ms"] = compute_task_duration_ms(data["project_id"], completed_at_ts)

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
async def checkpoint(cp: CheckpointIn, background_tasks: BackgroundTasks) -> CheckpointAck:
    """Store a checkpoint without running the planner."""
    data = _prepare_checkpoint_data(cp)
    history = get_recent_checkpoints(data["project_id"], n=10)
    stag = compute_stagnation_from_history(data["current_task"], history)
    data["stagnation_count"] = stag
    checkpoint_id = save_checkpoint(data)
    background_tasks.add_task(save_embedding, checkpoint_id, _embed_text_for(data))
    logger.info(
        "checkpoint  project=%s  task=%r  stagnation=%d  type=%s",
        data["project_id"], data["current_task"], stag, data["checkpoint_type"],
    )
    return CheckpointAck(project_id=data["project_id"], stagnation_count=stag)


@app.post("/sync", response_model=SyncResponse)
async def sync(cp: CheckpointIn, background_tasks: BackgroundTasks) -> SyncResponse:
    """Store a checkpoint and return an authoritative plan."""
    data = _prepare_checkpoint_data(cp)

    history = get_recent_checkpoints(data["project_id"], n=10)
    stag = compute_stagnation_from_history(data["current_task"], history)
    data["stagnation_count"] = stag

    report = build_stagnation_report(data["project_id"], data["current_task"]) if stag >= 3 else None
    attempt_replay_data = build_attempt_replay(data["project_id"], data["current_task"]) if stag >= 2 else None
    result = run_planner(data, history, stag, report, attempt_replay_data)

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

    if data.get("blockers"):
        match = find_similar_blocker(data["project_id"], data["blockers"][0])
        if match:
            result.blocker_match = match

    data["_planner_output"] = result.model_dump()
    data["planner_confidence"] = result.confidence
    data["planner_blocker_class"] = result.blocker_class
    data["planner_decomposition_suggested"] = result.decomposition_suggested

    checkpoint_id = save_checkpoint(data)
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
    return get_recent_checkpoints(project_id, n=min(limit, 100))


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


@app.post("/search", response_model=SearchResponse)
async def search(req: SearchRequest) -> SearchResponse:
    """Semantic KNN search over task/session checkpoints across projects."""
    results = search_checkpoints(req.query, req.limit, req.exclude_project_id)
    return SearchResponse(results=[SearchResult(**r) for r in results])


@app.get("/diff/{project_id:path}", response_model=DiffResponse)
async def diff(project_id: str) -> DiffResponse:
    """Compare the two most recent task checkpoints."""
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


@app.get("/snapshot/{project_id:path}")
async def snapshot(project_id: str) -> JSONResponse:
    """Generate a CLAUDE.md-compatible Markdown snapshot of the project."""
    if not project_exists(project_id):
        raise _not_found(project_id)
    md = build_snapshot(project_id)
    if md is None:
        raise _not_found(project_id)
    return JSONResponse(content={"markdown": md})


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


# ── Entry point ───────────────────────────────────────────────────────────────

def run() -> None:
    parser = argparse.ArgumentParser(
        prog="context-bridge",
        description="Stagnation detection and session continuity for Claude Code.",
    )
    parser.add_argument("--version", "-V", action="version", version="context-bridge 0.8.0")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("install",   help="Install skill + lifecycle hooks to ~/.claude/")
    sub.add_parser("uninstall", help="Remove hooks and the installed skill")
    sub.add_parser("start",     help="Start the backend server (default)")
    sub.add_parser("status",    help="Check backend status and planner configuration")
    sub.add_parser("list",      help="List all projects with checkpoint counts and type breakdown")
    sub.add_parser("why",       help="Show stagnation diagnosis and velocity for the current project")
    sub.add_parser("replay",    help="Show chronological attempt history for the current stagnant task")
    sub.add_parser("stats",     help="Alias for status")

    sync_p = sub.add_parser("sync", help="Checkpoint the current task and print the plan (used by the Claude skill)")
    sync_p.add_argument("--goal", required=True, help="Session goal")
    sync_p.add_argument("--task", required=True, help="Current task")
    sync_p.add_argument("--progress", required=True, help="What changed since the last sync")
    sync_p.add_argument("--next", dest="next_action", default="", help="Next intended action")
    sync_p.add_argument("--blocker", dest="blockers", action="append", default=[],
                        help="Blocker hit (repeatable; paste the exact error)")

    event_p = sub.add_parser("event", help="Record a structured event: failure, adr, or outcome")
    event_p.add_argument("kind", choices=["failure", "adr", "outcome"])
    event_p.add_argument("--goal", default="", help="Session goal (defaults to last checkpoint's)")
    event_p.add_argument("--task", default="", help="Related task (defaults to last checkpoint's)")
    event_p.add_argument("--attempted", default="", help="failure: what was tried")
    event_p.add_argument("--because", default="", help="failure: why it was abandoned")
    event_p.add_argument("--decision", default="", help="adr: the decision made")
    event_p.add_argument("--reason", default="", help="adr: why")
    event_p.add_argument("--tradeoff", default="", help="adr: accepted tradeoff")
    event_p.add_argument("--result", default="", help="outcome: what happened")
    event_p.add_argument("--impact", default="", help="outcome: why it matters")

    forget_p = sub.add_parser("forget", help="Delete all checkpoints for a project, clearing stagnation state")
    forget_p.add_argument("project_id", nargs="?", default="", help="Project ID to forget (defaults to current repo/branch)")

    diff_p = sub.add_parser("diff", help="Show what changed between the two most recent task checkpoints")
    diff_p.add_argument("project_id", help="Project ID (reponame/branch)")
    diff_p.add_argument("--branch", help="Branch name (appended to project_id if provided)")

    export_p = sub.add_parser("export", help="Export a CLAUDE.md-compatible Markdown snapshot")
    export_p.add_argument("--project", default="", help="Project ID (defaults to current repo/branch)")
    export_p.add_argument("--output", default="CONTEXT_BRIDGE_SNAPSHOT.md", help="Output file path")

    args = parser.parse_args()

    if args.cmd == "install":
        do_install()
    elif args.cmd == "uninstall":
        do_uninstall()
    elif args.cmd in ("status", "stats"):
        do_status()
    elif args.cmd == "list":
        do_list()
    elif args.cmd == "why":
        do_why()
    elif args.cmd == "replay":
        do_replay()
    elif args.cmd == "sync":
        do_sync(args.goal, args.task, args.progress, args.next_action, args.blockers)
    elif args.cmd == "event":
        do_event(
            args.kind,
            {
                "attempted": args.attempted, "because": args.because,
                "decision": args.decision, "reason": args.reason, "tradeoff": args.tradeoff,
                "result": args.result, "impact": args.impact,
            },
            args.goal,
            args.task,
        )
    elif args.cmd == "forget":
        pid = args.project_id or current_pid()
        do_forget(pid)
    elif args.cmd == "diff":
        pid = args.project_id
        if hasattr(args, "branch") and args.branch:
            pid = f"{pid}/{args.branch}"
        do_diff(pid)
    elif args.cmd == "export":
        do_export(args.project or current_pid(), args.output)
    else:
        settings.db_path.parent.mkdir(parents=True, exist_ok=True)
        print(f"Context Bridge  http://127.0.0.1:{settings.server_port}")
        print(f"Dashboard       http://127.0.0.1:{settings.server_port}/")
        print(f"DB              {settings.db_path}")
        print("Ctrl+C to stop.\n")
        uvicorn.run("server.main:app", host="127.0.0.1", port=settings.server_port, log_level="warning")
