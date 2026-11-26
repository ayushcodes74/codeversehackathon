package com.codeverse.assistant.ui

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AccessibilityNew
import androidx.compose.material.icons.filled.Layers
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.OpenInBrowser
import androidx.compose.material.icons.filled.Send
import androidx.compose.material.icons.filled.Shield
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import com.codeverse.assistant.AssistantApplication
import com.codeverse.assistant.accessibility.PortalAccessibilityService
import com.codeverse.assistant.service.FloatingBubbleService
import com.codeverse.assistant.state.AssistantCoordinator
import kotlinx.coroutines.launch

/**
 * Voice & Intent Router Activity:
 * - SpeechRecognizer (en-IN / hi-IN) + text problem intake
 * - Calls POST /api/v1/agent/chat to resolve portal URL
 * - Verifies portal via GET /api/v1/portals/verify (Official Domain Shield)
 * - Launches verified URL in default browser (Intent.ACTION_VIEW)
 * - Starts FloatingBubbleService and calls moveTaskToBack(true)
 */
class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val coordinator = (application as AssistantApplication).coordinator

        setContent {
            MaterialTheme {
                VoiceIntentRouterScreen(
                    coordinator = coordinator,
                    onLaunchPortalAndFloat = { portalUrl ->
                        launchVerifiedPortalAndFloat(portalUrl)
                    }
                )
            }
        }
    }

    private fun launchVerifiedPortalAndFloat(portalUrl: String) {
        if (!Settings.canDrawOverlays(this)) {
            startActivity(
                Intent(
                    Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                    Uri.parse("package:$packageName")
                )
            )
            return
        }

        val bubbleIntent = Intent(this, FloatingBubbleService::class.java)
        ContextCompat.startForegroundService(this, bubbleIntent)

        val browserIntent = Intent(Intent.ACTION_VIEW, Uri.parse(portalUrl)).apply {
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        startActivity(browserIntent)
        moveTaskToBack(true)
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun VoiceIntentRouterScreen(
    coordinator: AssistantCoordinator,
    onLaunchPortalAndFloat: (String) -> Unit
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val state by coordinator.uiState.collectAsState()
    val a11yConnected by PortalAccessibilityService.isConnected.collectAsState()

    var queryInput by remember { mutableStateOf("") }
    var serverUrlInput by remember(state.baseUrl) { mutableStateOf(state.baseUrl) }
    var speechLocale by remember { mutableStateOf(if (state.language == "hi") "hi-IN" else "en-IN") }
    var isListening by remember { mutableStateOf(false) }
    var speechStatus by remember { mutableStateOf<String?>(null) }

    val hasOverlayPermission = Settings.canDrawOverlays(context)
    var hasAudioPermission by remember {
        mutableStateOf(
            ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) ==
                PackageManager.PERMISSION_GRANTED
        )
    }

    val audioPermissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        hasAudioPermission = granted
        if (!granted) {
            speechStatus = "RECORD_AUDIO permission denied."
        }
    }

    val notificationPermissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { }

    val speechRecognizer = remember(context) {
        if (SpeechRecognizer.isRecognitionAvailable(context)) {
            SpeechRecognizer.createSpeechRecognizer(context)
        } else {
            null
        }
    }

    DisposableEffect(speechRecognizer) {
        speechRecognizer?.setRecognitionListener(object : RecognitionListener {
            override fun onReadyForSpeech(params: Bundle?) {
                isListening = true
                speechStatus = "Listening ($speechLocale)..."
            }

            override fun onBeginningOfSpeech() {}
            override fun onRmsChanged(rmsdB: Float) {}
            override fun onBufferReceived(buffer: ByteArray?) {}
            override fun onEndOfSpeech() {
                isListening = false
            }

            override fun onError(error: Int) {
                isListening = false
                speechStatus = "Speech error code $error. You can type your request below."
            }

            override fun onResults(results: Bundle?) {
                isListening = false
                val matches = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                val transcript = matches?.firstOrNull().orEmpty()
                if (transcript.isNotBlank()) {
                    queryInput = transcript
                    speechStatus = "Heard: \"$transcript\""
                }
            }

            override fun onPartialResults(partialResults: Bundle?) {
                val partial = partialResults
                    ?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                    ?.firstOrNull()
                if (!partial.isNullOrBlank()) {
                    queryInput = partial
                }
            }

            override fun onEvent(eventType: Int, params: Bundle?) {}
        })

        onDispose {
            speechRecognizer?.destroy()
        }
    }

    fun startVoiceRecognition() {
        if (!hasAudioPermission) {
            audioPermissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
            return
        }
        val recognizer = speechRecognizer
        if (recognizer == null) {
            speechStatus = "SpeechRecognizer unavailable on this device."
            return
        }
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, speechLocale)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_PREFERENCE, speechLocale)
            putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
        }
        recognizer.startListening(intent)
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Text(
                        "JanSahayak AI • Voice & Intent Router",
                        fontWeight = FontWeight.Bold
                    )
                }
            )
        }
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(16.dp)
                .verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            // 1. Setup & Permissions Card
            Card(
                colors = CardDefaults.cardColors(containerColor = Color(0xFFF8FAFC)),
                shape = RoundedCornerShape(14.dp)
            ) {
                Column(modifier = Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("Permissions & Accessibility", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold)
                    Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        OutlinedButton(
                            onClick = {
                                context.startActivity(
                                    Intent(
                                        Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                                        Uri.parse("package:${context.packageName}")
                                    )
                                )
                            },
                            modifier = Modifier.weight(1f)
                        ) {
                            Icon(Icons.Default.Layers, contentDescription = null, modifier = Modifier.size(16.dp))
                            Spacer(Modifier.width(6.dp))
                            Text(if (hasOverlayPermission) "Overlay OK" else "Grant Overlay")
                        }
                        OutlinedButton(
                            onClick = {
                                context.startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
                            },
                            modifier = Modifier.weight(1f)
                        ) {
                            Icon(Icons.Default.AccessibilityNew, contentDescription = null, modifier = Modifier.size(16.dp))
                            Spacer(Modifier.width(6.dp))
                            Text(if (a11yConnected) "Reader OK" else "Enable Reader")
                        }
                    }

                    OutlinedTextField(
                        value = serverUrlInput,
                        onValueChange = {
                            serverUrlInput = it
                            coordinator.updateBaseUrl(it)
                        },
                        label = { Text("Backend API Base URL") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth()
                    )
                }
            }

            // 2. Bilingual Voice + Text Intake Card
            Card(shape = RoundedCornerShape(14.dp)) {
                Column(modifier = Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text(
                        "Ask by Voice or Text (en-IN / hi-IN)",
                        style = MaterialTheme.typography.titleSmall,
                        fontWeight = FontWeight.Bold
                    )

                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        FilterChip(
                            selected = speechLocale == "en-IN",
                            onClick = {
                                speechLocale = "en-IN"
                                coordinator.setLanguage("en")
                            },
                            label = { Text("English (en-IN)") }
                        )
                        FilterChip(
                            selected = speechLocale == "hi-IN",
                            onClick = {
                                speechLocale = "hi-IN"
                                coordinator.setLanguage("hi")
                            },
                            label = { Text("हिन्दी (hi-IN)") }
                        )
                    }

                    OutlinedTextField(
                        value = queryInput,
                        onValueChange = { queryInput = it },
                        label = { Text("e.g., Apply for Rajasthan post-matric scholarship") },
                        minLines = 2,
                        maxLines = 4,
                        trailingIcon = {
                            IconButton(
                                onClick = {
                                    if (isListening) {
                                        speechRecognizer?.stopListening()
                                        isListening = false
                                    } else {
                                        startVoiceRecognition()
                                    }
                                }
                            ) {
                                Icon(
                                    imageVector = if (isListening) Icons.Default.Stop else Icons.Default.Mic,
                                    contentDescription = "Voice Input",
                                    tint = if (isListening) Color(0xFFDC2626) else Color(0xFF0F4C81)
                                )
                            }
                        },
                        modifier = Modifier.fillMaxWidth()
                    )

                    speechStatus?.let {
                        Text(it, style = MaterialTheme.typography.labelSmall, color = Color(0xFF475569))
                    }

                    Button(
                        onClick = {
                            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
                                ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) !=
                                PackageManager.PERMISSION_GRANTED
                            ) {
                                notificationPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
                            }

                            val prompt = queryInput.trim()
                            if (prompt.isNotEmpty()) {
                                scope.launch {
                                    val result = coordinator.resolveAndVerifyPortal(prompt)
                                    val portalUrl = result.getOrNull()
                                    if (!portalUrl.isNullOrBlank()) {
                                        onLaunchPortalAndFloat(portalUrl)
                                    }
                                }
                            }
                        },
                        enabled = !state.isBusy && queryInput.isNotBlank(),
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        if (state.isBusy) {
                            CircularProgressIndicator(
                                modifier = Modifier.size(18.dp),
                                color = Color.White,
                                strokeWidth = 2.dp
                            )
                            Spacer(Modifier.width(8.dp))
                        } else {
                            Icon(Icons.Default.Send, contentDescription = null, modifier = Modifier.size(18.dp))
                            Spacer(Modifier.width(8.dp))
                        }
                        Text("Resolve Portal, Verify & Open Browser")
                    }
                }
            }

            // 3. Domain Shield & Agent Response Card
            state.errorMessage?.let { err ->
                Surface(color = Color(0xFFFEE2E2), shape = RoundedCornerShape(10.dp), modifier = Modifier.fillMaxWidth()) {
                    Text(err, color = Color(0xFF991B1B), modifier = Modifier.padding(12.dp))
                }
            }

            state.portalVerification?.let { shield ->
                Surface(
                    color = if (shield.isOfficial) Color(0xFFDCFCE7) else Color(0xFFFEE2E2),
                    shape = RoundedCornerShape(12.dp),
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Row(
                        modifier = Modifier.padding(12.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Icon(
                            imageVector = Icons.Default.Shield,
                            contentDescription = null,
                            tint = if (shield.isOfficial) Color(0xFF15803D) else Color(0xFFDC2626)
                        )
                        Spacer(Modifier.width(10.dp))
                        Column {
                            Text(
                                text = "Official Domain Shield: ${shield.supportTier}",
                                fontWeight = FontWeight.Bold,
                                style = MaterialTheme.typography.labelMedium
                            )
                            Text(shield.reason, style = MaterialTheme.typography.bodySmall)
                        }
                    }
                }
            }

            if (state.agentMessage.isNotBlank()) {
                Card(shape = RoundedCornerShape(14.dp), modifier = Modifier.fillMaxWidth()) {
                    Column(modifier = Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text("Agent Guidance", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold)
                        Text(state.agentMessage, style = MaterialTheme.typography.bodyMedium)

                        state.resolvedPortalUrl?.let { url ->
                            OutlinedButton(
                                onClick = { onLaunchPortalAndFloat(url) },
                                modifier = Modifier.fillMaxWidth()
                            ) {
                                Icon(Icons.Default.OpenInBrowser, contentDescription = null, modifier = Modifier.size(18.dp))
                                Spacer(Modifier.width(8.dp))
                                Text("Re-open Verified Portal & Float Bubble")
                            }
                        }
                    }
                }
            }

            // Quick launch bubble standalone for testing against an already open Chrome tab
            OutlinedButton(
                onClick = {
                    if (!Settings.canDrawOverlays(context)) {
                        context.startActivity(
                            Intent(
                                Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                                Uri.parse("package:${context.packageName}")
                            )
                        )
                    } else {
                        ContextCompat.startForegroundService(
                            context,
                            Intent(context, FloatingBubbleService::class.java)
                        )
                        (context as? ComponentActivity)?.moveTaskToBack(true)
                    }
                },
                modifier = Modifier.fillMaxWidth()
            ) {
                Icon(Icons.Default.Layers, contentDescription = null, modifier = Modifier.size(18.dp))
                Spacer(Modifier.width(8.dp))
                Text("Start Floating Bubble Only")
            }

            Spacer(Modifier.height(12.dp))
        }
    }
}
