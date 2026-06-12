# context-bridge

[![CI](https://github.com/pushkalkumar/context-bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/pushkalkumar/context-bridge/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/context-bridge)](https://pypi.org/project/context-bridge/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue)](https://pypi.org/project/context-bridge/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![API key optional](https://img.shields.io/badge/API%20key-optional-brightgreen)](#planner-tiers)

Persistent memory and replanning for Claude Code.

```text
[context-bridge] Session context restored:  Summary: JWT auth ~60% done. /register works. /login is the blocker.  Next: Implement /login: verify bcrypt hash, sign HS256 token...  Priority: SECRET_KEY must come from env — it was hardcoded in auth.py:34  Hotspots: auth.py (5x), main.py (3x)
```

## The problem

Claude Code starts each session blind. It forgets what was in progress, what blocked the work, and which decisions were already made. context-bridge fixes that by checkpointing every task and restoring the right context on the next session.

## Install

Python 3.11+ is required.

```bash
pip install context-bridge
context-bridge install
context-bridge
```

One-liner:

```bash
curl -fsSL https://raw.githubusercontent.com/pushkalkumar/context-bridge/main/install.sh | bash
```

## How it works

```text
Claude Code task completes
        |
        v
PostToolUse hook
  capture git diff + task summary
  POST /sync -> planner -> next_instruction + priority_focus
        |
        v
SessionStart hook
  restore context before the first user message
```

| Tier | Requirement | What it does |
|------|-------------|--------------|
| Anthropic | `ANTHROPIC_API_KEY` | Full context-aware replanning |
| Ollama | Local Ollama | Same planner style with local inference |
| Rule-based | Nothing | Deterministic fallback with stagnation handling |

## Features

| Feature | How it works |
|--------|--------------|
| Session continuity | Automatic checkpoints after task completion and end-of-session snapshots |
| Developer profile | Learns recurring blockers and rejected approaches across projects |
| Stagnation detection | Detects repeated tasks and forces decomposition before the loop continues |
| Structured memory | Records ADRs and failures so the system can reason over prior decisions |
| Offline operation | Works without LLMs through the rule-based tier |

## Commands

- `context-bridge` — start the backend server
- `context-bridge install` — wire the hooks and skill into `~/.claude/`
- `context-bridge list` — show projects and checkpoint counts
- `context-bridge status` — show backend health and planner state

Example output for `context-bridge list`:

```text
my-api/main              12 checkpoints   2h ago
my-api/feature-auth       4 checkpoints  14h ago   ⚠ stagnant (3x)
```

## Configuration

| Variable | Purpose |
|----------|---------|
| `ANTHROPIC_API_KEY` | Enables the Anthropic planner |
| `OLLAMA_HOST` | Optional local Ollama endpoint |
| `OLLAMA_MODEL` | Optional Ollama model override |

## Why not just use CLAUDE.md?

- CLAUDE.md is static; context-bridge updates after each task.
- CLAUDE.md is manual; context-bridge runs automatically.
- CLAUDE.md cannot detect stagnation or surface recurring blockers.

## Manual usage

For non-hooked or advanced workflows, see [docs/manual-sync.md](docs/manual-sync.md).

## Contributing

```bash
git clone https://github.com/pushkalkumar/context-bridge.git
cd context-bridge
pip install -e ".[dev]"
pytest tests/
```

The active package for development is the `server/` package.

Open an issue at https://github.com/pushkalkumar/context-bridge/issues if you want to contribute.
  "checkpoint_count": 4
}
```

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

Issues and PRs are welcome. The codebase is small and well-tested (40 tests covering every endpoint).

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
