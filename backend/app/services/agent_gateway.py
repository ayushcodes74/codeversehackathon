"""Anubhav-owned AgentGateway interface isolating backend APIs from Ayush's agent orchestrator.

POC Assumption:
    Only Madhav's document tool registry (``app.documents.agent_tools.DOCUMENT_AGENT_TOOLS``)
    exists in the repository; Ayush's planning/orchestration loop is not yet implemented.
    ``UnconfiguredAgentGateway`` therefore raises ``ServiceUnavailableError`` (HTTP 503) for
    goal/clarification execution and reports ``manual_action_required=True`` for unsupported
    pause/resume controls, rather than simulating success or inventing task results.
"""

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.api.db_routes import TaskState
from app.core.exceptions import ServiceUnavailableError
from app.schemas.clarification import (
    ClarificationAnswerRequest,
    ClarificationAnswerResponse,
)
from app.schemas.goal import GoalSubmitRequest, GoalSubmitResponse
from app.schemas.session import SessionPortalContext
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
    """Contract between Anubhav-owned API services and Ayush's agent orchestrator."""

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


class UnconfiguredAgentGateway(AgentGateway):
    """Fail-closed gateway used when Ayush's agent orchestrator is not connected."""

    async def submit_goal(
        self,
        *,
        user_id: uuid.UUID,
        profile_id: uuid.UUID,
        sanitized_goal_text: str,
        payload: GoalSubmitRequest,
        portal_context: SessionPortalContext | None,
    ) -> GoalSubmitResponse:
        _ = (user_id, profile_id, sanitized_goal_text, payload, portal_context)
        raise ServiceUnavailableError(
            message=(
                "Agent planning/orchestration service is not configured. "
                "Goal intake requires Ayush's agent orchestrator implementation."
            ),
            details={"dependency": "agent_orchestrator", "flow": "goal_submit"},
        )

    async def submit_clarification(
        self,
        *,
        user_id: uuid.UUID,
        task_id: uuid.UUID,
        current_state: TaskState,
        sanitized_payload: ClarificationAnswerRequest,
    ) -> ClarificationAnswerResponse:
        _ = (user_id, task_id, current_state, sanitized_payload)
        raise ServiceUnavailableError(
            message=(
                "Agent planning/orchestration service is not configured. "
                "Clarification resumption requires Ayush's agent orchestrator implementation."
            ),
            details={"dependency": "agent_orchestrator", "flow": "clarification_answer"},
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
        return AgentTaskControlResult(
            supported=False,
            executed=False,
            manual_action_required=True,
            next_state=current_state,
            message=(
                f"Automatic '{action.value}' is unsupported: TaskState defines no generic "
                "PAUSED state and no agent orchestrator is connected. Perform any browser "
                "pause/resume step manually or use supported cancel/approval controls."
            ),
        )


_default_agent_gateway = UnconfiguredAgentGateway()


def get_agent_gateway() -> AgentGateway:
    """Return the active AgentGateway instance."""
    return _default_agent_gateway
