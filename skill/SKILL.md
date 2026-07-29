---
name: context-bridge
description: >
  Cross-session task memory and stagnation detection for Claude Code, backed by a
  local context-bridge server that auto-starts on session start. Use when hook
  output tagged [context-bridge] appears, when the user says "checkpoint", "sync
  context", or "remember this for next session", when a task keeps reappearing
  without completing, or when recording an architecture decision, an abandoned
  approach, or a shipped outcome.
---

# context-bridge

Persistent task memory across Claude Code sessions: checkpoints, stagnation
detection, attempt replay, and planning. The backend auto-starts when a session
begins and hooks checkpoint automatically. Everything you do goes through two
CLI commands — never hand-build HTTP calls (raw API, only if the CLI is
unavailable: [references/api.md](references/api.md)).

## Record progress: `context-bridge sync`

Run at these moments — not every message:

- The user states or changes the session goal.
- A task completes and the next step is known.
- A blocker is hit (pass the exact error via `--blocker`).

```bash
context-bridge sync \
  --goal "Ship JWT auth" \
  --task "Implement /login endpoint" \
  --progress "/register done; /login returns 500 on bcrypt verify" \
  --blocker "error: cannot find module 'bcrypt'" \
  --next "Fix bcrypt import, then sign HS256 token"
```

`--blocker` is repeatable. Project ID and git state are gathered automatically.

The output is the plan. Act on it:

- `Plan:` — execute it; on conflict with the user request, see Conflicts.
- `Priority:` — the active constraint for the session.
- `stagnation N` with N ≥ 3 — apply the stagnation protocol (below).
- `confidence` below 75% — surface the uncertainty before proceeding.
- `⚠ Blocker class:` — address that blocker type before retrying.
- `⚠ Decompose:` — split into subtasks of 30 minutes or less first.
- `rule-based` source — follow the plan exactly, do not improvise.

## Record events: `context-bridge event`

```bash
context-bridge event failure --attempted "session cookies" --because "stateless API requirement"
context-bridge event adr --decision "HS256 signing" --reason "single service" --tradeoff "rotation needs redeploy"
context-bridge event outcome --result "auth suite green" --impact "auth feature complete"
```

Record `failure` when abandoning an approach (feeds blocker matching), `adr` on
architecture decisions, `outcome` when a significant result lands. Goal and task
default to the latest checkpoint's — override with `--goal`/`--task` if the
event belongs elsewhere.

## Reacting to injected warnings

**Session context restored** at session start: state the summary, next step, and
priority in one short line, then proceed. Only ask for confirmation when a drift
or stagnation warning is also present.

**`⚠ STAGNATION RISK`** (before a task starts) or stagnation ≥ 3: do NOT start
the task as described. Decompose into subtasks of 30 minutes or less, name the
primary blocker, and confirm the first subtask with the user.

**`⚠ ATTEMPT HISTORY`**: read every attempt; do not repeat any listed
file-change pattern or plan. State what is different about the new approach. If
nothing is different, stop and ask the user for a new direction.

**`⚠ GOAL DRIFT`**: present the listed goals, ask which is current, then sync
with the confirmed `--goal`.

**`⚠ VELOCITY ALERT`**: if genuinely blocked, surface the blocker and ask;
otherwise continue but check in after each step.

**`⚠ Recurring error`** (previously resolved): re-apply the recorded fix and
verify it is committed. **`⚠ Persistent error`** (never resolved): try a
fundamentally different approach, not the previous plan.

## Conflicts

When the user request conflicts with `Priority:`, surface the conflict and offer
both options — never silently pick a side. If the user overrides, sync with the
override noted via `--blocker`.

## If a command fails

The backend auto-starts; a failing command prints its own diagnosis. Relay it to
the user and suggest `context-bridge status`. Never retry in a loop, and never
block work on an unreachable backend.

## Manual commands

`/cb-status` `/cb-why` `/cb-replay` `/cb-diff` `/cb-export` `/cb-forget`, or
`context-bridge <status|why|replay|list|diff|export|forget>` in the terminal.
