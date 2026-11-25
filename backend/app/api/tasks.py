"""Frontend-facing task query routes preserving Ayush's existing db_routes endpoints."""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.db_routes import TaskState
from app.database import get_db
from app.schemas.task import TaskListResponse
from app.services.task_service import list_user_tasks

router = APIRouter(prefix="/tasks", tags=["Tasks"])


@router.get("", response_model=TaskListResponse)
async def list_tasks_endpoint(
    user_id: uuid.UUID = Query(..., description="Authenticated owner user UUID"),
    profile_id: uuid.UUID | None = Query(default=None),
    status_filter: TaskState | None = Query(default=None, alias="status"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> TaskListResponse:
    """List paginated tasks owned by the authenticated user and optional profile."""
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
