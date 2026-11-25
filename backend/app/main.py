from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.router import register_routers
from app.core.config import AppSettings, get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import register_middlewares
from app.database import get_db
from app.models import ApprovedPortal
from app.api.db_routes import router as db_router
from app.api.agent_routes import router as agent_router

app = FastAPI(
    title="Universal AI Web Assistant API",
    version="3.0.0",
    description="Zero-Leak Hybrid AI Web Assistant API & Browser Automation Layer"
)

# Enable CORS for Chrome Extension and Demo Portal communication
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(db_router)
app.include_router(agent_router)


@app.get("/health/db")
async def check_db(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ApprovedPortal).where(ApprovedPortal.is_active == True))
    portals = result.scalars().all()
    return {
        "status": "connected",
        "approved_portals_count": len(portals),
        "domains": [p.domain for p in portals],
    }
