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
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
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
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowForward
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material.icons.filled.AccountBalance
import androidx.compose.material.icons.filled.AutoAwesome
import androidx.compose.material.icons.filled.CallMade
import androidx.compose.material.icons.filled.Description
import androidx.compose.material.icons.filled.Fingerprint
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material.icons.filled.Public
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Shield
import androidx.compose.material.icons.filled.Verified
import androidx.compose.material.icons.filled.VerifiedUser
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
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
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import com.codeverse.assistant.AssistantApplication
import com.codeverse.assistant.accessibility.PortalAccessibilityService
import com.codeverse.assistant.service.FloatingBubbleService
import com.codeverse.assistant.state.AssistantCoordinator
import kotlinx.coroutines.launch

private object OmniPalette {
    val Background = Color(0xFFF8FAFC)
    val Border = Color(0xFFE6EBF1)
    val PrimaryBlue = Color(0xFF1D4ED8)
    val PrimaryBlueSoft = Color(0xFFEDF3FF)
    val InkPrimary = Color(0xFF1D2C42)
    val InkSecondary = Color(0xFF526178)
    val InkMuted = Color(0xFF8491A3)
    val VerifiedGreen = Color(0xFF16845C)
    val VerifiedGreenBg = Color(0xFFF5FBF7)
    val VerifiedGreenBorder = Color(0xFFD9EEE3)
    val VerifiedGreenChip = Color(0xFFE6F5EC)
    val AmberText = Color(0xFFA26D14)
    val AmberBg = Color(0xFFFFF7E8)
}

/**
 * Voice & Intent Router Activity aligned with the Universal AI (omni-assist) design:
 * - Bilingual EN / हिं header + SpeechRecognizer (en-IN / hi-IN)
 * - Voice orb + waveform transcript card + Popular Tasks chips
 * - POST /api/v1/agent/chat + GET /api/v1/portals/verify Official Domain Shield check
 * - Launches verified portal in default browser (ACTION_VIEW), starts FloatingBubbleService,
 *   and calls moveTaskToBack(true).
 */
class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val coordinator = (application as AssistantApplication).coordinator

        setContent {
            MaterialTheme {
                OmniVoiceIntakeScreen(
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

private data class QuickPromptItem(
    val title: String,
    val transcriptEn: String,
    val transcriptHi: String,
    val icon: ImageVector,
    val iconTint: Color,
    val iconBg: Color
)

@Composable
private fun OmniVoiceIntakeScreen(
    coordinator: AssistantCoordinator,
    onLaunchPortalAndFloat: (String) -> Unit
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val state by coordinator.uiState.collectAsState()
    val a11yConnected by PortalAccessibilityService.isConnected.collectAsState()

    val isHindi = state.language == "hi"
    val speechLocale = if (isHindi) "hi-IN" else "en-IN"

    var queryInput by remember { mutableStateOf("") }
    var selectedChip by remember { mutableStateOf("Apply for Post-Matric Scholarship") }
    var serverUrlInput by remember(state.baseUrl) { mutableStateOf(state.baseUrl) }
    var showSettingsPanel by remember { mutableStateOf(false) }
    var isListening by remember { mutableStateOf(false) }
    var speechError by remember { mutableStateOf<String?>(null) }

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
            speechError = "Microphone permission is required for voice intake."
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
                speechError = null
            }

            override fun onBeginningOfSpeech() {}
            override fun onRmsChanged(rmsdB: Float) {}
            override fun onBufferReceived(buffer: ByteArray?) {}
            override fun onEndOfSpeech() {
                isListening = false
            }

            override fun onError(error: Int) {
                isListening = false
                speechError = "Speech input unavailable (code $error) — type below instead."
            }

            override fun onResults(results: Bundle?) {
                isListening = false
                val transcript = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                    ?.firstOrNull()
                    .orEmpty()
                if (transcript.isNotBlank()) {
                    queryInput = transcript
                }
            }

            override fun onPartialResults(partialResults: Bundle?) {
                val partial = partialResults?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
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

    fun startOrStopVoice() {
        if (isListening) {
            speechRecognizer?.stopListening()
            isListening = false
            return
        }
        if (!hasAudioPermission) {
            audioPermissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
            return
        }
        val recognizer = speechRecognizer
        if (recognizer == null) {
            speechError = "SpeechRecognizer service unavailable on this device."
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

    val quickPrompts = remember {
        listOf(
            QuickPromptItem(
                title = "Apply for Post-Matric Scholarship",
                transcriptEn = "I need help applying for a post-matric scholarship.",
                transcriptHi = "मुझे पोस्ट-मैट्रिक छात्रवृत्ति के लिए आवेदन करना है।",
                icon = Icons.Default.Description,
                iconTint = Color(0xFF2C5EC7),
                iconBg = Color(0xFFEEF4FF)
            ),
            QuickPromptItem(
                title = "Check PAN–Aadhaar Link",
                transcriptEn = "I want to check if my PAN is linked to Aadhaar.",
                transcriptHi = "मुझे जांचना है कि मेरा पैन आधार से लिंक है या नहीं।",
                icon = Icons.Default.Fingerprint,
                iconTint = OmniPalette.AmberText,
                iconBg = OmniPalette.AmberBg
            ),
            QuickPromptItem(
                title = "Find IFSC on Passbook",
                transcriptEn = "Help me find the IFSC code on my bank passbook.",
                transcriptHi = "मेरी बैंक पासबुक पर IFSC कोड खोजने में मदद करें।",
                icon = Icons.Default.AccountBalance,
                iconTint = OmniPalette.VerifiedGreen,
                iconBg = Color(0xFFEDF8F2)
            )
        )
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(OmniPalette.Background)
    ) {
        // Native App Bar
        Surface(color = Color.White, shadowElevation = 0.dp) {
            Column {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(58.dp)
                        .padding(horizontal = 16.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.SpaceBetween
                ) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Box(
                            modifier = Modifier
                                .size(36.dp)
                                .clip(RoundedCornerShape(10.dp))
                                .background(OmniPalette.PrimaryBlueSoft),
                            contentAlignment = Alignment.Center
                        ) {
                            Icon(
                                imageVector = Icons.Default.Shield,
                                contentDescription = null,
                                tint = OmniPalette.PrimaryBlue,
                                modifier = Modifier.size(20.dp)
                            )
                            Icon(
                                imageVector = Icons.Default.AutoAwesome,
                                contentDescription = null,
                                tint = Color.White,
                                modifier = Modifier.size(9.dp)
                            )
                        }
                        Spacer(Modifier.width(10.dp))
                        Column {
                            Text(
                                text = "Universal AI",
                                fontSize = 14.sp,
                                fontWeight = FontWeight.Bold,
                                color = Color(0xFF23334A)
                            )
                            Text(
                                text = "Web Assistant",
                                fontSize = 11.sp,
                                color = Color(0xFF8793A4)
                            )
                        }
                    }

                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Row(
                            modifier = Modifier
                                .clip(RoundedCornerShape(8.dp))
                                .background(Color(0xFFF2F4F7))
                                .padding(3.dp),
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            LanguageChipButton(
                                label = "EN",
                                selected = !isHindi,
                                onClick = { coordinator.setLanguage("en") }
                            )
                            Box(
                                modifier = Modifier
                                    .width(1.dp)
                                    .height(12.dp)
                                    .background(Color(0xFFD8DEE6))
                            )
                            LanguageChipButton(
                                label = "हिं",
                                selected = isHindi,
                                onClick = { coordinator.setLanguage("hi") }
                            )
                        }
                        Spacer(Modifier.width(6.dp))
                        IconButton(onClick = { showSettingsPanel = !showSettingsPanel }) {
                            Icon(
                                imageVector = Icons.Default.Settings,
                                contentDescription = "Settings & Permissions",
                                tint = if (!hasOverlayPermission || !a11yConnected) Color(0xFFD97706) else Color(0xFF77859A)
                            )
                        }
                    }
                }
                HorizontalDivider(color = Color(0xFFEDF0F4))
            }
        }

        Column(
            modifier = Modifier
                .weight(1f)
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 20.dp, vertical = 18.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            if (showSettingsPanel || !hasOverlayPermission || !a11yConnected) {
                Surface(
                    color = Color.White,
                    shape = RoundedCornerShape(12.dp),
                    border = BorderStroke(1.dp, OmniPalette.Border),
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Column(
                        modifier = Modifier.padding(12.dp),
                        verticalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        Text(
                            text = "DEVICE SETUP & BACKEND",
                            fontSize = 10.sp,
                            fontWeight = FontWeight.Bold,
                            letterSpacing = 0.8.sp,
                            color = OmniPalette.InkMuted
                        )
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.spacedBy(8.dp)
                        ) {
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
                                Text(
                                    if (hasOverlayPermission) "Overlay Ready" else "Enable Overlay",
                                    fontSize = 12.sp,
                                    color = if (hasOverlayPermission) OmniPalette.VerifiedGreen else OmniPalette.PrimaryBlue
                                )
                            }
                            OutlinedButton(
                                onClick = {
                                    context.startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
                                },
                                modifier = Modifier.weight(1f)
                            ) {
                                Text(
                                    if (a11yConnected) "Reader Active" else "Enable Reader",
                                    fontSize = 12.sp,
                                    color = if (a11yConnected) OmniPalette.VerifiedGreen else OmniPalette.PrimaryBlue
                                )
                            }
                        }
                        if (showSettingsPanel) {
                            OutlinedTextField(
                                value = serverUrlInput,
                                onValueChange = {
                                    serverUrlInput = it
                                    coordinator.updateBaseUrl(it)
                                },
                                label = { Text("FastAPI Backend URL") },
                                singleLine = true,
                                modifier = Modifier.fillMaxWidth()
                            )
                        }
                    }
                }
            }

            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(
                    modifier = Modifier
                        .size(20.dp)
                        .clip(RoundedCornerShape(6.dp))
                        .background(OmniPalette.PrimaryBlueSoft),
                    contentAlignment = Alignment.Center
                ) {
                    Icon(
                        imageVector = Icons.Default.AutoAwesome,
                        contentDescription = null,
                        tint = OmniPalette.PrimaryBlue,
                        modifier = Modifier.size(12.dp)
                    )
                }
                Spacer(Modifier.width(7.dp))
                Text(
                    text = "YOUR PERSONAL PORTAL GUIDE",
                    fontSize = 10.sp,
                    fontWeight = FontWeight.Bold,
                    letterSpacing = 1.sp,
                    color = Color(0xFF728198)
                )
            }

            Text(
                text = buildAnnotatedString {
                    if (isHindi) {
                        append("सरकारी काम,\n")
                        withStyle(SpanStyle(color = OmniPalette.PrimaryBlue)) {
                            append("आसानी से।")
                        }
                    } else {
                        append("Government forms,\n")
                        withStyle(SpanStyle(color = OmniPalette.PrimaryBlue)) {
                            append("made human.")
                        }
                    }
                },
                fontSize = 30.sp,
                lineHeight = 35.sp,
                fontWeight = FontWeight.ExtraBold,
                color = OmniPalette.InkPrimary
            )

            Text(
                text = if (isHindi) {
                    "बताइए आपको क्या करना है। आपकी जानकारी हमेशा आपके डिवाइस पर रहती है।"
                } else {
                    "Tell me what you need to do. Your private details stay right here on your device."
                },
                fontSize = 13.sp,
                lineHeight = 19.sp,
                color = Color(0xFF7E8B9E)
            )

            MicOrbStage(
                isListening = isListening,
                isHindi = isHindi,
                onClick = { startOrStopVoice() }
            )

            speechError?.let {
                Text(it, fontSize = 11.sp, color = Color(0xFFB45309))
            }

            Surface(
                color = Color.White,
                shape = RoundedCornerShape(11.dp),
                border = BorderStroke(1.dp, OmniPalette.Border),
                modifier = Modifier.fillMaxWidth()
            ) {
                Column(modifier = Modifier.padding(12.dp)) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        VoiceWaveIndicator(active = queryInput.isNotBlank() || isListening)
                        Spacer(Modifier.width(8.dp))
                        Text(
                            text = if (queryInput.isNotBlank()) "VOICE TRANSCRIPT" else "YOUR WORDS, YOUR WAY",
                            fontSize = 10.sp,
                            fontWeight = FontWeight.Bold,
                            letterSpacing = 0.8.sp,
                            color = OmniPalette.InkMuted,
                            modifier = Modifier.weight(1f)
                        )
                        Box(
                            modifier = Modifier
                                .size(24.dp)
                                .clip(RoundedCornerShape(6.dp))
                                .background(Color(0xFFF5F7FA))
                                .clickable { queryInput = "" },
                            contentAlignment = Alignment.Center
                        ) {
                            Icon(
                                imageVector = Icons.Default.CallMade,
                                contentDescription = "Clear transcript",
                                tint = OmniPalette.InkMuted,
                                modifier = Modifier.size(13.dp)
                            )
                        }
                    }
                    Spacer(Modifier.height(6.dp))
                    Box(modifier = Modifier.fillMaxWidth()) {
                        if (queryInput.isEmpty()) {
                            Text(
                                text = if (isHindi) "या यहाँ अपनी ज़रूरत लिखें…" else "Or type what you need help with…",
                                fontSize = 13.sp,
                                color = Color(0xFFA5AFBC)
                            )
                        }
                        BasicTextField(
                            value = queryInput,
                            onValueChange = { queryInput = it },
                            textStyle = TextStyle(
                                color = Color(0xFF45546B),
                                fontSize = 13.sp,
                                lineHeight = 19.sp
                            ),
                            minLines = 2,
                            maxLines = 4,
                            modifier = Modifier.fillMaxWidth()
                        )
                    }
                }
            }

            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    text = "POPULAR TASKS",
                    fontSize = 10.sp,
                    fontWeight = FontWeight.Bold,
                    letterSpacing = 0.8.sp,
                    color = Color(0xFF6F7E93)
                )
                Spacer(Modifier.width(8.dp))
                Text(
                    text = "CHOOSE ONE TO GET STARTED",
                    fontSize = 9.sp,
                    fontWeight = FontWeight.SemiBold,
                    letterSpacing = 0.5.sp,
                    color = Color(0xFFA3ADBA)
                )
            }

            Column(verticalArrangement = Arrangement.spacedBy(7.dp)) {
                quickPrompts.forEach { item ->
                    val isSelected = selectedChip == item.title
                    Surface(
                        color = if (isSelected) Color(0xFFF9FBFF) else Color.White,
                        shape = RoundedCornerShape(9.dp),
                        border = BorderStroke(
                            1.dp,
                            if (isSelected) Color(0xFFD8E5FC) else Color(0xFFE8EDF2)
                        ),
                        modifier = Modifier
                            .fillMaxWidth()
                            .clickable {
                                selectedChip = item.title
                                queryInput = if (isHindi) item.transcriptHi else item.transcriptEn
                            }
                    ) {
                        Row(
                            modifier = Modifier.padding(horizontal = 10.dp, vertical = 8.dp),
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Box(
                                modifier = Modifier
                                    .size(26.dp)
                                    .clip(RoundedCornerShape(7.dp))
                                    .background(item.iconBg),
                                contentAlignment = Alignment.Center
                            ) {
                                Icon(
                                    imageVector = item.icon,
                                    contentDescription = null,
                                    tint = item.iconTint,
                                    modifier = Modifier.size(15.dp)
                                )
                            }
                            Spacer(Modifier.width(10.dp))
                            Text(
                                text = item.title,
                                fontSize = 12.sp,
                                fontWeight = FontWeight.Medium,
                                color = OmniPalette.InkSecondary,
                                modifier = Modifier.weight(1f)
                            )
                            Icon(
                                imageVector = Icons.AutoMirrored.Filled.KeyboardArrowRight,
                                contentDescription = null,
                                tint = Color(0xFF9AA6B5),
                                modifier = Modifier.size(16.dp)
                            )
                        }
                    }
                }
            }

            state.errorMessage?.let { err ->
                Surface(
                    color = Color(0xFFFEE2E2),
                    shape = RoundedCornerShape(10.dp),
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Text(
                        text = err,
                        color = Color(0xFF991B1B),
                        fontSize = 12.sp,
                        modifier = Modifier.padding(10.dp)
                    )
                }
            }

            val verification = state.portalVerification
            val detectedDomain = verification?.canonicalDomain
                ?: state.resolvedPortalUrl?.let { Uri.parse(it).host }
                ?: "scholarships.gov.in"
            val isVerified = verification?.isOfficial ?: true

            Surface(
                color = if (isVerified) OmniPalette.VerifiedGreenBg else Color(0xFFFEF2F2),
                shape = RoundedCornerShape(10.dp),
                border = BorderStroke(
                    1.dp,
                    if (isVerified) OmniPalette.VerifiedGreenBorder else Color(0xFFFECACA)
                ),
                modifier = Modifier.fillMaxWidth()
            ) {
                Row(
                    modifier = Modifier.padding(horizontal = 10.dp, vertical = 9.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Box(
                        modifier = Modifier
                            .size(30.dp)
                            .clip(RoundedCornerShape(8.dp))
                            .background(if (isVerified) OmniPalette.VerifiedGreenChip else Color(0xFFFEE2E2)),
                        contentAlignment = Alignment.Center
                    ) {
                        Icon(
                            imageVector = Icons.Default.Verified,
                            contentDescription = null,
                            tint = if (isVerified) OmniPalette.VerifiedGreen else Color(0xFFDC2626),
                            modifier = Modifier.size(17.dp)
                        )
                    }
                    Spacer(Modifier.width(10.dp))
                    Column(modifier = Modifier.weight(1f)) {
                        Text(
                            text = if (verification != null) "PORTAL DETECTED" else "PORTAL SHIELD READY",
                            fontSize = 9.sp,
                            fontWeight = FontWeight.Bold,
                            letterSpacing = 0.7.sp,
                            color = Color(0xFF7F998C)
                        )
                        Text(
                            text = detectedDomain,
                            fontSize = 13.sp,
                            fontWeight = FontWeight.Bold,
                            color = if (isVerified) Color(0xFF236447) else Color(0xFF991B1B),
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis
                        )
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Box(
                                modifier = Modifier
                                    .size(5.dp)
                                    .clip(CircleShape)
                                    .background(if (isVerified) Color(0xFF159669) else Color(0xFFDC2626))
                            )
                            Spacer(Modifier.width(5.dp))
                            Text(
                                text = verification?.reason ?: "Official government domain shield active",
                                fontSize = 10.sp,
                                color = Color(0xFF7C9A8B),
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis
                            )
                        }
                    }
                    Icon(
                        imageVector = Icons.Default.VerifiedUser,
                        contentDescription = null,
                        tint = if (isVerified) OmniPalette.VerifiedGreen else Color(0xFFDC2626),
                        modifier = Modifier.size(16.dp)
                    )
                }
            }

            if (state.agentMessage.isNotBlank()) {
                Surface(
                    color = Color.White,
                    shape = RoundedCornerShape(10.dp),
                    border = BorderStroke(1.dp, OmniPalette.Border),
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Text(
                        text = state.agentMessage,
                        fontSize = 12.sp,
                        color = OmniPalette.InkSecondary,
                        modifier = Modifier.padding(10.dp)
                    )
                }
            }

            Button(
                onClick = {
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
                        ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) !=
                        PackageManager.PERMISSION_GRANTED
                    ) {
                        notificationPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
                    }

                    val prompt = queryInput.trim().ifEmpty {
                        if (isHindi) quickPrompts.first().transcriptHi else quickPrompts.first().transcriptEn
                    }
                    scope.launch {
                        val url = coordinator.resolveAndVerifyPortal(prompt).getOrNull()
                        if (!url.isNullOrBlank()) {
                            onLaunchPortalAndFloat(url)
                        }
                    }
                },
                enabled = !state.isBusy,
                shape = RoundedCornerShape(9.dp),
                colors = ButtonDefaults.buttonColors(
                    containerColor = OmniPalette.PrimaryBlue,
                    contentColor = Color.White
                ),
                modifier = Modifier
                    .fillMaxWidth()
                    .height(46.dp)
            ) {
                if (state.isBusy) {
                    CircularProgressIndicator(
                        modifier = Modifier.size(18.dp),
                        color = Color.White,
                        strokeWidth = 2.dp
                    )
                    Spacer(Modifier.width(10.dp))
                    Text("Verifying official portal…", fontWeight = FontWeight.Bold, fontSize = 13.sp)
                } else {
                    Icon(Icons.Default.Public, contentDescription = null, modifier = Modifier.size(18.dp))
                    Spacer(Modifier.width(8.dp))
                    Text(
                        text = "Open in Default Browser & Minimize",
                        fontWeight = FontWeight.Bold,
                        fontSize = 13.sp,
                        modifier = Modifier.weight(1f)
                    )
                    Icon(Icons.AutoMirrored.Filled.ArrowForward, contentDescription = null, modifier = Modifier.size(17.dp))
                }
            }

            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(top = 2.dp, bottom = 8.dp),
                horizontalArrangement = Arrangement.Center,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Icon(
                    imageVector = Icons.Default.Lock,
                    contentDescription = null,
                    tint = OmniPalette.VerifiedGreen,
                    modifier = Modifier.size(12.dp)
                )
                Spacer(Modifier.width(6.dp))
                Text(
                    text = "No account needed  •  Private by design  •  You stay in control",
                    fontSize = 10.sp,
                    color = Color(0xFF97A2B1)
                )
            }
        }
    }
}

@Composable
private fun LanguageChipButton(
    label: String,
    selected: Boolean,
    onClick: () -> Unit
) {
    Surface(
        color = if (selected) Color.White else Color.Transparent,
        shape = RoundedCornerShape(6.dp),
        shadowElevation = if (selected) 1.dp else 0.dp,
        modifier = Modifier.clickable { onClick() }
    ) {
        Text(
            text = label,
            fontSize = 11.sp,
            fontWeight = if (selected) FontWeight.Bold else FontWeight.Normal,
            color = if (selected) Color(0xFF35455D) else Color(0xFF929DAD),
            modifier = Modifier.padding(horizontal = 9.dp, vertical = 4.dp)
        )
    }
}

@Composable
private fun MicOrbStage(
    isListening: Boolean,
    isHindi: Boolean,
    onClick: () -> Unit
) {
    val transition = rememberInfiniteTransition(label = "micPulse")
    val pulseScale by transition.animateFloat(
        initialValue = 0.94f,
        targetValue = 1.12f,
        animationSpec = infiniteRepeatable(
            animation = tween(900),
            repeatMode = RepeatMode.Reverse
        ),
        label = "pulse"
    )

    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 4.dp),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Box(
            modifier = Modifier.size(144.dp),
            contentAlignment = Alignment.Center
        ) {
            Box(
                modifier = Modifier
                    .size(136.dp)
                    .scale(if (isListening) pulseScale else 1f)
                    .clip(CircleShape)
                    .border(1.dp, Color(0xFFECF1F8), CircleShape)
            )
            Box(
                modifier = Modifier
                    .size(106.dp)
                    .scale(if (isListening) pulseScale else 1f)
                    .clip(CircleShape)
                    .border(1.dp, Color(0xFFDFE9FA), CircleShape)
            )
            Box(
                modifier = Modifier
                    .size(86.dp)
                    .clip(CircleShape)
                    .background(Color(0xFFEFF4FF)),
                contentAlignment = Alignment.Center
            ) {
                Box(
                    modifier = Modifier
                        .size(74.dp)
                        .shadow(10.dp, CircleShape)
                        .clip(CircleShape)
                        .background(
                            Brush.linearGradient(
                                colors = listOf(Color(0xFF2C63DC), Color(0xFF1846B1))
                            )
                        )
                        .clickable { onClick() },
                    contentAlignment = Alignment.Center
                ) {
                    Box(
                        modifier = Modifier
                            .size(50.dp)
                            .clip(CircleShape)
                            .border(1.dp, Color.White.copy(alpha = 0.35f), CircleShape),
                        contentAlignment = Alignment.Center
                    ) {
                        Icon(
                            imageVector = if (isListening) Icons.Default.Pause else Icons.Default.Mic,
                            contentDescription = if (isListening) "Stop listening" else "Tap to speak",
                            tint = Color.White,
                            modifier = Modifier.size(26.dp)
                        )
                    }
                }
            }
        }
        Text(
            text = if (isListening) "LISTENING…" else "TAP TO SPEAK",
            fontSize = 10.sp,
            fontWeight = FontWeight.ExtraBold,
            letterSpacing = 1.1.sp,
            color = Color(0xFF5272B1)
        )
        Text(
            text = if (isHindi) "या बोलने के लिए टैप करें" else "or tap to tell me what you need",
            fontSize = 11.sp,
            color = Color(0xFF96A1AF)
        )
    }
}

@Composable
private fun VoiceWaveIndicator(active: Boolean) {
    val heights = listOf(5.dp, 9.dp, 12.dp, 7.dp, 12.dp, 9.dp, 5.dp)
    val barColor = if (active) Color(0xFF2B62CF) else Color(0xFF8AA1CC)
    Row(
        horizontalArrangement = Arrangement.spacedBy(2.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        heights.forEach { h ->
            Box(
                modifier = Modifier
                    .width(2.5.dp)
                    .height(h)
                    .clip(RoundedCornerShape(2.dp))
                    .background(barColor)
            )
        }
    }
}
