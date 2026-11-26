"""Frontend-facing assistant session endpoints backed by process-local SessionStore."""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Header, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.session import (
    SessionCloseResponse,
    SessionCreateRequest,
    SessionResponse,
    SessionUpdateRequest,
)
from app.services.session_service import (
    close_session,
    create_session,
    get_session,
    update_session,
)

router = APIRouter(prefix="/sessions", tags=["Sessions"])


@router.post("", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session_endpoint(
    payload: SessionCreateRequest,
    db: AsyncSession = Depends(get_db),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> SessionResponse:
    """Create a process-local assistant session for the authenticated caller."""
    return await create_session(
        db,
        payload=payload,
        x_user_id=x_user_id,
        authorization=authorization,
    )


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session_endpoint(
    session_id: uuid.UUID,
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> SessionResponse:
    """Retrieve a process-local session owned by the authenticated caller."""
    return await get_session(
        session_id=session_id,
        x_user_id=x_user_id,
        authorization=authorization,
    )


@router.patch("/{session_id}", response_model=SessionResponse)
async def update_session_endpoint(
    session_id: uuid.UUID,
    payload: SessionUpdateRequest,
    db: AsyncSession = Depends(get_db),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> SessionResponse:
    """Update active profile scope, language, sanitized domain context, or vault flag."""
    return await update_session(
        db,
        session_id=session_id,
        payload=payload,
        x_user_id=x_user_id,
        authorization=authorization,
    )


@router.post("/{session_id}/close", response_model=SessionCloseResponse)
async def close_session_endpoint(
    session_id: uuid.UUID,
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> SessionCloseResponse:
    """Close an owned process-local session and clear ephemeral context."""
    return await close_session(
        session_id=session_id,
        x_user_id=x_user_id,
        authorization=authorization,
    )
