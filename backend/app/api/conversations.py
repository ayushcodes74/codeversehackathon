"""Frontend-facing task conversation endpoints."""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Header, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.conversation import (
    ConversationHistoryResponse,
    ConversationMessageCreateRequest,
    ConversationMessageResponse,
)
from app.services.conversation_service import list_task_messages, post_user_message

router = APIRouter(prefix="/tasks", tags=["Conversations"])


@router.get("/{task_id}/messages", response_model=ConversationHistoryResponse)
async def get_task_messages_endpoint(
    task_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> ConversationHistoryResponse:
    """Fetch paginated, sanitized conversation history for an owned task."""
    return await list_task_messages(
        db,
        task_id=task_id,
        x_user_id=x_user_id,
        authorization=authorization,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/{task_id}/messages",
    response_model=ConversationMessageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_task_message_endpoint(
    task_id: uuid.UUID,
    payload: ConversationMessageCreateRequest,
    db: AsyncSession = Depends(get_db),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> ConversationMessageResponse:
    """Persist a sanitized user turn onto an active, caller-owned task."""
    return await post_user_message(
        db,
        task_id=task_id,
        payload=payload,
        x_user_id=x_user_id,
        authorization=authorization,
    )
