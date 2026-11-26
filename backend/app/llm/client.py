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


class MockLLMClient(BaseLLMClient):
    """Deterministic mock client for testing LLM tool-calling and completions without external API keys."""

    def __init__(
        self,
        settings: LLMSettings | None = None,
        response_override: dict[str, Any] | None = None,
        canned_response: str | dict[str, Any] | None = None,
    ) -> None:
        super().__init__(settings)
        if response_override is not None:
            self.response_override = response_override
        elif canned_response is not None:
            if isinstance(canned_response, str):
                try:
                    self.response_override = json.loads(canned_response)
                except Exception:
                    self.response_override = {"raw": canned_response}
            else:
                self.response_override = canned_response
        else:
            self.response_override = None

    @property
    def provider_name(self) -> str:
        return self.settings.llm_provider or "mock"

    @property
    def model_name(self) -> str:
        return self.settings.llm_model or "mock-agent-v1"

    async def _complete(
        self,
        request: LLMRequest,
        *,
        response_model: type[BaseModel] | None = None,
    ) -> LLMResponse[Any]:
        if self.response_override is not None:
            raw = json.dumps(self.response_override)
            parsed_val = (
                response_model.model_validate(self.response_override)
                if response_model is not None
                else None
            )
            return LLMResponse(
                provider=self.provider_name,
                model=self.model_name,
                raw_text=raw,
                parsed=parsed_val,
                usage=None,
            )

        last_msg = request.messages[-1].content.lower() if request.messages else ""
        intent = "form_automation"
        state = None
        edu = None
        scheme = None

        if any(w in last_msg for w in ("passbook", "where is", "ifsc", "खाता")):
            intent = "visual_guide_qa"
        elif "status" in last_msg:
            intent = "status_check"

        if "karnataka" in last_msg:
            state = "Karnataka"
        elif "maharashtra" in last_msg:
            state = "Maharashtra"

        if any(e in last_msg for e in ("post-matric", "post matric", "college")):
            edu = "Post-Matric"
        elif any(e in last_msg for e in ("pre-matric", "pre matric", "school")):
            edu = "Pre-Matric"

        if "ssp" in last_msg:
            scheme = "State Scholarship Portal (SSP)"
        elif "nsp" in last_msg or "national scholarship" in last_msg:
            scheme = "National Scholarship Portal (NSP)"

        mock_payload = {
            "intent": intent,
            "detected_language": "hi" if ("कहाँ" in last_msg or "नमस्ते" in last_msg) else "en",
            "domicile_state": state,
            "education_level": edu,
            "scheme_or_portal": scheme,
            "confidence": 0.96,
            "reasoning": "Mock tool-calling intent and entity extraction.",
        }

        parsed_val = (
            response_model.model_validate(mock_payload)
            if response_model is not None
            else None
        )
        return LLMResponse(
            provider=self.provider_name,
            model=self.model_name,
            raw_text=json.dumps(mock_payload),
            parsed=parsed_val,
            usage=None,
        )


class OpenAICompatibleLLMClient(BaseLLMClient):
    """Production provider adapter for OpenAI, Groq, Ollama, OpenRouter, or compatible APIs."""

    @property
    def provider_name(self) -> str:
        return self.settings.llm_provider or "openai"

    @property
    def model_name(self) -> str:
        return self.settings.llm_model or "gpt-4o-mini"

    async def _complete(
        self,
        request: LLMRequest,
        *,
        response_model: type[BaseModel] | None = None,
    ) -> LLMResponse[Any]:
        import httpx
        from app.llm.types import LLMTokenUsage

        api_key = (
            self.settings.llm_api_key.get_secret_value()
            if self.settings.llm_api_key is not None
            else ""
        )
        base_url = self.settings.llm_base_url or "https://api.openai.com/v1"
        url = f"{base_url.rstrip('/')}/chat/completions"

        headers: dict[str, str] = {
            "Content-Type": "application/json",
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        messages = [{"role": m.role, "content": m.content} for m in request.messages]

        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "temperature": (
                request.temperature
                if request.temperature is not None
                else self.settings.llm_temperature
            ),
            "max_tokens": (
                request.max_output_tokens
                if request.max_output_tokens is not None
                else self.settings.llm_max_output_tokens
            ),
        }

        if response_model is not None:
            payload["response_format"] = {"type": "json_object"}
            schema_json = json.dumps(response_model.model_json_schema())
            instruction = (
                f"\nYou MUST output a JSON object adhering strictly to this schema: {schema_json}"
            )
            if payload["messages"] and payload["messages"][0]["role"] == "system":
                payload["messages"][0]["content"] += instruction
            else:
                payload["messages"].insert(0, {"role": "system", "content": instruction})

        effective_timeout = request.timeout_seconds or self.settings.llm_timeout_seconds
        async with httpx.AsyncClient(timeout=effective_timeout) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code >= 400:
                raise LLMProviderError(
                    f"Provider '{self.provider_name}' error HTTP {resp.status_code}: {resp.text}"
                )

            data = resp.json()
            choice = data.get("choices", [{}])[0]
            msg_obj = choice.get("message", {})
            raw_content = msg_obj.get("content", "")
            finish_reason = choice.get("finish_reason")
            usage_info = data.get("usage")
            usage = (
                LLMTokenUsage(
                    prompt_tokens=usage_info.get("prompt_tokens"),
                    completion_tokens=usage_info.get("completion_tokens"),
                    total_tokens=usage_info.get("total_tokens"),
                )
                if usage_info
                else None
            )

            return LLMResponse(
                provider=self.provider_name,
                model=self.model_name,
                raw_text=raw_content,
                usage=usage,
                finish_reason=finish_reason,
            )


def get_llm_client(settings: LLMSettings | None = None) -> BaseLLMClient:
    """Return the configured LLM client, or UnconfiguredLLMClient if none is selected."""
    active_settings = settings or get_llm_settings()
    if not active_settings.is_configured:
        return UnconfiguredLLMClient(active_settings)

    provider = (active_settings.llm_provider or "").lower().strip()
    if provider in ("mock", "test", "fake"):
        return MockLLMClient(active_settings)

    return OpenAICompatibleLLMClient(active_settings)

