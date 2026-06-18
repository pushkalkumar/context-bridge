# context-bridge

[![CI](https://github.com/pushkalkumar/context-bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/pushkalkumar/context-bridge/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/claude-context-bridge?color=blue)](https://pypi.org/project/claude-context-bridge/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue)](https://github.com/pushkalkumar/context-bridge)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Works offline](https://img.shields.io/badge/works-offline-brightgreen)](#three-tier-planner)

**Claude doesn't know it's stuck. context-bridge does.**

context-bridge is a local backend for Claude Code that checkpoints every task, detects when Claude is looping on the same problem across sessions, and injects the full attempt history — including what failed and why — before Claude reads your first message.

Runs entirely on your machine. SQLite + FastAPI, no cloud, no telemetry.

---

## The problem

You've been working on the same blocked task for three sessions. Claude doesn't know it's stuck. You don't know the exact bottleneck. You're about to spend another hour on the wrong approach.

MEMORY.md and claude-mem solve session blindness — Claude knows what you were working on. That problem is solved. context-bridge solves the next one: **Claude knowing it's stuck, why it's stuck, and what to try differently.**

---

## Install

Python 3.11+ required.

```bash
pip install claude-context-bridge
context-bridge install    # wires hooks into ~/.claude/settings.json
context-bridge            # start the backend (separate terminal or background)
```

Or one-liner:

```bash
curl -fsSL https://raw.githubusercontent.com/pushkalkumar/context-bridge/main/install.sh | bash
```

Done. The hooks fire automatically on the next Claude Code session — no restart required. After a session completes its first task, context-bridge begins building history. From the second session onward, context is injected before Claude reads your first message.

---

## What you get

### Session start: full context before your first message

```
[context-bridge] Session context restored:
  Summary:  JWT auth ~60% done. /register works. /login is the blocker.
  Next:     Implement /login: verify bcrypt hash, sign HS256 token with
            SECRET_KEY from env, return {access_token, token_type: "bearer"}.
  Priority: SECRET_KEY must come from env — it was hardcoded in auth.py:34
  Hotspots: auth.py (5x), main.py (3x)

[context-bridge] Current repo state:
  Recent commits:
    a3f1c20 Add /register endpoint
    9b2e401 Add JWT token signing helper
  Uncommitted changes:
    auth.py | 12 ++++++------
```

### Stagnation gate: fires before Claude starts the stuck task again

When a task has appeared twice without completing, context-bridge fires a `PreToolUse` warning before the third attempt starts — before Claude writes a single line of code:

```
[context-bridge] ⚠ STAGNATION RISK — 'Implement /login endpoint' has appeared 2× in recent history.
  Last recorded blocker: error: cannot find module 'bcrypt'
  Pattern type: same files changing repeatedly without resolution
  Previous plan: Resolve the bcrypt import before adding more auth code.
  Decompose into the smallest completable subtask before starting. Run `context-bridge why` for root-cause analysis.
```

At three consecutive sessions, the planner switches to forced decomposition:

```
[context-bridge] Checkpoint saved. Next: The task 'Implement /login endpoint' has
  appeared 3 consecutive times without completing. Pick the smallest completable
  subtask and do only that one thing. Root cause: 'bcrypt import error'.
  confidence 40% · blocker: technical_debt · decompose

[context-bridge] ⚠ STAGNATION (3 sessions, 4.2h stuck)
  Blocker: bcrypt import error
  Action:  Break this into smaller tasks. Start with the failing import only.
```

### Attempt replay: every prior attempt, injected at session start

```
[context-bridge] ⚠ ATTEMPT HISTORY (3 sessions on this task):
  Attempt 1 (2026-06-10) (12.0m): auth.py, routes.py  blocker: ImportError: cannot import bcrypt
  Attempt 2 (2026-06-11) (8.0m):  auth.py             blocker: ModuleNotFoundError: bcrypt
  Attempt 3 (2026-06-13) (22.0m): auth.py, deps.py    blocker: bcrypt version mismatch
  Do NOT repeat a previous approach. Run `context-bridge replay` for full details.
```

### Blocker matcher: recognizes errors it has seen before

```
[context-bridge] ⚠ Recurring error: 'bcrypt version mismatch with passlib'
  Previously resolved → fix was: Pin bcrypt==4.0.1 in requirements.txt
  If it's back: verify the fix was committed/persisted.
```

### Velocity tracking: alerts when a task takes unusually long for you

```
  Next:     ⚠ VELOCITY ALERT: This task is taking 2.6x longer than your baseline on this branch.
            Last 10 tasks averaged 7m 0s. Current task has been open 18m 22s.
            Consider: Is this blocked? Is the scope larger than expected? Should it be decomposed?
```

---

## How it works

```
Claude Code task about to start
        │
        ▼
PreToolUse hook fires
  checks if incoming task matches a stagnant pattern in history
  stagnation_count ≥ 2 → injects cross-session warning before task starts
  includes blocker class, last recorded error, and previous plan
        │
        ▼
Task completes
        │
        ▼
PostToolUse hook fires
  captures git diff, files touched, task summary, error lines
  classifies checkpoint type (task / scratch / session)
  POST /sync ──────────────────> local backend  (port 7723)
                                   stagnation check (3× same task → forced decomposition)
                                   velocity check (2× baseline → alert prepended to next instruction)
                                   planner: Anthropic → Ollama → rule-based, in that order
                                   returns: next_instruction, confidence, blocker_class, alternatives
        │
Session ends ──> Stop hook ──> session-type checkpoint
        │
Next session starts
        │
        ▼
SessionStart hook fires
  known project: injects summary, next step, active constraint, hotspot files
                 injects current git state (recent commits + uncommitted diff)
                 injects attempt history when stagnant (stagnation_count ≥ 2)
                 warns on goal drift when ≥ 3 distinct goals in recent sessions
                 surfaces related past work from other projects (semantic similarity ≥ 0.75)
  new project:   injects cross-project developer profile (stack, velocity, known pitfalls)
        │
        ▼
Claude receives full context before reading your first message
```

Everything runs locally: SQLite in `~/.context-bridge/` and a FastAPI server on `127.0.0.1:7723`.

---

## Three-tier planner

| Tier | Requirement | What you get |
|------|-------------|--------------|
| **Anthropic** | `ANTHROPIC_API_KEY` | Context-aware replanning with confidence score, alternative approaches, structured blocker classification |
| **Ollama** | Ollama running locally | Same structured output, free, fully local inference |
| **Rule-based** | Nothing | Deterministic stagnation detection, blocker classification, decomposition flag — zero latency, works offline |

The rule-based tier is not a fallback placeholder. It covers four blocker classes (`technical_debt`, `dependency`, `unclear_spec`, `scope_creep`) and stagnation detection with no network call. If `ANTHROPIC_API_KEY` is set, you get context-aware replanning on top. If the API is unreachable, rule-based fires with `confidence: 0.3` to signal the fallback — you always get a usable response.

---

## vs alternatives

| | MEMORY.md | claude-mem | **context-bridge** |
|---|---|---|---|
| Restores context at session start | ✓ (manually) | ✓ (auto) | ✓ (auto) |
| Detects when Claude is stuck in a loop | ✗ | ✗ | ✓ |
| Injects attempt history before a repeat attempt | ✗ | ✗ | ✓ |
| Matches current errors to resolved historical ones | ✗ | ✗ | ✓ |
| Tracks task duration vs your personal baseline | ✗ | ✗ | ✓ |
| Detects goal drift across sessions | ✗ | ✗ | ✓ |
| Classifies blocker type (debt / spec / dependency) | ✗ | ✗ | ✓ |
| Works offline, no API key required | ✓ | ✗ | ✓ |
| Cross-project developer profile | ✗ | ✗ | ✓ |
| Updates automatically after every task | ✗ | ✓ | ✓ |

They compose. CLAUDE.md holds your conventions. context-bridge holds your state.

---

## Commands

```bash
context-bridge             # start the backend server
context-bridge install     # (re)install hooks and skill into ~/.claude/
context-bridge uninstall   # remove hooks and skill (database preserved)
context-bridge status      # backend health, planner tier, embedding status
context-bridge why         # stagnation diagnosis + velocity for the current project
context-bridge replay      # full attempt history for the current stagnant task
context-bridge list        # all projects with checkpoint counts and stagnation flags
context-bridge diff        # before/after of the two most recent task checkpoints
context-bridge export      # write CONTEXT_BRIDGE_SNAPSHOT.md (CLAUDE.md-compatible)
context-bridge forget      # delete all checkpoints for the current project (reset stagnation)
```

**`context-bridge why`**

```
  Project: my-api/main

  ⚠ STAGNATION DETECTED
  Task:    'Implement /login endpoint'
  Stuck:   3 sessions, 4.2h
  Blocker: bcrypt import error
  Action:  Break this into smaller tasks. Start with the failing import only.

  Velocity: 18m 22s current  |  7m 0s baseline  |  2.6×  ⚠ slower than baseline
```

**`context-bridge list`**

```
  my-api/main              12 checkpoints (9 task, 2 scratch, 1 session)   2h ago
  my-api/feature-auth       4 checkpoints (4 task, 0 scratch, 0 session)  14h ago   ⚠ stagnant (3x)
  data-pipeline/main        8 checkpoints (6 task, 1 scratch, 1 session)   3d ago
```

**`context-bridge diff`**

```
  FROM (2h ago):  Implement /register endpoint
  TO   (14m ago): Implement /login endpoint

  Planner confidence:    0.87 → 0.71  (↓)
  Velocity:              4m 12s → 18m 22s  (slower)
  Blocker class:         none → technical_debt
  Decomposition needed:  false → true
```

---

## Configuration

All variables can go in `~/.context-bridge/.env`.

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | — | Enables the Anthropic planner tier; also used as embedding fallback |
| `VOYAGE_API_KEY` | — | Preferred key for semantic embeddings (Voyage AI); tried before `ANTHROPIC_API_KEY` |
| `OLLAMA_HOST` | auto-detected | Override if Ollama isn't at `localhost:11434` |
| `OLLAMA_MODEL` | `qwen2.5-coder:7b` | Model used by the Ollama tier |
| `PLANNER_MODEL` | `claude-sonnet-4-6` | Anthropic model used for planning |
| `DB_PATH` | `~/.context-bridge/checkpoints.db` | SQLite database location |
| `SERVER_PORT` | `7723` | Backend port |

To enable semantic search across projects:

```bash
pip install "claude-context-bridge[semantic]"
export VOYAGE_API_KEY=...   # or ANTHROPIC_API_KEY
```

Without a key, search returns an empty list — no crash, no error mode.

---

## Manual API

The hooks are optional. The `/sync` and `/checkpoint` endpoints work with any HTTP client:

```bash
# Manual checkpoint
curl -s -X POST http://localhost:7723/sync \
  -H 'Content-Type: application/json' \
  -d '{"user_goal":"ship auth","current_task":"fix bcrypt","progress_summary":"pinned bcrypt==4.0.1"}'

# Record an architectural decision (ADR)
curl -s -X POST http://localhost:7723/checkpoint \
  -H 'Content-Type: application/json' \
  -d '{"user_goal":"ship auth","current_task":"Switch from bcrypt to argon2",
       "progress_summary":"ADR recorded","event_type":"adr",
       "event_data":{"decision":"argon2-cffi","reason":"bcrypt conflict on Python 3.12"}}'
```

See [docs/manual-sync.md](docs/manual-sync.md) for the full API reference, project ID derivation, and structured event types (`adr`, `failure`, `outcome`).

---

## Contributing

```bash
git clone https://github.com/pushkalkumar/context-bridge
cd context-bridge
pip install -e ".[dev]"
pytest
```

143 tests cover every endpoint, planner tier, checkpoint type, CLI command, and all v0.7.0 features. The active modules are `server/hook.py` (lifecycle hooks, session state, git metadata) and `server/planner.py` (three-tier planner, blocker classification). See [docs/architecture.md](docs/architecture.md) for the full decision tree.

Issues and PRs welcome: [open issues](https://github.com/pushkalkumar/context-bridge/issues).

---

## Why not just use CLAUDE.md?

CLAUDE.md is a static instruction set you write once and update manually. context-bridge is a stateful feedback loop that updates automatically after every task. CLAUDE.md updates when you remember to; context-bridge updates after every `Task` tool call. CLAUDE.md has no concept of stagnation — the same blocked task can appear in 10 sessions and CLAUDE.md will never know. context-bridge detects it at session 3 and forces decomposition. CLAUDE.md is scoped to one project; context-bridge builds a cross-project developer profile (preferred stack, velocity baseline, recurring blocker classes) that transfers to new projects automatically.

They compose. CLAUDE.md holds your conventions. context-bridge holds your state.

---

## License

MIT
