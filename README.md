# context-bridge

[![CI](https://github.com/pushkalkumar/context-bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/pushkalkumar/context-bridge/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue)](https://github.com/pushkalkumar/context-bridge)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Last commit](https://img.shields.io/github/last-commit/pushkalkumar/context-bridge)](https://github.com/pushkalkumar/context-bridge/commits/main)
[![API key optional](https://img.shields.io/badge/API%20key-optional-brightgreen)](#how-it-works)

Persistent memory and replanning for Claude Code.

```text
[context-bridge] Session context restored:
  Summary:  JWT auth ~60% done. /register works. /login is the blocker.
  Next:     Implement /login: verify bcrypt hash, sign HS256 token with
            SECRET_KEY from env, return {access_token, token_type: "bearer"}.
  Priority: SECRET_KEY must come from env — it was hardcoded in auth.py:34
  Hotspots: auth.py (5x), main.py (3x)
```

That block appears before Claude reads your first message. No re-explaining, no re-orientation.

## The problem

Claude Code starts every session blind. It forgets what was in progress, what blocked the work, and which decisions were already made — so you re-explain it, every time. context-bridge checkpoints every task automatically and restores the right context at the start of the next session.

## Install

Python 3.11+ required.

```bash
curl -fsSL https://raw.githubusercontent.com/pushkalkumar/context-bridge/main/install.sh | bash
```

Or manually:

```bash
pip install claude-context-bridge   # or: pip install "git+https://github.com/pushkalkumar/context-bridge.git@v0.4.0"
context-bridge install              # wires SessionStart + PostToolUse + Stop hooks
context-bridge                      # start the backend (separate terminal)
```

The PyPI package is `claude-context-bridge`; the command it installs is `context-bridge`. (`pip install context-bridge` is an unrelated package — don't use it.)

## How it works

```text
Claude Code task completes
        |
        v
PostToolUse hook
  capture git diff + task summary
  POST /sync ──> local backend (port 7723)
                   stagnation check
                   planner: next_instruction + priority_focus
        |
        v
Session ends ──> Stop hook ──> end-of-session snapshot
        |
Next session starts
        |
        v
SessionStart hook
  known project: restore summary, next step, constraint, hotspots
  new project:   inject cross-project developer profile
        |
        v
Claude receives context before your first message
```

Everything is local: a SQLite database in `~/.context-bridge/` and a FastAPI server on `127.0.0.1`. The planner picks the best available tier:

| Tier | Requirement | What it does |
|------|-------------|--------------|
| Anthropic | `ANTHROPIC_API_KEY` | Full context-aware replanning |
| Ollama | Ollama running locally | Same, free, local inference |
| Rule-based | Nothing | Deterministic stagnation + blocker heuristics — offline, zero latency |

## Features

| Feature | How it works |
|---------|--------------|
| Session continuity | Auto-checkpoint after every task and at session end; context injected on the next start |
| Stagnation detection | Same task 3x in a row → root-cause report (stuck since, dominant blocker) and forced decomposition |
| Structured memory | `adr` and `failure` events record decisions and abandoned approaches alongside the timeline |
| Developer profile | New projects get your cross-project profile: preferred stack, known pitfalls, rejected approaches |
| Offline operation | The rule-based tier needs no API key — checkpoints, stagnation, and blockers all work air-gapped |

## Commands

```bash
context-bridge             # start the backend server
context-bridge install     # (re)install hooks and skill
context-bridge uninstall   # remove hooks and skill (keeps the database)
context-bridge list        # all projects with checkpoint counts
context-bridge status      # backend health + planner tier in use
```

`context-bridge list`:

```text
  my-api/main              12 checkpoints   2h ago
  my-api/feature-auth       4 checkpoints  14h ago   ⚠ stagnant (3x)
  data-pipeline/main        8 checkpoints   3d ago
```

## Configuration

All variables can go in `~/.context-bridge/.env`.

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | — | Enables the Anthropic planner |
| `OLLAMA_HOST` | auto-detected | Set if Ollama isn't at `localhost:11434` |
| `OLLAMA_MODEL` | `qwen2.5-coder:7b` | Model for the Ollama tier |
| `DB_PATH` | `~/.context-bridge/checkpoints.db` | SQLite database path |
| `SERVER_PORT` | `7723` | Backend port |

## Why not just use CLAUDE.md?

- CLAUDE.md is static — context-bridge updates after every task.
- CLAUDE.md is manual — context-bridge is automatic; you never write the summary yourself.
- CLAUDE.md can't detect stagnation, surface recurring blockers, or learn across projects.

They compose: CLAUDE.md holds your conventions, context-bridge holds your state.

## Manual usage

The hooks are optional. The API works with plain HTTP — see [docs/manual-sync.md](docs/manual-sync.md) for project ID derivation, manual checkpointing, and structured event examples.

## Contributing

```bash
git clone https://github.com/pushkalkumar/context-bridge
cd context-bridge
pip install -e ".[dev]"
pytest
```

42 tests cover every endpoint. The active package is `server/` (hooks in `server/hook.py`, planner tiers in `server/planner.py`). Issues and PRs welcome: [open issues](https://github.com/pushkalkumar/context-bridge/issues).

## License

MIT
