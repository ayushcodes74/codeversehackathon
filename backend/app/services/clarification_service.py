"""Ownership-enforced Safe-Pause clarification service routing through AgentGateway."""

import logging
import uuid

from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.db_routes import TaskState
from app.core.exceptions import AppError, ConflictError, NotFoundError
from app.models import FamilyProfile, Task
from app.schemas.clarification import (
    ClarificationAnswerRequest,
    ClarificationAnswerResponse,
)
from app.services.agent_gateway import AgentGateway, get_agent_gateway
from app.services.auth_context import resolve_authenticated_user_id
from app.services.sanitization import (
    sanitize_metadata_payload,
    sanitize_text_for_persistence,
)

logger = logging.getLogger("app.services.clarification")

_CLARIFICATION_AWAITING_STATES = frozenset(
    {
        TaskState.AWAITING_USER_INPUT,
        TaskState.PAUSED_FOR_AUTH_OR_CAPTCHA,
    }
)


async def submit_clarification_answer(
    db: AsyncSession,
    *,
    task_id: uuid.UUID,
    payload: ClarificationAnswerRequest,
    x_user_id: str | None,
    authorization: str | None,
    gateway: AgentGateway | None = None,
) -> ClarificationAnswerResponse:
    """Validate task ownership/state and forward sanitized clarification answer to AgentGateway."""
    caller_user_id = resolve_authenticated_user_id(
        x_user_id=x_user_id,
        authorization=authorization,
    )

    task = await db.get(Task, task_id)
    if task is None:
        raise NotFoundError("Task not found.")
    if task.user_id != caller_user_id:
        raise AppError(
            message="Forbidden: caller identity does not match task owner.",
            status_code=status.HTTP_403_FORBIDDEN,
            code="forbidden_task_access",
        )

    profile = await db.get(FamilyProfile, task.profile_id)
    if profile is None or profile.user_id != caller_user_id:
        raise AppError(
            message="Forbidden: task profile ownership check failed.",
            status_code=status.HTTP_403_FORBIDDEN,
            code="forbidden_profile_access",
        )

    try:
        current_state = TaskState(task.status)
    except ValueError as exc:
        raise ConflictError(
            message=(
                f"Task '{task.id}' has incompatible database status '{task.status}' "
                "which is not in canonical TaskState."
            )
        ) from exc

    if current_state not in _CLARIFICATION_AWAITING_STATES:
        raise ConflictError(
            f"Task '{task.id}' is in '{current_state.value}' state and is not awaiting clarification."
        )

    sanitized_payload = payload.model_copy(
        update={
            "answer_text": (
                sanitize_text_for_persistence(payload.answer_text)
                if payload.answer_text is not None
                else None
            ),
            "field_values": sanitize_metadata_payload(payload.field_values),
        }
    )

    logger.info(
        "Forwarding clarification answer task_id=%s clarification_id=%s state=%s",
        task.id,
        payload.clarification_id,
        current_state.value,
    )

    active_gateway = gateway or get_agent_gateway()
    return await active_gateway.submit_clarification(
        user_id=caller_user_id,
        task_id=task.id,
        current_state=current_state,
        sanitized_payload=sanitized_payload,
    )
