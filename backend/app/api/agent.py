"""Protected Anubhav-owned /agent/chat route resolving the extension route mismatch.

Ayush's ``backend/app/api/agent_routes.py`` remains unmounted because it exposes
unauthenticated visual-guide endpoints and omits profile ownership checks on chat turns.
This router exposes only ``POST /api/v1/agent/chat`` behind caller, user, profile,
and task ownership enforcement.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.agent_contracts import AgentChatRequest, AgentStepResponse
from app.services.agent_gateway import get_agent_gateway
from app.services.auth_context import resolve_authenticated_user_id

router = APIRouter(prefix="/agent", tags=["Agent Orchestrator"])


@router.post("/chat", response_model=AgentStepResponse)
async def agent_chat_endpoint(
    payload: AgentChatRequest,
    db: AsyncSession = Depends(get_db),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> AgentStepResponse:
    """Execute one authenticated, ownership-enforced extension/agent turn."""
    caller_user_id = resolve_authenticated_user_id(
        x_user_id=x_user_id,
        authorization=authorization,
        expected_user_id=payload.user_id,
    )
    gateway = get_agent_gateway()
    return await gateway.run_chat_turn(
        db=db,
        caller_user_id=caller_user_id,
        payload=payload,
    )
