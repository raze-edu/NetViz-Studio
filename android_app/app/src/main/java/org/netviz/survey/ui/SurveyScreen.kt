package org.netviz.survey.ui

import android.content.Intent
import android.provider.Settings
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
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
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material.icons.filled.Download
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material.icons.filled.Upload
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Snackbar
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import org.netviz.survey.state.ScanMode
import org.netviz.survey.state.SurveyUiState
import org.netviz.survey.state.SurveyViewModel
import org.netviz.survey.ui.theme.NetVizColors
import kotlin.math.hypot

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SurveyScreen(
    state: SurveyUiState,
    viewModel: SurveyViewModel,
    onLaunchSafImport: () -> Unit,
    onLaunchSafExport: () -> Unit,
    onShareSurvey: () -> Unit,
    onRequestCameraPermission: (onGranted: () -> Unit) -> Unit
) {
    val context = LocalContext.current
    val clipboard = LocalClipboardManager.current

    var showImportSheet by remember { mutableStateOf(false) }
    var showExportSheet by remember { mutableStateOf(false) }
    var showQrScanner by remember { mutableStateOf(false) }
    var showGpsDiagnostics by remember { mutableStateOf(false) }
    var showOverflowMenu by remember { mutableStateOf(false) }
    var showBuildingMenu by remember { mutableStateOf(false) }
    var showFloorMenu by remember { mutableStateOf(false) }
    var showIntervalMenu by remember { mutableStateOf(false) }

    Scaffold(
        containerColor = NetVizColors.Background,
        topBar = {
            TopAppBar(
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = NetVizColors.Surface,
                    titleContentColor = NetVizColors.TextPrimary
                ),
                title = {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(4.dp)
                    ) {
                        Box {
                            TextButton(
                                onClick = { showBuildingMenu = true },
                                contentPadding = PaddingValues(horizontal = 6.dp, vertical = 2.dp)
                            ) {
                                Text(
                                    text = state.activeBuilding?.name ?: "Building",
                                    maxLines = 1,
                                    overflow = TextOverflow.Ellipsis,
                                    fontSize = 13.sp
                                )
                                Icon(Icons.Default.ArrowDropDown, contentDescription = null, modifier = Modifier.size(16.dp))
                            }
                            DropdownMenu(
                                expanded = showBuildingMenu,
                                onDismissRequest = { showBuildingMenu = false }
                            ) {
                                state.project.buildings.forEach { bldg ->
                                    DropdownMenuItem(
                                        text = { Text(bldg.name) },
                                        onClick = {
                                            showBuildingMenu = false
                                            viewModel.selectBuilding(bldg.id)
                                        }
                                    )
                                }
                            }
                        }

                        Box {
                            TextButton(
                                onClick = { showFloorMenu = true },
                                contentPadding = PaddingValues(horizontal = 6.dp, vertical = 2.dp)
                            ) {
                                Text(
                                    text = state.activeFloor?.name ?: "Floor",
                                    maxLines = 1,
                                    overflow = TextOverflow.Ellipsis,
                                    fontSize = 13.sp
                                )
                                Icon(Icons.Default.ArrowDropDown, contentDescription = null, modifier = Modifier.size(16.dp))
                            }
                            DropdownMenu(
                                expanded = showFloorMenu,
                                onDismissRequest = { showFloorMenu = false }
                            ) {
                                state.activeBuilding?.floors.orEmpty().forEach { fl ->
                                    DropdownMenuItem(
                                        text = { Text(fl.name) },
                                        onClick = {
                                            showFloorMenu = false
                                            viewModel.selectFloor(fl.id)
                                        }
                                    )
                                }
                            }
                        }
                    }
                },
                actions = {
                    IconButton(onClick = { showImportSheet = true }) {
                        Icon(Icons.Default.Download, contentDescription = "Import")
                    }
                    IconButton(onClick = { showExportSheet = true }) {
                        Icon(Icons.Default.Upload, contentDescription = "Export")
                    }
                    Box {
                        IconButton(onClick = { showOverflowMenu = true }) {
                            Icon(Icons.Default.MoreVert, contentDescription = "More")
                        }
                        DropdownMenu(
                            expanded = showOverflowMenu,
                            onDismissRequest = { showOverflowMenu = false }
                        ) {
                            DropdownMenuItem(
                                text = {
                                    Text(if (state.isDemoWalking) "Stop Demo Walk Simulator" else "Demo Walk Simulator")
                                },
                                onClick = {
                                    showOverflowMenu = false
                                    if (state.isDemoWalking) {
                                        viewModel.stopDemoWalk()
                                    } else {
                                        viewModel.startDemoWalkSimulator()
                                    }
                                }
                            )
                            DropdownMenuItem(
                                text = { Text("GPS Diagnostics") },
                                onClick = {
                                    showOverflowMenu = false
                                    showGpsDiagnostics = true
                                }
                            )
                            DropdownMenuItem(
                                text = { Text("Load Demo Campus") },
                                onClick = {
                                    showOverflowMenu = false
                                    viewModel.loadDemoProject()
                                }
                            )
                            DropdownMenuItem(
                                text = { Text("Clear Draft", color = NetVizColors.Error) },
                                onClick = {
                                    showOverflowMenu = false
                                    viewModel.clearDraftAndSamples()
                                }
                            )
                        }
                    }
                }
            )
        }
    ) { innerPadding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(innerPadding)
        ) {
            // Live Location & Signal HUD Card
            HudTelemetryCard(
                state = state,
                onOpenGpsSheet = { showGpsDiagnostics = true },
                onCenterGps = { viewModel.requestCenterOnGps() }
            )

            // Android Wi-Fi Scan Throttling Amber Banner
            if (state.isScanThrottled) {
                Surface(
                    color = NetVizColors.Warning.copy(alpha = 0.2f),
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(horizontal = 12.dp, vertical = 6.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.SpaceBetween
                    ) {
                        Text(
                            text = "Android Wi-Fi scan throttling detected (>15s). Disable in Developer Options.",
                            color = NetVizColors.Warning,
                            fontSize = 12.sp,
                            modifier = Modifier.weight(1f)
                        )
                        TextButton(
                            onClick = {
                                runCatching {
                                    context.startActivity(
                                        Intent(Settings.ACTION_APPLICATION_DEVELOPMENT_SETTINGS)
                                            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                                    )
                                }
                            }
                        ) {
                            Text("Dev Options", color = NetVizColors.Warning, fontSize = 12.sp)
                        }
                    }
                }
            }

            // Status Banner
            if (!state.statusBanner.isNullOrBlank()) {
                Surface(
                    color = NetVizColors.SurfaceVariant,
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(horizontal = 12.dp, vertical = 4.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.SpaceBetween
                    ) {
                        Text(
                            text = state.statusBanner,
                            color = NetVizColors.Primary,
                            fontSize = 12.sp,
                            modifier = Modifier.weight(1f)
                        )
                        TextButton(onClick = { viewModel.dismissStatusBanner() }) {
                            Text("OK", fontSize = 12.sp)
                        }
                    }
                }
            }

            // Main Interactive 60fps Floor Plan Canvas
            Box(modifier = Modifier.weight(1f).fillMaxWidth()) {
                FloorPlanCanvas(
                    building = state.activeBuilding,
                    floor = state.activeFloor,
                    components = state.project.components,
                    samples = state.activeFloorSamples,
                    renderedHeatmap = state.renderedHeatmap,
                    showHeatmap = state.showHeatmap,
                    showMapBackdrop = state.showMapBackdrop,
                    hasTilesDb = state.hasTilesDb,
                    selectedTapPtM = state.selectedTapPtM,
                    liveLocalPosM = state.liveLocalPosM,
                    liveAccuracyM = state.liveRawGpsFix?.accuracyM,
                    trueNorthAzimuthDeg = state.trueNorthAzimuthDeg,
                    centerOnGpsNonce = state.centerOnGpsNonce,
                    resetViewNonce = state.resetViewNonce,
                    readTile = viewModel::readTile,
                    onTapPointM = { ptM -> viewModel.onCanvasTap(ptM) },
                    onLongPressSample = { sample -> viewModel.inspectSample(sample) }
                )

                val suggested = state.autoSwitchBuildingSuggestion
                if (suggested != null) {
                    Snackbar(
                        modifier = Modifier
                            .align(Alignment.BottomCenter)
                            .padding(12.dp),
                        action = {
                            Row {
                                TextButton(onClick = { viewModel.acceptBuildingAutoSwitch() }) {
                                    Text("Switch", color = NetVizColors.Primary)
                                }
                                TextButton(onClick = { viewModel.dismissBuildingAutoSwitch() }) {
                                    Text("Dismiss")
                                }
                            }
                        }
                    ) {
                        Text("You appear to be inside ${suggested.name}")
                    }
                }
            }

            // Bottom Scan Control Bar
            Surface(
                color = NetVizColors.Surface,
                tonalElevation = 4.dp,
                modifier = Modifier.fillMaxWidth()
            ) {
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 10.dp, vertical = 8.dp),
                    verticalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        SingleChoiceSegmentedButtonRow(modifier = Modifier.weight(1f)) {
                            SegmentedButton(
                                selected = state.scanMode == ScanMode.MANUAL_TAP,
                                onClick = { viewModel.setScanMode(ScanMode.MANUAL_TAP) },
                                shape = SegmentedButtonDefaults.itemShape(0, 2)
                            ) {
                                Text("Manual Tap", fontSize = 12.sp, maxLines = 1)
                            }
                            SegmentedButton(
                                selected = state.scanMode == ScanMode.GPS_WALK,
                                onClick = { viewModel.setScanMode(ScanMode.GPS_WALK) },
                                shape = SegmentedButtonDefaults.itemShape(1, 2)
                            ) {
                                Text("GPS Walk", fontSize = 12.sp, maxLines = 1)
                            }
                        }

                        Button(
                            onClick = { viewModel.toggleScanning() },
                            colors = ButtonDefaults.buttonColors(
                                containerColor = if (state.isScanning) NetVizColors.Error else NetVizColors.Secondary
                            ),
                            shape = RoundedCornerShape(10.dp),
                            contentPadding = PaddingValues(horizontal = 14.dp, vertical = 8.dp)
                        ) {
                            Icon(
                                imageVector = if (state.isScanning) Icons.Default.Stop else Icons.Default.PlayArrow,
                                contentDescription = null,
                                modifier = Modifier.size(18.dp)
                            )
                            Spacer(Modifier.width(4.dp))
                            Text(
                                text = if (state.isScanning) "STOP SCAN" else "START SCAN",
                                fontWeight = FontWeight.Bold,
                                fontSize = 12.sp
                            )
                        }
                    }

                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .horizontalScroll(rememberScrollState()),
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        OutlinedButton(
                            onClick = { viewModel.captureThreeXBurst() },
                            enabled = !state.isBursting,
                            contentPadding = PaddingValues(horizontal = 10.dp, vertical = 4.dp)
                        ) {
                            Text(if (state.isBursting) "Bursting..." else "3× Burst", fontSize = 12.sp)
                        }
                        OutlinedButton(
                            onClick = { viewModel.undoLastSample() },
                            enabled = state.session.samples.isNotEmpty(),
                            contentPadding = PaddingValues(horizontal = 10.dp, vertical = 4.dp)
                        ) {
                            Text("Undo", fontSize = 12.sp)
                        }
                        FilterChip(
                            selected = state.showHeatmap,
                            onClick = { viewModel.toggleHeatmap() },
                            label = { Text("Heatmap", fontSize = 12.sp) }
                        )
                        FilterChip(
                            selected = state.showMapBackdrop,
                            onClick = { viewModel.toggleMapBackdrop() },
                            label = { Text("Map BG", fontSize = 12.sp) }
                        )
                        OutlinedButton(
                            onClick = { viewModel.requestCenterOnGps() },
                            contentPadding = PaddingValues(horizontal = 10.dp, vertical = 4.dp)
                        ) {
                            Text("Center GPS", fontSize = 12.sp)
                        }
                        FilterChip(
                            selected = state.isCalibratingTap || state.activeCalibrationOffset != null,
                            onClick = { viewModel.startCalibrationTap() },
                            label = {
                                val cal = state.activeCalibrationOffset
                                val label = if (cal != null) {
                                    "Calibrated (${"%.1f".format(hypot(cal.x, cal.y))}m)"
                                } else {
                                    "Calibrate"
                                }
                                Text(label, fontSize = 12.sp)
                            }
                        )
                        Box {
                            OutlinedButton(
                                onClick = { showIntervalMenu = true },
                                contentPadding = PaddingValues(horizontal = 10.dp, vertical = 4.dp)
                            ) {
                                Text("${state.scanIntervalMs / 1000.0}s", fontSize = 12.sp)
                            }
                            DropdownMenu(
                                expanded = showIntervalMenu,
                                onDismissRequest = { showIntervalMenu = false }
                            ) {
                                listOf(2000L to "2.0s", 2500L to "2.5s", 3000L to "3.0s", 5000L to "5.0s")
                                    .forEach { (ms, label) ->
                                        DropdownMenuItem(
                                            text = { Text(label) },
                                            onClick = {
                                                showIntervalMenu = false
                                                viewModel.setScanIntervalMs(ms)
                                            }
                                        )
                                    }
                            }
                        }
                    }
                }
            }
        }
    }

    if (showImportSheet) {
        ImportSyncDialog(
            state = state,
            viewModel = viewModel,
            onOpenQrScanner = {
                showImportSheet = false
                onRequestCameraPermission { showQrScanner = true }
            },
            onOpenSaf = {
                showImportSheet = false
                onLaunchSafImport()
            },
            onDismiss = { showImportSheet = false }
        )
    }

    if (showExportSheet) {
        ExportSyncDialog(
            state = state,
            viewModel = viewModel,
            onSaveSaf = {
                showExportSheet = false
                onLaunchSafExport()
            },
            onShareSheet = {
                showExportSheet = false
                onShareSurvey()
            },
            onDismiss = { showExportSheet = false }
        )
    }

    if (showQrScanner) {
        QrScannerSheet(
            onQrDetected = { rawQr ->
                showQrScanner = false
                viewModel.importFromQrString(rawQr)
            },
            onDismiss = { showQrScanner = false }
        )
    }

    if (showGpsDiagnostics) {
        GpsDiagnosticsSheet(
            state = state,
            onCalibrateHere = {
                showGpsDiagnostics = false
                viewModel.startCalibrationTap()
            },
            onResetCalibration = { viewModel.resetCalibration() },
            onToggleAccuracyGate = { enabled ->
                viewModel.setMaxAccuracyGate(state.maxAccuracyGateM, enabled)
            },
            onDismiss = { showGpsDiagnostics = false }
        )
    }

    state.inspectedSample?.let { sample ->
        AlertDialog(
            onDismissRequest = { viewModel.inspectSample(null) },
            title = { Text("Sample @ (${"%.1f".format(sample.posM.x)}m, ${"%.1f".format(sample.posM.y)}m)") },
            text = {
                Column(modifier = Modifier.verticalScroll(rememberScrollState())) {
                    Text("Source: ${sample.source.wireValue}", fontSize = 12.sp, color = NetVizColors.TextMuted)
                    Spacer(Modifier.height(6.dp))
                    sample.readings.take(12).forEach { r ->
                        val label = r.ssid.ifBlank { "(Hidden)" }
                        Text("$label · ${r.bssid} · ${r.rssiDbm} dBm (${r.frequencyMhz} MHz)", fontSize = 12.sp)
                    }
                }
            },
            confirmButton = {
                TextButton(onClick = { viewModel.deleteSample(sample.id) }) {
                    Text("Delete Sample", color = NetVizColors.Error)
                }
            },
            dismissButton = {
                TextButton(onClick = { viewModel.inspectSample(null) }) {
                    Text("Close")
                }
            }
        )
    }

    state.lastCrashReport?.let { crashText ->
        AlertDialog(
            onDismissRequest = { viewModel.dismissCrashReport() },
            title = { Text("Previous Crash Report") },
            text = {
                Box(
                    modifier = Modifier
                        .height(220.dp)
                        .verticalScroll(rememberScrollState())
                ) {
                    Text(crashText, fontSize = 11.sp)
                }
            },
            confirmButton = {
                TextButton(
                    onClick = {
                        clipboard.setText(AnnotatedString(crashText))
                        viewModel.dismissCrashReport()
                    }
                ) {
                    Text("Copy & Dismiss")
                }
            },
            dismissButton = {
                TextButton(onClick = { viewModel.dismissCrashReport() }) {
                    Text("Dismiss")
                }
            }
        )
    }
}

@Composable
private fun HudTelemetryCard(
    state: SurveyUiState,
    onOpenGpsSheet: () -> Unit,
    onCenterGps: () -> Unit
) {
    Card(
        colors = CardDefaults.cardColors(containerColor = NetVizColors.SurfaceVariant),
        shape = RoundedCornerShape(0.dp),
        modifier = Modifier
            .fillMaxWidth()
            .clickable { onOpenGpsSheet() }
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 12.dp, vertical = 8.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp)
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                val locationLine = when {
                    state.scanMode == ScanMode.MANUAL_TAP && state.liveLocalPosM == null ->
                        "Manual Tap · Tap floor to capture"
                    state.liveLocalPosM != null && state.liveInsideFloor -> {
                        val acc = state.liveRawGpsFix?.accuracyM?.let { "±${"%.1f".format(it)}m" } ?: "Sim"
                        val xy = "X ${"%.1f".format(state.liveLocalPosM.x)}m, Y ${"%.1f".format(state.liveLocalPosM.y)}m"
                        val room = state.liveRoomName ?: "Open Area"
                        "GPS $acc · $xy · Room: $room"
                    }
                    state.liveLocalPosM != null -> {
                        val acc = state.liveRawGpsFix?.accuracyM?.let { "±${"%.1f".format(it)}m" } ?: ""
                        "GPS $acc · ${state.liveBearingText ?: "Outside Floor"}"
                    }
                    else -> "GPS ${state.activeProviderLabel} · Waiting for fix..."
                }
                Text(
                    text = locationLine,
                    color = NetVizColors.TextPrimary,
                    fontSize = 12.sp,
                    fontWeight = FontWeight.Medium,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f)
                )
                if (state.liveLocalPosM != null && !state.liveInsideFloor) {
                    Text(
                        text = "[Center]",
                        color = NetVizColors.Primary,
                        fontSize = 12.sp,
                        fontWeight = FontWeight.Bold,
                        modifier = Modifier
                            .padding(start = 8.dp)
                            .clickable { onCenterGps() }
                    )
                }
            }

            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                val ssidLabel = state.strongestMatchedSsid ?: "No Signal"
                val rssi = state.strongestMatchedRssi
                val progress = if (rssi != null) ((rssi + 95f) / 65f).coerceIn(0f, 1f) else 0f
                val covPct = state.renderedHeatmap?.grid?.coveragePercent ?: 0.0

                Text(
                    text = if (rssi != null) "$ssidLabel: $rssi dBm" else "Signal: -- dBm",
                    color = NetVizColors.Primary,
                    fontSize = 12.sp,
                    fontWeight = FontWeight.SemiBold
                )
                LinearProgressIndicator(
                    progress = { progress },
                    color = if ((rssi ?: -100) >= -70) NetVizColors.Secondary else NetVizColors.Warning,
                    trackColor = NetVizColors.Background,
                    modifier = Modifier
                        .weight(1f)
                        .height(6.dp)
                )
                Text(
                    text = "Samples: ${state.activeFloorSamples.size} · ${"%.0f".format(covPct)}%",
                    color = NetVizColors.TextMuted,
                    fontSize = 11.sp
                )
            }
        }
    }
}
