"""Frontend-facing Safe-Pause clarification endpoint routed through AgentGateway."""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.clarification import (
    ClarificationAnswerRequest,
    ClarificationAnswerResponse,
)
from app.services.clarification_service import submit_clarification_answer

router = APIRouter(prefix="/tasks", tags=["Clarifications"])


@router.post("/{task_id}/clarifications", response_model=ClarificationAnswerResponse)
async def submit_clarification_endpoint(
    task_id: uuid.UUID,
    payload: ClarificationAnswerRequest,
    db: AsyncSession = Depends(get_db),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> ClarificationAnswerResponse:
    """Validate task ownership/state and forward clarification response to AgentGateway."""
    return await submit_clarification_answer(
        db,
        task_id=task_id,
        payload=payload,
        x_user_id=x_user_id,
        authorization=authorization,
    )
