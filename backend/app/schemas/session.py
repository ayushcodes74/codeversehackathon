"""Request and response schemas for frontend-facing assistant session APIs."""

import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.common import (
    ProfileScope,
    normalize_language_code,
    validate_http_url,
)


class ClientType(str, Enum):
    """Supported client surface types connecting to the backend."""

    CHROME_EXTENSION = "chrome_extension"
    WEB_APP = "web_app"
    OTHER = "other"


class SessionStatus(str, Enum):
    """Lifecycle status of an assistant interaction session."""

    ACTIVE = "active"
    PAUSED = "paused"
    EXPIRED = "expired"
    CLOSED = "closed"


class PortalSupportTier(str, Enum):
    """Domain Shield trust classification matching /api/v1/portals/verify."""

    VERIFIED_OFFICIAL = "verified_official"
    KNOWN_PORTAL = "known_portal"
    BLOCKED_UNTRUSTED = "blocked_untrusted"


class SessionPortalContext(BaseModel):
    """Domain Shield verification summary for the active browser tab."""

    domain: str = Field(..., min_length=1, max_length=255)
    is_official: bool
    support_tier: PortalSupportTier
    portal_name: str | None = Field(default=None, max_length=255)
    reason: str | None = Field(default=None, max_length=1000)


class SessionCreateRequest(BaseModel):
    """Payload to initialize a frontend or Chrome extension assistant session."""

    user_id: uuid.UUID | None = None
    profile_id: uuid.UUID | None = None
    profile_scope: ProfileScope = ProfileScope.SELF
    preferred_language: str = "en"
    active_url: str | None = Field(default=None, max_length=2048)
    client_type: ClientType = ClientType.CHROME_EXTENSION
    client_version: str | None = Field(default=None, max_length=32)

    @field_validator("preferred_language", mode="after")
    @classmethod
    def _validate_language(cls, value: str) -> str:
        return normalize_language_code(value)

    @field_validator("active_url", mode="after")
    @classmethod
    def _validate_active_url(cls, value: str | None) -> str | None:
        return validate_http_url(value, "active_url")

    @field_validator("client_version", mode="after")
    @classmethod
    def _strip_client_version(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class SessionUpdateRequest(BaseModel):
    """Partial update for active profile scope, language, URL context, or vault state."""

    active_profile_id: uuid.UUID | None = None
    profile_scope: ProfileScope | None = None
    preferred_language: str | None = None
    active_url: str | None = Field(default=None, max_length=2048)
    # Metadata flag only; passphrases and keys never leave the client Local Vault.
    vault_unlocked: bool | None = None
    status: SessionStatus | None = None

    @field_validator("preferred_language", mode="after")
    @classmethod
    def _validate_language(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_language_code(value)

    @field_validator("active_url", mode="after")
    @classmethod
    def _validate_active_url(cls, value: str | None) -> str | None:
        return validate_http_url(value, "active_url")

    @model_validator(mode="after")
    def _require_at_least_one_field(self) -> "SessionUpdateRequest":
        if all(
            field is None
            for field in (
                self.active_profile_id,
                self.profile_scope,
                self.preferred_language,
                self.active_url,
                self.vault_unlocked,
                self.status,
            )
        ):
            raise ValueError("At least one session field must be provided for update.")
        return self


class SessionResponse(BaseModel):
    """Frontend-facing representation of an active or paused assistant session."""

    session_id: uuid.UUID
    user_id: uuid.UUID | None = None
    active_profile_id: uuid.UUID | None = None
    profile_scope: ProfileScope = ProfileScope.SELF
    preferred_language: str = "en"
    status: SessionStatus
    vault_unlocked: bool = False
    active_task_id: uuid.UUID | None = None
    portal_context: SessionPortalContext | None = None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class SessionCloseResponse(BaseModel):
    """Confirmation payload returned when a session is explicitly closed."""

    session_id: uuid.UUID
    status: SessionStatus = SessionStatus.CLOSED
    closed_at: datetime
