package com.codeverse.assistant.vault

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import com.google.gson.Gson

/**
 * Backend storage abstraction so Keystore-backed EncryptedSharedPreferences
 * can be swapped with an in-memory map in JVM unit tests.
 */
interface VaultStorageBackend {
    fun putString(key: String, value: String)
    fun getString(key: String): String?
    fun allKeys(): Set<String>
    fun remove(key: String)
    fun clear()
}

class EncryptedPrefsVaultBackend(context: Context) : VaultStorageBackend {
    private val masterKey: MasterKey = MasterKey.Builder(context.applicationContext)
        .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
        .build()

    private val prefs: SharedPreferences = EncryptedSharedPreferences.create(
        context.applicationContext,
        PREFS_FILE_NAME,
        masterKey,
        EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
        EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM
    )

    override fun putString(key: String, value: String) {
        prefs.edit().putString(key, value).apply()
    }

    override fun getString(key: String): String? = prefs.getString(key, null)

    override fun allKeys(): Set<String> = prefs.all.keys.toSet()

    override fun remove(key: String) {
        prefs.edit().remove(key).apply()
    }

    override fun clear() {
        prefs.edit().clear().apply()
    }

    companion object {
        private const val PREFS_FILE_NAME = "ua_keystore_vault_prefs"
    }
}

data class VaultStoredPayload(
    val value: String,
    val verifiedByUser: Boolean,
    val confidence: Double,
    val updatedAtEpochMs: Long
)

data class VaultEntrySummary(
    val vaultKey: String,
    val profileId: String,
    val docType: String,
    val fieldKey: String,
    val maskedValue: String,
    val confidence: Double,
    val verifiedByUser: Boolean,
    val updatedAtEpochMs: Long
)

/**
 * FR-037 Keystore-backed Local Encrypted Vault.
 *
 * Stores every verified extracted field strictly under key:
 * `vault:<profileId>.<docType>.<fieldKey>`
 *
 * Enforces `verifiedByUser == true` on both write and token resolution.
 */
class EncryptedVaultStore(
    private val backend: VaultStorageBackend,
    private val gson: Gson = Gson()
) {
    constructor(context: Context) : this(EncryptedPrefsVaultBackend(context))

    fun saveVerifiedField(
        profileId: String,
        docType: String,
        fieldKey: String,
        value: String,
        confidence: Double,
        verifiedByUser: Boolean,
        nowEpochMs: Long = System.currentTimeMillis()
    ): String {
        if (!verifiedByUser) {
            throw SecurityException(
                "FR-037 violation: Field '$fieldKey' cannot be saved without verifiedByUser == true."
            )
        }
        val key = buildVaultKey(profileId, docType, fieldKey)
        val payload = VaultStoredPayload(
            value = value,
            verifiedByUser = true,
            confidence = confidence,
            updatedAtEpochMs = nowEpochMs
        )
        backend.putString(key, gson.toJson(payload))
        return key
    }

    /**
     * Resolves `vault:<profileId>.<docType>.<fieldKey>` (including aliases) ONLY when
     * `verifiedByUser == true`. Callers must enforce AuthorizationGate first.
     */
    fun resolveVerifiedToken(rawToken: String): String? {
        val canonical = standardizeVaultToken(rawToken) ?: return null
        val candidateKeys = candidateLookupKeys(canonical)
        for (candidate in candidateKeys) {
            val rawJson = backend.getString(candidate) ?: continue
            val parsed = runCatching { gson.fromJson(rawJson, VaultStoredPayload::class.java) }.getOrNull()
                ?: continue
            if (!parsed.verifiedByUser) {
                throw SecurityException("FR-037 violation: Vault entry '$candidate' is not verifiedByUser.")
            }
            return parsed.value
        }
        return null
    }

    fun listSummaries(profileFilter: String? = null): List<VaultEntrySummary> {
        val normalizedFilter = profileFilter?.trim()?.lowercase()
        return backend.allKeys()
            .asSequence()
            .filter { it.startsWith(VAULT_PREFIX) }
            .mapNotNull { key ->
                val parts = parseVaultKey(key) ?: return@mapNotNull null
                if (!normalizedFilter.isNullOrBlank() && parts.first != normalizedFilter) {
                    return@mapNotNull null
                }
                val rawJson = backend.getString(key) ?: return@mapNotNull null
                val payload = runCatching { gson.fromJson(rawJson, VaultStoredPayload::class.java) }.getOrNull()
                    ?: return@mapNotNull null
                VaultEntrySummary(
                    vaultKey = key,
                    profileId = parts.first,
                    docType = parts.second,
                    fieldKey = parts.third,
                    maskedValue = maskSensitiveValue(key, payload.value),
                    confidence = payload.confidence,
                    verifiedByUser = payload.verifiedByUser,
                    updatedAtEpochMs = payload.updatedAtEpochMs
                )
            }
            .sortedBy { it.vaultKey }
            .toList()
    }

    fun deleteEntry(vaultKey: String) {
        backend.remove(vaultKey)
    }

    fun clearAll() {
        backend.clear()
    }

    companion object {
        const val VAULT_PREFIX = "vault:"

        fun buildVaultKey(profileId: String, docType: String, fieldKey: String): String {
            val p = normalizeSegment(profileId, default = "self")
            val d = normalizeSegment(docType, default = "unknown")
            val f = normalizeSegment(fieldKey, default = "field")
            return "$VAULT_PREFIX$p.$d.$f"
        }

        fun standardizeVaultToken(rawToken: String): String? {
            val trimmed = rawToken.trim()
            if (!trimmed.startsWith(VAULT_PREFIX)) return null
            val body = trimmed.removePrefix(VAULT_PREFIX)
            val segments = body.split('.')
            if (segments.size < 3) return trimmed.lowercase()
            val profile = normalizeSegment(segments[0], "self")
            val docType = normalizeSegment(segments[1], "unknown")
            val rawField = normalizeSegment(segments.drop(2).joinToString("_"), "field")
            val canonicalField = when {
                docType == "pan" && rawField in setOf("id_number", "pan", "pan_no") -> "pan_number"
                docType == "aadhaar" && rawField in setOf("id_number", "aadhaar", "uid") -> "aadhaar_number"
                docType == "bank_passbook" && rawField in setOf("acct_no", "account_no") -> "account_number"
                docType == "bank_passbook" && rawField in setOf("ifsc", "ifsc_no") -> "ifsc_code"
                else -> rawField
            }
            return "$VAULT_PREFIX$profile.$docType.$canonicalField"
        }

        private fun candidateLookupKeys(canonical: String): List<String> {
            val parts = parseVaultKey(canonical) ?: return listOf(canonical)
            val (profile, docType, field) = parts
            val list = mutableListOf(canonical)
            if (docType == "pan" && field == "pan_number") {
                list += buildVaultKey(profile, docType, "id_number")
            } else if (docType == "aadhaar" && field == "aadhaar_number") {
                list += buildVaultKey(profile, docType, "id_number")
            }
            return list.distinct()
        }

        private fun parseVaultKey(key: String): Triple<String, String, String>? {
            if (!key.startsWith(VAULT_PREFIX)) return null
            val parts = key.removePrefix(VAULT_PREFIX).split('.')
            if (parts.size != 3) return null
            return Triple(parts[0], parts[1], parts[2])
        }

        private fun normalizeSegment(raw: String, default: String): String {
            val clean = raw.trim().lowercase().replace(Regex("[^a-z0-9_]+"), "_").trim('_')
            return clean.ifEmpty { default }
        }

        fun maskSensitiveValue(vaultKey: String, value: String): String {
            if (value.isBlank()) return ""
            val lower = vaultKey.lowercase()
            if (lower.contains("aadhaar")) {
                return "[Aadhaar Redacted]"
            }
            if (lower.contains(".pan.") || lower.endsWith("pan_number")) {
                return if (value.length >= 6) {
                    "${value.take(5)}****${value.takeLast(1)}"
                } else {
                    "****"
                }
            }
            if (lower.contains("account_number") || lower.contains("ifsc")) {
                return if (value.length <= 4) "****" else "*".repeat(value.length - 4) + value.takeLast(4)
            }
            return value
        }
    }
}
