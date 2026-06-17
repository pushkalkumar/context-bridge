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
from concurrent.futures import ThreadPoolExecutor, wait as fut_wait
from datetime import datetime, timezone
from pathlib import Path

BASE_URL = os.environ.get("CONTEXT_BRIDGE_URL", "http://127.0.0.1:7723")
_STATE_DIR = Path("/tmp/context-bridge-hooks")
_TASK_TOOL_NAMES = {"Task", "task"}

_SEARCH_SIMILARITY_THRESHOLD = 0.75


# ── Session state ─────────────────────────────────────────────────────────────

def _sanitize_sid(sid: str) -> str:
    return sid.replace("/", "_").replace("\\", "_")


def _state_path(sid: str) -> Path:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    return _STATE_DIR / f"{_sanitize_sid(sid)}.json"


def _read_state(sid: str) -> dict:
    p = _state_path(sid)
    try:
        return json.loads(p.read_text()) if p.exists() else {}
    except (ValueError, OSError):
        return {}


def _read(sid: str, key: str, default: str = "") -> str:
    return str(_read_state(sid).get(key, default))


def _write(sid: str, key: str, value: str) -> None:
    p = _state_path(sid)
    state = _read_state(sid)
    state[key] = value
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    tmp.replace(p)


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

_SKIP_DIRS = frozenset({
    "node_modules", "__pycache__", ".git", "dist", "build", ".next", ".nuxt",
    "target", "venv", ".venv", "env", ".env", "vendor", ".tox", "coverage",
    ".mypy_cache", ".ruff_cache", ".pytest_cache", "site-packages",
})


def _git_meta() -> dict:
    meta: dict = {}
    try:
        meta["git_diff_stat"] = subprocess.check_output(
            ["git", "diff", "--stat", "HEAD"], stderr=subprocess.DEVNULL, text=True,
        ).strip() or "(no uncommitted changes)"
        meta["git_log_recent"] = subprocess.check_output(
            ["git", "log", "--oneline", "-5"], stderr=subprocess.DEVNULL, text=True,
        ).strip()
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
                    and not any(
                        part.startswith(".") or part in _SKIP_DIRS
                        for part in p.parts
                    )
                ),
                key=lambda f: (cwd / f).stat().st_mtime,
                reverse=True,
            )[:20]
        except Exception:
            pass
    return meta


# ── HTTP ──────────────────────────────────────────────────────────────────────

def _get(path: str, timeout: float = 5.0):
    try:
        with urllib.request.urlopen(f"{BASE_URL}{path}", timeout=timeout) as r:
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


# ── PreToolUse ────────────────────────────────────────────────────────────────

_BLOCKER_CLASS_LABELS = {
    "technical_debt": "same files changing repeatedly without resolution",
    "dependency":     "blocked on something external to the codebase",
    "unclear_spec":   "acceptance criteria are ambiguous",
    "scope_creep":    "task has grown beyond its original boundary",
}


def _on_pre_tool_use(event: dict) -> None:
    """Warn before a Task tool fires if the incoming task matches a stagnant pattern."""
    if event.get("tool_name") not in _TASK_TOOL_NAMES:
        return

    tool_input = event.get("tool_input") or {}
    incoming_task = (
        tool_input.get("description") or str(tool_input.get("prompt", ""))
    ).strip()[:200]
    if not incoming_task:
        return

    sid = event.get("session_id", "default")
    pid = _read(sid, "project_id") or _project_id()

    # Use a short timeout — PreToolUse fires before every tool, so a downed backend
    # must not add multi-second latency before each task.
    # Fetch enough history to find the most recent non-scratch checkpoint matching
    # the incoming task — a scratch checkpoint in between would otherwise hide stagnation.
    history = _get(f"/history/{pid}?limit=10", timeout=1.0)
    if not history:
        return

    def _norm(t: str) -> str:
        return " ".join(t.lower().split())

    norm_incoming = _norm(incoming_task)
    # Find the most recent checkpoint whose task matches AND is not scratch type
    matching = next(
        (
            c for c in history
            if _norm(c.get("current_task") or "") == norm_incoming
            and c.get("checkpoint_type") != "scratch"
        ),
        None,
    )
    if matching is None or matching.get("stagnation_count", 1) < 2:
        return

    # This attempt would push stagnation_count to matching["stagnation_count"] + 1 (>= 3)
    prev_stag = matching["stagnation_count"]
    planner = matching.get("_planner_output") or {}
    blockers = matching.get("blockers") or []
    blocker_class = (matching.get("planner_blocker_class") or "none").strip()

    lines = [
        f"\n[context-bridge] ⚠ STAGNATION RISK — '{incoming_task[:70]}' "
        f"has appeared {prev_stag}× in recent history.",
    ]
    if blockers:
        lines.append(f"  Last recorded blocker: {blockers[0][:120]}")
    if blocker_class and blocker_class != "none":
        label = _BLOCKER_CLASS_LABELS.get(blocker_class, blocker_class)
        lines.append(f"  Pattern type: {label}")
    prev_instr = (planner.get("next_instruction") or "").strip()
    if prev_instr and not prev_instr.startswith("⚠"):
        first_line = prev_instr.split("\n")[0][:140]
        lines.append(f"  Previous plan: {first_line}")
    lines.append(
        "  Decompose into the smallest completable subtask before starting."
        " Run `context-bridge why` for root-cause analysis.\n"
    )
    print("\n".join(lines))


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

def _build_profile_lines(profile: dict) -> list[str]:
    """Format a /profile payload into display lines for session start."""
    if not profile or not (profile.get("checkpoint_count") or profile.get("total_task_checkpoints")):
        return []

    total_tasks = profile.get("total_task_checkpoints") or profile.get("checkpoint_count", 0)
    total_projects = profile.get("total_projects") or profile.get("project_count", 0)

    lines = [f"🧑‍💻 DEVELOPER PROFILE (computed from {total_tasks} tasks across {total_projects} projects):"]

    stack = profile.get("preferred_stack") or [t["text"] for t in profile.get("tech_patterns", [])[:5]]
    if stack:
        lines.append(f"  Preferred stack: {', '.join(stack[:5])}")

    for bc in profile.get("recurring_blocker_classes", [])[:2]:
        if bc["count"] >= 2:
            lines.append(f"  Watch for: {bc['text']} ({bc['count']}x across projects) — you tend to accumulate this")

    avg_vel = profile.get("avg_task_velocity_ms")
    if avg_vel:
        avg_s = int(avg_vel / 1000)
        m, s = divmod(avg_s, 60)
        lines.append(f"  Avg task pace: {m}m {s}s — if this task is taking much longer, consider decomposing")

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


def _git_state_lines() -> list[str]:
    """Format current repo state from _git_meta() output — no extra subprocess calls."""
    meta = _git_meta()
    lines = []

    log = meta.get("git_log_recent", "").strip()
    if log:
        lines.append("  Recent commits:")
        for commit in log.splitlines()[:3]:
            lines.append(f"    {commit}")

    diff = meta.get("git_diff_stat", "").strip()
    if diff and diff != "(no uncommitted changes)":
        lines.append("  Uncommitted changes:")
        for line in diff.splitlines()[:5]:  # first 5 lines = file names
            lines.append(f"    {line}")

    return lines


def _attempt_replay_lines(pid: str, stagnation_count: int) -> list[str]:
    """Compact attempt history injected when stagnation >= 2.  (v0.7.0)"""
    if stagnation_count < 2:
        return []
    replay = _get(f"/projects/{pid}/replay", timeout=2.0)
    if not replay or not isinstance(replay.get("attempts"), list):
        return []
    attempts = replay["attempts"]
    if len(attempts) < 2:
        return []

    lines = [f"\n[context-bridge] ⚠ ATTEMPT HISTORY ({len(attempts)} sessions on this task):"]
    for a in attempts:
        ts = a.get("timestamp", "")[:10]
        files = ", ".join(a.get("files_modified", [])[:3]) or "none"
        blockers = a.get("blockers", [])
        b_str = f"  blocker: {blockers[0][:60]}" if blockers else ""
        dur = f" ({a['duration_min']}m)" if a.get("duration_min") else ""
        lines.append(f"  Attempt {a['attempt']} ({ts}){dur}: {files}{b_str}")

    lines.append("  Do NOT repeat a previous approach. Run `context-bridge replay` for full details.")
    return lines


def _goal_drift_lines(pid: str) -> list[str]:
    """Goal drift warning injected when >= 3 distinct goals seen recently.  (v0.7.0)"""
    drift = _get(f"/projects/{pid}/goal-drift", timeout=2.0)
    if not drift or not drift.get("drifted"):
        return []
    goals = drift.get("goals", [])
    if len(goals) < 3:
        return []
    lines = [f"\n[context-bridge] ⚠ GOAL DRIFT ({drift['distinct_count']} goals in recent sessions):"]
    for g in goals[-4:]:
        lines.append(f"  → '{g}'")
    lines.append("  Confirm the current goal is still what you intend to ship.")
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

    # Fire all independent fetches in parallel — cuts sequential latency from
    # 5+ round-trips down to ~1 round-trip worth of wall time.
    with ThreadPoolExecutor(max_workers=5) as pool:
        fut_history  = pool.submit(_get, f"/history/{pid}?limit=10")
        fut_patterns = pool.submit(_get, f"/projects/{pid}/patterns")
        fut_drift    = pool.submit(_get, f"/projects/{pid}/goal-drift", 2.0)
        fut_git      = pool.submit(_git_state_lines)
        fut_profile  = pool.submit(_get, "/profile")

        history      = fut_history.result()
        patterns_raw = fut_patterns.result()
        drift_raw    = fut_drift.result()
        git_lines    = fut_git.result()
        profile_raw  = fut_profile.result()

    if not history:
        # New project — show cross-project developer profile if available
        if profile_raw:
            profile = _build_profile_lines(profile_raw)
            if profile:
                print("\n".join(profile))
        return

    latest = history[0]
    planner = latest.get("_planner_output") or {}
    next_instr = (planner.get("next_instruction") or "").strip()
    ctx = (planner.get("context_summary") or "").strip()
    priority = (planner.get("priority_focus") or "").strip()
    stagnation_count = latest.get("stagnation_count", 1)

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

    # Patterns from pre-fetched result
    if patterns_raw:
        hot = patterns_raw.get("hotspot_files", [])[:3]
        if hot:
            lines.append("  Hotspots: " + ", ".join(f"{h['path']} ({h['count']}x)" for h in hot))
        for b in patterns_raw.get("recurring_blockers", [])[:2]:
            lines.append(f"  Recurring blocker: {b['text']} ({b['count']}x)")

    # Semantic search: surface related past work (still sequential — conditional on next_instr)
    related = _related_work_lines(next_instr, pid)
    if related:
        lines.extend(related)

    # Git state (already computed in parallel)
    if git_lines:
        lines.append("\n[context-bridge] Current repo state:")
        lines.extend(git_lines)

    print("\n".join(lines))

    # Attempt replay — conditional on stagnation, fetched after we know stagnation_count
    replay_lines = _attempt_replay_lines(pid, stagnation_count)
    if replay_lines:
        print("\n".join(replay_lines))

    # Goal drift from pre-fetched result
    if drift_raw and drift_raw.get("drifted"):
        goals = drift_raw.get("goals", [])
        if len(goals) >= 3:
            drift_out = [f"\n[context-bridge] ⚠ GOAL DRIFT ({drift_raw['distinct_count']} goals in recent sessions):"]
            for g in goals[-4:]:
                drift_out.append(f"  → '{g}'")
            drift_out.append("  Confirm the current goal is still what you intend to ship.")
            print("\n".join(drift_out))


# ── PostToolUse ───────────────────────────────────────────────────────────────

def _on_post_tool_use(event: dict) -> None:
    sid = event.get("session_id", "default")
    tool = event.get("tool_name", "")

    count = int(_read(sid, "tool_count", "0")) + 1
    _write(sid, "tool_count", str(count))

    if count % 5 == 0:
        pid = _read(sid, "project_id") or _project_id()
        history = _get(f"/history/{pid}?limit=1", timeout=1.0)
        if history:
            planner = history[0].get("_planner_output") or {}
            new_p = (planner.get("priority_focus") or "").strip()
            if new_p and new_p != _read(sid, "priority"):
                _write(sid, "priority", new_p)
                print(f"[context-bridge] Priority updated: {new_p}")

    if tool in _TASK_TOOL_NAMES:
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
        display = next_instr.lstrip("⚠").strip()
        first_line = display.split("\n")[0][:120]
        print(f"[context-bridge] Checkpoint saved. Next: {first_line}")
    else:
        print("[context-bridge] Checkpoint saved.")

    # Surface planner intelligence when noteworthy
    confidence = response.get("confidence")
    blocker_class = (response.get("blocker_class") or "none").strip()
    decomposition = response.get("decomposition_suggested", False)
    alternatives = response.get("alternatives") or []

    meta_parts = []
    if confidence is not None and confidence < 0.75:
        meta_parts.append(f"confidence {confidence:.0%}")
    if blocker_class and blocker_class != "none":
        meta_parts.append(f"blocker: {blocker_class}")
    if decomposition:
        meta_parts.append("decompose")
    if meta_parts:
        print(f"[context-bridge]   {' · '.join(meta_parts)}")
    if alternatives:
        print(f"[context-bridge]   Alt: {alternatives[0][:100]}")

    report = response.get("stagnation_report")
    if report:
        hours = report.get("elapsed_hours", 0)
        count = report.get("checkpoint_count", 0)
        blocker = report.get("primary_blocker") or "none recorded"
        rec = report.get("recommendation", "")
        print(f"\n[context-bridge] ⚠ STAGNATION ({count} sessions, {hours}h stuck)")
        print(f"  Blocker: {blocker}")
        if rec:
            print(f"  Action:  {rec[:160]}")
        print()

    # Blocker history match: surface if this error has been seen before  (v0.7.0)
    blocker_match = response.get("blocker_match")
    if blocker_match:
        mb = (blocker_match.get("matched_blocker") or "")[:80]
        fix = (blocker_match.get("next_instruction") or "")[:120]
        resolved = blocker_match.get("resolved", False)
        if resolved:
            print(f"[context-bridge] ⚠ Recurring error: '{mb}'")
            print(f"  Previously resolved → fix was: {fix}")
            print(f"  If it's back: verify the fix was committed/persisted.")
        else:
            print(f"[context-bridge] ⚠ Persistent error: '{mb}'")
            print(f"  Seen before, never resolved. Previous plan: {fix}")


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

    try:
        _state_path(sid).unlink(missing_ok=True)
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
    elif hook == "PreToolUse":
        _on_pre_tool_use(event)
    elif hook == "PostToolUse":
        _on_post_tool_use(event)
    elif hook == "Stop":
        _on_stop(event)


if __name__ == "__main__":
    main()
