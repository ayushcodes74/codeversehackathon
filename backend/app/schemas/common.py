"""Common validators, pagination metadata, and shared-contract loaders for Module 2 schemas."""

import importlib.util
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_REPO_ROOT = _BACKEND_DIR.parent
_SHARED_DOC_MODULE_NAME = "shared_contracts_schemas_documents"
_LANG_CODE_RE = re.compile(r"^[a-z]{2}(?:-[a-z0-9]{2,6})?$")


def load_shared_document_contracts() -> Any:
    """Load shared-contracts/schemas/documents.py using the canonical module key."""
    if _SHARED_DOC_MODULE_NAME in sys.modules:
        return sys.modules[_SHARED_DOC_MODULE_NAME]

    contracts_path = _REPO_ROOT / "shared-contracts" / "schemas" / "documents.py"
    spec = importlib.util.spec_from_file_location(_SHARED_DOC_MODULE_NAME, contracts_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load shared document contracts from {contracts_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[_SHARED_DOC_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


_shared_docs = load_shared_document_contracts()

# Re-exported directly from shared-contracts/schemas/documents.py without duplication.
DocumentType = _shared_docs.DocumentType
ProfileScope = _shared_docs.ProfileScope
FormFieldTarget = _shared_docs.FormFieldTarget
FieldMappingItem = _shared_docs.FieldMappingItem
PortalUploadConstraints = _shared_docs.PortalUploadConstraints
ValidationReport = _shared_docs.ValidationReport
DocumentExtractionResult = _shared_docs.DocumentExtractionResult
MapFormFieldsResponse = _shared_docs.MapFormFieldsResponse


def normalize_non_empty_text(value: str, field_name: str = "value") -> str:
    """Strip surrounding whitespace and reject empty or whitespace-only text."""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string.")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} must not be empty or whitespace.")
    return cleaned


def normalize_language_code(value: str) -> str:
    """Normalize and validate an ISO-like language tag (e.g. 'en', 'hi', 'en-in')."""
    cleaned = normalize_non_empty_text(value, "language").lower()
    if len(cleaned) > 10 or not _LANG_CODE_RE.match(cleaned):
        raise ValueError(
            f"Invalid language code '{value}'. Expected a short code like 'en' or 'hi'."
        )
    return cleaned


def validate_http_url(value: str | None, field_name: str = "url") -> str | None:
    """Validate that an optional URL uses http or https with a valid hostname."""
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    parsed = urlparse(cleaned)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc or not parsed.hostname:
        raise ValueError(
            f"{field_name} must be a valid http:// or https:// URL with a hostname."
        )
    return cleaned


class PaginationMeta(BaseModel):
    """Standard pagination metadata for list responses."""

    total: int = Field(..., ge=0)
    limit: int = Field(..., ge=1, le=100)
    offset: int = Field(..., ge=0)
    has_more: bool = False
