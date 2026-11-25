"""Logging filter and regex redactor to prevent raw PII/ID leaks in server logs."""

import logging
import re
from typing import Any

_PAN_RE = re.compile(r"(?<![A-Za-z0-9])[A-Z]{5}[0-9]{4}[A-Z](?![A-Za-z0-9])", re.IGNORECASE)
# Redact context-labeled account numbers (9-18 digits) before UIDs so labeled 12-digit accounts become [REDACTED_ACCT].
_LABELED_ACCT_RE = re.compile(
    r"((?:account|acct|a/c)\s*(?:no\.?|number)?[:.\-\s]*)[0-9]{9,18}(?!\d)",
    re.IGNORECASE,
)
# Match 13-18 digit sequences (contiguous or 4x4 spaced) before 12-digit UIDs so long IDs/VIDs aren't split.
_LONG_ACCT_RE = re.compile(r"(?<!\d)(?:\d{13,18}|\d{4}(?:[\s-]\d{4}){3})(?!\d)")
_UID_RE = re.compile(r"(?<!\d)\d{4}[\s-]?\d{4}[\s-]?\d{4}(?![\s-]?\d)")
_ACCT_RE = re.compile(r"(?<!\d)\d{11,18}(?!\d)")


def redact_sensitive_text(text: str) -> str:
    """Mask 12-digit UIDs, Indian PANs, and 11-18 digit (or labeled 9-18 digit) bank account numbers."""
    if not isinstance(text, str) or not text:
        return text

    redacted = _PAN_RE.sub("[REDACTED_PAN]", text)
    redacted = _LABELED_ACCT_RE.sub(r"\1[REDACTED_ACCT]", redacted)
    redacted = _LONG_ACCT_RE.sub("[REDACTED_ACCT]", redacted)
    redacted = _UID_RE.sub("[REDACTED_UID]", redacted)
    redacted = _ACCT_RE.sub("[REDACTED_ACCT]", redacted)
    return redacted


def redact_dict(data: Any) -> Any:
    """Recursively mask sensitive IDs in dicts, lists, tuples, and strings."""
    if isinstance(data, dict):
        return {
            (redact_sensitive_text(k) if isinstance(k, str) else k): redact_dict(v)
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [redact_dict(item) for item in data]
    if isinstance(data, tuple):
        return tuple(redact_dict(item) for item in data)
    if isinstance(data, str):
        return redact_sensitive_text(data)
    return data


class SensitiveDataLogFilter(logging.Filter):
    """Logging filter that redacts sensitive IDs from record messages and string args."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_sensitive_text(record.msg)

        if record.args:
            if isinstance(record.args, tuple):
                record.args = tuple(
                    redact_sensitive_text(arg) if isinstance(arg, str) else arg
                    for arg in record.args
                )
            elif isinstance(record.args, dict):
                record.args = {
                    key: redact_sensitive_text(val) if isinstance(val, str) else val
                    for key, val in record.args.items()
                }
            elif isinstance(record.args, str):
                record.args = redact_sensitive_text(record.args)

        return True


def install_log_redaction(
    logger_names: tuple[str, ...] = ("uvicorn", "uvicorn.access", "uvicorn.error", "fastapi"),
) -> None:
    """Attach SensitiveDataLogFilter to root and FastAPI/Uvicorn loggers idempotently."""
    redactor = SensitiveDataLogFilter()
    root_logger = logging.getLogger()
    if not any(isinstance(f, SensitiveDataLogFilter) for f in root_logger.filters):
        root_logger.addFilter(redactor)

    for name in logger_names:
        target_logger = logging.getLogger(name)
        if not any(isinstance(f, SensitiveDataLogFilter) for f in target_logger.filters):
            target_logger.addFilter(redactor)


install_log_redaction()
