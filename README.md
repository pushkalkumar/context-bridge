# context-bridge

Claude Code forgets everything between sessions. Context Bridge fixes that.

[![CI](https://github.com/pushkal-kumar/context-bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/pushkal-kumar/context-bridge/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/context-bridge)](https://pypi.org/project/context-bridge/)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![API key](https://img.shields.io/badge/API%20key-optional-brightgreen)](#planner-tiers)

---

## What it does

Before every task, Claude Code posts a checkpoint to a local server. The server stores
checkpoint history and returns an authoritative plan. On the next session, it injects
that context automatically — before Claude Code's first message.

```
Session 1 ends:
  Hook fires → POST /sync → checkpoint stored

Session 2 starts:
  SessionStart hook fires →
  [context-bridge] Session context restored:
    Summary:  JWT auth complete. /register and /login done. Files: auth.py, models.py.
    Next:     Implement GET /me — reuse the JWT middleware in auth.py, don't recreate it.
    Priority: SECRET_KEY must come from env — it was hardcoded in auth.py:34 last session
```

Claude picks up exactly where it left off.

---

## Install

```bash
pip install context-bridge
context-bridge install   # installs skill + lifecycle hooks to ~/.claude/
context-bridge           # starts the backend on port 7723
```

Or one command:

```bash
curl -fsSL https://raw.githubusercontent.com/pushkal-kumar/context-bridge/main/install.sh | bash
```

---

## Planner tiers

The backend runs the best available planner automatically, in this order:

| Tier | Requirement | Quality |
|------|-------------|---------|
| Anthropic | `ANTHROPIC_API_KEY` in env or `~/.context-bridge/.env` | Best |
| Ollama | `OLLAMA_HOST=http://localhost:11434` | Good, free |
| Rule-based | Nothing | Works offline, detects stagnation |

The rule-based tier is not a fallback you reluctantly use — it is genuinely useful.
It detects when Claude Code is stuck (same task for 3+ checkpoints), surfaces recurring
blockers, and tells Claude exactly what to do about them.

```bash
# Use Anthropic:
echo 'ANTHROPIC_API_KEY=sk-ant-...' >> ~/.context-bridge/.env

# Use Ollama:
echo 'OLLAMA_HOST=http://localhost:11434' >> ~/.context-bridge/.env
echo 'OLLAMA_MODEL=qwen2.5-coder:7b' >> ~/.context-bridge/.env
```

---

## API

### `POST /sync` — checkpoint + plan

Submit a checkpoint; receive a plan. This is the primary endpoint.

**Request body:**

```json
{
  "project_id": "myapi-20260610",
  "user_goal": "Build JWT authentication for the API",
  "current_task": "Implement /login endpoint",
  "progress_summary": "FastAPI skeleton done. /register works with bcrypt.",
  "current_state": {
    "files_modified": ["main.py", "auth.py"],
    "code_summary": "SQLite + FastAPI. /register tested.",
    "architecture_notes": "HS256 JWT via python-jose"
  },
  "blockers": [],
  "next_intended_action": "Write POST /login handler"
}
```

**Response (`SyncResponse`):**

```json
{
  "next_instruction": "Implement /login: verify bcrypt hash, sign HS256 token with SECRET_KEY from env, return {access_token, token_type: 'bearer'}.",
  "context_summary": "Auth API 60% done. /register works. /login is the blocker.",
  "revised_plan": "1. /login\n2. GET /me\n3. Token expiry\n4. Tests",
  "priority_focus": "SECRET_KEY from env — never hardcode it",
  "source": "anthropic",
  "stagnation_count": 1
}
```

### `POST /checkpoint` — store only

Stores a checkpoint without running the planner. Returns `{project_id, stagnation_count}`.

### `GET /history/{project_id}?limit=50`

Returns last N checkpoints (max 100), newest first. Each includes `_planner_output`.

### `GET /projects`

Lists all project IDs with checkpoint count, last active timestamp, and current stagnation count.

### `GET /stats`

```json
{"total_projects": 4, "total_checkpoints": 38, "stagnation_events": 2}
```

### `DELETE /projects/{project_id}`

Deletes all checkpoints for a project. Returns `{"deleted": N}`.

### `GET /projects/{project_id}/export`

Downloads all checkpoints as a JSON file.

### `GET /health`

```json
{"status": "ok", "service": "context-bridge", "port": 7723}
```

---

## Config

All settings load from environment variables or `~/.context-bridge/.env`:

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | `None` | Enables Anthropic planner tier |
| `OLLAMA_HOST` | `None` | e.g. `http://localhost:11434` |
| `OLLAMA_MODEL` | `qwen2.5-coder:7b` | Ollama model name |
| `DB_PATH` | `~/.context-bridge/checkpoints.db` | SQLite database location |
| `SERVER_PORT` | `7723` | Backend port |

---

## How it works

```
Session start
    │
    │  SessionStart hook → GET /history/{project_id}?limit=1
    ▼
Claude receives last checkpoint context automatically

During session
    │
    │  After every Task tool call:
    │    git diff --stat HEAD + git log --oneline -5
    │    POST /sync → stagnation check → planner → plan stored
    │
    │  Every 5th tool call:
    │    GET /history/{project_id}?limit=1
    │    Alert if priority_focus changed
    ▼

Backend (sqlite at ~/.context-bridge/checkpoints.db)
    ├── Stagnation tracking: increments count on consecutive same task
    ├── Planner: Anthropic → Ollama → rule-based
    └── Response: next_instruction + source + stagnation_count
```

The web dashboard at `http://localhost:7723/` shows the full project timeline with
stagnation warnings, planner outputs, and file history.

---

## Why the rule-based planner is useful without an API key

The rule-based planner:
- Detects stagnation: same task (normalized) in 3+ consecutive checkpoints → forces decomposition
- Detects recurring blockers: same blocker across sessions → surfaces and escalates
- Works offline, zero latency, zero cost
- No hallucination — deterministic output

The stagnation check uses a persistent `stagnation_count` field stored in SQLite with
token-normalized string comparison. The count increments each time the same task is
submitted consecutively, and resets to 1 when the task changes.

---

## License

MIT
