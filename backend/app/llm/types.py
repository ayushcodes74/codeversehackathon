"""Provider-neutral LLM chat request, usage, and structured response models."""

from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, Field, field_validator

T = TypeVar("T", bound=BaseModel)


class LLMChatMessage(BaseModel):
    """Single prompt message passed to a provider-neutral LLM client."""

    role: Literal["system", "user", "assistant"]
    content: str = Field(..., min_length=1, max_length=32000)

    @field_validator("content", mode="after")
    @classmethod
    def _validate_non_blank_content(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("LLM message content must not be empty or whitespace.")
        return cleaned


class LLMTokenUsage(BaseModel):
    """Normalized token accounting reported by an LLM provider."""

    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)


class LLMRequest(BaseModel):
    """Bounded, provider-neutral request payload for text or structured generation.

    Note: LLM responses are advisory data only. Tool authorization, Domain Shield
    checks, and Safe-Pause policies remain strictly outside the model.
    """

    messages: list[LLMChatMessage] = Field(..., min_length=1, max_length=100)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_output_tokens: int | None = Field(default=None, ge=1, le=32768)
    timeout_seconds: float | None = Field(default=None, gt=0.0, le=120.0)


class LLMResponse(BaseModel, Generic[T]):
    """Normalized LLM generation result supporting optional parsed structured output."""

    provider: str = Field(..., min_length=1, max_length=64)
    model: str = Field(..., min_length=1, max_length=128)
    raw_text: str = ""
    parsed: T | None = None
    usage: LLMTokenUsage | None = None
    latency_ms: float | None = Field(default=None, ge=0.0)
    finish_reason: str | None = Field(default=None, max_length=64)
