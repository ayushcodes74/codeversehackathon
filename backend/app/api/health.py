"""Health and readiness check endpoints for the backend service."""

from fastapi import APIRouter, Depends

from app.core.config import AppSettings, get_settings

router = APIRouter(tags=["Health"])


@router.get("/health")
async def health_check(settings: AppSettings = Depends(get_settings)) -> dict[str, str]:
    """Lightweight liveness check."""
    return {
        "status": "ok",
        "service": settings.app_slug,
        "version": settings.app_version,
        "environment": settings.app_env,
    }


@router.get("/health/ready")
async def readiness_check(settings: AppSettings = Depends(get_settings)) -> dict[str, str]:
    """Service readiness check confirming the FastAPI app and configuration are loaded."""
    return {
        "status": "ready",
        "service": settings.app_slug,
        "version": settings.app_version,
    }
