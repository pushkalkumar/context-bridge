# Context Bridge Skill

## What This Is

Context Bridge is a checkpoint-based replanning system. It gives Claude Code persistent memory across tasks by storing structured checkpoints in a local backend and returning AI-generated next instructions.

## When to Invoke

Invoke Context Bridge:
- At the **START of every new task** before taking any action
- **After completing any subtask** that modifies files

## Checkpoint Protocol

Before taking any action on a new task:

1. Generate a checkpoint JSON matching this schema exactly:

```json
{
  "project_id": "string — use {repo-name}-{YYYYMMDD}, e.g. myapp-20260607",
  "timestamp": "string — ISO 8601, e.g. 2026-06-07T14:30:00",
  "user_goal": "string — the overarching goal the user has stated",
  "current_task": "string — the specific task being started or just completed",
  "progress_summary": "string — what has been done so far toward the user_goal",
  "current_state": {
    "files_modified": ["list", "of", "file", "paths"],
    "code_summary": "string — brief description of current code state",
    "architecture_notes": "string — any key architectural decisions made"
  },
  "blockers": ["list of current blockers, or empty array"],
  "next_intended_action": "string — what you were about to do next"
}
```

2. POST it to `http://localhost:8000/sync`:

```bash
curl -s -X POST http://localhost:8000/sync \
  -H "Content-Type: application/json" \
  -d '<checkpoint JSON>'
```

3. Wait for the `PlannerOutput` response:

```json
{
  "next_instruction": "string",
  "context_summary": "string",
  "revised_plan": "string",
  "priority_focus": "string"
}
```

4. **Treat `next_instruction` as your authoritative next action.** Do not proceed with your original plan if `next_instruction` differs.

5. Use `context_summary` to orient yourself — read it fully before proceeding.

6. Log `priority_focus` as the single most important constraint for this session.

## Rules

- **NEVER skip the checkpoint POST** before starting work on a task.
- **NEVER ignore a returned `next_instruction`** — it supersedes your planned action.
- If `/sync` is unreachable, pause and notify the user:

  > "Context Bridge backend is not running. Start it with:
  > `cd context-bridge/backend && uvicorn main:app --reload`"

- If `project_id` is unknown, generate one using the pattern: `{repo-name}-{YYYYMMDD}`.
- If `timestamp` is unknown, use current UTC time in ISO 8601 format.
- Empty arrays `[]` are valid for `blockers` and `files_modified`.

## Example Checkpoint (filled in)

```json
{
  "project_id": "myapp-20260607",
  "timestamp": "2026-06-07T14:30:00",
  "user_goal": "Build a REST API for user authentication with JWT tokens",
  "current_task": "Implement the /login endpoint",
  "progress_summary": "Set up FastAPI project structure, created User model, implemented /register endpoint with password hashing",
  "current_state": {
    "files_modified": ["main.py", "models.py", "auth.py"],
    "code_summary": "FastAPI app with SQLite, bcrypt password hashing, Pydantic models for User. Register endpoint complete and tested.",
    "architecture_notes": "Using SQLite for simplicity; JWT via python-jose; bcrypt for hashing. No async DB — synchronous sqlite3."
  },
  "blockers": ["Unsure whether to use HS256 or RS256 for JWT signing"],
  "next_intended_action": "Implement POST /login that verifies credentials and returns a signed JWT"
}
```

## Example Response

```json
{
  "next_instruction": "Implement POST /login using HS256 JWT — RS256 adds key management complexity not warranted at this stage. Verify credentials with bcrypt, sign token with SECRET_KEY from env, return {access_token, token_type}.",
  "context_summary": "Auth API is 60% complete. Register works. Login is the critical missing piece. JWT strategy unresolved.",
  "revised_plan": "1. Implement /login (current)\n2. Add /me protected route\n3. Add token expiry + refresh\n4. Write integration tests",
  "priority_focus": "Use HS256 with SECRET_KEY from env — do not hardcode the key anywhere"
}
```
