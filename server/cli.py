import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .config import settings
from .memory import _SQLITE_VEC_AVAILABLE

_EVENT_REQUIRED_FLAGS = {
    "failure": ("attempted", "because"),
    "adr":     ("decision", "reason"),
    "outcome": ("result", "impact"),
}
_EVENT_ALLOWED_FLAGS = {
    "failure": ("attempted", "because"),
    "adr":     ("decision", "reason", "tradeoff"),
    "outcome": ("result", "impact"),
}


def _fmt_age(ts: str | int | None) -> str:
    """Human-readable age from an ISO timestamp string or unix-millisecond int."""
    if not ts:
        return "unknown"
    try:
        if isinstance(ts, int):
            dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        else:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        s = (datetime.now(timezone.utc) - dt).total_seconds()
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
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{settings.server_port}{path}", timeout=timeout
        ) as r:
            return json.loads(r.read())
    except Exception:
        return None


def current_pid() -> str:
    """Derive project_id from git for CLI commands."""
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
        return f"{name}/{branch}" if branch and branch not in ("", "HEAD") else name
    except Exception:
        return name


def _post_json(path: str, payload: dict, timeout: float = 20.0):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{settings.server_port}{path}",
        data=data, headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


def ensure_backend(wait_s: float = 6.0) -> bool:
    """Start the backend in the background if it isn't running. True when healthy."""
    if _fetch("/health"):
        return True
    if os.environ.get("CONTEXT_BRIDGE_NO_AUTOSTART"):
        return False
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    log_path = settings.db_path.parent / "server.log"
    # Re-exec through the current interpreter so PATH doesn't matter.
    cmd = [
        sys.executable, "-c",
        "import sys; sys.argv = ['context-bridge']; from server.main import run; run()",
    ]
    try:
        with open(log_path, "ab") as log:
            subprocess.Popen(
                cmd, stdout=log, stderr=log,
                start_new_session=True, cwd=str(Path.home()),
            )
    except Exception:
        return False
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        time.sleep(0.25)
        if _fetch("/health", timeout=0.5):
            return True
    return False


# ── Git metadata (mirrors the hook's collection) ──────────────────────────────

def _git_meta() -> dict:
    meta: dict = {}
    for key, cmd in (
        ("git_diff_stat",   ["git", "diff", "--stat", "HEAD"]),
        ("git_log_recent",  ["git", "log", "--oneline", "-5"]),
        ("git_name_status", ["git", "diff", "--name-status", "HEAD"]),
    ):
        try:
            meta[key] = subprocess.check_output(
                cmd, stderr=subprocess.DEVNULL, text=True
            ).strip()
        except Exception:
            pass
    return meta


def _files_from_diff_stat(diff_stat: str) -> list[str]:
    files = []
    for line in diff_stat.splitlines():
        if "|" in line:
            fname = line.split("|")[0].strip()
            if fname:
                files.append(fname)
    return files


# ── sync / event payload builders (pure — unit tested) ───────────────────────

def build_sync_payload(
    project_id: str, goal: str, task: str, progress: str,
    next_action: str, blockers: list[str], git_meta: dict,
) -> dict:
    return {
        "project_id": project_id,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        "user_goal": goal,
        "current_task": task,
        "progress_summary": progress,
        "current_state": {
            "files_modified": _files_from_diff_stat(git_meta.get("git_diff_stat", "")),
            "code_summary": "",
            "architecture_notes": "",
            **{k: v for k, v in git_meta.items() if v},
        },
        "blockers": blockers,
        "next_intended_action": next_action,
    }


def build_event_payload(
    kind: str, project_id: str, event_data: dict, goal: str, task: str,
) -> dict:
    summaries = {
        "failure": f"Abandoned approach: {event_data.get('attempted', '')}",
        "adr":     f"Decision: {event_data.get('decision', '')}",
        "outcome": f"Result: {event_data.get('result', '')}",
    }
    return {
        "project_id": project_id,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        "user_goal": goal,
        "current_task": task,
        "progress_summary": summaries.get(kind, kind),
        "blockers": [],
        "next_intended_action": "",
        "event_type": kind,
        "event_data": event_data,
    }


def _print_sync_response(pid: str, resp: dict) -> None:
    stag = resp.get("stagnation_count", 1)
    conf = resp.get("confidence", 1.0)
    source = resp.get("source", "")
    print(f"  Synced {pid}  (stagnation {stag} · confidence {conf:.0%} · {source})")

    instr = (resp.get("next_instruction") or "").strip()
    if instr:
        first, *rest = instr.splitlines()
        print(f"  Plan:      {first}")
        for line in rest[:6]:
            print(f"             {line}")
    priority = (resp.get("priority_focus") or "").strip()
    if priority:
        print(f"  Priority:  {priority}")

    blocker_class = (resp.get("blocker_class") or "none").strip()
    if blocker_class and blocker_class != "none":
        print(f"  ⚠ Blocker class: {blocker_class} — address this before retrying")
    if resp.get("decomposition_suggested"):
        print("  ⚠ Decompose: split into subtasks of 30 minutes or less before starting")
    alternatives = resp.get("alternatives") or []
    if alternatives:
        print(f"  Alt:       {alternatives[0][:120]}")

    report = resp.get("stagnation_report")
    if report:
        print(f"  ⚠ STAGNATION ({report.get('checkpoint_count', 0)} sessions, "
              f"{report.get('elapsed_hours', 0)}h): {report.get('primary_blocker') or 'no blocker recorded'}")
        rec = report.get("recommendation", "")
        if rec:
            print(f"    Action: {rec[:160]}")

    match = resp.get("blocker_match")
    if match:
        label = "Recurring error (was resolved)" if match.get("resolved") else "Persistent error (never resolved)"
        print(f"  ⚠ {label}: {(match.get('matched_blocker') or '')[:80]}")
        fix = (match.get("next_instruction") or "")[:120]
        if fix:
            print(f"    Previous plan: {fix}")


def do_sync(goal: str, task: str, progress: str, next_action: str, blockers: list[str]) -> None:
    if not ensure_backend():
        print("Backend unreachable and auto-start failed. Diagnose: context-bridge status")
        raise SystemExit(1)
    pid = current_pid()
    payload = build_sync_payload(pid, goal, task, progress, next_action, blockers, _git_meta())
    resp = _post_json("/sync", payload)
    if not resp or not (resp.get("next_instruction") or "").strip():
        print("Sync failed — backend returned no plan. Diagnose: context-bridge status")
        raise SystemExit(1)
    _print_sync_response(pid, resp)


def do_event(kind: str, flags: dict, goal: str, task: str) -> None:
    missing = [f for f in _EVENT_REQUIRED_FLAGS[kind] if not flags.get(f)]
    if missing:
        print(f"Missing required flag(s) for '{kind}': " + " ".join(f"--{m}" for m in missing))
        raise SystemExit(2)
    if not ensure_backend():
        print("Backend unreachable and auto-start failed. Diagnose: context-bridge status")
        raise SystemExit(1)
    pid = current_pid()

    # Default goal/task from the latest checkpoint so events stay attached
    # to the task history they belong to.
    if not (goal and task):
        history = _fetch(f"/history/{pid}?limit=1") or []
        if history:
            goal = goal or history[0].get("user_goal", "")
            task = task or history[0].get("current_task", "")
    goal = goal or f"({kind} event)"
    task = task or f"({kind} event)"

    event_data = {k: v for k, v in flags.items() if v and k in _EVENT_ALLOWED_FLAGS[kind]}
    payload = build_event_payload(kind, pid, event_data, goal, task)
    resp = _post_json("/checkpoint", payload)
    if not resp:
        print("Event not recorded — backend error. Diagnose: context-bridge status")
        raise SystemExit(1)
    print(f"  Recorded {kind} event for {pid}.")


def do_status() -> None:
    data = _fetch("/health")
    if not data:
        print("  context-bridge  not running")
        print()
        print("  Start the backend:  context-bridge")
        print("  Or background:      context-bridge &")
        print("  Check install:      context-bridge install --help")
        return

    s = _fetch("/stats") or {}
    projects_data = _fetch("/projects") or []
    stagnant = [p for p in projects_data if p.get("stagnation_count", 0) >= 3]

    print()
    print(f"  Backend     running  (port {data['port']})")
    print(f"  Database    {settings.db_path}")
    print()
    print(f"  Projects    {s.get('total_projects', 0)}")
    print(f"  Checkpoints {s.get('total_checkpoints', 0)}  "
          f"(stagnation events: {s.get('stagnation_events', 0)})")
    if stagnant:
        names = ", ".join(p["project_id"] for p in stagnant[:3])
        extra = f"  +{len(stagnant) - 3} more" if len(stagnant) > 3 else ""
        print(f"  Stagnant    {len(stagnant)} project{'s' if len(stagnant) != 1 else ''}  "
              f"({names}{extra})  → context-bridge why")
    else:
        print(f"  Stagnant    none")
    print()

    if settings.anthropic_api_key:
        planner = f"Anthropic  ({settings.planner_model})"
    elif settings.resolved_ollama_host():
        planner = f"Ollama     ({settings.ollama_model})"
    else:
        planner = "rule-based  (set ANTHROPIC_API_KEY for LLM planning)"
    print(f"  Planner     {planner}")
    print(f"  Velocity    tracking enabled")
    if settings.embedding_api_key() and _SQLITE_VEC_AVAILABLE:
        embed_status = "enabled  (voyageai)"
    elif _SQLITE_VEC_AVAILABLE:
        embed_status = "disabled  (set VOYAGE_API_KEY or ANTHROPIC_API_KEY, then: pip install voyageai)"
    else:
        embed_status = "disabled  (pip install 'claude-context-bridge[semantic]')"
    print(f"  Embeddings  {embed_status}")
    print()


def do_list() -> None:
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
        type_str = (
            f" ({task_c} task, {scratch_c} scratch, {session_c} session)"
            if (task_c + scratch_c + session_c > 0) else ""
        )
        count_str = f"{n} checkpoint{'s' if n != 1 else ''}{type_str}"
        stag = p.get("stagnation_count", 0)
        stag_str = f"  ⚠ stagnant ({stag}x)" if stag >= 3 else ""
        age = _fmt_age(p.get("last_active", ""))
        print(f"  {pid}{count_str:<45}{age}{stag_str}")


def do_diff(project_id: str) -> None:
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

    from_age = _fmt_age(from_cp.get("completed_at_ts"))
    to_age = _fmt_age(to_cp.get("completed_at_ts"))
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


def do_export(project_id: str, output_path: str) -> None:
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


def do_replay() -> None:
    """Show chronological attempt history for the current stagnant task."""
    pid = current_pid()

    if not _fetch("/health"):
        print("Backend not running. Start it with: context-bridge")
        return

    replay = _fetch(f"/projects/{pid}/replay")
    if not replay or not replay.get("attempts"):
        print(f"No attempt history found for {pid}.")
        print("(Attempt replay requires stagnation — the same task appearing in 2+ sessions.)")
        return

    task = replay.get("task", "")
    count = replay.get("attempt_count", 0)
    print(f"\n  Project: {pid}")
    print(f"  Task:    '{task}'")
    print(f"  Attempts: {count}\n")

    for a in replay["attempts"]:
        ts = a.get("timestamp", "")[:16]
        files = ", ".join(a.get("files_modified", [])[:3]) or "none"
        blockers = a.get("blockers", [])
        dur = f"  ({a['duration_min']}m)" if a.get("duration_min") else ""
        bc = a.get("blocker_class") or ""
        bc_str = f"  [{bc}]" if bc and bc != "none" else ""
        plan = a.get("next_instruction", "")

        print(f"  Attempt {a['attempt']}  {ts}{dur}")
        print(f"    Files:   {files}")
        if blockers:
            print(f"    Blocker: {blockers[0][:100]}{bc_str}")
        if plan:
            print(f"    Plan:    {plan[:100]}")
        print()


def do_forget(project_id: str) -> None:
    """Delete all checkpoints for a project, clearing its stagnation history."""
    if not _fetch("/health"):
        print("Backend not running. Start it with: context-bridge")
        return

    import urllib.request
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{settings.server_port}/projects/{project_id}",
            method="DELETE",
        )
        with urllib.request.urlopen(req, timeout=5.0) as r:
            import json as _json
            data = _json.loads(r.read())
        print(f"  Deleted {data.get('deleted', 0)} checkpoint(s) for '{project_id}'.")
    except urllib.request.HTTPError as e:
        if e.code == 404:
            print(f"  Project '{project_id}' not found.")
        else:
            print(f"  Error: {e}")
    except Exception as e:
        print(f"  Error: {e}")


def do_why() -> None:
    """Show stagnation diagnosis and velocity for the current project."""
    pid = current_pid()

    if not _fetch("/health"):
        print("Backend not running. Start it with: context-bridge")
        return

    print(f"\n  Project: {pid}\n")

    history = _fetch(f"/history/{pid}?limit=1") or []
    current_stag = history[0].get("stagnation_count", 1) if history else 0

    if current_stag >= 3:
        stag = _fetch(f"/projects/{pid}/stagnation-report")
        if stag:
            hours = stag.get("elapsed_hours", 0)
            count = stag.get("checkpoint_count", 0)
            blocker = stag.get("primary_blocker") or "none recorded"
            rec = stag.get("recommendation", "")
            current_task = (history[0].get("current_task", "") if history else "")[:60]
            print("  ⚠ STAGNATION DETECTED")
            if current_task:
                print(f"  Task:    '{current_task}'")
            print(f"  Stuck:   {count} sessions, {hours}h")
            print(f"  Blocker: {blocker}")
            if rec:
                print(f"  Action:  {rec[:160]}")
        else:
            print("  Stagnation data unavailable.")
    elif current_stag == 2:
        task = (history[0].get("current_task", "") if history else "")[:60]
        print("  ⚠ APPROACHING STAGNATION (2 sessions)")
        if task:
            print(f"  Task: '{task}'")
        print("  One more attempt without completing this task triggers forced decomposition.")
    else:
        print("  No stagnation — current task is progressing normally.")

    print()

    vel = _fetch(f"/velocity/{pid}")
    if vel and vel.get("avg_duration_ms") is not None:
        avg_s = (vel["avg_duration_ms"] or 0) / 1000
        cur_s = (vel.get("current_duration_ms") or 0) / 1000
        avg_m, avg_s2 = divmod(int(avg_s), 60)
        cur_m, cur_s2 = divmod(int(cur_s), 60)
        ratio = vel.get("velocity_ratio")
        alert = vel.get("alert", False)
        ratio_str = f"  |  {ratio:.1f}×" if ratio is not None else ""
        status = "  ⚠ slower than baseline" if alert else "  on track"
        print(f"  Velocity: {cur_m}m {cur_s2}s current  |  {avg_m}m {avg_s2}s baseline{ratio_str}{status}")
    else:
        print("  Velocity: insufficient history (5+ completed tasks needed for baseline)")

    print()

    drift = _fetch(f"/projects/{pid}/goal-drift")
    if drift and drift.get("drifted"):
        goals = drift.get("goals", [])
        print(f"  ⚠ GOAL DRIFT ({drift['distinct_count']} distinct goals in recent sessions):")
        for g in goals[-4:]:
            print(f"    → '{g}'")
        print("  Confirm the current goal is still what you intend to ship.")
        print()

    if current_stag >= 2:
        replay = _fetch(f"/projects/{pid}/replay")
        if replay and replay.get("attempts") and len(replay["attempts"]) >= 2:
            print(f"  Attempt history ({len(replay['attempts'])} sessions on this task):")
            for a in replay["attempts"]:
                ts = a.get("timestamp", "")[:10]
                blockers = a.get("blockers", [])
                b_str = f"  → {blockers[0][:70]}" if blockers else ""
                dur = f" ({a['duration_min']}m)" if a.get("duration_min") else ""
                print(f"    Attempt {a['attempt']} ({ts}){dur}{b_str}")
            print(f"  Full history: context-bridge replay")
            print()
