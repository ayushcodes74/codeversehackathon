"""Authentication helpers for Anubhav-owned API routes.

Bearer tokens are verified locally with HS256 and an environment-provided secret.
The development identity header is opt-in and is never accepted in production.
"""

import base64
import hashlib
import hmac
import json
import time
import uuid

from fastapi import HTTPException, status

from app.core.config import get_settings


def _decode_segment(segment: str) -> bytes:
    padded = segment + "=" * (-len(segment) % 4)
    return base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)


def _verify_bearer_jwt(authorization: str) -> uuid.UUID:
    parts = authorization.strip().split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization header; expected a Bearer token.",
        )
    segments = parts[1].strip().split(".")
    if len(segments) != 3 or any(not item for item in segments):
        raise HTTPException(status_code=401, detail="Malformed bearer token.")

    settings = get_settings()
    secret_value = (
        settings.jwt_secret_key.get_secret_value()
        if settings.jwt_secret_key is not None
        else ""
    )
    if len(secret_value.encode("utf-8")) < 32:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="JWT authentication is not configured with a sufficiently long secret.",
        )

    header_segment, payload_segment, signature_segment = segments
    try:
        header = json.loads(_decode_segment(header_segment))
        payload = json.loads(_decode_segment(payload_segment))
        signature = _decode_segment(signature_segment)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=401, detail="Malformed bearer token.") from exc

    if not isinstance(header, dict) or header.get("alg") != "HS256":
        raise HTTPException(status_code=401, detail="Unsupported bearer token algorithm.")
    signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
    expected = hmac.new(secret_value.encode("utf-8"), signing_input, hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401, detail="Invalid bearer token signature.")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=401, detail="Invalid bearer token payload.")

    expiry = payload.get("exp")
    if isinstance(expiry, bool) or not isinstance(expiry, (int, float)) or expiry <= time.time():
        raise HTTPException(status_code=401, detail="Bearer token is expired or has no valid expiry.")
    not_before = payload.get("nbf")
    if not_before is not None and (
        isinstance(not_before, bool)
        or not isinstance(not_before, (int, float))
        or not_before > time.time()
    ):
        raise HTTPException(status_code=401, detail="Bearer token is not active.")

    subject = payload.get("sub") or payload.get("user_id")
    if payload.get("sub") and payload.get("user_id") and payload["sub"] != payload["user_id"]:
        raise HTTPException(status_code=401, detail="Bearer token identity claims disagree.")
    try:
        return uuid.UUID(str(subject))
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(status_code=401, detail="Bearer token subject is invalid.") from exc


def resolve_authenticated_user_id(
    *,
    x_user_id: str | None,
    authorization: str | None,
    expected_user_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Resolve the caller from a verified token or explicitly enabled dev header."""
    settings = get_settings()
    if authorization and authorization.strip():
        caller_id = _verify_bearer_jwt(authorization)
    elif (
        settings.app_env in ("development", "test")
        and settings.allow_dev_user_header
        and x_user_id
    ):
        try:
            caller_id = uuid.UUID(x_user_id.strip())
        except ValueError as exc:
            raise HTTPException(status_code=401, detail="Development user ID is invalid.") from exc
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )

    if expected_user_id is not None and caller_id != expected_user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Caller identity does not match the requested user.",
        )
    return caller_id
