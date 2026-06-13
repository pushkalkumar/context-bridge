# context-bridge

[![CI](https://github.com/pushkalkumar/context-bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/pushkalkumar/context-bridge/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue)](https://github.com/pushkalkumar/context-bridge)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Works offline](https://img.shields.io/badge/works-offline-brightgreen)](#how-it-works)
[![PyPI](https://img.shields.io/pypi/v/claude-context-bridge)](https://pypi.org/project/claude-context-bridge/)

Every Claude Code session starts blind. You opened a project you were deep in yesterday, and Claude has no idea what you were doing, what blocked you, or which decisions you already made. You explain it again — the auth flow, the reason you rejected Redis, the fact that `SECRET_KEY` must come from an environment variable. Every. Single. Session.

context-bridge ends that. It checkpoints every completed task automatically via Claude Code's lifecycle hooks, and injects the right context before Claude reads your first message.

```text
[context-bridge] Session context restored:
  Summary:  JWT auth ~60% done. /register works. /login is the blocker.
  Next:     Implement /login: verify bcrypt hash, sign HS256 token with
            SECRET_KEY from env, return {access_token, token_type: "bearer"}.
  Priority: SECRET_KEY must come from env — it was hardcoded in auth.py:34
  Hotspots: auth.py (5x), main.py (3x)
```

When the same task shows up three sessions in a row without completing, the stagnation detector fires and generates a root-cause report:

```text
[context-bridge] Checkpoint saved. Next: The task 'Implement /login endpoint' has
  appeared 3 consecutive times without completing. Pick the smallest completable
  subtask and do only that one thing. Root cause: 'bcrypt import error'.

[context-bridge] Stagnation report: stuck since 2026-06-12T10:18:00 (4.2h,
  3 checkpoints). Blocker: bcrypt import error. Break this into smaller tasks.
```

When a task is running significantly longer than your normal pace, the velocity tracker alerts before you realize you're stuck:

```text
[context-bridge] Session context restored:
  Summary:  JWT auth ~60% done. /register blocked on /login.
  Next:     ⚠ VELOCITY ALERT: This task is taking 2.6x longer than your baseline on this branch.
            Last 10 tasks averaged 7m 0s. Current task has been open 18m 22s.
            Consider: Is this blocked? Is the scope larger than expected? Should it be decomposed?

            Implement /login: verify bcrypt hash, sign HS256 token.
  Priority: SECRET_KEY must come from env
```

## Install

Python 3.11+ required.

```bash
curl -fsSL https://raw.githubusercontent.com/pushkalkumar/context-bridge/main/install.sh | bash
```

Or manually:

```bash
pip install claude-context-bridge
context-bridge install    # wires SessionStart + PostToolUse + Stop hooks into ~/.claude/settings.json
context-bridge            # start the backend server (separate terminal or background process)
```

## How it works

```text
Claude Code task completes
        |
        v
PostToolUse hook fires
  captures git diff, files touched, task summary, any error lines
  classifies checkpoint type (task / scratch / ephemeral micro-edit)
  POST /sync ──────────────────────────────> local backend (port 7723)
                                              stagnation check (3× same task → root-cause report)
                                              velocity check (2× baseline → alert prepended to instruction)
                                              planner: Anthropic → Ollama → rule-based, in that order
                                              returns: next_instruction, confidence, blocker_class, alternatives
        |
        v
Session ends ──> Stop hook ──> session-type checkpoint (preserves end-of-session state)
        |
Next session starts
        |
        v
SessionStart hook fires
  known project: injects summary, next step, active constraint, recurring hotspots
                 surfaces related past work from other projects (semantic search, similarity ≥ 0.75)
  new project:   injects cross-project developer profile (preferred stack, avg velocity, known pitfalls)
        |
        v
Claude receives full context before reading your first message
```

Everything runs locally: SQLite in `~/.context-bridge/` and a FastAPI server on `127.0.0.1:7723`. The planner tries each tier in order and falls back automatically — if the Anthropic API is unavailable, Ollama is tried; if Ollama is unavailable, the deterministic rule-based tier runs with zero latency and no network.

| Tier | Requirement | Output |
|------|-------------|--------|
| Anthropic | `ANTHROPIC_API_KEY` | Context-aware replanning with confidence score, alternatives, blocker classification |
| Ollama | Ollama running locally | Same structured output, free, local inference |
| Rule-based | Nothing | Deterministic stagnation detection, blocker classification, decomposition flag — works offline, zero latency |

## What makes this different

Most tools in this space auto-update a CLAUDE.md section or maintain a scratchpad. context-bridge does something different: it detects when you're stuck and forces a structured response, rather than letting the same blocked task accumulate silently across sessions.

The stagnation detector counts how many consecutive checkpoints contain the same normalized task. At three, it switches from "what's next" mode to decomposition mode: it generates a root-cause analysis (stuck since when, elapsed hours, dominant blocker from the blocker history), and changes `next_instruction` to force Claude to pick the smallest completable subtask rather than retrying the whole thing. This is not a heuristic applied after the fact — it runs on every checkpoint, so it catches stagnation the moment it crosses the threshold.

Velocity tracking is a per-project, per-branch baseline computed from `task_duration_ms` stored with each checkpoint. When the current task has been open for 2× longer than your rolling average — computed from the last 10 task checkpoints — a structured alert is prepended to the planner's instruction before it reaches Claude. No other comparable tool tracks task duration at this granularity, which means no other tool can tell you "this task is unusually slow for you specifically, on this project."

The three-tier planner is a reliability guarantee, not a marketing feature. The rule-based tier covers stagnation detection, blocker classification (`technical_debt`, `dependency`, `unclear_spec`, `scope_creep`), and decomposition suggestion with no network call and no latency. If you set `ANTHROPIC_API_KEY`, the Anthropic tier adds context-aware replanning with a confidence score (0–1), alternative approaches, and a structured blocker class. If the Anthropic API is down or returns unparseable JSON, the rule-based tier fires with `confidence: 0.3` to signal the fallback — you always get a usable response.

## Commands

All commands read from the same SQLite database the hooks write to — no server state is lost between restarts.

```bash
context-bridge             # start the backend server
context-bridge install     # (re)install hooks and skill into ~/.claude/
context-bridge uninstall   # remove hooks and skill (database is preserved)
context-bridge list        # all projects with checkpoint counts and type breakdown
context-bridge status      # backend health, planner tier, velocity and embedding status
context-bridge diff        # before/after of the last two task checkpoints
context-bridge export      # write CONTEXT_BRIDGE_SNAPSHOT.md (CLAUDE.md-compatible)
```

`context-bridge list`:

```text
  my-api/main              12 checkpoints (9 task, 2 scratch, 1 session)   2h ago
  my-api/feature-auth       4 checkpoints (4 task, 0 scratch, 0 session)  14h ago   ⚠ stagnant (3x)
  data-pipeline/main        8 checkpoints (6 task, 1 scratch, 1 session)   3d ago
```

`context-bridge diff`:

```text
  FROM (2h ago):  Implement /register endpoint
  TO   (14m ago): Implement /login endpoint

  Planner confidence:    0.87 → 0.71  (↓)
  Velocity:              4m 12s → 18m 22s  (slower)
  Blocker class:         none → technical_debt
  Decomposition needed:  false → true
```

## Configuration

All variables can go in `~/.context-bridge/.env`.

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | — | Enables the Anthropic planner tier and embedding fallback for semantic search |
| `VOYAGE_API_KEY` | — | Preferred key for semantic embeddings (Voyage AI); tried before `ANTHROPIC_API_KEY` |
| `OLLAMA_HOST` | auto-detected | Override if Ollama isn't at `localhost:11434` |
| `OLLAMA_MODEL` | `qwen2.5-coder:7b` | Model used by the Ollama tier |
| `DB_PATH` | `~/.context-bridge/checkpoints.db` | SQLite database location |
| `SERVER_PORT` | `7723` | Backend port |

To enable semantic search across projects, install the optional extra and set either API key:

```bash
pip install "claude-context-bridge[semantic]"
export VOYAGE_API_KEY=...   # or: export ANTHROPIC_API_KEY=...
```

When no key is present, a zero-vector placeholder keeps the schema intact and search returns an empty list — there is no crash or error mode.

## Why not just use CLAUDE.md?

CLAUDE.md is a static instruction set you write once and update manually. context-bridge is a stateful feedback loop that updates automatically after every task. CLAUDE.md updates when you remember to; context-bridge updates after every `Task` tool call. CLAUDE.md has no concept of stagnation — if the same blocked task appears in 10 sessions, CLAUDE.md will never know; context-bridge detects it at session 3 and forces decomposition. CLAUDE.md is scoped to one project; context-bridge builds a cross-project developer profile (preferred stack, velocity baseline, recurring blocker classes) that transfers to new projects automatically.

They compose. CLAUDE.md holds your conventions. context-bridge holds your state.

## Manual usage

The hooks are optional. The API works with any HTTP client — see [docs/manual-sync.md](docs/manual-sync.md) for project ID derivation, manual checkpointing, and structured event examples (ADR, failure, outcome).

## Contributing

```bash
git clone https://github.com/pushkalkumar/context-bridge
cd context-bridge
pip install -e ".[dev]"
pytest
```

97 tests cover every endpoint, planner tier, checkpoint type, and CLI command. The active modules are `server/hook.py` (lifecycle hooks, session state, git metadata collection) and `server/planner.py` (three-tier planner, blocker classification, structured output). See [docs/architecture.md](docs/architecture.md) for the full decision tree and checkpoint lifecycle. Issues and PRs welcome: [open issues](https://github.com/pushkalkumar/context-bridge/issues).

## License

MIT
