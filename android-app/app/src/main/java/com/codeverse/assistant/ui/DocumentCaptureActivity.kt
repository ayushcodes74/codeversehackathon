package com.codeverse.assistant.ui

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.provider.OpenableColumns
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.FileProvider
import androidx.lifecycle.lifecycleScope
import com.codeverse.assistant.AssistantApplication
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File

/**
 * Lightweight translucent Activity invoked from FloatingBubbleService (or MainActivity)
 * to host ActivityResultContracts.GetContent and ActivityResultContracts.TakePicture,
 * uploading the selected/captured document to POST /documents/extract-and-validate.
 */
class DocumentCaptureActivity : ComponentActivity() {

    private var expectedDocType: String = "unknown"
    private var pendingCameraUri: Uri? = null

    private val pickContentLauncher = registerForActivityResult(
        ActivityResultContracts.GetContent()
    ) { uri: Uri? ->
        if (uri == null) {
            finish()
        } else {
            processPickedUri(uri)
        }
    }

    private val takePictureLauncher = registerForActivityResult(
        ActivityResultContracts.TakePicture()
    ) { success: Boolean ->
        val uri = pendingCameraUri
        if (!success || uri == null) {
            finish()
        } else {
            processPickedUri(uri)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        expectedDocType = intent.getStringExtra(EXTRA_DOC_TYPE) ?: "unknown"
        val mode = intent.getStringExtra(EXTRA_MODE) ?: MODE_PICK

        if (savedInstanceState == null) {
            if (mode == MODE_CAMERA) {
                launchCamera()
            } else {
                pickContentLauncher.launch("*/*")
            }
        }
    }

    private fun launchCamera() {
        val docsDir = File(cacheDir, "docs").apply { mkdirs() }
        val photoFile = File(docsDir, "doc_${System.currentTimeMillis()}.jpg")
        val uri = FileProvider.getUriForFile(
            this,
            "${packageName}.fileprovider",
            photoFile
        )
        pendingCameraUri = uri
        takePictureLauncher.launch(uri)
    }

    private fun processPickedUri(uri: Uri) {
        val app = application as AssistantApplication
        lifecycleScope.launch {
            val payload = withContext(Dispatchers.IO) {
                val bytes = contentResolver.openInputStream(uri)?.use { it.readBytes() }
                    ?: ByteArray(0)
                val mime = contentResolver.getType(uri) ?: "image/jpeg"
                val name = queryDisplayName(uri) ?: "document_${System.currentTimeMillis()}.jpg"
                Triple(bytes, name, mime)
            }
            if (payload.first.isNotEmpty()) {
                app.coordinator.uploadAndExtractDocument(
                    bytes = payload.first,
                    fileName = payload.second,
                    mimeType = payload.third,
                    expectedDocType = expectedDocType
                )
            }
            finish()
        }
    }

    private fun queryDisplayName(uri: Uri): String? {
        return contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)
            ?.use { cursor ->
                if (cursor.moveToFirst()) {
                    val idx = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                    if (idx >= 0) cursor.getString(idx) else null
                } else {
                    null
                }
            }
    }

    companion object {
        const val EXTRA_MODE = "extra_mode"
        const val EXTRA_DOC_TYPE = "extra_doc_type"
        const val MODE_PICK = "pick"
        const val MODE_CAMERA = "camera"

        fun createIntent(context: Context, mode: String, expectedDocType: String): Intent {
            return Intent(context, DocumentCaptureActivity::class.java).apply {
                putExtra(EXTRA_MODE, mode)
                putExtra(EXTRA_DOC_TYPE, expectedDocType)
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }
        }
    }
}
