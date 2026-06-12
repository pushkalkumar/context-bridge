# Manual sync

Use this when the hooks are not installed or when you want to record a checkpoint manually.

## Derive the project ID

```bash
project_id="$(git remote get-url origin 2>/dev/null | sed 's/.*\///; s/\.git$//' 2>/dev/null)/$(git branch --show-current 2>/dev/null || echo main)"
```

## POST a checkpoint

```bash
curl -s -X POST http://localhost:7723/sync \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "my-app/main",
    "user_goal": "<overarching goal>",
    "current_task": "<specific task>",
    "progress_summary": "<what was done>",
    "current_state": {
      "files_modified": [],
      "code_summary": "",
      "architecture_notes": ""
    },
    "blockers": [],
    "next_intended_action": "<next step>"
  }'
```

## Record an ADR

```bash
curl -s -X POST http://localhost:7723/checkpoint \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "my-app/main",
    "user_goal": "...",
    "current_task": "Architecture decision: auth strategy",
    "progress_summary": "Decided on approach",
    "event_type": "adr",
    "event_data": {
      "decision": "Use JWT with HS256",
      "reason": "Stateless",
      "tradeoff": "No server-side revocation"
    }
  }'
```

## Record a failure

```bash
curl -s -X POST http://localhost:7723/checkpoint \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "my-app/main",
    "user_goal": "...",
    "current_task": "Abandoning Celery for task queue",
    "progress_summary": "Tried Celery, reverted to a simple thread",
    "event_type": "failure",
    "event_data": {
      "attempted": "Celery with Redis broker",
      "failed_because": "Redis was unavailable and added ops complexity"
    }
  }'
```
