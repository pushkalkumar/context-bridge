# context-bridge API reference

Prefer the CLI: `context-bridge sync` and `context-bridge event` wrap these
endpoints, derive the project ID, gather git state, and print the plan. The raw
HTTP API below is for integrations or when the CLI is unavailable.

Base URL: `http://127.0.0.1:7723` (override with `CONTEXT_BRIDGE_URL`).
All POST bodies are JSON with `Content-Type: application/json`.

Project ID format is `reponame/branch` — repo name from the origin remote
(fallback: working directory name), plus the current branch when on one.

## Endpoints

| Method | Path | Use |
|--------|------|-----|
| POST | /sync | Checkpoint + plan (primary endpoint) |
| POST | /checkpoint | Store checkpoint/event without planning |
| POST | /search | Semantic search over past checkpoints |
| GET | /health | Backend liveness |
| GET | /history/{project_id}?limit=N | Recent checkpoints |
| GET | /projects | All projects with stagnation counts |
| GET | /projects/{project_id}/stagnation-report | Root-cause analysis when stuck |
| GET | /projects/{project_id}/patterns | Hotspot files and recurring issues |
| GET | /projects/{project_id}/replay | Attempt replay for the stagnant task |
| GET | /projects/{project_id}/goal-drift | Goal drift detection |
| GET | /projects/{project_id}/blocker-history?q=... | Similar historical blocker |
| GET | /profile | Cross-project developer profile |
| GET | /stats | Total projects/checkpoints |
| GET | /velocity/{project_id} | Velocity metrics and alert status |
| GET | /diff/{project_id} | Compare two most recent task checkpoints |
| GET | /snapshot/{project_id} | CLAUDE.md-compatible Markdown snapshot |
| DELETE | /projects/{project_id} | Delete all checkpoints (`context-bridge forget`) |

## Checkpoint payload (POST /sync and /checkpoint)

Required: `user_goal`, `current_task`, `progress_summary`. Everything else is
optional but `project_id` should always be sent (otherwise the server generates
a random one and history splits).

```json
{
  "project_id": "reponame/branch",
  "user_goal": "Ship JWT auth",
  "current_task": "Implement /login endpoint",
  "progress_summary": "/register done; /login returns 500 on bcrypt verify",
  "current_state": {"files_modified": ["auth.py"], "code_summary": "", "architecture_notes": ""},
  "blockers": ["error: cannot find module 'bcrypt'"],
  "next_intended_action": "Fix bcrypt import, then sign HS256 token",
  "event_type": "checkpoint",
  "event_data": {}
}
```

## Structured events (POST /checkpoint)

Same required fields apply. Set `event_type` and `event_data`:

Abandoned approach — feeds the blocker history matcher:

```json
{
  "project_id": "reponame/branch",
  "user_goal": "Ship JWT auth",
  "current_task": "Implement /login endpoint",
  "progress_summary": "Abandoned session-cookie approach",
  "event_type": "failure",
  "event_data": {"attempted": "session cookies via starlette middleware",
                 "failed_because": "stateless API requirement rules out server sessions"}
}
```

Architecture decision — feeds the snapshot and cross-session context:

```json
{
  "project_id": "reponame/branch",
  "user_goal": "Ship JWT auth",
  "current_task": "Auth token design",
  "progress_summary": "Chose HS256 over RS256",
  "event_type": "adr",
  "event_data": {"decision": "HS256 signing", "reason": "single service, shared secret is fine",
                 "tradeoff": "key rotation requires redeploy"}
}
```

Significant result:

```json
{
  "project_id": "reponame/branch",
  "user_goal": "Ship JWT auth",
  "current_task": "Auth test suite",
  "progress_summary": "All 24 auth tests green",
  "event_type": "outcome",
  "event_data": {"result": "auth suite green", "impact": "auth feature complete"}
}
```

## /sync response contract

| Field | Required action |
|-------|----------------|
| `next_instruction` | Execute it. On conflict with the user request, follow the skill's conflict rules. |
| `priority_focus` | Treat as the active constraint for the session. |
| `context_summary` | Use it to orient the session. |
| `revised_plan` | Keep in mind when planning the next step. |
| `source` | `"rule-based"` → follow exactly, do not improvise. |
| `stagnation_count` | `>= 3` → apply the stagnation protocol. |
| `confidence` | `< 0.75` → surface the uncertainty before proceeding. |
| `blocker_class` | Not `"none"` → address the blocker type before retrying. |
| `decomposition_suggested` | `true` → split into subtasks of 30 minutes or less first. |
| `alternatives` | If present, mention the first alternative to the user as an option. |
| `stagnation_report` | If present, name `primary_blocker` and follow `recommendation`. |
| `blocker_match` | `resolved: true` → re-apply the recorded fix; `false` → new approach. |

## Blocker classes

| Class | Meaning | Required action |
|-------|---------|----------------|
| `technical_debt` | Same files changing repeatedly | Propose fixing the root-cause file before adding code |
| `dependency` | Blocked on something external | Surface it and get a user decision first |
| `unclear_spec` | Requirements ambiguous | Ask one clarifying question before writing code |
| `scope_creep` | Task grew too large | Trim to the smallest deliverable that unblocks the next step |
