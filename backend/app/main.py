"""FastAPI application entry point for Universal AI Web Assistant (v3.0)."""

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.agent_routes import router as agent_router
from app.api.router import register_routers
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.database import get_db
from app.policies.log_redactor import install_log_redaction, redact_sensitive_text

install_log_redaction()
_settings = get_settings()
configure_logging(_settings.log_level)

app = FastAPI(
    title=_settings.app_name,
    version=_settings.app_version,
    description="Zero-Leak Hybrid AI Web Assistant API & Browser Automation Layer",
    debug=_settings.debug,
    docs_url="/docs" if _settings.docs_enabled else None,
    redoc_url="/redoc" if _settings.docs_enabled else None,
    openapi_url="/openapi.json" if _settings.docs_enabled else None,
)

# Enable CORS for Chrome Extension and Demo Portal communication
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)
app.add_middleware(RequestContextMiddleware)
register_exception_handlers(app, _settings)

# Mount health, db_router, documents_router, Module 4 routers, and agent_router (/api/v1/agent/*)
register_routers(app, _settings)
app.include_router(agent_router)


@app.get("/health/db", tags=["Health"])
async def check_db(db: AsyncSession = Depends(get_db)):
    """Verify PostgreSQL connectivity via SELECT 1 without unhandled 500s."""
    try:
        await db.execute(text("SELECT 1"))
        return {"status": "connected", "database": "ok"}
    except Exception as exc:
        return {
            "status": "unhealthy",
            "database": "error",
            "detail": redact_sensitive_text(str(exc)),
        }


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
