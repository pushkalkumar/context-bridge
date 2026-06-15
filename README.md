# context-bridge

[![CI](https://github.com/pushkalkumar/context-bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/pushkalkumar/context-bridge/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue)](https://github.com/pushkalkumar/context-bridge)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Works offline](https://img.shields.io/badge/works-offline-brightgreen)](#how-it-works)
[![PyPI](https://img.shields.io/pypi/v/claude-context-bridge)](https://pypi.org/project/claude-context-bridge/)

Claude goes in circles. You've been working on the same blocked task for three sessions. Claude doesn't know it's stuck. You don't know the exact bottleneck. And you're about to spend another hour on the wrong approach.

MEMORY.md and claude-mem solve the session blindness problem — they tell Claude what you were doing. Neither one detects that you've been trying the same broken approach for 4.2 hours across three separate sessions, warns you before the fourth attempt starts, replays every prior attempt, or diagnoses why you keep failing.

context-bridge does.

It checkpoints every completed task automatically, detects stagnation the moment a task appears three sessions in a row, warns Claude before it repeats a stuck attempt, replays the full history of prior attempts at session start, matches current errors against resolved historical blockers, and tracks how long your tasks take against your own rolling baseline.

## What it looks like

When a task has been stuck for multiple sessions, context-bridge warns Claude before the next attempt fires:

```text
[context-bridge] ⚠ STAGNATION RISK — 'Implement /login endpoint' has appeared 2× in recent history.
  Last recorded blocker: error: cannot find module 'bcrypt'
  Pattern type: same files changing repeatedly without resolution
  Previous plan: Resolve the bcrypt import before adding more auth code.
  Decompose into the smallest completable subtask before starting. Run `context-bridge why` for root-cause analysis.
```

When stagnation is confirmed (three sessions, same task), the planner forces decomposition:

```text
[context-bridge] Checkpoint saved. Next: The task 'Implement /login endpoint' has
  appeared 3 consecutive times without completing. Pick the smallest completable
  subtask and do only that one thing. Root cause: 'bcrypt import error'.
  confidence 40% · blocker: technical_debt · decompose

[context-bridge] ⚠ STAGNATION (3 sessions, 4.2h stuck)
  Blocker: bcrypt import error
  Action:  Break this into smaller tasks. Start with the failing import only.
```

At session start, context is restored before Claude reads your first message — including what changed in git since the last session:

```text
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

When a task has been stuck across sessions, the attempt history is replayed at session start:

```text
[context-bridge] ⚠ ATTEMPT HISTORY (3 sessions on this task):
  Attempt 1 (2026-06-10) (12.0m): auth.py, routes.py  blocker: ImportError: cannot import bcrypt
  Attempt 2 (2026-06-11) (8.0m):  auth.py             blocker: ModuleNotFoundError: bcrypt
  Attempt 3 (2026-06-13) (22.0m): auth.py, deps.py    blocker: bcrypt version mismatch
  Do NOT repeat a previous approach. Run `context-bridge replay` for full details.
```

When the same error has appeared before, the blocker matcher fires:

```text
[context-bridge] ⚠ Recurring error: 'bcrypt version mismatch with passlib'
  Previously resolved → fix was: Pin bcrypt==4.0.1 in requirements.txt
  If it's back: verify the fix was committed/persisted.
```

When a task runs significantly longer than your usual pace, the velocity tracker alerts:

```text
  Next:     ⚠ VELOCITY ALERT: This task is taking 2.6x longer than your baseline on this branch.
            Last 10 tasks averaged 7m 0s. Current task has been open 18m 22s.
            Consider: Is this blocked? Is the scope larger than expected? Should it be decomposed?

            Implement /login: verify bcrypt hash, sign HS256 token.
```

## Install

Python 3.11+ required.

```bash
curl -fsSL https://raw.githubusercontent.com/pushkalkumar/context-bridge/main/install.sh | bash
```

Or manually:

```bash
pip install claude-context-bridge
context-bridge install    # wires SessionStart + PreToolUse + PostToolUse + Stop hooks
context-bridge            # start the backend server (separate terminal or background process)
```

## How it works

```text
Claude Code task about to start
        |
        v
PreToolUse hook fires
  checks if incoming task matches a stagnant pattern in history
  if stagnation_count >= 2 in recent history: injects cross-session warning
  includes blocker class, last recorded error, previous plan
        |
        v
Task completes
        |
        v
PostToolUse hook fires
  captures git diff, files touched, task summary, any error lines
  classifies checkpoint type (task / scratch / session)
  POST /sync ──────────────────────────────> local backend (port 7723)
                                              stagnation check (3× same task → forced decomposition)
                                              velocity check (2× baseline → alert prepended to instruction)
                                              planner: Anthropic → Ollama → rule-based, in that order
                                              returns: next_instruction, confidence, blocker_class, alternatives
        |
        v
Session ends ──> Stop hook ──> session-type checkpoint
        |
Next session starts
        |
        v
SessionStart hook fires
  known project: injects summary, next step, active constraint, recurring hotspots
                 injects current git state (recent commits, uncommitted diff)
                 injects attempt history when stagnant (stagnation_count >= 2)
                 warns on goal drift when >= 3 distinct goals in recent sessions
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

MEMORY.md (which Anthropic ships natively) and claude-mem solve session blindness — Claude knows what you were doing. That problem is solved. context-bridge solves the next problem: Claude knowing it's stuck, why it's stuck, and what to do differently.

The stagnation detector counts how many consecutive checkpoints contain the same normalized task. At two, it fires a PreToolUse warning before the third attempt starts — giving Claude the blocker class, the last recorded error, and the previous plan before it writes a single line of code. At session start, if stagnation_count >= 2, it injects the full attempt replay: every prior attempt's files, blockers, and the plan that was tried. The planner prompt also receives this replay with an explicit "do NOT repeat any of these approaches" instruction, forcing the LLM to reason about why prior attempts failed rather than trying the same thing again. At stagnation_count >= 3, it switches from "what's next" mode to decomposition mode. This is the piece that MEMORY.md cannot provide — it runs on every checkpoint and catches stagnation the moment it crosses the threshold.

Blocker history matching scans project checkpoint history after every `/sync` call. When the current blocker shares ≥50% keyword overlap with a historically recorded error, context-bridge identifies whether that error was resolved and what fix was used. If it was resolved but is back, the output warns that the fix didn't stick. If it was never resolved, it marks the blocker as persistent and surfaces the last plan that was tried. No other tool maintains this kind of error genealogy.

Goal drift detection scans the last 10 checkpoints for changes in `user_goal`. When three or more distinct goals appear in the window, context-bridge warns at session start and asks the user to confirm the current goal before work begins. This prevents Claude from working toward an outdated objective based on stale context.

Velocity tracking is a per-project, per-branch baseline computed from `task_duration_ms` stored with each checkpoint. When the current task has been open for 2× longer than your rolling average — computed from the last 10 task checkpoints — a structured alert is prepended to the planner's instruction before it reaches Claude. No other comparable tool tracks task duration at this granularity, which means no other tool can tell you "this task is unusually slow for you specifically, on this project."

The three-tier planner is a reliability guarantee, not a marketing feature. The rule-based tier covers stagnation detection, blocker classification (`technical_debt`, `dependency`, `unclear_spec`, `scope_creep`), and decomposition suggestion with no network call and no latency. If you set `ANTHROPIC_API_KEY`, the Anthropic tier adds context-aware replanning with a confidence score (0–1), alternative approaches, and a structured blocker class. If the Anthropic API is down or returns unparseable JSON, the rule-based tier fires with `confidence: 0.3` to signal the fallback — you always get a usable response.

## Commands

All commands read from the same SQLite database the hooks write to — no server state is lost between restarts.

```bash
context-bridge             # start the backend server
context-bridge install     # (re)install hooks and skill into ~/.claude/
context-bridge uninstall   # remove hooks and skill (database is preserved)
context-bridge why         # stagnation diagnosis and velocity report for the current project
context-bridge replay      # show full attempt history for the current project's stagnant task
context-bridge list        # all projects with checkpoint counts and type breakdown
context-bridge status      # backend health, planner tier, velocity and embedding status
context-bridge diff        # before/after of the last two task checkpoints
context-bridge export      # write CONTEXT_BRIDGE_SNAPSHOT.md (CLAUDE.md-compatible)
```

`context-bridge why`:

```text
  Project: my-api/main

  ⚠ STAGNATION DETECTED
  Task:    'Implement /login endpoint'
  Stuck:   3 sessions, 4.2h
  Blocker: bcrypt import error
  Action:  Break this into smaller tasks. Start with the failing import only.

  Velocity: 18m 22s current  |  7m 0s baseline  |  2.6×  ⚠ slower than baseline
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

141 tests cover every endpoint, planner tier, checkpoint type, CLI command, and all v0.7.0 features (attempt replay, blocker history, goal drift). The active modules are `server/hook.py` (lifecycle hooks, session state, git metadata collection) and `server/planner.py` (three-tier planner, blocker classification, structured output). See [docs/architecture.md](docs/architecture.md) for the full decision tree and checkpoint lifecycle. Issues and PRs welcome: [open issues](https://github.com/pushkalkumar/context-bridge/issues).

## License

MIT
