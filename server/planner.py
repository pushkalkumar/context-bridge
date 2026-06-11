import json
from collections import Counter
from difflib import SequenceMatcher

from .config import settings
from .models import SyncResponse


# ── Prompt builder ────────────────────────────────────────────────────────────

def _build_prompt(user_goal: str, history: list[dict], current: dict) -> str:
    history_lines = "\n".join(
        "[{ts}] task={task!r} progress={prog!r} blockers={blk}{advice}".format(
            ts=c["timestamp"],
            task=c["current_task"],
            prog=c["progress_summary"],
            blk=c.get("blockers", []) or "none",
            advice=(
                " planner=" + repr(c["_planner_output"]["next_instruction"])
                if c.get("_planner_output")
                else ""
            ),
        )
        for c in history
    )
    state = current.get("current_state", {})
    return f"""You are a coding project planner. Return a JSON plan for the current checkpoint.

## Goal
{user_goal}

## History (newest first)
{history_lines or "(no prior checkpoints)"}

## Current checkpoint
task: {current["current_task"]}
progress: {current["progress_summary"]}
files: {", ".join(state.get("files_modified", [])) or "none"}
git_diff: {state.get("git_diff_stat", "")}
blockers: {", ".join(current.get("blockers", [])) or "none"}
next_intended: {current.get("next_intended_action", "")}
stagnation_count: {current.get("stagnation_count", 1)}

If the same task recurs across checkpoints, address why it is stuck and force decomposition.

Return ONLY valid JSON:
{{
  "next_instruction": "the single next action Claude Code should take",
  "context_summary": "concise state-of-project summary",
  "revised_plan": "updated step-by-step plan from here",
  "priority_focus": "the single most important constraint right now"
}}"""


def _parse(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
    return json.loads(raw.strip())


# ── Tier 1: Anthropic ─────────────────────────────────────────────────────────

def _run_anthropic(checkpoint: dict, history: list[dict]) -> SyncResponse | None:
    if not settings.anthropic_api_key:
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        prompt = _build_prompt(checkpoint["user_goal"], history, checkpoint)
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system="Return ONLY valid JSON matching the schema. No markdown, no explanation.",
            messages=[{"role": "user", "content": prompt}],
        )
        if msg.stop_reason == "refusal":
            return None
        data = _parse(msg.content[0].text)
        return SyncResponse(
            **data,
            source="anthropic",
            stagnation_count=checkpoint.get("stagnation_count", 1),
        )
    except Exception:
        return None


# ── Tier 2: Ollama ────────────────────────────────────────────────────────────

def _run_ollama(checkpoint: dict, history: list[dict]) -> SyncResponse | None:
    host = settings.ollama_host
    if not host:
        return None
    try:
        import httpx
        prompt = _build_prompt(checkpoint["user_goal"], history, checkpoint)
        r = httpx.post(
            f"{host}/api/chat",
            json={
                "model": settings.ollama_model,
                "messages": [
                    {"role": "system", "content": "Return ONLY valid JSON matching the schema."},
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
            },
            timeout=60,
        )
        r.raise_for_status()
        data = _parse(r.json()["message"]["content"])
        return SyncResponse(
            **data,
            source="ollama",
            stagnation_count=checkpoint.get("stagnation_count", 1),
        )
    except Exception:
        return None


# ── Tier 3: Rule-based ────────────────────────────────────────────────────────

def _rule_based(checkpoint: dict, history: list[dict], stagnation_count: int) -> SyncResponse:
    blockers = checkpoint.get("blockers", [])
    task = checkpoint["current_task"]

    if stagnation_count >= 3:
        instruction = (
            f"You have submitted '{task}' {stagnation_count} consecutive times. "
            "Break it into the smallest possible subtask completable in a single step."
        )
        priority = f"Stagnation — same task for {stagnation_count} checkpoints"
    else:
        # Recurring blocker detection across history
        all_blockers = [b for c in history for b in c.get("blockers", [])]
        top = Counter(all_blockers).most_common(1)
        if top and top[0][1] >= 2:
            blocker, count = top[0]
            instruction = (
                f"Recurring blocker '{blocker}' has appeared {count} times. "
                f"Resolve it first, then: {checkpoint.get('next_intended_action', '')}"
            )
            priority = f"Recurring blocker (×{count}): {blocker}"
        elif blockers:
            instruction = f"Resolve blocker: {blockers[0]}. Then: {checkpoint.get('next_intended_action', '')}"
            priority = blockers[0]
        else:
            instruction = checkpoint.get("next_intended_action", "Continue.")
            priority = "No blockers — proceed"

    state = checkpoint.get("current_state", {})
    files = state.get("files_modified", [])
    context = (
        f"Goal: {checkpoint['user_goal']} | Task: {task} | Progress: {checkpoint['progress_summary']}"
        + (f" | Files: {', '.join(files[-3:])}" if files else "")
    )

    has_llm = bool(settings.anthropic_api_key) or bool(settings.ollama_host)
    plan = (
        f"Continue toward: {checkpoint['user_goal']}"
        if has_llm
        else (
            "No LLM configured. Add ANTHROPIC_API_KEY to ~/.context-bridge/.env "
            "or set OLLAMA_HOST (e.g. http://localhost:11434). "
            "Checkpoints and history are stored regardless."
        )
    )

    return SyncResponse(
        next_instruction=instruction,
        context_summary=context,
        revised_plan=plan,
        priority_focus=priority,
        source="rule-based",
        stagnation_count=stagnation_count,
    )


# ── Public entry point ────────────────────────────────────────────────────────

def run_planner(checkpoint: dict, history: list[dict], stagnation_count: int) -> SyncResponse:
    result = _run_anthropic(checkpoint, history) or _run_ollama(checkpoint, history)
    if result:
        return result
    return _rule_based(checkpoint, history, stagnation_count)
