# context-bridge

[![CI](https://github.com/pushkalkumar/context-bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/pushkalkumar/context-bridge/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/context-bridge)](https://pypi.org/project/context-bridge/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue)](https://pypi.org/project/context-bridge/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![API key optional](https://img.shields.io/badge/API%20key-optional-brightgreen)](#planner-tiers)

Persistent memory and replanning for [Claude Code](https://claude.ai/code). Checkpoints what Claude was doing, stores it locally, and feeds it back at the start of the next session — automatically, without any manual intervention.

---

## The problem

Claude Code has no memory between sessions. Every time you open a new terminal, Claude starts blind:

- Which files were you editing?
- What was the actual plan?
- What blockers did you hit?
- Why did you make that architectural decision?

You re-explain this every session. It wastes time and causes mistakes.

## What context-bridge does

After every `Task` tool call, a hook captures what Claude did (git diff, files modified, blockers, progress) and sends it to a local server. The server stores it and runs a planner on it. Next session, another hook fires *before Claude's first message* and injects the context:

```
[context-bridge] Session context restored:
  Summary:  JWT auth ~60% done. /register works. /login is the blocker.
  Next:     Implement /login: verify bcrypt hash, sign HS256 token with
            SECRET_KEY from env, return {access_token, token_type: "bearer"}.
  Priority: SECRET_KEY must come from env — it was hardcoded in auth.py:34 last session
```

Claude picks up exactly where it left off. No re-explanation, no re-orientation.

---

## Install

```bash
pip install context-bridge
context-bridge install     # wires SessionStart + PostToolUse hooks into ~/.claude/
context-bridge             # starts the backend on port 7723
```

One-liner:

```bash
curl -fsSL https://raw.githubusercontent.com/pushkalkumar/context-bridge/main/install.sh | bash
```

Open Claude Code. The hooks are live on the next session.

---

## Commands

```bash
context-bridge             # start the backend server
context-bridge install     # (re)install hooks and skill
context-bridge list        # show all projects with checkpoint counts
context-bridge status      # backend health + planner tier in use
```

`context-bridge list` output:

```
  my-api/main              12 checkpoints   2h ago
  my-api/feature-auth       4 checkpoints  14h ago   ⚠ stagnant (3x)
  data-pipeline/main        8 checkpoints   3d ago
```

The stagnation warning fires when Claude submits the same task three sessions in a row. The planner catches it and forces decomposition.

---

## Planner tiers

The server picks the best available planner automatically:

| Tier | Requirement | What it does |
|------|-------------|--------------|
| Anthropic | `ANTHROPIC_API_KEY` | Full context-aware replanning with claude-sonnet-4-6 |
| Ollama | Ollama running locally | Same, free, using `qwen2.5-coder:7b` by default |
| Rule-based | Nothing | Stagnation detection, recurring blocker surfacing — deterministic, offline |

The rule-based tier is not a compromise. It catches the most common failure mode (Claude spinning on the same task), surfaces blockers that appear across multiple sessions, and works with zero latency and zero cost.

Configure via `~/.context-bridge/.env`:

```bash
ANTHROPIC_API_KEY=sk-ant-...
# or:
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=qwen2.5-coder:7b
```

Ollama is auto-detected at `localhost:11434` — you don't need to set `OLLAMA_HOST` if it's running there.

---

## How it works

```
Your Claude Code session
        |
        | Task completes
        |
        v
PostToolUse hook
  git diff --stat HEAD
  git log --oneline -5
  POST /sync  ──────────────────────────> Backend (port 7723)
                                               |
                                               | stagnation check
                                               | Anthropic / Ollama / rule-based
                                               |
  <── next_instruction + priority_focus <──────┘
        |
        | Every 5 tool calls:
        | GET /history/{project_id}?limit=1
        | alert if priority changed
        |
Next session starts
        |
SessionStart hook
  GET /history/{project_id}?limit=1
  inject: context_summary, next_instruction, priority_focus
        |
        v
Claude receives context before seeing any user message
```

Project IDs are `reponame/branch` (e.g. `my-app/main`), stable across sessions, separate per branch.

---

## API

The server runs on `http://localhost:7723`. The dashboard is at `http://localhost:7723/`.

### `POST /sync` — checkpoint + plan

Submit a checkpoint, receive an authoritative plan. This is what the hook calls.

```json
{
  "project_id": "my-app/main",
  "user_goal": "Build JWT authentication",
  "current_task": "Implement /login endpoint",
  "progress_summary": "FastAPI skeleton done. /register works.",
  "current_state": {
    "files_modified": ["main.py", "auth.py"],
    "git_diff_stat": "auth.py | 23 +++--",
    "architecture_notes": "HS256 JWT via python-jose"
  },
  "blockers": [],
  "next_intended_action": "Write POST /login handler"
}
```

Response:

```json
{
  "next_instruction": "Implement /login: verify bcrypt hash, sign HS256 token...",
  "context_summary": "Auth API 60% done. /register works. /login is the blocker.",
  "revised_plan": "1. /login\n2. GET /me\n3. Token expiry\n4. Tests",
  "priority_focus": "SECRET_KEY from env — never hardcode it",
  "source": "anthropic",
  "stagnation_count": 1
}
```

### Other endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/checkpoint` | Store checkpoint without running planner |
| `GET` | `/history/{project_id}` | Last N checkpoints (newest first) |
| `GET` | `/projects` | All projects with stagnation counts |
| `GET` | `/stats` | Total projects, checkpoints, stagnation events |
| `DELETE` | `/projects/{project_id}` | Delete a project |
| `GET` | `/projects/{project_id}/export` | Download history as JSON |

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | — | Enables Anthropic planner |
| `OLLAMA_HOST` | auto | Set if Ollama isn't at localhost:11434 |
| `OLLAMA_MODEL` | `qwen2.5-coder:7b` | Model for Ollama tier |
| `DB_PATH` | `~/.context-bridge/checkpoints.db` | SQLite database path |
| `SERVER_PORT` | `7723` | Backend port |

All variables can also go in `~/.context-bridge/.env`.

---

## Why the rule-based planner matters

Every other memory tool for Claude Code requires an API key. Context-bridge works offline from day one.

The rule-based planner does three things:

1. **Stagnation detection** — tracks the same task across consecutive checkpoints using token-normalized comparison. At three in a row, it tells Claude to pick the smallest completable subtask and do only that.

2. **Recurring blocker escalation** — if the same blocker appears across multiple sessions, it surfaces and prioritizes it over the current task.

3. **Continuity without an LLM** — returns a structured `next_instruction` and `priority_focus` even with no API access.

---

## Contributing

Issues and PRs are welcome. The codebase is small and well-tested (23 tests covering every endpoint).

```bash
git clone https://github.com/pushkalkumar/context-bridge
cd context-bridge
pip install -e ".[dev]"
pytest
```

The server package is `server/`. Hook logic is in `server/hook.py`. The planner tiers are in `server/planner.py`.

---

## License

MIT
