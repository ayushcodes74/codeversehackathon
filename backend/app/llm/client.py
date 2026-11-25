"""Provider-neutral async LLM client interface with bounded timeouts and structured parsing."""

import asyncio
import json
import logging
import time
from abc import ABC, abstractmethod
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.llm.config import LLMSettings, get_llm_settings
from app.llm.exceptions import (
    LLMError,
    LLMNotConfiguredError,
    LLMProviderError,
    LLMStructuredOutputError,
    LLMTimeoutError,
)
from app.llm.types import LLMRequest, LLMResponse
from app.policies.log_redactor import redact_sensitive_text

logger = logging.getLogger("app.llm")

T = TypeVar("T", bound=BaseModel)
_MAX_TIMEOUT_SECONDS = 120.0


def _strip_json_fences(raw_text: str) -> str:
    """Strip optional Markdown code fences around a JSON payload."""
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if len(lines) >= 2 and lines[0].startswith("```") and lines[-1].strip() == "```":
            return "\n".join(lines[1:-1]).strip()
    return cleaned


class BaseLLMClient(ABC):
    """Abstract provider-neutral LLM client with bounded timeouts and schema validation.

    Subclasses implement ``_complete`` for a chosen vendor without altering tool
    permissions or executing actions directly from model output.
    """

    def __init__(self, settings: LLMSettings | None = None) -> None:
        self.settings = settings or get_llm_settings()

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Canonical identifier of the active LLM provider."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Configured model identifier for the active LLM provider."""

    def _resolve_timeout(
        self, request: LLMRequest, timeout_seconds: float | None
    ) -> float:
        candidate = (
            timeout_seconds
            if timeout_seconds is not None
            else (
                request.timeout_seconds
                if request.timeout_seconds is not None
                else self.settings.llm_timeout_seconds
            )
        )
        return max(0.1, min(float(candidate), _MAX_TIMEOUT_SECONDS))

    async def generate(
        self,
        request: LLMRequest,
        *,
        timeout_seconds: float | None = None,
    ) -> LLMResponse[Any]:
        """Run a bounded text completion against the configured provider."""
        effective_timeout = self._resolve_timeout(request, timeout_seconds)
        return await self._execute_bounded(
            request=request,
            response_model=None,
            timeout=effective_timeout,
        )

    async def generate_structured(
        self,
        request: LLMRequest,
        response_model: type[T],
        *,
        timeout_seconds: float | None = None,
    ) -> LLMResponse[T]:
        """Run a bounded completion and validate output against ``response_model``."""
        effective_timeout = self._resolve_timeout(request, timeout_seconds)
        response = await self._execute_bounded(
            request=request,
            response_model=response_model,
            timeout=effective_timeout,
        )

        if response.parsed is not None:
            try:
                validated = response_model.model_validate(response.parsed)
            except ValidationError as exc:
                raise LLMStructuredOutputError(
                    f"Structured LLM output failed {response_model.__name__} validation."
                ) from exc
            return response.model_copy(update={"parsed": validated})

        json_candidate = _strip_json_fences(response.raw_text)
        if not json_candidate:
            raise LLMStructuredOutputError("LLM returned an empty structured output.")

        try:
            payload = json.loads(json_candidate)
            validated = response_model.model_validate(payload)
        except json.JSONDecodeError as exc:
            raise LLMStructuredOutputError(
                f"LLM structured output is not valid JSON: {exc.msg}"
            ) from exc
        except ValidationError as exc:
            raise LLMStructuredOutputError(
                f"LLM structured output failed {response_model.__name__} validation."
            ) from exc

        return response.model_copy(update={"parsed": validated})

    async def _execute_bounded(
        self,
        *,
        request: LLMRequest,
        response_model: type[BaseModel] | None,
        timeout: float,
    ) -> LLMResponse[Any]:
        started = time.perf_counter()
        try:
            response = await asyncio.wait_for(
                self._complete(request, response_model=response_model),
                timeout=timeout,
            )
        except (TimeoutError, asyncio.TimeoutError) as exc:
            logger.warning(
                "LLM request timed out provider=%s model=%s timeout=%.2fs",
                self.provider_name,
                self.model_name,
                timeout,
            )
            raise LLMTimeoutError(
                f"LLM request exceeded {timeout:.1f}s timeout."
            ) from exc
        except LLMError:
            raise
        except Exception as exc:
            safe_err = redact_sensitive_text(str(exc))
            logger.error(
                "LLM provider failure provider=%s model=%s error=%s",
                self.provider_name,
                self.model_name,
                safe_err,
            )
            raise LLMProviderError(f"LLM provider failure: {safe_err}") from exc

        if response.latency_ms is None:
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            response = response.model_copy(update={"latency_ms": elapsed_ms})
        return response

    @abstractmethod
    async def _complete(
        self,
        request: LLMRequest,
        *,
        response_model: type[BaseModel] | None = None,
    ) -> LLMResponse[Any]:
        """Execute the provider-specific completion call."""


class UnconfiguredLLMClient(BaseLLMClient):
    """Safe placeholder client returned until the team selects a provider and model."""

    @property
    def provider_name(self) -> str:
        return self.settings.llm_provider or "unconfigured"

    @property
    def model_name(self) -> str:
        return self.settings.llm_model or "unconfigured"

    async def _complete(
        self,
        request: LLMRequest,
        *,
        response_model: type[BaseModel] | None = None,
    ) -> LLMResponse[Any]:
        _ = (request, response_model)
        raise LLMNotConfiguredError()


def get_llm_client(settings: LLMSettings | None = None) -> BaseLLMClient:
    """Return the configured LLM client, or UnconfiguredLLMClient if none is selected."""
    active_settings = settings or get_llm_settings()
    if not active_settings.is_configured:
        return UnconfiguredLLMClient(active_settings)

    raise LLMNotConfiguredError(
        f"No adapter registered for configured LLM provider '{active_settings.llm_provider}'. "
        "Register the team-approved provider adapter in app.llm.client."
    )
