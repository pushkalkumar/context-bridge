# Context Bridge Skill

## What This Is

Context Bridge is a checkpoint-based replanning system that gives Claude Code persistent
memory across sessions. A local backend stores checkpoint history and returns an authoritative
next instruction — with or without an API key.

After running `context-bridge install`, checkpointing is **automatic**. Claude Code lifecycle
hooks fire on every `Task` completion and on session start, with no manual steps required.

---

## Automatic Mode (hooks installed)

When `context-bridge install` has been run, the following hooks are active in
`~/.claude/settings.json`:

| Hook | When it fires | What it does |
|------|---------------|--------------|
| `SessionStart` | Start of every session | Fetches last checkpoint, injects `context_summary` + `next_instruction` + `priority_focus` before your first message |
| `PostToolUse` (Task) | After every `Task` tool completion | Auto-checkpoints with real `git diff --stat HEAD` + `git log --oneline -5` metadata; POSTs to `/sync`; validates response |
| `PostToolUse` (any) | Every 5th tool call | Polls `/history/{project_id}?limit=1`; surfaces a reminder if `priority_focus` has changed |

You do not need to manually POST to `/sync` when hooks are installed.

---

## Manual Mode (fallback)

Use this when:
- Hooks are not installed yet, or
- You want to checkpoint at a specific moment with richer context than the hook auto-captures

### 1. Generate the checkpoint JSON

```json
{
  "project_id": "<auto — see below>",
  "timestamp": "<ISO 8601 UTC, e.g. 2026-06-07T14:30:00>",
  "user_goal": "<the overarching goal the user stated>",
  "current_task": "<the specific task being started or just completed>",
  "progress_summary": "<what has been done so far toward user_goal>",
  "current_state": {
    "files_modified": ["list", "of", "relative", "file", "paths"],
    "code_summary": "<brief description of current code state>",
    "architecture_notes": "<key architectural decisions made>"
  },
  "blockers": ["list of current blockers, or empty array []"],
  "next_intended_action": "<what you were about to do next>"
}
```

**Auto-generating `project_id`:**
1. Run `git remote get-url origin 2>/dev/null` → extract repo name → append date: `reponame-YYYYMMDD`
2. If no git remote: use current directory name + date: `dirname-YYYYMMDD`
3. Use the same `project_id` for all checkpoints in the same project

### 2. POST to /sync

```bash
curl -s -X POST http://localhost:8000/sync \
  -H "Content-Type: application/json" \
  -d '<checkpoint JSON>'
```

### 3. Read the response

```json
{
  "next_instruction": "...",
  "context_summary": "...",
  "revised_plan": "...",
  "priority_focus": "..."
}
```

- **`next_instruction`** — treat this as your authoritative next action; it supersedes your original plan if different
- **`context_summary`** — read this to orient yourself before proceeding
- **`priority_focus`** — the single most important constraint for this session; log it and do not violate it

**If `next_instruction` is empty**, halt and warn the user that the backend may be unhealthy.
Do not proceed silently with no context.

---

## Rules

- **Never ignore `next_instruction`** — follow it even if it differs from what you planned.
- **If `/sync` is unreachable**, pause and tell the user:
  > "Context Bridge backend is not running. Start it with: `context-bridge`"
  > (or: `uvicorn context_bridge.main:app --reload` from source)
- If the backend returns an error, log it and proceed with `next_intended_action` from your own checkpoint.

---

## Filled-In Example

**Checkpoint:**
```json
{
  "project_id": "myapi-20260607",
  "timestamp": "2026-06-07T14:30:00",
  "user_goal": "Build a REST API for user authentication with JWT tokens",
  "current_task": "Implement the /login endpoint",
  "progress_summary": "Set up FastAPI project, created User model, implemented /register with bcrypt hashing",
  "current_state": {
    "files_modified": ["main.py", "models.py", "auth.py"],
    "code_summary": "FastAPI app with SQLite. Register endpoint complete and tested.",
    "architecture_notes": "Synchronous sqlite3, bcrypt via passlib, JWT via python-jose"
  },
  "blockers": ["Unsure whether to use HS256 or RS256 for JWT signing"],
  "next_intended_action": "Implement POST /login that verifies credentials and returns a signed JWT"
}
```

**Response:**
```json
{
  "next_instruction": "Use HS256 — RS256 adds key-pair management with no benefit at this scale. Sign with SECRET_KEY from env, never hardcoded. Return {access_token, token_type: 'bearer'}.",
  "context_summary": "Auth API ~60% complete. Register works. Login is the missing piece before the project is usable.",
  "revised_plan": "1. Implement /login (current)\n2. Add GET /me protected route\n3. Add token expiry\n4. Integration tests",
  "priority_focus": "SECRET_KEY must come from env — never hardcode it anywhere in the codebase"
}
```
