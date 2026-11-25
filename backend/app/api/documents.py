"""FastAPI router for in-memory document extraction, 4-stage validation, and vault mapping."""

import json
import logging
import sys
from pathlib import Path
from typing import Annotated, Any, Dict, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field, ValidationError

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_REPO_ROOT = _BACKEND_DIR.parent
for _path_entry in (str(_BACKEND_DIR), str(_REPO_ROOT)):
    if _path_entry not in sys.path:
        sys.path.insert(0, _path_entry)

from app.documents.agent_tools import (
    MAX_UPLOAD_BYTES,
    DocumentExtractionResult,
    FormFieldTarget,
    MapFormFieldsResponse,
    PortalUploadConstraints,
    UnsupportedMediaTypeError,
    run_document_extraction_tool,
    run_map_form_fields_tool,
)
from app.policies.log_redactor import SensitiveDataLogFilter, install_log_redaction

logger = logging.getLogger("app.api.documents")
if not any(isinstance(f, SensitiveDataLogFilter) for f in logger.filters):
    logger.addFilter(SensitiveDataLogFilter())
install_log_redaction()

router = APIRouter(prefix="/documents", tags=["Documents & OCR"])


class MapFormFieldsRequest(BaseModel):
    """Request payload for mapping vault document keys onto portal DOM form targets."""

    profile_id: str = "self"
    doc_type: str
    available_field_keys: List[str] = Field(default_factory=list)
    form_fields: List[FormFieldTarget] = Field(default_factory=list)


@router.post("/extract-and-validate", response_model=DocumentExtractionResult)
async def extract_and_validate_document(
    file: Annotated[UploadFile, File(description="Document image or PDF file")],
    expected_doc_type: Annotated[str, Form()] = "unknown",
    profile_id: Annotated[str, Form()] = "self",
    profile_full_name: Annotated[Optional[str], Form()] = None,
    language: Annotated[str, Form()] = "en",
    portal_constraints_json: Annotated[Optional[str], Form()] = None,
) -> DocumentExtractionResult:
    """Extract fields and run 4-stage verification strictly in memory."""
    lang_key = (language or "en").strip().lower()
    if lang_key not in ("en", "hi"):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported language '{language}'. Supported values: 'en', 'hi'.",
        )

    if profile_full_name is not None:
        profile_full_name = profile_full_name.strip() or None

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

    try:
        return run_document_extraction_tool(
            file_bytes=file_bytes,
            mime_type=file.content_type or "",
            expected_doc_type=expected_doc_type,
            profile_id=profile_id,
            profile_full_name=profile_full_name,
            language=lang_key,
            portal_constraints=portal_constraints,
        )
    except UnsupportedMediaTypeError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/map-fields", response_model=MapFormFieldsResponse)
async def map_fields_endpoint(payload: MapFormFieldsRequest) -> MapFormFieldsResponse:
    """Map vault document keys onto discovered DOM form fields via vault: tokens."""
    return run_map_form_fields_tool(
        profile_id=payload.profile_id,
        doc_type=payload.doc_type,
        available_field_keys=payload.available_field_keys,
        form_fields=[field.model_dump() for field in payload.form_fields],
    )
