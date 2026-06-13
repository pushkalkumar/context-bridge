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
