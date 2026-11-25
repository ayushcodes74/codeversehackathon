"""FastAPI application entry point for Universal AI Web Assistant (v3.0)."""

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.db_routes import router as db_router
from app.api.documents import router as documents_router
from app.database import get_db
from app.policies.log_redactor import install_log_redaction, redact_sensitive_text

install_log_redaction()

app = FastAPI(title="Universal AI Web Assistant API", version="3.0.0")

app.include_router(db_router)
app.include_router(documents_router)


@app.get("/health")
async def health_check():
    """Lightweight liveness check."""
    return {
        "status": "ok",
        "service": "universal-ai-web-assistant",
        "version": "3.0.0",
    }


@app.get("/health/db")
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
