"""Central API router registry keeping the backend structured for future modules."""

from fastapi import FastAPI

from app.api.db_routes import router as db_router
from app.api.documents import router as documents_router
from app.api.health import router as health_router
from app.core.config import AppSettings


def register_routers(app: FastAPI, settings: AppSettings) -> None:
    """Mount foundation, existing member, and future API routers on the FastAPI app.

    Future API modules (e.g., agent orchestration, LLM workflows, auth) should define
    their own ``APIRouter`` modules under ``app/api/`` and be registered here under
    ``settings.api_v1_prefix``.
    """
    # Foundation liveness/readiness routes
    app.include_router(health_router)

    # Existing member routers preserved without modifying their routes or prefixes
    app.include_router(db_router)
    app.include_router(documents_router)

    # Extension point for future v1 feature routers:
    # e.g. app.include_router(agent_router, prefix=settings.api_v1_prefix)
    _ = settings.api_v1_prefix
