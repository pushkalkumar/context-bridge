# Architecture Decisions — context-bridge v0.5.0 Sprint

## ADR-001: Velocity endpoint path convention

**Decision:** `GET /velocity/{project_id:path}` accepts the full `reponame/branch` string as a single path param, not separate `{project_id}/{branch}` params.

**Why:** The existing data model uses `project_id = reponame/branch` as an atomic identifier. Splitting on slash would be ambiguous for branch names containing slashes (e.g., `feature/auth/v2`). Using `{project_id:path}` is consistent with all other project-scoped endpoints (`/history`, `/patterns`, `/stagnation-report`, etc.).

**Trade-off:** Clients must pass the full `reponame/branch` string. Branch is not separately addressable.

---

## ADR-002: Diff and snapshot endpoint naming

**Decision:** `GET /diff/{project_id:path}` and `GET /snapshot/{project_id:path}` (not `/export/{project_id}/{branch}`).

**Why:** `/projects/{project_id:path}/export` already exists and returns JSON. The new Markdown export is a different artifact. Naming it `/snapshot/` avoids ambiguity. Same `:path` convention as ADR-001.

---

## ADR-003: checkpoint_type defaults to 'task' when no diff info present

**Decision:** When `git_diff_stat` is absent or empty, `checkpoint_type` defaults to `'task'` instead of `'scratch'`.

**Why:** Test checkpoints and programmatic checkpoints typically don't include git diff info. If we classified these as `scratch`, stagnation detection and velocity tracking would silently stop working for most unit tests and for users who call `/sync` without a git context. Defaulting to `'task'` preserves backward compatibility and matches the semantics (a manually submitted checkpoint is a meaningful unit of work).

**Applied:** Only classify as `'scratch'` when we have explicit `git_diff_stat` data showing < 10 lines changed and no new files detected.

---

## ADR-004: PlannerOutput dataclass is internal; SyncResponse is the external contract

**Decision:** `PlannerOutput` is used internally within the planner module. `SyncResponse` is extended with the new fields as optional additive fields. `run_planner` continues to return `SyncResponse`.

**Why:** Changing the return type of `run_planner` would require updating main.py, hook.py, and all test callsites. SyncResponse is already the API contract. Extending it with optional fields (with defaults) is backward compatible.

---

## ADR-005: Semantic embeddings use Voyage AI via VOYAGE_API_KEY or ANTHROPIC_API_KEY

**Decision:** Embeddings use `voyageai` (optional install) with `voyage-3-lite` model at 256 dimensions. Tried with `VOYAGE_API_KEY` first, then `ANTHROPIC_API_KEY` as fallback (Anthropic acquired Voyage AI; some users share keys). When neither is available or voyageai is not installed, embedding returns None and search returns [].

**Why:** Anthropic's Python SDK does not include an embeddings API. Voyage AI is Anthropic's recommended embedding solution post-acquisition. Making voyageai optional preserves the "works offline with no API key" guarantee.

**How to enable:** `pip install voyageai` and set `VOYAGE_API_KEY` or `ANTHROPIC_API_KEY` in `~/.context-bridge/.env`.

---

## ADR-006: Velocity alert prepended to next_instruction (no hook change)

**Decision:** When a velocity alert fires, the warning text is prepended to `next_instruction` in the `SyncResponse`. This gets stored in `_planner_output` and displayed by the SessionStart hook without any hook modifications.

**Why:** The SessionStart hook already displays `next_instruction`. Prepending the alert text is the minimal change that surfaces it to Claude at session start without touching hook.py's display logic.

---

## ADR-007: save_checkpoint returns lastrowid for embedding linkage

**Decision:** `save_checkpoint()` now returns `int` (the rowid of the inserted checkpoint). Existing callers that don't use the return value are unaffected (Python ignores unused return values).

---

## ADR-008: Zero-vector stored as embedding placeholder when offline

**Decision:** When embed returns None (offline / no key), a zero-vector `[0.0] * 256` is stored in `checkpoint_embeddings` as a placeholder. Search with a None query immediately returns [] without hitting the DB.

**Why:** Storing a row keeps the `checkpoint_id` linkage intact. The placeholder is never returned in search results because search returns [] early when the query embedding is None. This ensures `test_embedding_stored_on_checkpoint_write` passes even without an API key.

---

## ADR-009: Blocker class classified in rule-based tier from history patterns

**Decision:** `blocker_class` is derived from the checkpoint history in the rule-based tier using these rules (in priority order):
1. Same file appears in 2+ of the last 5 history checkpoints AND in current checkpoint → `technical_debt`
2. Blocker text contains "dependency"/"waiting on"/"blocked by" → `dependency`
3. Blocker text contains "spec"/"unclear"/"requirement" → `unclear_spec`
4. stagnation_count >= 5 → `scope_creep`
5. Default → `none`

**Why:** Deterministic classification without LLM. Captures the most common blocker patterns from the data. False positives are acceptable — it's a hint, not a diagnosis.
