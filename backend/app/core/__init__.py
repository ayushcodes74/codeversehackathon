"""Core backend foundation utilities (configuration, logging, middleware, exceptions)."""

from app.core.config import AppSettings, get_settings, settings
from app.core.exceptions import (
    AppError,
    BadRequestError,
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
    register_exception_handlers,
)
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware, register_middlewares

__all__ = [
    "AppError",
    "AppSettings",
    "BadRequestError",
    "ConflictError",
    "NotFoundError",
    "RequestContextMiddleware",
    "ServiceUnavailableError",
    "configure_logging",
    "get_settings",
    "register_exception_handlers",
    "register_middlewares",
    "settings",
]
