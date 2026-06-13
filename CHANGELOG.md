# Changelog

All notable changes to context-bridge are recorded here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
