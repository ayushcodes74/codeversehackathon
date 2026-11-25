"""Provider-neutral LLM configuration loaded from environment variables."""

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


class LLMSettings(BaseSettings):
    """Provider-neutral LLM client settings with no preselected vendor or hardcoded secrets."""

    llm_provider: str | None = None
    llm_model: str | None = None
    llm_api_key: SecretStr | None = None
    llm_base_url: str | None = None

    llm_timeout_seconds: float = Field(default=30.0, gt=0.0, le=120.0)
    llm_max_retries: int = Field(default=1, ge=0, le=3)
    llm_temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    llm_max_output_tokens: int = Field(default=1024, ge=1, le=32768)

    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @field_validator("llm_provider", "llm_model", mode="after")
    @classmethod
    def _strip_optional_identifier(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("llm_base_url", mode="after")
    @classmethod
    def _validate_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        parsed = urlparse(cleaned)
        if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc or not parsed.hostname:
            raise ValueError("LLM_BASE_URL must be a valid http:// or https:// URL.")
        return cleaned.rstrip("/")

    @property
    def is_configured(self) -> bool:
        """Return True when both a provider and model have been explicitly configured."""
        return bool(self.llm_provider and self.llm_model)


@lru_cache(maxsize=1)
def get_llm_settings() -> LLMSettings:
    """Return cached provider-neutral LLM settings instance."""
    return LLMSettings()
