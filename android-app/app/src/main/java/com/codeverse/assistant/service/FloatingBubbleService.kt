package com.codeverse.assistant.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.graphics.PixelFormat
import android.os.Build
import android.os.IBinder
import android.view.Gravity
import android.view.WindowManager
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Assistant
import androidx.compose.material.icons.filled.CameraAlt
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.DocumentScanner
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Screenshot
import androidx.compose.material.icons.filled.UploadFile
import androidx.compose.material.icons.filled.VerifiedUser
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.ComposeView
import androidx.compose.ui.platform.ViewCompositionStrategy
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.core.app.NotificationCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.LifecycleRegistry
import androidx.lifecycle.ViewModelStore
import androidx.lifecycle.ViewModelStoreOwner
import androidx.lifecycle.setViewTreeLifecycleOwner
import androidx.lifecycle.setViewTreeViewModelStoreOwner
import androidx.savedstate.SavedStateRegistry
import androidx.savedstate.SavedStateRegistryController
import androidx.savedstate.SavedStateRegistryOwner
import androidx.savedstate.setViewTreeSavedStateRegistryOwner
import com.codeverse.assistant.AssistantApplication
import com.codeverse.assistant.R
import com.codeverse.assistant.accessibility.PortalAccessibilityService
import com.codeverse.assistant.state.AssistantCoordinator
import com.codeverse.assistant.state.AssistantUiState
import com.codeverse.assistant.state.OverlayTab
import com.codeverse.assistant.ui.DocumentCaptureActivity
import com.codeverse.assistant.ui.MainActivity
import kotlinx.coroutines.launch
import kotlin.math.roundToInt

/**
 * Foreground Service hosting a WindowManager TYPE_APPLICATION_OVERLAY window:
 * - Collapsed: 56dp draggable circular hovering ball
 * - Expanded: Interactive Compose card for Screen Capture, FR-037 Document Upload
 *   + Keystore Vault, and FR-027 Action-Bound Approval.
 */
class FloatingBubbleService : Service() {

    private class OverlayViewTreeOwner :
        LifecycleOwner,
        ViewModelStoreOwner,
        SavedStateRegistryOwner {

        private val lifecycleRegistry = LifecycleRegistry(this)
        private val store = ViewModelStore()
        private val savedStateController = SavedStateRegistryController.create(this)

        override val lifecycle: Lifecycle get() = lifecycleRegistry
        override val viewModelStore: ViewModelStore get() = store
        override val savedStateRegistry: SavedStateRegistry
            get() = savedStateController.savedStateRegistry

        fun performRestore() {
            savedStateController.performRestore(null)
            lifecycleRegistry.handleLifecycleEvent(Lifecycle.Event.ON_CREATE)
            lifecycleRegistry.handleLifecycleEvent(Lifecycle.Event.ON_START)
            lifecycleRegistry.handleLifecycleEvent(Lifecycle.Event.ON_RESUME)
        }

        fun destroy() {
            lifecycleRegistry.handleLifecycleEvent(Lifecycle.Event.ON_PAUSE)
            lifecycleRegistry.handleLifecycleEvent(Lifecycle.Event.ON_STOP)
            lifecycleRegistry.handleLifecycleEvent(Lifecycle.Event.ON_DESTROY)
            store.clear()
        }
    }

    private lateinit var windowManager: WindowManager
    private lateinit var coordinator: AssistantCoordinator
    private var overlayView: ComposeView? = null
    private var viewTreeOwner: OverlayViewTreeOwner? = null
    private var layoutParams: WindowManager.LayoutParams? = null

    override fun onCreate() {
        super.onCreate()
        windowManager = getSystemService(Context.WINDOW_SERVICE) as WindowManager
        coordinator = (application as AssistantApplication).coordinator

        startForegroundBubbleNotification()
        attachOverlayWindow()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopSelf()
            return START_NOT_STICKY
        }
        return START_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        super.onDestroy()
        overlayView?.let { view ->
            runCatching { windowManager.removeView(view) }
        }
        viewTreeOwner?.destroy()
        overlayView = null
        viewTreeOwner = null
    }

    private fun startForegroundBubbleNotification() {
        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        val channel = NotificationChannel(
            CHANNEL_ID,
            getString(R.string.bubble_notification_channel_name),
            NotificationManager.IMPORTANCE_LOW
        )
        manager.createNotificationChannel(channel)

        val openAppIntent = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )

        val notification: Notification = NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.mipmap.ic_launcher)
            .setContentTitle(getString(R.string.bubble_notification_title))
            .setContentText(getString(R.string.bubble_notification_text))
            .setContentIntent(openAppIntent)
            .setOngoing(true)
            .build()

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(
                NOTIFICATION_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE
            )
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    private fun attachOverlayWindow() {
        val params = WindowManager.LayoutParams(
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
            PixelFormat.TRANSLUCENT
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            x = 24
            y = 260
        }
        layoutParams = params

        val owner = OverlayViewTreeOwner().also { it.performRestore() }
        viewTreeOwner = owner

        val composeView = ComposeView(this).apply {
            setViewCompositionStrategy(ViewCompositionStrategy.DisposeOnDetachedFromWindow)
            setViewTreeLifecycleOwner(owner)
            setViewTreeViewModelStoreOwner(owner)
            setViewTreeSavedStateRegistryOwner(owner)
            setContent {
                MaterialTheme {
                    OverlayRoot(
                        coordinator = coordinator,
                        onDragBubble = { dx, dy -> updateWindowPosition(dx, dy) },
                        onExpandChanged = { expanded -> updateWindowFocusability(expanded) },
                        onLaunchDocPicker = { mode, docType ->
                            startActivity(
                                DocumentCaptureActivity.createIntent(
                                    this@FloatingBubbleService,
                                    mode,
                                    docType
                                )
                            )
                        },
                        onOpenMainApp = {
                            startActivity(
                                Intent(this@FloatingBubbleService, MainActivity::class.java).apply {
                                    addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                                }
                            )
                        },
                        onCloseService = { stopSelf() }
                    )
                }
            }
        }
        overlayView = composeView
        windowManager.addView(composeView, params)
    }

    private fun updateWindowPosition(dx: Float, dy: Float) {
        val params = layoutParams ?: return
        val view = overlayView ?: return
        params.x += dx.roundToInt()
        params.y += dy.roundToInt()
        runCatching { windowManager.updateViewLayout(view, params) }
    }

    private fun updateWindowFocusability(expanded: Boolean) {
        val params = layoutParams ?: return
        val view = overlayView ?: return
        params.flags = if (expanded) {
            // Allow typing into overlay inputs while expanded
            WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or
                WindowManager.LayoutParams.FLAG_WATCH_OUTSIDE_TOUCH
        } else {
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN
        }
        runCatching { windowManager.updateViewLayout(view, params) }
    }

    companion object {
        private const val CHANNEL_ID = "floating_bubble_channel"
        private const val NOTIFICATION_ID = 4107
        const val ACTION_STOP = "com.codeverse.assistant.ACTION_STOP_BUBBLE"
    }
}

@Composable
private fun OverlayRoot(
    coordinator: AssistantCoordinator,
    onDragBubble: (Float, Float) -> Unit,
    onExpandChanged: (Boolean) -> Unit,
    onLaunchDocPicker: (mode: String, docType: String) -> Unit,
    onOpenMainApp: () -> Unit,
    onCloseService: () -> Unit
) {
    val state by coordinator.uiState.collectAsState()
    val a11yConnected by PortalAccessibilityService.isConnected.collectAsState()

    if (!state.overlayExpanded) {
        FloatingBallBubble(
            hasPendingApproval = !state.pendingActionHash.isNullOrBlank(),
            a11yConnected = a11yConnected,
            onDrag = onDragBubble,
            onTap = {
                coordinator.setOverlayExpanded(true)
                onExpandChanged(true)
            }
        )
    } else {
        ExpandedOverlayCard(
            state = state,
            a11yConnected = a11yConnected,
            coordinator = coordinator,
            onCollapse = {
                coordinator.setOverlayExpanded(false)
                onExpandChanged(false)
            },
            onLaunchDocPicker = onLaunchDocPicker,
            onOpenMainApp = onOpenMainApp,
            onCloseService = onCloseService
        )
    }
}

@Composable
private fun FloatingBallBubble(
    hasPendingApproval: Boolean,
    a11yConnected: Boolean,
    onDrag: (Float, Float) -> Unit,
    onTap: () -> Unit
) {
    val bubbleColor = when {
        hasPendingApproval -> Color(0xFFD97706)
        a11yConnected -> Color(0xFF0F4C81)
        else -> Color(0xFF475569)
    }
    Box(
        modifier = Modifier
            .size(56.dp)
            .shadow(8.dp, CircleShape)
            .clip(CircleShape)
            .background(bubbleColor)
            .border(2.dp, Color.White, CircleShape)
            .pointerInput(Unit) {
                detectDragGestures { change, dragAmount ->
                    change.consume()
                    onDrag(dragAmount.x, dragAmount.y)
                }
            }
            .clickable { onTap() },
        contentAlignment = Alignment.Center
    ) {
        Icon(
            imageVector = if (hasPendingApproval) Icons.Default.VerifiedUser else Icons.Default.Assistant,
            contentDescription = "Expand Assistant",
            tint = Color.White,
            modifier = Modifier.size(28.dp)
        )
    }
}

@Composable
private fun ExpandedOverlayCard(
    state: AssistantUiState,
    a11yConnected: Boolean,
    coordinator: AssistantCoordinator,
    onCollapse: () -> Unit,
    onLaunchDocPicker: (mode: String, docType: String) -> Unit,
    onOpenMainApp: () -> Unit,
    onCloseService: () -> Unit
) {
    val scope = rememberCoroutineScope()

    Card(
        modifier = Modifier
            .widthIn(min = 320.dp, max = 368.dp)
            .heightIn(max = 560.dp)
            .padding(8.dp),
        shape = RoundedCornerShape(18.dp),
        colors = CardDefaults.cardColors(containerColor = Color(0xFFFDFEFE)),
        elevation = CardDefaults.cardElevation(defaultElevation = 12.dp)
    ) {
        Column(modifier = Modifier.padding(12.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        text = "JanSahayak Overlay",
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.Bold,
                        color = Color(0xFF0F4C81)
                    )
                    Text(
                        text = if (a11yConnected) "Reader Active • ${state.agentStatus}" else "Enable Accessibility Reader",
                        style = MaterialTheme.typography.labelSmall,
                        color = if (a11yConnected) Color(0xFF15803D) else Color(0xFFB91C1C),
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis
                    )
                }
                Row {
                    Text(
                        text = "App",
                        style = MaterialTheme.typography.labelMedium,
                        color = Color(0xFF0F4C81),
                        modifier = Modifier
                            .clip(RoundedCornerShape(8.dp))
                            .clickable { onOpenMainApp() }
                            .padding(horizontal = 8.dp, vertical = 6.dp)
                    )
                    IconButton(onClick = onCollapse, modifier = Modifier.size(32.dp)) {
                        Icon(Icons.Default.Close, contentDescription = "Collapse to Bubble")
                    }
                }
            }

            if (state.isBusy) {
                Row(
                    modifier = Modifier.padding(vertical = 4.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    CircularProgressIndicator(modifier = Modifier.size(16.dp), strokeWidth = 2.dp)
                    Spacer(Modifier.width(8.dp))
                    Text("Syncing with agent...", style = MaterialTheme.typography.labelSmall)
                }
            }

            state.errorMessage?.let { err ->
                Surface(
                    color = Color(0xFFFEE2E2),
                    shape = RoundedCornerShape(8.dp),
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(vertical = 4.dp)
                ) {
                    Text(
                        text = err,
                        color = Color(0xFF991B1B),
                        style = MaterialTheme.typography.labelSmall,
                        modifier = Modifier.padding(8.dp)
                    )
                }
            }

            val tabs = listOf(
                OverlayTab.ASSIST to "Assist",
                OverlayTab.VAULT_DOCS to "Vault",
                OverlayTab.APPROVAL to if (state.pendingActionHash != null) "Approve!" else "Approve"
            )
            TabRow(selectedTabIndex = tabs.indexOfFirst { it.first == state.activeOverlayTab }.coerceAtLeast(0)) {
                tabs.forEach { (tab, label) ->
                    Tab(
                        selected = state.activeOverlayTab == tab,
                        onClick = { coordinator.selectOverlayTab(tab) },
                        text = { Text(label, maxLines = 1) }
                    )
                }
            }

            Spacer(Modifier.height(8.dp))

            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                when (state.activeOverlayTab) {
                    OverlayTab.ASSIST -> AssistTabContent(
                        state = state,
                        onCapture = { prompt ->
                            scope.launch { coordinator.captureScreenAndConsultAgent(prompt) }
                        },
                        onAuthorizeFill = { coordinator.authorizeAndExecuteFieldFill() },
                        onStopBubble = onCloseService
                    )

                    OverlayTab.VAULT_DOCS -> VaultDocsTabContent(
                        state = state,
                        coordinator = coordinator,
                        onLaunchDocPicker = onLaunchDocPicker
                    )

                    OverlayTab.APPROVAL -> ApprovalTabContent(
                        state = state,
                        onApprove = { note ->
                            scope.launch { coordinator.submitTaskApproval(approved = true, reviewerNote = note) }
                        },
                        onReject = { note ->
                            scope.launch { coordinator.submitTaskApproval(approved = false, reviewerNote = note) }
                        }
                    )
                }
            }
        }
    }
}

@Composable
private fun AssistTabContent(
    state: AssistantUiState,
    onCapture: (String?) -> Unit,
    onAuthorizeFill: () -> Unit,
    onStopBubble: () -> Unit
) {
    var followUpPrompt by remember { mutableStateOf("") }

    Button(
        onClick = { onCapture(followUpPrompt.ifBlank { null }) },
        modifier = Modifier.fillMaxWidth()
    ) {
        Icon(Icons.Default.Screenshot, contentDescription = null, modifier = Modifier.size(18.dp))
        Spacer(Modifier.width(8.dp))
        Text("Capture Screen & Inspect DOM")
    }

    OutlinedTextField(
        value = followUpPrompt,
        onValueChange = { followUpPrompt = it },
        label = { Text("Ask / Clarify (en or hi)") },
        singleLine = true,
        modifier = Modifier.fillMaxWidth()
    )

    state.lastSnapshot?.let { snap ->
        Surface(
            color = Color(0xFFF1F5F9),
            shape = RoundedCornerShape(10.dp),
            modifier = Modifier.fillMaxWidth()
        ) {
            Column(modifier = Modifier.padding(8.dp)) {
                Text(
                    text = "Snapshot: ${snap.url}",
                    style = MaterialTheme.typography.labelSmall,
                    fontWeight = FontWeight.SemiBold,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis
                )
                Text(
                    text = "Step ${snap.currentStepNumber}/${snap.totalSteps} • ${snap.fields.size} fields • ${snap.buttons.size} buttons",
                    style = MaterialTheme.typography.labelSmall,
                    color = Color(0xFF475569)
                )
            }
        }
    }

    if (state.agentMessage.isNotBlank()) {
        Surface(
            color = Color(0xFFEFF6FF),
            shape = RoundedCornerShape(10.dp),
            modifier = Modifier.fillMaxWidth()
        ) {
            Text(
                text = state.agentMessage,
                style = MaterialTheme.typography.bodySmall,
                modifier = Modifier.padding(10.dp)
            )
        }
    }

    val fillActions = state.pendingActions.filter { it.actionType == "FILL_FIELD" }
    if (fillActions.isNotEmpty()) {
        Text(
            text = "Prepared Field Actions (${fillActions.size})",
            style = MaterialTheme.typography.labelMedium,
            fontWeight = FontWeight.Bold
        )
        fillActions.take(6).forEach { act ->
            Text(
                text = "• ${act.elementKey ?: act.selector} <- ${if (act.isSensitive) "[Protected Vault Ref]" else act.valueOrVaultRef}",
                style = MaterialTheme.typography.labelSmall
            )
        }
        Button(
            onClick = onAuthorizeFill,
            colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF15803D)),
            modifier = Modifier.fillMaxWidth()
        ) {
            Icon(Icons.Default.PlayArrow, contentDescription = null, modifier = Modifier.size(18.dp))
            Spacer(Modifier.width(6.dp))
            Text("Authorize & Fill Fields (AUTHORIZE_FIELD_FILL)")
        }
    }

    if (state.statusLog.isNotBlank()) {
        Text(
            text = state.statusLog,
            style = MaterialTheme.typography.labelSmall,
            color = Color(0xFF334155)
        )
    }

    OutlinedButton(
        onClick = onStopBubble,
        modifier = Modifier.fillMaxWidth()
    ) {
        Text("Stop Floating Bubble")
    }
}

@Composable
private fun VaultDocsTabContent(
    state: AssistantUiState,
    coordinator: AssistantCoordinator,
    onLaunchDocPicker: (mode: String, docType: String) -> Unit
) {
    val docTypes = listOf("pan", "aadhaar", "bank_passbook", "marksheet", "income_certificate")
    var selectedDocType by remember { mutableStateOf("pan") }

    Text("1. Select Document & Upload (FR-037)", style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.Bold)
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .horizontalScroll(rememberScrollState()),
        horizontalArrangement = Arrangement.spacedBy(6.dp)
    ) {
        docTypes.forEach { docType ->
            FilterChip(
                selected = selectedDocType == docType,
                onClick = { selectedDocType = docType },
                label = { Text(docType) }
            )
        }
    }

    Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Button(
            onClick = { onLaunchDocPicker(DocumentCaptureActivity.MODE_PICK, selectedDocType) },
            modifier = Modifier.weight(1f)
        ) {
            Icon(Icons.Default.UploadFile, contentDescription = null, modifier = Modifier.size(16.dp))
            Spacer(Modifier.width(4.dp))
            Text("Pick File")
        }
        OutlinedButton(
            onClick = { onLaunchDocPicker(DocumentCaptureActivity.MODE_CAMERA, selectedDocType) },
            modifier = Modifier.weight(1f)
        ) {
            Icon(Icons.Default.CameraAlt, contentDescription = null, modifier = Modifier.size(16.dp))
            Spacer(Modifier.width(4.dp))
            Text("Camera")
        }
    }

    state.extractionResult?.let { res ->
        Surface(
            color = if (res.validation.overallPassed) Color(0xFFDCFCE7) else Color(0xFFFEF3C7),
            shape = RoundedCornerShape(8.dp),
            modifier = Modifier.fillMaxWidth()
        ) {
            Row(modifier = Modifier.padding(8.dp), verticalAlignment = Alignment.CenterVertically) {
                Icon(Icons.Default.DocumentScanner, contentDescription = null, modifier = Modifier.size(16.dp))
                Spacer(Modifier.width(6.dp))
                Text(
                    text = "Detected: ${res.detectedDocType} (${(res.classificationConfidence * 100).toInt()}%) • 4-Stage: ${if (res.validation.overallPassed) "PASSED" else "REVIEW"}",
                    style = MaterialTheme.typography.labelSmall
                )
            }
        }
    }

    if (state.editableFields.isNotEmpty()) {
        Text(
            text = "2. Verify Fields (verifiedByUser required)",
            style = MaterialTheme.typography.labelMedium,
            fontWeight = FontWeight.Bold
        )
        state.editableFields.forEachIndexed { index, field ->
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Checkbox(
                    checked = field.verifiedByUser,
                    onCheckedChange = { checked ->
                        coordinator.updateEditableField(index, verifiedByUser = checked)
                    }
                )
                OutlinedTextField(
                    value = field.value,
                    onValueChange = { txt -> coordinator.updateEditableField(index, newValue = txt) },
                    label = {
                        val badge = "${(field.confidence * 100).toInt()}%"
                        val warn = if (field.needsUserVerification) " [Check]" else ""
                        Text("${field.key} ($badge)$warn")
                    },
                    singleLine = true,
                    modifier = Modifier.weight(1f)
                )
            }
        }
        Button(
            onClick = { coordinator.commitVerifiedFieldsToVault(selectedDocType) },
            modifier = Modifier.fillMaxWidth()
        ) {
            Icon(Icons.Default.Lock, contentDescription = null, modifier = Modifier.size(16.dp))
            Spacer(Modifier.width(6.dp))
            Text("Save Verified to Keystore Vault")
        }
    }

    HorizontalDivider()
    Text(
        text = "Keystore Vault (${state.vaultEntries.size} verified fields)",
        style = MaterialTheme.typography.labelMedium,
        fontWeight = FontWeight.Bold
    )
    if (state.vaultEntries.isEmpty()) {
        Text("No encrypted fields stored yet.", style = MaterialTheme.typography.labelSmall, color = Color(0xFF64748B))
    } else {
        state.vaultEntries.forEach { entry ->
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        text = entry.vaultKey,
                        style = MaterialTheme.typography.labelSmall,
                        fontFamily = FontFamily.Monospace,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis
                    )
                    Text(
                        text = entry.maskedValue,
                        style = MaterialTheme.typography.bodySmall,
                        fontWeight = FontWeight.Medium
                    )
                }
                Icon(
                    imageVector = Icons.Default.CheckCircle,
                    contentDescription = "verifiedByUser",
                    tint = Color(0xFF15803D),
                    modifier = Modifier.size(16.dp)
                )
            }
        }
    }
}

@Composable
private fun ApprovalTabContent(
    state: AssistantUiState,
    onApprove: (String) -> Unit,
    onReject: (String) -> Unit
) {
    var reviewerNote by remember { mutableStateOf("") }
    val pendingHash = state.pendingActionHash

    if (pendingHash.isNullOrBlank()) {
        Surface(
            color = Color(0xFFF1F5F9),
            shape = RoundedCornerShape(10.dp),
            modifier = Modifier.fillMaxWidth()
        ) {
            Text(
                text = "No pending final submission awaiting FR-027 approval. Capture the portal review step first.",
                style = MaterialTheme.typography.bodySmall,
                modifier = Modifier.padding(10.dp)
            )
        }
        return
    }

    Surface(
        color = Color(0xFFFFFBEB),
        shape = RoundedCornerShape(10.dp),
        modifier = Modifier.fillMaxWidth()
    ) {
        Column(modifier = Modifier.padding(10.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(
                text = "Action-Bound Approval Required (FR-027)",
                style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.Bold,
                color = Color(0xFFB45309)
            )
            Text(
                text = state.approvalWarning ?: "Review all fields carefully before approving final portal submission.",
                style = MaterialTheme.typography.bodySmall
            )
            Text(
                text = "pending_action_hash:",
                style = MaterialTheme.typography.labelSmall,
                fontWeight = FontWeight.Bold
            )
            Surface(color = Color(0xFF0F172A), shape = RoundedCornerShape(6.dp)) {
                Text(
                    text = pendingHash,
                    color = Color(0xFF38BDF8),
                    style = MaterialTheme.typography.labelSmall,
                    fontFamily = FontFamily.Monospace,
                    modifier = Modifier.padding(8.dp)
                )
            }
        }
    }

    if (state.approvalSummary.isNotEmpty()) {
        Text("Summary of Changes:", style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.Bold)
        state.approvalSummary.forEach { (k, v) ->
            Text("• $k: $v", style = MaterialTheme.typography.labelSmall)
        }
    }

    OutlinedTextField(
        value = reviewerNote,
        onValueChange = { reviewerNote = it },
        label = { Text("Reviewer Note (optional)") },
        singleLine = true,
        modifier = Modifier.fillMaxWidth()
    )

    Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Button(
            onClick = { onApprove(reviewerNote) },
            colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF15803D)),
            modifier = Modifier.weight(1f)
        ) {
            Text("Approve & Submit")
        }
        OutlinedButton(
            onClick = { onReject(reviewerNote) },
            modifier = Modifier.weight(1f)
        ) {
            Text("Reject")
        }
    }
}
