"""Ownership-enforced task query service reusing Ayush's ORM models and TaskState."""

import uuid

from fastapi import status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.db_routes import TaskState, verify_caller_ownership
from app.core.exceptions import AppError, NotFoundError
from app.models import FamilyProfile, Task
from app.schemas.common import PaginationMeta
from app.schemas.task import TaskListResponse, TaskSummaryResponse
from app.services.sanitization import sanitize_text_for_persistence


async def list_user_tasks(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    x_user_id: str | None,
    authorization: str | None,
    profile_id: uuid.UUID | None = None,
    status_filter: TaskState | None = None,
    limit: int = 20,
    offset: int = 0,
) -> TaskListResponse:
    """List paginated tasks belonging to the authenticated caller and optional profile."""
    verify_caller_ownership(str(user_id), x_user_id=x_user_id, authorization=authorization)

    if profile_id is not None:
        profile = await db.get(FamilyProfile, profile_id)
        if profile is None:
            raise NotFoundError("Family profile not found.")
        if profile.user_id != user_id:
            raise AppError(
                message="Forbidden: profile_id does not belong to this user.",
                status_code=status.HTTP_403_FORBIDDEN,
                code="forbidden_profile_access",
            )

    filters = [Task.user_id == user_id]
    if profile_id is not None:
        filters.append(Task.profile_id == profile_id)
    if status_filter is not None:
        filters.append(Task.status == status_filter.value)

    count_stmt = select(func.count()).select_from(Task).where(*filters)
    total = int((await db.execute(count_stmt)).scalar_one() or 0)

    list_stmt = (
        select(Task)
        .where(*filters)
        .order_by(Task.updated_at.desc(), Task.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    rows = (await db.execute(list_stmt)).scalars().all()

    summaries: list[TaskSummaryResponse] = []
    for task in rows:
        try:
            canonical_status = TaskState(task.status)
        except ValueError as exc:
            raise AppError(
                message=(
                    f"Task '{task.id}' has non-canonical status '{task.status}'. "
                    "Database status must align with TaskState."
                ),
                status_code=status.HTTP_409_CONFLICT,
                code="invalid_persisted_task_state",
            ) from exc

        summaries.append(
            TaskSummaryResponse(
                id=task.id,
                user_id=task.user_id,
                profile_id=task.profile_id,
                portal_id=task.portal_id,
                goal_text=sanitize_text_for_persistence(task.goal_text),
                target_url=task.target_url,
                status=canonical_status,
                current_step_index=task.current_step_index,
                total_steps=task.total_steps,
                application_ref_no=(
                    sanitize_text_for_persistence(task.application_ref_no)
                    if task.application_ref_no is not None
                    else None
                ),
                created_at=task.created_at,
                updated_at=task.updated_at,
            )
        )

    return TaskListResponse(
        tasks=summaries,
        pagination=PaginationMeta(
            total=total,
            limit=limit,
            offset=offset,
            has_more=(offset + len(summaries) < total),
        ),
    )
