---
name: context-bridge
description: >
  Activate when a SessionStart hook injects [context-bridge] output, when the user
  says "checkpoint", "sync context", or "remember this", or when you detect a task
  has appeared more than twice without completing. Governs restored-context handling,
  priority constraints, stagnation decomposition, and velocity awareness.
---

# Context Bridge

## 1. On session start

- If `[context-bridge] Session context restored:` appears, announce the summary, next instruction, and constraint, then ask "Still accurate?" once per project. If the user has already confirmed once, announce the summary and proceed.
- If `[context-bridge] Developer profile active:` appears, acknowledge the profile and ask what to work on.

## 2. Decision tree

```text
Starting a task?
├─ goal recorded this session → POST /sync
└─ no goal yet               → POST /sync (records it)

Task completed, no next task → POST /checkpoint

Architecture decision made  → POST /checkpoint  event_type: "adr"
Approach abandoned          → POST /checkpoint  event_type: "failure"

Backend unreachable         → warn user: context-bridge status
```

Minimal `/sync` payload:

```json
{
  "project_id": "<reponame/branch>",
  "user_goal": "<goal>",
  "current_task": "<task>",
  "progress_summary": "<what changed>",
  "current_state": {"files_modified": []},
  "blockers": [],
  "next_intended_action": "<next step>"
}
```

Project ID derivation:

```bash
project_id="$(git remote get-url origin 2>/dev/null | sed 's/.*\///; s/\.git$//' 2>/dev/null)/$(git branch --show-current 2>/dev/null || echo main)"
```

## 3. Response contract

| Field | Required action |
|-------|----------------|
| `next_instruction` | Execute it. If it conflicts with the request, follow the conflict rules below. |
| `priority_focus` | Treat as the active constraint for the session. |
| `context_summary` | Use it to orient the session. |
| `revised_plan` | Keep it in mind when planning the next step. |
| `source` | If `source: "rule-based"`, follow exactly and do not improvise. |
| `stagnation_count` | If `>= 3`, apply the stagnation protocol. |
| `confidence` | If `< 0.75`, surface the uncertainty to the user before proceeding. |
| `blocker_class` | If not `"none"`, address the blocker type before retrying the task. |
| `decomposition_suggested` | If `true`, break the task into subtasks ≤ 30 min before starting. |
| `alternatives` | If present, mention the first alternative to the user as an option. |

## 4. Conflicts

When the user request conflicts with `priority_focus`, surface it, offer two choices, and never silently pick either side. If the user overrides, POST to `/sync` with the override in `blockers`.

## 5. Stagnation protocol

When `stagnation_count >= 3`, pause immediately, decompose the task into substeps of 30 minutes or less, name the primary blocker from the stagnation report if present, and confirm the first subtask with the user before proceeding.

When a `[context-bridge] ⚠ STAGNATION RISK` warning appears in PreToolUse output, do NOT start the task as described. Immediately decompose it and confirm with the user which subtask to start first.

## 6. Velocity awareness

When `next_instruction` begins with `⚠ VELOCITY ALERT`, this task is running significantly slower than your baseline. Before continuing:
1. Check if you are genuinely blocked (something you cannot resolve without user input).
2. If blocked: surface the blocker explicitly to the user and ask how to proceed.
3. If not blocked: continue, but check in after each step rather than running silently.

## 7. Blocker class actions

| Blocker class | Required action |
|---------------|----------------|
| `technical_debt` | The same files are changing repeatedly. Propose addressing the root cause file before adding more code. |
| `dependency` | Something external is blocking. Surface it to the user and get a decision before proceeding. |
| `unclear_spec` | The requirements are ambiguous. Ask one clarifying question before writing any code. |
| `scope_creep` | The task has grown too large. Trim to the smallest deliverable that unblocks the next step. |

## 8. Structured events

Record a `failure` event when you abandon an approach — it feeds the blocker history matcher and prevents the planner from repeating failed strategies:

```json
{"event_type": "failure", "event_data": {"attempted": "...", "failed_because": "..."}}
```

Record an `adr` when you make an architectural decision — it feeds the snapshot and cross-session context:

```json
{"event_type": "adr", "event_data": {"decision": "...", "reason": "...", "tradeoff": "..."}}
```

Record an `outcome` after a significant result lands (test suite green, feature shipped, PR merged):

```json
{"event_type": "outcome", "event_data": {"result": "...", "impact": "..."}}
```

## 9. Attempt replay handling

When `[context-bridge] ⚠ ATTEMPT HISTORY` appears at session start (stagnation >= 2):

1. Read every attempt entry — note which files were changed, which blockers appeared, and what plan was tried.
2. Do NOT repeat any file-change pattern or plan shown in the attempt history.
3. Before starting: explicitly state what is different about your current approach vs. the history.
4. If no different approach is apparent: stop, explain why the previous attempts failed, and ask the user for a new direction.

To see the full attempt history manually: run `context-bridge replay` in the terminal.

## 10. Goal drift handling

When `[context-bridge] ⚠ GOAL DRIFT` appears at session start:

1. Present the listed goals to the user.
2. Ask which one is the current goal before proceeding.
3. POST to `/sync` with the confirmed goal as `user_goal` so drift resets.

## 11. Blocker match response

When the PostToolUse output shows `[context-bridge] ⚠ Recurring error:` or `⚠ Persistent error:`:

- **Recurring (resolved before):** the fix was found but apparently didn't stick. Check whether the fix was committed/persisted. Apply it again and verify it's durable.
- **Persistent (never resolved):** this error block has never been broken. Try a fundamentally different approach — not the same plan that failed before.

## 12. Endpoints reference

| Method | Path | Use |
|--------|------|-----|
| POST | /sync | Checkpoint + plan (primary endpoint) |
| POST | /checkpoint | Store event without planning |
| GET | /history/{project_id} | Recent checkpoints |
| GET | /projects | All projects with stagnation counts |
| GET | /projects/{project_id}/stagnation-report | Root-cause analysis when stuck |
| GET | /projects/{project_id}/patterns | Hotspots and recurring issues |
| GET | /projects/{project_id}/replay | Attempt replay for stagnant task |
| GET | /projects/{project_id}/goal-drift | Goal drift detection |
| GET | /projects/{project_id}/blocker-history?q=... | Similar historical blocker lookup |
| GET | /profile | Cross-project developer profile |
| GET | /stats | Total projects/checkpoints |
| GET | /velocity/{project_id} | Velocity metrics and alert status |
