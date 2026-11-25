"""PRD-safe payload sanitization ensuring secrets, vault data, and raw page content are never persisted."""

from typing import Any

from app.policies.log_redactor import redact_dict, redact_sensitive_text

# Keys that must never be stored in conversation history, task metadata, or logs.
_FORBIDDEN_METADATA_KEYS = frozenset(
    {
        "passphrase",
        "password",
        "pin",
        "otp",
        "cvv",
        "secret",
        "secret_key",
        "api_key",
        "access_token",
        "refresh_token",
        "authorization",
        "cookie",
        "cookies",
        "set_cookie",
        "raw_document",
        "file_base64",
        "formatted_file_base64",
        "ciphertextbase64",
        "ciphertext_base64",
        "page_html",
        "dom_html",
        "raw_html",
        "vault_value",
        "raw_vault_value",
    }
)


def sanitize_text_for_persistence(text: str) -> str:
    """Redact PII/IDs from text before persisting in conversation records or logs."""
    return redact_sensitive_text(text)


def sanitize_metadata_payload(payload: Any) -> Any:
    """Recursively strip forbidden secret/raw keys and redact PII from metadata dicts."""
    if isinstance(payload, dict):
        cleaned: dict[str, Any] = {}
        for key, value in payload.items():
            key_str = str(key)
            if key_str.strip().lower() in _FORBIDDEN_METADATA_KEYS:
                cleaned[key_str] = "[REDACTED_FORBIDDEN_FIELD]"
                continue
            safe_key = redact_sensitive_text(key_str) if isinstance(key, str) else key
            cleaned[safe_key] = sanitize_metadata_payload(value)
        return cleaned
    if isinstance(payload, list):
        return [sanitize_metadata_payload(item) for item in payload]
    if isinstance(payload, tuple):
        return [sanitize_metadata_payload(item) for item in payload]
    if isinstance(payload, str):
        return redact_sensitive_text(payload)
    return redact_dict(payload)
