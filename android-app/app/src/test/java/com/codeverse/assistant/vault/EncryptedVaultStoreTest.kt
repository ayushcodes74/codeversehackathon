package com.codeverse.assistant.vault

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

private class InMemoryVaultBackend : VaultStorageBackend {
    private val map = mutableMapOf<String, String>()
    override fun putString(key: String, value: String) { map[key] = value }
    override fun getString(key: String): String? = map[key]
    override fun allKeys(): Set<String> = map.keys.toSet()
    override fun remove(key: String) { map.remove(key) }
    override fun clear() { map.clear() }
}

class EncryptedVaultStoreTest {

    @Test
    fun `rejects unverified field save under FR-037`() {
        val store = EncryptedVaultStore(InMemoryVaultBackend())
        assertThrows(SecurityException::class.java) {
            store.saveVerifiedField(
                profileId = "self",
                docType = "pan",
                fieldKey = "pan_number",
                value = "ABCDE1234F",
                confidence = 0.72,
                verifiedByUser = false
            )
        }
    }

    @Test
    fun `stores and resolves under vault profile docType fieldKey`() {
        val backend = InMemoryVaultBackend()
        val store = EncryptedVaultStore(backend)

        val key = store.saveVerifiedField(
            profileId = "self",
            docType = "pan",
            fieldKey = "pan_number",
            value = "ABCDE1234F",
            confidence = 0.98,
            verifiedByUser = true
        )

        assertEquals("vault:self.pan.pan_number", key)
        assertTrue(backend.allKeys().contains("vault:self.pan.pan_number"))
        assertEquals("ABCDE1234F", store.resolveVerifiedToken("vault:self.pan.id_number"))
        assertNull(store.resolveVerifiedToken("vault:self.aadhaar.aadhaar_number"))
    }

    @Test
    fun `masks sensitive values in summaries`() {
        val store = EncryptedVaultStore(InMemoryVaultBackend())
        store.saveVerifiedField("self", "aadhaar", "aadhaar_number", "123456789012", 0.99, true)
        store.saveVerifiedField("self", "pan", "pan_number", "ABCDE1234F", 0.99, true)

        val summaries = store.listSummaries("self").associateBy { it.vaultKey }
        assertEquals("[Aadhaar Redacted]", summaries["vault:self.aadhaar.aadhaar_number"]?.maskedValue)
        assertEquals("ABCDE****F", summaries["vault:self.pan.pan_number"]?.maskedValue)
    }
}
