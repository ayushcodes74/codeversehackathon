"""FastAPI application factory for the Universal AI Web Assistant backend."""

import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.agent_routes import router as agent_router
from app.api.router import register_routers
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.database import get_db
from app.policies.log_redactor import redact_sensitive_text

logger = logging.getLogger("app")


def create_app(custom_settings: AppSettings | None = None) -> FastAPI:
    """Create one configured app instance without shadow apps or wildcard CORS."""
    active_settings = custom_settings or get_settings()
    configure_logging(active_settings.log_level)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        app_settings = getattr(application.state, "settings", active_settings)
        logger.info(
            "Starting %s v%s in %s mode",
            app_settings.app_name,
            app_settings.app_version,
            app_settings.app_env,
        )
        yield
        logger.info("Shutting down %s", app_settings.app_name)

    application = FastAPI(
        title=active_settings.app_name,
        version=active_settings.app_version,
        debug=active_settings.debug,
        docs_url="/docs" if active_settings.docs_enabled else None,
        redoc_url="/redoc" if active_settings.docs_enabled else None,
        openapi_url="/openapi.json" if active_settings.docs_enabled else None,
        lifespan=lifespan,
    )
    application.state.settings = active_settings
    if custom_settings is not None:
        application.dependency_overrides[get_settings] = lambda: active_settings

    register_middlewares(application, active_settings)
    register_exception_handlers(application, active_settings)
    register_routers(application, active_settings)

    @application.get("/health/db", tags=["Health"])
    async def check_db(db: AsyncSession = Depends(get_db)) -> dict[str, str]:
        """Check database connectivity without exposing driver exception details."""
        try:
            await db.execute(text("SELECT 1"))
            return {"status": "connected", "database": "ok"}
        except Exception as exc:
            logger.warning("Database readiness query failed: %s", redact_sensitive_text(str(exc)))
            return {"status": "unhealthy", "database": "error"}

    return application


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        app_dir=str(BACKEND_DIR),
        host=_settings.host,
        port=_settings.port,
        reload=_settings.is_development,
        log_level=_settings.log_level.lower(),
    )
