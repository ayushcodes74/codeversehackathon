"""Request and response schemas for frontend-facing goal intake and planning previews."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.db_routes import TaskState
from app.schemas.clarification import ClarificationPrompt
from app.schemas.common import (
    DocumentType,
    ProfileScope,
    normalize_language_code,
    normalize_non_empty_text,
    validate_http_url,
)
from app.schemas.session import PortalSupportTier


class GoalSubmitRequest(BaseModel):
    """Frontend request submitting a user goal to initiate a guided task."""

    goal_text: str = Field(..., min_length=3, max_length=2000)
    session_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    profile_id: uuid.UUID | None = None
    profile_scope: ProfileScope = ProfileScope.SELF
    language: str = "en"
    current_url: str | None = Field(default=None, max_length=2048)
    available_doc_types: list[DocumentType] = Field(default_factory=list)
    available_field_keys: list[str] = Field(default_factory=list, max_length=200)

    @field_validator("goal_text", mode="after")
    @classmethod
    def _validate_goal_text(cls, value: str) -> str:
        cleaned = normalize_non_empty_text(value, "goal_text")
        if len(cleaned) < 3:
            raise ValueError("goal_text must be at least 3 characters long.")
        return cleaned

    @field_validator("language", mode="after")
    @classmethod
    def _validate_language(cls, value: str) -> str:
        return normalize_language_code(value)

    @field_validator("current_url", mode="after")
    @classmethod
    def _validate_current_url(cls, value: str | None) -> str | None:
        return validate_http_url(value, "current_url")

    @field_validator("available_field_keys", mode="after")
    @classmethod
    def _normalize_field_keys(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        normalized: list[str] = []
        for item in value:
            cleaned = normalize_non_empty_text(item, "available_field_keys item")
            if cleaned not in seen:
                seen.add(cleaned)
                normalized.append(cleaned)
        return normalized


class GoalPortalRecommendation(BaseModel):
    """Official portal metadata matched to a submitted user goal."""

    portal_id: uuid.UUID | None = None
    portal_name: str = Field(..., min_length=1, max_length=255)
    domain: str = Field(..., min_length=1, max_length=255)
    entry_url: str = Field(..., min_length=1, max_length=2048)
    category: str | None = Field(default=None, max_length=100)
    support_tier: PortalSupportTier = PortalSupportTier.VERIFIED_OFFICIAL
    required_doc_types: list[DocumentType] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class GoalStepPreview(BaseModel):
    """High-level step summary shown to the user after goal intake."""

    step_index: int = Field(..., ge=0)
    title: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    requires_user_approval: bool = False


class GoalSubmitResponse(BaseModel):
    """Backend response returned immediately after accepting a user goal."""

    task_id: uuid.UUID
    session_id: uuid.UUID | None = None
    status: TaskState
    goal_text: str
    language: str = "en"
    detected_intent: str | None = None
    recommended_portal: GoalPortalRecommendation | None = None
    steps_preview: list[GoalStepPreview] = Field(default_factory=list)
    clarification_needed: bool = False
    clarification: ClarificationPrompt | None = None
    created_at: datetime
