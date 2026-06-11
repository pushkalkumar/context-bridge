from typing import Literal

from pydantic import BaseModel, Field


class CheckpointIn(BaseModel):
    project_id: str = ""
    timestamp: str = ""
    user_goal: str
    current_task: str
    progress_summary: str
    current_state: dict = Field(default_factory=dict)
    blockers: list[str] = Field(default_factory=list)
    next_intended_action: str = ""


class SyncResponse(BaseModel):
    next_instruction: str
    context_summary: str
    revised_plan: str
    priority_focus: str
    source: Literal["anthropic", "ollama", "rule-based"]
    stagnation_count: int = 0
