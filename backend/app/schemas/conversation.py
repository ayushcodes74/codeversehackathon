"""Request and response schemas for frontend-facing task conversation APIs."""

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.clarification import ClarificationPrompt
from app.schemas.common import (
    DocumentType,
    FieldMappingItem,
    PaginationMeta,
    normalize_language_code,
    normalize_non_empty_text,
)


class MessageRole(str, Enum):
    """Allowed conversation message roles aligned with ConversationMessage ORM model."""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class ConversationUiKind(str, Enum):
    """Structured UI card/payload kinds rendered by the frontend assistant panel."""

    TEXT = "text"
    CLARIFICATION = "clarification"
    FIELD_MAPPING_PREVIEW = "field_mapping_preview"
    SUBMIT_APPROVAL = "submit_approval"
    PORTAL_WARNING = "portal_warning"
    DOCUMENT_REQUEST = "document_request"
    STATUS_UPDATE = "status_update"


class ConversationUiAction(BaseModel):
    """Interactive action button descriptor attached to an assistant message."""

    action_id: str = Field(..., min_length=1, max_length=64)
    label: str = Field(..., min_length=1, max_length=120)
    action_type: Literal[
        "submit_clarification",
        "approve_submit",
        "upload_document",
        "open_portal",
        "cancel_task",
        "custom",
    ] = "custom"
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("action_id", "label", mode="after")
    @classmethod
    def _strip_required_fields(cls, value: str, info) -> str:
        return normalize_non_empty_text(value, info.field_name)


class ConversationUiPayload(BaseModel):
    """Structured UI payload persisted on assistant ConversationMessage entries."""

    kind: ConversationUiKind = ConversationUiKind.TEXT
    title: str | None = Field(default=None, max_length=200)
    clarification: ClarificationPrompt | None = None
    field_mappings: list[FieldMappingItem] = Field(default_factory=list)
    actions: list[ConversationUiAction] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConversationMessageCreateRequest(BaseModel):
    """Frontend payload posting a user turn into a task's conversation."""

    content: str = Field(..., min_length=1, max_length=4000)
    language: str = "en"
    role: Literal["user"] = "user"
    client_message_id: str | None = Field(default=None, max_length=64)
    referenced_doc_types: list[DocumentType] = Field(default_factory=list)

    @field_validator("content", mode="after")
    @classmethod
    def _validate_content(cls, value: str) -> str:
        return normalize_non_empty_text(value, "content")

    @field_validator("language", mode="after")
    @classmethod
    def _validate_language(cls, value: str) -> str:
        return normalize_language_code(value)

    @field_validator("client_message_id", mode="after")
    @classmethod
    def _strip_client_message_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class ConversationMessageResponse(BaseModel):
    """Serialized conversation turn aligned with ConversationMessage ORM model."""

    id: uuid.UUID
    task_id: uuid.UUID
    role: MessageRole
    language: str
    content: str
    ui_payload: ConversationUiPayload | dict[str, Any] | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ConversationHistoryResponse(BaseModel):
    """Paginated conversation history for a task."""

    task_id: uuid.UUID
    messages: list[ConversationMessageResponse] = Field(default_factory=list)
    pagination: PaginationMeta
