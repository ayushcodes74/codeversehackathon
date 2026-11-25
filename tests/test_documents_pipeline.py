"""Pytest suite for document OCR validation, field mapping, log redaction, and FastAPI routes."""

import importlib.util
import sys
from pathlib import Path

import cv2
import numpy as np

import pymupdf
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.api import documents as api_documents
from app.api.documents import MAX_UPLOAD_BYTES, router as documents_router
from app.documents import (
    DOCUMENT_AGENT_TOOLS,
    DOCUMENT_TOOL_DEFINITIONS,
    FourStageDocumentValidator,
    map_document_fields_to_form,
    run_document_extraction_tool,
    run_map_form_fields_tool,
)
from app.documents import agent_tools
from app.documents import paddle_ocr as paddle_ocr_module
from app.policies.log_redactor import redact_dict, redact_sensitive_text

_contracts = agent_tools._load_shared_document_contracts()
DocumentExtractionResult = _contracts.DocumentExtractionResult
MapFormFieldsResponse = _contracts.MapFormFieldsResponse


def _encode_png(image: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", image)
    assert ok
    return buf.tobytes()


# --- 1. Log Redactor Tests ---
def test_redact_sensitive_text_masks_pan_uid_and_labeled_12_digit_account() -> None:
    text = (
        "PAN ABCDE1234F, Aadhaar 1234 5678 9012, "
        "Account No: 123456789012, A/C 998877665544, Phone 9876543210"
    )
    redacted = redact_sensitive_text(text)

    assert "ABCDE1234F" not in redacted
    assert "[REDACTED_PAN]" in redacted
    assert "1234 5678 9012" not in redacted
    assert "[REDACTED_UID]" in redacted
    assert "123456789012" not in redacted
    assert "998877665544" not in redacted
    assert "Account No: [REDACTED_ACCT]" in redacted
    assert "A/C [REDACTED_ACCT]" in redacted
    assert "9876543210" in redacted


def test_redact_dict_recursive_sanitization() -> None:
    payload = {
        "pan_ABCDE1234F": ["2345 6789 0123", ("Account No: 112233445566", 7)],
        "nested": {"summary": "PAN XYZAB9876K and Acct 445566778899", "safe": True},
    }
    sanitized = redact_dict(payload)

    assert "pan_[REDACTED_PAN]" in sanitized
    assert sanitized["pan_[REDACTED_PAN]"][0] == "[REDACTED_UID]"
    assert sanitized["pan_[REDACTED_PAN]"][1] == ("Account No: [REDACTED_ACCT]", 7)
    assert "[REDACTED_PAN]" in sanitized["nested"]["summary"]
    assert "[REDACTED_ACCT]" in sanitized["nested"]["summary"]
    assert sanitized["nested"]["safe"] is True


# --- 2. FourStageDocumentValidator Tests ---
def test_validator_rejects_blurry_image() -> None:
    blurry = np.full((240, 320, 3), 150, dtype=np.uint8)
    validator = FourStageDocumentValidator()
    report = validator.validate(
        file_bytes=_encode_png(blurry),
        mime_type="image/png",
        expected_doc_type="pan",
        detected_doc_type="pan",
        classification_confidence=0.95,
        extracted_fields=[],
    )

    assert report["overall_passed"] is False
    assert report["stage_2_visual_quality"]["passed"] is False
    assert any("blurry" in err.lower() for err in report["stage_2_visual_quality"]["errors"])


def test_validator_rejects_wrong_document_type() -> None:
    sharp = np.full((300, 500, 3), 200, dtype=np.uint8)
    for y in range(40, 280, 40):
        cv2.putText(sharp, "AADHAAR CARD 2345 6789 0123", (20, y), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (10, 10, 10), 2)

    validator = FourStageDocumentValidator()
    report = validator.validate(
        file_bytes=_encode_png(sharp),
        mime_type="image/png",
        expected_doc_type="pan",
        detected_doc_type="aadhaar",
        classification_confidence=0.92,
        extracted_fields=[],
    )

    assert report["overall_passed"] is False
    assert report["stage_3_document_classification"]["passed"] is False


def test_validator_aspect_ratio_crop_and_post_compression_sharpness_check() -> None:
    fine_img = np.full((600, 900, 3), 200, dtype=np.uint8)
    for y in range(25, 580, 22):
        cv2.putText(fine_img, "PORTRAIT TEXT 1234567890", (20, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (145, 145, 145), 1)

    validator = FourStageDocumentValidator(blur_threshold=120.0)
    report = validator.validate(
        file_bytes=_encode_png(fine_img),
        mime_type="image/png",
        expected_doc_type="passport_photo",
        detected_doc_type="passport_photo",
        classification_confidence=0.86,
        extracted_fields=[],
        portal_constraints={
            "allowed_mime_types": ["image/jpeg"],
            "max_size_kb": 2,
            "aspect_ratio": "35:45",
        },
    )

    assert report["overall_passed"] is True
    assert report["auto_formatted_applied"] is True
    assert report["formatted_mime_type"] == "image/jpeg"
    assert any("aspect ratio" in w.lower() for w in report["stage_1_technical_specs"]["warnings"])
    assert "post_format_sharpness" in report["stage_2_visual_quality"]["metrics"]
    assert report["stage_2_visual_quality"]["warnings"]
    assert report["requires_user_override"] is True


def test_validator_pdf_white_background_glare_suppression() -> None:
    white_img = np.full((300, 450, 3), 252, dtype=np.uint8)
    for y in range(40, 260, 40):
        cv2.putText(white_img, "CLEAN DIGITAL PDF", (20, y), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (180, 180, 180), 2)
    raw_png = _encode_png(white_img)

    validator = FourStageDocumentValidator()
    pdf_report = validator.validate(
        file_bytes=raw_png,
        mime_type="image/png",
        expected_doc_type="pan",
        detected_doc_type="pan",
        classification_confidence=0.95,
        extracted_fields=[],
        source_is_pdf=True,
    )
    cam_report = validator.validate(
        file_bytes=raw_png,
        mime_type="image/png",
        expected_doc_type="pan",
        detected_doc_type="pan",
        classification_confidence=0.95,
        extracted_fields=[],
        source_is_pdf=False,
    )

    assert pdf_report["stage_2_visual_quality"]["warnings"] == []
    assert pdf_report["requires_user_override"] is False
    assert len(cam_report["stage_2_visual_quality"]["warnings"]) == 1
    assert cam_report["requires_user_override"] is True


def test_validator_stage_4_fuzzy_name_consistency() -> None:
    sharp = np.full((300, 500, 3), 200, dtype=np.uint8)
    for y in range(40, 280, 40):
        cv2.putText(sharp, "INCOME TAX PAN ABCDE1234F", (20, y), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (20, 20, 20), 2)
    raw_png = _encode_png(sharp)

    validator = FourStageDocumentValidator()
    matched = validator.validate(
        file_bytes=raw_png,
        mime_type="image/png",
        expected_doc_type="pan",
        detected_doc_type="pan",
        classification_confidence=0.95,
        extracted_fields=[{"key": "full_name", "value": "Madhav Sharma"}],
        profile_full_name="Madhav K Sharma",
    )
    mismatched = validator.validate(
        file_bytes=raw_png,
        mime_type="image/png",
        expected_doc_type="pan",
        detected_doc_type="pan",
        classification_confidence=0.95,
        extracted_fields=[{"key": "full_name", "value": "Completely Different Name"}],
        profile_full_name="Madhav Sharma",
    )

    assert matched["stage_4_cross_field_consistency"]["warnings"] == []
    assert matched["requires_user_override"] is False
    assert len(mismatched["stage_4_cross_field_consistency"]["warnings"]) == 1
    assert mismatched["requires_user_override"] is True


# --- 3. Field Mapper Tests ---
def test_field_mapper_pan_roll_income_and_unmapped_required() -> None:
    pan_res = map_document_fields_to_form(
        profile_id="self",
        doc_type="pan",
        available_field_keys=["pan_number", "full_name"],
        form_fields=[
            {"element_key": "pan_input", "label": "Permanent Account Number (PAN)", "input_type": "text", "required": True},
            {"element_key": "father_input", "label": "Father's Name", "input_type": "text", "required": True},
        ],
    )
    MapFormFieldsResponse(**pan_res)
    assert pan_res["mappings"][0]["source_ref"] == "vault:self.pan.pan_number"
    assert len(pan_res["unmapped_required_fields"]) == 1
    assert pan_res["unmapped_required_fields"][0]["element_key"] == "father_input"
    assert pan_res["clarification_needed"] is True

    marksheet_res = map_document_fields_to_form(
        profile_id="self",
        doc_type="marksheet",
        available_field_keys=["roll_number"],
        form_fields=[
            {"element_key": "roll_input", "label": "University Enrollment Number", "input_type": "text", "required": True},
        ],
    )
    assert marksheet_res["mappings"][0]["source_ref"] == "vault:self.marksheet.roll_number"

    income_res = map_document_fields_to_form(
        profile_id="father",
        doc_type="income_certificate",
        available_field_keys=["annual_income"],
        form_fields=[
            {"element_key": "inc_input", "label": "Total Family Income (Yearly)", "input_type": "text", "required": True},
        ],
    )
    assert income_res["mappings"][0]["source_ref"] == "vault:father.income_certificate.annual_income"
    assert income_res["clarification_needed"] is False


# --- 4. FastAPI Documents API Tests ---
def test_documents_api_extract_and_validate_contracts_and_guards() -> None:
    app = FastAPI()
    app.include_router(documents_router)
    client = TestClient(app)

    # 200 valid embedded-text PDF
    pdf_doc = pymupdf.open()
    page = pdf_doc.new_page()
    page.insert_text(
        (72, 72),
        "INCOME TAX DEPARTMENT\nPermanent Account Number\nABCDE1234F\nMADHAV SHARMA\nFather's Name: RAJESH SHARMA\n15/08/1998",
    )
    pdf_bytes = pdf_doc.tobytes()
    pdf_doc.close()

    resp_ok = client.post(
        "/documents/extract-and-validate",
        files={"file": ("pan.pdf", pdf_bytes, "application/pdf")},
        data={
            "expected_doc_type": "pan",
            "profile_id": "self",
            "profile_full_name": "Madhav Sharma",
        },
    )
    assert resp_ok.status_code == 200
    parsed = DocumentExtractionResult(**resp_ok.json())
    assert parsed.detected_doc_type.value == "pan"
    assert parsed.extraction_method == "pymupdf_text"
    assert parsed.vault_commit_recommended is True

    # 400 malformed portal_constraints_json
    resp_400 = client.post(
        "/documents/extract-and-validate",
        files={"file": ("pan.pdf", pdf_bytes, "application/pdf")},
        data={"portal_constraints_json": "{bad_json"},
    )
    assert resp_400.status_code == 400

    # 413 >10 MB upload guard
    resp_413 = client.post(
        "/documents/extract-and-validate",
        files={"file": ("oversize.png", b"0" * (MAX_UPLOAD_BYTES + 1), "image/png")},
    )
    assert resp_413.status_code == 413


# --- 5. Agent Tools & Shared Singleton Tests ---
def test_agent_tools_and_shared_ocr_singleton() -> None:
    assert len(DOCUMENT_TOOL_DEFINITIONS) == 2
    assert {t["function"]["name"] for t in DOCUMENT_TOOL_DEFINITIONS} == {
        "extract_and_validate_document",
        "map_document_fields_to_form",
    }
    assert set(DOCUMENT_AGENT_TOOLS.keys()) == {
        "extract_and_validate_document",
        "map_document_fields_to_form",
    }

    # api/documents.py delegates directly to agent_tools.py and shares its single OCR singleton
    assert api_documents.run_document_extraction_tool is agent_tools.run_document_extraction_tool
    assert api_documents.run_map_form_fields_tool is agent_tools.run_map_form_fields_tool
    assert agent_tools._get_ocr_engine() is agent_tools._get_ocr_engine()

    pdf_doc = pymupdf.open()
    page = pdf_doc.new_page()
    page.insert_text(
        (72, 72),
        "INCOME TAX DEPARTMENT\nPermanent Account Number\nABCDE1234F\nMADHAV SHARMA\nFather's Name: RAJESH SHARMA\n15/08/1998",
    )
    pdf_bytes = pdf_doc.tobytes()
    pdf_doc.close()

    extraction = run_document_extraction_tool(
        file_bytes=pdf_bytes,
        mime_type="application/pdf",
        expected_doc_type="pan",
        profile_id="self",
        profile_full_name="Madhav Sharma",
    )
    assert isinstance(extraction, DocumentExtractionResult)
    assert extraction.detected_doc_type.value == "pan"
    assert extraction.extraction_method == "pymupdf_text"
    assert extraction.vault_commit_recommended is True

    mapped = run_map_form_fields_tool(
        profile_id="self",
        doc_type="pan",
        available_field_keys=["pan_number", "full_name"],
        form_fields=[
            {"element_key": "pan_el", "label": "Permanent Account Number", "input_type": "text", "required": True},
        ],
    )
    assert isinstance(mapped, MapFormFieldsResponse)
    assert mapped.mappings[0].source_ref == "vault:self.pan.pan_number"


def test_paddle_ocr_extract_lines_and_field_parsing(monkeypatch) -> None:
    class FakePaddleEngine:
        def ocr(self, image, cls: bool = True):
            assert image is not None and cls is True
            return [[
                [[[10, 10], [260, 10], [260, 40], [10, 40]], ("INCOME TAX DEPARTMENT", 0.97)],
                [[[10, 50], [220, 50], [220, 80], [10, 80]], ("MADHAV SHARMA", 0.93)],
                [[[10, 90], [280, 90], [280, 120], [10, 120]], ("Father's Name: RAJESH SHARMA", 0.91)],
                [[[10, 130], [180, 130], [180, 160], [10, 160]], ("ABCDE1234F", 0.74)],
                [[[10, 170], [190, 170], [190, 200], [10, 200]], ("DOB: 15/08/1998", 0.89)],
            ]]

    monkeypatch.setattr(paddle_ocr_module, "_create_paddle_ocr", lambda lang: FakePaddleEngine())

    synthetic_card = np.full((220, 320, 3), 240, dtype=np.uint8)
    ocr = paddle_ocr_module.BilingualDocumentOCR(confidence_threshold=0.80)
    lines = ocr.extract_lines(_encode_png(synthetic_card), lang="en")

    assert len(lines) == 5
    assert lines[0]["bbox"] == {"x": 10, "y": 10, "width": 250, "height": 30}

    doc_type, class_conf, fields = ocr.classify_and_parse(lines)
    assert doc_type == "pan"
    assert class_conf >= 0.65

    by_key = {f["key"]: f for f in fields}
    assert by_key["full_name"]["value"] == "MADHAV SHARMA"
    assert by_key["full_name"]["needs_user_verification"] is False
    assert by_key["pan_number"]["value"] == "ABCDE1234F"
    assert by_key["pan_number"]["needs_user_verification"] is True
    assert by_key["dob"]["value"] == "15/08/1998"
