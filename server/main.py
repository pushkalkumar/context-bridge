import argparse
import json
import logging
import shutil
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from .config import settings
from .memory import (
    compute_stagnation_count,
    get_all_projects,
    get_recent_checkpoints,
    init_db,
    save_checkpoint,
)
from .models import CheckpointIn, SyncResponse
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("Context Bridge started — DB at %s", settings.db_path)
    yield


app = FastAPI(title="Context Bridge", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def dashboard():
    return HTMLResponse(_DASHBOARD)


@app.post("/checkpoint")
async def checkpoint(cp: CheckpointIn):
    """Store a checkpoint without running the planner. Returns stagnation_count."""
    data = cp.model_dump()
    if not data["timestamp"]:
        data["timestamp"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    if not data["project_id"]:
        data["project_id"] = str(uuid.uuid4())[:8]
    stag = compute_stagnation_count(data["project_id"], data["current_task"])
    data["stagnation_count"] = stag
    save_checkpoint(data)
    logger.info("checkpoint stored project=%s task=%r stagnation=%d", data["project_id"], data["current_task"], stag)
    return {"project_id": data["project_id"], "stagnation_count": stag}


@app.post("/sync", response_model=SyncResponse)
async def sync(cp: CheckpointIn):
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

    logger.info("sync project=%s task=%r source=%s stagnation=%d", data["project_id"], data["current_task"], result.source, stag)
    return result


@app.get("/health")
async def health():
    return {"status": "ok", "service": "context-bridge", "port": settings.server_port}


@app.get("/projects")
async def projects():
    return get_all_projects()


@app.get("/history/{project_id}")
async def history(project_id: str, limit: int = 50):
    return get_recent_checkpoints(project_id, n=min(limit, 100))


# ── Install command ───────────────────────────────────────────────────────────

def _do_install() -> None:
    _CLAUDE_DIR.mkdir(parents=True, exist_ok=True)

    # Skill file → ~/.claude/context-bridge.md
    if _SKILL_SRC.exists():
        shutil.copy(_SKILL_SRC, _SKILL_DEST)
        print(f"  Skill     → {_SKILL_DEST}")
    else:
        print(f"  WARNING: skill source missing at {_SKILL_SRC}")
        return

    # Wire @import to ~/.claude/CLAUDE.md
    if _CLAUDE_MD.exists():
        content = _CLAUDE_MD.read_text()
        if _IMPORT_LINE not in content:
            _CLAUDE_MD.write_text(content.rstrip() + f"\n\n{_IMPORT_LINE}\n")
            print(f"  Wired     → {_CLAUDE_MD}")
        else:
            print(f"  Already   → {_CLAUDE_MD}")
    else:
        _CLAUDE_MD.write_text(f"{_IMPORT_LINE}\n")
        print(f"  Created   → {_CLAUDE_MD}")

    # Hook script → ~/.claude/context-bridge-hook.py
    if _HOOK_SRC.exists():
        shutil.copy(_HOOK_SRC, _HOOK_DEST)
        _HOOK_DEST.chmod(0o755)
        print(f"  Hook      → {_HOOK_DEST}")

    # Lifecycle hooks in ~/.claude/settings.json
    _configure_hooks()

    print()
    print("✅ Done. Hooks active: SessionStart restores context. PostToolUse auto-checkpoints.")


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
        print(f"  Hooks     → {_SETTINGS_PATH}")
    else:
        print(f"  Hooks OK  → {_SETTINGS_PATH}")


# ── Entry point ───────────────────────────────────────────────────────────────

def run() -> None:
    parser = argparse.ArgumentParser(
        prog="context-bridge",
        description="Persistent memory for Claude Code — checkpoint-based replanning.",
    )
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("install", help="Install skill + hooks to ~/.claude/")
    sub.add_parser("start", help="Start the backend server (default)")
    args = parser.parse_args()

    if args.cmd == "install":
        _do_install()
    else:
        settings.db_path.parent.mkdir(parents=True, exist_ok=True)
        print(f"Context Bridge  http://127.0.0.1:{settings.server_port}")
        print(f"Dashboard       http://127.0.0.1:{settings.server_port}/")
        print(f"DB              {settings.db_path}")
        print("Press Ctrl+C to stop.\n")
        uvicorn.run("server.main:app", host="127.0.0.1", port=settings.server_port)
