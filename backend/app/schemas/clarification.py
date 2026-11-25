"""Request and response schemas for frontend-facing Safe-Pause clarification APIs."""

import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator

from app.api.db_routes import TaskState
from app.schemas.common import FormFieldTarget, normalize_non_empty_text


class ClarificationType(str, Enum):
    """Supported Safe-Pause clarification and user-confirmation categories."""

    MISSING_FIELD = "missing_field"
    AMBIGUOUS_MAPPING = "ambiguous_mapping"
    PROFILE_SELECTION = "profile_selection"
    AUTH_OR_CAPTCHA = "auth_or_captcha"
    SUBMIT_APPROVAL = "submit_approval"
    GENERAL_QUESTION = "general_question"


class ClarificationOption(BaseModel):
    """Selectable choice presented to the user during clarification."""

    option_id: str = Field(..., min_length=1, max_length=64)
    label: str = Field(..., min_length=1, max_length=200)
    value: str = Field(..., min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=500)

    @field_validator("option_id", "label", "value", mode="after")
    @classmethod
    def _strip_required_fields(cls, value: str, info) -> str:
        return normalize_non_empty_text(value, info.field_name)


class ClarificationPrompt(BaseModel):
    """Structured prompt emitted when the workflow pauses for user clarification."""

    clarification_id: uuid.UUID
    task_id: uuid.UUID
    clarification_type: ClarificationType
    question: str = Field(..., min_length=1, max_length=2000)
    field_key: str | None = Field(default=None, max_length=120)
    target_field: FormFieldTarget | None = None
    options: list[ClarificationOption] = Field(default_factory=list)
    required: bool = True
    sensitive: bool = False
    expires_at: datetime | None = None

    @field_validator("question", mode="after")
    @classmethod
    def _validate_question(cls, value: str) -> str:
        return normalize_non_empty_text(value, "question")


class ClarificationAnswerRequest(BaseModel):
    """Frontend response resolving a pending Safe-Pause clarification prompt."""

    clarification_id: uuid.UUID
    answer_text: str | None = Field(default=None, max_length=2000)
    selected_option_id: str | None = Field(default=None, max_length=64)
    approved: bool | None = None
    field_values: dict[str, str] = Field(default_factory=dict)
    save_to_memory: bool = False

    @field_validator("answer_text", "selected_option_id", mode="after")
    @classmethod
    def _normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("field_values", mode="after")
    @classmethod
    def _validate_field_values(cls, value: dict[str, str]) -> dict[str, str]:
        cleaned_map: dict[str, str] = {}
        for key, val in value.items():
            clean_key = normalize_non_empty_text(key, "field_values key")
            clean_val = normalize_non_empty_text(val, f"field_values[{clean_key}]")
            cleaned_map[clean_key] = clean_val
        return cleaned_map

    @model_validator(mode="after")
    def _require_clarification_input(self) -> "ClarificationAnswerRequest":
        if (
            self.answer_text is None
            and self.selected_option_id is None
            and self.approved is None
            and not self.field_values
        ):
            raise ValueError(
                "Provide at least one of answer_text, selected_option_id, approved, or field_values."
            )
        return self


class ClarificationAnswerResponse(BaseModel):
    """Backend acknowledgement after processing a user's clarification response."""

    task_id: uuid.UUID
    clarification_id: uuid.UUID
    accepted: bool
    next_status: TaskState
    message: str
    next_clarification: ClarificationPrompt | None = None
