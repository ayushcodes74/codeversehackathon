package com.codeverse.assistant.state

import android.content.Context
import android.content.SharedPreferences
import com.codeverse.assistant.accessibility.PortalAccessibilityService
import com.codeverse.assistant.network.AgentChatRequest
import com.codeverse.assistant.network.AgentStepResponse
import com.codeverse.assistant.network.ApiClientProvider
import com.codeverse.assistant.network.CaptureReceiptOutput
import com.codeverse.assistant.network.ClientAction
import com.codeverse.assistant.network.DOMSnapshot
import com.codeverse.assistant.network.DevTokenRequest
import com.codeverse.assistant.network.DocumentExtractionResult
import com.codeverse.assistant.network.GoogleAuthUpsertRequest
import com.codeverse.assistant.network.PortalVerifyResponse
import com.codeverse.assistant.network.TaskApprovalRequest
import com.codeverse.assistant.network.VisualGuideCard
import com.codeverse.assistant.security.AuthorizationGate
import com.codeverse.assistant.vault.EncryptedVaultStore
import com.codeverse.assistant.vault.VaultEntrySummary
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.MultipartBody
import okhttp3.RequestBody.Companion.toRequestBody

enum class OverlayTab {
    AUTOMATE,
    DOCUMENTS,
    APPROVAL
}

data class EditableExtractedField(
    val key: String,
    val value: String,
    val confidence: Double,
    val needsUserVerification: Boolean,
    val verifiedByUser: Boolean = false
)

data class AssistantUiState(
    val baseUrl: String = "",
    val language: String = "en",
    val userId: String? = null,
    val profileId: String? = null,
    val profileSlug: String = "self",
    val isAuthenticated: Boolean = false,
    val currentTaskId: String? = null,
    val resolvedPortalUrl: String? = null,
    val portalVerification: PortalVerifyResponse? = null,
    val agentMessage: String = "",
    val agentStatus: String = "idle",
    val responseType: String = "",
    val lastSnapshot: DOMSnapshot? = null,
    val pendingActions: List<ClientAction> = emptyList(),
    val pendingActionHash: String? = null,
    val approvalWarning: String? = null,
    val approvalSummary: Map<String, String> = emptyMap(),
    val visualGuide: VisualGuideCard? = null,
    val showGuideDialog: Boolean = false,
    val receiptData: CaptureReceiptOutput? = null,
    val fillCompleted: Boolean = false,
    val extractionResult: DocumentExtractionResult? = null,
    val editableFields: List<EditableExtractedField> = emptyList(),
    val lastUploadedFileName: String? = null,
    val vaultCommitSaved: Boolean = false,
    val vaultEntries: List<VaultEntrySummary> = emptyList(),
    val overlayExpanded: Boolean = false,
    val activeOverlayTab: OverlayTab = OverlayTab.AUTOMATE,
    val isBusy: Boolean = false,
    val statusLog: String = "",
    val errorMessage: String? = null
)

/**
 * Coordinates MainActivity, FloatingBubbleService, DocumentCaptureActivity,
 * PortalAccessibilityService, Retrofit API calls, and EncryptedVaultStore.
 */
class AssistantCoordinator(
    private val appContext: Context,
    private val apiProvider: ApiClientProvider,
    val vaultStore: EncryptedVaultStore,
    val authGate: AuthorizationGate
) {
    private val sessionPrefs: SharedPreferences =
        appContext.getSharedPreferences("ua_assistant_session", Context.MODE_PRIVATE)

    private val _uiState = MutableStateFlow(
        AssistantUiState(
            baseUrl = sessionPrefs.getString(KEY_BASE_URL, null) ?: apiProvider.currentBaseUrl(),
            language = sessionPrefs.getString(KEY_LANGUAGE, "en") ?: "en",
            userId = sessionPrefs.getString(KEY_USER_ID, null),
            profileId = sessionPrefs.getString(KEY_PROFILE_ID, null),
            profileSlug = sessionPrefs.getString(KEY_PROFILE_SLUG, "self") ?: "self",
            vaultEntries = vaultStore.listSummaries()
        )
    )
    val uiState: StateFlow<AssistantUiState> = _uiState.asStateFlow()

    init {
        apiProvider.updateConfig(
            newBaseUrl = _uiState.value.baseUrl,
            newAuthToken = sessionPrefs.getString(KEY_AUTH_TOKEN, null),
            newUserId = _uiState.value.userId
        )
        _uiState.update {
            it.copy(isAuthenticated = !sessionPrefs.getString(KEY_AUTH_TOKEN, null).isNullOrBlank())
        }
    }

    fun updateBaseUrl(newUrl: String) {
        val normalized = ApiClientProvider.normalizeBaseUrl(newUrl)
        apiProvider.updateConfig(newBaseUrl = normalized)
        sessionPrefs.edit().putString(KEY_BASE_URL, normalized).apply()
        _uiState.update { it.copy(baseUrl = normalized, errorMessage = null) }
    }

    fun setLanguage(lang: String) {
        val clean = if (lang.startsWith("hi")) "hi" else "en"
        sessionPrefs.edit().putString(KEY_LANGUAGE, clean).apply()
        _uiState.update { it.copy(language = clean) }
    }

    fun setOverlayExpanded(expanded: Boolean) {
        _uiState.update { it.copy(overlayExpanded = expanded) }
    }

    fun selectOverlayTab(tab: OverlayTab) {
        _uiState.update { it.copy(activeOverlayTab = tab) }
    }

    fun setGuideDialogVisible(visible: Boolean) {
        _uiState.update { it.copy(showGuideDialog = visible) }
    }

    fun dismissReceipt() {
        _uiState.update { it.copy(receiptData = null, overlayExpanded = false) }
    }

    fun clearError() {
        _uiState.update { it.copy(errorMessage = null) }
    }

    fun refreshVaultEntries() {
        _uiState.update { it.copy(vaultEntries = vaultStore.listSummaries()) }
    }

    /**
     * Ensures a valid backend user + profile + Bearer JWT exist for /api/v1/agent/chat.
     */
    suspend fun ensureAuthenticated(): Result<String> = runCatching {
        val currentToken = sessionPrefs.getString(KEY_AUTH_TOKEN, null)
        val currentUserId = _uiState.value.userId
        if (!currentToken.isNullOrBlank() && !currentUserId.isNullOrBlank()) {
            apiProvider.updateConfig(newAuthToken = currentToken, newUserId = currentUserId)
            return@runCatching currentUserId
        }

        val api = apiProvider.service()
        val authRes = api.upsertUser(
            GoogleAuthUpsertRequest(
                googleSub = "android-local-demo-sub",
                email = "citizen.android@jansahayak.local",
                fullName = "Android Citizen Demo",
                preferredLanguage = _uiState.value.language
            )
        )
        val defaultProfile = authRes.profiles.firstOrNull { it.isDefault } ?: authRes.profiles.firstOrNull()
        val tokenRes = api.issueDevToken(DevTokenRequest(userId = authRes.userId))

        sessionPrefs.edit()
            .putString(KEY_USER_ID, authRes.userId)
            .putString(KEY_PROFILE_ID, defaultProfile?.id)
            .putString(KEY_PROFILE_SLUG, defaultProfile?.profileSlug ?: "self")
            .putString(KEY_AUTH_TOKEN, tokenRes.accessToken)
            .apply()

        apiProvider.updateConfig(newAuthToken = tokenRes.accessToken, newUserId = authRes.userId)
        _uiState.update {
            it.copy(
                userId = authRes.userId,
                profileId = defaultProfile?.id,
                profileSlug = defaultProfile?.profileSlug ?: "self",
                isAuthenticated = true
            )
        }
        authRes.userId
    }

    /**
     * Voice / text intake router:
     * 1. Calls POST /api/v1/agent/chat to resolve the target portal URL.
     * 2. Verifies the resolved URL via GET /api/v1/portals/verify (Official Domain Shield).
     */
    suspend fun resolveAndVerifyPortal(userPrompt: String): Result<String?> = runCatching {
        _uiState.update { it.copy(isBusy = true, errorMessage = null, portalVerification = null) }
        ensureAuthenticated().getOrThrow()

        val state = _uiState.value
        val step = apiProvider.service().agentChat(
            AgentChatRequest(
                userId = state.userId!!,
                profileId = state.profileId,
                taskId = state.currentTaskId,
                message = userPrompt.trim(),
                language = state.language
            )
        )

        val navAction = step.clientActions.firstOrNull { it.actionType == "NAVIGATE" }
        val resolvedUrl = navAction?.valueOrVaultRef?.trim()?.takeIf { it.isNotBlank() }
        var verification: PortalVerifyResponse? = null

        if (resolvedUrl != null) {
            verification = apiProvider.service().verifyPortal(resolvedUrl)
            if (!verification.isOfficial) {
                throw SecurityException("Domain Shield blocked unverified URL: ${verification.reason}")
            }
        }

        applyAgentStep(step)
        _uiState.update {
            it.copy(
                resolvedPortalUrl = resolvedUrl ?: it.resolvedPortalUrl,
                portalVerification = verification,
                isBusy = false,
                fillCompleted = false,
                statusLog = if (resolvedUrl != null) {
                    "Verified portal (${verification?.supportTier}): $resolvedUrl"
                } else {
                    step.message
                }
            )
        }
        resolvedUrl
    }.onFailure { err ->
        _uiState.update {
            it.copy(isBusy = false, errorMessage = err.message ?: "Portal resolution failed")
        }
    }

    /**
     * Requests live AccessibilityNodeInfo tree from PortalAccessibilityService
     * into DOMSnapshot and sends it to POST /api/v1/agent/chat.
     */
    suspend fun captureScreenAndConsultAgent(userMessage: String? = null): Result<AgentStepResponse> =
        runCatching {
            _uiState.update { it.copy(isBusy = true, errorMessage = null) }
            ensureAuthenticated().getOrThrow()

            val service = PortalAccessibilityService.instance
                ?: throw IllegalStateException("PortalAccessibilityService is not enabled in Android Accessibility Settings.")

            val snapshot = service.captureDomSnapshot(fallbackUrl = _uiState.value.resolvedPortalUrl)
                ?: throw IllegalStateException("Unable to read active browser window. Ensure Chrome/browser is open.")

            val state = _uiState.value
            val prompt = userMessage?.trim()?.takeIf { it.isNotBlank() }
                ?: "Inspect active portal page and prepare next step"

            val step = apiProvider.service().agentChat(
                AgentChatRequest(
                    userId = state.userId!!,
                    profileId = state.profileId,
                    taskId = state.currentTaskId,
                    message = prompt,
                    language = state.language,
                    currentUrl = snapshot.url,
                    domSnapshot = snapshot
                )
            )

            _uiState.update { it.copy(lastSnapshot = snapshot, isBusy = false, fillCompleted = false) }
            applyAgentStep(step)
            step
        }.onFailure { err ->
            _uiState.update {
                it.copy(isBusy = false, errorMessage = err.message ?: "Screen capture failed")
            }
        }

    /**
     * Authorizes FILL_FIELD actions (AUTHORIZE_FIELD_FILL) and runs ACTION_SET_TEXT
     * via PortalAccessibilityService without allowing CLICK_BUTTON.
     */
    fun authorizeAndExecuteFieldFill(): Result<String> = runCatching {
        val taskId = _uiState.value.currentTaskId
            ?: throw IllegalStateException("No active task_id to authorize.")
        val fillActions = _uiState.value.pendingActions.filter { it.actionType == "FILL_FIELD" }
        if (fillActions.isEmpty()) {
            throw IllegalStateException("No pending FILL_FIELD actions from agent.")
        }
        val service = PortalAccessibilityService.instance
            ?: throw IllegalStateException("PortalAccessibilityService is not enabled.")

        authGate.authorizeFieldFill(taskId)
        val report = service.executeActions(
            taskId = taskId,
            actions = fillActions,
            actionHash = null,
            vaultStore = vaultStore,
            authGate = authGate
        )
        val summary = "Field Fill: ${report.executedCount} filled, ${report.failedCount} failed.\n" +
            report.logs.joinToString("\n")
        _uiState.update {
            it.copy(
                statusLog = summary,
                fillCompleted = report.executedCount > 0 && report.failedCount == 0,
                errorMessage = null
            )
        }
        summary
    }.onFailure { err ->
        _uiState.update { it.copy(errorMessage = err.message ?: "Field fill failed") }
    }

    /**
     * FR-027 Action-Bound Approval:
     * Calls POST /api/v1/tasks/{task_id}/approval with pending_action_hash BEFORE
     * permitting PortalAccessibilityService to execute CLICK_BUTTON (ACTION_CLICK).
     */
    suspend fun submitTaskApproval(approved: Boolean, reviewerNote: String? = null): Result<String> =
        runCatching {
            _uiState.update { it.copy(isBusy = true, errorMessage = null) }
            ensureAuthenticated().getOrThrow()

            val state = _uiState.value
            val taskId = state.currentTaskId
                ?: throw IllegalStateException("No active task_id for approval.")
            val actionHash = state.pendingActionHash
                ?: throw IllegalStateException("Missing pending_action_hash (FR-027).")

            val approvalResp = apiProvider.service().approveTask(
                taskId = taskId,
                payload = TaskApprovalRequest(
                    approved = approved,
                    actionHash = actionHash,
                    reviewerNote = reviewerNote?.trim()?.takeIf { it.isNotBlank() }
                )
            )

            var clickSummary = "Task ${approvalResp.status} (approved=${approvalResp.approved})."
            if (approved && approvalResp.approved) {
                authGate.approveTask(taskId, actionHash)
                val clickActions = state.pendingActions.filter { it.actionType == "CLICK_BUTTON" }
                if (clickActions.isNotEmpty()) {
                    val service = PortalAccessibilityService.instance
                    if (service != null) {
                        val report = service.executeActions(
                            taskId = taskId,
                            actions = clickActions,
                            actionHash = actionHash,
                            vaultStore = vaultStore,
                            authGate = authGate
                        )
                        clickSummary += " Submit click: ${report.executedCount} executed, ${report.failedCount} failed."
                    }
                } else if (state.lastSnapshot != null) {
                    val nextStep = apiProvider.service().agentChat(
                        AgentChatRequest(
                            userId = state.userId!!,
                            profileId = state.profileId,
                            taskId = taskId,
                            message = "Execute verified submit after approval",
                            language = state.language,
                            currentUrl = state.lastSnapshot.url,
                            domSnapshot = state.lastSnapshot
                        )
                    )
                    applyAgentStep(nextStep)
                    val service = PortalAccessibilityService.instance
                    val emittedClicks = nextStep.clientActions.filter { it.actionType == "CLICK_BUTTON" }
                    if (service != null && emittedClicks.isNotEmpty()) {
                        val report = service.executeActions(
                            taskId = taskId,
                            actions = emittedClicks,
                            actionHash = actionHash,
                            vaultStore = vaultStore,
                            authGate = authGate
                        )
                        clickSummary += " Submit click: ${report.executedCount} executed, ${report.failedCount} failed."
                    }
                }
            } else {
                authGate.revokeTask(taskId)
            }

            _uiState.update {
                it.copy(
                    isBusy = false,
                    agentStatus = approvalResp.status,
                    pendingActionHash = if (approved) null else it.pendingActionHash,
                    statusLog = clickSummary
                )
            }
            clickSummary
        }.onFailure { err ->
            _uiState.update {
                it.copy(isBusy = false, errorMessage = err.message ?: "Approval submission failed")
            }
        }

    /**
     * Uploads picked/captured document bytes to POST /documents/extract-and-validate
     * and stages extracted fields with mandatory verifiedByUser checkboxes (FR-037).
     */
    suspend fun uploadAndExtractDocument(
        bytes: ByteArray,
        fileName: String,
        mimeType: String,
        expectedDocType: String
    ): Result<DocumentExtractionResult> = runCatching {
        _uiState.update { it.copy(isBusy = true, errorMessage = null, vaultCommitSaved = false) }
        val api = apiProvider.service()

        val mediaType = mimeType.toMediaTypeOrNull() ?: "application/octet-stream".toMediaTypeOrNull()
        val filePart = MultipartBody.Part.createFormData(
            "file",
            fileName,
            bytes.toRequestBody(mediaType)
        )
        val textPlain = "text/plain".toMediaTypeOrNull()
        val docTypeBody = expectedDocType.toRequestBody(textPlain)
        val profileBody = _uiState.value.profileSlug.toRequestBody(textPlain)
        val langBody = _uiState.value.language.toRequestBody(textPlain)

        val result = api.extractAndValidateDocument(
            file = filePart,
            expectedDocType = docTypeBody,
            profileId = profileBody,
            profileFullName = null,
            language = langBody
        )

        val editable = result.fields.map { field ->
            EditableExtractedField(
                key = field.key,
                value = field.value,
                confidence = field.confidence,
                needsUserVerification = field.needsUserVerification,
                verifiedByUser = false
            )
        }

        _uiState.update {
            it.copy(
                isBusy = false,
                extractionResult = result,
                editableFields = editable,
                lastUploadedFileName = fileName,
                vaultCommitSaved = false,
                activeOverlayTab = OverlayTab.DOCUMENTS,
                statusLog = "Extracted ${editable.size} fields (${result.detectedDocType}). Check verifiedByUser before saving."
            )
        }
        result
    }.onFailure { err ->
        _uiState.update {
            it.copy(isBusy = false, errorMessage = err.message ?: "Document extraction failed")
        }
    }

    fun updateEditableField(index: Int, newValue: String? = null, verifiedByUser: Boolean? = null) {
        _uiState.update { state ->
            val updated = state.editableFields.mapIndexed { i, item ->
                if (i == index) {
                    item.copy(
                        value = newValue ?: item.value,
                        verifiedByUser = verifiedByUser ?: item.verifiedByUser
                    )
                } else {
                    item
                }
            }
            state.copy(editableFields = updated, vaultCommitSaved = false)
        }
    }

    /**
     * FR-037: Commits verified fields into Android Keystore EncryptedSharedPreferences
     * under keys `vault:<profileId>.<docType>.<fieldKey>`.
     */
    fun commitVerifiedFieldsToVault(docTypeOverride: String? = null): Result<Int> = runCatching {
        val state = _uiState.value
        val docType = docTypeOverride?.takeIf { it.isNotBlank() }
            ?: state.extractionResult?.detectedDocType
            ?: "unknown"
        val fields = state.editableFields
        if (fields.isEmpty()) {
            throw IllegalStateException("No extracted fields to save.")
        }
        val unverified = fields.filter { !it.verifiedByUser }
        if (unverified.isNotEmpty()) {
            throw SecurityException(
                "FR-037: Please check verifiedByUser for all fields before committing (${unverified.joinToString { it.key }})."
            )
        }

        var count = 0
        for (field in fields) {
            vaultStore.saveVerifiedField(
                profileId = state.profileSlug,
                docType = docType,
                fieldKey = field.key,
                value = field.value,
                confidence = field.confidence,
                verifiedByUser = field.verifiedByUser
            )
            count += 1
        }
        val summaries = vaultStore.listSummaries()
        _uiState.update {
            it.copy(
                vaultEntries = summaries,
                vaultCommitSaved = true,
                statusLog = "Committed $count verified fields to Keystore Vault under vault:${state.profileSlug}.$docType.*",
                errorMessage = null
            )
        }
        count
    }.onFailure { err ->
        _uiState.update { it.copy(errorMessage = err.message ?: "Vault commit blocked") }
    }

    private suspend fun applyAgentStep(step: AgentStepResponse) {
        var actionHash = step.approvalRequest?.actionPayload?.get("action_hash")?.toString()
        val taskId = step.taskId ?: _uiState.value.currentTaskId

        if (actionHash.isNullOrBlank() &&
            !taskId.isNullOrBlank() &&
            (step.status == "awaiting_submit_approval" || step.status == "awaiting_approval" || step.responseType == "approval_request")
        ) {
            actionHash = runCatching { apiProvider.service().getTask(taskId).pendingActionHash }.getOrNull()
        }

        val summaryMap = mutableMapOf<String, String>()
        val rawSummary = step.approvalRequest?.actionPayload?.get("summary_of_changes")
        if (rawSummary is Map<*, *>) {
            rawSummary.forEach { (k, v) ->
                if (k != null) summaryMap[k.toString()] = v?.toString().orEmpty()
            }
        }

        val isApprovalStep = !actionHash.isNullOrBlank() || step.responseType == "approval_request"
        _uiState.update {
            it.copy(
                currentTaskId = taskId,
                agentMessage = if (it.language == "hi" && !step.messageHi.isNullOrBlank()) step.messageHi else step.message,
                agentStatus = step.status,
                responseType = step.responseType,
                pendingActions = step.clientActions,
                pendingActionHash = actionHash,
                approvalWarning = step.approvalRequest?.warningMessage,
                approvalSummary = summaryMap,
                visualGuide = step.visualGuide ?: it.visualGuide,
                receiptData = step.receiptData ?: it.receiptData,
                activeOverlayTab = if (isApprovalStep) OverlayTab.APPROVAL else it.activeOverlayTab,
                statusLog = step.message
            )
        }
    }

    companion object {
        private const val KEY_BASE_URL = "api_base_url"
        private const val KEY_LANGUAGE = "language"
        private const val KEY_USER_ID = "user_id"
        private const val KEY_PROFILE_ID = "profile_id"
        private const val KEY_PROFILE_SLUG = "profile_slug"
        private const val KEY_AUTH_TOKEN = "auth_token"
    }
}
