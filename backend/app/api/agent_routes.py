"""FastAPI Agent Orchestration & Browser Automation API Routes.

Endpoints:
- POST /api/v1/agent/chat: Master chat & browser orchestration loop
- GET  /api/v1/agent/tasks/{user_id}/resumable: List in-progress & paused tasks for Safe-Pause & Resume
- POST /api/v1/agent/tasks/{task_id}/pause: Pause active task with snapshot
- POST /api/v1/agent/tasks/{task_id}/resume: Resume paused task
- GET  /api/v1/agent/guides/{topic}: Direct access to visual guides
"""

import uuid
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from app.database import get_db
from app.models import Task, User, FamilyProfile
from app.schemas.agent_contracts import (
    AgentChatRequest,
    AgentStepResponse,
    VisualGuideCard,
)
from app.agent.orchestrator import execute_agent_turn
from app.agent.visual_guides import get_visual_guide_card, VISUAL_GUIDE_REGISTRY

router = APIRouter(prefix="/api/v1/agent", tags=["Agent Orchestrator"])


@router.post("/chat", response_model=AgentStepResponse)
async def chat_with_agent(
    payload: AgentChatRequest,
    db: AsyncSession = Depends(get_db)
) -> AgentStepResponse:
    """Master conversational and browser orchestration endpoint.
    
    Accepts: user message, current URL, and optional DOMSnapshot.
    Returns: Next tool execution, Visual Guide card, Approval Request, or Status update.
    """
    try:
        response = await execute_agent_turn(request=payload, db=db)
        return response
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agent orchestration failed: {str(e)}")


@router.get("/tasks/{user_id}/resumable")
async def get_resumable_tasks(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db)
):
    """Lists all paused and in-progress tasks for Safe-Pause & Resume Later."""
    stmt = (
        select(Task)
        .where(Task.user_id == user_id, Task.status.in_(["in_progress", "paused"]))
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
                "checkpoint_state": t.checkpoint_state,
                "application_ref_no": t.application_ref_no,
                "updated_at": t.updated_at.isoformat() if t.updated_at else None,
            }
            for t in tasks
        ]
    }


@router.post("/tasks/{task_id}/pause")
async def pause_task(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db)
):
    """Manually pauses an in-progress task for Safe-Pause & Resume Later."""
    stmt = select(Task).where(Task.id == task_id)
    result = await db.execute(stmt)
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    task.status = "paused"
    await db.flush()
    return {"message": "Task paused safely", "task_id": str(task.id), "status": task.status}


@router.post("/tasks/{task_id}/resume", response_model=AgentStepResponse)
async def resume_task(
    task_id: uuid.UUID,
    language: str = "en",
    db: AsyncSession = Depends(get_db)
):
    """Resumes a paused task from its last saved checkpoint."""
    stmt = select(Task).where(Task.id == task_id)
    result = await db.execute(stmt)
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    task.status = "in_progress"
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
        checkpoint_saved=True
    )


@router.get("/guides/all", response_model=list[VisualGuideCard])
async def list_all_visual_guides():
    """Returns all available visual reference guide cards."""
    return VISUAL_GUIDE_REGISTRY


@router.get("/guides/search", response_model=VisualGuideCard | None)
async def search_visual_guide(
    query: str = Query(..., description="Query topic, e.g. 'account number', 'pan', 'ifsc'"),
    language: str = "en"
):
    """Retrieves a specific visual guide card for a given topic."""
    card = get_visual_guide_card(query, language=language)
    if not card:
        raise HTTPException(status_code=404, detail="No visual guide found matching query")
    return card
