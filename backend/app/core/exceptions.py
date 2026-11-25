"""Common application exceptions and global FastAPI error handlers."""

import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import AppSettings
from app.core.logging import request_id_ctx
from app.policies.log_redactor import redact_dict, redact_sensitive_text

logger = logging.getLogger("app.errors")


class AppError(Exception):
    """Base application exception producing a structured JSON error response."""

    def __init__(
        self,
        message: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        code: str = "app_error",
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
        self.details = details


class BadRequestError(AppError):
    """Raised when a client request violates domain or input preconditions."""

    def __init__(self, message: str = "Bad request.", details: Any = None) -> None:
        super().__init__(
            message=message,
            status_code=status.HTTP_400_BAD_REQUEST,
            code="bad_request",
            details=details,
        )


class NotFoundError(AppError):
    """Raised when a requested resource does not exist."""

    def __init__(self, message: str = "Resource not found.", details: Any = None) -> None:
        super().__init__(
            message=message,
            status_code=status.HTTP_404_NOT_FOUND,
            code="not_found",
            details=details,
        )


class ConflictError(AppError):
    """Raised when an operation conflicts with existing state."""

    def __init__(self, message: str = "Resource conflict.", details: Any = None) -> None:
        super().__init__(
            message=message,
            status_code=status.HTTP_409_CONFLICT,
            code="conflict",
            details=details,
        )


class ServiceUnavailableError(AppError):
    """Raised when a required downstream dependency is unavailable."""

    def __init__(
        self, message: str = "Service temporarily unavailable.", details: Any = None
    ) -> None:
        super().__init__(
            message=message,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="service_unavailable",
            details=details,
        )


def _get_request_id(request: Request) -> str:
    state_id = getattr(request.state, "request_id", None)
    if isinstance(state_id, str) and state_id:
        return state_id
    ctx_id = request_id_ctx.get()
    return ctx_id if ctx_id and ctx_id != "-" else "unknown"


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_sensitive_text(value)
    if isinstance(value, (dict, list, tuple)):
        return redact_dict(value)
    return value


def _build_error_body(
    *,
    code: str,
    message: str,
    request_id: str,
    detail: Any = None,
    details: Any = None,
) -> dict[str, Any]:
    error_obj: dict[str, Any] = {
        "code": code,
        "message": message,
        "request_id": request_id,
    }
    if details is not None:
        error_obj["details"] = details

    return {
        "detail": detail if detail is not None else message,
        "error": error_obj,
    }


def register_exception_handlers(app: FastAPI, settings: AppSettings) -> None:
    """Register uniform JSON exception handlers on the FastAPI application."""

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        request_id = _get_request_id(request)
        safe_message = redact_sensitive_text(exc.message)
        safe_details = _sanitize_value(exc.details)
        return JSONResponse(
            status_code=exc.status_code,
            content=_build_error_body(
                code=exc.code,
                message=safe_message,
                request_id=request_id,
                detail=safe_message,
                details=safe_details,
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        request_id = _get_request_id(request)
        safe_detail = _sanitize_value(exc.detail)
        message = (
            safe_detail
            if isinstance(safe_detail, str)
            else f"HTTP {exc.status_code} error."
        )
        return JSONResponse(
            status_code=exc.status_code,
            headers=exc.headers,
            content=_build_error_body(
                code=f"http_{exc.status_code}",
                message=message,
                request_id=request_id,
                detail=safe_detail,
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        request_id = _get_request_id(request)
        safe_errors = _sanitize_value(exc.errors())
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=_build_error_body(
                code="validation_error",
                message="Request validation failed.",
                request_id=request_id,
                detail=safe_errors,
                details=safe_errors,
            ),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        request_id = _get_request_id(request)
        safe_exc = redact_sensitive_text(str(exc))
        logger.exception(
            "Unhandled exception on %s %s: %s",
            request.method,
            request.url.path,
            safe_exc,
        )
        details = {"exception": safe_exc} if settings.expose_error_details else None
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_build_error_body(
                code="internal_server_error",
                message="An unexpected internal server error occurred.",
                request_id=request_id,
                detail="Internal server error.",
                details=details,
            ),
        )
