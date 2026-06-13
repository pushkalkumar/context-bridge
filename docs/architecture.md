# Architecture

## Three-tier planner

Every `/sync` request runs through a waterfall. Each tier is tried in order; the first one that succeeds returns a `SyncResponse`. If a tier encounters a network or auth error it returns `None` and the next tier is tried. If a tier responds but with unparseable JSON, the rule-based tier fires with `confidence: 0.3` to signal degraded output.

```text
POST /sync
    │
    ▼
Anthropic tier ── ANTHROPIC_API_KEY set? ──► No ──► skip
    │ Yes
    │ call claude-sonnet-4-6 with structured prompt
    │ parse JSON response
    ├── success ──────────────────────────────────► SyncResponse (source="anthropic", confidence=0.85–1.0)
    ├── JSON parse failure ───────────────────────► rule-based fallback (confidence=0.3)
    └── network/auth error ──────────────────────► try next tier
    │
    ▼
Ollama tier ── OLLAMA_HOST reachable? ──► No ──► skip
    │ Yes
    │ POST to /api/chat, 60s timeout
    ├── success ──────────────────────────────────► SyncResponse (source="ollama", confidence as returned)
    ├── JSON parse failure ───────────────────────► rule-based fallback (confidence=0.3)
    └── connection error ─────────────────────────► try next tier
    │
    ▼
Rule-based tier ── always runs, never fails
    │
    │ stagnation_count >= 3? ──► decomposition instruction + root-cause report
    │ recurring blocker?       ──► address blocker first
    │ current blocker?         ──► resolve then continue
    │ else                     ──► next_intended_action as-is
    │
    └────────────────────────────────────────────► SyncResponse (source="rule-based", confidence=0.85 or 0.4)
```

Every response carries: `next_instruction`, `context_summary`, `revised_plan`, `priority_focus`, `confidence` (float 0–1), `alternatives` (list), `blocker_class` (one of `technical_debt`, `dependency`, `unclear_spec`, `scope_creep`, `none`), `decomposition_suggested` (bool). These fields are stored in the checkpoint row and returned to the hook.

**Blocker classification** (rule-based, runs in all tiers for the stored column): if the same file appears in 2+ of the last 5 checkpoint diffs and in the current one → `technical_debt`; if blocker text contains "waiting on"/"blocked by" → `dependency`; if it contains "spec"/"unclear"/"ambiguous" → `unclear_spec`; if stagnation_count ≥ 5 → `scope_creep`; otherwise → `none`.

---

## Checkpoint type lifecycle

Every checkpoint written to the database is one of three types. The type is inferred automatically from the git diff and the hook that fires.

```text
PostToolUse hook fires after a Task tool call
    │
    ├── git_name_status contains "A\t" (new file added)?  ──► type = "task"
    ├── git diff line count > 10 lines changed?            ──► type = "task"
    ├── explicit hint passed (checkpoint_type field)?      ──► use hint directly
    ├── no diff info available?                            ──► type = "task" (safe default)
    └── small diff (≤ 10 lines, no new files)?            ──► type = "scratch"

Stop hook fires at session end
    └── always ──────────────────────────────────────────► type = "session"
```

Type determines what gets counted and what gets purged:

| Type | Stagnation | Velocity baseline | Semantic search | Purge |
|------|-----------|-------------------|-----------------|-------|
| `task` | counted | included | indexed | never |
| `scratch` | excluded | excluded | excluded | after 24 h |
| `session` | excluded | excluded | indexed | never |

The purge loop runs every 6 hours in the background (`asyncio.create_task`). Scratch checkpoints with `completed_at_ts` older than 24 hours are deleted. This keeps the stagnation streak and velocity baseline clean without requiring the user to categorize anything.

---

## Semantic search injection path

Embeddings are 256-dimensional float vectors stored in a `vec0` virtual table (sqlite-vec). They are written asynchronously after each checkpoint is saved.

```text
checkpoint saved → save_embedding(checkpoint_id, embed_text)
    │
    ├── embedding_api_key present AND voyageai installed?
    │   └── Yes → call Voyage AI text-embedding-3-small → store real vector
    └── No  → store zero vector as placeholder (schema stays intact, search disabled)

SessionStart hook
    │
    ├── GET /history/{project_id}?limit=1 → latest checkpoint
    ├── POST /search  { query: next_instruction[:500], limit: 3, exclude_project_id: current }
    │       │
    │       ├── sqlite-vec KNN: SELECT ... FROM checkpoint_embeddings WHERE embedding MATCH ? AND k = ?
    │       ├── filter: checkpoint_type IN ('task', 'session')
    │       └── returns results with cosine similarity score
    │
    └── filter results: similarity >= 0.75
        │
        ├── match found → inject "📎 RELATED PAST WORK" block into stdout
        └── no match or offline → inject nothing (no error, no warning)
```

The 0.75 similarity threshold (`_SEARCH_SIMILARITY_THRESHOLD` in `hook.py`) is the minimum cosine similarity for a result to be surfaced. Results below this threshold are silently discarded — the filter prevents spurious injections from loosely related work. When sqlite-vec is not installed or no API key is set, `search_checkpoints()` returns an empty list immediately.
