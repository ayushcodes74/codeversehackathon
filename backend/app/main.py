from fastapi import FastAPI, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models import ApprovedPortal
from app.api.db_routes import router as db_router
app.include_router(db_router)
app = FastAPI(title="Universal AI Web Assistant API")

@app.get("/health/db")
async def check_db(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ApprovedPortal).where(ApprovedPortal.is_active == True))
    portals = result.scalars().all()
    return {
        "status": "connected",
        "approved_portals_count": len(portals),
        "domains": [p.domain for p in portals],
    }