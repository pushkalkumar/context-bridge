# Context Bridge

Persists Claude Code state across sessions via a local checkpoint backend (port 7723).

## If hooks are installed (`context-bridge install`)

Nothing to do manually. Two hooks run automatically:

- **SessionStart** — injects last checkpoint's `context_summary`, `next_instruction`, and `priority_focus` before your first message.
- **PostToolUse (Task)** — auto-checkpoints with `git diff --stat HEAD` after every Task completion; POSTs to `/sync`; polls for priority changes every 5 tool calls.

## If hooks are not installed (manual fallback)

POST a checkpoint before starting any task:

```bash
curl -s -X POST http://localhost:7723/sync \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "<reponame/branch or dirname>",
    "user_goal": "<overarching goal>",
    "current_task": "<specific task starting now>",
    "progress_summary": "<what has been done>",
    "current_state": {
      "files_modified": [],
      "code_summary": "",
      "architecture_notes": ""
    },
    "blockers": [],
    "next_intended_action": "<what you plan to do next>"
  }'
```

## Response contract

| Field | Meaning |
|-------|---------|
| `next_instruction` | **Follow this.** It overrides your original plan if they differ. |
| `priority_focus` | **Never violate this.** Hard constraint for the session. |
| `context_summary` | Read this to orient yourself. |
| `source` | Which planner ran: `anthropic`, `ollama`, or `rule-based`. |

- If `next_instruction` is empty: warn the user the backend may be unhealthy. Do not continue silently.
- If `/sync` is unreachable: tell the user → `context-bridge` to start the server.
