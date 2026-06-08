import json
import os
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

from .models import PlannerOutput

# Load from ~/.context-bridge/.env first, then fall back to CWD .env
load_dotenv(Path.home() / ".context-bridge" / ".env")
load_dotenv()


def build_prompt(user_goal: str, history: list[dict], current: dict) -> str:
    history_lines = "\n".join(
        f"[{c['timestamp']}] task: {c['current_task']} | "
        f"progress: {c['progress_summary']} | "
        f"blockers: {', '.join(c['blockers']) or 'none'}"
        for c in history
    )

    return f"""You are a coding project planner. Analyze the checkpoint history and current state, then return a JSON plan.

## User Goal
{user_goal}

## Checkpoint History (most recent first)
{history_lines or '(no prior checkpoints)'}

## Current Checkpoint
- Task: {current['current_task']}
- Progress: {current['progress_summary']}
- Files modified: {', '.join(current['current_state'].get('files_modified', [])) or 'none'}
- Code summary: {current['current_state'].get('code_summary', '')}
- Architecture notes: {current['current_state'].get('architecture_notes', '')}
- Blockers: {', '.join(current['blockers']) or 'none'}
- Next intended action: {current['next_intended_action']}

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


# ── Fallback: rule-based (no LLM required) ───────────────────────────────────

def _rule_based(checkpoint: dict) -> PlannerOutput:
    blockers = checkpoint.get("blockers", [])
    return PlannerOutput(
        next_instruction=checkpoint["next_intended_action"],
        context_summary=(
            f"Goal: {checkpoint['user_goal']} | "
            f"Current task: {checkpoint['current_task']} | "
            f"Progress: {checkpoint['progress_summary']}"
        ),
        revised_plan=(
            "No LLM configured — checkpoints are being stored and history is available. "
            "To enable AI planning, add ANTHROPIC_API_KEY to ~/.context-bridge/.env "
            "or install Ollama (https://ollama.ai) for free local planning."
        ),
        priority_focus=blockers[0] if blockers else "No blockers — proceed with next_intended_action",
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
        pass  # fall through to rule-based

    return _rule_based(checkpoint)
