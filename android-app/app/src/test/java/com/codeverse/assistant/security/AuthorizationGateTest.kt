package com.codeverse.assistant.security

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class AuthorizationGateTest {

    @Test
    fun `field fill authorization does not permit task submit click`() {
        val gate = AuthorizationGate()
        gate.authorizeFieldFill("task-123")

        assertTrue(gate.isFieldFillAuthorized("task-123"))
        assertFalse(gate.isTaskApproved("task-123"))
    }

    @Test
    fun `task approval binds strictly to pending action hash`() {
        val gate = AuthorizationGate()
        val hash = "sha256:deadbeef"
        gate.approveTask("task-123", hash)

        assertTrue(gate.isFieldFillAuthorized("task-123"))
        assertTrue(gate.isTaskApproved("task-123", hash))
        assertFalse(gate.isTaskApproved("task-123", "sha256:different"))
    }
}
