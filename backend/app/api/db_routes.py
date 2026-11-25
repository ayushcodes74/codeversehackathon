"""Database state, Domain Shield, user profile, and task checkpoint routes."""

import base64
import json
import uuid
from enum import Enum
from typing import Any, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
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
from app.policies.log_redactor import redact_dict, redact_sensitive_text

router = APIRouter(prefix="/api/v1", tags=["Database & State"])


class TaskState(str, Enum):
    """Allowed workflow states for task lifecycle and Safe-Pause checkpoints."""

    DISCOVERING = "discovering"
    PLANNING = "planning"
    COLLECTING_DOCS = "collecting_docs"
    NAVIGATING = "navigating"
    INSPECTING_FORM = "inspecting_form"
    MAPPING_FIELDS = "mapping_fields"
    FILLING = "filling"
    AWAITING_USER_INPUT = "awaiting_user_input"
    PAUSED_FOR_AUTH_OR_CAPTCHA = "paused_for_auth_or_captcha"
    AWAITING_APPROVAL = "awaiting_approval"
    AWAITING_SUBMIT_APPROVAL = "awaiting_submit_approval"
    SUBMITTING = "submitting"
    VERIFYING = "verifying"
    SUBMITTED = "submitted"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_TASK_STATES = frozenset(
    {TaskState.SUBMITTED, TaskState.FAILED, TaskState.CANCELLED}
)

ALLOWED_STATE_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.DISCOVERING: frozenset(
        {
            TaskState.PLANNING,
            TaskState.COLLECTING_DOCS,
            TaskState.NAVIGATING,
            TaskState.AWAITING_USER_INPUT,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }
    ),
    TaskState.PLANNING: frozenset(
        {
            TaskState.COLLECTING_DOCS,
            TaskState.NAVIGATING,
            TaskState.AWAITING_APPROVAL,
            TaskState.AWAITING_SUBMIT_APPROVAL,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }
    ),
    TaskState.COLLECTING_DOCS: frozenset(
        {
            TaskState.FILLING,
            TaskState.MAPPING_FIELDS,
            TaskState.AWAITING_APPROVAL,
            TaskState.AWAITING_USER_INPUT,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }
    ),
    TaskState.NAVIGATING: frozenset(
        {
            TaskState.INSPECTING_FORM,
            TaskState.PAUSED_FOR_AUTH_OR_CAPTCHA,
            TaskState.AWAITING_USER_INPUT,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }
    ),
    TaskState.INSPECTING_FORM: frozenset(
        {
            TaskState.MAPPING_FIELDS,
            TaskState.COLLECTING_DOCS,
            TaskState.FILLING,
            TaskState.AWAITING_USER_INPUT,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }
    ),
    TaskState.MAPPING_FIELDS: frozenset(
        {
            TaskState.FILLING,
            TaskState.AWAITING_USER_INPUT,
            TaskState.AWAITING_APPROVAL,
            TaskState.AWAITING_SUBMIT_APPROVAL,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }
    ),
    TaskState.FILLING: frozenset(
        {
            TaskState.AWAITING_APPROVAL,
            TaskState.AWAITING_SUBMIT_APPROVAL,
            TaskState.VERIFYING,
            TaskState.PAUSED_FOR_AUTH_OR_CAPTCHA,
            TaskState.AWAITING_USER_INPUT,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }
    ),
    TaskState.AWAITING_USER_INPUT: frozenset(
        {
            TaskState.DISCOVERING,
            TaskState.PLANNING,
            TaskState.COLLECTING_DOCS,
            TaskState.NAVIGATING,
            TaskState.INSPECTING_FORM,
            TaskState.MAPPING_FIELDS,
            TaskState.FILLING,
            TaskState.PAUSED_FOR_AUTH_OR_CAPTCHA,
            TaskState.AWAITING_APPROVAL,
            TaskState.AWAITING_SUBMIT_APPROVAL,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }
    ),
    TaskState.PAUSED_FOR_AUTH_OR_CAPTCHA: frozenset(
        {
            TaskState.NAVIGATING,
            TaskState.INSPECTING_FORM,
            TaskState.MAPPING_FIELDS,
            TaskState.FILLING,
            TaskState.AWAITING_APPROVAL,
            TaskState.AWAITING_SUBMIT_APPROVAL,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }
    ),
    TaskState.AWAITING_APPROVAL: frozenset(
        {
            TaskState.SUBMITTING,
            TaskState.FILLING,
            TaskState.CANCELLED,
            TaskState.FAILED,
        }
    ),
    TaskState.AWAITING_SUBMIT_APPROVAL: frozenset(
        {
            TaskState.SUBMITTING,
            TaskState.VERIFYING,
            TaskState.SUBMITTED,
            TaskState.FILLING,
            TaskState.CANCELLED,
            TaskState.FAILED,
        }
    ),
    TaskState.SUBMITTING: frozenset(
        {TaskState.VERIFYING, TaskState.SUBMITTED, TaskState.FAILED}
    ),
    TaskState.VERIFYING: frozenset({TaskState.SUBMITTED, TaskState.FAILED}),
    TaskState.SUBMITTED: frozenset(),
    TaskState.FAILED: frozenset(),
    TaskState.CANCELLED: frozenset(),
}


def _extract_jwt_subject(authorization: Optional[str]) -> Optional[str]:
    """Extract `user_id` or `sub` claim from a Bearer JWT payload if present."""
    if not authorization:
        return None
    parts = authorization.strip().split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    segments = parts[1].strip().split(".")
    if len(segments) != 3:
        return None
    try:
        padded = segments[1] + "=" * (-len(segments[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        if isinstance(claims, dict):
            val = claims.get("user_id") or claims.get("sub")
            return str(val).strip() if val is not None else None
    except Exception:
        return None
    return None


def verify_caller_ownership(
    requested_user_id: str | uuid.UUID,
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> None:
    """Enforce caller ownership against X-User-Id header or JWT sub claim (PRD FR-005)."""
    target_id = str(requested_user_id).strip()
    caller_id: Optional[str] = x_user_id.strip() if isinstance(x_user_id, str) and x_user_id.strip() else None
    if caller_id is None:
        caller_id = _extract_jwt_subject(authorization)

    if caller_id is not None and caller_id != target_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: caller identity does not match requested user_id.",
        )


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
    current_step_index: int | None = Field(default=None, ge=0)
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
    user_id: uuid.UUID,
    payload: FamilyProfileCreate,
    db: AsyncSession = Depends(get_db),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
):
    verify_caller_ownership(str(user_id), x_user_id=x_user_id, authorization=authorization)

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
    user_id: uuid.UUID,
    profile_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
):
    verify_caller_ownership(str(user_id), x_user_id=x_user_id, authorization=authorization)

    stmt = select(AgentMemory).where(AgentMemory.user_id == user_id)
    if profile_id:
        stmt = stmt.where(AgentMemory.profile_id == profile_id)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.put("/users/{user_id}/memory")
async def upsert_memory(
    user_id: uuid.UUID,
    payload: MemoryUpsert,
    db: AsyncSession = Depends(get_db),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
):
    verify_caller_ownership(str(user_id), x_user_id=x_user_id, authorization=authorization)

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
            "support_tier": "blocked",
            "reason": "Invalid or unparseable domain/URL.",
        }

    # Reject punycode / IDN homograph domains.
    labels = canonical_domain.split(".")
    if any(lbl.startswith("xn--") for lbl in labels) or not canonical_domain.isascii():
        return {
            "is_official": False,
            "canonical_domain": canonical_domain,
            "support_tier": "blocked",
            "reason": f"Punycode or non-ASCII homograph domain '{canonical_domain}' is blocked by Official Domain Shield.",
        }

    result = await db.execute(
        select(ApprovedPortal).where(
            ApprovedPortal.domain == canonical_domain,
            ApprovedPortal.is_active == True,
        )
    )
    portal = result.scalar_one_or_none()
    if portal:
        is_gov_suffix = canonical_domain.endswith((".gov.in", ".nic.in"))
        return {
            "is_official": True,
            "canonical_domain": canonical_domain,
            "support_tier": "verified_official" if is_gov_suffix else "known_portal",
            "reason": f"Verified portal '{portal.portal_name}' in approved_portals registry.",
        }

    return {
        "is_official": False,
        "canonical_domain": canonical_domain,
        "support_tier": "blocked",
        "reason": f"Domain '{canonical_domain}' is not registered in the approved_portals registry.",
    }


# --- 5. Task Lifecycle, Read, History & Safe-Pause Checkpoints ---
@router.post("/tasks", status_code=status.HTTP_201_CREATED)
async def create_task(
    payload: TaskCreate,
    db: AsyncSession = Depends(get_db),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
):
    verify_caller_ownership(str(payload.user_id), x_user_id=x_user_id, authorization=authorization)

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
        "goal": task.goal_text,
        "user_goal": task.goal_text,
        "state": task.status,
        "status": task.status,
        "current_step_index": task.current_step_index,
        "checkpoint_state": task.checkpoint_state,
        "application_ref_no": task.application_ref_no,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
    }


@router.get("/tasks/{task_id}")
async def get_task(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
):
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    verify_caller_ownership(str(task.user_id), x_user_id=x_user_id, authorization=authorization)

    return {
        "id": task.id,
        "user_id": task.user_id,
        "profile_id": task.profile_id,
        "portal_id": task.portal_id,
        "goal": task.goal_text,
        "state": task.status,
        "current_step_index": task.current_step_index,
        "checkpoint_state": redact_dict(task.checkpoint_state),
        "application_ref_no": (
            redact_sensitive_text(task.application_ref_no)
            if task.application_ref_no is not None
            else None
        ),
        "created_at": task.created_at,
        "updated_at": task.updated_at,
    }


@router.get("/tasks/{task_id}/history")
async def get_task_history(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
):
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    verify_caller_ownership(str(task.user_id), x_user_id=x_user_id, authorization=authorization)

    stmt = (
        select(ToolAuditLog)
        .where(ToolAuditLog.task_id == task_id)
        .order_by(ToolAuditLog.created_at.asc())
    )
    entries = (await db.execute(stmt)).scalars().all()
    return {
        "task_id": task.id,
        "history": [
            {
                "id": entry.id,
                "task_id": entry.task_id,
                "tool_name": entry.tool_name,
                "status": entry.status,
                "sanitized_input": redact_dict(entry.sanitized_input),
                "sanitized_output": redact_dict(entry.sanitized_output),
                "created_at": entry.created_at,
            }
            for entry in entries
        ],
    }


@router.patch("/tasks/{task_id}/checkpoint")
async def save_task_checkpoint(
    task_id: uuid.UUID,
    payload: TaskCheckpointUpdate,
    db: AsyncSession = Depends(get_db),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
):
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    verify_caller_ownership(str(task.user_id), x_user_id=x_user_id, authorization=authorization)

    if payload.status is not None:
        try:
            current_state = TaskState(task.status)
        except ValueError:
            current_state = None

        if current_state is not None:
            if current_state in TERMINAL_TASK_STATES and payload.status != current_state:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Terminal state '{current_state.value}' cannot transition to '{payload.status.value}'.",
                )
            if payload.status != current_state:
                allowed = ALLOWED_STATE_TRANSITIONS.get(current_state, frozenset())
                if payload.status not in allowed:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"Invalid state transition from '{current_state.value}' to '{payload.status.value}'.",
                    )

        task.status = payload.status.value

    if payload.current_step_index is not None:
        task.current_step_index = payload.current_step_index
    if payload.checkpoint_state is not None:
        task.checkpoint_state = redact_dict(payload.checkpoint_state)
    if payload.application_ref_no is not None:
        task.application_ref_no = redact_sensitive_text(payload.application_ref_no)

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
