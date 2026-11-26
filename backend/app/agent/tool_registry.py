"""Tool Registry and Audit Logging for all 16 Agent Tools.

Enforces:
- Strict Zero-Leak data redaction (Aadhaar, PAN, Bank Account digits never persisted in plaintext).
- Tool audit logging into PostgreSQL tool_audit_logs table.
- Strict input/output typing using agent_contracts.py.
"""

from __future__ import annotations
from datetime import datetime, timezone
import re
import uuid
from typing import Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import ToolAuditLog, ApprovedPortal, Task
from app.schemas.agent_contracts import (
    UnderstandUserGoalInput, UnderstandUserGoalOutput,
    GenerateTaskPlanInput, GenerateTaskPlanOutput, PlanStep,
    SearchApprovedPortalsInput, SearchApprovedPortalsOutput, ApprovedPortalItem,
    VerifyDomainInput, VerifyDomainOutput,
    InspectPageInput, InspectPageOutput,
    ExtractPageInformationInput, ExtractPageInformationOutput,
    ExtractDocumentInput, ExtractDocumentOutput,
    ValidateDocumentInput, ValidateDocumentOutput,
    MapFormFieldsInput, MapFormFieldsOutput, FieldMapping,
    FillFormFieldsInput, FillFormFieldsOutput,
    VerifyFilledFieldsInput, VerifyFilledFieldsOutput,
    RequestUserInputInput, RequestUserInputOutput,
    RequestApprovalInput, RequestApprovalOutput,
    SaveTaskStateInput, SaveTaskStateOutput,
    ShowVisualGuideInput, ShowVisualGuideOutput,
    CaptureReceiptInput, CaptureReceiptOutput,
    ClientAction,
)
from app.agent.discovery import check_domain_shield
from app.agent.visual_guides import get_visual_guide_card
from app.documents.agent_tools import (
    run_document_extraction_tool,
    run_map_form_fields_tool,
)


# =====================================================================
# Zero-Leak Redaction Utilities
# =====================================================================

AADHAAR_REGEX = re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b")
PAN_REGEX = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]{1}\b")
BANK_ACC_REGEX = re.compile(r"\b\d{9,18}\b")
CARD_REGEX = re.compile(r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b")


def sanitize_sensitive_data(data: Any) -> Any:
    """Recursively redacts Aadhaar, PAN, Bank Account, and Card numbers from objects before logging."""
    if isinstance(data, str):
        # Do not redact vault references
        if data.startswith("vault:"):
            return data
        s = AADHAAR_REGEX.sub("[REDACTED_AADHAAR]", data)
        s = PAN_REGEX.sub("[REDACTED_PAN]", s)
        s = CARD_REGEX.sub("[REDACTED_CARD]", s)
        # Redact lone 9-18 digit account numbers
        s = BANK_ACC_REGEX.sub("[REDACTED_BANK_ACCOUNT]", s)
        return s
    elif isinstance(data, dict):
        sanitized = {}
        for k, v in data.items():
            k_lower = k.lower()
            if any(s in k_lower for s in ["aadhaar", "pan", "account_number", "bank_account", "cvv", "card"]):
                if isinstance(v, str) and v.startswith("vault:"):
                    sanitized[k] = v
                else:
                    sanitized[k] = "[REDACTED_SENSITIVE_ID]"
            else:
                sanitized[k] = sanitize_sensitive_data(v)
        return sanitized
    elif isinstance(data, list):
        return [sanitize_sensitive_data(item) for item in data]
    return data


async def audit_log_tool_call(
    db: AsyncSession,
    task_id: uuid.UUID,
    tool_name: str,
    status: str,
    raw_input: dict[str, Any],
    raw_output: dict[str, Any]
) -> None:
    """Safely sanitizes and logs every tool execution into the tool_audit_logs table."""
    try:
        sanitized_in = sanitize_sensitive_data(raw_input)
        sanitized_out = sanitize_sensitive_data(raw_output)

        audit_entry = ToolAuditLog(
            task_id=task_id,
            tool_name=tool_name,
            status=status,
            sanitized_input=sanitized_in,
            sanitized_output=sanitized_out
        )
        db.add(audit_entry)
        await db.flush()
    except Exception as e:
        # Audit logging failure must not crash the orchestrator
        print(f"[AuditLog Error] Failed to log tool {tool_name}: {e}")


# =====================================================================
# 16 Tool Implementations
# =====================================================================

# 1. understand_user_goal
async def understand_user_goal_tool(payload: UnderstandUserGoalInput) -> UnderstandUserGoalOutput:
    msg = payload.user_message.lower().strip()
    entities: dict[str, Any] = {}

    # Extract state if mentioned
    for state in ["karnataka", "maharashtra", "uttar pradesh", "delhi", "tamil nadu", "bihar"]:
        if state in msg:
            entities["domicile_state"] = state.title()

    # Extract education level if mentioned
    if any(e in msg for e in ["post-matric", "post matric", "college", "degree", "diploma"]):
        entities["education_level"] = "Post-Matric"
    elif any(e in msg for e in ["pre-matric", "pre matric", "school", "class 10"]):
        entities["education_level"] = "Pre-Matric"

    # Intent detection
    if any(w in msg for w in ["where is", "where can i find", "how to find", "what is", "passbook", "ifsc"]):
        intent = "visual_guide_qa"
    elif any(w in msg for w in ["status", "check status", "application status"]):
        intent = "status_check"
    elif any(w in msg for w in ["scholarship", "pan", "ration", "form", "apply", "register", "fill", "demo"]):
        intent = "form_automation"
    else:
        intent = "form_automation"

    return UnderstandUserGoalOutput(
        intent=intent,
        detected_language=payload.language,
        extracted_entities=entities,
        requires_clarification=False,
        confidence=0.95
    )


# 2. generate_task_plan
async def generate_task_plan_tool(payload: GenerateTaskPlanInput) -> GenerateTaskPlanOutput:
    steps = [
        PlanStep(
            step_index=1,
            step_name="Personal Details",
            step_name_hi="व्यक्तिगत विवरण",
            description="Fill applicant personal information and identity details.",
            expected_action="FILL_FIELDS"
        ),
        PlanStep(
            step_index=2,
            step_name="Address Information",
            step_name_hi="पते की जानकारी",
            description="Fill communication and domicile address details.",
            expected_action="FILL_FIELDS"
        ),
        PlanStep(
            step_index=3,
            step_name="Documents & Bank Account",
            step_name_hi="दस्तावेज़ एवं बैंक खाता",
            description="Inject verified Bank Account and IFSC vault tokens.",
            expected_action="FILL_FIELDS"
        ),
        PlanStep(
            step_index=4,
            step_name="Review & Submit",
            step_name_hi="समीक्षा एवं जमा",
            description="Review all inputs and prompt user approval before final submission.",
            expected_action="REQUEST_APPROVAL"
        )
    ]
    return GenerateTaskPlanOutput(
        plan_id=str(uuid.uuid4())[:8],
        goal=payload.goal_text,
        portal_name=payload.portal_name,
        total_steps=len(steps),
        steps=steps
    )


# 3. search_approved_portals
async def search_approved_portals_tool(payload: SearchApprovedPortalsInput, db: AsyncSession) -> SearchApprovedPortalsOutput:
    stmt = select(ApprovedPortal).where(ApprovedPortal.is_active == True)  # noqa: E712
    if payload.category:
        stmt = stmt.where(ApprovedPortal.category.ilike(f"%{payload.category}%"))
    if payload.domicile_state:
        stmt = stmt.where(ApprovedPortal.domicile_state.ilike(f"%{payload.domicile_state}%"))

    res = await db.execute(stmt)
    records = res.scalars().all()
    items = [
        ApprovedPortalItem(
            id=r.id,
            portal_name=r.portal_name,
            domain=r.domain,
            entry_url=r.entry_url,
            category=r.category,
            domicile_state=r.domicile_state,
            education_level=r.education_level,
            required_doc_types=r.required_doc_types or []
        )
        for r in records
    ]
    exact = items[0] if len(items) == 1 else None
    return SearchApprovedPortalsOutput(
        match_count=len(items),
        portals=items,
        exact_match=exact,
        needs_narrowing=len(items) > 1,
        missing_criteria=["domicile_state"] if len(items) > 1 and not payload.domicile_state else []
    )


# 4. verify_domain
async def verify_domain_tool(payload: VerifyDomainInput, db: AsyncSession) -> VerifyDomainOutput:
    return await check_domain_shield(payload.url, db)


# 5. inspect_page
async def inspect_page_tool(payload: InspectPageInput) -> InspectPageOutput:
    snapshot = payload.dom_snapshot
    active_step = snapshot.step_indicator or "Step 1: Personal Details"
    step_num = snapshot.current_step_number
    total = snapshot.total_steps if snapshot.total_steps > 0 else 4

    text_snippet = (snapshot.text_content_snippet or "").lower()
    indicator = (snapshot.step_indicator or "").lower()
    has_submit_btn = any(
        "submit" in (b.text or "").lower() or (b.button_type or "").lower() == "submit"
        for b in snapshot.buttons
    )
    page_type = "form_step"
    if any(w in text_snippet or w in indicator for w in ["receipt", "acknowledgement", "app-2026", "submitted successfully", "ack-"]):
        page_type = "acknowledgement_receipt"
    elif "review" in indicator or (step_num == total and total > 1 and (snapshot.fields or has_submit_btn)) or (has_submit_btn and "review" in text_snippet):
        page_type = "review_page"

    return InspectPageOutput(
        page_type=page_type,
        active_step=active_step,
        current_step_number=step_num,
        total_steps=total,
        interactive_fields_count=len(snapshot.fields),
        forms_detected=1 if snapshot.fields else 0,
        action_buttons=[b.text for b in snapshot.buttons]
    )


# 6. extract_page_information
async def extract_page_information_tool(payload: ExtractPageInformationInput) -> ExtractPageInformationOutput:
    snapshot = payload.dom_snapshot
    kv = {}
    for f in snapshot.fields:
        if f.current_value:
            label = f.label or f.name or f.element_key
            kv[label] = f.current_value

    summary = f"Detected {len(snapshot.fields)} fields with {len(kv)} populated values."
    return ExtractPageInformationOutput(
        summary=summary,
        key_value_pairs=kv,
        status_messages=["Page information extracted successfully."],
        has_validation_errors=False,
        error_messages=[]
    )


_VAULT_TOKEN_RE = re.compile(
    r"^vault:[a-zA-Z0-9_-]+\.(aadhaar|pan|bank_passbook|marksheet|income_certificate|caste_certificate|passport_photo|signature|unknown)\.[a-zA-Z0-9_]+$"
)


def _normalize_doc_type_hint(raw_doc_type: str) -> str:
    doc = (raw_doc_type or "").strip().lower()
    if "pan" in doc:
        return "pan"
    if "aadhaar" in doc or "aadhar" in doc or "uid" in doc:
        return "aadhaar"
    if "bank" in doc or "passbook" in doc:
        return "bank_passbook"
    if "mark" in doc:
        return "marksheet"
    if "income" in doc:
        return "income_certificate"
    if "caste" in doc:
        return "caste_certificate"
    if "photo" in doc:
        return "passport_photo"
    if "sign" in doc:
        return "signature"
    return doc or "unknown"


def _standardize_vault_token(source_ref: str) -> str:
    """Normalize vault reference tokens to match shared-contracts/schemas/documents.ts."""
    if source_ref.endswith(".aadhaar.id_number"):
        return source_ref[: -len(".id_number")] + ".aadhaar_number"
    return source_ref


# 7. extract_document
async def extract_document_tool(
    payload: ExtractDocumentInput,
    file_bytes: bytes | None = None,
    mime_type: str = "application/pdf",
    profile_full_name: str | None = None,
    language: str = "en",
    portal_constraints: dict[str, Any] | None = None,
) -> ExtractDocumentOutput:
    raw_bytes = file_bytes if file_bytes is not None else getattr(payload, "file_bytes", None)
    resolved_mime = getattr(payload, "mime_type", None) or mime_type
    expected_doc = _normalize_doc_type_hint(payload.document_type)
    slug = payload.profile_slug or "self"

    if not raw_bytes:
        raise ValueError(
            "extract_document_tool requires raw file_bytes (hardcoded stub responses are disabled)."
        )

    extraction = run_document_extraction_tool(
        file_bytes=raw_bytes,
        mime_type=resolved_mime,
        expected_doc_type=expected_doc,
        profile_id=slug,
        profile_full_name=profile_full_name,
        language=language,
        portal_constraints=portal_constraints,
    )
    detected_doc = extraction.detected_doc_type.value
    available_fields: list[str] = []
    vault_token_mapping: dict[str, str] = {}
    for field in extraction.fields:
        key = "aadhaar_number" if (detected_doc == "aadhaar" and field.key == "id_number") else field.key
        available_fields.append(key)
        vault_token_mapping[key] = f"vault:{slug}.{detected_doc}.{key}"

    return ExtractDocumentOutput(
        document_type=detected_doc,
        profile_slug=slug,
        available_fields=available_fields,
        vault_token_mapping=vault_token_mapping,
    )


# 8. validate_document
async def validate_document_tool(
    payload: ValidateDocumentInput,
    file_bytes: bytes | None = None,
    mime_type: str = "image/jpeg",
    profile_id: str = "self",
    profile_full_name: str | None = None,
    language: str = "en",
    portal_constraints: dict[str, Any] | None = None,
) -> ValidateDocumentOutput:
    raw_bytes = file_bytes if file_bytes is not None else getattr(payload, "file_bytes", None)
    expected_doc = _normalize_doc_type_hint(payload.document_type)

    if raw_bytes:
        effective_constraints = dict(portal_constraints or {})
        if payload.required_format and "allowed_mime_types" not in effective_constraints:
            effective_constraints["allowed_mime_types"] = [payload.required_format]

        extraction = run_document_extraction_tool(
            file_bytes=raw_bytes,
            mime_type=mime_type,
            expected_doc_type=expected_doc,
            profile_id=profile_id,
            profile_full_name=profile_full_name,
            language=language,
            portal_constraints=effective_constraints or None,
        )
        v = extraction.validation
        stage_map = {
            "technical_specs": v.stage_1_technical_specs,
            "visual_quality": v.stage_2_visual_quality,
            "document_classification": v.stage_3_document_classification,
            "cross_field_consistency": v.stage_4_cross_field_consistency,
        }
        checks_passed = [name for name, stage in stage_map.items() if stage.passed]
        if v.auto_formatted_applied:
            checks_passed.append("auto_formatted_for_portal")
        errors = [err for stage in stage_map.values() for err in stage.errors]
        return ValidateDocumentOutput(
            is_valid=v.overall_passed,
            format_valid=v.stage_1_technical_specs.passed,
            checks_passed=checks_passed,
            errors=errors,
        )

    ref = (payload.vault_token_or_ref or "").strip()
    token_valid = bool(_VAULT_TOKEN_RE.match(ref))
    if not token_valid:
        return ValidateDocumentOutput(
            is_valid=False,
            format_valid=False,
            checks_passed=[],
            errors=[
                f"Invalid vault reference token '{ref}'. Expected vault:<profileId>.<docType>.<fieldKey> or raw file_bytes."
            ],
        )

    return ValidateDocumentOutput(
        is_valid=True,
        format_valid=True,
        checks_passed=["vault_token_format_valid"],
        errors=[],
    )


# 9. map_form_fields
async def map_form_fields_tool(payload: MapFormFieldsInput) -> MapFormFieldsOutput:
    mappings: list[FieldMapping] = []
    unmapped: list[str] = []
    sensitive_count = 0
    slug = payload.profile_slug or "self"
    memories = {k.strip().lower(): v for k, v in payload.available_memories.items() if v is not None and str(v).strip()}

    form_targets = [
        {
            "element_key": f.element_key,
            "label": (f.label or f.name or f.placeholder or f.id or f.element_key),
            "input_type": f.input_type or "text",
            "required": bool(f.is_required),
        }
        for f in payload.fields
    ]

    # Delegate document-backed field matching to run_map_form_fields_tool (field_mapper.py).
    vault_specs: list[tuple[str, list[str], bool]] = [
        ("aadhaar", ["id_number", "dob", "full_name"], True),
        ("pan", ["pan_number", "dob", "full_name"], True),
        ("bank_passbook", ["account_number", "ifsc_code"], True),
        ("marksheet", ["roll_number"], False),
        ("income_certificate", ["annual_income"], False),
    ]
    label_by_element = {
        f.element_key: (f.label or f.name or f.placeholder or f.id or "").lower()
        for f in payload.fields
    }
    key_required_cues: dict[str, tuple[str, ...]] = {
        "pan_number": ("pan", "permanent account"),
        "aadhaar_number": ("aadhaar", "aadhar", "uid", "id number"),
        "dob": ("dob", "d.o.b", "birth", "janm"),
        "full_name": ("full name", "applicant name", "as per aadhaar", "as per pan", "candidate name", "naam"),
        "account_number": ("account", "a/c", "acct", "khata"),
        "ifsc_code": ("ifsc", "ifs"),
        "roll_number": ("roll", "enrollment", "seat", "registration"),
        "annual_income": ("income", "aay", "salary"),
    }
    best_vault_by_element: dict[str, tuple[str, str, float, bool]] = {}
    for doc_type, field_keys, default_sensitive in vault_specs:
        mapped_resp = run_map_form_fields_tool(
            profile_id=slug,
            doc_type=doc_type,
            available_field_keys=field_keys,
            form_fields=form_targets,
        )
        for item in mapped_resp.mappings:
            token = _standardize_vault_token(item.source_ref)
            matched_key = token.rsplit(".", 1)[-1]
            cues = key_required_cues.get(matched_key)
            el_label = label_by_element.get(item.element_key, "")
            if matched_key in ("aadhaar_number", "pan_number") and any(w in el_label for w in ("name", "naam")):
                continue
            if cues and not any(cue in el_label for cue in cues):
                continue
            is_sensitive = default_sensitive and matched_key not in {"ifsc_code", "dob", "full_name"}
            existing = best_vault_by_element.get(item.element_key)
            if existing is None or item.confidence > existing[2]:
                best_vault_by_element[item.element_key] = (
                    token,
                    matched_key,
                    item.confidence,
                    is_sensitive,
                )

    for f in payload.fields:
        label = (f.label or f.name or f.placeholder or f.id or "").lower()
        val_or_ref: str | None = None
        matched_key: str | None = None
        is_sensitive = False
        confidence = 1.0

        vault_match = best_vault_by_element.get(f.element_key)
        # Prefer explicit non-sensitive memory values for IFSC, DOB, and full_name when present; otherwise use vault token.
        if vault_match and vault_match[1] == "ifsc_code":
            mem_ifsc = memories.get("ifsc") or memories.get("ifsc_code")
            if mem_ifsc:
                val_or_ref = mem_ifsc
                matched_key = "ifsc_code"
            else:
                val_or_ref, matched_key, confidence, is_sensitive = vault_match
        elif vault_match and vault_match[1] == "dob":
            mem_dob = memories.get("dob") or memories.get("date_of_birth")
            if mem_dob:
                val_or_ref = mem_dob
                matched_key = "dob"
            else:
                val_or_ref, matched_key, confidence, is_sensitive = vault_match
        elif vault_match and vault_match[1] == "full_name" and not ("father" in label or "mother" in label or "guardian" in label):
            mem_name = memories.get("full_name") or memories.get("name")
            if mem_name:
                val_or_ref = mem_name
                matched_key = "full_name"
            else:
                val_or_ref, matched_key, confidence, is_sensitive = vault_match
        elif vault_match and not ("bank" in label and "name" in label):
            val_or_ref, matched_key, confidence, is_sensitive = vault_match
            if is_sensitive:
                sensitive_count += 1
        elif "khata" in label:
            val_or_ref = f"vault:{slug}.bank_passbook.account_number"
            matched_key = "account_number"
            is_sensitive = True
            sensitive_count += 1
        elif "ifsc" in label:
            val_or_ref = memories.get("ifsc") or memories.get("ifsc_code")
            matched_key = "ifsc_code"
        elif "bank" in label and "name" in label:
            val_or_ref = memories.get("bank_name")
            matched_key = "bank_name"
        elif "father" in label or "mother" in label or "guardian" in label:
            if "father" in label:
                val_or_ref = memories.get("father_name")
                matched_key = "father_name"
            elif "mother" in label:
                val_or_ref = memories.get("mother_name")
                matched_key = "mother_name"
            else:
                val_or_ref = memories.get("guardian_name")
                matched_key = "guardian_name"
        elif "name" in label or "naam" in label:
            val_or_ref = memories.get("full_name") or memories.get("name")
            matched_key = "full_name"
        elif "dob" in label or "birth" in label or "janm" in label:
            val_or_ref = memories.get("dob") or memories.get("date_of_birth")
            matched_key = "dob"
        elif "gender" in label or "ling" in label:
            val_or_ref = memories.get("gender")
            matched_key = "gender"
        elif "mobile" in label or "phone" in label or "contact" in label:
            val_or_ref = memories.get("mobile_no") or memories.get("phone")
            matched_key = "mobile_no"
        elif "email" in label:
            val_or_ref = memories.get("email")
            matched_key = "email"
        elif "address" in label or "pata" in label:
            val_or_ref = memories.get("address")
            matched_key = "address"
        elif "state" in label or "domicile" in label or "rajya" in label:
            val_or_ref = memories.get("domicile_state") or memories.get("state")
            matched_key = "state"
        elif "pin" in label or "postal" in label:
            val_or_ref = memories.get("pincode")
            matched_key = "pincode"
        else:
            val_or_ref = f.current_value or None

        if val_or_ref:
            mappings.append(
                FieldMapping(
                    element_key=f.element_key,
                    field_label=f.label or f.name or f.element_key,
                    selector=f.selector,
                    matched_fact_key=matched_key,
                    value_or_vault_ref=val_or_ref,
                    is_sensitive=is_sensitive,
                    confidence=confidence,
                    field_type=f.input_type,
                )
            )
        elif f.is_required:
            unmapped.append(f.label or f.element_key)

    return MapFormFieldsOutput(
        mappings=mappings,
        unmapped_required_fields=unmapped,
        sensitive_fields_count=sensitive_count,
    )


# 10. fill_form_fields
async def fill_form_fields_tool(payload: FillFormFieldsInput) -> FillFormFieldsOutput:
    client_actions = []
    for m in payload.mappings:
        if m.value_or_vault_ref:
            client_actions.append(ClientAction(
                action_type="FILL_FIELD",
                element_key=m.element_key,
                selector=m.selector,
                value_or_vault_ref=m.value_or_vault_ref,
                field_type=m.field_type,
                is_sensitive=m.is_sensitive,
                description=f"Fill {m.field_label}"
            ))

    return FillFormFieldsOutput(
        queued_actions_count=len(client_actions),
        actions=client_actions,
        status="ready_to_dispatch" if client_actions else "partial"
    )


# 11. verify_filled_fields
async def verify_filled_fields_tool(payload: VerifyFilledFieldsInput) -> VerifyFilledFieldsOutput:
    matched = 0
    mismatches = []
    for item in payload.items:
        # If expected is a vault ref, any filled masked/decrypted value counts as match
        if item.expected_value_or_ref.startswith("vault:") and item.actual_value:
            matched += 1
        elif item.expected_value_or_ref == item.actual_value:
            matched += 1
        else:
            mismatches.append({
                "element_key": item.element_key,
                "expected": item.expected_value_or_ref,
                "actual": item.actual_value
            })

    all_matched = (len(mismatches) == 0) and (len(payload.items) > 0)
    return VerifyFilledFieldsOutput(
        all_matched=all_matched,
        matched_count=matched,
        mismatches=mismatches,
        retry_required=not all_matched and len(mismatches) > 0
    )


# 12. request_user_input
async def request_user_input_tool(payload: RequestUserInputInput) -> RequestUserInputOutput:
    return RequestUserInputOutput(
        field_key=payload.missing_field_key,
        prompt_delivered=True,
        question=payload.question_en,
        options=payload.options
    )


# 13. request_approval
async def request_approval_tool(payload: RequestApprovalInput) -> RequestApprovalOutput:
    return RequestApprovalOutput(
        approval_required=True,
        action_type=payload.action_type,
        action_payload={
            "button_selector": payload.button_selector,
            "button_text": payload.button_text,
            "summary": payload.summary_of_changes
        },
        warning_message=payload.warning_message_en
    )


# 14. save_task_state
async def save_task_state_tool(payload: SaveTaskStateInput, db: AsyncSession) -> SaveTaskStateOutput:
    stmt = select(Task).where(Task.id == payload.task_id)
    res = await db.execute(stmt)
    task = res.scalar_one_or_none()

    if task:
        task.current_step_index = payload.current_step_index
        task.total_steps = payload.total_steps
        task.status = payload.status
        task.checkpoint_state = payload.checkpoint_state
        if payload.target_url:
            task.target_url = payload.target_url
        if payload.application_ref_no:
            task.application_ref_no = payload.application_ref_no
        await db.flush()
        return SaveTaskStateOutput(
            task_id=task.id,
            saved=True,
            status=task.status,
            current_step_index=task.current_step_index
        )

    return SaveTaskStateOutput(
        task_id=payload.task_id,
        saved=False,
        status="not_found",
        current_step_index=payload.current_step_index
    )


# 15. show_visual_guide
async def show_visual_guide_tool(payload: ShowVisualGuideInput) -> ShowVisualGuideOutput:
    card = get_visual_guide_card(payload.query_topic, payload.language, payload.target_field_key)
    if not card:
        # Fallback card
        card = get_visual_guide_card("passbook", payload.language, payload.target_field_key)
    actions = [card.quick_action] if card and card.quick_action != "NONE" else []
    return ShowVisualGuideOutput(
        card=card,
        quick_actions=actions
    )


# 16. capture_receipt
async def capture_receipt_tool(payload: CaptureReceiptInput) -> CaptureReceiptOutput:
    snapshot = payload.dom_snapshot
    parts = [snapshot.text_content_snippet or "", payload.receipt_selector_or_text or ""]
    for f in snapshot.fields:
        if f.current_value:
            parts.append(f.current_value)
    text = " ".join(p for p in parts if p)

    # Verify an explicit structured acknowledgement/receipt reference pattern (never fabricate one).
    ref_match = re.search(
        r"\b(?:APP-\d{4}-\d{4,6}|ACK-\d{4,10}|REF-\d{4,10}|"
        r"(?:receipt|reference|application|acknowledgement)\s*(?:no\.?|number|id)?\s*[:#-]?\s*([A-Z0-9-]*\d[A-Z0-9-]{4,19}))\b",
        text,
        re.IGNORECASE,
    )
    if not ref_match:
        return CaptureReceiptOutput(
            receipt_number=None,
            submission_timestamp=None,
            status="unconfirmed",
            captured_data={"portal_url": snapshot.url},
            is_success=False,
        )

    receipt_no = ref_match.group(1) or ref_match.group(0)
    return CaptureReceiptOutput(
        receipt_number=receipt_no.upper(),
        submission_timestamp=datetime.now(timezone.utc).isoformat(),
        status="acknowledged",
        captured_data={"application_ref_no": receipt_no.upper(), "portal_url": snapshot.url},
        is_success=True,
    )
