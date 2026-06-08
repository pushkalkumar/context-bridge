# Context Bridge

A checkpoint-based replanning system that gives Claude Code persistent memory across tasks. Claude Code posts a structured checkpoint to a local backend before every task; the backend stores the history and returns the next authoritative instruction — with or without an API key.

**Works without an API key.** Checkpoints are stored and history is always available. AI-powered planning activates automatically when you add an Anthropic key or install Ollama.

---

## Install (one command)

```bash
curl -fsSL https://raw.githubusercontent.com/pushkal-kumar/context-bridge/main/install.sh | bash
```

This installs the `context-bridge` CLI, sets up `~/.context-bridge/`, and copies the Claude Code skill to `~/.claude/context-bridge.md`.

**Or with pip directly:**

```bash
pip install git+https://github.com/pushkal-kumar/context-bridge.git
```

---

## Start the backend

```bash
context-bridge
# → http://127.0.0.1:8000
```

---

## Add AI planning (optional)

**Option A — Anthropic API key** (paid, best quality):
```bash
echo 'ANTHROPIC_API_KEY=sk-ant-...' > ~/.context-bridge/.env
```

**Option B — Ollama** (free, runs locally):
```bash
# Install from https://ollama.ai, then pull any model:
ollama pull llama3.2
# Context Bridge auto-detects Ollama — no config needed
```

Without either, the backend still stores checkpoints and echoes back your `next_intended_action` as the instruction.

---

## Add the skill to Claude Code

Add this line to your project's `CLAUDE.md` (or `~/.claude/CLAUDE.md` for global):

```
@~/.claude/context-bridge.md
```

Once added, Claude Code will POST a checkpoint to `/sync` before every task and follow the returned instruction.

---

## API

### POST /sync — submit checkpoint, receive plan

```bash
curl -s -X POST http://localhost:8000/sync \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "myapp-20260607",
    "timestamp": "2026-06-07T14:30:00",
    "user_goal": "Build a REST API for user authentication with JWT tokens",
    "current_task": "Implement the /login endpoint",
    "progress_summary": "Set up FastAPI project, created User model, implemented /register",
    "current_state": {
      "files_modified": ["main.py", "models.py", "auth.py"],
      "code_summary": "FastAPI app with SQLite and bcrypt. Register endpoint complete.",
      "architecture_notes": "Synchronous sqlite3, bcrypt hashing, python-jose for JWT"
    },
    "blockers": ["Unsure whether to use HS256 or RS256"],
    "next_intended_action": "Implement POST /login returning a signed JWT"
  }'
```

**Response:**
```json
{
  "next_instruction": "Use HS256 with SECRET_KEY from env...",
  "context_summary": "Auth API 60% complete. Login is the missing piece.",
  "revised_plan": "1. /login\n2. /me protected route\n3. token refresh\n4. tests",
  "priority_focus": "Never hardcode the JWT secret key"
}
```

### GET /health
```bash
curl http://localhost:8000/health
# {"status": "ok", "service": "context-bridge"}
```

### GET /history/{project_id}
```bash
curl http://localhost:8000/history/myapp-20260607
# Returns last 20 checkpoints, newest first
```

---

## How it works

```
Claude Code
    │
    │  POST /sync  (Checkpoint JSON)
    ▼
Context Bridge backend
    ├── saves checkpoint to SQLite (~/.context-bridge/context_bridge.db)
    ├── loads last 10 checkpoints for project
    ├── calls planner:
    │     Anthropic API  → if ANTHROPIC_API_KEY set
    │     Ollama         → if running at localhost:11434
    │     rule-based     → fallback (no LLM required)
    └── returns PlannerOutput
    │
    │  { next_instruction, context_summary, revised_plan, priority_focus }
    ▼
Claude Code executes next_instruction
```
