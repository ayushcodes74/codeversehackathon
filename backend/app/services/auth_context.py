"""Verified caller identity resolution built on Ayush's db_routes auth helpers."""

import uuid

from fastapi import HTTPException, status

from app.api.db_routes import (
    _allow_dev_user_header,
    _verify_hs256_jwt,
    verify_caller_ownership,
)


def resolve_authenticated_user_id(
    *,
    x_user_id: str | None,
    authorization: str | None,
    expected_user_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Resolve and verify the caller's UUID strictly from auth context.

    Never trusts a client-supplied ``user_id`` without a matching verified Bearer HS256
    JWT (or dev ``X-User-Id`` header when Ayush's ``_allow_dev_user_header()`` allows it).
    """
    caller_raw: str | None = None
    if authorization and authorization.strip():
        caller_raw = _verify_hs256_jwt(authorization)
    elif _allow_dev_user_header() and isinstance(x_user_id, str) and x_user_id.strip():
        caller_raw = x_user_id.strip()

    if not caller_raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required: provide Authorization Bearer JWT.",
        )

    try:
        caller_uuid = uuid.UUID(caller_raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authenticated caller subject is not a valid user UUID.",
        ) from exc

    if expected_user_id is not None:
        verify_caller_ownership(
            str(expected_user_id),
            x_user_id=x_user_id,
            authorization=authorization,
        )

    return caller_uuid
