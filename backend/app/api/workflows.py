"""FastAPI router for versioned WorkflowDefinition registry (PRD FR-010)."""

import uuid
from typing import Optional, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import WorkflowDefinition
from app.schemas.workflow import (
    WorkflowDefinitionCreate,
    WorkflowDefinitionResponse,
    WorkflowDefinitionUpdate,
)

router = APIRouter(prefix="/workflows", tags=["Workflow Registry"])


@router.post("", response_model=WorkflowDefinitionResponse, status_code=status.HTTP_201_CREATED)
async def create_workflow_definition(
    payload: WorkflowDefinitionCreate,
    db: AsyncSession = Depends(get_db),
) -> WorkflowDefinitionResponse:
    """Create a new versioned workflow definition or publish a new version."""
    stmt = select(WorkflowDefinition).where(
        WorkflowDefinition.workflow_id == payload.workflow_id,
        WorkflowDefinition.version == payload.version,
    )
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Workflow '{payload.workflow_id}' version '{payload.version}' already exists.",
        )

    workflow = WorkflowDefinition(
        workflow_id=payload.workflow_id,
        version=payload.version,
        name=payload.name,
        description=payload.description,
        portal_id=payload.portal_id,
        steps=[s.model_dump() for s in payload.steps],
        is_active=payload.is_active,
        metadata_payload=payload.metadata_payload,
    )
    db.add(workflow)
    await db.commit()
    await db.refresh(workflow)
    return WorkflowDefinitionResponse.model_validate(workflow)


@router.get("", response_model=list[WorkflowDefinitionResponse])
async def list_workflow_definitions(
    portal_id: Optional[uuid.UUID] = Query(default=None),
    active_only: bool = Query(default=True),
    db: AsyncSession = Depends(get_db),
) -> list[WorkflowDefinitionResponse]:
    """List workflow definitions with optional portal_id and active filters."""
    stmt = select(WorkflowDefinition)
    if active_only:
        stmt = stmt.where(WorkflowDefinition.is_active == True)  # noqa: E712
    if portal_id:
        stmt = stmt.where(WorkflowDefinition.portal_id == portal_id)
    stmt = stmt.order_by(WorkflowDefinition.workflow_id, desc(WorkflowDefinition.version))

    records = (await db.execute(stmt)).scalars().all()
    return [WorkflowDefinitionResponse.model_validate(r) for r in records]


@router.get("/{workflow_id}", response_model=WorkflowDefinitionResponse)
async def get_workflow_definition(
    workflow_id: str,
    version: Optional[str] = Query(default=None, description="Specific version; defaults to latest"),
    db: AsyncSession = Depends(get_db),
) -> WorkflowDefinitionResponse:
    """Lookup a workflow definition by workflow_id, returning latest version if unspecified."""
    stmt = select(WorkflowDefinition).where(WorkflowDefinition.workflow_id == workflow_id)
    if version:
        stmt = stmt.where(WorkflowDefinition.version == version)
    else:
        stmt = stmt.order_by(desc(WorkflowDefinition.version))

    record = (await db.execute(stmt)).scalars().first()
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow definition '{workflow_id}' not found.",
        )
    return WorkflowDefinitionResponse.model_validate(record)


@router.get("/{workflow_id}/versions", response_model=list[WorkflowDefinitionResponse])
async def list_workflow_versions(
    workflow_id: str,
    db: AsyncSession = Depends(get_db),
) -> list[WorkflowDefinitionResponse]:
    """Retrieve all versions of a given workflow_id ordered newest first."""
    stmt = (
        select(WorkflowDefinition)
        .where(WorkflowDefinition.workflow_id == workflow_id)
        .order_by(desc(WorkflowDefinition.version))
    )
    records = (await db.execute(stmt)).scalars().all()
    if not records:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No versions found for workflow '{workflow_id}'.",
        )
    return [WorkflowDefinitionResponse.model_validate(r) for r in records]


@router.put("/{workflow_id}/{version}", response_model=WorkflowDefinitionResponse)
async def update_workflow_definition(
    workflow_id: str,
    version: str,
    payload: WorkflowDefinitionUpdate,
    db: AsyncSession = Depends(get_db),
) -> WorkflowDefinitionResponse:
    """Update metadata, steps, or active state of a specific workflow definition version."""
    stmt = select(WorkflowDefinition).where(
        WorkflowDefinition.workflow_id == workflow_id,
        WorkflowDefinition.version == version,
    )
    workflow = (await db.execute(stmt)).scalar_one_or_none()
    if not workflow:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow '{workflow_id}' version '{version}' not found.",
        )

    if payload.name is not None:
        workflow.name = payload.name
    if payload.description is not None:
        workflow.description = payload.description
    if payload.portal_id is not None:
        workflow.portal_id = payload.portal_id
    if payload.steps is not None:
        workflow.steps = [s.model_dump() for s in payload.steps]
    if payload.is_active is not None:
        workflow.is_active = payload.is_active
    if payload.metadata_payload is not None:
        workflow.metadata_payload = payload.metadata_payload

    await db.commit()
    await db.refresh(workflow)
    return WorkflowDefinitionResponse.model_validate(workflow)


@router.delete("/{workflow_id}", status_code=status.HTTP_200_OK)
async def deactivate_workflow_definition(
    workflow_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Deactivate all versions or a specific version of a workflow definition."""
    try:
        wf_uuid = uuid.UUID(workflow_id)
        stmt = select(WorkflowDefinition).where(
            (WorkflowDefinition.workflow_id == workflow_id) | (WorkflowDefinition.id == wf_uuid)
        )
    except ValueError:
        stmt = select(WorkflowDefinition).where(WorkflowDefinition.workflow_id == workflow_id)

    records = (await db.execute(stmt)).scalars().all()
    if not records:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow '{workflow_id}' not found.",
        )
    for r in records:
        r.is_active = False
    await db.commit()
    return {"status": "deactivated", "workflow_id": workflow_id, "is_active": False, "versions_affected": len(records)}
