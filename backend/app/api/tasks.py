"""Frontend-facing task query and control routes preserving Ayush's db_routes."""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.task_state import TaskState
from app.database import get_db
from app.schemas.task import TaskActionRequest, TaskActionResponse, TaskListResponse
from app.services.task_service import apply_task_action, list_user_tasks

router = APIRouter(prefix="/tasks", tags=["Tasks"])


@router.get("", response_model=TaskListResponse)
async def list_tasks_endpoint(
    user_id: uuid.UUID | None = Query(
        default=None,
        description="Optional owner UUID; must match verified auth context if provided.",
    ),
    profile_id: uuid.UUID | None = Query(default=None),
    status_filter: TaskState | None = Query(default=None, alias="status"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> TaskListResponse:
    """List paginated tasks owned by the authenticated caller and optional profile."""
    return await list_user_tasks(
        db,
        user_id=user_id,
        x_user_id=x_user_id,
        authorization=authorization,
        profile_id=profile_id,
        status_filter=status_filter,
        limit=limit,
        offset=offset,
    )


@router.post("/{task_id}/actions", response_model=TaskActionResponse)
async def task_action_endpoint(
    task_id: uuid.UUID,
    payload: TaskActionRequest,
    db: AsyncSession = Depends(get_db),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> TaskActionResponse:
    """Execute cancel/action_hash-gated approval, or return manual-required for pause/resume."""
    return await apply_task_action(
        db,
        task_id=task_id,
        payload=payload,
        x_user_id=x_user_id,
        authorization=authorization,
    )
