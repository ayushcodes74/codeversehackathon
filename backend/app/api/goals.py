"""Frontend-facing goal intake endpoint routed through AgentGateway."""

from typing import Optional

from fastapi import APIRouter, Depends, Header, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.goal import GoalSubmitRequest, GoalSubmitResponse
from app.services.goal_service import submit_goal

router = APIRouter(prefix="/goals", tags=["Goals"])


@router.post("", response_model=GoalSubmitResponse, status_code=status.HTTP_201_CREATED)
async def submit_goal_endpoint(
    payload: GoalSubmitRequest,
    db: AsyncSession = Depends(get_db),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> GoalSubmitResponse:
    """Validate caller/profile/session ownership and forward goal intake to AgentGateway."""
    return await submit_goal(
        db,
        payload=payload,
        x_user_id=x_user_id,
        authorization=authorization,
    )
