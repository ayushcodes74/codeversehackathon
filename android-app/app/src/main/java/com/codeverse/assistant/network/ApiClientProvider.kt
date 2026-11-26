package com.codeverse.assistant.network

import com.codeverse.assistant.BuildConfig
import okhttp3.Interceptor
import okhttp3.OkHttpClient
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import java.util.concurrent.TimeUnit

/**
 * Thread-safe Retrofit/OkHttp provider supporting dynamic base URL updates
 * and Bearer JWT + X-User-Id authentication headers.
 */
class ApiClientProvider(
    initialBaseUrl: String = BuildConfig.DEFAULT_API_BASE_URL
) {
    @Volatile
    private var baseUrl: String = normalizeBaseUrl(initialBaseUrl)

    @Volatile
    private var authToken: String? = null

    @Volatile
    private var userId: String? = null

    @Volatile
    private var cachedService: AssistantApiService? = null

    private val authInterceptor = Interceptor { chain ->
        val original = chain.request()
        val builder = original.newBuilder()
        authToken?.takeIf { it.isNotBlank() }?.let { token ->
            builder.header("Authorization", "Bearer $token")
        }
        userId?.takeIf { it.isNotBlank() }?.let { uid ->
            builder.header("X-User-Id", uid)
        }
        chain.proceed(builder.build())
    }

    private val okHttpClient: OkHttpClient = OkHttpClient.Builder()
        .addInterceptor(authInterceptor)
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(45, TimeUnit.SECONDS)
        .writeTimeout(45, TimeUnit.SECONDS)
        .build()

    @Synchronized
    fun updateConfig(
        newBaseUrl: String? = null,
        newAuthToken: String? = null,
        newUserId: String? = null
    ) {
        if (newAuthToken != null) {
            authToken = newAuthToken
        }
        if (newUserId != null) {
            userId = newUserId
        }
        if (!newBaseUrl.isNullOrBlank()) {
            val normalized = normalizeBaseUrl(newBaseUrl)
            if (normalized != baseUrl) {
                baseUrl = normalized
                cachedService = null
            }
        }
    }

    fun currentBaseUrl(): String = baseUrl

    @Synchronized
    fun service(): AssistantApiService {
        val existing = cachedService
        if (existing != null) return existing
        val created = Retrofit.Builder()
            .baseUrl(baseUrl)
            .client(okHttpClient)
            .addConverterFactory(GsonConverterFactory.create())
            .build()
            .create(AssistantApiService::class.java)
        cachedService = created
        return created
    }

    companion object {
        fun normalizeBaseUrl(raw: String): String {
            val trimmed = raw.trim().ifEmpty { BuildConfig.DEFAULT_API_BASE_URL }
            val withScheme = if (trimmed.startsWith("http://") || trimmed.startsWith("https://")) {
                trimmed
            } else {
                "http://$trimmed"
            }
            return if (withScheme.endsWith("/")) withScheme else "$withScheme/"
        }
    }
}
