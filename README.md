# context-bridge

> Claude Code starts every session with zero memory. Context Bridge fixes that.

[![CI](https://github.com/pushkal-kumar/context-bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/pushkal-kumar/context-bridge/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/context-bridge)](https://pypi.org/project/context-bridge/)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![API key](https://img.shields.io/badge/API%20key-optional-brightgreen)](https://github.com/pushkal-kumar/context-bridge#add-ai-planning-optional)

---

## The problem

Claude Code has no memory between sessions. Every time you open a project you re-explain:

- What you built in the last session and why
- Which files were modified and what they do
- What broke, what was deferred, what decisions were made
- Where you were heading next

A 30-minute coding session requires a 5-minute re-orientation tax every single time.

## How it fixes it

**Without Context Bridge — starting Session 2:**

```
You:   "Let's continue on the auth API"
Claude: "Sure! What are you working on? What's the current state?"
You:   [re-explains everything]
```

**With Context Bridge — starting Session 2:**

```
Claude POSTs to /sync → receives:

next_instruction  → "Implement GET /me protected route. JWT middleware is
                     already in auth.py — reuse it, don't recreate it."
context_summary   → "JWT auth complete: HS256, tokens in Redis, /register
                     and /login endpoints done. Files: auth.py, models.py,
                     main.py, redis_client.py"
priority_focus    → "SECRET_KEY must come from env — you left it hardcoded
                     in auth.py:34 last session"
```

Zero re-orientation. Claude picks up exactly where it left off.

---

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/pushkal-kumar/context-bridge/main/install.sh | bash
```

Or with pip:

```bash
pip install git+https://github.com/pushkal-kumar/context-bridge.git
```

---

## Start the backend

```bash
context-bridge
# → http://127.0.0.1:8000
# → Dashboard at http://127.0.0.1:8000/
```

---

## Add AI planning (optional)

> ✅ **Works without an API key.** Checkpoints are always stored. History is always available.
> AI planning is an optional enhancement — the tool is useful without it.

**Option A — Anthropic** (best quality):
```bash
echo 'ANTHROPIC_API_KEY=sk-ant-...' > ~/.context-bridge/.env
```

**Option B — Ollama** (free, runs locally, zero cost):
```bash
# Install from https://ollama.ai, then:
ollama pull llama3.2
# Context Bridge auto-detects Ollama — no config needed
```

Without either, the backend runs stagnation detection and blocker pattern analysis entirely locally — and still catches when Claude Code is going in circles.

---

## Add the skill to Claude Code

Claude Code reads a `CLAUDE.md` file in your project root (or `~/.claude/CLAUDE.md` globally) to know how to behave. The skill file tells it to checkpoint before every task.

**One command:**
```bash
context-bridge install
# ✅ Skill installed. Claude Code will now checkpoint every task.
```

Or manually — add this line to your project's or global `CLAUDE.md`:
```
@~/.claude/context-bridge.md
```

---

## See it in action

**Session 1** — you finish implementing auth and checkpoint before closing:

```bash
curl -s -X POST http://localhost:8000/sync \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "myapi-20260607",
    "timestamp": "2026-06-07T18:00:00",
    "user_goal": "Build JWT authentication for the API",
    "current_task": "Implement /login endpoint",
    "progress_summary": "FastAPI app set up, User model done, /register works with bcrypt hashing",
    "current_state": {
      "files_modified": ["main.py", "models.py", "auth.py"],
      "code_summary": "FastAPI + SQLite. /register endpoint tested and passing.",
      "architecture_notes": "HS256 for JWT, python-jose library, bcrypt via passlib"
    },
    "blockers": [],
    "next_intended_action": "Implement GET /me protected route"
  }'
```

**Response saved with the checkpoint:**

```json
{
  "next_instruction": "Implement GET /me: extract user_id from JWT in Authorization header using the middleware already in auth.py — don't recreate it. Return {id, email, created_at}.",
  "context_summary": "JWT auth complete. /register and /login done. Files: auth.py, models.py, main.py. HS256 signing with python-jose.",
  "revised_plan": "1. GET /me protected route (current)\n2. Token expiry + refresh\n3. Integration tests\n4. Rate limiting",
  "priority_focus": "Reuse existing JWT middleware in auth.py — it handles all edge cases already"
}
```

**Session 2** — fresh Claude Code instance, same command, instant context:

```bash
curl -s http://localhost:8000/history/myapi-20260607
# Returns all prior checkpoints with what the planner recommended each time
```

The [dashboard](http://localhost:8000/) shows the full project timeline.

---

## API

### `POST /sync` — submit a checkpoint, receive a plan

| Field | Type | Description |
|-------|------|-------------|
| `project_id` | string | Unique project identifier (auto-generated if empty) |
| `timestamp` | string | ISO 8601, auto-filled if empty |
| `user_goal` | string | The overarching goal for this project |
| `current_task` | string | The specific task being started or just completed |
| `progress_summary` | string | What has been done toward `user_goal` |
| `current_state` | object | `files_modified`, `code_summary`, `architecture_notes` |
| `blockers` | array | Current blockers (empty array is fine) |
| `next_intended_action` | string | What you were about to do next |

Returns a `PlannerOutput`:

| Field | Description |
|-------|-------------|
| `next_instruction` | Authoritative next action — follow this over your own plan |
| `context_summary` | Where the project stands right now |
| `revised_plan` | Step-by-step plan from this point forward |
| `priority_focus` | The single most important constraint this session |

### `GET /health`

```bash
curl http://localhost:8000/health
# {"status": "ok", "service": "context-bridge"}
```

### `GET /projects`

```bash
curl http://localhost:8000/projects
# [{"project_id": "myapi-20260607", "checkpoint_count": 7, "last_active": "..."}]
```

### `GET /history/{project_id}`

```bash
curl http://localhost:8000/history/myapi-20260607
# Last 50 checkpoints, newest first, each including the planner output
```

---

## How it works

```
Claude Code
    │
    │  POST /sync  (Checkpoint JSON)
    ▼
Context Bridge backend
    ├── fetches last 10 checkpoints for project (history)
    ├── runs planner:
    │     Anthropic API  → if ANTHROPIC_API_KEY set
    │     Ollama         → if running at localhost:11434  (free)
    │     rule-based     → stagnation + blocker detection (no LLM needed)
    ├── attaches planner output to checkpoint
    ├── saves to SQLite (~/.context-bridge/context_bridge.db)
    └── returns PlannerOutput
    │
    │  { next_instruction, context_summary, revised_plan, priority_focus }
    ▼
Claude Code executes next_instruction
```

The rule-based planner (no LLM) detects when Claude Code is stuck: if the same task appears in 3+ consecutive checkpoints, it overrides `next_intended_action` with instructions to break it down. Recurring blockers across sessions are flagged and surfaced as `priority_focus`.

---

## Why Context Bridge

| Feature | Context Bridge | No memory tool |
|---------|:-:|:-:|
| Persists context across sessions | ✅ | ❌ |
| Works without an API key | ✅ | — |
| AI-powered replanning | ✅ optional | — |
| Detects stagnation / loops | ✅ | — |
| Local-first (your data, your machine) | ✅ | — |
| Web dashboard | ✅ | — |
| Single command install | ✅ | — |

---

## License

MIT
