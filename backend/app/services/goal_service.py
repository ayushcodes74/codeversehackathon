"""Ownership-enforced goal intake service routing through AgentGateway."""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.models import User
from app.schemas.goal import GoalSubmitRequest, GoalSubmitResponse
from app.schemas.session import SessionStatus
from app.services.agent_gateway import AgentGateway, get_agent_gateway
from app.services.auth_context import resolve_authenticated_user_id
from app.services.sanitization import sanitize_text_for_persistence
from app.services.session_service import (
    SessionStore,
    _get_owned_session_record,
    _resolve_owned_profile,
    get_session_store,
    resolve_portal_context,
)

logger = logging.getLogger("app.services.goal")


async def submit_goal(
    db: AsyncSession,
    *,
    payload: GoalSubmitRequest,
    x_user_id: str | None,
    authorization: str | None,
    session_store: SessionStore | None = None,
    gateway: AgentGateway | None = None,
) -> GoalSubmitResponse:
    """Validate ownership/portal context and forward sanitized goal intake to AgentGateway."""
    caller_user_id = resolve_authenticated_user_id(
        x_user_id=x_user_id,
        authorization=authorization,
        expected_user_id=payload.user_id,
    )

    user = await db.get(User, caller_user_id)
    if user is None:
        raise NotFoundError("Authenticated user record not found.")

    profile = await _resolve_owned_profile(db, caller_user_id, payload.profile_id)

    if payload.session_id is not None:
        active_store = session_store or get_session_store()
        session = _get_owned_session_record(
            active_store,
            session_id=payload.session_id,
            caller_user_id=caller_user_id,
        )
        if session.status in (SessionStatus.CLOSED, SessionStatus.EXPIRED):
            raise ConflictError(
                f"Cannot submit goal using session in '{session.status.value}' state."
            )

    portal_context = (
        await resolve_portal_context(db, payload.current_url)
        if payload.current_url
        else None
    )
    sanitized_goal = sanitize_text_for_persistence(payload.goal_text)

    logger.info(
        "Forwarding goal intake user_id=%s profile_id=%s has_portal=%s",
        caller_user_id,
        profile.id,
        portal_context is not None,
    )

    active_gateway = gateway or get_agent_gateway()
    return await active_gateway.submit_goal(
        db=db,
        user_id=caller_user_id,
        profile_id=profile.id,
        sanitized_goal_text=sanitized_goal,
        payload=payload,
        portal_context=portal_context,
    )
