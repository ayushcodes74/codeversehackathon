package com.codeverse.assistant.security

import java.util.concurrent.ConcurrentHashMap

/**
 * Enforces the two-tier human-in-the-loop security boundary before
 * PortalAccessibilityService can modify or submit external browser forms:
 *
 * 1. AUTHORIZE_FIELD_FILL -> permits FILL_FIELD actions only (ACTION_SET_TEXT).
 * 2. APPROVE_TASK (FR-027) -> bound to `pending_action_hash` returned after
 *    POST /api/v1/tasks/{task_id}/approval succeeds; permits CLICK_BUTTON / submit.
 */
class AuthorizationGate {
    private val authorizedFieldFillTasks = ConcurrentHashMap<String, Boolean>()
    private val approvedTaskHashes = ConcurrentHashMap<String, String>()

    fun authorizeFieldFill(taskId: String) {
        val cleanId = taskId.trim()
        require(cleanId.isNotEmpty()) { "task_id is required for AUTHORIZE_FIELD_FILL." }
        authorizedFieldFillTasks[cleanId] = true
    }

    fun approveTask(taskId: String, actionHash: String) {
        val cleanId = taskId.trim()
        val cleanHash = actionHash.trim()
        require(cleanId.isNotEmpty()) { "task_id is required for APPROVE_TASK." }
        require(cleanHash.isNotEmpty()) { "action_hash is required for APPROVE_TASK." }
        approvedTaskHashes[cleanId] = cleanHash
    }

    fun revokeTask(taskId: String) {
        val cleanId = taskId.trim()
        authorizedFieldFillTasks.remove(cleanId)
        approvedTaskHashes.remove(cleanId)
    }

    fun isFieldFillAuthorized(taskId: String?): Boolean {
        if (taskId.isNullOrBlank()) return false
        val id = taskId.trim()
        return authorizedFieldFillTasks[id] == true || approvedTaskHashes.containsKey(id)
    }

    fun isTaskApproved(taskId: String?, actionHash: String? = null): Boolean {
        if (taskId.isNullOrBlank()) return false
        val recordedHash = approvedTaskHashes[taskId.trim()] ?: return false
        if (actionHash.isNullOrBlank()) return true
        return recordedHash == actionHash.trim()
    }

    fun approvedHashFor(taskId: String?): String? {
        if (taskId.isNullOrBlank()) return null
        return approvedTaskHashes[taskId.trim()]
    }
}
