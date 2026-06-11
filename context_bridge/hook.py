#!/usr/bin/env python3
"""
Context Bridge lifecycle hook for Claude Code.

Handles three concerns automatically:
  SessionStart  — fetch last checkpoint, inject context summary into session
  PostToolUse   — every 5 tool calls: poll for priority_focus change
                — on Task completion: auto-checkpoint with real git diff metadata
"""
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE_URL = os.environ.get("CONTEXT_BRIDGE_URL", "http://127.0.0.1:8000")
_STATE_DIR = Path("/tmp/context-bridge-hooks")


# ── Session state helpers ─────────────────────────────────────────────────────

def _state_path(session_id, key):
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    # Truncate session_id to avoid filesystem limits
    return _STATE_DIR / "{}_{}.txt".format(session_id[:20], key)


def _read_state(session_id, key, default=""):
    p = _state_path(session_id, key)
    return p.read_text().strip() if p.exists() else default


def _write_state(session_id, key, value):
    _state_path(session_id, key).write_text(str(value))


# ── Project ID derivation ─────────────────────────────────────────────────────

def _derive_project_id():
    try:
        remote = subprocess.check_output(
            ["git", "remote", "get-url", "origin"],
            stderr=subprocess.DEVNULL, text=True,
        ).strip()
        name = remote.rstrip("/").split("/")[-1]
        if name.endswith(".git"):
            name = name[:-4]
    except Exception:
        name = Path.cwd().name
    date = datetime.now(timezone.utc).strftime("%Y%m%d")
    return "{}-{}".format(name, date)


# ── Git metadata collection ───────────────────────────────────────────────────

def _get_git_metadata():
    meta = {}
    try:
        diff = subprocess.check_output(
            ["git", "diff", "--stat", "HEAD"],
            stderr=subprocess.DEVNULL, text=True,
        ).strip()
        meta["git_diff_stat"] = diff or "(no uncommitted changes)"
        meta["git_log_recent"] = subprocess.check_output(
            ["git", "log", "--oneline", "-5"],
            stderr=subprocess.DEVNULL, text=True,
        ).strip()
    except Exception:
        # Not a git repo — fall back to mtime-based file scan (last hour)
        try:
            cwd = Path.cwd()
            cutoff = datetime.now().timestamp() - 3600
            recent = sorted(
                (
                    str(p.relative_to(cwd))
                    for p in cwd.rglob("*")
                    if p.is_file()
                    and p.stat().st_mtime > cutoff
                    and not any(part.startswith(".") for part in p.parts)
                ),
                key=lambda f: Path(cwd / f).stat().st_mtime,
                reverse=True,
            )
            meta["recent_files_mtime"] = recent[:20]
        except Exception:
            pass
    return meta


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _http_get(path):
    try:
        with urllib.request.urlopen("{}{}".format(BASE_URL, path), timeout=5) as r:
            return json.loads(r.read())
    except Exception:
        return None


def _http_post(path, payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        "{}{}".format(BASE_URL, path),
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception:
        return None


# ── Response contract validation (#6) ────────────────────────────────────────

def _validate_response(response):
    """Return True iff response carries a non-empty next_instruction."""
    if not response:
        return False
    if not (response.get("next_instruction") or "").strip():
        print(
            "[context-bridge] WARNING: /sync returned empty next_instruction — "
            "verify the backend is healthy and the planner is not misconfigured.",
            file=sys.stderr,
        )
        return False
    return True


# ── SessionStart handler (#2) ─────────────────────────────────────────────────

def _handle_session_start(event):
    session_id = event.get("session_id", "default")
    project_id = _derive_project_id()
    _write_state(session_id, "project_id", project_id)
    _write_state(session_id, "tool_count", "0")

    history = _http_get("/history/{}?limit=1".format(project_id))
    if not history:
        return  # backend not running or no history yet

    latest = history[0]
    planner = latest.get("_planner_output") or {}
    next_instr = (planner.get("next_instruction") or "").strip()
    ctx_summary = (planner.get("context_summary") or "").strip()
    priority = (planner.get("priority_focus") or "").strip()

    if priority:
        _write_state(session_id, "priority", priority)
    if latest.get("user_goal"):
        _write_state(session_id, "goal", latest["user_goal"])

    if not (next_instr or ctx_summary):
        return

    lines = ["[context-bridge] Session context restored:"]
    if ctx_summary:
        lines.append("  Summary:  {}".format(ctx_summary))
    if next_instr:
        lines.append("  Next:     {}".format(next_instr))
    if priority:
        lines.append("  Priority: {}".format(priority))
    print("\n".join(lines))


# ── PostToolUse handler (#1 + #4) ─────────────────────────────────────────────

def _handle_post_tool_use(event):
    session_id = event.get("session_id", "default")
    tool_name = event.get("tool_name", "")

    count = int(_read_state(session_id, "tool_count", "0")) + 1
    _write_state(session_id, "tool_count", str(count))

    # Every 5th tool call: poll for priority_focus change (mid-session replanning)
    if count % 5 == 0:
        project_id = _read_state(session_id, "project_id") or _derive_project_id()
        history = _http_get("/history/{}?limit=1".format(project_id))
        if history:
            planner = (history[0].get("_planner_output") or {})
            new_priority = (planner.get("priority_focus") or "").strip()
            old_priority = _read_state(session_id, "priority")
            if new_priority and new_priority != old_priority:
                _write_state(session_id, "priority", new_priority)
                print("[context-bridge] Priority focus updated: {}".format(new_priority))

    # On Task completion: auto-checkpoint (#1 + #3)
    if tool_name == "Task":
        _auto_checkpoint(event, session_id)


# ── Auto-checkpoint on Task completion (#1 + #3 + #6) ────────────────────────

def _auto_checkpoint(event, session_id):
    tool_input = event.get("tool_input") or {}
    tool_response = event.get("tool_response") or {}

    project_id = _read_state(session_id, "project_id") or _derive_project_id()
    user_goal = (
        _read_state(session_id, "goal")
        or "(user goal not recorded — post a manual /sync to set it)"
    )
    current_task = (
        tool_input.get("description")
        or str(tool_input.get("prompt", ""))[:120]
        or "(auto-checkpoint)"
    )

    # Real file diffing (#3)
    git = _get_git_metadata()
    files_modified = []
    diff_stat = git.get("git_diff_stat", "")
    if diff_stat and diff_stat != "(no uncommitted changes)":
        for line in diff_stat.splitlines():
            if "|" in line:
                fname = line.split("|")[0].strip()
                if fname:
                    files_modified.append(fname)
    if not files_modified:
        files_modified = git.get("recent_files_mtime", [])

    # Extract result text and simple blocker detection
    if isinstance(tool_response, dict):
        result_text = str(tool_response.get("result", tool_response.get("output", "")))
    else:
        result_text = str(tool_response)

    blockers = []
    for line in result_text.splitlines():
        if any(kw in line.lower() for kw in ("error:", "failed:", "blocked:", "unable to")):
            blockers.append(line.strip()[:200])
            break

    checkpoint = {
        "project_id": project_id,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        "user_goal": user_goal,
        "current_task": current_task,
        "progress_summary": result_text[:500] if result_text else "(task completed)",
        "current_state": {
            "files_modified": files_modified,
            "code_summary": "",
            "architecture_notes": "",
            # Real git metadata attached automatically
            "git_diff_stat": git.get("git_diff_stat"),
            "git_log_recent": git.get("git_log_recent"),
        },
        "blockers": blockers,
        "next_intended_action": "(auto-checkpoint — awaiting planner instruction)",
    }

    response = _http_post("/sync", checkpoint)

    # Response contract enforcement (#6)
    if not _validate_response(response):
        return

    priority = (response.get("priority_focus") or "").strip()
    old_priority = _read_state(session_id, "priority")
    if priority:
        _write_state(session_id, "priority", priority)

    next_instr = (response.get("next_instruction") or "").strip()
    if priority and priority != old_priority:
        print("[context-bridge] Checkpoint saved. Priority: {}".format(priority))
    elif next_instr:
        print("[context-bridge] Checkpoint saved. Next: {}".format(next_instr[:120]))
    else:
        print("[context-bridge] Checkpoint saved.")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    raw = sys.stdin.read().strip()
    if not raw:
        return
    try:
        event = json.loads(raw)
    except ValueError:
        return

    hook_type = event.get("hook_event_name") or event.get("hook_type", "")
    if hook_type == "SessionStart":
        _handle_session_start(event)
    elif hook_type == "PostToolUse":
        _handle_post_tool_use(event)


if __name__ == "__main__":
    main()
