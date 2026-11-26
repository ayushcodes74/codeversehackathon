"""Environment-based application configuration for the backend foundation."""

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
_VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


class AppSettings(BaseSettings):
    """Core FastAPI application settings loaded from environment or backend/.env."""

    app_name: str = "Universal AI Web Assistant API"
    app_slug: str = "universal-ai-web-assistant"
    app_version: str = "3.0.0"
    app_env: Literal["development", "staging", "production", "test"] = "development"
    debug: bool = False
    log_level: str = "INFO"

    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    api_v1_prefix: str = "/api/v1"

    docs_enabled: bool = True
    include_error_details: bool = False
    jwt_secret_key: SecretStr | None = None
    allow_dev_user_header: bool = False
    allow_local_dev_portals: bool = True

    cors_origins: str | list[str] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ]
    )
    cors_allow_credentials: bool = True

    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @field_validator("log_level", mode="after")
    @classmethod
    def _normalize_log_level(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in _VALID_LOG_LEVELS:
            raise ValueError(
                f"Invalid LOG_LEVEL '{value}'. Expected one of: {sorted(_VALID_LOG_LEVELS)}."
            )
        return normalized

    @field_validator("api_v1_prefix", mode="after")
    @classmethod
    def _normalize_api_prefix(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            return "/api/v1"
        if not cleaned.startswith("/"):
            cleaned = f"/{cleaned}"
        return cleaned.rstrip("/")

    @field_validator("cors_origins", mode="after")
    @classmethod
    def _parse_cors_origins(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return []
            if raw.startswith("["):
                parsed = json.loads(raw)
                if not isinstance(parsed, list):
                    raise ValueError("CORS_ORIGINS JSON value must be a list of origins.")
                return [str(item).strip() for item in parsed if str(item).strip()]
            return [origin.strip() for origin in raw.split(",") if origin.strip()]
        return [str(origin).strip() for origin in value if str(origin).strip()]

    @property
    def is_development(self) -> bool:
        """Return True when running in local development mode."""
        return self.app_env == "development"

    @property
    def is_production(self) -> bool:
        """Return True when running in production mode."""
        return self.app_env == "production"

    @property
    def local_dev_portals_allowed(self) -> bool:
        """Return True when local dev portals (localhost, 127.0.0.1, file://) are permitted."""
        return self.allow_local_dev_portals and not self.is_production

    @property
    def expose_error_details(self) -> bool:
        """Return True if redacted exception details may be included in 500 responses."""
        return self.debug or self.include_error_details or self.is_development


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """Return cached application settings instance."""
    return AppSettings()


settings = get_settings()
