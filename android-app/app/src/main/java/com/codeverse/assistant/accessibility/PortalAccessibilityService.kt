package com.codeverse.assistant.accessibility

import android.accessibilityservice.AccessibilityService
import android.graphics.Rect
import android.os.Build
import android.os.Bundle
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityWindowInfo
import com.codeverse.assistant.network.ClientAction
import com.codeverse.assistant.network.DOMBoundingBox
import com.codeverse.assistant.network.DOMButton
import com.codeverse.assistant.network.DOMField
import com.codeverse.assistant.network.DOMSnapshot
import com.codeverse.assistant.security.AuthorizationGate
import com.codeverse.assistant.vault.EncryptedVaultStore
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.util.concurrent.ConcurrentHashMap

data class ActionExecutionReport(
    val executedCount: Int,
    val failedCount: Int,
    val logs: List<String>
)

/**
 * Cross-Browser Accessibility Screen Reader & Form Automator.
 *
 * Traverses the active mobile browser AccessibilityNodeInfo hierarchy (Chrome, Edge,
 * Firefox, Brave, Samsung Internet, etc.), extracts viewIdResourceName, contentDescription,
 * hintText, className, and bounding boxes into a DOMSnapshot, and executes authorized
 * FILL_FIELD (ACTION_SET_TEXT) and approved CLICK_BUTTON (ACTION_CLICK) actions.
 */
class PortalAccessibilityService : AccessibilityService() {

    private data class IndexedNodeMeta(
        val elementKey: String,
        val selector: String,
        val viewIdResourceName: String?,
        val label: String,
        val bounds: Rect,
        val isField: Boolean
    )

    private val indexedMetaByKey = ConcurrentHashMap<String, IndexedNodeMeta>()

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
        _isConnected.value = true
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        val pkg = event?.packageName?.toString()
        if (!pkg.isNullOrBlank() && pkg != packageName) {
            _activePackageName.value = pkg
        }
    }

    override fun onInterrupt() {
        // No-op: user or OS interrupted accessibility feedback.
    }

    override fun onDestroy() {
        super.onDestroy()
        if (instance === this) {
            instance = null
        }
        _isConnected.value = false
        indexedMetaByKey.clear()
    }

    /**
     * Captures the live AccessibilityNodeInfo tree from the active browser window
     * and converts it into a DOMSnapshot matching POST /api/v1/agent/chat.
     */
    fun captureDomSnapshot(fallbackUrl: String? = null): DOMSnapshot? {
        val browserRoot = findActiveBrowserRoot() ?: return null
        val pkg = browserRoot.packageName?.toString().orEmpty()
        if (pkg.isNotBlank() && pkg != packageName) {
            _activePackageName.value = pkg
        }

        indexedMetaByKey.clear()

        var detectedUrl: String? = null
        var detectedTitle = ""
        val fields = mutableListOf<DOMField>()
        val buttons = mutableListOf<DOMButton>()
        val textSnippets = mutableListOf<String>()
        var fieldIdx = 1
        var btnIdx = 1

        fun traverse(node: AccessibilityNodeInfo?, depth: Int) {
            if (node == null || depth > MAX_TRAVERSAL_DEPTH) return
            if (!node.isVisibleToUser) return

            val viewId = node.viewIdResourceName
            val rawText = node.text?.toString()?.trim().orEmpty()
            val contentDesc = node.contentDescription?.toString()?.trim().orEmpty()
            val hintText = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                node.hintText?.toString()?.trim().orEmpty()
            } else {
                ""
            }
            val className = node.className?.toString().orEmpty()
            val simpleClass = className.substringAfterLast('.')

            // 1. Detect browser URL bar
            if (detectedUrl == null && isUrlBarNode(viewId, rawText, hintText, contentDesc)) {
                val candidate = listOf(rawText, contentDesc).firstOrNull { it.isNotBlank() }
                if (!candidate.isNullOrBlank()) {
                    detectedUrl = normalizeBrowserUrl(candidate)
                }
            }

            // 2. Skip browser chrome toolbar widgets (tabs, menu, back/forward, url bar itself)
            val isBrowserToolbarControl = isBrowserChromeControl(viewId)
            val rect = Rect()
            node.getBoundsInScreen(rect)
            val hasValidBounds = rect.width() > 0 && rect.height() > 0

            if (!isBrowserToolbarControl && hasValidBounds) {
                val combinedLabel = listOf(contentDesc, hintText, rawText)
                    .firstOrNull { it.isNotBlank() }
                    .orEmpty()

                if (detectedTitle.isBlank() && simpleClass.contains("WebView", ignoreCase = true) && combinedLabel.isNotBlank()) {
                    detectedTitle = combinedLabel.take(120)
                }

                when {
                    isFormFieldNode(node, simpleClass) -> {
                        val key = "field_${fieldIdx.toString().padStart(2, '0')}"
                        fieldIdx += 1

                        val idToken = viewId?.substringAfter(":id/")?.takeIf { it.isNotBlank() }
                        val selector = buildSelector(idToken, combinedLabel, key)
                        val inputType = inferInputType(node, simpleClass, combinedLabel)
                        val tagName = inferTagName(node, simpleClass)
                        val label = listOf(hintText, contentDesc)
                            .firstOrNull { it.isNotBlank() }
                            ?: if (node.isCheckable) rawText else ""

                        val domField = DOMField(
                            elementKey = key,
                            selector = selector,
                            tagName = tagName,
                            inputType = inputType,
                            label = label.ifBlank { idToken ?: key },
                            name = idToken,
                            id = idToken,
                            placeholder = hintText.ifBlank { null },
                            isRequired = label.contains("*"),
                            isDisabled = !node.isEnabled,
                            currentValue = when {
                                node.isCheckable -> node.isChecked.toString()
                                rawText.isNotBlank() && rawText != hintText -> rawText
                                else -> null
                            },
                            boundingBox = DOMBoundingBox(
                                x = rect.left.toFloat(),
                                y = rect.top.toFloat(),
                                width = rect.width().toFloat(),
                                height = rect.height().toFloat()
                            )
                        )
                        fields += domField
                        indexedMetaByKey[key] = IndexedNodeMeta(
                            elementKey = key,
                            selector = selector,
                            viewIdResourceName = viewId,
                            label = domField.label,
                            bounds = Rect(rect),
                            isField = true
                        )
                    }

                    isActionButtonNode(node, simpleClass, combinedLabel) -> {
                        val key = "btn_${btnIdx.toString().padStart(2, '0')}"
                        btnIdx += 1
                        val idToken = viewId?.substringAfter(":id/")?.takeIf { it.isNotBlank() }
                        val selector = buildSelector(idToken, combinedLabel, key)
                        val btnText = combinedLabel.ifBlank { idToken ?: "Action" }
                        val lower = btnText.lowercase()
                        val btnType = if ("submit" in lower) "submit" else "button"

                        buttons += DOMButton(
                            elementKey = key,
                            selector = selector,
                            text = btnText.take(80),
                            buttonType = btnType,
                            isPrimary = btnType == "submit" || "next" in lower || "continue" in lower,
                            isDisabled = !node.isEnabled
                        )
                        indexedMetaByKey[key] = IndexedNodeMeta(
                            elementKey = key,
                            selector = selector,
                            viewIdResourceName = viewId,
                            label = btnText,
                            bounds = Rect(rect),
                            isField = false
                        )
                    }

                    rawText.isNotBlank() && textSnippets.sumOf { it.length } < MAX_SNIPPET_CHARS -> {
                        if (rawText.length > 2) {
                            textSnippets += rawText
                        }
                    }
                }
            }

            for (i in 0 until node.childCount) {
                traverse(node.getChild(i), depth + 1)
            }
        }

        traverse(browserRoot, 0)

        val resolvedUrl = (detectedUrl ?: fallbackUrl.orEmpty()).ifBlank { "https://localhost" }
        val fullSnippet = textSnippets.distinct().joinToString(" | ").take(MAX_SNIPPET_CHARS)
        val stepMatch = Regex("step\\s+(\\d+)\\s+of\\s+(\\d+)", RegexOption.IGNORE_CASE).find(fullSnippet)
        val currentStep = stepMatch?.groupValues?.getOrNull(1)?.toIntOrNull() ?: 1
        val totalSteps = stepMatch?.groupValues?.getOrNull(2)?.toIntOrNull() ?: 1

        return DOMSnapshot(
            url = resolvedUrl,
            title = detectedTitle.ifBlank { pkg.ifBlank { "Mobile Browser Page" } },
            stepIndicator = stepMatch?.value,
            currentStepNumber = currentStep,
            totalSteps = totalSteps,
            fields = fields,
            buttons = buttons,
            textContentSnippet = fullSnippet.ifBlank { null },
            timestamp = System.currentTimeMillis().toDouble()
        )
    }

    /**
     * Executes ClientActions inside the active browser ONLY after AuthorizationGate permits them:
     * - FILL_FIELD -> requires AUTHORIZE_FIELD_FILL (or APPROVE_TASK)
     * - CLICK_BUTTON -> strictly requires APPROVE_TASK with matching pending_action_hash
     */
    fun executeActions(
        taskId: String,
        actions: List<ClientAction>,
        actionHash: String?,
        vaultStore: EncryptedVaultStore,
        authGate: AuthorizationGate
    ): ActionExecutionReport {
        val logs = mutableListOf<String>()
        var executed = 0
        var failed = 0

        val root = findActiveBrowserRoot()
        if (root == null) {
            return ActionExecutionReport(
                executedCount = 0,
                failedCount = actions.size,
                logs = listOf("Active browser window not found. Bring Chrome/browser to foreground.")
            )
        }

        for (action in actions) {
            when (action.actionType) {
                "FILL_FIELD" -> {
                    if (!authGate.isFieldFillAuthorized(taskId)) {
                        failed += 1
                        logs += "Blocked FILL_FIELD (${action.elementKey}): AUTHORIZE_FIELD_FILL required."
                        continue
                    }

                    val rawValue = action.valueOrVaultRef.orEmpty()
                    val resolvedValue = if (rawValue.startsWith(EncryptedVaultStore.VAULT_PREFIX)) {
                        try {
                            vaultStore.resolveVerifiedToken(rawValue)
                        } catch (sec: SecurityException) {
                            failed += 1
                            logs += "FR-037 blocked vault token ${action.elementKey}: ${sec.message}"
                            continue
                        }
                    } else {
                        rawValue
                    }

                    if (resolvedValue == null) {
                        failed += 1
                        logs += "Missing verified vault value for ${action.elementKey} ($rawValue)."
                        continue
                    }

                    val targetNode = locateTargetNode(root, action)
                    if (targetNode == null) {
                        failed += 1
                        logs += "Node not found for ${action.elementKey ?: action.selector}."
                        continue
                    }

                    val ok = performFillOnNode(targetNode, resolvedValue)
                    if (ok) {
                        executed += 1
                        logs += "Filled ${action.elementKey ?: action.selector} via AccessibilityNodeInfo."
                    } else {
                        failed += 1
                        logs += "ACTION_SET_TEXT failed on ${action.elementKey ?: action.selector}."
                    }
                }

                "CLICK_BUTTON" -> {
                    if (!authGate.isTaskApproved(taskId, actionHash)) {
                        failed += 1
                        logs += "Blocked CLICK_BUTTON (${action.elementKey}): APPROVE_TASK (FR-027) required."
                        continue
                    }

                    val targetNode = locateTargetNode(root, action)
                    if (targetNode == null) {
                        failed += 1
                        logs += "Button node not found for ${action.elementKey ?: action.selector}."
                        continue
                    }

                    val ok = performClickOnNode(targetNode)
                    if (ok) {
                        executed += 1
                        logs += "Clicked '${action.description.ifBlank { action.elementKey ?: "button" }}' via ACTION_CLICK."
                    } else {
                        failed += 1
                        logs += "ACTION_CLICK failed on ${action.elementKey ?: action.selector}."
                    }
                }

                else -> {
                    logs += "Skipped non-DOM action ${action.actionType}."
                }
            }
        }

        return ActionExecutionReport(executed, failed, logs)
    }

    private fun performFillOnNode(node: AccessibilityNodeInfo, value: String): Boolean {
        node.performAction(AccessibilityNodeInfo.ACTION_FOCUS)
        if (node.isCheckable) {
            val wantChecked = value.equals("true", ignoreCase = true) ||
                value.equals("yes", ignoreCase = true) ||
                value == "1"
            if (node.isChecked != wantChecked) {
                return performClickOnNode(node)
            }
            return true
        }
        val args = Bundle().apply {
            putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, value)
        }
        return node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
    }

    private fun performClickOnNode(node: AccessibilityNodeInfo): Boolean {
        var cursor: AccessibilityNodeInfo? = node
        var hops = 0
        while (cursor != null && hops < 5) {
            if (cursor.isClickable && cursor.isEnabled) {
                return cursor.performAction(AccessibilityNodeInfo.ACTION_CLICK)
            }
            cursor = cursor.parent
            hops += 1
        }
        return node.performAction(AccessibilityNodeInfo.ACTION_CLICK)
    }

    private fun locateTargetNode(root: AccessibilityNodeInfo, action: ClientAction): AccessibilityNodeInfo? {
        val meta = action.elementKey?.let { indexedMetaByKey[it] }
            ?: indexedMetaByKey.values.firstOrNull { it.selector == action.selector }

        val targetId = meta?.viewIdResourceName
        val targetLabel = meta?.label
        val targetBounds = meta?.bounds
        val selectorId = action.selector?.removePrefix("#")?.takeIf {
            !action.selector.contains("[") && !action.selector.contains(" ")
        }

        var bestMatch: AccessibilityNodeInfo? = null

        fun search(node: AccessibilityNodeInfo?, depth: Int) {
            if (node == null || depth > MAX_TRAVERSAL_DEPTH || bestMatch != null) return
            if (!node.isVisibleToUser) return

            val viewId = node.viewIdResourceName
            val rect = Rect().also { node.getBoundsInScreen(it) }
            val hint = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                node.hintText?.toString()?.trim().orEmpty()
            } else ""
            val desc = node.contentDescription?.toString()?.trim().orEmpty()
            val text = node.text?.toString()?.trim().orEmpty()

            val idMatches = (!targetId.isNullOrBlank() && viewId == targetId) ||
                (!selectorId.isNullOrBlank() && viewId?.endsWith(":id/$selectorId") == true)

            val boundsMatch = targetBounds != null &&
                kotlin.math.abs(rect.left - targetBounds.left) <= 12 &&
                kotlin.math.abs(rect.top - targetBounds.top) <= 12 &&
                kotlin.math.abs(rect.width() - targetBounds.width()) <= 12

            val labelMatches = !targetLabel.isNullOrBlank() &&
                (hint.equals(targetLabel, ignoreCase = true) ||
                    desc.equals(targetLabel, ignoreCase = true) ||
                    text.equals(targetLabel, ignoreCase = true))

            val interactiveForAction = if (action.actionType == "FILL_FIELD") {
                node.isEditable || node.isCheckable || node.className?.toString()?.contains("EditText") == true
            } else {
                node.isClickable || node.className?.toString()?.contains("Button") == true
            }

            if (interactiveForAction && (boundsMatch || idMatches || labelMatches)) {
                bestMatch = node
                return
            }

            for (i in 0 until node.childCount) {
                search(node.getChild(i), depth + 1)
            }
        }

        search(root, 0)
        return bestMatch
    }

    /**
     * Selects the active browser AccessibilityWindowInfo even when our overlay
     * sheet is currently focused on screen.
     */
    private fun findActiveBrowserRoot(): AccessibilityNodeInfo? {
        val windowList = runCatching { windows }.getOrNull().orEmpty()
        val appRoots = windowList
            .asSequence()
            .filter { it.type == AccessibilityWindowInfo.TYPE_APPLICATION }
            .mapNotNull { it.root }
            .filter { it.packageName?.toString() != packageName }
            .toList()

        val preferredBrowser = appRoots.firstOrNull { root ->
            val pkg = root.packageName?.toString().orEmpty()
            pkg in KNOWN_BROWSER_PACKAGES
        }
        if (preferredBrowser != null) return preferredBrowser
        if (appRoots.isNotEmpty()) return appRoots.first()

        val activeRoot = rootInActiveWindow
        if (activeRoot != null && activeRoot.packageName?.toString() != packageName) {
            return activeRoot
        }
        return null
    }

    private fun isUrlBarNode(
        viewId: String?,
        rawText: String,
        hintText: String,
        contentDesc: String
    ): Boolean {
        val idLower = viewId?.lowercase().orEmpty()
        if (URL_BAR_ID_HINTS.any { idLower.contains(it) }) {
            return true
        }
        if (rawText.startsWith("http://") || rawText.startsWith("https://")) {
            return true
        }
        val combinedHint = "$hintText $contentDesc".lowercase()
        if (("search or type" in combinedHint || "address bar" in combinedHint) && rawText.contains(".")) {
            return true
        }
        return false
    }

    private fun isBrowserChromeControl(viewId: String?): Boolean {
        val idLower = viewId?.lowercase().orEmpty()
        if (idLower.isBlank()) return false
        return BROWSER_CHROME_IDS.any { idLower.endsWith(":id/$it") || idLower.contains(it) }
    }

    private fun isFormFieldNode(node: AccessibilityNodeInfo, simpleClass: String): Boolean {
        if (node.isEditable || node.isCheckable) return true
        val cls = simpleClass.lowercase()
        return cls.contains("edittext") ||
            cls.contains("spinner") ||
            cls.contains("checkbox") ||
            cls.contains("radiobutton") ||
            cls.contains("switch")
    }

    private fun isActionButtonNode(
        node: AccessibilityNodeInfo,
        simpleClass: String,
        label: String
    ): Boolean {
        val cls = simpleClass.lowercase()
        if (cls.contains("button") && node.isClickable) return true
        if (node.isClickable && !node.isEditable && label.isNotBlank() && label.length <= 60) {
            val lower = label.lowercase()
            return ACTION_KEYWORDS.any { it in lower }
        }
        return false
    }

    private fun inferTagName(node: AccessibilityNodeInfo, simpleClass: String): String {
        val cls = simpleClass.lowercase()
        return when {
            cls.contains("spinner") -> "select"
            else -> "input"
        }
    }

    private fun inferInputType(
        node: AccessibilityNodeInfo,
        simpleClass: String,
        label: String
    ): String {
        val cls = simpleClass.lowercase()
        val lowerLabel = label.lowercase()
        return when {
            cls.contains("checkbox") -> "checkbox"
            cls.contains("radiobutton") -> "radio"
            cls.contains("spinner") -> "select"
            node.isPassword -> "password"
            "email" in lowerLabel -> "email"
            "phone" in lowerLabel || "mobile" in lowerLabel -> "tel"
            "date" in lowerLabel || "dob" in lowerLabel -> "date"
            else -> "text"
        }
    }

    private fun buildSelector(idToken: String?, label: String, fallbackKey: String): String {
        if (!idToken.isNullOrBlank()) return "#$idToken"
        if (label.isNotBlank()) {
            val sanitized = label.replace("\"", "").take(40)
            return "[aria-label=\"$sanitized\"]"
        }
        return "[data-agy-key=\"$fallbackKey\"]"
    }

    private fun normalizeBrowserUrl(raw: String): String {
        val clean = raw.trim()
        if (clean.startsWith("http://") || clean.startsWith("https://")) {
            return clean
        }
        val scheme = if (clean.startsWith("localhost") || clean.startsWith("127.0.0.1") || clean.startsWith("10.0.2.2")) {
            "http://"
        } else {
            "https://"
        }
        return "$scheme$clean"
    }

    companion object {
        private const val MAX_TRAVERSAL_DEPTH = 32
        private const val MAX_SNIPPET_CHARS = 1500

        private val KNOWN_BROWSER_PACKAGES = setOf(
            "com.android.chrome",
            "com.chrome.beta",
            "com.chrome.dev",
            "com.chrome.canary",
            "com.microsoft.emmx",
            "org.mozilla.firefox",
            "com.brave.browser",
            "com.sec.android.app.sbrowser",
            "com.opera.browser",
            "com.duckduckgo.mobile.android"
        )

        private val URL_BAR_ID_HINTS = listOf(
            "url_bar",
            "location_bar_edit_text",
            "mozac_browser_toolbar_url_view",
            "url_field",
            "omnibox"
        )

        private val BROWSER_CHROME_IDS = listOf(
            "url_bar",
            "location_bar",
            "tab_switcher_button",
            "menu_button",
            "home_button",
            "back_button",
            "forward_button",
            "refresh_button",
            "search_box_text",
            "toolbar_buttons"
        )

        private val ACTION_KEYWORDS = listOf(
            "submit",
            "next",
            "continue",
            "proceed",
            "save",
            "verify",
            "register",
            "login",
            "apply",
            "upload"
        )

        @Volatile
        var instance: PortalAccessibilityService? = null
            private set

        private val _isConnected = MutableStateFlow(false)
        val isConnected: StateFlow<Boolean> = _isConnected.asStateFlow()

        private val _activePackageName = MutableStateFlow<String?>(null)
        val activePackageName: StateFlow<String?> = _activePackageName.asStateFlow()
    }
}
