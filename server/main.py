import argparse
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
    compute_stagnation_count,
    delete_project,
    get_all_projects,
    get_recent_checkpoints,
    get_stats,
    init_db,
    project_exists,
    save_checkpoint,
)
from .models import (
    CheckpointAck,
    CheckpointIn,
    ErrorResponse,
    ProjectStats,
    ProjectSummary,
    SyncResponse,
)
from .planner import run_planner

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

_DASHBOARD = (Path(__file__).parent / "dashboard.html").read_text()
_SKILL_SRC = Path(__file__).parent / "skill.md"
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("Context Bridge started  db=%s  port=%d", settings.db_path, settings.server_port)
    yield


app = FastAPI(
    title="Context Bridge",
    description="Persistent memory for Claude Code. Checkpoint history and replanning across sessions.",
    version="0.2.0",
    lifespan=lifespan,
)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def dashboard() -> HTMLResponse:
    return HTMLResponse(_DASHBOARD)


@app.post("/checkpoint", response_model=CheckpointAck)
async def checkpoint(cp: CheckpointIn) -> CheckpointAck:
    """Store a checkpoint without running the planner."""
    data = cp.model_dump()
    if not data["timestamp"]:
        data["timestamp"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    if not data["project_id"]:
        data["project_id"] = str(uuid.uuid4())[:8]
    stag = compute_stagnation_count(data["project_id"], data["current_task"])
    data["stagnation_count"] = stag
    save_checkpoint(data)
    logger.info("checkpoint  project=%s  task=%r  stagnation=%d", data["project_id"], data["current_task"], stag)
    return CheckpointAck(project_id=data["project_id"], stagnation_count=stag)


@app.post("/sync", response_model=SyncResponse)
async def sync(cp: CheckpointIn) -> SyncResponse:
    """Store a checkpoint and return an authoritative plan."""
    data = cp.model_dump()
    if not data["timestamp"]:
        data["timestamp"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    if not data["project_id"]:
        data["project_id"] = str(uuid.uuid4())[:8]

    stag = compute_stagnation_count(data["project_id"], data["current_task"])
    data["stagnation_count"] = stag

    history = get_recent_checkpoints(data["project_id"], n=10)
    result = run_planner(data, history, stag)
    data["_planner_output"] = result.model_dump()
    save_checkpoint(data)

    logger.info(
        "sync  project=%s  task=%r  source=%s  stagnation=%d",
        data["project_id"], data["current_task"], result.source, stag,
    )
    return result


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "context-bridge", "port": settings.server_port}


@app.get("/stats", response_model=ProjectStats)
async def stats() -> ProjectStats:
    """Overall database statistics."""
    return ProjectStats(**get_stats())


@app.get("/projects", response_model=list[ProjectSummary])
async def projects() -> list[ProjectSummary]:
    return [ProjectSummary(**p) for p in get_all_projects()]


@app.delete("/projects/{project_id}")
async def delete(project_id: str) -> dict:
    """Delete a project and all its checkpoints."""
    if not project_exists(project_id):
        raise _not_found(project_id)
    count = delete_project(project_id)
    logger.info("deleted  project=%s  checkpoints=%d", project_id, count)
    return {"deleted": count}


@app.get("/history/{project_id}", response_model=list[dict])
async def history(project_id: str, limit: int = 50) -> list[dict]:
    if not project_exists(project_id):
        raise _not_found(project_id)
    return get_recent_checkpoints(project_id, n=min(limit, 100))


@app.get("/projects/{project_id}/export")
async def export(project_id: str) -> JSONResponse:
    """Download all checkpoints for a project as JSON."""
    if not project_exists(project_id):
        raise _not_found(project_id)
    data = get_recent_checkpoints(project_id, n=10_000)
    filename = f"context-bridge-{project_id}.json"
    return JSONResponse(
        content=data,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Install command ───────────────────────────────────────────────────────────

def _do_install() -> None:
    _CLAUDE_DIR.mkdir(parents=True, exist_ok=True)

    if _SKILL_SRC.exists():
        shutil.copy(_SKILL_SRC, _SKILL_DEST)
        print(f"  Skill     -> {_SKILL_DEST}")
    else:
        print(f"  WARNING: skill source missing at {_SKILL_SRC}")
        return

    if _CLAUDE_MD.exists():
        content = _CLAUDE_MD.read_text()
        if _IMPORT_LINE not in content:
            _CLAUDE_MD.write_text(content.rstrip() + f"\n\n{_IMPORT_LINE}\n")
            print(f"  Wired     -> {_CLAUDE_MD}")
        else:
            print(f"  Already   -> {_CLAUDE_MD}")
    else:
        _CLAUDE_MD.write_text(f"{_IMPORT_LINE}\n")
        print(f"  Created   -> {_CLAUDE_MD}")

    if _HOOK_SRC.exists():
        shutil.copy(_HOOK_SRC, _HOOK_DEST)
        _HOOK_DEST.chmod(0o755)
        print(f"  Hook      -> {_HOOK_DEST}")

    _configure_hooks()

    print()
    print("Done. Hooks active:")
    print("  SessionStart  restores last checkpoint before your first message")
    print("  PostToolUse   auto-checkpoints after every Task completion")


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
    }
    changed = False
    for event, entry in entries.items():
        existing = hooks.get(event, [])
        if not any(any(h.get("command") == hook_cmd for h in e.get("hooks", [])) for e in existing):
            hooks[event] = existing + [entry]
            changed = True

    if changed:
        _SETTINGS_PATH.write_text(json.dumps(settings_data, indent=2) + "\n")
        print(f"  Hooks     -> {_SETTINGS_PATH}")
    else:
        print(f"  Hooks OK  -> {_SETTINGS_PATH}")


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


def _do_list() -> None:
    data = _fetch("/health")
    if not data:
        print("Backend not running. Start it with: context-bridge")
        return

    projects = _fetch("/projects")
    if not projects:
        print("No projects yet. Open Claude Code in a git repo and run a task.")
        return

    col_w = max(len(p["project_id"]) for p in projects) + 2
    for p in projects:
        pid = p["project_id"].ljust(col_w)
        n = f"{p['checkpoint_count']} checkpoint{'s' if p['checkpoint_count'] != 1 else ''}"
        stag = p.get("stagnation_count", 0)
        stag_str = f"  ⚠ stagnant ({stag}x)" if stag >= 3 else ""
        ts = p.get("last_active", "")
        if ts:
            from datetime import datetime, timezone
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
        else:
            age = ""
        print(f"  {pid}{n:<20}{age}{stag_str}")


# ── Entry point ───────────────────────────────────────────────────────────────

def run() -> None:
    parser = argparse.ArgumentParser(
        prog="context-bridge",
        description="Persistent memory for Claude Code.",
    )
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("install", help="Install skill + lifecycle hooks to ~/.claude/")
    sub.add_parser("start",   help="Start the backend server (default)")
    sub.add_parser("status",  help="Check backend status and planner configuration")
    sub.add_parser("list",    help="List all projects with checkpoint counts")
    args = parser.parse_args()

    if args.cmd == "install":
        _do_install()
    elif args.cmd == "status":
        _do_status()
    elif args.cmd == "list":
        _do_list()
    else:
        settings.db_path.parent.mkdir(parents=True, exist_ok=True)
        print(f"Context Bridge  http://127.0.0.1:{settings.server_port}")
        print(f"Dashboard       http://127.0.0.1:{settings.server_port}/")
        print(f"DB              {settings.db_path}")
        print("Ctrl+C to stop.\n")
        uvicorn.run("server.main:app", host="127.0.0.1", port=settings.server_port, log_level="warning")
