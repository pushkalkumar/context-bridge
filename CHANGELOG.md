# Changelog

All notable changes to context-bridge are recorded here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased]

### Added
- `GET /stats` endpoint: total projects, total checkpoints, stagnation event count
- `DELETE /projects/{project_id}` endpoint: wipe a project and all its checkpoints
- `GET /projects/{project_id}/export` endpoint: download full checkpoint history as JSON
- `GET /projects` now includes `stagnation_count` per project
- `context-bridge status` CLI subcommand: shows backend health, DB path, planner tier in use
- Stop lifecycle hook: saves an end-of-session checkpoint after the session closes
- 19 new API tests covering every endpoint (23 total, all passing)
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
- README API section documents all 8 endpoints including the new ones

### Fixed
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
