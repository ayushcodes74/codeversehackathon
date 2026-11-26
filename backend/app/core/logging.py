"""Centralized logging configuration integrated with PII log redaction."""

import logging
from contextvars import ContextVar

from app.policies.log_redactor import SensitiveDataLogFilter, install_log_redaction

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")


class RequestIdLogFilter(logging.Filter):
    """Attach the active request ID from ContextVar to every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_ctx.get()
        return True


class SafeRequestIdFormatter(logging.Formatter):
    """Formatter ensuring request_id is always present on every log record."""

    def format(self, record: logging.LogRecord) -> str:
        if not hasattr(record, "request_id"):
            record.request_id = request_id_ctx.get()
        return super().format(record)


def configure_logging(log_level: str = "INFO") -> None:
    """Configure application logging level, format, request ID filter, and PII redaction."""
    level = getattr(logging, log_level.upper(), logging.INFO)
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    req_filter = RequestIdLogFilter()
    formatter = SafeRequestIdFormatter(
        "%(asctime)s | %(levelname)s | %(name)s | req=%(request_id)s | %(message)s"
    )

    if not root_logger.handlers:
        handler = logging.StreamHandler()
        handler.addFilter(req_filter)
        handler.setFormatter(formatter)
        root_logger.addHandler(handler)
    else:
        for handler in root_logger.handlers:
            if not any(isinstance(f, RequestIdLogFilter) for f in handler.filters):
                handler.addFilter(req_filter)
            if handler.formatter is None:
                handler.setFormatter(formatter)

    if not any(isinstance(f, RequestIdLogFilter) for f in root_logger.filters):
        root_logger.addFilter(req_filter)
    # Ensure existing PII redaction filter stays attached to root, FastAPI, and Uvicorn loggers.
    install_log_redaction()

    # Attach RequestIdLogFilter and SensitiveDataLogFilter to root handlers so child
    # loggers propagating to root always receive request_id and PII redaction.
    request_filter = RequestIdLogFilter()
    pii_filter = SensitiveDataLogFilter()
    for handler in root_logger.handlers:
        if not any(isinstance(f, RequestIdLogFilter) for f in handler.filters):
            handler.addFilter(request_filter)
        if not any(isinstance(f, SensitiveDataLogFilter) for f in handler.filters):
            handler.addFilter(pii_filter)
