import json
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .config import settings
from .memory import _SQLITE_VEC_AVAILABLE


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


def do_status() -> None:
    data = _fetch("/health")
    if not data:
        print(f"Backend    not running  (start with: context-bridge)")
        return
    print(f"Backend    running on port {data['port']}")
    print(f"DB         {settings.db_path}")

    s = _fetch("/stats")
    projects_data = _fetch("/projects") or []
    if s:
        print(f"Projects   {s['total_projects']}")
        print(f"Checkpoints {s['total_checkpoints']}")

    stagnant = [p for p in projects_data if p.get("stagnation_count", 0) >= 3]
    if stagnant:
        names = ", ".join(p["project_id"] for p in stagnant[:3])
        suffix = f" → run `context-bridge why`  ({names})"
        print(f"Stagnant   {len(stagnant)} project{'s' if len(stagnant) != 1 else ''}{suffix}")
    else:
        print(f"Stagnant   none")

    planner = "rule-based (no LLM configured)"
    if settings.anthropic_api_key:
        planner = f"Anthropic ({settings.planner_model})"
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
