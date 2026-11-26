package com.codeverse.assistant.network

import com.google.gson.annotations.SerializedName

// =====================================================================
// DOM & Accessibility Snapshot Schemas (aligned with agent_contracts.py)
// =====================================================================

data class DOMBoundingBox(
    val x: Float = 0f,
    val y: Float = 0f,
    val width: Float = 0f,
    val height: Float = 0f
)

data class DOMField(
    @SerializedName("element_key") val elementKey: String,
    val selector: String,
    @SerializedName("tag_name") val tagName: String,
    @SerializedName("input_type") val inputType: String = "text",
    val label: String = "",
    val name: String? = null,
    val id: String? = null,
    val placeholder: String? = null,
    @SerializedName("is_required") val isRequired: Boolean = false,
    @SerializedName("is_disabled") val isDisabled: Boolean = false,
    @SerializedName("current_value") val currentValue: String? = null,
    val options: List<String> = emptyList(),
    @SerializedName("bounding_box") val boundingBox: DOMBoundingBox? = null
)

data class DOMButton(
    @SerializedName("element_key") val elementKey: String,
    val selector: String,
    val text: String,
    @SerializedName("button_type") val buttonType: String = "button",
    @SerializedName("is_primary") val isPrimary: Boolean = false,
    @SerializedName("is_disabled") val isDisabled: Boolean = false
)

data class DOMSnapshot(
    val url: String,
    val title: String = "",
    @SerializedName("step_indicator") val stepIndicator: String? = null,
    @SerializedName("current_step_number") val currentStepNumber: Int = 1,
    @SerializedName("total_steps") val totalSteps: Int = 1,
    val fields: List<DOMField> = emptyList(),
    val buttons: List<DOMButton> = emptyList(),
    @SerializedName("text_content_snippet") val textContentSnippet: String? = null,
    val timestamp: Double? = null
)

// =====================================================================
// Client Execution Actions & Agent Chat Contracts
// =====================================================================

data class ClientAction(
    @SerializedName("action_type") val actionType: String,
    @SerializedName("element_key") val elementKey: String? = null,
    val selector: String? = null,
    @SerializedName("value_or_vault_ref") val valueOrVaultRef: String? = null,
    @SerializedName("field_type") val fieldType: String = "text",
    @SerializedName("is_sensitive") val isSensitive: Boolean = false,
    val description: String = ""
)

data class CalloutBox(
    val label: String,
    @SerializedName("label_hi") val labelHi: String? = null,
    val description: String,
    @SerializedName("description_hi") val descriptionHi: String? = null,
    val x: Float = 0f,
    val y: Float = 0f,
    val width: Float = 0f,
    val height: Float = 0f
)

data class VisualGuideCard(
    @SerializedName("card_id") val cardId: String,
    val topic: String,
    val title: String,
    @SerializedName("title_hi") val titleHi: String,
    val explanation: String,
    @SerializedName("explanation_hi") val explanationHi: String,
    @SerializedName("document_sample_id") val documentSampleId: String,
    @SerializedName("bounding_box_callouts") val boundingBoxCallouts: List<CalloutBox> = emptyList(),
    @SerializedName("quick_action") val quickAction: String = "NONE",
    @SerializedName("target_element_key") val targetElementKey: String? = null
)

data class RequestApprovalOutput(
    @SerializedName("approval_required") val approvalRequired: Boolean = true,
    @SerializedName("action_type") val actionType: String = "submit_form",
    @SerializedName("action_payload") val actionPayload: Map<String, Any?> = emptyMap(),
    @SerializedName("warning_message") val warningMessage: String = ""
)

data class CaptureReceiptOutput(
    @SerializedName("receipt_number") val receiptNumber: String? = null,
    @SerializedName("submission_timestamp") val submissionTimestamp: String? = null,
    val status: String = "acknowledged",
    @SerializedName("captured_data") val capturedData: Map<String, String> = emptyMap(),
    @SerializedName("is_success") val isSuccess: Boolean = true
)

data class AgentChatRequest(
    @SerializedName("user_id") val userId: String,
    @SerializedName("profile_id") val profileId: String? = null,
    @SerializedName("task_id") val taskId: String? = null,
    val message: String,
    val language: String = "en",
    @SerializedName("current_url") val currentUrl: String? = null,
    @SerializedName("dom_snapshot") val domSnapshot: DOMSnapshot? = null
)

data class AgentStepResponse(
    @SerializedName("task_id") val taskId: String? = null,
    @SerializedName("response_type") val responseType: String,
    val message: String,
    @SerializedName("message_hi") val messageHi: String? = null,
    @SerializedName("intent_lane") val intentLane: String = "form_automation",
    @SerializedName("next_tool") val nextTool: String? = null,
    @SerializedName("tool_input") val toolInput: Map<String, Any?>? = null,
    @SerializedName("client_actions") val clientActions: List<ClientAction> = emptyList(),
    @SerializedName("visual_guide") val visualGuide: VisualGuideCard? = null,
    @SerializedName("approval_request") val approvalRequest: RequestApprovalOutput? = null,
    @SerializedName("receipt_data") val receiptData: CaptureReceiptOutput? = null,
    @SerializedName("checkpoint_saved") val checkpointSaved: Boolean = false,
    @SerializedName("current_step_index") val currentStepIndex: Int = 0,
    @SerializedName("total_steps") val totalSteps: Int = 1,
    val status: String = "in_progress"
)

// =====================================================================
// Domain Shield, Task Approval, Auth & Document Extraction Contracts
// =====================================================================

data class PortalVerifyResponse(
    @SerializedName("is_official") val isOfficial: Boolean,
    @SerializedName("canonical_domain") val canonicalDomain: String,
    @SerializedName("support_tier") val supportTier: String,
    val reason: String
)

data class TaskRecordResponse(
    val id: String,
    @SerializedName("user_id") val userId: String,
    @SerializedName("profile_id") val profileId: String? = null,
    @SerializedName("portal_id") val portalId: String? = null,
    val goal: String,
    val state: String,
    @SerializedName("current_step_index") val currentStepIndex: Int = 0,
    @SerializedName("pending_action_hash") val pendingActionHash: String? = null
)

data class TaskApprovalRequest(
    val approved: Boolean,
    @SerializedName("action_hash") val actionHash: String,
    @SerializedName("reviewer_note") val reviewerNote: String? = null
)

data class TaskApprovalResponse(
    @SerializedName("task_id") val taskId: String,
    val status: String,
    val approved: Boolean,
    @SerializedName("action_hash") val actionHash: String
)

data class GoogleAuthUpsertRequest(
    @SerializedName("google_sub") val googleSub: String,
    val email: String,
    @SerializedName("full_name") val fullName: String,
    @SerializedName("mobile_no") val mobileNo: String? = null,
    @SerializedName("preferred_language") val preferredLanguage: String = "en"
)

data class FamilyProfileDto(
    val id: String,
    @SerializedName("profile_slug") val profileSlug: String,
    @SerializedName("display_name") val displayName: String,
    @SerializedName("relation_type") val relationType: String,
    @SerializedName("is_default") val isDefault: Boolean = false
)

data class UserAuthResponse(
    @SerializedName("user_id") val userId: String,
    val email: String,
    val profiles: List<FamilyProfileDto> = emptyList()
)

data class DevTokenRequest(
    @SerializedName("user_id") val userId: String,
    @SerializedName("expires_in_seconds") val expiresInSeconds: Int = 3600
)

data class DevTokenResponse(
    @SerializedName("access_token") val accessToken: String,
    @SerializedName("token_type") val tokenType: String,
    @SerializedName("user_id") val userId: String
)

data class BoundingBoxDto(
    val x: Int = 0,
    val y: Int = 0,
    val width: Int = 0,
    val height: Int = 0
)

data class ExtractedFieldDto(
    val key: String,
    val value: String,
    val confidence: Double = 0.0,
    @SerializedName("needs_user_verification") val needsUserVerification: Boolean = false,
    val bbox: BoundingBoxDto? = null,
    @SerializedName("source_language") val sourceLanguage: String = "en"
)

data class StageResultDto(
    val passed: Boolean,
    val warnings: List<String> = emptyList(),
    val errors: List<String> = emptyList(),
    val metrics: Map<String, Any?> = emptyMap()
)

data class ValidationReportDto(
    @SerializedName("overall_passed") val overallPassed: Boolean,
    @SerializedName("requires_user_override") val requiresUserOverride: Boolean = false,
    @SerializedName("stage_1_technical_specs") val stage1TechnicalSpecs: StageResultDto,
    @SerializedName("stage_2_visual_quality") val stage2VisualQuality: StageResultDto,
    @SerializedName("stage_3_document_classification") val stage3DocumentClassification: StageResultDto,
    @SerializedName("stage_4_cross_field_consistency") val stage4CrossFieldConsistency: StageResultDto,
    @SerializedName("auto_formatted_applied") val autoFormattedApplied: Boolean = false,
    @SerializedName("auto_formatted_summary") val autoFormattedSummary: String? = null
)

data class DocumentExtractionResult(
    @SerializedName("request_id") val requestId: String,
    @SerializedName("profile_id") val profileId: String = "self",
    @SerializedName("detected_doc_type") val detectedDocType: String,
    @SerializedName("classification_confidence") val classificationConfidence: Double,
    @SerializedName("page_count") val pageCount: Int = 1,
    @SerializedName("extraction_method") val extractionMethod: String,
    val fields: List<ExtractedFieldDto> = emptyList(),
    val validation: ValidationReportDto,
    @SerializedName("vault_commit_recommended") val vaultCommitRecommended: Boolean
)
