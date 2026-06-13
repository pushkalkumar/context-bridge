import json
import logging
from collections import Counter
from dataclasses import dataclass, field

from .config import settings
from .models import StagnationReport, SyncResponse

logger = logging.getLogger(__name__)


@dataclass
class PlannerOutput:
    next_instruction: str
    confidence: float
    alternatives: list[str] = field(default_factory=list)
    blocker_class: str | None = None
    decomposition_suggested: bool = False


# ── Prompt ────────────────────────────────────────────────────────────────────

def _build_prompt(
    user_goal: str, history: list[dict], current: dict, stagnation_report: dict | None = None
) -> str:
    history = history[:10]
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
    state = current.get("current_state") or {}
    if hasattr(state, "model_dump"):
        state = state.model_dump()
    stagnation_section = ""
    if stagnation_report:
        stagnation_section = (
            "## Stagnation analysis\n"
            f"stuck_since: {stagnation_report['stuck_since']} "
            f"({stagnation_report['elapsed_hours']}h, "
            f"{stagnation_report['checkpoint_count']} checkpoints)\n"
            f"primary_blocker: {stagnation_report['primary_blocker'] or 'none recorded'}\n"
            f"recommendation: {stagnation_report['recommendation']}\n\n"
        )
    return (
        "You are a coding project planner. Return a JSON plan for the current checkpoint.\n\n"
        f"## Goal\n{user_goal}\n\n"
        f"## History (newest first)\n{history_lines or '(no prior checkpoints)'}\n\n"
        "## Current checkpoint\n"
        f"task: {current['current_task']}\n"
        f"progress: {current['progress_summary']}\n"
        f"files: {', '.join(state.get('files_modified', [])) or 'none'}\n"
        f"git_diff: {state.get('git_diff_stat', '')}\n"
        f"blockers: {', '.join(current.get('blockers', [])) or 'none'}\n"
        f"next_intended: {current.get('next_intended_action', '')}\n"
        f"stagnation_count: {current.get('stagnation_count', 1)}\n\n"
        + stagnation_section
        + "If the same task recurs across checkpoints, address why it is stuck and force decomposition.\n\n"
        "Return ONLY valid JSON with these exact keys:\n"
        "{\n"
        '  "next_instruction": "the single next action Claude Code should take",\n'
        '  "context_summary": "concise state-of-project summary",\n'
        '  "revised_plan": "updated step-by-step plan from here",\n'
        '  "priority_focus": "the single most important constraint right now",\n'
        '  "confidence": 0.85,\n'
        '  "alternatives": ["alternative approach 1"],\n'
        '  "blocker_class": "none",\n'
        '  "decomposition_suggested": false\n'
        "}"
    )


def _parse(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]
    return json.loads(raw.strip())


def _sync_response_from_data(
    data: dict, source: str, checkpoint: dict
) -> SyncResponse:
    return SyncResponse(
        next_instruction=data.get("next_instruction", ""),
        context_summary=data.get("context_summary", ""),
        revised_plan=data.get("revised_plan", ""),
        priority_focus=data.get("priority_focus", ""),
        source=source,  # type: ignore[arg-type]
        stagnation_count=checkpoint.get("stagnation_count", 1),
        confidence=float(data.get("confidence", 0.85)),
        alternatives=list(data.get("alternatives", [])),
        blocker_class=data.get("blocker_class") or None,
        decomposition_suggested=bool(data.get("decomposition_suggested", False)),
    )


# ── Tier 1: Anthropic ─────────────────────────────────────────────────────────

def _run_anthropic(
    checkpoint: dict, history: list[dict], stagnation_report: dict | None = None
) -> SyncResponse | None:
    if not settings.anthropic_api_key:
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        prompt = _build_prompt(checkpoint["user_goal"], history, checkpoint, stagnation_report)
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system="Return ONLY valid JSON matching the schema. No markdown, no explanation.",
            messages=[{"role": "user", "content": prompt}],
        )
        if msg.stop_reason == "refusal":
            logger.warning("Anthropic refused to plan checkpoint — falling through to next tier")
            return None
        try:
            data = _parse(msg.content[0].text)
            return _sync_response_from_data(data, "anthropic", checkpoint)
        except (json.JSONDecodeError, KeyError) as json_exc:
            # API responded but returned unparseable JSON → specific fallback with low confidence
            logger.warning("Anthropic response JSON parse failed: %s — using rule-based fallback", json_exc)
            fallback = _rule_based(checkpoint, history, checkpoint.get("stagnation_count", 1), stagnation_report)
            fallback.confidence = 0.3
            return fallback
    except Exception as exc:
        logger.warning("Anthropic planner failed (%s: %s) — trying next tier", type(exc).__name__, exc)
        return None


# ── Tier 2: Ollama ────────────────────────────────────────────────────────────

def _run_ollama(
    checkpoint: dict, history: list[dict], stagnation_report: dict | None = None
) -> SyncResponse | None:
    host = settings.resolved_ollama_host()
    if not host:
        return None
    try:
        import httpx
        prompt = _build_prompt(checkpoint["user_goal"], history, checkpoint, stagnation_report)
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
            timeout=60.0,
        )
        r.raise_for_status()
        try:
            data = _parse(r.json()["message"]["content"])
            return _sync_response_from_data(data, "ollama", checkpoint)
        except (json.JSONDecodeError, KeyError) as json_exc:
            logger.warning("Ollama response JSON parse failed: %s — using rule-based fallback", json_exc)
            fallback = _rule_based(checkpoint, history, checkpoint.get("stagnation_count", 1), stagnation_report)
            fallback.confidence = 0.3
            return fallback
    except Exception as exc:
        logger.warning("Ollama planner failed (%s: %s) — falling back to rule-based", type(exc).__name__, exc)
        return None


# ── Blocker classification ────────────────────────────────────────────────────

def _classify_blocker(checkpoint: dict, history: list[dict]) -> str:
    """Classify the primary blocker type from history patterns (rule-based)."""
    # Technical debt: same file recurring in recent diffs AND in current checkpoint
    state = checkpoint.get("current_state") or {}
    if hasattr(state, "model_dump"):
        state = state.model_dump()
    current_files = set(state.get("files_modified", []))
    file_counts: Counter = Counter()
    for c in history[:5]:
        s = c.get("current_state") or {}
        file_counts.update(s.get("files_modified", []))
    if current_files and any(count >= 2 and f in current_files for f, count in file_counts.items()):
        return "technical_debt"

    blockers_text = " ".join(checkpoint.get("blockers", [])).lower()
    if any(kw in blockers_text for kw in ("dependency", "waiting on", "blocked by", "waiting for")):
        return "dependency"
    if any(kw in blockers_text for kw in ("spec", "unclear", "requirement", "not sure", "unsure", "ambiguous")):
        return "unclear_spec"

    stag = checkpoint.get("stagnation_count", 1)
    if stag >= 5:
        return "scope_creep"

    return "none"


# ── Tier 3: Rule-based ────────────────────────────────────────────────────────

def _rule_based(
    checkpoint: dict,
    history: list[dict],
    stagnation_count: int,
    stagnation_report: dict | None = None,
) -> SyncResponse:
    blockers = checkpoint.get("blockers", [])
    task = checkpoint["current_task"]
    blocker_class = _classify_blocker(checkpoint, history)
    decomposition_suggested = stagnation_count >= 3 or blocker_class == "scope_creep"
    confidence = 0.4 if stagnation_count >= 3 else 0.85

    if stagnation_count >= 3:
        instruction = (
            f"The task '{task}' has appeared {stagnation_count} consecutive times without completing. "
            "Pick the smallest completable subtask and do only that one thing."
        )
        if stagnation_report:
            if stagnation_report["primary_blocker"]:
                instruction += f" Root cause: '{stagnation_report['primary_blocker']}'."
            instruction += f" {stagnation_report['recommendation']}"
        priority = f"Stagnation on '{task}' ({stagnation_count} consecutive checkpoints)"
    else:
        all_blockers = [b for c in history for b in c.get("blockers", [])]
        top = Counter(all_blockers).most_common(1)
        if top and top[0][1] >= 2:
            blocker, count = top[0]
            instruction = (
                f"'{blocker}' has blocked you {count} times. Fix it before anything else. "
                f"Then: {checkpoint.get('next_intended_action', '')}"
            )
            priority = f"Recurring blocker (x{count}): {blocker}"
        elif blockers:
            instruction = (
                f"Resolve: {blockers[0]}. "
                f"Then: {checkpoint.get('next_intended_action', '')}"
            )
            priority = blockers[0]
        else:
            instruction = checkpoint.get("next_intended_action", "Continue.")
            priority = "No blockers"

    state = checkpoint.get("current_state") or {}
    if hasattr(state, "model_dump"):
        state = state.model_dump()
    files = state.get("files_modified", [])
    context = (
        f"Goal: {checkpoint['user_goal']} | "
        f"Task: {task} | "
        f"Progress: {checkpoint['progress_summary']}"
        + (f" | Files: {', '.join(files[-3:])}" if files else "")
    )

    ollama_available = bool(settings.resolved_ollama_host())
    has_llm = bool(settings.anthropic_api_key) or ollama_available
    plan = (
        f"Continue toward: {checkpoint['user_goal']}"
        if has_llm
        else (
            "No LLM configured. Add ANTHROPIC_API_KEY or OLLAMA_HOST to "
            "~/.context-bridge/.env. Checkpoints and history are stored regardless."
        )
    )

    return SyncResponse(
        next_instruction=instruction,
        context_summary=context,
        revised_plan=plan,
        priority_focus=priority,
        source="rule-based",
        stagnation_count=stagnation_count,
        confidence=confidence,
        alternatives=[],
        blocker_class=blocker_class,
        decomposition_suggested=decomposition_suggested,
    )


# ── Entry point ───────────────────────────────────────────────────────────────

def run_planner(
    checkpoint: dict,
    history: list[dict],
    stagnation_count: int,
    stagnation_report: dict | None = None,
) -> SyncResponse:
    result = (
        _run_anthropic(checkpoint, history, stagnation_report)
        or _run_ollama(checkpoint, history, stagnation_report)
        or _rule_based(checkpoint, history, stagnation_count, stagnation_report)
    )
    if stagnation_report:
        result.stagnation_report = StagnationReport(**stagnation_report)
    return result
