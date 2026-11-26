package com.codeverse.assistant

import android.app.Application
import com.codeverse.assistant.network.ApiClientProvider
import com.codeverse.assistant.security.AuthorizationGate
import com.codeverse.assistant.state.AssistantCoordinator
import com.codeverse.assistant.vault.EncryptedVaultStore

class AssistantApplication : Application() {

    lateinit var apiProvider: ApiClientProvider
        private set

    lateinit var vaultStore: EncryptedVaultStore
        private set

    lateinit var authGate: AuthorizationGate
        private set

    lateinit var coordinator: AssistantCoordinator
        private set

    override fun onCreate() {
        super.onCreate()
        apiProvider = ApiClientProvider()
        vaultStore = EncryptedVaultStore(this)
        authGate = AuthorizationGate()
        coordinator = AssistantCoordinator(
            appContext = this,
            apiProvider = apiProvider,
            vaultStore = vaultStore,
            authGate = authGate
        )
    }
}
