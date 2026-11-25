"""Unified Pydantic contracts and schemas for AI Agent Orchestration & Browser Automation.

All 16 agent tools have strict input/output models conforming to:
- Zero-Leak Hybrid Storage (using vault: references for sensitive identity tokens)
- Official Domain Shield
- Human-in-the-Loop Gates
- Multilingual Support (en / hi)
"""

from __future__ import annotations
import uuid
from typing import Any, Literal
from pydantic import BaseModel, Field


# =====================================================================
# DOM & Extension Snapshot Schemas
# =====================================================================

class DOMBoundingBox(BaseModel):
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0


class DOMField(BaseModel):
    element_key: str = Field(..., description="Ephemeral unique field identifier, e.g. field_01")
    selector: str = Field(..., description="CSS selector or unique path to the element")
    tag_name: str = Field(..., description="HTML tag name: input, select, textarea, button")
    input_type: str = Field(default="text", description="HTML input type: text, number, file, checkbox, etc.")
    label: str = Field(default="", description="Human readable label associated with this field")
    name: str | None = None
    id: str | None = None
    placeholder: str | None = None
    is_required: bool = False
    is_disabled: bool = False
    current_value: str | None = None
    options: list[str] = Field(default_factory=list, description="Available options for select dropdowns")
    bounding_box: DOMBoundingBox | None = None


class DOMButton(BaseModel):
    element_key: str
    selector: str
    text: str
    button_type: str = "button"  # button, submit, reset
    is_primary: bool = False
    is_disabled: bool = False


class DOMSnapshot(BaseModel):
    url: str
    title: str = ""
    step_indicator: str | None = Field(default=None, description="e.g. 'Step 1 of 4: Personal Details'")
    current_step_number: int = 1
    total_steps: int = 1
    fields: list[DOMField] = Field(default_factory=list)
    buttons: list[DOMButton] = Field(default_factory=list)
    text_content_snippet: str | None = None
    timestamp: float | str | None = None


# =====================================================================
# Visual Guide Schemas
# =====================================================================

class CalloutBox(BaseModel):
    label: str
    label_hi: str | None = None
    description: str
    description_hi: str | None = None
    x: float
    y: float
    width: float
    height: float


class VisualGuideCard(BaseModel):
    card_id: str
    topic: str
    title: str
    title_hi: str
    explanation: str
    explanation_hi: str
    document_sample_id: str = Field(
        ...,
        description="Reference template id, e.g. 'sample_pan_card', 'sample_bank_passbook', 'sample_aadhaar'"
    )
    bounding_box_callouts: list[CalloutBox] = Field(default_factory=list)
    quick_action: Literal["TRIGGER_FILE_PICKER", "FOCUS_INPUT", "OPEN_SAMPLE", "NONE"] = "NONE"
    target_element_key: str | None = None


# =====================================================================
# Client Execution Action Schemas (Chrome Extension Adapter)
# =====================================================================

class ClientAction(BaseModel):
    action_type: Literal[
        "FILL_FIELD",
        "CLICK_BUTTON",
        "NAVIGATE",
        "SCROLL_TO",
        "FOCUS_INPUT",
        "TRIGGER_FILE_PICKER",
        "REQUEST_APPROVAL",
        "CAPTURE_RECEIPT"
    ]
    element_key: str | None = None
    selector: str | None = None
    value_or_vault_ref: str | None = Field(
        default=None,
        description="Literal value or vault reference token like 'vault:self.pan.id_number'"
    )
    field_type: str = "text"
    is_sensitive: bool = False
    description: str = ""


# =====================================================================
# 16 Tool Contracts (Input / Output)
# =====================================================================

# 1. understand_user_goal
class UnderstandUserGoalInput(BaseModel):
    user_message: str
    language: str = "en"  # "en" or "hi"
    current_url: str | None = None
    task_id: uuid.UUID | None = None


class UnderstandUserGoalOutput(BaseModel):
    intent: Literal["form_automation", "visual_guide_qa", "portal_discovery", "clarification_needed", "status_check"]
    detected_language: str
    extracted_entities: dict[str, Any] = Field(default_factory=dict)
    requires_clarification: bool = False
    clarifying_question: str | None = None
    clarifying_question_hi: str | None = None
    confidence: float = 1.0


# 2. generate_task_plan
class PlanStep(BaseModel):
    step_index: int
    step_name: str
    step_name_hi: str | None = None
    description: str
    expected_action: str
    status: Literal["pending", "in_progress", "done", "skipped"] = "pending"


class GenerateTaskPlanInput(BaseModel):
    goal_text: str
    portal_name: str
    portal_url: str
    language: str = "en"
    known_steps: list[str] | None = None


class GenerateTaskPlanOutput(BaseModel):
    plan_id: str
    goal: str
    portal_name: str
    total_steps: int
    steps: list[PlanStep]


# 3. search_approved_portals
class SearchApprovedPortalsInput(BaseModel):
    query: str
    category: str | None = None
    domicile_state: str | None = None
    education_level: str | None = None


class ApprovedPortalItem(BaseModel):
    id: uuid.UUID
    portal_name: str
    domain: str
    entry_url: str
    category: str
    domicile_state: str | None = None
    education_level: str | None = None
    required_doc_types: list[str] = Field(default_factory=list)


class SearchApprovedPortalsOutput(BaseModel):
    match_count: int
    portals: list[ApprovedPortalItem]
    exact_match: ApprovedPortalItem | None = None
    needs_narrowing: bool = False
    missing_criteria: list[str] = Field(default_factory=list)


# 4. verify_domain
class VerifyDomainInput(BaseModel):
    url: str


class VerifyDomainOutput(BaseModel):
    is_allowed: bool
    domain: str
    portal_name: str | None = None
    category: str | None = None
    shield_status: Literal["VERIFIED_OFFICIAL", "BLOCKED_UNOFFICIAL", "LOCAL_DEV_ALLOWED"]
    warning: str | None = None


# 5. inspect_page
class InspectPageInput(BaseModel):
    dom_snapshot: DOMSnapshot
    current_url: str


class InspectPageOutput(BaseModel):
    page_type: Literal["form_step", "review_page", "acknowledgement_receipt", "portal_home", "unknown"]
    active_step: str
    current_step_number: int
    total_steps: int
    interactive_fields_count: int
    forms_detected: int
    action_buttons: list[str]


# 6. extract_page_information
class ExtractPageInformationInput(BaseModel):
    dom_snapshot: DOMSnapshot
    query_or_target: str | None = None


class ExtractPageInformationOutput(BaseModel):
    summary: str
    key_value_pairs: dict[str, str] = Field(default_factory=dict)
    status_messages: list[str] = Field(default_factory=list)
    has_validation_errors: bool = False
    error_messages: list[str] = Field(default_factory=list)


# 7. extract_document
class ExtractDocumentInput(BaseModel):
    document_type: str  # "pan", "aadhaar", "bank_passbook", "income_cert"
    profile_slug: str = "self"  # "self", "father", "mother"


class ExtractDocumentOutput(BaseModel):
    document_type: str
    profile_slug: str
    available_fields: list[str]
    vault_token_mapping: dict[str, str] = Field(
        default_factory=dict,
        description="e.g. {'id_number': 'vault:self.pan.id_number', 'holder_name': 'vault:self.pan.holder_name'}"
    )


# 8. validate_document
class ValidateDocumentInput(BaseModel):
    document_type: str
    vault_token_or_ref: str
    required_format: str | None = None


class ValidateDocumentOutput(BaseModel):
    is_valid: bool
    format_valid: bool
    checks_passed: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


# 9. map_form_fields
class FieldMapping(BaseModel):
    element_key: str
    field_label: str
    selector: str
    matched_fact_key: str | None = None
    value_or_vault_ref: str | None = None
    is_sensitive: bool = False
    confidence: float = 1.0
    field_type: str = "text"


class MapFormFieldsInput(BaseModel):
    fields: list[DOMField]
    profile_slug: str = "self"
    available_memories: dict[str, str] = Field(default_factory=dict)


class MapFormFieldsOutput(BaseModel):
    mappings: list[FieldMapping]
    unmapped_required_fields: list[str] = Field(default_factory=list)
    sensitive_fields_count: int = 0


# 10. fill_form_fields
class FillFormFieldsInput(BaseModel):
    mappings: list[FieldMapping]


class FillFormFieldsOutput(BaseModel):
    queued_actions_count: int
    actions: list[ClientAction]
    status: Literal["ready_to_dispatch", "partial", "failed"]


# 11. verify_filled_fields
class FieldVerificationItem(BaseModel):
    element_key: str
    expected_value_or_ref: str
    actual_value: str
    is_matched: bool


class VerifyFilledFieldsInput(BaseModel):
    items: list[FieldVerificationItem]


class VerifyFilledFieldsOutput(BaseModel):
    all_matched: bool
    matched_count: int
    mismatches: list[dict[str, Any]] = Field(default_factory=list)
    retry_required: bool = False


# 12. request_user_input
class RequestUserInputInput(BaseModel):
    missing_field_key: str
    question_en: str
    question_hi: str
    input_type: Literal["text", "select", "radio", "date", "number"] = "text"
    options: list[str] = Field(default_factory=list)


class RequestUserInputOutput(BaseModel):
    field_key: str
    prompt_delivered: bool = True
    question: str
    options: list[str] = Field(default_factory=list)


# 13. request_approval
class RequestApprovalInput(BaseModel):
    action_type: Literal["submit_form", "payment", "delete_data"] = "submit_form"
    summary_of_changes: dict[str, str] = Field(default_factory=dict)
    button_selector: str
    button_text: str = "Submit Application"
    warning_message_en: str
    warning_message_hi: str


class RequestApprovalOutput(BaseModel):
    approval_required: bool = True
    action_type: str
    action_payload: dict[str, Any]
    warning_message: str


# 14. save_task_state
class SaveTaskStateInput(BaseModel):
    task_id: uuid.UUID
    current_step_index: int
    total_steps: int
    status: str
    checkpoint_state: dict[str, Any]
    target_url: str | None = None
    application_ref_no: str | None = None


class SaveTaskStateOutput(BaseModel):
    task_id: uuid.UUID
    saved: bool
    status: str
    current_step_index: int


# 15. show_visual_guide
class ShowVisualGuideInput(BaseModel):
    query_topic: str
    target_field_key: str | None = None
    language: str = "en"


class ShowVisualGuideOutput(BaseModel):
    card: VisualGuideCard
    quick_actions: list[str] = Field(default_factory=list)


# 16. capture_receipt
class CaptureReceiptInput(BaseModel):
    dom_snapshot: DOMSnapshot
    receipt_selector_or_text: str | None = None


class CaptureReceiptOutput(BaseModel):
    receipt_number: str | None = None
    submission_timestamp: str | None = None
    status: str = "acknowledged"
    captured_data: dict[str, str] = Field(default_factory=dict)
    is_success: bool = True


# =====================================================================
# Master Agent Chat Request & Response Schemas
# =====================================================================

class AgentChatRequest(BaseModel):
    user_id: uuid.UUID
    profile_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None
    message: str
    language: str = "en"  # "en" or "hi"
    current_url: str | None = None
    dom_snapshot: DOMSnapshot | None = None


class AgentStepResponse(BaseModel):
    task_id: uuid.UUID | None = None
    response_type: Literal[
        "action",
        "clarification",
        "approval_request",
        "visual_guide",
        "completed",
        "error",
        "resumed"
    ]
    message: str
    message_hi: str | None = None
    intent_lane: Literal["form_automation", "knowledge_qa"] = "form_automation"
    next_tool: str | None = None
    tool_input: dict[str, Any] | None = None
    client_actions: list[ClientAction] = Field(default_factory=list)
    visual_guide: VisualGuideCard | None = None
    approval_request: RequestApprovalOutput | None = None
    receipt_data: CaptureReceiptOutput | None = None
    checkpoint_saved: bool = False
    current_step_index: int = 0
    total_steps: int = 1
    status: str = "in_progress"
