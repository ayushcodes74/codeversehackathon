"""Shared Pydantic v2 data contracts for document extraction, validation, and field mapping."""

from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator


class DocumentType(str, Enum):
    """Supported identity, academic, financial, and application media document types."""

    AADHAAR = "aadhaar"
    PAN = "pan"
    BANK_PASSBOOK = "bank_passbook"
    MARKSHEET = "marksheet"
    INCOME_CERTIFICATE = "income_certificate"
    CASTE_CERTIFICATE = "caste_certificate"
    PASSPORT_PHOTO = "passport_photo"
    SIGNATURE = "signature"
    UNKNOWN = "unknown"


class ProfileScope(str, Enum):
    """Family or dependent profile scope for on-device Local Vault isolation."""

    SELF = "self"
    FATHER = "father"
    MOTHER = "mother"
    SIBLING = "sibling"
    OTHER = "other"


class BoundingBox(BaseModel):
    """Pixel-space bounding box for an extracted text span or visual callout."""

    x: int
    y: int
    width: int
    height: int


class ExtractedField(BaseModel):
    """Single structured key-value field parsed from a document."""

    key: str
    value: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    needs_user_verification: bool = False
    bbox: Optional[BoundingBox] = None
    source_language: Literal["en", "hi", "mixed"] = "en"

    @model_validator(mode="after")
    def _enforce_low_confidence_verification(self) -> "ExtractedField":
        # Low-confidence extractions always require user confirmation before vault commit.
        if self.confidence < 0.80:
            self.needs_user_verification = True
        return self


class PortalUploadConstraints(BaseModel):
    """Target portal technical requirements for an uploaded file."""

    allowed_mime_types: List[str] = Field(default_factory=list)
    max_size_kb: Optional[int] = None
    min_size_kb: Optional[int] = None
    required_width_px: Optional[int] = None
    required_height_px: Optional[int] = None
    aspect_ratio: Optional[str] = None


class StageResult(BaseModel):
    """Outcome and diagnostics for a single verification pipeline stage."""

    passed: bool
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    metrics: Dict[str, Any] = Field(default_factory=dict)


class ValidationReport(BaseModel):
    """Aggregated 4-stage verification and auto-formatting report."""

    overall_passed: bool
    requires_user_override: bool = False
    stage_1_technical_specs: StageResult
    stage_2_visual_quality: StageResult
    stage_3_document_classification: StageResult
    stage_4_cross_field_consistency: StageResult
    auto_formatted_applied: bool = False
    auto_formatted_summary: Optional[str] = None
    formatted_file_base64: Optional[str] = None
    formatted_mime_type: Optional[str] = None


class DocumentExtractionResult(BaseModel):
    """Complete response payload returned by the extract-and-validate endpoint."""

    request_id: str
    profile_id: str = "self"
    detected_doc_type: DocumentType
    classification_confidence: float = Field(..., ge=0.0, le=1.0)
    page_count: int = Field(default=1, ge=1)
    extraction_method: Literal["pymupdf_text", "paddle_ocr", "digilocker_xml"]
    fields: List[ExtractedField] = Field(default_factory=list)
    validation: ValidationReport
    vault_commit_recommended: bool


class FormFieldTarget(BaseModel):
    """DOM form input descriptor discovered on a target portal page."""

    element_key: str
    label: str
    input_type: str
    section_title: Optional[str] = None
    required: bool = False


class FieldMappingItem(BaseModel):
    """Zero-leak mapping between a DOM input and a tokenized vault/cloud reference."""

    element_key: str
    target_label: str
    source_ref: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    ambiguous_candidates: List[str] = Field(default_factory=list)


class MapFormFieldsResponse(BaseModel):
    """Form field mapping output with unmapped required fields and clarification prompts."""

    mappings: List[FieldMappingItem] = Field(default_factory=list)
    unmapped_required_fields: List[FormFieldTarget] = Field(default_factory=list)
    clarification_needed: bool = False
    clarification_question: Optional[str] = None
