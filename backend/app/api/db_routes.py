"""Database state, Domain Shield, user profile, and task checkpoint routes."""

import uuid
from enum import Enum
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import (
    AgentMemory,
    ApprovedPortal,
    FamilyProfile,
    Task,
    ToolAuditLog,
    User,
)
from app.policies.log_redactor import redact_dict

router = APIRouter(prefix="/api/v1", tags=["Database & State"])


class TaskState(str, Enum):
    """Allowed workflow states for Safe-Pause task checkpoints."""

    DISCOVERING = "discovering"
    NAVIGATING = "navigating"
    INSPECTING_FORM = "inspecting_form"
    MAPPING_FIELDS = "mapping_fields"
    AWAITING_USER_INPUT = "awaiting_user_input"
    PAUSED_FOR_AUTH_OR_CAPTCHA = "paused_for_auth_or_captcha"
    AWAITING_SUBMIT_APPROVAL = "awaiting_submit_approval"
    SUBMITTED = "submitted"
    FAILED = "failed"


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


class TaskCreate(BaseModel):
    user_id: uuid.UUID
    portal_id: uuid.UUID | None = None
    profile_id: uuid.UUID | None = None
    user_goal: str = Field(..., min_length=1)
    initial_state: TaskState = TaskState.DISCOVERING


class TaskCheckpointUpdate(BaseModel):
    status: TaskState | None = None
    current_step_index: int | None = None
    checkpoint_state: dict[str, Any] | None = None
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
async def create_family_profile(
    user_id: uuid.UUID, payload: FamilyProfileCreate, db: AsyncSession = Depends(get_db)
):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    existing_stmt = select(FamilyProfile).where(
        FamilyProfile.user_id == user_id,
        FamilyProfile.profile_slug == payload.profile_slug,
    )
    if (await db.execute(existing_stmt)).scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Profile slug '{payload.profile_slug}' already exists for this user.",
        )

    profile = FamilyProfile(
        user_id=user_id,
        profile_slug=payload.profile_slug,
        display_name=payload.display_name,
        relation_type=payload.relation_type,
        is_default=False,
    )
    db.add(profile)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Profile slug '{payload.profile_slug}' already exists for this user.",
        ) from exc
    return profile


# --- 3. Editable Agent Memory (Get & Upsert) ---
@router.get("/users/{user_id}/memory")
async def get_user_memory(
    user_id: uuid.UUID, profile_id: uuid.UUID | None = None, db: AsyncSession = Depends(get_db)
):
    stmt = select(AgentMemory).where(AgentMemory.user_id == user_id)
    if profile_id:
        stmt = stmt.where(AgentMemory.profile_id == profile_id)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.put("/users/{user_id}/memory")
async def upsert_memory(
    user_id: uuid.UUID, payload: MemoryUpsert, db: AsyncSession = Depends(get_db)
):
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


# --- 4. Official Domain Shield Check ---
def _extract_canonical_hostname(raw_input: str) -> str:
    candidate = (raw_input or "").strip()
    if not candidate:
        return ""
    to_parse = candidate if "://" in candidate else f"https://{candidate}"
    parsed = urlparse(to_parse)
    hostname = (parsed.hostname or "").strip().lower().rstrip(".")
    return hostname.removeprefix("www.")


@router.get("/portals/verify")
async def verify_domain(
    domain: str | None = Query(default=None),
    url: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    raw_target = domain if domain is not None else url
    if not raw_target or not raw_target.strip():
        raise HTTPException(status_code=422, detail="Query parameter 'domain' or 'url' is required.")

    canonical_domain = _extract_canonical_hostname(raw_target)
    if not canonical_domain or "." not in canonical_domain:
        return {
            "is_official": False,
            "canonical_domain": canonical_domain,
            "support_tier": "blocked_untrusted",
            "reason": "Invalid or unparseable domain/URL.",
        }

    # Reject punycode / IDN homograph domains.
    labels = canonical_domain.split(".")
    if any(lbl.startswith("xn--") for lbl in labels) or not canonical_domain.isascii():
        return {
            "is_official": False,
            "canonical_domain": canonical_domain,
            "support_tier": "blocked_untrusted",
            "reason": f"Punycode or non-ASCII homograph domain '{canonical_domain}' is blocked by Official Domain Shield.",
        }

    result = await db.execute(
        select(ApprovedPortal).where(
            ApprovedPortal.domain == canonical_domain,
            ApprovedPortal.is_active == True,
        )
    )
    portal = result.scalar_one_or_none()
    has_official_suffix = canonical_domain.endswith(".gov.in") or canonical_domain.endswith(".nic.in")

    if portal and has_official_suffix:
        return {
            "is_official": True,
            "canonical_domain": canonical_domain,
            "support_tier": "verified_official",
            "reason": f"Verified official portal '{portal.portal_name}' in approved registry.",
        }

    if portal:
        return {
            "is_official": True,
            "canonical_domain": canonical_domain,
            "support_tier": "known_portal",
            "reason": f"Approved portal '{portal.portal_name}' found in registry.",
        }

    if has_official_suffix:
        return {
            "is_official": True,
            "canonical_domain": canonical_domain,
            "support_tier": "verified_official",
            "reason": f"Official Indian government domain suffix verified for '{canonical_domain}'.",
        }

    return {
        "is_official": False,
        "canonical_domain": canonical_domain,
        "support_tier": "blocked_untrusted",
        "reason": f"Domain '{canonical_domain}' is neither an official .gov.in/.nic.in domain nor in the approved portal registry.",
    }


# --- 5. Task Creation & Safe-Pause Checkpoints ---
@router.post("/tasks", status_code=status.HTTP_201_CREATED)
async def create_task(payload: TaskCreate, db: AsyncSession = Depends(get_db)):
    user = await db.get(User, payload.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    profile: FamilyProfile | None = None
    if payload.profile_id is not None:
        profile = await db.get(FamilyProfile, payload.profile_id)
        if not profile or profile.user_id != payload.user_id:
            raise HTTPException(status_code=404, detail="Family profile not found for this user")
    else:
        prof_stmt = (
            select(FamilyProfile)
            .where(FamilyProfile.user_id == payload.user_id)
            .order_by(FamilyProfile.is_default.desc(), FamilyProfile.created_at.asc())
        )
        profile = (await db.execute(prof_stmt)).scalars().first()
        if not profile:
            raise HTTPException(status_code=404, detail="No family profile found for this user")

    if payload.portal_id is not None:
        portal = await db.get(ApprovedPortal, payload.portal_id)
        if not portal:
            raise HTTPException(status_code=404, detail="Approved portal not found")

    task = Task(
        user_id=payload.user_id,
        profile_id=profile.id,
        portal_id=payload.portal_id,
        goal_text=payload.user_goal,
        status=payload.initial_state.value,
    )
    db.add(task)
    await db.flush()

    return {
        "task_id": task.id,
        "id": task.id,
        "user_id": task.user_id,
        "profile_id": task.profile_id,
        "portal_id": task.portal_id,
        "user_goal": task.goal_text,
        "status": task.status,
        "current_step_index": task.current_step_index,
        "checkpoint_state": task.checkpoint_state,
        "created_at": task.created_at,
    }


@router.patch("/tasks/{task_id}/checkpoint")
async def save_task_checkpoint(
    task_id: uuid.UUID, payload: TaskCheckpointUpdate, db: AsyncSession = Depends(get_db)
):
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if payload.status is not None:
        task.status = payload.status.value
    if payload.current_step_index is not None:
        task.current_step_index = payload.current_step_index
    if payload.checkpoint_state is not None:
        task.checkpoint_state = redact_dict(payload.checkpoint_state)
    if payload.application_ref_no is not None:
        task.application_ref_no = payload.application_ref_no

    sanitized_input = redact_dict(payload.model_dump(mode="json", exclude_none=True))
    sanitized_output = redact_dict(
        {
            "task_id": str(task.id),
            "status": task.status,
            "current_step_index": task.current_step_index,
        }
    )
    audit_log = ToolAuditLog(
        task_id=task.id,
        tool_name="task_checkpoint_update",
        status="ok",
        sanitized_input=sanitized_input,
        sanitized_output=sanitized_output,
    )
    db.add(audit_log)
    await db.flush()

    return {"task_id": task.id, "status": task.status, "step": task.current_step_index}
