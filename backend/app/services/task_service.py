"""Ownership-enforced task query and lifecycle control service reusing Ayush's contracts."""

import hashlib
import hmac
import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.task_state import (
    ALLOWED_STATE_TRANSITIONS,
    TERMINAL_TASK_STATES,
    TaskState,
)
from app.core.exceptions import AppError, ConflictError, NotFoundError
from app.models import FamilyProfile, Task, ToolAuditLog
from app.schemas.common import PaginationMeta
from app.schemas.task import (
    TaskActionRequest,
    TaskActionResponse,
    TaskControlAction,
    TaskListResponse,
    TaskSummaryResponse,
)
from app.services.agent_gateway import AgentGateway, get_agent_gateway
from app.services.auth_context import resolve_authenticated_user_id
from app.services.sanitization import sanitize_text_for_persistence

logger = logging.getLogger("app.services.task")


def _compute_pending_action_hash(task: Task) -> str:
    """Bind approval to the current task, profile, portal, step, and checkpoint."""
    canonical = (
        f"{task.id}:{task.user_id}:{task.profile_id}:{task.portal_id}:"
        f"{task.current_step_index}:{json.dumps(task.checkpoint_state or {}, sort_keys=True)}"
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _parse_canonical_task_state(task: Task) -> TaskState:
    try:
        return TaskState(task.status)
    except ValueError as exc:
        raise ConflictError(
            message=(
                f"Task '{task.id}' has incompatible database status '{task.status}' "
                "which is not in canonical TaskState."
            )
        ) from exc


async def list_user_tasks(
    db: AsyncSession,
    *,
    user_id: uuid.UUID | None,
    x_user_id: str | None,
    authorization: str | None,
    profile_id: uuid.UUID | None = None,
    status_filter: TaskState | None = None,
    limit: int = 20,
    offset: int = 0,
) -> TaskListResponse:
    """List paginated tasks belonging to the authenticated caller and optional profile."""
    caller_user_id = resolve_authenticated_user_id(
        x_user_id=x_user_id,
        authorization=authorization,
        expected_user_id=user_id,
    )

    if profile_id is not None:
        profile = await db.get(FamilyProfile, profile_id)
        if profile is None:
            raise NotFoundError("Family profile not found.")
        if profile.user_id != caller_user_id:
            raise AppError(
                message="Forbidden: profile_id does not belong to this user.",
                status_code=status.HTTP_403_FORBIDDEN,
                code="forbidden_profile_access",
            )

    filters = [Task.user_id == caller_user_id]
    if profile_id is not None:
        filters.append(Task.profile_id == profile_id)
    if status_filter is not None:
        filters.append(Task.status == status_filter.value)

    count_stmt = select(func.count()).select_from(Task).where(*filters)
    total = int((await db.execute(count_stmt)).scalar_one() or 0)

    list_stmt = (
        select(Task)
        .where(*filters)
        .order_by(Task.updated_at.desc(), Task.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    rows = (await db.execute(list_stmt)).scalars().all()

    summaries: list[TaskSummaryResponse] = []
    for task in rows:
        canonical_status = _parse_canonical_task_state(task)
        summaries.append(
            TaskSummaryResponse(
                id=task.id,
                user_id=task.user_id,
                profile_id=task.profile_id,
                portal_id=task.portal_id,
                goal_text=sanitize_text_for_persistence(task.goal_text),
                target_url=task.target_url,
                status=canonical_status,
                current_step_index=task.current_step_index,
                total_steps=task.total_steps,
                application_ref_no=(
                    sanitize_text_for_persistence(task.application_ref_no)
                    if task.application_ref_no is not None
                    else None
                ),
                created_at=task.created_at,
                updated_at=task.updated_at,
            )
        )

    return TaskListResponse(
        tasks=summaries,
        pagination=PaginationMeta(
            total=total,
            limit=limit,
            offset=offset,
            has_more=(offset + len(summaries) < total),
        ),
    )


async def apply_task_action(
    db: AsyncSession,
    *,
    task_id: uuid.UUID,
    payload: TaskActionRequest,
    x_user_id: str | None,
    authorization: str | None,
    gateway: AgentGateway | None = None,
) -> TaskActionResponse:
    """Execute supported task cancel/approval actions or return manual-required for pause/resume."""
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

    current_state = _parse_canonical_task_state(task)
    now = datetime.now(timezone.utc)

    if payload.action in (TaskControlAction.PAUSE, TaskControlAction.RESUME):
        active_gateway = gateway or get_agent_gateway()
        control_result = await active_gateway.request_task_control(
            user_id=caller_user_id,
            task_id=task.id,
            current_state=current_state,
            action=payload.action,
            reason=payload.reason,
        )
        next_state = control_result.next_state or current_state
        if (
            control_result.executed
            and next_state != current_state
            and next_state in ALLOWED_STATE_TRANSITIONS.get(current_state, frozenset())
        ):
            task.status = next_state.value
            await db.flush()
            await db.refresh(task)
            return TaskActionResponse(
                task_id=task.id,
                action=payload.action,
                previous_status=current_state,
                status=next_state,
                executed=True,
                manual_action_required=control_result.manual_action_required,
                message=control_result.message,
                updated_at=task.updated_at or now,
            )

        return TaskActionResponse(
            task_id=task.id,
            action=payload.action,
            previous_status=current_state,
            status=current_state,
            executed=False,
            manual_action_required=True,
            message=control_result.message,
            updated_at=task.updated_at or now,
        )

    if current_state in TERMINAL_TASK_STATES:
        raise ConflictError(
            f"Terminal task state '{current_state.value}' cannot execute '{payload.action.value}'."
        )

    safe_reason = (
        sanitize_text_for_persistence(payload.reason)
        if payload.reason is not None
        else None
    )

    if payload.action == TaskControlAction.CANCEL:
        allowed = ALLOWED_STATE_TRANSITIONS.get(current_state, frozenset())
        if TaskState.CANCELLED not in allowed:
            raise ConflictError(
                f"Transition from '{current_state.value}' to 'cancelled' is not allowed."
            )
        task.status = TaskState.CANCELLED.value
        db.add(
            ToolAuditLog(
                task_id=task.id,
                tool_name="task_control_cancel",
                status="ok",
                sanitized_input={"action": "cancel", "reason": safe_reason},
                sanitized_output={"task_id": str(task.id), "status": task.status},
            )
        )
        await db.flush()
        await db.refresh(task)
        logger.info("Cancelled task id=%s from state=%s", task.id, current_state.value)
        return TaskActionResponse(
            task_id=task.id,
            action=payload.action,
            previous_status=current_state,
            status=TaskState.CANCELLED,
            executed=True,
            manual_action_required=False,
            message="Task cancelled.",
            updated_at=task.updated_at or now,
        )

    # APPROVE_SUBMIT / REJECT_SUBMIT: strictly gated by FR-027 action_hash verification.
    if current_state not in (
        TaskState.AWAITING_APPROVAL,
        TaskState.AWAITING_SUBMIT_APPROVAL,
    ):
        raise ConflictError(
            f"Task must be awaiting approval to execute '{payload.action.value}' "
            f"(current: '{current_state.value}')."
        )

    expected_hash = _compute_pending_action_hash(task)
    submitted_hash = (payload.action_hash or "").strip()
    if not submitted_hash or not hmac.compare_digest(submitted_hash, expected_hash):
        raise ConflictError(
            "Action hash mismatch: pending task state changed after approval prompt (FR-027)."
        )

    approved = payload.action == TaskControlAction.APPROVE_SUBMIT
    # Approval records permission to proceed; it does not prove the portal submitted.
    # Keep the task in SUBMITTING until the client returns observable outcome evidence.
    next_state = TaskState.SUBMITTING if approved else TaskState.CANCELLED
    task.status = next_state.value
    db.add(
        ToolAuditLog(
            task_id=task.id,
            tool_name="task_submit_approval",
            status="approved" if approved else "rejected",
            sanitized_input={
                "approved": approved,
                "action_hash": submitted_hash,
                "reviewer_note": safe_reason,
                "reviewed_fields_count": payload.reviewed_fields_count,
            },
            sanitized_output={
                "task_id": str(task.id),
                "status": task.status,
                "action_hash": submitted_hash,
            },
        )
    )
    await db.flush()
    await db.refresh(task)
    logger.info(
        "Recorded submit decision task_id=%s approved=%s status=%s",
        task.id,
        approved,
        next_state.value,
    )
    return TaskActionResponse(
        task_id=task.id,
        action=payload.action,
        previous_status=current_state,
        status=next_state,
        executed=True,
        manual_action_required=False,
        action_hash=submitted_hash,
        message=(
            "Approval recorded. The client must perform the submit action and return evidence."
            if approved
            else "Submission rejected and task cancelled."
        ),
        updated_at=task.updated_at or now,
    )
