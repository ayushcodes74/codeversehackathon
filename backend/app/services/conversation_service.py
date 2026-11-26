"""Ownership-enforced conversation history and safe message persistence service."""

import logging
import uuid
from typing import Any

from fastapi import status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.task_state import TERMINAL_TASK_STATES, TaskState
from app.core.exceptions import AppError, ConflictError, NotFoundError
from app.models import ConversationMessage, FamilyProfile, Task
from app.schemas.common import PaginationMeta
from app.schemas.conversation import (
    ConversationHistoryResponse,
    ConversationMessageCreateRequest,
    ConversationMessageResponse,
    MessageRole,
)
from app.services.sanitization import (
    sanitize_metadata_payload,
    sanitize_text_for_persistence,
)
from app.services.auth_context import resolve_authenticated_user_id

logger = logging.getLogger("app.services.conversation")


async def _get_owned_task(
    db: AsyncSession,
    *,
    task_id: uuid.UUID,
    x_user_id: str | None,
    authorization: str | None,
) -> Task:
    task = await db.get(Task, task_id)
    if task is None:
        raise NotFoundError("Task not found.")

    resolve_authenticated_user_id(
        x_user_id=x_user_id,
        authorization=authorization,
        expected_user_id=task.user_id,
    )

    profile = await db.get(FamilyProfile, task.profile_id)
    if profile is None or profile.user_id != task.user_id:
        raise AppError(
            message="Forbidden: task profile ownership check failed.",
            status_code=status.HTTP_403_FORBIDDEN,
            code="forbidden_profile_access",
        )
    return task


async def list_task_messages(
    db: AsyncSession,
    *,
    task_id: uuid.UUID,
    x_user_id: str | None,
    authorization: str | None,
    limit: int = 50,
    offset: int = 0,
) -> ConversationHistoryResponse:
    """Return paginated, redacted conversation messages for an owned task."""
    task = await _get_owned_task(
        db,
        task_id=task_id,
        x_user_id=x_user_id,
        authorization=authorization,
    )

    count_stmt = (
        select(func.count())
        .select_from(ConversationMessage)
        .where(ConversationMessage.task_id == task.id)
    )
    total = int((await db.execute(count_stmt)).scalar_one() or 0)

    msg_stmt = (
        select(ConversationMessage)
        .where(ConversationMessage.task_id == task.id)
        .order_by(ConversationMessage.created_at.asc(), ConversationMessage.id.asc())
        .offset(offset)
        .limit(limit)
    )
    rows = (await db.execute(msg_stmt)).scalars().all()

    items = [
        ConversationMessageResponse(
            id=row.id,
            task_id=row.task_id,
            role=MessageRole(row.role),
            language=row.language,
            content=sanitize_text_for_persistence(row.content),
            ui_payload=(
                sanitize_metadata_payload(row.ui_payload)
                if row.ui_payload is not None
                else None
            ),
            created_at=row.created_at,
        )
        for row in rows
    ]

    return ConversationHistoryResponse(
        task_id=task.id,
        messages=items,
        pagination=PaginationMeta(
            total=total,
            limit=limit,
            offset=offset,
            has_more=(offset + len(items) < total),
        ),
    )


async def post_user_message(
    db: AsyncSession,
    *,
    task_id: uuid.UUID,
    payload: ConversationMessageCreateRequest,
    x_user_id: str | None,
    authorization: str | None,
) -> ConversationMessageResponse:
    """Persist a sanitized user message onto an active, caller-owned task."""
    task = await _get_owned_task(
        db,
        task_id=task_id,
        x_user_id=x_user_id,
        authorization=authorization,
    )

    try:
        current_state = TaskState(task.status)
    except ValueError:
        current_state = None

    if current_state in TERMINAL_TASK_STATES:
        raise ConflictError(
            f"Cannot post conversation messages to task in terminal state '{task.status}'."
        )

    safe_content = sanitize_text_for_persistence(payload.content)
    metadata: dict[str, Any] = {}
    if payload.client_message_id:
        metadata["client_message_id"] = payload.client_message_id
    if payload.referenced_doc_types:
        metadata["referenced_doc_types"] = [doc.value for doc in payload.referenced_doc_types]

    safe_ui_payload = (
        sanitize_metadata_payload({"kind": "text", "metadata": metadata})
        if metadata
        else None
    )

    message = ConversationMessage(
        task_id=task.id,
        role=MessageRole.USER.value,
        language=payload.language,
        content=safe_content,
        ui_payload=safe_ui_payload,
    )
    db.add(message)
    await db.flush()
    await db.refresh(message)

    logger.info(
        "Persisted user conversation message id=%s task_id=%s language=%s",
        message.id,
        task.id,
        message.language,
    )

    return ConversationMessageResponse.model_validate(message)
