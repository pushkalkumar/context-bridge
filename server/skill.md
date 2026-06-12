# Context Bridge

Persistent memory and replanning for Claude Code. A local backend on port 7723 stores
checkpoints after every Task completion and injects context at the start of each session.

---

## Session-start protocol

When the SessionStart hook fires, context is injected before any user message. You must:

1. Announce the restoration explicitly:
   ```
   Resuming from last session: [context_summary from injected context]
   ```
2. State the active instruction and constraint:
   ```
   Active instruction: [next_instruction]
   Active constraint: [priority_focus]
   ```
3. Ask the user to confirm before proceeding:
   ```
   Does this match your goal, or has something changed since last session?
   ```

Do not silently absorb the injected context and start working. The handoff must be
visible. If the user confirms, proceed. If the goal changed, update the checkpoint via
POST /sync with the new goal before doing any work.

---

## Response contract

After every POST /sync or POST /checkpoint, you receive:

| Field | Required action |
|-------|----------------|
| `next_instruction` | Execute this. If it conflicts with what the user asked, see Conflict Resolution below. |
| `priority_focus` | Never violate this constraint during the session. Treat it as a hard invariant. |
| `context_summary` | Use this to orient yourself. Do not re-derive what it already states. |
| `source` | Determines how to interpret the instruction. See Planner-source behavior below. |
| `stagnation_count` | If >= 3, see Stagnation protocol below. Do not proceed until that protocol is complete. |

If `next_instruction` is empty: stop, warn the user that the backend response is malformed,
and suggest running `context-bridge status` to check health. Do not continue silently.

If POST /sync is unreachable: tell the user the backend is not running, and instruct
them to run `context-bridge` in a separate terminal before continuing.

---

## Planner-source behavior

The `source` field tells you which tier generated the response.

**source = "anthropic" or "ollama"**
The instruction is context-aware and has analyzed project history. You may:
- Reason about it before executing
- Flag a disagreement if you have new information the planner didn't have
- Propose a modification and ask the user to confirm

But you must execute the instruction unless the user explicitly overrides it.

**source = "rule-based"**
The instruction is deterministic output from heuristics. It is not reasoning — it is
a computed rule applied to the checkpoint data. You must:
- Follow it exactly as stated
- Not improvise or deviate based on your own reasoning
- Not second-guess the decomposition it specifies

The rule-based tier fires when no LLM is configured. Deviating from it defeats the
purpose of having an offline-capable planner.

---

## Conflict resolution

When a user request conflicts with the active `priority_focus`:

1. Surface the conflict explicitly:
   ```
   The active priority constraint is: [priority_focus]
   Proceeding with [user request] would violate it because [reason].
   ```
2. Ask the user to choose:
   - Adjust the task to respect the constraint
   - Override the constraint (with a stated reason)
3. If the user overrides, POST to /sync immediately with:
   ```json
   {
     "blockers": ["User overrode priority_focus: [their reason]"],
     "current_task": "[updated task]"
   }
   ```

Never silently violate `priority_focus`. Never silently override the user either.
Both paths require an explicit acknowledgment.

---

## Stagnation protocol

When `stagnation_count >= 3`:

1. Do not start the next task.
2. State the situation:
   ```
   Stagnation detected: "[current_task]" has appeared [N] times in a row.
   Before proceeding, this task needs to be broken down.
   ```
3. Decompose the stuck task into the smallest completable unit (target: 30 minutes or less).
   Show the decomposition to the user and ask them to confirm or modify it.
4. Once confirmed, proceed with the first subtask only.
5. POST the confirmed subtask as the new `current_task` to /sync.

This is a mandatory pause. Do not skip it because the user seems impatient.
Stagnation means the task as stated is underscoped and cannot be completed as-is.

If the server returns a `stagnation_report` in the checkpoint data, include its
`primary_blocker` and `recommendation` in your decomposition proposal.

---

## Manual fallback (hooks not installed)

Derive the project_id deterministically:

```bash
# Step 1: repo name
git remote get-url origin 2>/dev/null | sed 's/.*\///' | sed 's/\.git//'
# Step 2: current branch
git branch --show-current 2>/dev/null
```

Combine as `reponame/branch`. Normalize: lowercase, spaces to hyphens.
If not a git repo: use `dirname(cwd)/none`.

POST a checkpoint before starting any task:

```bash
curl -s -X POST http://localhost:7723/sync \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "my-app/main",
    "user_goal": "<overarching goal for this project>",
    "current_task": "<the specific task you are about to start>",
    "progress_summary": "<what has been done so far>",
    "current_state": {
      "files_modified": [],
      "code_summary": "",
      "architecture_notes": ""
    },
    "blockers": [],
    "next_intended_action": "<what you plan to do next>"
  }'
```

---

## Structured event recording

Beyond checkpoints, you can record structured decisions and failures that build
a persistent project knowledge base.

**Record architecture decisions (ADR):**
```bash
curl -s -X POST http://localhost:7723/checkpoint \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "my-app/main",
    "user_goal": "...",
    "current_task": "Architecture decision: auth strategy",
    "progress_summary": "Decided on approach",
    "event_type": "adr",
    "event_data": {
      "decision": "Use JWT with HS256, tokens in Authorization header",
      "reason": "Stateless, works with the existing FastAPI setup",
      "tradeoff": "No server-side revocation without a blocklist"
    }
  }'
```

**Record abandoned approaches:**
```bash
curl -s -X POST http://localhost:7723/checkpoint \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "my-app/main",
    "user_goal": "...",
    "current_task": "Abandoning Celery for task queue",
    "progress_summary": "Tried Celery, reverted to simple background thread",
    "event_type": "failure",
    "event_data": {
      "attempted": "Celery with Redis broker",
      "failed_because": "Redis not available in production env, adds ops complexity"
    }
  }'
```

Recording decisions and failures builds the developer profile that context-bridge
uses to give better advice on future projects.

---

## Available endpoints

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
