---
name: context-bridge
description: >
  Activate when a SessionStart hook injects [context-bridge] output, when the user
  says "checkpoint", "sync context", or "remember this", or when you detect a task
  has appeared more than twice without completing. Governs restored-context handling,
  priority constraints, and stagnation decomposition.
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

## 4. Conflicts

When the user request conflicts with `priority_focus`, surface it, offer two choices, and never silently pick either side. If the user overrides, POST to `/sync` with the override in `blockers`.

## 5. Stagnation protocol

When `stagnation_count >= 3`, pause immediately, decompose the task into substeps of 30 minutes or less, name the primary blocker from the stagnation report if present, and confirm the first subtask with the user before proceeding.

## 6. Structured events

```json
{"event_type": "adr", "event_data": {"decision": "...", "reason": "...", "tradeoff": "..."}}
```

```json
{"event_type": "failure", "event_data": {"attempted": "...", "failed_because": "..."}}
```

## 7. Endpoints reference

| Method | Path | Use |
|--------|------|-----|
| POST | /sync | Checkpoint + plan (primary endpoint) |
| POST | /checkpoint | Store event without planning |
| GET | /history/{project_id} | Recent checkpoints |
| GET | /projects | All projects with stagnation counts |
| GET | /projects/{project_id}/stagnation-report | Root-cause analysis when stuck |
| GET | /projects/{project_id}/patterns | Hotspots and recurring issues |
| GET | /profile | Cross-project developer profile |
| GET | /stats | Total projects/checkpoints |
