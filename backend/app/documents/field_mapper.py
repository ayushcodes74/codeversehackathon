"""Zero-leak DOM form field mapper emitting vault: reference tokens."""

import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from rapidfuzz import fuzz

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

_FIELD_SYNONYMS: Dict[str, Tuple[str, ...]] = {
    "pan_number": (
        "pan",
        "pan number",
        "pan card number",
        "pan no",
        "permanent account number",
        "applicant pan",
    ),
    "id_number": (
        "aadhaar",
        "aadhaar number",
        "aadhaar no",
        "aadhar",
        "aadhar number",
        "aadhar no",
        "uid",
        "uid number",
        "12 digit aadhaar",
        "id number",
    ),
    "account_number": (
        "account number",
        "bank account number",
        "account no",
        "a c no",
        "a c number",
        "acct no",
        "beneficiary account number",
        "savings account number",
    ),
    "ifsc_code": (
        "ifsc",
        "ifsc code",
        "ifs code",
        "bank ifsc",
        "branch ifsc code",
    ),
    "dob": (
        "dob",
        "d o b",
        "date of birth",
        "birth date",
        "applicant date of birth",
        "janm tithi",
    ),
    "full_name": (
        "full name",
        "applicant name",
        "applicant full name",
        "candidate name",
        "student name",
        "beneficiary name",
        "name of applicant",
        "name as per aadhaar",
        "name as per pan",
        "your name",
    ),
    "roll_number": (
        "roll number",
        "roll no",
        "enrollment number",
        "seat number",
        "registration number",
    ),
    "annual_income": (
        "annual income",
        "family income",
        "total income",
        "yearly income",
    ),
}

# Tokens that indicate a different entity/person than self full_name.
_DISQUALIFYING_NAME_TOKENS = frozenset(
    {"father", "mother", "husband", "wife", "guardian", "spouse", "nominee", "bank", "branch", "school", "college", "institute"}
)

_NON_FILLABLE_INPUT_TYPES = frozenset({"file", "submit", "button", "reset", "image", "hidden"})
_MATCH_THRESHOLD = 0.78
_AMBIGUITY_DELTA = 0.06


def _normalize_label(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9\s]", " ", (text or "").lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def _score_field_match(normalized_label: str, field_key: str) -> float:
    """Compute normalized [0.0, 1.0] fuzzy match confidence for a label and key."""
    if not normalized_label:
        return 0.0

    label_tokens = set(normalized_label.split())
    if field_key == "full_name":
        if normalized_label == "name":
            return 0.92
        if label_tokens & _DISQUALIFYING_NAME_TOKENS:
            return 0.0

    synonyms = list(_FIELD_SYNONYMS.get(field_key, ()))
    key_phrase = _normalize_label(field_key.replace("_", " "))
    if key_phrase and key_phrase not in synonyms:
        synonyms.append(key_phrase)

    best = 0.0
    for syn in synonyms:
        ratio = fuzz.token_set_ratio(normalized_label, syn) / 100.0
        if ratio > best:
            best = ratio
    return round(min(1.0, best), 4)


def map_document_fields_to_form(
    profile_id: str,
    doc_type: str,
    available_field_keys: List[str],
    form_fields: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Match discovered DOM form inputs to vault: reference tokens without raw PII.

    Args:
        profile_id: Active Local Vault profile scope (e.g. ``"self"``).
        doc_type: DocumentType string (e.g. ``"pan"``, ``"aadhaar"``).
        available_field_keys: Extracted field keys present in the vault record.
        form_fields: List of DOM field dicts matching ``FormFieldTarget``.

    Returns:
        Dict matching ``MapFormFieldsResponse`` in ``shared-contracts/schemas/documents.py``.
    """
    clean_profile = (profile_id or "self").strip()
    clean_doc_type = (doc_type or "unknown").strip().lower()
    unique_keys = list(dict.fromkeys(k.strip() for k in available_field_keys if k and k.strip()))

    mappings: List[Dict[str, Any]] = []
    unmapped_required_fields: List[Dict[str, Any]] = []
    ambiguous_labels: List[str] = []

    for raw_field in form_fields:
        element_key = str(raw_field.get("element_key", "")).strip()
        target_label = str(raw_field.get("label", "")).strip()
        input_type = str(raw_field.get("input_type", "text")).strip().lower()
        section_title: Optional[str] = raw_field.get("section_title")
        required = bool(raw_field.get("required", False))

        if input_type in _NON_FILLABLE_INPUT_TYPES:
            continue

        norm_label = _normalize_label(target_label)
        scored: List[Tuple[str, float]] = []
        for key in unique_keys:
            conf = _score_field_match(norm_label, key)
            if conf >= _MATCH_THRESHOLD:
                scored.append((key, conf))

        scored.sort(key=lambda item: item[1], reverse=True)

        if scored:
            best_key, best_conf = scored[0]
            close_candidates = [
                f"vault:{clean_profile}.{clean_doc_type}.{key}"
                for key, score in scored
                if (best_conf - score) <= _AMBIGUITY_DELTA
            ]
            ambiguous = close_candidates if len(close_candidates) > 1 else []
            if ambiguous:
                ambiguous_labels.append(target_label)

            mappings.append(
                {
                    "element_key": element_key,
                    "target_label": target_label,
                    "source_ref": f"vault:{clean_profile}.{clean_doc_type}.{best_key}",
                    "confidence": best_conf,
                    "ambiguous_candidates": ambiguous,
                }
            )
        elif required:
            unmapped_required_fields.append(
                {
                    "element_key": element_key,
                    "label": target_label,
                    "input_type": input_type,
                    "section_title": section_title,
                    "required": required,
                }
            )

    clarification_needed = bool(unmapped_required_fields or ambiguous_labels)
    clarification_question: Optional[str] = None
    if ambiguous_labels:
        clarification_question = (
            f"Multiple vault fields match form input(s): {', '.join(ambiguous_labels)}. "
            "Which field should be used?"
        )
    elif unmapped_required_fields:
        missing_labels = [f["label"] for f in unmapped_required_fields]
        clarification_question = (
            f"Required form field(s) could not be mapped from your {clean_doc_type} document: "
            f"{', '.join(missing_labels)}. Please provide them or upload another document."
        )

    return {
        "mappings": mappings,
        "unmapped_required_fields": unmapped_required_fields,
        "clarification_needed": clarification_needed,
        "clarification_question": clarification_question,
    }
