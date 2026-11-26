package com.codeverse.assistant.network

import okhttp3.MultipartBody
import okhttp3.RequestBody
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.Multipart
import retrofit2.http.POST
import retrofit2.http.Part
import retrofit2.http.Path
import retrofit2.http.Query

interface AssistantApiService {

    @POST("api/v1/users/auth")
    suspend fun upsertUser(@Body payload: GoogleAuthUpsertRequest): UserAuthResponse

    @POST("api/v1/auth/dev-token")
    suspend fun issueDevToken(@Body payload: DevTokenRequest): DevTokenResponse

    @POST("api/v1/agent/chat")
    suspend fun agentChat(@Body payload: AgentChatRequest): AgentStepResponse

    @GET("api/v1/portals/verify")
    suspend fun verifyPortal(@Query("url") url: String): PortalVerifyResponse

    @GET("api/v1/tasks/{task_id}")
    suspend fun getTask(@Path("task_id") taskId: String): TaskRecordResponse

    @POST("api/v1/tasks/{task_id}/approval")
    suspend fun approveTask(
        @Path("task_id") taskId: String,
        @Body payload: TaskApprovalRequest
    ): TaskApprovalResponse

    @Multipart
    @POST("documents/extract-and-validate")
    suspend fun extractAndValidateDocument(
        @Part file: MultipartBody.Part,
        @Part("expected_doc_type") expectedDocType: RequestBody,
        @Part("profile_id") profileId: RequestBody,
        @Part("profile_full_name") profileFullName: RequestBody?,
        @Part("language") language: RequestBody
    ): DocumentExtractionResult
}
