"""Central API router registry keeping the backend structured for future modules."""

from fastapi import FastAPI

from app.api.agent import router as protected_agent_router
from app.api.clarifications import router as clarifications_router
from app.api.conversations import router as conversations_router
from app.api.db_routes import router as db_router
from app.api.documents import router as documents_router
from app.api.goals import router as goals_router
from app.api.health import router as health_router
from app.api.sessions import router as sessions_router
from app.api.tasks import router as tasks_router
from app.api.workflows import router as workflows_router
from app.core.config import AppSettings


def register_routers(app: FastAPI, settings: AppSettings) -> None:
    """Mount foundation, existing member, and Anubhav-owned Module 4 API routers."""
    # Foundation liveness/readiness routes
    app.include_router(health_router)

    # Existing member routers preserved without modifying their routes or prefixes
    app.include_router(db_router)
    app.include_router(documents_router)

    # Anubhav-owned Module 4 frontend-facing routes (never mount unauthenticated agent_routes.py)
    app.include_router(protected_agent_router, prefix=settings.api_v1_prefix)
    app.include_router(sessions_router, prefix=settings.api_v1_prefix)
    app.include_router(goals_router, prefix=settings.api_v1_prefix)
    app.include_router(tasks_router, prefix=settings.api_v1_prefix)
    app.include_router(clarifications_router, prefix=settings.api_v1_prefix)
    app.include_router(conversations_router, prefix=settings.api_v1_prefix)
    app.include_router(workflows_router, prefix=settings.api_v1_prefix)
