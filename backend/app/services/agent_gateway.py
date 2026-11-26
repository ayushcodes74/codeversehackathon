"""Anubhav-owned adapter between frontend APIs and Ayush's orchestration service."""

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass

from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.orchestrator import execute_agent_turn
from app.core.exceptions import AppError, BadRequestError, ConflictError, NotFoundError
from app.core.task_state import ALLOWED_STATE_TRANSITIONS, TaskState
from app.models import AgentMemory, ApprovedPortal, FamilyProfile, Task, User
from app.schemas.agent_contracts import AgentChatRequest, AgentStepResponse
from app.schemas.clarification import (
    ClarificationAnswerRequest,
    ClarificationAnswerResponse,
    ClarificationOption,
    ClarificationPrompt,
    ClarificationType,
)
from app.schemas.common import DocumentType
from app.schemas.goal import (
    GoalPortalRecommendation,
    GoalSubmitRequest,
    GoalSubmitResponse,
)
from app.schemas.session import (
    PortalSupportTier,
    SessionPortalContext,
)
from app.schemas.task import TaskControlAction
from app.services.sanitization import (
    sanitize_metadata_payload,
    sanitize_text_for_persistence,
)

_MEMORY_CLARIFICATION_KEYS = frozenset(
    {"domicile_state", "state", "education_level", "education"}
)


@dataclass(frozen=True)
class AgentTaskControlResult:
    """Outcome of an orchestrator-managed task control request."""

    supported: bool
    executed: bool
    manual_action_required: bool
    next_state: TaskState | None
    message: str


class AgentGateway(ABC):
    """Contract between Anubhav's API/services and Ayush's agent orchestrator."""

    @abstractmethod
    async def submit_goal(
        self,
        *,
        db: AsyncSession,
        user_id: uuid.UUID,
        profile_id: uuid.UUID,
        sanitized_goal_text: str,
        payload: GoalSubmitRequest,
        portal_context: SessionPortalContext | None,
    ) -> GoalSubmitResponse:
        """Start a caller-validated goal through the existing orchestrator."""

    @abstractmethod
    async def submit_clarification(
        self,
        *,
        db: AsyncSession,
        user_id: uuid.UUID,
        task_id: uuid.UUID,
        current_state: TaskState,
        sanitized_payload: ClarificationAnswerRequest,
    ) -> ClarificationAnswerResponse:
        """Resume a supported, caller-validated general-fact clarification."""

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
        """Request pause or resume where the current contracts can do so safely."""

    @abstractmethod
    async def run_chat_turn(
        self,
        *,
        db: AsyncSession,
        caller_user_id: uuid.UUID,
        payload: AgentChatRequest,
    ) -> AgentStepResponse:
        """Run one ownership-verified extension/agent turn through the orchestrator."""


def _state_from_agent_response(response: AgentStepResponse, fallback: TaskState) -> TaskState:
    try:
        return TaskState(response.status)
    except ValueError:
        return fallback


def _parse_task_state(task: Task) -> TaskState:
    try:
        return TaskState(task.status)
    except ValueError as exc:
        raise ConflictError(
            f"Task '{task.id}' has incompatible database status '{task.status}' "
            "which is not in canonical TaskState."
        ) from exc


def _build_clarification(
    response: AgentStepResponse,
    *,
    task_id: uuid.UUID,
    language: str,
) -> ClarificationPrompt | None:
    data = response.tool_input or {}
    field_key = str(data.get("missing_field_key") or "").strip()
    if response.response_type != "clarification" or not field_key:
        return None
    raw_question = (
        data.get("question_hi") if language == "hi" else data.get("question_en")
    )
    question = str(raw_question or response.message or "").strip()
    if not question:
        return None
    options = [
        ClarificationOption(option_id=f"option_{index}", label=str(value), value=str(value))
        for index, value in enumerate(data.get("options") or [], start=1)
    ]
    return ClarificationPrompt(
        clarification_id=uuid.uuid4(),
        task_id=task_id,
        clarification_type=ClarificationType.MISSING_FIELD,
        question=question,
        field_key=field_key,
        options=options,
        required=True,
        sensitive=False,
    )


def _save_pending_clarification(task: Task, prompt: ClarificationPrompt | None) -> None:
    checkpoint = dict(task.checkpoint_state or {})
    if prompt is None:
        checkpoint.pop("pending_clarification", None)
        task.checkpoint_state = sanitize_metadata_payload(checkpoint)
        return
    checkpoint["pending_clarification"] = prompt.model_dump(mode="json")
    task.checkpoint_state = sanitize_metadata_payload(checkpoint)


class OrchestratorAgentGateway(AgentGateway):
    """Authenticated service adapter calling the existing orchestrator in-process.

    Ayush's ``agent_routes.py`` is intentionally not mounted because it exposes
    unauthenticated endpoints and skips profile ownership checks. All calls arrive here
    only after Anubhav's API layer enforces caller, user, profile, and task ownership.
    """

    async def submit_goal(
        self,
        *,
        db: AsyncSession,
        user_id: uuid.UUID,
        profile_id: uuid.UUID,
        sanitized_goal_text: str,
        payload: GoalSubmitRequest,
        portal_context: SessionPortalContext | None,
    ) -> GoalSubmitResponse:
        if portal_context is not None and portal_context.support_tier == PortalSupportTier.BLOCKED:
            raise ConflictError(
                "The current domain is not in the approved portal registry; automation is blocked."
            )
        response = await execute_agent_turn(
            request=AgentChatRequest(
                user_id=user_id,
                profile_id=profile_id,
                message=sanitized_goal_text,
                language=payload.language,
                current_url=payload.current_url,
            ),
            db=db,
        )
        if response.response_type == "error":
            raise ConflictError(
                response.message or "Goal intake was blocked by the agent orchestrator."
            )
        if response.task_id is None:
            raise ConflictError("The agent did not create or select a task for this goal.")
        task = await db.get(Task, response.task_id)
        if task is None or task.user_id != user_id or task.profile_id != profile_id:
            raise ConflictError("The agent returned a task outside the authenticated profile.")

        current_state = _parse_task_state(task)
        clarification = _build_clarification(
            response, task_id=task.id, language=payload.language
        )
        checkpoint = dict(task.checkpoint_state or {})
        checkpoint["language"] = payload.language
        task.checkpoint_state = sanitize_metadata_payload(checkpoint)
        if clarification is not None:
            if TaskState.AWAITING_USER_INPUT != current_state and (
                TaskState.AWAITING_USER_INPUT
                in ALLOWED_STATE_TRANSITIONS.get(current_state, frozenset())
            ):
                task.status = TaskState.AWAITING_USER_INPUT.value
            _save_pending_clarification(task, clarification)
        else:
            desired_state = _state_from_agent_response(response, TaskState.IN_PROGRESS)
            if desired_state != current_state and desired_state in ALLOWED_STATE_TRANSITIONS.get(
                current_state, frozenset()
            ):
                task.status = desired_state.value
        await db.flush()
        await db.refresh(task)

        recommendation = None
        if task.portal_id is not None:
            portal = await db.get(ApprovedPortal, task.portal_id)
            if portal is not None:
                doc_types: list[DocumentType] = []
                for raw_type in portal.required_doc_types or []:
                    try:
                        doc_types.append(DocumentType(raw_type))
                    except ValueError:
                        continue
                recommendation = GoalPortalRecommendation(
                    portal_id=portal.id,
                    portal_name=portal.portal_name,
                    domain=portal.domain,
                    entry_url=portal.entry_url,
                    category=portal.category,
                    # Registry presence is known; do not infer government status from a suffix.
                    support_tier=PortalSupportTier.KNOWN_PORTAL,
                    required_doc_types=doc_types,
                )

        return GoalSubmitResponse(
            task_id=task.id,
            session_id=payload.session_id,
            status=_parse_task_state(task),
            goal_text=sanitized_goal_text,
            language=payload.language,
            detected_intent=response.intent_lane,
            recommended_portal=recommendation,
            steps_preview=[],
            clarification_needed=clarification is not None,
            clarification=clarification,
            created_at=task.created_at,
        )

    async def submit_clarification(
        self,
        *,
        db: AsyncSession,
        user_id: uuid.UUID,
        task_id: uuid.UUID,
        current_state: TaskState,
        sanitized_payload: ClarificationAnswerRequest,
    ) -> ClarificationAnswerResponse:
        _ = current_state
        task = await db.get(Task, task_id)
        if task is None or task.user_id != user_id:
            raise NotFoundError("Task not found for authenticated caller.")
        pending = (task.checkpoint_state or {}).get("pending_clarification")
        if not isinstance(pending, dict) or str(pending.get("clarification_id")) != str(
            sanitized_payload.clarification_id
        ):
            raise ConflictError("Clarification is missing, expired, or no longer current.")
        if sanitized_payload.approved is not None:
            raise BadRequestError(
                "Final submission approval must use the action-hash-bound task action endpoint."
            )

        field_key = str(pending.get("field_key") or "").strip().lower()
        if field_key not in _MEMORY_CLARIFICATION_KEYS:
            raise ConflictError(
                "This clarification type is not resumable by the current orchestrator; complete it manually."
            )
        answer = sanitized_payload.field_values.get(field_key) or sanitized_payload.answer_text
        if answer is None and sanitized_payload.selected_option_id:
            selected_id = sanitized_payload.selected_option_id
            for option in pending.get("options") or []:
                if isinstance(option, dict) and option.get("option_id") == selected_id:
                    answer = str(option.get("value") or "")
                    break
        if not answer:
            raise BadRequestError("A valid answer for the requested clarification is required.")

        profile_id = task.profile_id
        memory_stmt = select(AgentMemory).where(
            AgentMemory.user_id == user_id,
            AgentMemory.profile_id == profile_id,
            AgentMemory.fact_key == field_key,
        )
        memory = (await db.execute(memory_stmt)).scalar_one_or_none()
        old_value = memory.fact_value if memory is not None else None
        if memory is None:
            memory = AgentMemory(
                user_id=user_id,
                profile_id=profile_id,
                category="clarification",
                fact_key=field_key,
                fact_value=answer,
                source="user_confirmed",
            )
            db.add(memory)
        else:
            memory.fact_value = answer
            memory.category = "clarification"
            memory.source = "user_confirmed"
        await db.flush()

        try:
            response = await execute_agent_turn(
                request=AgentChatRequest(
                    user_id=user_id,
                    profile_id=profile_id,
                    task_id=task_id,
                    message=task.goal_text,
                    language=str((task.checkpoint_state or {}).get("language") or "en"),
                    current_url=task.target_url,
                ),
                db=db,
            )
        finally:
            if not sanitized_payload.save_to_memory:
                if old_value is None:
                    await db.delete(memory)
                else:
                    memory.fact_value = old_value
                await db.flush()

        if response.response_type == "error":
            raise ConflictError(
                response.message or "Clarification could not be processed by the orchestrator."
            )

        next_prompt = _build_clarification(
            response,
            task_id=task_id,
            language=str((task.checkpoint_state or {}).get("language") or "en"),
        )
        task = await db.get(Task, task_id)
        if task is None:
            raise NotFoundError("Task disappeared while processing clarification.")
        refreshed_state = _parse_task_state(task)
        if next_prompt is not None:
            if TaskState.AWAITING_USER_INPUT != refreshed_state and (
                TaskState.AWAITING_USER_INPUT
                in ALLOWED_STATE_TRANSITIONS.get(refreshed_state, frozenset())
            ):
                task.status = TaskState.AWAITING_USER_INPUT.value
            _save_pending_clarification(task, next_prompt)
        else:
            desired_state = _state_from_agent_response(response, TaskState.IN_PROGRESS)
            if desired_state != refreshed_state and (
                desired_state in ALLOWED_STATE_TRANSITIONS.get(refreshed_state, frozenset())
            ):
                task.status = desired_state.value
            _save_pending_clarification(task, None)
        await db.flush()

        return ClarificationAnswerResponse(
            task_id=task_id,
            clarification_id=sanitized_payload.clarification_id,
            accepted=True,
            next_status=_parse_task_state(task),
            message=response.message,
            next_clarification=next_prompt,
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
                f"Automatic '{action.value}' needs a fresh client page snapshot and an active "
                "orchestration session; perform any pause/resume step manually."
            ),
        )

    async def run_chat_turn(
        self,
        *,
        db: AsyncSession,
        caller_user_id: uuid.UUID,
        payload: AgentChatRequest,
    ) -> AgentStepResponse:
        user = await db.get(User, caller_user_id)
        if user is None:
            raise NotFoundError("Authenticated user record not found.")

        if payload.profile_id is not None:
            profile = await db.get(FamilyProfile, payload.profile_id)
            if profile is None:
                raise NotFoundError("Family profile not found.")
            if profile.user_id != caller_user_id:
                raise AppError(
                    message="Forbidden: profile_id does not belong to this user.",
                    status_code=status.HTTP_403_FORBIDDEN,
                    code="forbidden_profile_access",
                )

        if payload.task_id is not None:
            task = await db.get(Task, payload.task_id)
            if task is None:
                raise NotFoundError("Task not found.")
            if task.user_id != caller_user_id:
                raise AppError(
                    message="Forbidden: caller identity does not match task owner.",
                    status_code=status.HTTP_403_FORBIDDEN,
                    code="forbidden_task_access",
                )
            task_profile = await db.get(FamilyProfile, task.profile_id)
            if task_profile is None or task_profile.user_id != caller_user_id:
                raise AppError(
                    message="Forbidden: task profile ownership check failed.",
                    status_code=status.HTTP_403_FORBIDDEN,
                    code="forbidden_profile_access",
                )
            if payload.profile_id is not None and task.profile_id != payload.profile_id:
                raise ConflictError(
                    "Requested profile_id does not match the existing task's profile."
                )

        sanitized_request = payload.model_copy(
            update={
                "user_id": caller_user_id,
                "message": sanitize_text_for_persistence(payload.message),
            }
        )
        try:
            return await execute_agent_turn(request=sanitized_request, db=db)
        except PermissionError as exc:
            raise AppError(
                message=str(exc),
                status_code=status.HTTP_403_FORBIDDEN,
                code="forbidden_agent_access",
            ) from exc
        except ValueError as exc:
            raise BadRequestError(message=str(exc)) from exc


_default_agent_gateway = OrchestratorAgentGateway()


def get_agent_gateway() -> AgentGateway:
    """Return the active in-process orchestrator adapter."""
    return _default_agent_gateway
