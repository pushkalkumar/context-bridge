import json
import os
import urllib.request
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

from .models import PlannerOutput

load_dotenv(Path.home() / ".context-bridge" / ".env")
load_dotenv()


def build_prompt(user_goal: str, history: list[dict], current: dict) -> str:
    history_lines = "\n".join(
        "[{ts}] task: {task} | progress: {prog} | blockers: {blk}{advice}".format(
            ts=c["timestamp"],
            task=c["current_task"],
            prog=c["progress_summary"],
            blk=", ".join(c["blockers"]) or "none",
            advice=(
                " | planner said: " + c["_planner_output"]["next_instruction"]
                if c.get("_planner_output")
                else ""
            ),
        )
        for c in history
    )

    return f"""You are a coding project planner. Analyze the checkpoint history and current state, then return a JSON plan.

## User Goal
{user_goal}

## Checkpoint History (most recent first, includes what planner previously recommended)
{history_lines or '(no prior checkpoints)'}

## Current Checkpoint
- Task: {current['current_task']}
- Progress: {current['progress_summary']}
- Files modified: {', '.join(current['current_state'].get('files_modified', [])) or 'none'}
- Code summary: {current['current_state'].get('code_summary', '')}
- Architecture notes: {current['current_state'].get('architecture_notes', '')}
- Blockers: {', '.join(current['blockers']) or 'none'}
- Next intended action: {current['next_intended_action']}

If the same task or blocker keeps recurring across checkpoints, explicitly address why it is stuck.

Return ONLY valid JSON matching this schema exactly:
{{
  "next_instruction": "string — the single next action Claude Code should take",
  "context_summary": "string — concise summary of where the project stands",
  "revised_plan": "string — updated step-by-step plan from this point forward",
  "priority_focus": "string — the single most important constraint or concern right now"
}}"""


def _parse_llm_output(raw: str) -> PlannerOutput:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
    return PlannerOutput(**json.loads(raw.strip()))


# ── Provider: Anthropic ───────────────────────────────────────────────────────

def _run_anthropic(checkpoint: dict, history: list[dict], api_key: str) -> PlannerOutput:
    import anthropic
    prompt = build_prompt(checkpoint["user_goal"], history, checkpoint)
    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=1000,
        system="You are a coding project planner. Return ONLY valid JSON matching PlannerOutput schema.",
        messages=[{"role": "user", "content": prompt}],
    )
    return _parse_llm_output(message.content[0].text)


# ── Provider: Ollama (free, local) ───────────────────────────────────────────

def _ollama_available() -> bool:
    try:
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2)
        return True
    except Exception:
        return False


def _get_ollama_model() -> str:
    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2) as r:
            data = json.loads(r.read())
            models = data.get("models", [])
            if models:
                return models[0]["name"]
    except Exception:
        pass
    return "llama3.2"


def _run_ollama(checkpoint: dict, history: list[dict]) -> PlannerOutput:
    prompt = build_prompt(checkpoint["user_goal"], history, checkpoint)
    model = _get_ollama_model()
    payload = json.dumps({
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "You are a coding project planner. Return ONLY valid JSON matching PlannerOutput schema.",
            },
            {"role": "user", "content": prompt},
        ],
        "stream": False,
    }).encode()
    req = urllib.request.Request(
        "http://localhost:11434/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        result = json.loads(r.read())
        return _parse_llm_output(result["message"]["content"])


# ── Fallback: rule-based with stagnation + blocker pattern detection ──────────

def _rule_based(checkpoint: dict, history: list[dict]) -> PlannerOutput:
    blockers = checkpoint.get("blockers", [])
    current_task = checkpoint["current_task"]

    # Stagnation: consecutive checkpoints (newest-first) with the same task
    same_task_streak = 0
    for c in history:
        if c.get("current_task") == current_task:
            same_task_streak += 1
        else:
            break

    # Recurring blockers across history
    all_blockers = [b for c in history for b in c.get("blockers", [])]
    blocker_freq = Counter(all_blockers).most_common(1)
    top_recurring = blocker_freq[0] if blocker_freq and blocker_freq[0][1] >= 2 else None

    # Determine next_instruction
    if same_task_streak >= 2:
        instruction = (
            f"You have been on '{current_task}' for {same_task_streak + 1} consecutive checkpoints. "
            "Break it into the smallest possible subtask you can complete in one step and do only that."
        )
        priority = f"Stagnation detected — {same_task_streak + 1} checkpoints on the same task"
    elif top_recurring:
        blocker_text, count = top_recurring
        instruction = (
            f"Recurring blocker '{blocker_text}' has appeared {count} times. "
            f"Resolve it directly before continuing with: {checkpoint['next_intended_action']}"
        )
        priority = f"Recurring blocker (×{count}): {blocker_text}"
    elif blockers:
        instruction = (
            f"Resolve blocker first: {blockers[0]}. "
            f"Then: {checkpoint['next_intended_action']}"
        )
        priority = blockers[0]
    else:
        instruction = checkpoint["next_intended_action"]
        priority = "No blockers — proceed"

    files = checkpoint["current_state"].get("files_modified", [])
    context = (
        f"Goal: {checkpoint['user_goal']} | "
        f"Task: {current_task} | "
        f"Progress: {checkpoint['progress_summary']}"
        + (f" | Files: {', '.join(files[-3:])}" if files else "")
    )

    has_llm = bool(os.environ.get("ANTHROPIC_API_KEY", "").strip()) or _ollama_available()
    plan = (
        f"Continue toward: {checkpoint['user_goal']}"
        if has_llm
        else (
            "No LLM configured. Add ANTHROPIC_API_KEY to ~/.context-bridge/.env "
            "or install Ollama (https://ollama.ai) for AI planning. "
            "Checkpoints and history are being stored regardless."
        )
    )

    return PlannerOutput(
        next_instruction=instruction,
        context_summary=context,
        revised_plan=plan,
        priority_focus=priority,
    )


# ── Public entry point ────────────────────────────────────────────────────────

def run_planner(checkpoint: dict, history: list[dict]) -> PlannerOutput:
    try:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if api_key:
            return _run_anthropic(checkpoint, history, api_key)
        if _ollama_available():
            return _run_ollama(checkpoint, history)
    except Exception:
        pass
    return _rule_based(checkpoint, history)
