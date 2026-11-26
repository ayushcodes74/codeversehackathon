"""Pydantic schemas for the versioned WorkflowDefinition registry (PRD FR-010)."""

import uuid
from datetime import datetime
from typing import Any
from pydantic import BaseModel, Field, ConfigDict


class WorkflowStepDefinition(BaseModel):
    step_index: int = Field(..., ge=1)
    step_name: str = Field(..., min_length=1)
    step_name_hi: str | None = None
    description: str = ""
    expected_action: str = "FILL_FIELDS"
    target_path: str | None = None
    required_fields: list[str] = Field(default_factory=list)


class WorkflowDefinitionCreate(BaseModel):
    workflow_id: str = Field(..., min_length=2, max_length=100)
    version: str = Field(default="1.0.0", min_length=1, max_length=20)
    name: str = Field(..., min_length=2, max_length=255)
    description: str | None = None
    portal_id: uuid.UUID | None = None
    steps: list[WorkflowStepDefinition] = Field(default_factory=list)
    is_active: bool = True
    metadata_payload: dict[str, Any] = Field(default_factory=dict)


class WorkflowDefinitionUpdate(BaseModel):
    version: str | None = None
    name: str | None = None
    description: str | None = None
    portal_id: uuid.UUID | None = None
    steps: list[WorkflowStepDefinition] | None = None
    is_active: bool | None = None
    metadata_payload: dict[str, Any] | None = None


class WorkflowDefinitionResponse(BaseModel):
    id: uuid.UUID
    workflow_id: str
    version: str
    name: str
    description: str | None = None
    portal_id: uuid.UUID | None = None
    steps: list[dict[str, Any]]
    is_active: bool
    metadata_payload: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
