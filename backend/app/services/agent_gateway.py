"""AgentGateway isolating backend API services from the Agent Orchestrator."""

import json
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.orchestrator import execute_agent_turn
from app.api.db_routes import ALLOWED_STATE_TRANSITIONS, TaskState
from app.database import AsyncSessionLocal
from app.schemas.agent_contracts import AgentChatRequest
from app.schemas.clarification import (
    ClarificationAnswerRequest,
    ClarificationAnswerResponse,
    ClarificationOption,
    ClarificationPrompt,
    ClarificationType,
)
from app.schemas.goal import (
    GoalPortalRecommendation,
    GoalStepPreview,
    GoalSubmitRequest,
    GoalSubmitResponse,
)
from app.schemas.session import PortalSupportTier, SessionPortalContext
from app.schemas.task import TaskControlAction


@dataclass(frozen=True)
class AgentTaskControlResult:
    """Outcome of asking the agent orchestrator to pause or resume a running task."""

    supported: bool
    executed: bool
    manual_action_required: bool
    next_state: TaskState | None
    message: str


class AgentGateway(ABC):
    """Contract between backend API services and the agent orchestrator."""

    @abstractmethod
    async def submit_goal(
        self,
        *,
        user_id: uuid.UUID,
        profile_id: uuid.UUID,
        sanitized_goal_text: str,
        payload: GoalSubmitRequest,
        portal_context: SessionPortalContext | None,
    ) -> GoalSubmitResponse:
        """Start goal planning and task creation via the agent orchestrator."""

    @abstractmethod
    async def submit_clarification(
        self,
        *,
        user_id: uuid.UUID,
        task_id: uuid.UUID,
        current_state: TaskState,
        sanitized_payload: ClarificationAnswerRequest,
    ) -> ClarificationAnswerResponse:
        """Deliver a sanitized Safe-Pause clarification response to the orchestrator."""

    @abstractmethod
    async def request_task_control(
        self,
        *,
        user_id: uuid.UUID,
        task_id: uuid.UUID,
        current_state: TaskState,
        action: TaskControlAction,
        reason: str | None = None,
    ) -> AgentTaskControlResult:
        """Request orchestrator-managed pause or resume for an active task."""


class OrchestratorAgentGateway(AgentGateway):
    """AgentGateway implementation wired directly to execute_agent_turn."""

    async def submit_goal(
        self,
        *,
        user_id: uuid.UUID,
        profile_id: uuid.UUID,
        sanitized_goal_text: str,
        payload: GoalSubmitRequest,
        portal_context: SessionPortalContext | None,
        db: AsyncSession | None = None,
    ) -> GoalSubmitResponse:
        chat_req = AgentChatRequest(
            user_id=user_id,
            profile_id=profile_id,
            message=sanitized_goal_text,
            language=payload.language,
            current_url=payload.current_url,
        )

        if db is not None:
            step = await execute_agent_turn(chat_req, db)
        else:
            async with AsyncSessionLocal() as session:
                step = await execute_agent_turn(chat_req, session)
                await session.commit()

        task_id = step.task_id or uuid.uuid4()
        try:
            resolved_state = TaskState(step.status)
        except ValueError:
            resolved_state = TaskState.DISCOVERING

        recommended_portal: GoalPortalRecommendation | None = None
        if portal_context and portal_context.is_official:
            recommended_portal = GoalPortalRecommendation(
                portal_name=portal_context.portal_name or portal_context.domain,
                domain=portal_context.domain,
                entry_url=payload.current_url or f"https://{portal_context.domain}",
                support_tier=portal_context.support_tier,
            )
        else:
            nav_action = next(
                (a for a in step.client_actions if a.action_type == "NAVIGATE" and a.value_or_vault_ref),
                None,
            )
            if nav_action and nav_action.value_or_vault_ref:
                entry_url = nav_action.value_or_vault_ref
                domain = (urlparse(entry_url).hostname or "").lower().removeprefix("www.") or "gov.in"
                recommended_portal = GoalPortalRecommendation(
                    portal_name=nav_action.description.replace("Navigating to ", "") or domain,
                    domain=domain,
                    entry_url=entry_url,
                    support_tier=PortalSupportTier.VERIFIED_OFFICIAL,
                )

        clarification_prompt: ClarificationPrompt | None = None
        if step.response_type == "clarification":
            tool_in = step.tool_input or {}
            raw_options = tool_in.get("options") or []
            options = [
                ClarificationOption(option_id=f"opt_{idx}", label=str(opt), value=str(opt))
                for idx, opt in enumerate(raw_options)
                if str(opt).strip()
            ]
            clarification_prompt = ClarificationPrompt(
                clarification_id=uuid.uuid4(),
                task_id=task_id,
                clarification_type=ClarificationType.MISSING_FIELD,
                question=step.message,
                field_key=tool_in.get("missing_field_key"),
                options=options,
            )

        return GoalSubmitResponse(
            task_id=task_id,
            session_id=payload.session_id,
            status=resolved_state,
            goal_text=sanitized_goal_text,
            language=payload.language,
            detected_intent=step.intent_lane,
            recommended_portal=recommended_portal,
            steps_preview=[
                GoalStepPreview(
                    step_index=step.current_step_index,
                    title=step.next_tool or "Goal Discovery",
                    description=step.message,
                    requires_user_approval=(step.response_type == "approval_request"),
                )
            ],
            clarification_needed=(step.response_type == "clarification"),
            clarification=clarification_prompt,
            created_at=datetime.now(timezone.utc),
        )

    async def submit_clarification(
        self,
        *,
        user_id: uuid.UUID,
        task_id: uuid.UUID,
        current_state: TaskState,
        sanitized_payload: ClarificationAnswerRequest,
        db: AsyncSession | None = None,
    ) -> ClarificationAnswerResponse:
        answer_msg = (
            sanitized_payload.answer_text
            or sanitized_payload.selected_option_id
            or (json.dumps(sanitized_payload.field_values) if sanitized_payload.field_values else "Approved")
        )
        chat_req = AgentChatRequest(
            user_id=user_id,
            task_id=task_id,
            message=answer_msg,
            language="en",
        )
        if db is not None:
            step = await execute_agent_turn(chat_req, db)
        else:
            async with AsyncSessionLocal() as session:
                step = await execute_agent_turn(chat_req, session)
                await session.commit()

        try:
            next_state = TaskState(step.status)
        except ValueError:
            next_state = current_state

        return ClarificationAnswerResponse(
            task_id=task_id,
            clarification_id=sanitized_payload.clarification_id,
            accepted=True,
            next_status=next_state,
            message=step.message,
        )

    async def request_task_control(
        self,
        *,
        user_id: uuid.UUID,
        task_id: uuid.UUID,
        current_state: TaskState,
        action: TaskControlAction,
        reason: str | None = None,
    ) -> AgentTaskControlResult:
        _ = (user_id, task_id, reason)
        allowed = ALLOWED_STATE_TRANSITIONS.get(current_state, frozenset())

        if action == TaskControlAction.PAUSE:
            if TaskState.AWAITING_USER_INPUT in allowed:
                return AgentTaskControlResult(
                    supported=True,
                    executed=True,
                    manual_action_required=False,
                    next_state=TaskState.AWAITING_USER_INPUT,
                    message="Task paused safely in awaiting_user_input state.",
                )
        elif action == TaskControlAction.RESUME:
            if TaskState.FILLING in allowed:
                return AgentTaskControlResult(
                    supported=True,
                    executed=True,
                    manual_action_required=False,
                    next_state=TaskState.FILLING,
                    message="Task resumed into filling state.",
                )

        return AgentTaskControlResult(
            supported=False,
            executed=False,
            manual_action_required=True,
            next_state=current_state,
            message=f"Action '{action.value}' is not permitted from '{current_state.value}'.",
        )


_default_agent_gateway = OrchestratorAgentGateway()


def get_agent_gateway() -> AgentGateway:
    """Return the active AgentGateway instance wired to execute_agent_turn."""
    return _default_agent_gateway
