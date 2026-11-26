"""FastAPI Agent Orchestration & Browser Automation API Routes."""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.orchestrator import execute_agent_turn
from app.agent.visual_guides import VISUAL_GUIDE_REGISTRY, get_visual_guide_card
from app.api.db_routes import (
    TERMINAL_TASK_STATES,
    TaskState,
    verify_caller_ownership,
)
from app.database import get_db
from app.models import Task
from app.policies.log_redactor import redact_dict, redact_sensitive_text
from app.schemas.agent_contracts import (
    AgentChatRequest,
    AgentStepResponse,
    VisualGuideCard,
)

router = APIRouter(prefix="/api/v1/agent", tags=["Agent Orchestrator"])

_RESUMABLE_STATUSES = tuple(
    s.value for s in TaskState if s not in TERMINAL_TASK_STATES
)


@router.post("/chat", response_model=AgentStepResponse)
async def chat_with_agent(
    payload: AgentChatRequest,
    db: AsyncSession = Depends(get_db),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> AgentStepResponse:
    """Master conversational and browser orchestration endpoint (ownership-enforced)."""
    verify_caller_ownership(
        str(payload.user_id),
        x_user_id=x_user_id,
        authorization=authorization,
    )

    try:
        return await execute_agent_turn(request=payload, db=db)
    except PermissionError as pe:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(pe))
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Agent orchestration failed: {redact_sensitive_text(str(e))}",
        )


@router.get("/tasks/{user_id}/resumable")
async def get_resumable_tasks(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
):
    """List all active/resumable non-terminal tasks for Safe-Pause & Resume Later."""
    verify_caller_ownership(
        str(user_id),
        x_user_id=x_user_id,
        authorization=authorization,
    )

    stmt = (
        select(Task)
        .where(Task.user_id == user_id, Task.status.in_(_RESUMABLE_STATUSES))
        .order_by(desc(Task.updated_at))
    )
    result = await db.execute(stmt)
    tasks = result.scalars().all()

    return {
        "count": len(tasks),
        "tasks": [
            {
                "id": str(t.id),
                "goal_text": t.goal_text,
                "target_url": t.target_url,
                "status": t.status,
                "current_step_index": t.current_step_index,
                "total_steps": t.total_steps,
                "checkpoint_state": redact_dict(t.checkpoint_state),
                "application_ref_no": (
                    redact_sensitive_text(t.application_ref_no)
                    if t.application_ref_no is not None
                    else None
                ),
                "updated_at": t.updated_at.isoformat() if t.updated_at else None,
            }
            for t in tasks
        ],
    }


@router.post("/tasks/{task_id}/pause")
async def pause_task(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
):
    """Safely pause an active task in canonical awaiting_user_input state."""
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    verify_caller_ownership(
        str(task.user_id),
        x_user_id=x_user_id,
        authorization=authorization,
    )

    if task.status in {s.value for s in TERMINAL_TASK_STATES}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot pause task in terminal state '{task.status}'.",
        )

    task.status = TaskState.AWAITING_USER_INPUT.value
    await db.flush()
    return {
        "message": "Task paused safely",
        "task_id": str(task.id),
        "status": task.status,
    }


@router.post("/tasks/{task_id}/resume", response_model=AgentStepResponse)
async def resume_task(
    task_id: uuid.UUID,
    language: str = "en",
    db: AsyncSession = Depends(get_db),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
):
    """Resume a paused task from its last saved checkpoint into canonical TaskState."""
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    verify_caller_ownership(
        str(task.user_id),
        x_user_id=x_user_id,
        authorization=authorization,
    )

    if task.status in {s.value for s in TERMINAL_TASK_STATES}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot resume task in terminal state '{task.status}'.",
        )

    task.status = (
        TaskState.DISCOVERING.value
        if task.current_step_index <= 1 and not task.target_url
        else TaskState.FILLING.value
    )
    await db.flush()

    msg_en = f"Resumed task: '{task.goal_text}' from Step {task.current_step_index} of {task.total_steps}."
    msg_hi = f"कार्य पुनरारंभ किया गया: '{task.goal_text}' चरण {task.current_step_index} / {task.total_steps} से।"

    return AgentStepResponse(
        task_id=task.id,
        response_type="resumed",
        intent_lane="form_automation",
        message=msg_hi if language == "hi" else msg_en,
        message_hi=msg_hi,
        current_step_index=task.current_step_index,
        total_steps=task.total_steps,
        status=task.status,
        checkpoint_saved=True,
    )


@router.get("/guides/all", response_model=list[VisualGuideCard])
async def list_all_visual_guides():
    """Return all available visual reference guide cards."""
    return VISUAL_GUIDE_REGISTRY


@router.get("/guides/search", response_model=VisualGuideCard | None)
async def search_visual_guide(
    query: str = Query(..., description="Query topic, e.g. 'account number', 'pan', 'ifsc'"),
    language: str = "en",
):
    """Retrieve a specific visual guide card for a given topic."""
    card = get_visual_guide_card(query, language=language)
    if not card:
        raise HTTPException(status_code=404, detail="No visual guide found matching query")
    return card
