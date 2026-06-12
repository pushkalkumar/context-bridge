from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class EventType(str, Enum):
    CHECKPOINT = "checkpoint"          # default: timeline event
    ARCHITECTURE_DECISION = "adr"      # intentional design choices
    FAILURE = "failure"                # attempted approaches that were abandoned
    PATTERN = "pattern"                # recurring solution recognized
    OUTCOME = "outcome"                # measurable result of a change


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
    event_type: EventType = EventType.CHECKPOINT
    # adr: decision, reason, tradeoff | failure: attempted, failed_because
    # outcome: goal, change_made, result
    event_data: dict = Field(default_factory=dict)


class CheckpointAck(BaseModel):
    project_id: str
    stagnation_count: int


class StagnationReport(BaseModel):
    stuck_since: str          # timestamp of earliest checkpoint with the stuck task
    elapsed_hours: float
    primary_blocker: str      # "" when no blocker recorded
    recommendation: str
    checkpoint_count: int     # checkpoints matching the stuck task


class SyncResponse(BaseModel):
    next_instruction: str
    context_summary: str
    revised_plan: str
    priority_focus: str
    source: Literal["anthropic", "ollama", "rule-based"]
    stagnation_count: int = 0
    stagnation_report: StagnationReport | None = None


class RecurringItem(BaseModel):
    text: str
    count: int


class FileHotspot(BaseModel):
    path: str
    count: int


class PatternsReport(BaseModel):
    project_id: str
    hotspot_files: list[FileHotspot]        # modified across 3+ checkpoints
    recurring_blockers: list[RecurringItem]  # appeared 2+ times
    recurring_tasks: list[RecurringItem]     # recurred 3+ times without resolution


class RejectedApproach(BaseModel):
    attempted: str
    failed_because: str
    project_id: str


class DeveloperProfile(BaseModel):
    project_count: int
    checkpoint_count: int
    top_file_types: list[RecurringItem]      # extension frequency, e.g. ".py"
    common_blockers: list[RecurringItem]
    tech_patterns: list[RecurringItem]       # stack names from ADR architecture notes
    rejected_approaches: list[RejectedApproach]


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
