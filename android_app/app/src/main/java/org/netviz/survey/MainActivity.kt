package org.netviz.survey

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch
import org.netviz.survey.data.CrashLogger
import org.netviz.survey.state.SurveyViewModel
import org.netviz.survey.ui.SurveyScreen
import org.netviz.survey.ui.theme.NetVizSurveyTheme

class MainActivity : ComponentActivity() {
    private val viewModel: SurveyViewModel by viewModels()
    private var pendingCameraAction: (() -> Unit)? = null

    private val permissionsLauncher = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { _ ->
        // Foreground permissions resolved; hardware engines check permission at start time.
    }

    private val cameraPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        val action = pendingCameraAction
        pendingCameraAction = null
        if (granted) {
            action?.invoke()
        }
    }

    private val openNetvizLauncher = registerForActivityResult(
        ActivityResultContracts.OpenDocument()
    ) { uri ->
        if (uri != null) {
            viewModel.importFromSafUri(uri)
        }
    }

    private val createSurveyLauncher = registerForActivityResult(
        ActivityResultContracts.CreateDocument("application/json")
    ) { uri ->
        if (uri != null) {
            viewModel.exportToSafUri(uri)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        CrashLogger.install(applicationContext)
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        requestSurveyPermissionsIfNeeded()

        setContent {
            val lifecycleOwner = LocalLifecycleOwner.current
            DisposableEffect(lifecycleOwner) {
                val observer = LifecycleEventObserver { _, event ->
                    when (event) {
                        Lifecycle.Event.ON_RESUME -> viewModel.onAppForegrounded()
                        Lifecycle.Event.ON_PAUSE,
                        Lifecycle.Event.ON_STOP -> viewModel.onAppBackgrounded()
                        else -> Unit
                    }
                }
                lifecycleOwner.lifecycle.addObserver(observer)
                onDispose {
                    lifecycleOwner.lifecycle.removeObserver(observer)
                }
            }

            val uiState by viewModel.uiState.collectAsStateWithLifecycle()
            NetVizSurveyTheme {
                SurveyScreen(
                    state = uiState,
                    viewModel = viewModel,
                    onLaunchSafImport = {
                        openNetvizLauncher.launch(arrayOf("*/*"))
                    },
                    onLaunchSafExport = {
                        val safeName = uiState.session.name
                            .replace(Regex("[^A-Za-z0-9._-]+"), "_")
                            .trim('_')
                            .ifEmpty { "survey" }
                        createSurveyLauncher.launch("$safeName.netviz-survey.json")
                    },
                    onShareSurvey = { launchShareSheet() },
                    onRequestCameraPermission = { onGranted ->
                        if (ContextCompat.checkSelfPermission(
                                this,
                                Manifest.permission.CAMERA
                            ) == PackageManager.PERMISSION_GRANTED
                        ) {
                            onGranted()
                        } else {
                            pendingCameraAction = onGranted
                            cameraPermissionLauncher.launch(Manifest.permission.CAMERA)
                        }
                    }
                )
            }
        }
    }

    private fun launchShareSheet() {
        lifecycleScope.launch {
            viewModel.exportForShareSheet().onSuccess { file ->
                val uri = FileProvider.getUriForFile(
                    this@MainActivity,
                    "${packageName}.fileprovider",
                    file
                )
                val shareIntent = Intent(Intent.ACTION_SEND).apply {
                    type = "application/json"
                    putExtra(Intent.EXTRA_STREAM, uri)
                    addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                }
                startActivity(Intent.createChooser(shareIntent, "Share NetViz Survey"))
            }
        }
    }

    private fun requestSurveyPermissionsIfNeeded() {
        val needed = buildList {
            add(Manifest.permission.ACCESS_FINE_LOCATION)
            add(Manifest.permission.ACCESS_COARSE_LOCATION)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                add(Manifest.permission.NEARBY_WIFI_DEVICES)
            }
        }.filter {
            ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED
        }
        if (needed.isNotEmpty()) {
            permissionsLauncher.launch(needed.toTypedArray())
        }
    }
}
