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
    event_data: dict = Field(default_factory=dict)
    # New in v0.5.0 — optional, server computes if absent
    checkpoint_type: str | None = None      # 'scratch' | 'task' | 'session'
    completed_at_ts: int | None = None      # unix milliseconds


class CheckpointAck(BaseModel):
    project_id: str
    stagnation_count: int


class StagnationReport(BaseModel):
    stuck_since: str
    elapsed_hours: float
    primary_blocker: str
    recommendation: str
    checkpoint_count: int


class SyncResponse(BaseModel):
    next_instruction: str
    context_summary: str
    revised_plan: str
    priority_focus: str
    source: Literal["anthropic", "ollama", "rule-based"]
    stagnation_count: int = 0
    stagnation_report: StagnationReport | None = None
    # Structured planner output — v0.5.0
    confidence: float = 1.0
    alternatives: list[str] = Field(default_factory=list)
    blocker_class: str | None = None
    decomposition_suggested: bool = False


class VelocityReport(BaseModel):
    avg_duration_ms: float | None
    current_duration_ms: float | None
    velocity_ratio: float | None
    alert: bool
    alert_reason: str


class RecurringItem(BaseModel):
    text: str
    count: int


class FileHotspot(BaseModel):
    path: str
    count: int


class PatternsReport(BaseModel):
    project_id: str
    hotspot_files: list[FileHotspot]
    recurring_blockers: list[RecurringItem]
    recurring_tasks: list[RecurringItem]


class RejectedApproach(BaseModel):
    attempted: str
    failed_because: str
    project_id: str


class DeveloperProfile(BaseModel):
    # Original fields — kept for backward compatibility
    project_count: int
    checkpoint_count: int
    top_file_types: list[RecurringItem]
    common_blockers: list[RecurringItem]
    tech_patterns: list[RecurringItem]
    rejected_approaches: list[RejectedApproach]
    # New computed fields — v0.5.0
    avg_task_velocity_ms: float | None = None
    preferred_stack: list[str] = Field(default_factory=list)
    recurring_blocker_classes: list[RecurringItem] = Field(default_factory=list)
    total_task_checkpoints: int = 0
    total_projects: int = 0


class ProjectSummary(BaseModel):
    project_id: str
    checkpoint_count: int
    last_active: str
    stagnation_count: int = 0
    type_breakdown: dict[str, int] = Field(default_factory=dict)


class ProjectStats(BaseModel):
    total_projects: int
    total_checkpoints: int
    stagnation_events: int


# ── Search (Task 4) ────────────────────────────────────────────────────────────

class SearchRequest(BaseModel):
    query: str
    limit: int = 5
    exclude_project_id: str | None = None


class SearchResult(BaseModel):
    project_id: str
    branch: str
    task_summary: str
    checkpoint_type: str
    similarity: float
    completed_at_ts: int | None
    planner_next_instruction: str


class SearchResponse(BaseModel):
    results: list[SearchResult]


# ── Diff (Task 5) ─────────────────────────────────────────────────────────────

class CheckpointDiff(BaseModel):
    task_summary: str
    completed_at_ts: int | None
    planner_confidence: float | None
    planner_blocker_class: str | None
    planner_decomposition_suggested: bool
    task_duration_ms: int | None


class DiffResponse(BaseModel):
    from_checkpoint: CheckpointDiff = Field(alias="from")
    to_checkpoint: CheckpointDiff = Field(alias="to")
    next_instruction: str
    priority_focus: list[str]

    model_config = ConfigDict(populate_by_name=True)


# ── Shared error envelope ─────────────────────────────────────────────────────

class ErrorResponse(BaseModel):
    error: str
    message: str
    details: dict | None = None
