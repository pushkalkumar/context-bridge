from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CheckpointState(BaseModel):
    """Typed state snapshot attached to each checkpoint."""
    model_config = ConfigDict(extra="allow")  # preserve git_diff_stat etc from hook

    files_modified: list[str] = Field(default_factory=list)
    code_summary: str = ""
    architecture_notes: str = ""


class CheckpointIn(BaseModel):
    project_id: str = ""
    timestamp: str = ""
    user_goal: str
    current_task: str
    progress_summary: str
    current_state: CheckpointState = Field(default_factory=CheckpointState)
    blockers: list[str] = Field(default_factory=list)
    next_intended_action: str = ""


class CheckpointAck(BaseModel):
    project_id: str
    stagnation_count: int


class SyncResponse(BaseModel):
    next_instruction: str
    context_summary: str
    revised_plan: str
    priority_focus: str
    source: Literal["anthropic", "ollama", "rule-based"]
    stagnation_count: int = 0


class ProjectSummary(BaseModel):
    project_id: str
    checkpoint_count: int
    last_active: str
    stagnation_count: int = 0  # from the latest checkpoint


class ProjectStats(BaseModel):
    total_projects: int
    total_checkpoints: int
    stagnation_events: int  # checkpoints where stagnation_count >= 3


class ErrorResponse(BaseModel):
    error: str       # snake_case machine-readable code
    message: str     # human-readable explanation
    details: dict | None = None
