"""FastAPI router for in-memory document extraction, 4-stage validation, and vault mapping."""

import importlib.util
import json
import logging
import sys
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field, ValidationError

from ..documents.field_mapper import map_document_fields_to_form
from ..documents.paddle_ocr import BilingualDocumentOCR
from ..documents.pdf_reader import inspect_and_extract_pdf
from ..documents.validator import FourStageDocumentValidator
from ..policies.log_redactor import SensitiveDataLogFilter, install_log_redaction

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


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
FormFieldTarget = _contracts.FormFieldTarget
MapFormFieldsResponse = _contracts.MapFormFieldsResponse
PortalUploadConstraints = _contracts.PortalUploadConstraints

MAX_UPLOAD_BYTES = 10 * 1024 * 1024

logger = logging.getLogger("app.api.documents")
if not any(isinstance(f, SensitiveDataLogFilter) for f in logger.filters):
    logger.addFilter(SensitiveDataLogFilter())
install_log_redaction()

router = APIRouter(prefix="/documents", tags=["Documents & OCR"])

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


class MapFormFieldsRequest(BaseModel):
    """Request payload for mapping vault document keys onto portal DOM form targets."""

    profile_id: str = "self"
    doc_type: str
    available_field_keys: List[str] = Field(default_factory=list)
    form_fields: List[FormFieldTarget] = Field(default_factory=list)


@router.post("/extract-and-validate", response_model=DocumentExtractionResult)
async def extract_and_validate_document(
    file: UploadFile = File(...),
    expected_doc_type: str = Form("unknown"),
    profile_id: str = Form("self"),
    profile_full_name: Optional[str] = Form(None),
    language: str = Form("en"),
    portal_constraints_json: Optional[str] = Form(None),
) -> Dict[str, Any]:
    """Extract fields and run 4-stage verification strictly in memory."""
    lang_key = (language or "en").strip().lower()
    if lang_key not in ("en", "hi"):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported language '{language}'. Supported values: 'en', 'hi'.",
        )

    portal_constraints: Optional[Dict[str, Any]] = None
    if portal_constraints_json and portal_constraints_json.strip():
        try:
            parsed_constraints = json.loads(portal_constraints_json)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid portal_constraints_json: {exc.msg}",
            ) from exc
        if not isinstance(parsed_constraints, dict):
            raise HTTPException(
                status_code=400,
                detail="portal_constraints_json must decode to a JSON object.",
            )
        try:
            portal_constraints = PortalUploadConstraints(**parsed_constraints).model_dump()
        except ValidationError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid portal_constraints_json schema: {exc.errors()}",
            ) from exc

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if len(file_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail="File exceeds 10 MB maximum upload size.",
        )

    mime_type = _detect_mime_type(file_bytes, file.content_type)
    ocr_engine = _get_ocr_engine()

    lines: List[Dict[str, Any]] = []
    page_count = 1
    extraction_method = "paddle_ocr"
    image_dimensions: Optional[Tuple[int, int]] = None

    if mime_type == "application/pdf":
        try:
            page_count, pages = inspect_and_extract_pdf(file_bytes)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid PDF document: {exc}") from exc

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
    elif mime_type.startswith("image/"):
        try:
            lines = ocr_engine.extract_lines(file_bytes, lang=lang_key)
            image_dimensions = _get_image_dimensions(file_bytes)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        extraction_method = "paddle_ocr"
    else:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported media type '{mime_type}'. Expected application/pdf or image/*.",
        )

    detected_doc_type, classification_confidence, parsed_fields = ocr_engine.classify_and_parse(
        lines,
        expected_doc_type=expected_doc_type,
        image_dimensions=image_dimensions,
    )

    validation = _validator_instance.validate(
        file_bytes=file_bytes,
        mime_type=mime_type,
        expected_doc_type=expected_doc_type,
        detected_doc_type=detected_doc_type,
        classification_confidence=classification_confidence,
        extracted_fields=parsed_fields,
        profile_full_name=profile_full_name,
        portal_constraints=portal_constraints,
        source_is_pdf=(mime_type == "application/pdf"),
    )

    request_id = f"doc_{uuid.uuid4().hex[:12]}"
    vault_commit_recommended = bool(
        validation["overall_passed"]
        and detected_doc_type != "unknown"
        and len(parsed_fields) > 0
    )

    logger.info(
        "Processed document request_id=%s profile_id=%s detected_doc_type=%s passed=%s",
        request_id,
        profile_id,
        detected_doc_type,
        validation["overall_passed"],
    )

    return {
        "request_id": request_id,
        "profile_id": profile_id,
        "detected_doc_type": detected_doc_type,
        "classification_confidence": classification_confidence,
        "page_count": page_count,
        "extraction_method": extraction_method,
        "fields": parsed_fields,
        "validation": validation,
        "vault_commit_recommended": vault_commit_recommended,
    }


@router.post("/map-fields", response_model=MapFormFieldsResponse)
async def map_fields_endpoint(payload: MapFormFieldsRequest) -> Dict[str, Any]:
    """Map vault document keys onto discovered DOM form fields via vault: tokens."""
    return map_document_fields_to_form(
        profile_id=payload.profile_id,
        doc_type=payload.doc_type,
        available_field_keys=payload.available_field_keys,
        form_fields=[field.model_dump() for field in payload.form_fields],
    )
