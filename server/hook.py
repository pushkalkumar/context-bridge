#!/usr/bin/env python3
"""
Context Bridge lifecycle hook for Claude Code.

Installed to ~/.claude/context-bridge-hook.py by `context-bridge install`.
Pure stdlib — no external dependencies.

Handles:
  SessionStart  — inject last checkpoint context before first message
  PostToolUse   — auto-checkpoint on Task completion (with git diff)
                — poll priority change every 5 tool calls
  Stop          — end-of-session checkpoint with call-count context
"""
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE_URL = os.environ.get("CONTEXT_BRIDGE_URL", "http://127.0.0.1:7723")
_STATE_DIR = Path("/tmp/context-bridge-hooks")
_TASK_TOOL_NAMES = {"Task", "task"}

_SEARCH_SIMILARITY_THRESHOLD = 0.75


# ── Session state ─────────────────────────────────────────────────────────────

def _sanitize_sid(sid: str) -> str:
    return sid.replace("/", "_").replace("\\", "_")


def _sp(sid: str, key: str) -> Path:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    return _STATE_DIR / f"{_sanitize_sid(sid)}_{key}.txt"


def _read(sid: str, key: str, default: str = "") -> str:
    p = _sp(sid, key)
    return p.read_text().strip() if p.exists() else default


def _write(sid: str, key: str, value: str) -> None:
    _sp(sid, key).write_text(str(value))


# ── Project ID ────────────────────────────────────────────────────────────────

def _project_id() -> str:
    """Stable ID: reponame/branch (e.g. my-app/main). No date suffix."""
    try:
        remote = subprocess.check_output(
            ["git", "remote", "get-url", "origin"],
            stderr=subprocess.DEVNULL, text=True,
        ).strip()
        name = remote.rstrip("/").split("/")[-1].removesuffix(".git")
    except Exception:
        name = Path.cwd().name or "unknown"

    try:
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            stderr=subprocess.DEVNULL, text=True,
        ).strip()
        if branch and branch not in ("HEAD", ""):
            return f"{name}/{branch}"
    except Exception:
        pass

    return name


# ── Git metadata ──────────────────────────────────────────────────────────────

def _git_meta() -> dict:
    meta: dict = {}
    try:
        meta["git_diff_stat"] = subprocess.check_output(
            ["git", "diff", "--stat", "HEAD"], stderr=subprocess.DEVNULL, text=True,
        ).strip() or "(no uncommitted changes)"
        meta["git_log_recent"] = subprocess.check_output(
            ["git", "log", "--oneline", "-5"], stderr=subprocess.DEVNULL, text=True,
        ).strip()
        # New: name-status for new-file detection in checkpoint_type classification
        meta["git_name_status"] = subprocess.check_output(
            ["git", "diff", "--name-status", "HEAD"], stderr=subprocess.DEVNULL, text=True,
        ).strip()
    except Exception:
        try:
            cwd = Path.cwd()
            cutoff = datetime.now().timestamp() - 3600
            meta["recent_files_mtime"] = sorted(
                (
                    str(p.relative_to(cwd))
                    for p in cwd.rglob("*")
                    if p.is_file()
                    and p.stat().st_mtime > cutoff
                    and not any(part.startswith(".") for part in p.parts)
                ),
                key=lambda f: (cwd / f).stat().st_mtime,
                reverse=True,
            )[:20]
        except Exception:
            pass
    return meta


# ── HTTP ──────────────────────────────────────────────────────────────────────

def _get(path: str):
    try:
        with urllib.request.urlopen(f"{BASE_URL}{path}", timeout=5) as r:
            return json.loads(r.read())
    except Exception:
        return None


def _post(path: str, payload: dict):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{BASE_URL}{path}", data=data,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception:
        return None


# ── Response contract ─────────────────────────────────────────────────────────

def _validate(response) -> bool:
    if not response:
        return False
    if not (response.get("next_instruction") or "").strip():
        print(
            "[context-bridge] WARNING: /sync returned empty next_instruction — "
            "verify the backend is healthy.",
            file=sys.stderr,
        )
        return False
    return True


# ── Semantic search (Task 4) ──────────────────────────────────────────────────

def _related_work_lines(next_instr: str, current_pid: str) -> list[str]:
    """Query /search and format a RELATED PAST WORK block if similarity >= threshold."""
    if not next_instr.strip():
        return []
    results = _post("/search", {
        "query": next_instr[:500],
        "limit": 3,
        "exclude_project_id": current_pid,
    })
    if not results or not isinstance(results.get("results"), list):
        return []
    for r in results["results"]:
        if r.get("similarity", 0) >= _SEARCH_SIMILARITY_THRESHOLD:
            pid = r.get("project_id", "")
            ts = r.get("completed_at_ts")
            if ts:
                try:
                    dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
                    diff_s = (datetime.now(timezone.utc) - dt).total_seconds()
                    if diff_s < 86400:
                        age = f"{int(diff_s // 3600)}h ago"
                    else:
                        age = f"{int(diff_s // 86400)} days ago"
                except Exception:
                    age = "recently"
            else:
                age = "previously"
            sim = r.get("similarity", 0)
            task_summary = r.get("task_summary", "")
            planner_instr = r.get("planner_next_instruction", "")
            lines = [f"📎 RELATED PAST WORK (from {pid}, {age}, similarity {sim:.2f}):"]
            if task_summary:
                lines.append(f"  Task: {task_summary}")
            if planner_instr:
                lines.append(f"  What worked: {planner_instr}")
            return lines
    return []


# ── SessionStart ──────────────────────────────────────────────────────────────

def _profile_lines() -> list[str]:
    """Computed cross-project developer profile, shown when a project has no history."""
    profile = _get("/profile")
    if not profile or not (profile.get("checkpoint_count") or profile.get("total_task_checkpoints")):
        return []

    total_tasks = profile.get("total_task_checkpoints") or profile.get("checkpoint_count", 0)
    total_projects = profile.get("total_projects") or profile.get("project_count", 0)

    lines = [f"🧑‍💻 DEVELOPER PROFILE (computed from {total_tasks} tasks across {total_projects} projects):"]

    # Preferred stack from computed field, fall back to tech_patterns
    stack = profile.get("preferred_stack") or [t["text"] for t in profile.get("tech_patterns", [])[:5]]
    if stack:
        lines.append(f"  Preferred stack: {', '.join(stack[:5])}")

    # Recurring blocker classes from planner analysis
    for bc in profile.get("recurring_blocker_classes", [])[:2]:
        if bc["count"] >= 2:
            lines.append(f"  Watch for: {bc['text']} ({bc['count']}x across projects) — you tend to accumulate this")

    # Avg task velocity
    avg_vel = profile.get("avg_task_velocity_ms")
    if avg_vel:
        avg_s = int(avg_vel / 1000)
        m, s = divmod(avg_s, 60)
        lines.append(f"  Avg task pace: {m}m {s}s — if this task is taking much longer, consider decomposing")

    # Rejected approaches from failure events
    rejected = profile.get("rejected_approaches", [])
    seen: dict[str, int] = {}
    for r in rejected:
        if r.get("attempted"):
            seen[r["attempted"]] = seen.get(r["attempted"], 0) + 1
    for attempted, count in sorted(seen.items(), key=lambda kv: -kv[1])[:3]:
        suffix = f" (abandoned in {count} prior projects)" if count > 1 else " (previously abandoned)"
        lines.append(f"  Avoid suggesting: {attempted}{suffix}")

    return lines if len(lines) > 1 else []


def _pattern_lines(pid: str) -> list[str]:
    patterns = _get(f"/projects/{pid}/patterns")
    if not patterns:
        return []
    lines = []
    hot = patterns.get("hotspot_files", [])[:3]
    if hot:
        lines.append("  Hotspots: " + ", ".join(f"{h['path']} ({h['count']}x)" for h in hot))
    for b in patterns.get("recurring_blockers", [])[:2]:
        lines.append(f"  Recurring blocker: {b['text']} ({b['count']}x)")
    for t in patterns.get("recurring_tasks", [])[:2]:
        lines.append(f"  Unresolved task: {t['text']} ({t['count']}x)")
    return lines


def _on_session_start(event: dict) -> None:
    sid = event.get("session_id", "default")
    pid = _project_id()
    _write(sid, "project_id", pid)
    _write(sid, "tool_count", "0")

    if not _get("/health"):
        print(
            "[context-bridge] Backend not running. Start it with: context-bridge\n"
            "Memory hooks are wired but inactive until the server is up.",
            file=sys.stderr,
        )
        return

    history = _get(f"/history/{pid}?limit=1")
    if not history:
        profile = _profile_lines()
        if profile:
            print("\n".join(profile))
        return

    latest = history[0]
    planner = latest.get("_planner_output") or {}
    next_instr = (planner.get("next_instruction") or "").strip()
    ctx = (planner.get("context_summary") or "").strip()
    priority = (planner.get("priority_focus") or "").strip()

    if priority:
        _write(sid, "priority", priority)
    if latest.get("user_goal"):
        _write(sid, "goal", latest["user_goal"])

    if not (next_instr or ctx):
        return

    lines = ["[context-bridge] Session context restored:"]
    if ctx:
        lines.append(f"  Summary:  {ctx}")
    if next_instr:
        lines.append(f"  Next:     {next_instr}")
    if priority:
        lines.append(f"  Priority: {priority}")
    lines.extend(_pattern_lines(pid))

    # Semantic search: surface related past work (Task 4)
    related = _related_work_lines(next_instr, pid)
    if related:
        lines.extend(related)

    print("\n".join(lines))


# ── PostToolUse ───────────────────────────────────────────────────────────────

def _on_post_tool_use(event: dict) -> None:
    sid = event.get("session_id", "default")
    tool = event.get("tool_name", "")

    count = int(_read(sid, "tool_count", "0")) + 1
    _write(sid, "tool_count", str(count))

    if count % 5 == 0:
        pid = _read(sid, "project_id") or _project_id()
        history = _get(f"/history/{pid}?limit=1")
        if history:
            planner = history[0].get("_planner_output") or {}
            new_p = (planner.get("priority_focus") or "").strip()
            if new_p and new_p != _read(sid, "priority"):
                _write(sid, "priority", new_p)
                print(f"[context-bridge] Priority updated: {new_p}")

    if tool in _TASK_TOOL_NAMES or tool.lower().startswith("task"):
        _auto_checkpoint(event, sid)


def _auto_checkpoint(event: dict, sid: str) -> None:
    tool_input = event.get("tool_input") or {}
    tool_response = event.get("tool_response") or {}

    pid = _read(sid, "project_id") or _project_id()
    goal = _read(sid, "goal") or "(not yet recorded — use /sync to set)"
    task = (
        tool_input.get("description")
        or str(tool_input.get("prompt", ""))[:120]
        or "(auto-checkpoint)"
    )

    git = _git_meta()
    files: list = []
    diff_stat = git.get("git_diff_stat", "")
    if diff_stat and diff_stat != "(no uncommitted changes)":
        for line in diff_stat.splitlines():
            if "|" in line:
                fname = line.split("|")[0].strip()
                if fname:
                    files.append(fname)
    if not files:
        files = git.get("recent_files_mtime", [])

    result_text = (
        str(tool_response.get("result", tool_response.get("output", "")))
        if isinstance(tool_response, dict)
        else str(tool_response)
    )
    blockers = []
    for line in result_text.splitlines():
        if any(kw in line.lower() for kw in ("error:", "failed:", "blocked:", "unable to")):
            blockers.append(line.strip()[:200])
            break

    payload = {
        "project_id": pid,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        "user_goal": goal,
        "current_task": task,
        "progress_summary": result_text[:500] or "(task completed)",
        "current_state": {
            "files_modified": files,
            "code_summary": "",
            "architecture_notes": "",
            "git_diff_stat": git.get("git_diff_stat"),
            "git_name_status": git.get("git_name_status"),
            "git_log_recent": git.get("git_log_recent"),
        },
        "blockers": blockers,
        "next_intended_action": "(auto-checkpoint — awaiting planner)",
    }

    response = _post("/sync", payload)
    if not _validate(response):
        return

    priority = (response.get("priority_focus") or "").strip()
    old_p = _read(sid, "priority")
    if priority:
        _write(sid, "priority", priority)

    next_instr = (response.get("next_instruction") or "").strip()
    if priority and priority != old_p:
        print(f"[context-bridge] Checkpoint saved. Priority: {priority}")
    elif next_instr:
        print(f"[context-bridge] Checkpoint saved. Next: {next_instr[:120]}")
    else:
        print("[context-bridge] Checkpoint saved.")

    report = response.get("stagnation_report")
    if report:
        print(
            f"[context-bridge] Stagnation report: stuck since {report.get('stuck_since')} "
            f"({report.get('elapsed_hours')}h, {report.get('checkpoint_count')} checkpoints). "
            f"Blocker: {report.get('primary_blocker') or 'none recorded'}. "
            f"{report.get('recommendation', '')}"
        )


# ── Stop ─────────────────────────────────────────────────────────────────────

def _on_stop(event: dict) -> None:
    sid = event.get("session_id", "default")
    count = int(_read(sid, "tool_count", "0"))
    if count == 0:
        return

    pid = _read(sid, "project_id") or _project_id()
    goal = _read(sid, "goal") or "Session ended"
    git = _git_meta()

    files: list = []
    diff_stat = git.get("git_diff_stat", "")
    if diff_stat and diff_stat != "(no uncommitted changes)":
        for line in diff_stat.splitlines():
            if "|" in line:
                fname = line.split("|")[0].strip()
                if fname:
                    files.append(fname)
    if not files:
        files = git.get("recent_files_mtime", [])

    progress_summary = f"Session ended ({count} tool calls)"
    if files:
        progress_summary += f". Files changed: {', '.join(files[:10])}"

    payload = {
        "project_id": pid,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        "user_goal": goal,
        "current_task": "End of session",
        "progress_summary": progress_summary,
        "current_state": {
            "files_modified": files,
            "git_diff_stat": git.get("git_diff_stat"),
            "git_name_status": git.get("git_name_status"),
            "git_log_recent": git.get("git_log_recent"),
        },
        "blockers": [],
        "next_intended_action": "Review changes on next session start",
        "checkpoint_type": "session",  # explicitly mark Stop hook checkpoints
    }

    result = _post("/checkpoint", payload)
    if result:
        print(
            f"[context-bridge] End-of-session checkpoint saved ({count} tool calls).",
            file=sys.stderr,
        )

    for key in ("tool_count", "priority", "goal", "project_id"):
        try:
            _sp(sid, key).unlink(missing_ok=True)
        except OSError:
            pass


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    raw = sys.stdin.read().strip()
    if not raw:
        return
    try:
        event = json.loads(raw)
    except ValueError:
        return
    hook = event.get("hook_event_name") or event.get("hook_type", "")
    if hook == "SessionStart":
        _on_session_start(event)
    elif hook == "PostToolUse":
        _on_post_tool_use(event)
    elif hook == "Stop":
        _on_stop(event)


if __name__ == "__main__":
    main()
