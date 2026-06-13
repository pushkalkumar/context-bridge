import argparse
import asyncio
import json
import logging
import shutil
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from .config import settings
from .memory import (
    _SQLITE_VEC_AVAILABLE,
    build_profile,
    build_snapshot,
    build_stagnation_report,
    classify_checkpoint_type,
    compute_stagnation_count,
    compute_task_duration_ms,
    delete_project,
    extract_patterns,
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
    CheckpointAck,
    CheckpointIn,
    DeveloperProfile,
    DiffResponse,
    ErrorResponse,
    PatternsReport,
    ProjectStats,
    ProjectSummary,
    SearchRequest,
    SearchResponse,
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
    description="Persistent memory for Claude Code. Checkpoint history and replanning across sessions.",
    version="0.5.0",
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
async def sync(cp: CheckpointIn) -> SyncResponse:
    """Store a checkpoint and return an authoritative plan."""
    data = _prepare_checkpoint_data(cp)
    stag = compute_stagnation_count(data["project_id"], data["current_task"])
    data["stagnation_count"] = stag

    history = get_recent_checkpoints(data["project_id"], n=10)
    report = build_stagnation_report(data["project_id"], data["current_task"]) if stag >= 3 else None
    result = run_planner(data, history, stag, report)

    # Velocity alert — prepend warning to next_instruction when triggered (ADR-006)
    velocity = get_velocity(data["project_id"])
    if velocity and velocity["alert"]:
        avg_s = (velocity["avg_duration_ms"] or 0) / 1000
        cur_s = (velocity["current_duration_ms"] or 0) / 1000
        ratio = velocity["velocity_ratio"] or 0
        avg_min = int(avg_s // 60)
        avg_sec = int(avg_s % 60)
        cur_min = int(cur_s // 60)
        cur_sec = int(cur_s % 60)
        warning = (
            f"⚠ VELOCITY ALERT: This task is taking {ratio:.1f}x longer than your baseline on this branch.\n"
            f"  Last 10 tasks averaged {avg_min}m {avg_sec}s. Current task has been open {cur_min}m {cur_sec}s.\n"
            f"  Consider: Is this blocked? Is the scope larger than expected? Should it be decomposed?\n\n"
        )
        result.next_instruction = warning + result.next_instruction

    # Store structured planner output back into the blob
    data["_planner_output"] = result.model_dump()
    data["planner_confidence"] = result.confidence
    data["planner_blocker_class"] = result.blocker_class
    data["planner_decomposition_suggested"] = result.decomposition_suggested

    checkpoint_id = save_checkpoint(data)
    save_embedding(checkpoint_id, _embed_text_for(data))

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
    from .models import SearchResult
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


# ── Install command ───────────────────────────────────────────────────────────

_HOOK_EVENTS = ("SessionStart", "PostToolUse", "Stop")


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
    print(f"✓ SessionStart hook  → {hook_dest}")
    print(f"✓ PostToolUse hook   → {hook_dest}")
    print(f"✓ Stop hook          → {hook_dest}")
    print(f"✓ Skill imported     → CLAUDE.md ← {_SKILL_DEST.name}")


def _configure_hooks() -> None:
    hook_cmd = f"python3 {_HOOK_DEST}"
    try:
        settings_data = json.loads(_SETTINGS_PATH.read_text()) if _SETTINGS_PATH.exists() else {}
    except (ValueError, OSError):
        settings_data = {}

    hooks = settings_data.setdefault("hooks", {})
    entries = {
        "SessionStart": {"hooks": [{"type": "command", "command": hook_cmd}]},
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
        _SETTINGS_PATH.write_text(json.dumps(settings_data, indent=2) + "\n")


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
        _SETTINGS_PATH.write_text(json.dumps(settings_data, indent=2) + "\n")
    return changed


def _do_uninstall() -> None:
    if _unconfigure_hooks():
        print(f"✗ Hooks removed      → {_SETTINGS_PATH}")
    for path, label in ((_HOOK_DEST, "Hook script removed"), (_SKILL_DEST, "Skill removed")):
        if path.exists():
            path.unlink()
            print(f"✗ {label:<18} → {path}")
    if _CLAUDE_MD.exists():
        content = _CLAUDE_MD.read_text()
        if _IMPORT_LINE in content:
            _CLAUDE_MD.write_text(content.replace(f"\n\n{_IMPORT_LINE}\n", "\n").replace(f"{_IMPORT_LINE}\n", ""))
            print(f"✗ Import removed     → {_CLAUDE_MD}")
    print("Done. The checkpoint database at ~/.context-bridge/ was not touched.")


def _fetch(path: str, timeout: float = 2.0):
    import urllib.request
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{settings.server_port}{path}", timeout=timeout
        ) as r:
            return json.loads(r.read())
    except Exception:
        return None


def _do_status() -> None:
    data = _fetch("/health")
    if not data:
        print(f"Backend    not running  (start with: context-bridge)")
        return
    print(f"Backend    running on port {data['port']}")
    print(f"DB         {settings.db_path}")

    s = _fetch("/stats")
    if s:
        print(f"Projects   {s['total_projects']}")
        print(f"Checkpoints {s['total_checkpoints']}")
        print(f"Stagnation {s['stagnation_events']} events")

    planner = "rule-based (no LLM configured)"
    if settings.anthropic_api_key:
        planner = "Anthropic (claude-sonnet-4-6)"
    elif settings.resolved_ollama_host():
        planner = f"Ollama ({settings.ollama_model})"
    print(f"Planner    {planner}")
    print(f"Velocity   tracking enabled")
    embed_status = (
        "enabled (voyageai)" if (settings.embedding_api_key() and _SQLITE_VEC_AVAILABLE)
        else "disabled (set VOYAGE_API_KEY or ANTHROPIC_API_KEY and pip install voyageai)"
        if _SQLITE_VEC_AVAILABLE
        else "disabled (sqlite-vec not installed)"
    )
    print(f"Embeddings {embed_status}")


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
        type_str = f" ({task_c} task, {scratch_c} scratch, {session_c} session)" if (task_c + scratch_c + session_c > 0) else ""
        count_str = f"{n} checkpoint{'s' if n != 1 else ''}{type_str}"
        stag = p.get("stagnation_count", 0)
        stag_str = f"  ⚠ stagnant ({stag}x)" if stag >= 3 else ""
        ts = p.get("last_active", "")
        age = ""
        if ts:
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                diff = datetime.now(timezone.utc) - dt
                s = diff.total_seconds()
                if s < 3600:
                    age = f"{int(s // 60)}m ago"
                elif s < 86400:
                    age = f"{int(s // 3600)}h ago"
                else:
                    age = f"{int(s // 86400)}d ago"
            except Exception:
                age = ts
        print(f"  {pid}{count_str:<45}{age}{stag_str}")


def _do_diff(project_id: str) -> None:
    data = _fetch("/health")
    if not data:
        print("Backend not running. Start it with: context-bridge")
        return

    result = _fetch(f"/diff/{project_id}")
    if result is None:
        print(f"Not enough task checkpoints to diff. Run more sessions first.")
        return
    if "detail" in result:
        detail = result["detail"]
        if isinstance(detail, dict) and detail.get("error") == "insufficient_history":
            print(detail.get("message", "Not enough task checkpoints to diff."))
        return

    from_cp = result.get("from") or result.get("from_checkpoint", {})
    to_cp = result.get("to") or result.get("to_checkpoint", {})

    def _fmt_ts(ts_ms):
        if not ts_ms:
            return "unknown"
        try:
            dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
            diff = datetime.now(timezone.utc) - dt
            s = diff.total_seconds()
            if s < 3600:
                return f"{int(s // 60)}m ago"
            elif s < 86400:
                return f"{int(s // 3600)}h ago"
            return f"{int(s // 86400)}d ago"
        except Exception:
            return str(ts_ms)

    def _fmt_ms(ms):
        if ms is None:
            return "unknown"
        m, s = divmod(int(ms / 1000), 60)
        return f"{m}m {s}s"

    from_age = _fmt_ts(from_cp.get("completed_at_ts"))
    to_age = _fmt_ts(to_cp.get("completed_at_ts"))
    from_dur = from_cp.get("task_duration_ms")
    to_dur = to_cp.get("task_duration_ms")
    vel_str = ""
    if from_dur is not None and to_dur is not None:
        vel_str = "faster" if to_dur < from_dur else "slower"

    from_conf = from_cp.get("planner_confidence")
    to_conf = to_cp.get("planner_confidence")
    if from_conf is not None and to_conf is not None:
        conf_dir = "↑" if to_conf > from_conf else "↓" if to_conf < from_conf else "→"
        conf_str = f"{from_conf:.2f} → {to_conf:.2f}  ({conf_dir})"
    else:
        conf_str = "N/A"

    print(f"\n  FROM ({from_age}):  {from_cp.get('task_summary', '')}")
    print(f"  TO   ({to_age}):   {to_cp.get('task_summary', '')}")
    print()
    print(f"  Planner confidence:    {conf_str}")
    if from_dur is not None and to_dur is not None:
        print(f"  Velocity:              {_fmt_ms(from_dur)} → {_fmt_ms(to_dur)}  ({vel_str})")
    print(f"  Blocker class:         {from_cp.get('planner_blocker_class', 'none')} → {to_cp.get('planner_blocker_class', 'none')}")
    decomp_from = "true" if from_cp.get("planner_decomposition_suggested") else "false"
    decomp_to = "true" if to_cp.get("planner_decomposition_suggested") else "false"
    print(f"  Decomposition needed:  {decomp_from} → {decomp_to}")
    next_instr = result.get("next_instruction", "")
    if next_instr:
        print(f"\n  Next instruction:")
        for line in next_instr.splitlines():
            print(f"    {line}")
    priority = result.get("priority_focus", [])
    if priority:
        print(f"    Priority focus: {', '.join(priority[:5])}")
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


# ── Entry point ───────────────────────────────────────────────────────────────

def run() -> None:
    parser = argparse.ArgumentParser(
        prog="context-bridge",
        description="Persistent memory for Claude Code.",
    )
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("install", help="Install skill + lifecycle hooks to ~/.claude/")
    sub.add_parser("uninstall", help="Remove hooks and the installed skill")
    sub.add_parser("start", help="Start the backend server (default)")
    sub.add_parser("status", help="Check backend status and planner configuration")
    sub.add_parser("list", help="List all projects with checkpoint counts and type breakdown")

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
    elif args.cmd == "diff":
        pid = args.project_id
        if hasattr(args, "branch") and args.branch:
            pid = f"{pid}/{args.branch}"
        _do_diff(pid)
    elif args.cmd == "export":
        pid = args.project or ""
        if not pid:
            # derive from git
            import subprocess
            try:
                remote = subprocess.check_output(
                    ["git", "remote", "get-url", "origin"], stderr=subprocess.DEVNULL, text=True
                ).strip()
                name = remote.rstrip("/").split("/")[-1].removesuffix(".git")
            except Exception:
                name = Path.cwd().name or "unknown"
            try:
                branch = subprocess.check_output(
                    ["git", "rev-parse", "--abbrev-ref", "HEAD"], stderr=subprocess.DEVNULL, text=True
                ).strip()
                pid = f"{name}/{branch}" if branch and branch != "HEAD" else name
            except Exception:
                pid = name
        _do_export(pid, args.output)
    else:
        settings.db_path.parent.mkdir(parents=True, exist_ok=True)
        print(f"Context Bridge  http://127.0.0.1:{settings.server_port}")
        print(f"Dashboard       http://127.0.0.1:{settings.server_port}/")
        print(f"DB              {settings.db_path}")
        print("Ctrl+C to stop.\n")
        uvicorn.run("server.main:app", host="127.0.0.1", port=settings.server_port, log_level="warning")
