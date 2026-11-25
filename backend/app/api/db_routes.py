import uuid
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models import User, FamilyProfile, AgentMemory, Task, ApprovedPortal

router = APIRouter(prefix="/api/v1", tags=["Database & State"])


# --- Pydantic Schemas ---
class GoogleAuthUpsert(BaseModel):
    google_sub: str
    email: str
    full_name: str
    mobile_no: str | None = None
    preferred_language: str = "en"


class FamilyProfileCreate(BaseModel):
    profile_slug: str  # 'father', 'mother', 'sibling_1'
    display_name: str
    relation_type: str  # 'parent', 'sibling', 'other'


class MemoryUpsert(BaseModel):
    profile_id: uuid.UUID | None = None
    category: str
    fact_key: str
    fact_value: str


class TaskCheckpointUpdate(BaseModel):
    status: str | None = None
    current_step_index: int | None = None
    checkpoint_state: dict | None = None
    application_ref_no: str | None = None


# --- 1. User Upsert (Auto-creates default 'self' FamilyProfile) ---
@router.post("/users/auth")
async def upsert_user(payload: GoogleAuthUpsert, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.google_sub == payload.google_sub))
    user = result.scalar_one_or_none()

    if not user:
        user = User(
            google_sub=payload.google_sub,
            email=payload.email,
            full_name=payload.full_name,
            mobile_no=payload.mobile_no,
            preferred_language=payload.preferred_language,
        )
        db.add(user)
        await db.flush()

        # Automatically create default 'self' profile
        default_profile = FamilyProfile(
            user_id=user.id,
            profile_slug="self",
            display_name=payload.full_name,
            relation_type="self",
            is_default=True,
        )
        db.add(default_profile)
        await db.flush()
    else:
        user.full_name = payload.full_name
        if payload.mobile_no:
            user.mobile_no = payload.mobile_no
        user.preferred_language = payload.preferred_language

    profiles_res = await db.execute(select(FamilyProfile).where(FamilyProfile.user_id == user.id))
    profiles = profiles_res.scalars().all()
    return {"user_id": user.id, "email": user.email, "profiles": profiles}


# --- 2. Add Family / Dependent Profile ---
@router.post("/users/{user_id}/profiles")
async def create_family_profile(user_id: uuid.UUID, payload: FamilyProfileCreate, db: AsyncSession = Depends(get_db)):
    profile = FamilyProfile(
        user_id=user_id,
        profile_slug=payload.profile_slug,
        display_name=payload.display_name,
        relation_type=payload.relation_type,
        is_default=False,
    )
    db.add(profile)
    await db.flush()
    return profile


# --- 3. Editable Agent Memory (Get & Upsert) ---
@router.get("/users/{user_id}/memory")
async def get_user_memory(user_id: uuid.UUID, profile_id: uuid.UUID | None = None, db: AsyncSession = Depends(get_db)):
    stmt = select(AgentMemory).where(AgentMemory.user_id == user_id)
    if profile_id:
        stmt = stmt.where(AgentMemory.profile_id == profile_id)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.put("/users/{user_id}/memory")
async def upsert_memory(user_id: uuid.UUID, payload: MemoryUpsert, db: AsyncSession = Depends(get_db)):
    stmt = select(AgentMemory).where(
        AgentMemory.user_id == user_id,
        AgentMemory.profile_id == payload.profile_id,
        AgentMemory.fact_key == payload.fact_key,
    )
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing:
        existing.fact_value = payload.fact_value
        existing.category = payload.category
        return existing

    mem = AgentMemory(
        user_id=user_id,
        profile_id=payload.profile_id,
        category=payload.category,
        fact_key=payload.fact_key,
        fact_value=payload.fact_value,
    )
    db.add(mem)
    await db.flush()
    return mem


# --- 4. Domain Shield Check ---
@router.get("/portals/verify")
async def verify_domain(domain: str, db: AsyncSession = Depends(get_db)):
    clean_domain = domain.lower().removeprefix("www.")
    result = await db.execute(
        select(ApprovedPortal).where(ApprovedPortal.domain == clean_domain, ApprovedPortal.is_active == True)
    )
    portal = result.scalar_one_or_none()
    if portal:
        return {"status": "allowed", "portal": portal}
    return {"status": "blocked_unverified", "reason": f"Domain '{clean_domain}' is not in the verified portal registry."}


# --- 5. Safe-Pause & Resume Task Checkpoints ---
@router.patch("/tasks/{task_id}/checkpoint")
async def save_task_checkpoint(task_id: uuid.UUID, payload: TaskCheckpointUpdate, db: AsyncSession = Depends(get_db)):
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if payload.status is not None:
        task.status = payload.status
    if payload.current_step_index is not None:
        task.current_step_index = payload.current_step_index
    if payload.checkpoint_state is not None:
        task.checkpoint_state = payload.checkpoint_state
    if payload.application_ref_no is not None:
        task.application_ref_no = payload.application_ref_no
    return {"task_id": task.id, "status": task.status, "step": task.current_step_index}
