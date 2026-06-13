# Sprint Notes — v0.5.0 Engineering Sprint

Working memory for the sprint. Derived from reading source before writing any code.

## 1. Checkpoints table columns (as of v0.4.0 baseline)

```
id               INTEGER PRIMARY KEY AUTOINCREMENT
project_id       TEXT NOT NULL
timestamp        TEXT NOT NULL           -- ISO-8601 string
stagnation_count INTEGER NOT NULL DEFAULT 1
event_type       TEXT NOT NULL DEFAULT 'checkpoint'   -- EventType enum value
data             TEXT NOT NULL           -- full JSON blob of the CheckpointIn payload
                                         -- plus _planner_output from SyncResponse
```

## 2. PostToolUse payload shape at /sync

```json
{
  "project_id": "reponame/branch",
  "timestamp": "2026-06-12T14:32:00",
  "user_goal": "Build a REST API",
  "current_task": "Implement /login endpoint",
  "progress_summary": "FastAPI skeleton done",
  "current_state": {
    "files_modified": ["auth.py", "main.py"],
    "code_summary": "",
    "architecture_notes": "",
    "git_diff_stat": " auth.py | 15 +++++++++++++++\n 1 file changed, 15 insertions(+)",
    "git_log_recent": "abc1234 Add login route\n..."
  },
  "blockers": [],
  "next_intended_action": "(auto-checkpoint — awaiting planner)",
  "event_type": "checkpoint",
  "event_data": {}
}
```

## 3. Planner return shape (SyncResponse)

```python
SyncResponse(
    next_instruction: str,      # primary instruction for Claude
    context_summary: str,       # concise project state
    revised_plan: str,          # step-by-step plan
    priority_focus: str,        # single most important constraint
    source: Literal["anthropic", "ollama", "rule-based"],
    stagnation_count: int,
    stagnation_report: StagnationReport | None,
    # NEW in v0.5.0:
    confidence: float,
    alternatives: list[str],
    blocker_class: str | None,
    decomposition_suggested: bool,
)
```

## README Audit

Read: README.md, CHANGELOG.md, server/planner.py, server/hook.py, SPRINT_NOTES.md.

### Structural problems found

- **Undifferentiated headline.** "Persistent memory and replanning for Claude Code" matches every CLAUDE.md auto-updater. Velocity tracking and stagnation decomposition are the actual differentiators and are buried in a feature table.
- **Only one output block.** Session restore is shown; stagnation report format and velocity alert format are not shown anywhere. Three important user-visible outputs have zero representation.
- **PyPI namespace warning.** "(`pip install context-bridge` is an unrelated package — don't use it.)" appears in the install section. This is a first-impression document; that sentence signals you don't own the name.
- **"API key optional" badge.** Negative framing — implies the good path requires a key. Should lead with the offline capability.
- **Features table.** Feature tables are read by people who already want the product. The differentiators need to be in prose before the table.
- **CLAUDE.md section is defensive.** Bullet list of "CLAUDE.md can't do X" reads as insecure. Should be architectural: one is a static instruction set, the other is a stateful feedback loop.

### Output format discrepancies found (for Output Format Audit section below)

See `## Output Format Audit` below.

---

## Output Format Audit

### Session restore (hook.py:283–296) — MATCH
The README example is accurate. Real format:
```
[context-bridge] Session context restored:
  Summary:  {context_summary}
  Next:     {next_instruction}
  Priority: {priority_focus}
  Hotspots: {path} ({count}x), ...
  Recurring blocker: {text} ({count}x)
```

### Stagnation report (hook.py:393–400) — MISMATCH with brief
Brief suggested: `[context-bridge] Stagnation detected: /login 3× (4h 12m). Root cause: bcrypt import error recurring. Decomposing into 3 subtasks.`

Actual format from hook.py:
```
[context-bridge] Stagnation report: stuck since {stuck_since} ({elapsed_hours}h, {checkpoint_count} checkpoints). Blocker: {primary_blocker}. {recommendation}
```
The prefix is "Stagnation report:" not "Stagnation detected:". There is no "Decomposing into N subtasks" text — the recommendation field from the rule-based tier says "Pick the smallest completable subtask and do only that one thing." README will use the real format.

### Velocity alert (main.py:181–186) — MISMATCH with brief
Brief suggested: `[context-bridge] Velocity alert: current task 2.4× slower than your baseline (18 min vs 7 min avg). Confidence: 0.71. Consider: extract hash comparison to separate function.`

Actual format — this is **prepended to next_instruction**, not a separate print:
```
⚠ VELOCITY ALERT: This task is taking {ratio:.1f}x longer than your baseline on this branch.
  Last 10 tasks averaged {avg_min}m {avg_sec}s. Current task has been open {cur_min}m {cur_sec}s.
  Consider: Is this blocked? Is the scope larger than expected? Should it be decomposed?
```
Two differences: (1) confidence is NOT included in the alert text — it's a separate SyncResponse field; (2) the prefix is "⚠ VELOCITY ALERT:" not "[context-bridge] Velocity alert:"; (3) "Consider" is a generic prompt, not task-specific advice. README will use the real format.

### SyncResponse structured fields — MATCH
Fields confirmed in planner.py + models.py: `confidence` (float), `alternatives` (list[str]), `blocker_class` (str|None), `decomposition_suggested` (bool). All real. All stored in DB columns.

---

## 4. SessionStart hook injection format

Printed to stdout, Claude reads it as first message context:

```
[context-bridge] Session context restored:
  Summary:  Goal: ... | Task: ... | Progress: ...
  Next:     <next_instruction from last planner output>
  Priority: <priority_focus from last planner output>
  Hotspots: auth.py (5x), main.py (3x)
  Recurring blocker: Missing env var (2x)
```

For new projects (no history), injects developer profile:
```
[context-bridge] Developer profile active (built from prior projects):
  Preferred stack: fastapi, sqlite, typescript
  Known pitfall: Missing env var (occurred 3x)
  Avoid suggesting: Celery with Redis broker (previously abandoned)
```
