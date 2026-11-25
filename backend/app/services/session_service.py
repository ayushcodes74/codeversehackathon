"""Process-local SessionStore and ownership-enforced session service (POC).

POC Assumption:
    Because Ayush's PostgreSQL schema (backend/app/models.py) defines no Session table,
    assistant sessions are kept in a process-local ``InMemorySessionStore`` behind the
    ``SessionStore`` interface. Sessions are lost on server restart and are not shared
    across multiple backend worker processes. No Redis or new DB tables are used.
"""

import logging
import threading
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone

from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.db_routes import _extract_canonical_hostname
from app.core.exceptions import AppError, ConflictError, NotFoundError
from app.models import ApprovedPortal, FamilyProfile, User
from app.schemas.session import (
    PortalSupportTier,
    SessionCloseResponse,
    SessionCreateRequest,
    SessionPortalContext,
    SessionResponse,
    SessionStatus,
    SessionUpdateRequest,
)
from app.services.auth_context import resolve_authenticated_user_id

logger = logging.getLogger("app.services.session")

_DEFAULT_SESSION_TTL = timedelta(hours=24)


class SessionStore(ABC):
    """Minimal storage interface for ephemeral assistant session metadata."""

    @abstractmethod
    def create(self, session: SessionResponse) -> SessionResponse:
        """Persist a new session record."""

    @abstractmethod
    def get(self, session_id: uuid.UUID) -> SessionResponse | None:
        """Retrieve a session record by ID, or None if missing/expired."""

    @abstractmethod
    def save(self, session: SessionResponse) -> SessionResponse:
        """Update an existing session record."""

    @abstractmethod
    def delete(self, session_id: uuid.UUID) -> None:
        """Remove a session record if present."""


class InMemorySessionStore(SessionStore):
    """Thread-safe process-local session store.

    Note: Sessions are ephemeral to a single Python process; restarts clear all
    sessions and multi-worker deployments do not share state.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[uuid.UUID, SessionResponse] = {}

    def create(self, session: SessionResponse) -> SessionResponse:
        with self._lock:
            self._sessions[session.session_id] = session
            return session.model_copy(deep=True)

    def get(self, session_id: uuid.UUID) -> SessionResponse | None:
        now = datetime.now(timezone.utc)
        with self._lock:
            record = self._sessions.get(session_id)
            if record is None:
                return None
            if (
                record.expires_at is not None
                and record.expires_at <= now
                and record.status != SessionStatus.CLOSED
            ):
                expired = record.model_copy(
                    update={"status": SessionStatus.EXPIRED, "updated_at": now}
                )
                self._sessions[session_id] = expired
                return expired.model_copy(deep=True)
            return record.model_copy(deep=True)

    def save(self, session: SessionResponse) -> SessionResponse:
        with self._lock:
            self._sessions[session.session_id] = session
            return session.model_copy(deep=True)

    def delete(self, session_id: uuid.UUID) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)


_default_session_store = InMemorySessionStore()


def get_session_store() -> SessionStore:
    """Return the process-local SessionStore singleton."""
    return _default_session_store


async def resolve_portal_context(
    db: AsyncSession, raw_url: str
) -> SessionPortalContext:
    """Evaluate a browser URL against Domain Shield and ApprovedPortal without storing query strings."""
    canonical_domain = _extract_canonical_hostname(raw_url)
    if not canonical_domain or "." not in canonical_domain:
        return SessionPortalContext(
            domain=canonical_domain or "invalid",
            is_official=False,
            support_tier=PortalSupportTier.BLOCKED,
            reason="Invalid or unparseable domain/URL.",
        )

    labels = canonical_domain.split(".")
    if any(lbl.startswith("xn--") for lbl in labels) or not canonical_domain.isascii():
        return SessionPortalContext(
            domain=canonical_domain,
            is_official=False,
            support_tier=PortalSupportTier.BLOCKED,
            reason=(
                f"Punycode or non-ASCII homograph domain '{canonical_domain}' "
                "is blocked by Official Domain Shield."
            ),
        )

    result = await db.execute(
        select(ApprovedPortal).where(
            ApprovedPortal.domain == canonical_domain,
            ApprovedPortal.is_active == True,
        )
    )
    portal = result.scalar_one_or_none()
    if portal:
        is_gov_suffix = canonical_domain.endswith((".gov.in", ".nic.in"))
        return SessionPortalContext(
            domain=canonical_domain,
            is_official=True,
            support_tier=(
                PortalSupportTier.VERIFIED_OFFICIAL
                if is_gov_suffix
                else PortalSupportTier.KNOWN_PORTAL
            ),
            portal_name=portal.portal_name,
            reason=f"Verified portal '{portal.portal_name}' in approved_portals registry.",
        )

    return SessionPortalContext(
        domain=canonical_domain,
        is_official=False,
        support_tier=PortalSupportTier.BLOCKED,
        reason=(
            f"Domain '{canonical_domain}' is not registered in the approved_portals registry."
        ),
    )


async def _resolve_owned_profile(
    db: AsyncSession, user_id: uuid.UUID, profile_id: uuid.UUID | None
) -> FamilyProfile:
    if profile_id is not None:
        profile = await db.get(FamilyProfile, profile_id)
        if profile is None:
            raise NotFoundError("Family profile not found.")
        if profile.user_id != user_id:
            raise AppError(
                message="Forbidden: profile_id does not belong to this user.",
                status_code=status.HTTP_403_FORBIDDEN,
                code="forbidden_profile_access",
            )
        return profile

    stmt = (
        select(FamilyProfile)
        .where(FamilyProfile.user_id == user_id)
        .order_by(FamilyProfile.is_default.desc(), FamilyProfile.created_at.asc())
    )
    default_profile = (await db.execute(stmt)).scalars().first()
    if default_profile is None:
        raise NotFoundError("No family profile found for this user.")
    return default_profile


def _get_owned_session_record(
    store: SessionStore,
    *,
    session_id: uuid.UUID,
    caller_user_id: uuid.UUID,
) -> SessionResponse:
    record = store.get(session_id)
    if record is None:
        raise NotFoundError("Session not found.")
    if record.user_id != caller_user_id:
        raise AppError(
            message="Forbidden: caller identity does not own this session.",
            status_code=status.HTTP_403_FORBIDDEN,
            code="forbidden_session_access",
        )
    return record


async def create_session(
    db: AsyncSession,
    *,
    payload: SessionCreateRequest,
    x_user_id: str | None,
    authorization: str | None,
    store: SessionStore | None = None,
) -> SessionResponse:
    """Initialize a process-local assistant session for the authenticated user."""
    active_store = store or get_session_store()
    caller_user_id = resolve_authenticated_user_id(
        x_user_id=x_user_id,
        authorization=authorization,
        expected_user_id=payload.user_id,
    )

    user = await db.get(User, caller_user_id)
    if user is None:
        raise NotFoundError("Authenticated user record not found.")

    profile = await _resolve_owned_profile(db, caller_user_id, payload.profile_id)
    portal_context = (
        await resolve_portal_context(db, payload.active_url)
        if payload.active_url
        else None
    )

    now = datetime.now(timezone.utc)
    record = SessionResponse(
        session_id=uuid.uuid4(),
        user_id=caller_user_id,
        active_profile_id=profile.id,
        profile_scope=payload.profile_scope,
        preferred_language=payload.preferred_language,
        status=SessionStatus.ACTIVE,
        vault_unlocked=False,
        active_task_id=None,
        portal_context=portal_context,
        created_at=now,
        updated_at=now,
        expires_at=now + _DEFAULT_SESSION_TTL,
    )
    saved = active_store.create(record)
    logger.info(
        "Created process-local session id=%s user_id=%s profile_id=%s",
        saved.session_id,
        caller_user_id,
        profile.id,
    )
    return saved


async def get_session(
    *,
    session_id: uuid.UUID,
    x_user_id: str | None,
    authorization: str | None,
    store: SessionStore | None = None,
) -> SessionResponse:
    """Retrieve a process-local session owned by the authenticated caller."""
    active_store = store or get_session_store()
    caller_user_id = resolve_authenticated_user_id(
        x_user_id=x_user_id,
        authorization=authorization,
    )
    return _get_owned_session_record(
        active_store,
        session_id=session_id,
        caller_user_id=caller_user_id,
    )


async def update_session(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    payload: SessionUpdateRequest,
    x_user_id: str | None,
    authorization: str | None,
    store: SessionStore | None = None,
) -> SessionResponse:
    """Update active profile, language, sanitized domain context, or vault flag."""
    active_store = store or get_session_store()
    caller_user_id = resolve_authenticated_user_id(
        x_user_id=x_user_id,
        authorization=authorization,
    )
    record = _get_owned_session_record(
        active_store,
        session_id=session_id,
        caller_user_id=caller_user_id,
    )

    if record.status in (SessionStatus.CLOSED, SessionStatus.EXPIRED):
        raise ConflictError(
            f"Cannot update session in '{record.status.value}' state."
        )

    updates: dict[str, object] = {"updated_at": datetime.now(timezone.utc)}
    if payload.active_profile_id is not None:
        profile = await _resolve_owned_profile(
            db, caller_user_id, payload.active_profile_id
        )
        updates["active_profile_id"] = profile.id
    if payload.profile_scope is not None:
        updates["profile_scope"] = payload.profile_scope
    if payload.preferred_language is not None:
        updates["preferred_language"] = payload.preferred_language
    if payload.active_url is not None:
        updates["portal_context"] = await resolve_portal_context(
            db, payload.active_url
        )
    if payload.vault_unlocked is not None:
        updates["vault_unlocked"] = payload.vault_unlocked
    if payload.status is not None:
        updates["status"] = payload.status

    updated = active_store.save(record.model_copy(update=updates))
    logger.info(
        "Updated process-local session id=%s status=%s",
        updated.session_id,
        updated.status.value,
    )
    return updated


async def close_session(
    *,
    session_id: uuid.UUID,
    x_user_id: str | None,
    authorization: str | None,
    store: SessionStore | None = None,
) -> SessionCloseResponse:
    """Close an owned session and clear ephemeral context metadata."""
    active_store = store or get_session_store()
    caller_user_id = resolve_authenticated_user_id(
        x_user_id=x_user_id,
        authorization=authorization,
    )
    record = _get_owned_session_record(
        active_store,
        session_id=session_id,
        caller_user_id=caller_user_id,
    )

    now = datetime.now(timezone.utc)
    if record.status != SessionStatus.CLOSED:
        active_store.save(
            record.model_copy(
                update={
                    "status": SessionStatus.CLOSED,
                    "vault_unlocked": False,
                    "portal_context": None,
                    "updated_at": now,
                }
            )
        )
        logger.info("Closed process-local session id=%s", session_id)

    return SessionCloseResponse(
        session_id=session_id,
        status=SessionStatus.CLOSED,
        closed_at=now,
    )
