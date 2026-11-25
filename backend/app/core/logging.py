"""Centralized logging configuration integrated with PII log redaction."""

import logging
from contextvars import ContextVar

from app.policies.log_redactor import install_log_redaction

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")


class RequestIdLogFilter(logging.Filter):
    """Attach the active request ID from ContextVar to every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_ctx.get()
        return True


def configure_logging(log_level: str = "INFO") -> None:
    """Configure application logging level, format, request ID filter, and PII redaction."""
    level = getattr(logging, log_level.upper(), logging.INFO)
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    if not root_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)s | %(name)s | req=%(request_id)s | %(message)s"
            )
        )
        root_logger.addHandler(handler)
    else:
        for handler in root_logger.handlers:
            if handler.formatter is None:
                handler.setFormatter(
                    logging.Formatter(
                        "%(asctime)s | %(levelname)s | %(name)s | req=%(request_id)s | %(message)s"
                    )
                )

    if not any(isinstance(f, RequestIdLogFilter) for f in root_logger.filters):
        root_logger.addFilter(RequestIdLogFilter())

    # Ensure existing PII redaction filter stays attached to root, FastAPI, and Uvicorn loggers.
    install_log_redaction()
