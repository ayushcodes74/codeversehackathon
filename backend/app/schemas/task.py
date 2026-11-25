"""Request and response schemas for frontend-facing task status and control APIs."""

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.api.db_routes import TaskState
from app.schemas.clarification import ClarificationPrompt
from app.schemas.common import FieldMappingItem, PaginationMeta


class TaskControlAction(str, Enum):
    """User-initiated lifecycle control actions on an active task."""

    PAUSE = "pause"
    RESUME = "resume"
    CANCEL = "cancel"
    APPROVE_SUBMIT = "approve_submit"
    REJECT_SUBMIT = "reject_submit"


class TaskActionRequest(BaseModel):
    """Frontend request to pause, resume, cancel, or approve final submission."""

    action: TaskControlAction
    reason: str | None = Field(default=None, max_length=1000)
    reviewed_fields_count: int | None = Field(default=None, ge=0)
    action_hash: str | None = Field(default=None, min_length=8, max_length=128)

    @field_validator("reason", "action_hash", mode="after")
    @classmethod
    def _strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @model_validator(mode="after")
    def _require_action_hash_for_approval(self) -> "TaskActionRequest":
        if (
            self.action
            in (TaskControlAction.APPROVE_SUBMIT, TaskControlAction.REJECT_SUBMIT)
            and not self.action_hash
        ):
            raise ValueError(
                "action_hash is required for approve_submit and reject_submit actions (FR-027)."
            )
        return self


class TaskSummaryResponse(BaseModel):
    """Compact task representation aligned with the Task ORM model."""

    id: uuid.UUID
    user_id: uuid.UUID
    profile_id: uuid.UUID
    portal_id: uuid.UUID | None = None
    goal_text: str
    target_url: str | None = None
    status: TaskState
    current_step_index: int = Field(default=0, ge=0)
    total_steps: int = Field(default=1, ge=1)
    application_ref_no: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TaskDetailResponse(TaskSummaryResponse):
    """Detailed task state including Safe-Pause checkpoint and pending clarification."""

    checkpoint_state: dict[str, Any] = Field(default_factory=dict)
    pending_action_hash: str | None = None
    pending_clarification: ClarificationPrompt | None = None
    pending_field_mappings: list[FieldMappingItem] = Field(default_factory=list)


class TaskListResponse(BaseModel):
    """Paginated list of user tasks for frontend history/resume views."""

    tasks: list[TaskSummaryResponse] = Field(default_factory=list)
    pagination: PaginationMeta


class TaskActionResponse(BaseModel):
    """Response confirming a task lifecycle, Safe-Pause approval, or manual-required outcome."""

    task_id: uuid.UUID
    action: TaskControlAction
    previous_status: TaskState
    status: TaskState
    executed: bool = True
    manual_action_required: bool = False
    action_hash: str | None = None
    message: str
    updated_at: datetime
