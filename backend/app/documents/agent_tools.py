"""Callable document intelligence tools and LLM tool schemas for the Agent Orchestrator."""

import importlib.util
import logging
import sys
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_REPO_ROOT = _BACKEND_DIR.parent
for _path_entry in (str(_BACKEND_DIR), str(_REPO_ROOT)):
    if _path_entry not in sys.path:
        sys.path.insert(0, _path_entry)

from app.documents.field_mapper import map_document_fields_to_form
from app.documents.paddle_ocr import BilingualDocumentOCR
from app.documents.pdf_reader import inspect_and_extract_pdf
from app.documents.validator import FourStageDocumentValidator
from app.policies.log_redactor import SensitiveDataLogFilter, install_log_redaction


def _load_shared_document_contracts() -> Any:
    module_name = "shared_contracts_schemas_documents"
    if module_name in sys.modules:
        return sys.modules[module_name]
    contracts_path = _REPO_ROOT / "shared-contracts" / "schemas" / "documents.py"
    spec = importlib.util.spec_from_file_location(module_name, contracts_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load shared document contracts from {contracts_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_contracts = _load_shared_document_contracts()
DocumentExtractionResult = _contracts.DocumentExtractionResult
DocumentType = _contracts.DocumentType
FormFieldTarget = _contracts.FormFieldTarget
MapFormFieldsResponse = _contracts.MapFormFieldsResponse
PortalUploadConstraints = _contracts.PortalUploadConstraints

MAX_UPLOAD_BYTES = 10 * 1024 * 1024


class UnsupportedMediaTypeError(ValueError):
    """Raised when an uploaded file's MIME type is neither PDF nor image/*."""


logger = logging.getLogger("app.documents.agent_tools")
if not any(isinstance(f, SensitiveDataLogFilter) for f in logger.filters):
    logger.addFilter(SensitiveDataLogFilter())
install_log_redaction()

_ocr_lock = threading.Lock()
_ocr_instance: Optional[BilingualDocumentOCR] = None
_validator_instance = FourStageDocumentValidator()


def _get_ocr_engine() -> BilingualDocumentOCR:
    global _ocr_instance
    if _ocr_instance is None:
        with _ocr_lock:
            if _ocr_instance is None:
                _ocr_instance = BilingualDocumentOCR()
    return _ocr_instance


def _detect_mime_type(file_bytes: bytes, declared_mime: Optional[str]) -> str:
    """Resolve MIME type using header declaration with magic-byte fallback."""
    mime = (declared_mime or "").strip().lower()
    if mime == "image/jpg":
        mime = "image/jpeg"
    if mime == "application/pdf" or mime.startswith("image/"):
        return mime

    if file_bytes.startswith(b"%PDF"):
        return "application/pdf"
    if file_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if file_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(file_bytes) >= 12 and file_bytes[:4] == b"RIFF" and file_bytes[8:12] == b"WEBP":
        return "image/webp"
    return mime or "application/octet-stream"


def _get_image_dimensions(file_bytes: bytes) -> Optional[Tuple[int, int]]:
    arr = np.frombuffer(file_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_UNCHANGED)
    if img is None:
        return None
    return int(img.shape[1]), int(img.shape[0])


def run_document_extraction_tool(
    file_bytes: bytes,
    mime_type: str,
    expected_doc_type: str = "unknown",
    profile_id: str = "self",
    profile_full_name: Optional[str] = None,
    language: str = "en",
    portal_constraints: Optional[Dict[str, Any]] = None,
) -> DocumentExtractionResult:
    """Execute in-memory PDF/OCR extraction and 4-stage validation for agent tool calls."""
    if not file_bytes:
        raise ValueError("file_bytes must not be empty.")
    if len(file_bytes) > MAX_UPLOAD_BYTES:
        raise ValueError("File exceeds 10 MB maximum upload size.")

    lang_key = (language or "en").strip().lower()
    if lang_key not in ("en", "hi"):
        raise ValueError(f"Unsupported language '{language}'. Supported values: 'en', 'hi'.")

    if profile_full_name is not None:
        profile_full_name = profile_full_name.strip() or None

    validated_constraints: Optional[Dict[str, Any]] = None
    if portal_constraints is not None:
        if hasattr(portal_constraints, "model_dump"):
            validated_constraints = PortalUploadConstraints(**portal_constraints.model_dump()).model_dump()
        else:
            validated_constraints = PortalUploadConstraints(**portal_constraints).model_dump()

    resolved_mime = _detect_mime_type(file_bytes, mime_type)
    ocr_engine = _get_ocr_engine()

    lines: List[Dict[str, Any]] = []
    page_count = 1
    extraction_method = "paddle_ocr"
    image_dimensions: Optional[Tuple[int, int]] = None

    if resolved_mime == "application/pdf":
        try:
            page_count, pages = inspect_and_extract_pdf(file_bytes)
        except Exception as exc:
            raise ValueError(f"Invalid PDF document: {exc}") from exc
        used_ocr = False
        used_embedded = False
        for page in pages:
            if page.has_embedded_text and page.embedded_text:
                used_embedded = True
                for raw_line in page.embedded_text.splitlines():
                    cleaned = raw_line.strip()
                    if cleaned:
                        lines.append({"text": cleaned, "confidence": 0.99, "bbox": None})
            elif page.rendered_png_bytes:
                used_ocr = True
                lines.extend(ocr_engine.extract_lines(page.rendered_png_bytes, lang=lang_key))

        extraction_method = "pymupdf_text" if (used_embedded and not used_ocr) else "paddle_ocr"
    elif resolved_mime.startswith("image/"):
        lines = ocr_engine.extract_lines(file_bytes, lang=lang_key)
        image_dimensions = _get_image_dimensions(file_bytes)
        extraction_method = "paddle_ocr"
    else:
        raise UnsupportedMediaTypeError(
            f"Unsupported media type '{resolved_mime}'. Expected application/pdf or image/*."
        )

    detected_doc_type, classification_confidence, parsed_fields = ocr_engine.classify_and_parse(
        lines,
        expected_doc_type=expected_doc_type,
        image_dimensions=image_dimensions,
    )

    validation = _validator_instance.validate(
        file_bytes=file_bytes,
        mime_type=resolved_mime,
        expected_doc_type=expected_doc_type,
        detected_doc_type=detected_doc_type,
        classification_confidence=classification_confidence,
        extracted_fields=parsed_fields,
        profile_full_name=profile_full_name,
        portal_constraints=validated_constraints,
        source_is_pdf=(resolved_mime == "application/pdf"),
    )

    request_id = f"doc_{uuid.uuid4().hex[:12]}"
    vault_commit_recommended = bool(
        validation["overall_passed"]
        and detected_doc_type != "unknown"
        and len(parsed_fields) > 0
    )

    logger.info(
        "Agent tool processed document request_id=%s profile_id=%s detected_doc_type=%s passed=%s",
        request_id,
        profile_id,
        detected_doc_type,
        validation["overall_passed"],
    )

    return DocumentExtractionResult(
        request_id=request_id,
        profile_id=profile_id,
        detected_doc_type=detected_doc_type,
        classification_confidence=classification_confidence,
        page_count=page_count,
        extraction_method=extraction_method,
        fields=parsed_fields,
        validation=validation,
        vault_commit_recommended=vault_commit_recommended,
    )


def run_map_form_fields_tool(
    profile_id: str,
    doc_type: str,
    available_field_keys: List[str],
    form_fields: List[Dict[str, Any]],
) -> MapFormFieldsResponse:
    """Map Local Vault field keys to DOM form labels using zero-leak vault: tokens."""
    normalized_fields = [
        f.model_dump() if hasattr(f, "model_dump") else dict(f) for f in form_fields
    ]
    mapped = map_document_fields_to_form(
        profile_id=profile_id,
        doc_type=doc_type,
        available_field_keys=available_field_keys,
        form_fields=normalized_fields,
    )
    return MapFormFieldsResponse(**mapped)


_DOC_TYPE_ENUM = [dt.value for dt in DocumentType]

DOCUMENT_TOOL_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "extract_and_validate_document",
            "description": (
                "Extract structured EN/HI OCR fields from an uploaded PDF or image in memory, "
                "run 4-stage technical/visual/classification/name-consistency validation, and "
                "auto-format images when portal upload constraints require compression or resizing."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "mime_type": {
                        "type": "string",
                        "description": "MIME type of the uploaded file (e.g., application/pdf, image/jpeg, image/png).",
                    },
                    "expected_doc_type": {
                        "type": "string",
                        "enum": _DOC_TYPE_ENUM,
                        "description": "Expected document type requested by the portal or user.",
                    },
                    "profile_id": {
                        "type": "string",
                        "description": "Family or dependent Local Vault profile scope (e.g., self, father, mother, sibling).",
                    },
                    "profile_full_name": {
                        "type": "string",
                        "description": "Expected applicant full name from profile for Stage 4 cross-field consistency.",
                    },
                    "language": {
                        "type": "string",
                        "enum": ["en", "hi"],
                        "description": "Primary OCR language engine ('en' or 'hi').",
                    },
                    "portal_constraints": {
                        "type": "object",
                        "description": "Optional portal upload constraints (allowed_mime_types, max_size_kb, required_width_px, required_height_px, aspect_ratio).",
                        "properties": {
                            "allowed_mime_types": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "max_size_kb": {"type": "integer"},
                            "min_size_kb": {"type": "integer"},
                            "required_width_px": {"type": "integer"},
                            "required_height_px": {"type": "integer"},
                            "aspect_ratio": {"type": "string"},
                        },
                    },
                },
                "required": ["mime_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "map_document_fields_to_form",
            "description": (
                "Match discovered portal DOM form fields to Local Vault document keys using fuzzy "
                "label matching and return zero-leak vault:<profile_id>.<doc_type>.<field_key> tokens."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "profile_id": {
                        "type": "string",
                        "description": "Local Vault profile scope (e.g., self, father, mother).",
                    },
                    "doc_type": {
                        "type": "string",
                        "enum": _DOC_TYPE_ENUM,
                        "description": "Document type stored in the Local Vault.",
                    },
                    "available_field_keys": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Extracted field keys available in the decrypted Local Vault record.",
                    },
                    "form_fields": {
                        "type": "array",
                        "description": "DOM form inputs discovered on the target portal page.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "element_key": {"type": "string"},
                                "label": {"type": "string"},
                                "input_type": {"type": "string"},
                                "section_title": {"type": "string"},
                                "required": {"type": "boolean"},
                            },
                            "required": ["element_key", "label", "input_type"],
                        },
                    },
                },
                "required": ["profile_id", "doc_type", "available_field_keys", "form_fields"],
            },
        },
    },
]

DOCUMENT_AGENT_TOOLS: Dict[str, Dict[str, Any]] = {
    "extract_and_validate_document": {
        "callable": run_document_extraction_tool,
        "description": DOCUMENT_TOOL_DEFINITIONS[0]["function"]["description"],
        "schema": DOCUMENT_TOOL_DEFINITIONS[0],
        "returns": "DocumentExtractionResult",
    },
    "map_document_fields_to_form": {
        "callable": run_map_form_fields_tool,
        "description": DOCUMENT_TOOL_DEFINITIONS[1]["function"]["description"],
        "schema": DOCUMENT_TOOL_DEFINITIONS[1],
        "returns": "MapFormFieldsResponse",
    },
}
