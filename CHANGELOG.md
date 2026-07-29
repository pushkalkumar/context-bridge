# Changelog

All notable changes to context-bridge are recorded here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [0.8.0] - 2026-07-29

### Added
- **Backend auto-start** — the SessionStart hook now spawns the server detached (logs to `~/.context-bridge/server.log`) when `/health` fails and waits up to 6s for it to come up. Install is now two commands with nothing to keep running; the launchd prompt was removed from `install.sh` (existing launch agents are still cleaned up on uninstall). Opt out with `CONTEXT_BRIDGE_NO_AUTOSTART=1`. The `sync`/`event` CLI commands auto-start the backend too.
- **`context-bridge sync` CLI command** — checkpoints the current task and prints the plan. Derives the project ID, gathers git state, and formats the planner response (plan, priority, stagnation, confidence, blocker class, decomposition, alternatives, blocker match) for Claude to read. Flags: `--goal --task --progress --next --blocker` (repeatable).
- **`context-bridge event` CLI command** — records structured `failure` / `adr` / `outcome` events with per-kind flags (`--attempted/--because`, `--decision/--reason/--tradeoff`, `--result/--impact`). Goal and task default to the latest checkpoint's so events stay attached to their task history.

### Changed
- **Skill rewritten as a proper Agent Skill** — `skill/CLAUDE.md` replaced by `skill/SKILL.md` + `skill/references/api.md`. Installs to `~/.claude/skills/context-bridge/` and loads on demand instead of being `@`-imported into global CLAUDE.md, cutting the always-on context cost from ~2,500 tokens per session to one description line. `context-bridge install` migrates pre-0.8.0 installs automatically (removes the CLAUDE.md import line and `~/.claude/context-bridge.md`); `uninstall` cleans both layouts.
- **Skill is CLI-first** — the skill instructs Claude to run `context-bridge sync` / `context-bridge event` instead of hand-building curl JSON. Payload construction, project ID derivation, and the backend URL all moved out of the skill into the CLI; the raw HTTP API remains documented in `references/api.md` for integrations. Fixes two 0.7.x defects in one move: the skill never stated the backend address, and its sed-based project ID snippet produced IDs like `/main` in remote-less repos, silently splitting history.
- **Session start is no longer blocking** — the skill states restored context in one line and proceeds; it only asks for confirmation when a goal-drift or stagnation warning is present. Replaces the "Still accurate?" question on every project's first session.
- **Sync cadence defined** — `/sync` on goal changes, task completions, and blockers; `/checkpoint` for failure/adr/outcome events; never per-message (hooks cover subagent completions and session end).

### Fixed
- **Auto-checkpoint and the stagnation gate were dead on current Claude Code** — the hook matched tool names `Task`/`task`, but newer versions spawn subagents via the `Agent` tool. Both names are now recognized.
- **Structured event examples were invalid** — the documented `failure`/`adr`/`outcome` payloads omitted `user_goal`, `current_task`, and `progress_summary`, which `CheckpointIn` requires; posting them verbatim returned 422. `references/api.md` now shows complete valid payloads.
- **Install output omitted `/cb-forget`** — six slash commands are installed; only five were printed.

---

## [0.7.1] - 2026-06-18

### Fixed
- **`_word_re_cache` was a local variable** in `find_similar_blocker()` — the regex cache was recreated on every call, discarding all cached patterns. Moved to module-level `_WORD_RE_CACHE` with a helper `_get_word_re()`. Matching now amortizes compilation across calls.
- **`build_profile()` COUNT(*) with LIMIT silently ignored** — `SELECT COUNT(*) FROM checkpoints ORDER BY id DESC LIMIT 1000` is a scalar aggregate; SQLite ignores the `LIMIT` clause on it, returning a full-table count. Changed to `SELECT COUNT(*) FROM (SELECT 1 FROM checkpoints ORDER BY id DESC LIMIT 1000)` which correctly counts at most 1000 rows.
- **`SyncResponse.stagnation_count` defaulted to `0`** — stagnation counting starts at 1 (first appearance). The model field now correctly defaults to `1`, matching the semantics everywhere else in the codebase.
- **Wrong embedding model in `docs/architecture.md`** — referenced `text-embedding-3-small` (OpenAI) instead of `voyage-3-lite` (Voyage AI). Fixed.

### Changed
- **`context-bridge status` output** — reformatted with consistent indentation, clearer labels, backend-down guidance ("Start the backend: context-bridge"), and stagnant project names inline.
- **`--version` / `-V` flag** — `context-bridge --version` now prints the version string instead of showing the help and exiting non-zero.
- **README** — restructured for faster conversion: problem statement before install, comparison table vs MEMORY.md and claude-mem, simplified "How it works" diagram, new "Three-tier planner" table, cleaner terminal output examples throughout.
- **`docs/architecture.md`** — updated stagnation diagram and fixed embedding model reference.

---

## [0.7.0] - 2026-06-14

### Added
- **Cross-session attempt replay** — when `stagnation_count >= 2`, `GET /projects/{id}/replay` returns an ordered chronological list of every past attempt on the stagnant task: files modified, blockers hit, planner plan, and duration. Injected into the planner prompt with "do NOT repeat any of these approaches" so the LLM breaks stuck loops instead of looping. Also shown at SessionStart when stagnant, and available via `context-bridge replay` CLI.
- **Blocker history matching** — after every `/sync`, if the payload includes blockers, `find_similar_blocker()` scans project history for keyword overlap (≥50%) and returns whether a similar error was previously resolved and what fix was used. Surfaced as `⚠ Recurring error` / `⚠ Persistent error` in PostToolUse output so repeated errors are caught immediately.
- **Goal drift detection** — `GET /projects/{id}/goal-drift` detects when `user_goal` changes 3+ times in recent checkpoints (skipping placeholder goals) and returns `{drifted, goals, distinct_count}`. Warning injected at SessionStart when drifted, prompting the user to confirm the current goal before work begins.
- **Git-coherent session start** — SessionStart now injects current repo state (recent commits, uncommitted diff stat) alongside the restored context. Gives Claude accurate awareness of what changed since the last session without relying solely on checkpoint history.
- **`context-bridge replay` CLI command** — prints the attempt history for the current project's stagnant task in the terminal. Complements `context-bridge why` for manual diagnosis.
- **Three new API endpoints**: `GET /projects/{id}/replay` → `AttemptReplay`; `GET /projects/{id}/goal-drift` → `GoalDriftReport`; `GET /projects/{id}/blocker-history?q=...` → `BlockerMatch | null`.
- **New Pydantic models**: `AttemptEntry`, `AttemptReplay`, `BlockerMatch`, `GoalDriftReport`; `blocker_match` field added to `SyncResponse`.
- 29 new tests (141 total): `test_attempt_replay.py` (10), `test_blocker_history.py` (9), `test_goal_drift.py` (10).

### Changed
- SessionStart output now grouped into three distinct blocks: restored context, current repo state, and (when applicable) attempt history / goal drift warnings — so each category is scannable independently.
- `skill/CLAUDE.md` restructured: added attempt replay handling protocol (§9), goal drift handling (§10), blocker match response protocol (§11), failure/ADR/outcome event recording guidance, and `context-bridge replay` command reference.
- Planner prompt now includes full attempt history when `stagnation_count >= 2`, forcing the LLM to reason about failed approaches rather than repeating them.
- Version bumped to `0.7.0` in `pyproject.toml` and `app.version`.
- PyPI description updated to reflect the full v0.7.0 feature set.

### Upgrade notes
No schema migrations required — all new features read from existing checkpoint data. Re-run `context-bridge install` to pick up the updated hook binary (SessionStart changes require no hook re-install, but it's good practice).

---

## [0.6.0] - 2026-06-14

### Added
- **PreToolUse hook** — fires before every `Task` tool call and checks if the incoming task matches a stagnant pattern in the project's checkpoint history. When `stagnation_count >= 2` on the most recent matching checkpoint, Claude receives a structured warning (blocker class, last recorded error, previous plan) before the task starts — catching stuck loops before they accumulate rather than after. Uses a 1-second timeout so a downed backend adds no meaningful latency. The hook is wired automatically by `context-bridge install`.
- **`context-bridge why` command** — prints a stagnation diagnosis + velocity report for the current project in a single compact view. Shows task name, session count, elapsed time, recorded blocker, recommended action, and current velocity vs. baseline. Intended as the first command to run when Claude appears stuck.
- **Planner intelligence surfaced in PostToolUse output** — when the planner returns `confidence < 0.75`, a non-`none` `blocker_class`, or `decomposition_suggested: true`, these signals are now printed inline after the checkpoint confirmation line. First `alternatives` entry is also shown when present.
- **Stagnation report reformatted as structured block** — the stagnation report in PostToolUse output is now multi-line with separate `Blocker:` and `Action:` fields rather than a single concatenated line, making it scannable at a glance.
- 15 new tests (112 total): `tests/test_pre_tool_use.py` (10 tests covering no-op paths, warning conditions, blocker label mapping, normalization, and dispatch); `tests/test_why_command.py` (5 tests covering no stagnation, approaching stagnation, full stagnation with velocity, backend down, and insufficient history).

### Changed
- `context-bridge install` now wires four hooks: `SessionStart`, `PreToolUse`, `PostToolUse`, `Stop`.
- `context-bridge status` now shows current stagnation health (which projects are stuck right now) instead of a historical event count.
- `context-bridge install` output labels the PreToolUse hook with its purpose: `(cross-session stagnation warning)`.
- `skill/CLAUDE.md` extended with response contract fields for `confidence`, `blocker_class`, `decomposition_suggested`, and `alternatives`; PreToolUse warning handling; velocity awareness protocol; and blocker class action table.
- `_get()` in `hook.py` now accepts a `timeout` parameter (default 5s). PreToolUse handler uses 1s to avoid blocking tool use when the backend is unreachable.
- FastAPI `app.version` updated to `0.6.0`.

### Upgrade notes
Existing installations need to re-run `context-bridge install` to wire the PreToolUse hook into `~/.claude/settings.json`.

---

## [0.5.0] - 2026-06-13

### Added
- **Velocity tracking** — every task checkpoint records `task_duration_ms`; `GET /velocity/{project_id}` returns the per-project average with a 2× alert when the current task is running long (requires 5+ baseline checkpoints). Alerts are prepended to `next_instruction` so the skill receives them without hook changes.
- **Checkpoint type hierarchy** — checkpoints are classified as `task` (permanent, counted in stagnation/velocity), `scratch` (ephemeral micro-edits, automatically purged after 24 h), or `session` (end-of-session snapshot). Stagnation counting and velocity baselines exclude scratch checkpoints. A background `asyncio` loop purges stale scratch every 6 h. `GET /projects` now includes a `type_breakdown` field.
- **Structured planner output** — planner tiers now return a `PlannerOutput` dataclass with `confidence` (0–1), `alternatives` (list of strings), `blocker_class` (rule-classified), and `decomposition_suggested` (flag). These fields propagate through `SyncResponse` and are stored in new SQLite columns. `confidence` drops to 0.3 when an LLM tier returns unparseable JSON (rule-based fallback kicks in).
- **Semantic search with sqlite-vec** — `POST /search` performs KNN embedding search over `task`/`session` checkpoints. `SessionStart` hook injects a "RELATED PAST WORK" block for results with similarity ≥ 0.75. Embeddings use Voyage AI (`voyageai` package, `VOYAGE_API_KEY` or `ANTHROPIC_API_KEY`); when offline, a zero-vector placeholder is stored and search returns an empty list gracefully. `pip install "claude-context-bridge[semantic]"` pulls in `voyageai`.
- **`context-bridge diff` command** — `GET /diff/{project_id}` returns the two most recent task-type checkpoints with task summaries, durations, confidence, and changed files. The CLI displays a before/after table with a faster/slower direction indicator. Returns 404 with `{"error":"insufficient_history"}` when fewer than 2 task checkpoints exist.
- **Computed developer profile** — `GET /profile` now returns `avg_task_velocity_ms`, `preferred_stack` (inferred from file extensions across all checkpoints), `recurring_blocker_classes` (aggregated `planner_blocker_class` counts), and `total_task_checkpoints`. `SessionStart` profile injection uses the new fields.
- **`context-bridge export` command** — `GET /snapshot/{project_id}` returns a CLAUDE.md-compatible Markdown document covering current state, velocity, recurring patterns, ADR events, and file hotspots. The CLI writes it to `CONTEXT_BRIDGE_SNAPSHOT.md` by default.
- 55 new tests (97 total, all passing): `test_velocity.py`, `test_checkpoint_types.py`, `test_planner_structured.py`, `test_search.py`, `test_diff.py`, `test_profile_computed.py`, `test_export.py`. A shared `conftest.py` provides `isolated_db` and `client` fixtures that use a real in-memory schema.

### Changed
- `save_checkpoint()` now returns `int` (the inserted row ID) so the caller can immediately store embeddings for the new checkpoint.
- `compute_stagnation_count()` excludes `scratch` checkpoints from consecutive-task streak counting.
- Schema migration is additive: seven new `ALTER TABLE … ADD COLUMN IF NOT EXISTS` statements; existing databases are upgraded automatically on server start.

### Fixed
- `GET /diff/{project_id}` returns a typed `{"error": "insufficient_history"}` detail instead of a generic 404 message, so CLI output is actionable rather than opaque.

---

## [0.4.0] - 2026-06-12

### Changed
- **PyPI distribution renamed to `claude-context-bridge`** — the `context-bridge`
  name on PyPI belongs to an unrelated package. The CLI command, repo name, and
  import paths are unchanged
- Removed the orphaned `context_bridge/` duplicate package and `server/skill.md`;
  the canonical skill is `skill/CLAUDE.md`, shipped in the wheel as package data
- Skill rewritten with activation frontmatter, an explicit /sync-vs-/checkpoint
  decision tree, once-per-project confirmation, and no curl content (moved to
  `docs/manual-sync.md`)
- README restructured to lead with the restored-context output; added the
  "Why not just use CLAUDE.md?" section
- Rule-based stagnation wording: "has appeared N consecutive times" instead of
  "you have submitted N times"
- installer: `--upgrade` and `--uninstall` flags, wired-hook summary output,
  optional macOS launchd agent, server-running detection, prompts read from
  /dev/tty so they work under `curl | bash`
- `context-bridge uninstall` subcommand removes hooks, the hook script, the
  skill file, and the CLAUDE.md import line

### Fixed
- `Stop` lifecycle hook was handled by the hook script but never registered by
  `context-bridge install` — end-of-session checkpoints now actually fire
- Export download filename contained raw slashes from `reponame/branch` project IDs
- Stagnation-report `elapsed_hours` overwrote the offset of timezone-aware
  client timestamps instead of respecting it
- Hook session-state files no longer collide between parallel sessions
  (full session ID instead of a 20-char prefix)
- SessionStart warns on stderr when the backend is down instead of failing silently
- Stop-hook snapshots include the changed-file list, not just the tool-call count
- Planner prompt caps history at the 10 most recent checkpoints
- `_parse` accepts bare ``` fences, not just ```json

---

## [0.3.0] - 2026-06-11

### Added
- Structured event types on checkpoints: `event_type` (`checkpoint`, `adr`, `failure`,
  `pattern`, `outcome`) and `event_data` payload, stored in a new SQLite column with
  automatic migration of pre-0.3 databases
- `GET /projects/{project_id}/stagnation-report`: root-cause analysis of the stuck
  task — stuck since when, elapsed hours, dominant blocker, recommendation
- `/sync` runs the stagnation analysis at `stagnation_count >= 3` and returns it as
  `stagnation_report` on the response; all three planner tiers consume it
- `GET /projects/{project_id}/patterns`: file hotspots (3+ checkpoints), recurring
  blockers (2+), recurring unresolved tasks (3+)
- `GET /profile`: cross-project developer profile — top file types, common blockers,
  tech patterns from ADR notes, rejected approaches from failure events
- SessionStart hook appends pattern signals to the restored-context injection, and
  injects the developer profile when a project has no history yet
- Skill rewritten as an executable protocol: session-start handoff, conflict
  resolution against `priority_focus`, mandatory stagnation pause, planner-source
  behavior (`rule-based` is binding, LLM tiers may be challenged), deterministic
  project ID derivation
- 14 new tests (37 total, all passing)
- `GET /stats` endpoint: total projects, total checkpoints, stagnation event count
- `DELETE /projects/{project_id}` endpoint: wipe a project and all its checkpoints
- `GET /projects/{project_id}/export` endpoint: download full checkpoint history as JSON
- `GET /projects` now includes `stagnation_count` per project
- `context-bridge status` CLI subcommand: shows backend health, DB path, planner tier in use
- Stop lifecycle hook: saves an end-of-session checkpoint after the session closes
- Export and delete buttons in the web dashboard
- Source badge on planner output (anthropic / ollama / rule-based) in dashboard cards
- Server-side stagnation_count used throughout dashboard (no client-side string comparison)

### Changed
- `SyncResponse` includes `source` field (`"anthropic"`, `"ollama"`, or `"rule-based"`)
- `CheckpointAck` returned from `POST /checkpoint` includes `stagnation_count`
- Dashboard auto-refreshes every 15 seconds instead of relying on manual reload
- `stagnation_count` stored in SQLite column, not derived at read time
- `GET /history` returns 404 with typed `ErrorResponse` envelope when project not found
- Hook `_on_stop` cleans up session state files after writing the checkpoint
- README API section documents all endpoints including the new ones

### Fixed
- Path routes (`/history/{id}`, `/projects/{id}/...`) now accept project IDs
  containing slashes (`reponame/branch`) — previously these returned 404, which
  silently broke SessionStart context restoration for every real project
- Stagnation count off-by-one: added `id DESC` tiebreaker to `ORDER BY timestamp DESC`
  so simultaneous timestamps don't return rows in non-deterministic order
- Dashboard `stagnation_count` was previously computed client-side by string comparison;
  now reads the field directly from the server response

---

## [0.2.0] - 2026-06-08

### Added
- `server/` package: `config.py`, `memory.py`, `planner.py`, `models.py`, `main.py`, `hook.py`
- pydantic-settings config with env file support at `~/.context-bridge/.env`
- Three-tier planner: Anthropic (claude-sonnet-4-6) -> Ollama (httpx, 60s timeout) -> rule-based
- `resolved_ollama_host()` auto-detects Ollama at localhost:11434 without requiring `OLLAMA_HOST`
- SQLite WAL mode (`PRAGMA journal_mode=WAL`) for concurrent read/write access
- Stagnation detection with `difflib.SequenceMatcher` similarity >= 0.85
- `POST /checkpoint` (store only) separate from `POST /sync` (store + plan)
- `run()` entry point wired to `context-bridge` CLI via `pyproject.toml`
- `context-bridge install` wires SessionStart and PostToolUse hooks into `~/.claude/settings.json`
- Web dashboard at `/`: project list, checkpoint timeline, planner output
- CI matrix: Python 3.11, 3.12, 3.13

### Changed
- Port changed from 8000 to 7723 to avoid conflicts with common dev servers
- `current_state` field uses typed `CheckpointState` model with `extra="allow"` for git metadata pass-through

---

## [0.1.0] - 2026-05-20

### Added
- Initial release: checkpoint-based replanning system for Claude Code
- `POST /sync` endpoint accepting checkpoint JSON, returning `SyncResponse`
- `GET /history/{project_id}` endpoint
- Rule-based planner with stagnation and blocker detection
- SQLite persistence at `~/.context-bridge/checkpoints.db`
- SessionStart and PostToolUse hooks for automatic checkpointing
- `skill/CLAUDE.md` for Claude Code skill integration
