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
import android.net.Uri
import android.os.Build
import android.os.IBinder
import android.view.Gravity
import android.view.WindowManager
import androidx.compose.foundation.BorderStroke
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
import androidx.compose.material.icons.automirrored.filled.ArrowForward
import androidx.compose.material.icons.filled.AccountBalance
import androidx.compose.material.icons.filled.AutoAwesome
import androidx.compose.material.icons.filled.AutoFixHigh
import androidx.compose.material.icons.filled.CallMade
import androidx.compose.material.icons.filled.CameraAlt
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Description
import androidx.compose.material.icons.filled.HelpOutline
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.Public
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Schedule
import androidx.compose.material.icons.filled.Shield
import androidx.compose.material.icons.filled.Upload
import androidx.compose.material.icons.filled.Verified
import androidx.compose.material.icons.filled.VerifiedUser
import androidx.compose.material.icons.filled.WarningAmber
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
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.ComposeView
import androidx.compose.ui.platform.ViewCompositionStrategy
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
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
import com.codeverse.assistant.network.CaptureReceiptOutput
import com.codeverse.assistant.state.AssistantCoordinator
import com.codeverse.assistant.state.AssistantUiState
import com.codeverse.assistant.state.OverlayTab
import com.codeverse.assistant.ui.DocumentCaptureActivity
import com.codeverse.assistant.ui.MainActivity
import kotlinx.coroutines.launch
import kotlin.math.roundToInt

/**
 * Foreground Service hosting a WindowManager TYPE_APPLICATION_OVERLAY window
 * matching the Universal AI (omni-assist) floating experience:
 * - Collapsed: 56dp draggable circular gradient ball with live status pill
 * - Expanded: Interactive frosted sheet with Live Screen Context, Automate,
 *   Documents (FR-037 Keystore Vault), Approve (FR-027), Visual Guide, and Receipt (FR-061).
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
                    OmniOverlayRoot(
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
private fun OmniOverlayRoot(
    coordinator: AssistantCoordinator,
    onDragBubble: (Float, Float) -> Unit,
    onExpandChanged: (Boolean) -> Unit,
    onLaunchDocPicker: (mode: String, docType: String) -> Unit,
    onOpenMainApp: () -> Unit,
    onCloseService: () -> Unit
) {
    val state by coordinator.uiState.collectAsState()
    val a11yConnected by PortalAccessibilityService.isConnected.collectAsState()
    val activeBrowserPkg by PortalAccessibilityService.activePackageName.collectAsState()

    if (!state.overlayExpanded) {
        val badgeText = when {
            !state.pendingActionHash.isNullOrBlank() -> "Approval Ready"
            state.pendingActions.isNotEmpty() -> "${state.pendingActions.size} Action Ready"
            else -> "Assistant Ready"
        }
        OmniHoveringBall(
            badgeText = badgeText,
            hasPendingApproval = !state.pendingActionHash.isNullOrBlank(),
            onDrag = onDragBubble,
            onTap = {
                coordinator.setOverlayExpanded(true)
                onExpandChanged(true)
            }
        )
    } else if (state.receiptData != null && state.receiptData?.isSuccess == true) {
        VerifiedReceiptOverlayCard(
            receipt = state.receiptData!!,
            portalUrl = state.resolvedPortalUrl ?: state.lastSnapshot?.url ?: "scholarships.gov.in",
            onDone = {
                coordinator.dismissReceipt()
                onExpandChanged(false)
            }
        )
    } else {
        OmniExpandedSheet(
            state = state,
            a11yConnected = a11yConnected,
            activeBrowserPkg = activeBrowserPkg,
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
private fun OmniHoveringBall(
    badgeText: String,
    hasPendingApproval: Boolean,
    onDrag: (Float, Float) -> Unit,
    onTap: () -> Unit
) {
    Box(
        modifier = Modifier.padding(top = 12.dp, end = 14.dp),
        contentAlignment = Alignment.Center
    ) {
        Box(
            modifier = Modifier
                .size(56.dp)
                .shadow(10.dp, CircleShape)
                .clip(CircleShape)
                .background(
                    Brush.linearGradient(
                        colors = listOf(
                            Color(0xFF2460DA),
                            Color(0xFF1D4ED8),
                            Color(0xFF14805F)
                        )
                    )
                )
                .border(2.5.dp, Color.White.copy(alpha = 0.88f), CircleShape)
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
                imageVector = Icons.Default.Shield,
                contentDescription = "Open floating assistant",
                tint = Color.White,
                modifier = Modifier.size(24.dp)
            )
            Icon(
                imageVector = Icons.Default.AutoAwesome,
                contentDescription = null,
                tint = Color.White,
                modifier = Modifier.size(10.dp)
            )
        }

        Surface(
            color = Color.White,
            shape = RoundedCornerShape(12.dp),
            border = BorderStroke(1.dp, Color(0xFFE0E9F7)),
            shadowElevation = 3.dp,
            modifier = Modifier.align(Alignment.TopEnd)
        ) {
            Row(
                modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Box(
                    modifier = Modifier
                        .size(6.dp)
                        .clip(CircleShape)
                        .background(if (hasPendingApproval) Color(0xFFD99526) else Color(0xFF15976A))
                )
                Spacer(Modifier.width(4.dp))
                Text(
                    text = badgeText,
                    fontSize = 9.sp,
                    fontWeight = FontWeight.Bold,
                    color = Color(0xFF4C607E)
                )
            }
        }
    }
}

@Composable
private fun OmniExpandedSheet(
    state: AssistantUiState,
    a11yConnected: Boolean,
    activeBrowserPkg: String?,
    coordinator: AssistantCoordinator,
    onCollapse: () -> Unit,
    onLaunchDocPicker: (mode: String, docType: String) -> Unit,
    onOpenMainApp: () -> Unit,
    onCloseService: () -> Unit
) {
    val scope = rememberCoroutineScope()

    Card(
        modifier = Modifier
            .widthIn(min = 336.dp, max = 376.dp)
            .heightIn(max = 590.dp)
            .padding(6.dp),
        shape = RoundedCornerShape(20.dp),
        colors = CardDefaults.cardColors(containerColor = Color(0xFFFDFEFE)),
        border = BorderStroke(1.dp, Color(0xFFE5EAF0)),
        elevation = CardDefaults.cardElevation(defaultElevation = 14.dp)
    ) {
        Column(modifier = Modifier.fillMaxWidth()) {
            Box(
                modifier = Modifier
                    .padding(top = 8.dp, bottom = 4.dp)
                    .width(34.dp)
                    .height(3.5.dp)
                    .clip(RoundedCornerShape(4.dp))
                    .background(Color(0xFFD3DAE3))
                    .align(Alignment.CenterHorizontally)
            )

            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 12.dp, vertical = 4.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    modifier = Modifier.weight(1f)
                ) {
                    Box(
                        modifier = Modifier
                            .size(28.dp)
                            .clip(RoundedCornerShape(8.dp))
                            .background(Color(0xFFEDF3FF)),
                        contentAlignment = Alignment.Center
                    ) {
                        Icon(
                            imageVector = Icons.Default.AutoAwesome,
                            contentDescription = null,
                            tint = Color(0xFF285FC9),
                            modifier = Modifier.size(16.dp)
                        )
                    }
                    Spacer(Modifier.width(8.dp))
                    Column {
                        Text(
                            text = "Assistant is ready",
                            fontSize = 13.sp,
                            fontWeight = FontWeight.Bold,
                            color = Color(0xFF35445A)
                        )
                        val browserName = activeBrowserPkg?.substringAfterLast('.')?.replaceFirstChar { it.uppercase() }
                            ?: "Mobile Browser"
                        Text(
                            text = "Hovering over $browserName",
                            fontSize = 10.sp,
                            color = Color(0xFF929DAC),
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis
                        )
                    }
                }

                Row(verticalAlignment = Alignment.CenterVertically) {
                    IconButton(
                        onClick = { coordinator.setGuideDialogVisible(true) },
                        modifier = Modifier.size(28.dp)
                    ) {
                        Icon(
                            imageVector = Icons.Default.HelpOutline,
                            contentDescription = "Visual Guide",
                            tint = Color(0xFF8794A6),
                            modifier = Modifier.size(17.dp)
                        )
                    }
                    Surface(
                        color = Color.White,
                        shape = RoundedCornerShape(7.dp),
                        border = BorderStroke(1.dp, Color(0xFFE6EAF0)),
                        modifier = Modifier.clickable { onCollapse() }
                    ) {
                        Text(
                            text = "Minimize to Ball",
                            fontSize = 10.sp,
                            fontWeight = FontWeight.SemiBold,
                            color = Color(0xFF69788D),
                            modifier = Modifier.padding(horizontal = 8.dp, vertical = 5.dp)
                        )
                    }
                }
            }

            Surface(
                color = Color(0xFFF6F9FF),
                shape = RoundedCornerShape(9.dp),
                border = BorderStroke(1.dp, Color(0xFFE1EAF7)),
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 11.dp, vertical = 4.dp)
            ) {
                Column(modifier = Modifier.padding(horizontal = 9.dp, vertical = 7.dp)) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Box(
                            modifier = Modifier
                                .size(7.dp)
                                .clip(CircleShape)
                                .background(if (a11yConnected) Color(0xFF4885E3) else Color(0xFFD97706))
                        )
                        Spacer(Modifier.width(6.dp))
                        Text(
                            text = "LIVE SCREEN CONTEXT",
                            fontSize = 9.sp,
                            fontWeight = FontWeight.Bold,
                            letterSpacing = 0.7.sp,
                            color = Color(0xFF6E7F99),
                            modifier = Modifier.weight(1f)
                        )
                        Surface(
                            color = Color(0xFFE8F1FF),
                            shape = RoundedCornerShape(4.dp),
                            modifier = Modifier.clickable {
                                scope.launch { coordinator.captureScreenAndConsultAgent() }
                            }
                        ) {
                            Row(
                                modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                Icon(
                                    imageVector = Icons.Default.Refresh,
                                    contentDescription = "Capture screen",
                                    tint = Color(0xFF1D67BF),
                                    modifier = Modifier.size(11.dp)
                                )
                                Spacer(Modifier.width(3.dp))
                                Text(
                                    text = "CAPTURE",
                                    fontSize = 9.sp,
                                    fontWeight = FontWeight.Bold,
                                    color = Color(0xFF1D67BF)
                                )
                            }
                        }
                    }
                    Spacer(Modifier.height(5.dp))
                    val snap = state.lastSnapshot
                    val domain = snap?.url?.let { Uri.parse(it).host }
                        ?: state.portalVerification?.canonicalDomain
                        ?: "Awaiting capture"
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(10.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Text(
                            text = if (a11yConnected) "Reading Screen" else "Reader Off",
                            fontSize = 10.sp,
                            color = Color(0xFF61728A)
                        )
                        Text(
                            text = "${snap?.fields?.size ?: 0} fields",
                            fontSize = 10.sp,
                            color = Color(0xFF61728A)
                        )
                        Text(
                            text = domain,
                            fontSize = 10.sp,
                            color = Color(0xFF16845C),
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis
                        )
                    }
                }
            }

            if (state.isBusy) {
                Row(
                    modifier = Modifier.padding(horizontal = 14.dp, vertical = 4.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    CircularProgressIndicator(modifier = Modifier.size(14.dp), strokeWidth = 2.dp)
                    Spacer(Modifier.width(8.dp))
                    Text("Syncing with backend…", fontSize = 11.sp, color = Color(0xFF526178))
                }
            }

            state.errorMessage?.let { err ->
                Surface(
                    color = Color(0xFFFEE2E2),
                    shape = RoundedCornerShape(8.dp),
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 11.dp, vertical = 4.dp)
                ) {
                    Text(
                        text = err,
                        color = Color(0xFF991B1B),
                        fontSize = 11.sp,
                        modifier = Modifier.padding(8.dp)
                    )
                }
            }

            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 11.dp)
            ) {
                OverlayTabButton(
                    title = "Automate",
                    icon = Icons.Default.AutoFixHigh,
                    selected = state.activeOverlayTab == OverlayTab.AUTOMATE,
                    showDot = false,
                    onClick = { coordinator.selectOverlayTab(OverlayTab.AUTOMATE) },
                    modifier = Modifier.weight(1f)
                )
                OverlayTabButton(
                    title = "Documents",
                    icon = Icons.Default.Description,
                    selected = state.activeOverlayTab == OverlayTab.DOCUMENTS,
                    showDot = false,
                    onClick = { coordinator.selectOverlayTab(OverlayTab.DOCUMENTS) },
                    modifier = Modifier.weight(1f)
                )
                OverlayTabButton(
                    title = "Approve",
                    icon = Icons.Default.VerifiedUser,
                    selected = state.activeOverlayTab == OverlayTab.APPROVAL,
                    showDot = !state.pendingActionHash.isNullOrBlank(),
                    onClick = { coordinator.selectOverlayTab(OverlayTab.APPROVAL) },
                    modifier = Modifier.weight(1f)
                )
            }
            HorizontalDivider(color = Color(0xFFE8EDF2), modifier = Modifier.padding(horizontal = 11.dp))

            Column(
                modifier = Modifier
                    .weight(1f, fill = false)
                    .verticalScroll(rememberScrollState())
                    .padding(horizontal = 12.dp, vertical = 10.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                when (state.activeOverlayTab) {
                    OverlayTab.AUTOMATE -> AutomateTabBody(
                        state = state,
                        onCapture = { prompt ->
                            scope.launch { coordinator.captureScreenAndConsultAgent(prompt) }
                        },
                        onAutofill = { coordinator.authorizeAndExecuteFieldFill() },
                        onOpenGuide = { coordinator.setGuideDialogVisible(true) },
                        onOpenMainApp = onOpenMainApp,
                        onCloseService = onCloseService
                    )

                    OverlayTab.DOCUMENTS -> DocumentsTabBody(
                        state = state,
                        coordinator = coordinator,
                        onLaunchDocPicker = onLaunchDocPicker
                    )

                    OverlayTab.APPROVAL -> ApprovalTabBody(
                        state = state,
                        onApprove = { note ->
                            scope.launch { coordinator.submitTaskApproval(approved = true, reviewerNote = note) }
                        },
                        onCancel = { note ->
                            scope.launch {
                                coordinator.submitTaskApproval(approved = false, reviewerNote = note)
                                coordinator.selectOverlayTab(OverlayTab.AUTOMATE)
                            }
                        }
                    )
                }

                if (state.showGuideDialog) {
                    VisualGuideInlineCard(
                        guide = state.visualGuide,
                        onClose = { coordinator.setGuideDialogVisible(false) }
                    )
                }
            }

            HorizontalDivider(color = Color(0xFFEDF0F4))
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(vertical = 7.dp),
                horizontalArrangement = Arrangement.Center,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Icon(
                    imageVector = Icons.Default.Lock,
                    contentDescription = null,
                    tint = Color(0xFF19845D),
                    modifier = Modifier.size(12.dp)
                )
                Spacer(Modifier.width(5.dp))
                Text(
                    text = "Actions are local and require your approval",
                    fontSize = 10.sp,
                    color = Color(0xFF94A0AE)
                )
            }
        }
    }
}

@Composable
private fun OverlayTabButton(
    title: String,
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    selected: Boolean,
    showDot: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier
) {
    Column(
        modifier = modifier
            .clickable { onClick() }
            .padding(top = 8.dp),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(
                imageVector = icon,
                contentDescription = null,
                tint = if (selected) Color(0xFF2257BE) else Color(0xFF8591A2),
                modifier = Modifier.size(14.dp)
            )
            Spacer(Modifier.width(4.dp))
            Text(
                text = title,
                fontSize = 11.sp,
                fontWeight = FontWeight.SemiBold,
                color = if (selected) Color(0xFF2257BE) else Color(0xFF8591A2),
                maxLines = 1
            )
            if (showDot) {
                Spacer(Modifier.width(4.dp))
                Box(
                    modifier = Modifier
                        .size(6.dp)
                        .clip(CircleShape)
                        .background(Color(0xFFD99526))
                )
            }
        }
        Spacer(Modifier.height(7.dp))
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .height(2.dp)
                .background(if (selected) Color(0xFF2861CF) else Color.Transparent)
        )
    }
}

@Composable
private fun AutomateTabBody(
    state: AssistantUiState,
    onCapture: (String?) -> Unit,
    onAutofill: () -> Unit,
    onOpenGuide: () -> Unit,
    onOpenMainApp: () -> Unit,
    onCloseService: () -> Unit
) {
    var promptInput by remember { mutableStateOf("") }
    val fillActions = state.pendingActions.filter { it.actionType == "FILL_FIELD" }

    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically
    ) {
        Column {
            Text(
                text = "SMART FIELD MATCHING",
                fontSize = 9.sp,
                fontWeight = FontWeight.Bold,
                letterSpacing = 0.7.sp,
                color = Color(0xFF8B97A8)
            )
            Text(
                text = "${fillActions.size} fields matched",
                fontSize = 13.sp,
                fontWeight = FontWeight.Bold,
                color = Color(0xFF34435A)
            )
        }
        if (fillActions.isNotEmpty()) {
            Surface(color = Color(0xFFECF8F1), shape = RoundedCornerShape(6.dp)) {
                Text(
                    text = "✓ Ready",
                    fontSize = 10.sp,
                    fontWeight = FontWeight.Bold,
                    color = Color(0xFF16835C),
                    modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp)
                )
            }
        }
    }

    OutlinedTextField(
        value = promptInput,
        onValueChange = { promptInput = it },
        label = { Text("Ask / Clarify or Capture") },
        singleLine = true,
        modifier = Modifier.fillMaxWidth()
    )

    OutlinedButton(
        onClick = { onCapture(promptInput.ifBlank { null }) },
        modifier = Modifier.fillMaxWidth()
    ) {
        Icon(Icons.Default.Public, contentDescription = null, modifier = Modifier.size(16.dp))
        Spacer(Modifier.width(6.dp))
        Text("Capture Screen & Match Fields")
    }

    if (state.agentMessage.isNotBlank()) {
        Surface(
            color = Color(0xFFEFF6FF),
            shape = RoundedCornerShape(8.dp),
            modifier = Modifier.fillMaxWidth()
        ) {
            Text(
                text = state.agentMessage,
                fontSize = 11.sp,
                color = Color(0xFF1E3A8A),
                modifier = Modifier.padding(8.dp)
            )
        }
    }

    if (fillActions.isNotEmpty()) {
        Surface(
            color = Color.White,
            shape = RoundedCornerShape(9.dp),
            border = BorderStroke(1.dp, Color(0xFFE8EDF2)),
            modifier = Modifier.fillMaxWidth()
        ) {
            Column {
                fillActions.forEachIndexed { idx, action ->
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(horizontal = 9.dp, vertical = 8.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Box(
                            modifier = Modifier
                                .size(18.dp)
                                .clip(CircleShape)
                                .background(Color(0xFFEAF7F0)),
                            contentAlignment = Alignment.Center
                        ) {
                            Icon(
                                imageVector = Icons.Default.Check,
                                contentDescription = null,
                                tint = Color(0xFF16845C),
                                modifier = Modifier.size(11.dp)
                            )
                        }
                        Spacer(Modifier.width(8.dp))
                        Column(modifier = Modifier.weight(1f)) {
                            Text(
                                text = action.description.ifBlank { action.elementKey ?: "Field" },
                                fontSize = 11.sp,
                                fontWeight = FontWeight.SemiBold,
                                color = Color(0xFF506078),
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis
                            )
                            Text(
                                text = action.valueOrVaultRef ?: "",
                                fontSize = 10.sp,
                                fontFamily = FontFamily.Monospace,
                                color = Color(0xFF9A8A6B),
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis
                            )
                        }
                    }
                    if (idx < fillActions.lastIndex) {
                        HorizontalDivider(color = Color(0xFFF0F2F5))
                    }
                }
            }
        }

        Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(
                imageVector = Icons.Default.Lock,
                contentDescription = null,
                tint = Color(0xFF16845C),
                modifier = Modifier.size(13.dp)
            )
            Spacer(Modifier.width(6.dp))
            Text(
                text = "Values are typed locally into Chrome. Nothing is uploaded to our servers.",
                fontSize = 10.sp,
                color = Color(0xFF8693A3)
            )
        }

        Button(
            onClick = onAutofill,
            shape = RoundedCornerShape(8.dp),
            colors = if (state.fillCompleted) {
                ButtonDefaults.buttonColors(
                    containerColor = Color(0xFFEAF8F1),
                    contentColor = Color(0xFF16845C)
                )
            } else {
                ButtonDefaults.buttonColors(
                    containerColor = Color(0xFF1D4ED8),
                    contentColor = Color.White
                )
            },
            modifier = Modifier
                .fillMaxWidth()
                .height(40.dp)
        ) {
            Icon(
                imageVector = if (state.fillCompleted) Icons.Default.CheckCircle else Icons.Default.AutoFixHigh,
                contentDescription = null,
                modifier = Modifier.size(16.dp)
            )
            Spacer(Modifier.width(8.dp))
            Text(
                text = if (state.fillCompleted) "Fields filled in Chrome" else "Authorize & Auto-Fill Chrome",
                fontWeight = FontWeight.Bold,
                fontSize = 12.sp,
                modifier = Modifier.weight(1f)
            )
            if (!state.fillCompleted) {
                Icon(Icons.AutoMirrored.Filled.ArrowForward, contentDescription = null, modifier = Modifier.size(15.dp))
            }
        }
    }

    Surface(
        color = Color.White,
        shape = RoundedCornerShape(9.dp),
        border = BorderStroke(1.dp, Color(0xFFE8EDF2)),
        modifier = Modifier
            .fillMaxWidth()
            .clickable { onOpenGuide() }
    ) {
        Row(
            modifier = Modifier.padding(9.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Box(
                modifier = Modifier
                    .size(28.dp)
                    .clip(RoundedCornerShape(7.dp))
                    .background(Color(0xFFEDF3FF)),
                contentAlignment = Alignment.Center
            ) {
                Icon(
                    imageVector = Icons.Default.AccountBalance,
                    contentDescription = null,
                    tint = Color(0xFF2C5EC5),
                    modifier = Modifier.size(16.dp)
                )
            }
            Spacer(Modifier.width(8.dp))
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = "VISUAL GUIDE",
                    fontSize = 9.sp,
                    fontWeight = FontWeight.Bold,
                    color = Color(0xFF8794A5)
                )
                Text(
                    text = state.visualGuide?.title ?: "Find IFSC on your passbook",
                    fontSize = 11.sp,
                    fontWeight = FontWeight.Bold,
                    color = Color(0xFF41516A)
                )
                Text(
                    text = state.visualGuide?.explanation ?: "Look on the first page, beside your branch name.",
                    fontSize = 10.sp,
                    color = Color(0xFF8E9AAA),
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis
                )
            }
            Icon(
                imageVector = Icons.Default.CallMade,
                contentDescription = "Open visual guide",
                tint = Color(0xFF8895A4),
                modifier = Modifier.size(15.dp)
            )
        }
    }

    Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        OutlinedButton(onClick = onOpenMainApp, modifier = Modifier.weight(1f)) {
            Text("Main App", fontSize = 11.sp)
        }
        OutlinedButton(onClick = onCloseService, modifier = Modifier.weight(1f)) {
            Text("Stop Bubble", fontSize = 11.sp)
        }
    }
}

@Composable
private fun DocumentsTabBody(
    state: AssistantUiState,
    coordinator: AssistantCoordinator,
    onLaunchDocPicker: (mode: String, docType: String) -> Unit
) {
    val docTypes = listOf("pan", "aadhaar", "bank_passbook", "marksheet", "income_certificate")
    var selectedDocType by remember { mutableStateOf("pan") }

    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically
    ) {
        Column {
            Text(
                text = "FR-037 · ENCRYPTED LOCAL VAULT",
                fontSize = 9.sp,
                fontWeight = FontWeight.Bold,
                letterSpacing = 0.7.sp,
                color = Color(0xFF8B97A8)
            )
            Text(
                text = "Verify extracted fields",
                fontSize = 13.sp,
                fontWeight = FontWeight.Bold,
                color = Color(0xFF35445B)
            )
        }
        Surface(
            color = Color(0xFFEFF9F4),
            shape = RoundedCornerShape(6.dp),
            border = BorderStroke(1.dp, Color(0xFFDCEFE4))
        ) {
            Row(
                modifier = Modifier.padding(horizontal = 7.dp, vertical = 4.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Icon(Icons.Default.Lock, contentDescription = null, tint = Color(0xFF16845C), modifier = Modifier.size(11.dp))
                Spacer(Modifier.width(4.dp))
                Text("AES-256-GCM", fontSize = 9.sp, fontFamily = FontFamily.Monospace, color = Color(0xFF16845C))
            }
        }
    }

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
                label = { Text(docType, fontSize = 11.sp) }
            )
        }
    }

    Surface(
        color = Color(0xFFFBFDFE),
        shape = RoundedCornerShape(9.dp),
        border = BorderStroke(1.dp, Color(0xFFCBD6E4)),
        modifier = Modifier.fillMaxWidth()
    ) {
        Column(modifier = Modifier.padding(10.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(
                    modifier = Modifier
                        .size(30.dp)
                        .clip(RoundedCornerShape(8.dp))
                        .background(Color(0xFFEEF4FF)),
                    contentAlignment = Alignment.Center
                ) {
                    Icon(Icons.Default.Upload, contentDescription = null, tint = Color(0xFF3166C7), modifier = Modifier.size(16.dp))
                }
                Spacer(Modifier.width(9.dp))
                Column {
                    Text("Camera or choose a document", fontSize = 12.sp, fontWeight = FontWeight.Bold, color = Color(0xFF4C5B72))
                    Text("PAN · Aadhaar · Bank passbook", fontSize = 10.sp, color = Color(0xFF929EAC))
                }
            }
            Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(
                    onClick = { onLaunchDocPicker(DocumentCaptureActivity.MODE_PICK, selectedDocType) },
                    colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF1D4ED8)),
                    modifier = Modifier.weight(1f)
                ) {
                    Text("Choose File", fontSize = 11.sp)
                }
                OutlinedButton(
                    onClick = { onLaunchDocPicker(DocumentCaptureActivity.MODE_CAMERA, selectedDocType) },
                    modifier = Modifier.weight(1f)
                ) {
                    Icon(Icons.Default.CameraAlt, contentDescription = null, modifier = Modifier.size(14.dp))
                    Spacer(Modifier.width(4.dp))
                    Text("Camera", fontSize = 11.sp)
                }
            }
        }
    }

    state.extractionResult?.let { res ->
        Surface(
            color = if (res.validation.overallPassed) Color(0xFFEFF8F3) else Color(0xFFFFFBEB),
            shape = RoundedCornerShape(7.dp),
            modifier = Modifier.fillMaxWidth()
        ) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 8.dp, vertical = 7.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                val stages = listOf(
                    "Format" to res.validation.stage1TechnicalSpecs.passed,
                    "Quality" to res.validation.stage2VisualQuality.passed,
                    "OCR" to res.validation.stage3DocumentClassification.passed,
                    "Schema" to res.validation.stage4CrossFieldConsistency.passed
                )
                stages.forEach { (label, passed) ->
                    Text(
                        text = "${if (passed) "✓" else "!"} $label",
                        fontSize = 10.sp,
                        fontWeight = FontWeight.SemiBold,
                        color = if (passed) Color(0xFF16845C) else Color(0xFFB45309)
                    )
                }
            }
        }
    }

    if (state.editableFields.isNotEmpty()) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween
        ) {
            Text("Extracted information", fontSize = 11.sp, fontWeight = FontWeight.Bold, color = Color(0xFF4B5A71))
            Text("Confirm each field", fontSize = 10.sp, color = Color(0xFF929EAC))
        }

        Surface(
            color = Color.White,
            shape = RoundedCornerShape(8.dp),
            border = BorderStroke(1.dp, Color(0xFFE8EDF2)),
            modifier = Modifier.fillMaxWidth()
        ) {
            Column {
                state.editableFields.forEachIndexed { index, field ->
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(horizontal = 8.dp, vertical = 6.dp),
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
                            label = { Text(field.key, fontSize = 10.sp) },
                            singleLine = true,
                            modifier = Modifier.weight(1f)
                        )
                        Spacer(Modifier.width(6.dp))
                        Column(horizontalAlignment = Alignment.End) {
                            Text(
                                text = "${(field.confidence * 100).toInt()}%",
                                fontSize = 11.sp,
                                fontFamily = FontFamily.Monospace,
                                color = Color(0xFF16845C)
                            )
                            Text("CONFIDENCE", fontSize = 7.sp, color = Color(0xFFA3ACB7))
                        }
                    }
                    if (index < state.editableFields.lastIndex) {
                        HorizontalDivider(color = Color(0xFFF0F2F5))
                    }
                }
            }
        }

        val allChecked = state.editableFields.all { it.verifiedByUser }
        Button(
            onClick = { coordinator.commitVerifiedFieldsToVault(selectedDocType) },
            enabled = allChecked || state.vaultCommitSaved,
            colors = ButtonDefaults.buttonColors(
                containerColor = if (state.vaultCommitSaved) Color(0xFFEAF8F1) else Color(0xFF1D4ED8),
                contentColor = if (state.vaultCommitSaved) Color(0xFF16845C) else Color.White
            ),
            modifier = Modifier.fillMaxWidth()
        ) {
            Icon(
                imageVector = if (state.vaultCommitSaved) Icons.Default.CheckCircle else Icons.Default.Lock,
                contentDescription = null,
                modifier = Modifier.size(15.dp)
            )
            Spacer(Modifier.width(6.dp))
            Text(
                text = when {
                    state.vaultCommitSaved -> "Saved to encrypted local vault"
                    allChecked -> "Save verified fields to Keystore Vault"
                    else -> "Verify all fields to save to vault"
                },
                fontSize = 11.sp,
                fontWeight = FontWeight.Bold
            )
        }
        Text(
            text = "verifiedByUser consent is required for every field.",
            fontSize = 10.sp,
            color = Color(0xFF8D99A8),
            textAlign = TextAlign.Center,
            modifier = Modifier.fillMaxWidth()
        )
    }

    HorizontalDivider(color = Color(0xFFEDF0F4))
    Text(
        text = "Keystore Vault (${state.vaultEntries.size} verified fields)",
        fontSize = 11.sp,
        fontWeight = FontWeight.Bold,
        color = Color(0xFF35445B)
    )
    if (state.vaultEntries.isEmpty()) {
        Text("No encrypted fields stored yet.", fontSize = 11.sp, color = Color(0xFF64748B))
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
                        fontSize = 10.sp,
                        fontFamily = FontFamily.Monospace,
                        color = Color(0xFF758399),
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis
                    )
                    Text(
                        text = entry.maskedValue,
                        fontSize = 12.sp,
                        fontWeight = FontWeight.SemiBold,
                        color = Color(0xFF3C4C63)
                    )
                }
                Icon(
                    imageVector = Icons.Default.CheckCircle,
                    contentDescription = "verifiedByUser",
                    tint = Color(0xFF16845C),
                    modifier = Modifier.size(15.dp)
                )
            }
        }
    }
}

@Composable
private fun ApprovalTabBody(
    state: AssistantUiState,
    onApprove: (String) -> Unit,
    onCancel: (String) -> Unit
) {
    var reviewerNote by remember { mutableStateOf("") }
    val pendingHash = state.pendingActionHash

    Row(verticalAlignment = Alignment.CenterVertically) {
        Box(
            modifier = Modifier
                .size(28.dp)
                .clip(RoundedCornerShape(8.dp))
                .background(Color(0xFFEDF4FF)),
            contentAlignment = Alignment.Center
        ) {
            Icon(Icons.Default.VerifiedUser, contentDescription = null, tint = Color(0xFF285FC8), modifier = Modifier.size(16.dp))
        }
        Spacer(Modifier.width(8.dp))
        Column {
            Text(
                text = "FR-027 · ACTION-BOUND APPROVAL",
                fontSize = 9.sp,
                fontWeight = FontWeight.Bold,
                color = Color(0xFF63738B)
            )
            Text(
                text = "One final review before submitting",
                fontSize = 11.sp,
                color = Color(0xFF919EAE)
            )
        }
    }

    if (pendingHash.isNullOrBlank()) {
        Surface(
            color = Color(0xFFF1F5F9),
            shape = RoundedCornerShape(9.dp),
            modifier = Modifier.fillMaxWidth()
        ) {
            Text(
                text = "No pending final submission awaiting FR-027 approval. Capture the portal review step first.",
                fontSize = 11.sp,
                color = Color(0xFF475569),
                modifier = Modifier.padding(10.dp)
            )
        }
        return
    }

    val domain = state.portalVerification?.canonicalDomain
        ?: state.lastSnapshot?.url?.let { Uri.parse(it).host }
        ?: "verified-portal"
    Surface(
        color = Color(0xFFF4FBF7),
        shape = RoundedCornerShape(8.dp),
        border = BorderStroke(1.dp, Color(0xFFD9EEE2)),
        modifier = Modifier.fillMaxWidth()
    ) {
        Column(modifier = Modifier.padding(9.dp)) {
            Text("VERIFIED TARGET DOMAIN", fontSize = 8.sp, fontWeight = FontWeight.Bold, color = Color(0xFF7B9687))
            Spacer(Modifier.height(4.dp))
            Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
                Icon(Icons.Default.Verified, contentDescription = null, tint = Color(0xFF16845C), modifier = Modifier.size(15.dp))
                Spacer(Modifier.width(6.dp))
                Text(domain, fontSize = 12.sp, fontWeight = FontWeight.Bold, color = Color(0xFF16845C), modifier = Modifier.weight(1f))
                Surface(color = Color(0xFFE6F5EC), shape = RoundedCornerShape(4.dp)) {
                    Text("OFFICIAL", fontSize = 8.sp, fontWeight = FontWeight.Bold, color = Color(0xFF16845C), modifier = Modifier.padding(horizontal = 5.dp, vertical = 2.dp))
                }
            }
        }
    }

    if (state.approvalSummary.isNotEmpty()) {
        Surface(
            color = Color.White,
            shape = RoundedCornerShape(8.dp),
            border = BorderStroke(1.dp, Color(0xFFE7ECF1)),
            modifier = Modifier.fillMaxWidth()
        ) {
            Column {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .background(Color(0xFFF7F9FB))
                        .padding(horizontal = 9.dp, vertical = 6.dp),
                    horizontalArrangement = Arrangement.SpaceBetween
                ) {
                    Text("Mapped fields", fontSize = 10.sp, fontWeight = FontWeight.Bold, color = Color(0xFF55647B))
                    Text("${state.approvalSummary.size} fields", fontSize = 10.sp, color = Color(0xFF99A4B1))
                }
                state.approvalSummary.forEach { (k, v) ->
                    HorizontalDivider(color = Color(0xFFF0F2F5))
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(horizontal = 9.dp, vertical = 6.dp),
                        horizontalArrangement = Arrangement.SpaceBetween
                    ) {
                        Text(k, fontSize = 10.sp, color = Color(0xFF8793A3))
                        Text(v, fontSize = 11.sp, fontWeight = FontWeight.SemiBold, color = Color(0xFF46556B))
                    }
                }
            }
        }
    }

    Surface(
        color = Color(0xFFF7F9FB),
        shape = RoundedCornerShape(6.dp),
        border = BorderStroke(1.dp, Color(0xFFE8EDF2)),
        modifier = Modifier.fillMaxWidth()
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 8.dp, vertical = 7.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text("pending_action_hash", fontSize = 9.sp, fontFamily = FontFamily.Monospace, color = Color(0xFF8491A3))
            Spacer(Modifier.width(8.dp))
            Text(
                text = pendingHash,
                fontSize = 10.sp,
                fontFamily = FontFamily.Monospace,
                color = Color(0xFF3D4D64),
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f)
            )
            Icon(Icons.Default.Lock, contentDescription = null, tint = Color(0xFF8794A5), modifier = Modifier.size(12.dp))
        }
    }

    Surface(
        color = Color(0xFFFFF9EE),
        shape = RoundedCornerShape(6.dp),
        border = BorderStroke(1.dp, Color(0xFFF2E6CE)),
        modifier = Modifier.fillMaxWidth()
    ) {
        Row(modifier = Modifier.padding(8.dp), verticalAlignment = Alignment.CenterVertically) {
            Icon(Icons.Default.WarningAmber, contentDescription = null, tint = Color(0xFF99701F), modifier = Modifier.size(15.dp))
            Spacer(Modifier.width(6.dp))
            Text(
                text = state.approvalWarning ?: "This exact action will submit your application to the verified portal.",
                fontSize = 10.sp,
                color = Color(0xFF99701F)
            )
        }
    }

    OutlinedTextField(
        value = reviewerNote,
        onValueChange = { reviewerNote = it },
        label = { Text("Reviewer Note (optional)") },
        singleLine = true,
        modifier = Modifier.fillMaxWidth()
    )

    Button(
        onClick = { onApprove(reviewerNote) },
        colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF1D4ED8)),
        modifier = Modifier
            .fillMaxWidth()
            .height(40.dp)
    ) {
        Icon(Icons.Default.VerifiedUser, contentDescription = null, modifier = Modifier.size(16.dp))
        Spacer(Modifier.width(8.dp))
        Text("Approve & Submit", fontWeight = FontWeight.Bold, fontSize = 12.sp, modifier = Modifier.weight(1f))
        Icon(Icons.AutoMirrored.Filled.ArrowForward, contentDescription = null, modifier = Modifier.size(15.dp))
    }

    OutlinedButton(
        onClick = { onCancel(reviewerNote) },
        modifier = Modifier.fillMaxWidth()
    ) {
        Text("Cancel submission", fontSize = 11.sp, color = Color(0xFF7B8798))
    }
}

@Composable
private fun VisualGuideInlineCard(
    guide: com.codeverse.assistant.network.VisualGuideCard?,
    onClose: () -> Unit
) {
    Surface(
        color = Color.White,
        shape = RoundedCornerShape(12.dp),
        border = BorderStroke(1.dp, Color(0xFFD8E5FC)),
        shadowElevation = 4.dp,
        modifier = Modifier.fillMaxWidth()
    ) {
        Column(modifier = Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text("QUICK VISUAL GUIDE", fontSize = 9.sp, fontWeight = FontWeight.Bold, color = Color(0xFF8B97A8))
                IconButton(onClick = onClose, modifier = Modifier.size(22.dp)) {
                    Icon(Icons.Default.Close, contentDescription = "Close guide", modifier = Modifier.size(14.dp))
                }
            }
            Text(
                text = guide?.title ?: "Find IFSC on your passbook",
                fontSize = 14.sp,
                fontWeight = FontWeight.Bold,
                color = Color(0xFF314158)
            )
            Text(
                text = guide?.explanation ?: "Open the first page. Your IFSC is printed beside your branch details.",
                fontSize = 11.sp,
                color = Color(0xFF7E8B9D)
            )
            Surface(
                color = Color(0xFFF8F9FB),
                shape = RoundedCornerShape(8.dp),
                border = BorderStroke(1.dp, Color(0xFFE9EDF2)),
                modifier = Modifier.fillMaxWidth()
            ) {
                Column(modifier = Modifier.padding(10.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Text("◉ STATE BANK OF INDIA", fontSize = 10.sp, fontWeight = FontWeight.Bold, color = Color(0xFF917947))
                    Box(modifier = Modifier.width(120.dp).height(3.dp).background(Color(0xFFE2E6EB)))
                    Surface(color = Color(0xFFFFF0AA), shape = RoundedCornerShape(4.dp)) {
                        Text(
                            text = "IFSC: SBIN0004821",
                            fontSize = 10.sp,
                            fontFamily = FontFamily.Monospace,
                            color = Color(0xFF655333),
                            modifier = Modifier.padding(horizontal = 7.dp, vertical = 4.dp)
                        )
                    }
                }
            }
            Button(
                onClick = onClose,
                colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF1D4ED8)),
                modifier = Modifier.fillMaxWidth()
            ) {
                Text("Got it ✓", fontSize = 11.sp, fontWeight = FontWeight.Bold)
            }
        }
    }
}

@Composable
private fun VerifiedReceiptOverlayCard(
    receipt: CaptureReceiptOutput,
    portalUrl: String,
    onDone: () -> Unit
) {
    val domain = runCatching { Uri.parse(portalUrl).host }.getOrNull() ?: portalUrl
    Card(
        modifier = Modifier
            .widthIn(min = 320.dp, max = 368.dp)
            .padding(8.dp),
        shape = RoundedCornerShape(18.dp),
        colors = CardDefaults.cardColors(containerColor = Color(0xFFFCFEFF)),
        border = BorderStroke(1.dp, Color(0xFFE5EAF0)),
        elevation = CardDefaults.cardElevation(defaultElevation = 14.dp)
    ) {
        Column(
            modifier = Modifier.padding(16.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            Box(
                modifier = Modifier
                    .size(46.dp)
                    .clip(CircleShape)
                    .background(Color(0xFFE8F7EF)),
                contentAlignment = Alignment.Center
            ) {
                Icon(Icons.Default.Check, contentDescription = null, tint = Color(0xFF14885E), modifier = Modifier.size(24.dp))
            }
            Text("FR-061 · VERIFIED RECEIPT", fontSize = 9.sp, fontWeight = FontWeight.Bold, color = Color(0xFF16845C))
            Text("Application submitted.", fontSize = 18.sp, fontWeight = FontWeight.Bold, color = Color(0xFF26374D))
            Text(
                text = "The portal confirmed your submission.",
                fontSize = 11.sp,
                color = Color(0xFF7C899A),
                textAlign = TextAlign.Center
            )
            Surface(
                color = Color(0xFFF7F9FB),
                shape = RoundedCornerShape(7.dp),
                border = BorderStroke(1.dp, Color(0xFFE8EDF2)),
                modifier = Modifier.fillMaxWidth()
            ) {
                Column(modifier = Modifier.padding(10.dp)) {
                    Text("APPLICATION REFERENCE", fontSize = 9.sp, fontWeight = FontWeight.Bold, color = Color(0xFF8C98A7))
                    Text(
                        text = receipt.receiptNumber ?: "VERIFIED-SUBMISSION",
                        fontSize = 13.sp,
                        fontWeight = FontWeight.Bold,
                        fontFamily = FontFamily.Monospace,
                        color = Color(0xFF35455B)
                    )
                }
            }
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Icon(Icons.Default.Verified, contentDescription = null, tint = Color(0xFF16845C), modifier = Modifier.size(15.dp))
                Spacer(Modifier.width(6.dp))
                Text(domain, fontSize = 11.sp, fontWeight = FontWeight.Bold, color = Color(0xFF16845C), modifier = Modifier.weight(1f))
                Text("VERIFIED PORTAL", fontSize = 9.sp, color = Color(0xFF688B79))
            }
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Icon(Icons.Default.Schedule, contentDescription = null, tint = Color(0xFF8493A5), modifier = Modifier.size(13.dp))
                Spacer(Modifier.width(5.dp))
                Text(
                    text = "${receipt.submissionTimestamp ?: "Just now"} · Audit hash verified",
                    fontSize = 10.sp,
                    color = Color(0xFF8491A1)
                )
            }
            Button(
                onClick = onDone,
                colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF1D4ED8)),
                modifier = Modifier.fillMaxWidth()
            ) {
                Text("Done & Close Overlay ✓", fontSize = 12.sp, fontWeight = FontWeight.Bold)
            }
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(Icons.Default.Lock, contentDescription = null, tint = Color(0xFF16845C), modifier = Modifier.size(11.dp))
                Spacer(Modifier.width(4.dp))
                Text("Personal information is never included in the audit hash", fontSize = 9.sp, color = Color(0xFF8E9AA9))
            }
        }
    }
}
