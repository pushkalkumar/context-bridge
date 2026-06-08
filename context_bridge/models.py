from pydantic import BaseModel


class Checkpoint(BaseModel):
    project_id: str
    timestamp: str          # ISO 8601
    user_goal: str
    current_task: str
    progress_summary: str
    current_state: dict     # keys: files_modified (list), code_summary (str), architecture_notes (str)
    blockers: list[str]
    next_intended_action: str


class PlannerOutput(BaseModel):
    next_instruction: str
    context_summary: str
    revised_plan: str
    priority_focus: str
