"""Provider-neutral LLM exception hierarchy integrated with AppError and PII redaction."""

from typing import Any

from fastapi import status

from app.core.exceptions import AppError
from app.policies.log_redactor import redact_sensitive_text


class LLMError(AppError):
    """Base error raised by LLM client adapters."""

    def __init__(
        self,
        message: str,
        status_code: int = status.HTTP_502_BAD_GATEWAY,
        code: str = "llm_error",
        details: Any = None,
    ) -> None:
        super().__init__(
            message=redact_sensitive_text(message),
            status_code=status_code,
            code=code,
            details=details,
        )


class LLMNotConfiguredError(LLMError):
    """Raised when an LLM call is attempted without a configured provider or model."""

    def __init__(
        self,
        message: str = (
            "LLM provider/model is not configured. Select a team-approved provider "
            "and set LLM_PROVIDER and LLM_MODEL in environment configuration."
        ),
        details: Any = None,
    ) -> None:
        super().__init__(
            message=message,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="llm_not_configured",
            details=details,
        )


class LLMTimeoutError(LLMError):
    """Raised when an LLM request exceeds its bounded timeout."""

    def __init__(
        self,
        message: str = "LLM provider request timed out.",
        details: Any = None,
    ) -> None:
        super().__init__(
            message=message,
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            code="llm_timeout",
            details=details,
        )


class LLMRateLimitError(LLMError):
    """Raised when the upstream LLM provider rejects a request due to rate limiting."""

    def __init__(
        self,
        message: str = "LLM provider rate limit exceeded.",
        details: Any = None,
    ) -> None:
        super().__init__(
            message=message,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="llm_rate_limit",
            details=details,
        )


class LLMProviderError(LLMError):
    """Raised when the upstream LLM provider returns an unexpected error."""

    def __init__(
        self,
        message: str = "LLM provider request failed.",
        details: Any = None,
    ) -> None:
        super().__init__(
            message=message,
            status_code=status.HTTP_502_BAD_GATEWAY,
            code="llm_provider_error",
            details=details,
        )


class LLMStructuredOutputError(LLMError):
    """Raised when LLM output is not valid JSON or fails target Pydantic validation."""

    def __init__(
        self,
        message: str = "LLM response did not match the expected structured output schema.",
        details: Any = None,
    ) -> None:
        super().__init__(
            message=message,
            status_code=status.HTTP_502_BAD_GATEWAY,
            code="llm_invalid_structured_output",
            details=details,
        )
