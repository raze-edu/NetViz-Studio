package org.netviz.survey.state

import android.app.Application
import android.net.Uri
import android.os.Build
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.netviz.survey.data.BundleRepository
import org.netviz.survey.data.CrashLogger
import org.netviz.survey.data.LanSyncClient
import org.netviz.survey.data.LoadedProjectBundle
import org.netviz.survey.data.QrPairingPayload
import org.netviz.survey.data.UdpDiscoveryReply
import org.netviz.survey.geo.GeoMath
import org.netviz.survey.geo.HeatmapEngine
import org.netviz.survey.hardware.CalibrationMath
import org.netviz.survey.hardware.DemoWalkSimulator
import org.netviz.survey.hardware.GpsFix
import org.netviz.survey.hardware.LocationCompassEngine
import org.netviz.survey.hardware.WifiScannerEngine
import org.netviz.survey.model.Building
import org.netviz.survey.model.Floor
import org.netviz.survey.model.Point2D
import org.netviz.survey.model.PositionSource
import org.netviz.survey.model.Project
import org.netviz.survey.model.SurveySample
import org.netviz.survey.model.SurveySession
import org.netviz.survey.model.WifiReading
import org.netviz.survey.model.newId
import org.netviz.survey.model.normalizeMac
import java.io.File
import java.time.Instant
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import kotlin.math.hypot

enum class ScanMode {
    MANUAL_TAP,
    GPS_WALK
}

data class TransferProgressState(
    val active: Boolean = false,
    val label: String = "",
    val bytesTransferred: Long = 0L,
    val totalBytes: Long = -1L
)

data class SurveyUiState(
    val project: Project = Project(),
    val activeBuildingId: String? = null,
    val activeFloorId: String? = null,
    val session: SurveySession = SurveySession(
        name = "Field Survey",
        createdUtc = Instant.now().toString(),
        deviceModel = "${Build.MANUFACTURER} ${Build.MODEL}".trim()
    ),
    val sourceLabel: String = "Loading...",
    val scanMode: ScanMode = ScanMode.MANUAL_TAP,
    val isScanning: Boolean = false,
    val isBursting: Boolean = false,
    val isDemoWalking: Boolean = false,
    val scanIntervalMs: Long = WifiScannerEngine.DEFAULT_SCAN_INTERVAL_MS,
    val maxAccuracyGateM: Float = 12.0f,
    val enforceAccuracyGate: Boolean = true,
    val isCalibratingTap: Boolean = false,
    val calibrationOffsetsByFloor: Map<String, Point2D> = emptyMap(),
    val selectedTapPtM: Point2D? = null,
    val liveRawGpsFix: GpsFix? = null,
    val liveRawProjectedPtM: Point2D? = null,
    val liveLocalPosM: Point2D? = null,
    val liveInsideFloor: Boolean = false,
    val liveRoomName: String? = null,
    val liveBearingText: String? = null,
    val trueNorthAzimuthDeg: Float? = null,
    val activeProviderLabel: String = "Idle",
    val autoSwitchBuildingSuggestion: Building? = null,
    val latestReadings: List<WifiReading> = emptyList(),
    val isScanThrottled: Boolean = false,
    val strongestMatchedSsid: String? = null,
    val strongestMatchedRssi: Int? = null,
    val showHeatmap: Boolean = true,
    val showMapBackdrop: Boolean = false,
    val hasTilesDb: Boolean = false,
    val renderedHeatmap: HeatmapEngine.RenderedHeatmap? = null,
    val deadZoneDbm: Double = -70.0,
    val statusBanner: String? = null,
    val lastCrashReport: String? = null,
    val isForeground: Boolean = false,
    val centerOnGpsNonce: Int = 0,
    val resetViewNonce: Int = 0,
    val inspectedSample: SurveySample? = null,
    val transferProgress: TransferProgressState = TransferProgressState(),
    val lastSyncHost: String = "192.168.1.42",
    val lastSyncPort: Int = 8765,
    val lastSyncToken: String = "",
    val discoveredHosts: List<UdpDiscoveryReply> = emptyList(),
    val syncDiagnosticsText: String? = null
) {
    val activeBuilding: Building?
        get() = activeBuildingId?.let { project.getBuilding(it) } ?: project.buildings.firstOrNull()

    val activeFloor: Floor?
        get() {
            val b = activeBuilding ?: return null
            return activeFloorId?.let { fid -> b.floors.firstOrNull { it.id == fid } }
                ?: b.floors.firstOrNull()
        }

    val activeFloorSamples: List<SurveySample>
        get() {
            val fid = activeFloor?.id ?: return emptyList()
            return session.samples.filter { it.floorId == fid }
        }

    val activeCalibrationOffset: Point2D?
        get() = activeFloor?.id?.let { calibrationOffsetsByFloor[it] }
}

class SurveyViewModel(application: Application) : AndroidViewModel(application) {
    val bundleRepository = BundleRepository(application)
    val lanSyncClient = LanSyncClient(application)
    val wifiScanner = WifiScannerEngine(application)
    val locationCompass = LocationCompassEngine(application)
    private val demoSimulator = DemoWalkSimulator()

    private val _uiState = MutableStateFlow(
        SurveyUiState(lastCrashReport = CrashLogger.readLastCrash(application))
    )
    val uiState: StateFlow<SurveyUiState> = _uiState.asStateFlow()

    private var gpsWalkJob: Job? = null
    private var demoWalkJob: Job? = null
    private var transferJob: Job? = null
    private var heatmapJob: Job? = null
    private var lastGpsRecordedPtM: Point2D? = null
    private var lastGpsRecordedWallMs: Long = 0L

    init {
        observeHardwareFlows()
        loadStartupData()
    }

    private fun observeHardwareFlows() {
        viewModelScope.launch {
            wifiScanner.latestReadings.collect { readings ->
                val state = _uiState.value
                val targetSsids = state.project.getTargetSsids()
                val matched = pickStrongestMatchedReading(readings, targetSsids)
                _uiState.update {
                    it.copy(
                        latestReadings = readings,
                        strongestMatchedSsid = matched?.ssid?.ifBlank { "(Hidden)" },
                        strongestMatchedRssi = matched?.rssiDbm
                    )
                }
            }
        }

        viewModelScope.launch {
            wifiScanner.isScanThrottled.collect { throttled ->
                _uiState.update { it.copy(isScanThrottled = throttled) }
            }
        }

        viewModelScope.launch {
            locationCompass.currentFix.collect { fix ->
                updateLiveGpsState(fix)
            }
        }

        viewModelScope.launch {
            locationCompass.trueNorthAzimuthDeg.collect { az ->
                _uiState.update { it.copy(trueNorthAzimuthDeg = az) }
            }
        }

        viewModelScope.launch {
            locationCompass.activeProviderLabel.collect { label ->
                _uiState.update { it.copy(activeProviderLabel = label) }
            }
        }
    }

    private fun loadStartupData() {
        viewModelScope.launch {
            val demo = runCatching { bundleRepository.loadDemoProject() }.getOrNull()
            val draft = bundleRepository.loadDraftSession()
            if (demo != null) {
                applyLoadedBundle(demo, preserveSession = draft)
            } else if (draft != null) {
                _uiState.update { it.copy(session = draft, sourceLabel = "Restored Draft") }
                recomputeHeatmap()
            }
        }
    }

    fun onAppForegrounded() {
        wifiScanner.setForeground(true)
        _uiState.update { it.copy(isForeground = true) }
    }

    fun onAppBackgrounded() {
        wifiScanner.setForeground(false)
        stopScanning()
        stopDemoWalk()
        locationCompass.stop()
        _uiState.update { it.copy(isForeground = false) }
    }

    fun dismissCrashReport() {
        CrashLogger.clearLastCrash(getApplication())
        _uiState.update { it.copy(lastCrashReport = null) }
    }

    fun dismissStatusBanner() {
        _uiState.update { it.copy(statusBanner = null) }
    }

    fun selectBuilding(buildingId: String) {
        val b = _uiState.value.project.getBuilding(buildingId) ?: return
        val firstFloor = b.floors.firstOrNull()
        _uiState.update {
            it.copy(
                activeBuildingId = b.id,
                activeFloorId = firstFloor?.id,
                selectedTapPtM = null,
                isCalibratingTap = false,
                autoSwitchBuildingSuggestion = null,
                resetViewNonce = it.resetViewNonce + 1
            )
        }
        updateLiveGpsState(_uiState.value.liveRawGpsFix)
        recomputeHeatmap()
    }

    fun selectFloor(floorId: String) {
        val b = _uiState.value.activeBuilding ?: return
        val f = b.floors.firstOrNull { it.id == floorId } ?: return
        _uiState.update {
            it.copy(
                activeFloorId = f.id,
                selectedTapPtM = null,
                isCalibratingTap = false,
                resetViewNonce = it.resetViewNonce + 1
            )
        }
        updateLiveGpsState(_uiState.value.liveRawGpsFix)
        recomputeHeatmap()
    }

    fun setScanMode(mode: ScanMode) {
        _uiState.update {
            it.copy(
                scanMode = mode,
                isCalibratingTap = false
            )
        }
        if (mode == ScanMode.GPS_WALK && _uiState.value.isForeground) {
            locationCompass.start(isForeground = true)
        }
    }

    fun setScanIntervalMs(intervalMs: Long) {
        _uiState.update { it.copy(scanIntervalMs = intervalMs) }
        if (_uiState.value.isScanning) {
            wifiScanner.startScanning(viewModelScope, intervalMs)
        }
    }

    fun setMaxAccuracyGate(maxMeters: Float, enforce: Boolean) {
        _uiState.update {
            it.copy(maxAccuracyGateM = maxMeters, enforceAccuracyGate = enforce)
        }
    }

    fun toggleHeatmap() {
        _uiState.update { it.copy(showHeatmap = !it.showHeatmap) }
        if (_uiState.value.showHeatmap) {
            recomputeHeatmap()
        }
    }

    fun toggleMapBackdrop() {
        val state = _uiState.value
        if (!state.hasTilesDb && !state.showMapBackdrop) {
            _uiState.update {
                it.copy(statusBanner = "No offline tiles.sqlite in current project. Prefetch tiles on Desktop first.")
            }
            return
        }
        _uiState.update { it.copy(showMapBackdrop = !it.showMapBackdrop) }
    }

    fun requestCenterOnGps() {
        if (_uiState.value.isForeground) {
            locationCompass.start(isForeground = true)
        }
        _uiState.update { it.copy(centerOnGpsNonce = it.centerOnGpsNonce + 1) }
    }

    fun requestResetView() {
        _uiState.update { it.copy(resetViewNonce = it.resetViewNonce + 1) }
    }

    fun startCalibrationTap() {
        if (_uiState.value.isForeground) {
            locationCompass.start(isForeground = true)
        }
        val state = _uiState.value
        if (state.liveRawProjectedPtM == null) {
            _uiState.update {
                it.copy(statusBanner = "Waiting for GPS lock before calibrating. Ensure Location permission & GPS are active.")
            }
            return
        }
        _uiState.update {
            it.copy(
                isCalibratingTap = true,
                statusBanner = "Tap your exact standing spot on the floor plan to calibrate GPS."
            )
        }
    }

    fun resetCalibration() {
        val floorId = _uiState.value.activeFloor?.id ?: return
        _uiState.update {
            it.copy(
                calibrationOffsetsByFloor = it.calibrationOffsetsByFloor - floorId,
                isCalibratingTap = false,
                statusBanner = "Indoor GPS calibration reset."
            )
        }
        updateLiveGpsState(_uiState.value.liveRawGpsFix)
    }

    fun startScanning() {
        val state = _uiState.value
        if (!state.isForeground) return
        stopDemoWalk()
        wifiScanner.startScanning(viewModelScope, state.scanIntervalMs)
        locationCompass.start(isForeground = true)
        _uiState.update { it.copy(isScanning = true) }

        gpsWalkJob?.cancel()
        gpsWalkJob = viewModelScope.launch {
            while (isActive && _uiState.value.isScanning && _uiState.value.isForeground) {
                delay(1500L)
                val cur = _uiState.value
                if (cur.scanMode == ScanMode.GPS_WALK) {
                    tryCaptureGpsWalkSample(cur)
                }
            }
        }
    }

    fun stopScanning() {
        gpsWalkJob?.cancel()
        gpsWalkJob = null
        wifiScanner.stopScanning()
        _uiState.update { it.copy(isScanning = false) }
    }

    fun toggleScanning() {
        if (_uiState.value.isScanning) stopScanning() else startScanning()
    }

    fun onCanvasTap(ptM: Point2D) {
        val state = _uiState.value
        val floor = state.activeFloor ?: return
        val building = state.activeBuilding ?: return

        if (state.isCalibratingTap) {
            val rawProjected = state.liveRawProjectedPtM
            if (rawProjected == null) {
                _uiState.update {
                    it.copy(
                        isCalibratingTap = false,
                        statusBanner = "GPS fix lost — try calibrating again once locked."
                    )
                }
                return
            }
            val offset = CalibrationMath.computeOffset(ptM, rawProjected)
            _uiState.update {
                it.copy(
                    calibrationOffsetsByFloor = it.calibrationOffsetsByFloor + (floor.id to offset),
                    isCalibratingTap = false,
                    statusBanner = "GPS calibrated (offset ${"%.1f".format(hypot(offset.x, offset.y))} m)."
                )
            }
            updateLiveGpsState(state.liveRawGpsFix)
            return
        }

        if (state.scanMode == ScanMode.MANUAL_TAP) {
            _uiState.update { it.copy(selectedTapPtM = ptM) }
            if (state.isForeground) {
                val readings = if (state.latestReadings.isNotEmpty()) {
                    state.latestReadings
                } else {
                    val fresh = wifiScanner.readScanResultsOnce()
                    fresh.ifEmpty { demoSimulator.synthesizeReadingsAt(state.project, floor, ptM) }
                }
                recordSample(
                    buildingId = building.id,
                    floorId = floor.id,
                    posM = ptM,
                    source = PositionSource.MANUAL,
                    gpsFix = state.liveRawGpsFix,
                    calibrationOffsetM = null,
                    readings = readings
                )
            }
        }
    }

    fun captureThreeXBurst() {
        val state = _uiState.value
        if (!state.isForeground || state.isBursting) return
        val building = state.activeBuilding ?: return
        val floor = state.activeFloor ?: return

        val capturePt = when (state.scanMode) {
            ScanMode.MANUAL_TAP -> state.selectedTapPtM ?: state.liveLocalPosM
            ScanMode.GPS_WALK -> state.liveLocalPosM ?: state.selectedTapPtM
        } ?: run {
            _uiState.update {
                it.copy(statusBanner = "Tap a spot on the floor plan or wait for GPS lock first.")
            }
            return
        }

        viewModelScope.launch {
            _uiState.update { it.copy(isBursting = true, statusBanner = "Capturing 3× Wi-Fi burst...") }
            val averaged = try {
                val real = wifiScanner.captureBurst(burstCount = 3, spacingMs = 850L)
                real.ifEmpty {
                    listOf(
                        demoSimulator.synthesizeReadingsAt(state.project, floor, capturePt),
                        demoSimulator.synthesizeReadingsAt(state.project, floor, capturePt),
                        demoSimulator.synthesizeReadingsAt(state.project, floor, capturePt)
                    ).let { org.netviz.survey.hardware.BurstAverager.averageReadings(it) }
                }
            } finally {
                _uiState.update { it.copy(isBursting = false) }
            }

            if (averaged.isNotEmpty()) {
                val calOffset = if (state.scanMode == ScanMode.GPS_WALK) state.activeCalibrationOffset else null
                val source = when {
                    state.scanMode == ScanMode.MANUAL_TAP -> PositionSource.MANUAL
                    calOffset != null -> PositionSource.GPS_CALIBRATED
                    else -> PositionSource.GPS
                }
                recordSample(
                    buildingId = building.id,
                    floorId = floor.id,
                    posM = capturePt,
                    source = source,
                    gpsFix = state.liveRawGpsFix,
                    calibrationOffsetM = calOffset,
                    readings = averaged
                )
                _uiState.update {
                    it.copy(statusBanner = "3× Burst recorded (${averaged.size} APs averaged).")
                }
            }
        }
    }

    fun undoLastSample() {
        val samples = _uiState.value.session.samples
        if (samples.isEmpty()) return
        val updated = _uiState.value.session.copy(samples = samples.dropLast(1))
        persistSessionUpdate(updated)
    }

    fun inspectSample(sample: SurveySample?) {
        _uiState.update { it.copy(inspectedSample = sample) }
    }

    fun deleteSample(sampleId: String) {
        val updated = _uiState.value.session.copy(
            samples = _uiState.value.session.samples.filterNot { it.id == sampleId }
        )
        _uiState.update { it.copy(inspectedSample = null) }
        persistSessionUpdate(updated)
    }

    fun clearDraftAndSamples() {
        viewModelScope.launch {
            bundleRepository.clearDraftSession()
            val fresh = SurveySession(
                name = "Field Survey",
                createdUtc = Instant.now().toString(),
                deviceModel = "${Build.MANUFACTURER} ${Build.MODEL}".trim(),
                samples = emptyList()
            )
            lastGpsRecordedPtM = null
            _uiState.update {
                it.copy(
                    session = fresh,
                    selectedTapPtM = null,
                    statusBanner = "Cleared active survey draft."
                )
            }
            recomputeHeatmap()
        }
    }

    fun startDemoWalkSimulator() {
        val state = _uiState.value
        val building = state.activeBuilding ?: return
        val floor = state.activeFloor ?: return
        if (!state.isForeground) return

        stopScanning()
        stopDemoWalk()

        val waypoints = demoSimulator.buildWalkWaypoints(floor)
        if (waypoints.isEmpty()) return

        _uiState.update {
            it.copy(
                isDemoWalking = true,
                scanMode = ScanMode.GPS_WALK,
                statusBanner = "Demo Walk Simulator running on ${floor.name}..."
            )
        }

        demoWalkJob = viewModelScope.launch {
            var idx = 0
            while (isActive && _uiState.value.isDemoWalking && _uiState.value.isForeground) {
                val pt = waypoints[idx % waypoints.size]
                val nextPt = waypoints[(idx + 1) % waypoints.size]
                val anchor = building.geoAnchor
                val (lat, lon) = if (anchor != null) {
                    GeoMath.localMToLatLon(pt, anchor)
                } else {
                    52.52 to 13.405
                }
                val az = demoSimulator.headingAzimuthDeg(pt, nextPt, anchor?.rotationDeg ?: 0.0)
                val fix = GpsFix(
                    rawLat = lat,
                    rawLon = lon,
                    smoothedLat = lat,
                    smoothedLon = lon,
                    accuracyM = 2.4f,
                    provider = "Demo Sim",
                    timestampMs = System.currentTimeMillis()
                )
                val readings = demoSimulator.synthesizeReadingsAt(_uiState.value.project, floor, pt)
                val targetSsids = _uiState.value.project.getTargetSsids()
                val matched = pickStrongestMatchedReading(readings, targetSsids)
                val room = GeoMath.findRoomAtPoint(pt, floor)

                _uiState.update {
                    it.copy(
                        liveRawGpsFix = fix,
                        liveRawProjectedPtM = pt,
                        liveLocalPosM = pt,
                        liveInsideFloor = GeoMath.pointInPolygon(pt, floor.outerPolygonM),
                        liveRoomName = room?.name,
                        trueNorthAzimuthDeg = az,
                        activeProviderLabel = "Demo Sim",
                        latestReadings = readings,
                        strongestMatchedSsid = matched?.ssid,
                        strongestMatchedRssi = matched?.rssiDbm
                    )
                }

                recordSample(
                    buildingId = building.id,
                    floorId = floor.id,
                    posM = pt,
                    source = PositionSource.GPS,
                    gpsFix = fix,
                    calibrationOffsetM = null,
                    readings = readings
                )
                idx++
                delay(1200L)
            }
        }
    }

    fun stopDemoWalk() {
        demoWalkJob?.cancel()
        demoWalkJob = null
        if (_uiState.value.isDemoWalking) {
            _uiState.update { it.copy(isDemoWalking = false) }
        }
    }

    fun acceptBuildingAutoSwitch() {
        val suggested = _uiState.value.autoSwitchBuildingSuggestion ?: return
        selectBuilding(suggested.id)
    }

    fun dismissBuildingAutoSwitch() {
        _uiState.update { it.copy(autoSwitchBuildingSuggestion = null) }
    }

    fun loadDemoProject() {
        viewModelScope.launch {
            runCatching { bundleRepository.loadDemoProject() }
                .onSuccess { bundle ->
                    applyLoadedBundle(bundle, preserveSession = null)
                    _uiState.update { it.copy(statusBanner = "Loaded Demo Campus Project.") }
                }
                .onFailure { err ->
                    _uiState.update { it.copy(statusBanner = "Failed to load demo: ${err.message}") }
                }
        }
    }

    fun importFromSafUri(uri: Uri) {
        viewModelScope.launch {
            runCatching { bundleRepository.importFromUri(uri, "File (SAF)") }
                .onSuccess { bundle ->
                    applyLoadedBundle(bundle, preserveSession = null)
                    _uiState.update { it.copy(statusBanner = "Imported ${bundle.project.name}.") }
                }
                .onFailure { err ->
                    _uiState.update { it.copy(statusBanner = "Import failed: ${err.message}") }
                }
        }
    }

    fun exportToSafUri(uri: Uri) {
        viewModelScope.launch {
            runCatching { bundleRepository.exportSurveyToUri(_uiState.value.session, uri) }
                .onSuccess {
                    _uiState.update { it.copy(statusBanner = "Saved survey JSON via SAF.") }
                }
                .onFailure { err ->
                    _uiState.update { it.copy(statusBanner = "Save failed: ${err.message}") }
                }
        }
    }

    suspend fun exportForShareSheet(): Result<File> = runCatching {
        bundleRepository.exportSurveyForShare(_uiState.value.session)
    }

    fun testLanConnection(host: String, port: Int, token: String) {
        viewModelScope.launch {
            _uiState.update {
                it.copy(
                    lastSyncHost = host.trim(),
                    lastSyncPort = port,
                    lastSyncToken = token.trim(),
                    syncDiagnosticsText = "Pinging $host:$port..."
                )
            }
            runCatching { lanSyncClient.pingHost(host, port, token.ifBlank { null }) }
                .onSuccess { resp ->
                    val tokenNote = when (resp.tokenValid) {
                        true -> "Token valid"
                        false -> "TOKEN MISMATCH"
                        null -> "No token provided"
                    }
                    _uiState.update {
                        it.copy(
                            syncDiagnosticsText = "Connected to ${resp.projectName.ifBlank { resp.app }} " +
                                "(${resp.bundleBytes / 1024} KB · $tokenNote)"
                        )
                    }
                }
                .onFailure { err ->
                    _uiState.update { it.copy(syncDiagnosticsText = err.message ?: "Ping failed") }
                }
        }
    }

    fun discoverDesktopsOnLan() {
        viewModelScope.launch {
            _uiState.update { it.copy(syncDiagnosticsText = "Broadcasting UDP 8766 discovery...") }
            runCatching { lanSyncClient.discoverDesktopOnLan() }
                .onSuccess { list ->
                    _uiState.update {
                        it.copy(
                            discoveredHosts = list,
                            syncDiagnosticsText = if (list.isEmpty()) {
                                "No Desktop PCs answered UDP 8766. Try QR scan, Manual IP, or USB Sync (127.0.0.1)."
                            } else {
                                "Found ${list.size} Desktop PC(s)."
                            }
                        )
                    }
                }
                .onFailure { err ->
                    _uiState.update { it.copy(syncDiagnosticsText = "UDP discovery error: ${err.message}") }
                }
        }
    }

    fun importFromQrString(rawQr: String, onComplete: (Boolean) -> Unit = {}) {
        val payload: QrPairingPayload = try {
            lanSyncClient.parseQrPayload(rawQr)
        } catch (err: Exception) {
            _uiState.update { it.copy(syncDiagnosticsText = err.message ?: "Invalid QR code") }
            onComplete(false)
            return
        }
        importFromLanCandidates(
            ips = payload.resolvedIps(),
            port = payload.port,
            token = payload.token,
            onComplete = onComplete
        )
    }

    fun importFromLanCandidates(
        ips: List<String>,
        port: Int,
        token: String,
        onComplete: (Boolean) -> Unit = {}
    ) {
        transferJob?.cancel()
        transferJob = viewModelScope.launch {
            _uiState.update {
                it.copy(
                    transferProgress = TransferProgressState(true, "Probing Desktop PCs...", 0L, -1L),
                    syncDiagnosticsText = null
                )
            }
            try {
                val (winnerIp, pingResp) = lanSyncClient.probeFirstReachableHost(ips, port, token)
                _uiState.update {
                    it.copy(
                        lastSyncHost = winnerIp,
                        lastSyncPort = port,
                        lastSyncToken = token,
                        transferProgress = TransferProgressState(
                            true,
                            "Downloading ${pingResp.projectName.ifBlank { "project.netviz" }}...",
                            0L,
                            pingResp.bundleBytes
                        )
                    )
                }
                val destFile = File(getApplication<Application>().cacheDir, "lan_import.netviz")
                val dl = lanSyncClient.downloadProjectNetviz(
                    host = winnerIp,
                    port = port,
                    token = token,
                    destFile = destFile,
                    expectedSha256 = pingResp.bundleSha256.takeIf { it.isNotBlank() }
                ) { read, total ->
                    _uiState.update {
                        it.copy(
                            transferProgress = TransferProgressState(
                                true,
                                "Downloading from $winnerIp...",
                                read,
                                total
                            )
                        )
                    }
                }

                val bundle = bundleRepository.extractNetvizFile(dl.file, "LAN ($winnerIp)")
                applyLoadedBundle(bundle, preserveSession = null)
                _uiState.update {
                    it.copy(
                        transferProgress = TransferProgressState(),
                        statusBanner = "Synced '${bundle.project.name}' from $winnerIp.",
                        syncDiagnosticsText = "Downloaded ${dl.bytesRead / 1024} KB (SHA-256 verified)."
                    )
                }
                onComplete(true)
            } catch (err: Exception) {
                _uiState.update {
                    it.copy(
                        transferProgress = TransferProgressState(),
                        syncDiagnosticsText = err.message ?: "LAN import failed"
                    )
                }
                onComplete(false)
            }
        }
    }

    fun pushSurveyToDesktop(
        host: String,
        port: Int,
        token: String,
        onComplete: (Boolean) -> Unit = {}
    ) {
        transferJob?.cancel()
        transferJob = viewModelScope.launch {
            val cleanHost = host.trim()
            val cleanToken = token.trim()
            _uiState.update {
                it.copy(
                    lastSyncHost = cleanHost,
                    lastSyncPort = port,
                    lastSyncToken = cleanToken,
                    transferProgress = TransferProgressState(true, "Uploading survey to $cleanHost...", 0L, -1L),
                    syncDiagnosticsText = null
                )
            }
            try {
                val resp = lanSyncClient.uploadSurveySession(
                    host = cleanHost,
                    port = port,
                    token = cleanToken,
                    session = _uiState.value.session
                ) { sent, total ->
                    _uiState.update {
                        it.copy(
                            transferProgress = TransferProgressState(
                                true,
                                "Uploading survey to $cleanHost...",
                                sent,
                                total
                            )
                        )
                    }
                }
                _uiState.update {
                    it.copy(
                        transferProgress = TransferProgressState(),
                        statusBanner = "Uploaded ${resp.sampleCount} samples to Desktop.",
                        syncDiagnosticsText = "Upload succeeded (${resp.sampleCount} samples)."
                    )
                }
                onComplete(true)
            } catch (err: Exception) {
                _uiState.update {
                    it.copy(
                        transferProgress = TransferProgressState(),
                        syncDiagnosticsText = err.message ?: "Upload failed"
                    )
                }
                onComplete(false)
            }
        }
    }

    fun cancelActiveTransfer() {
        transferJob?.cancel()
        transferJob = null
        _uiState.update {
            it.copy(
                transferProgress = TransferProgressState(),
                syncDiagnosticsText = "Transfer cancelled."
            )
        }
    }

    fun readTile(provider: String, z: Int, x: Int, y: Int) =
        bundleRepository.readTile(provider, z, x, y)

    private fun tryCaptureGpsWalkSample(state: SurveyUiState) {
        val fix = state.liveRawGpsFix ?: return
        val localPos = state.liveLocalPosM ?: return
        val building = state.activeBuilding ?: return
        val floor = state.activeFloor ?: return
        if (state.enforceAccuracyGate && fix.accuracyM > state.maxAccuracyGateM) {
            return
        }
        val readings = state.latestReadings
        if (readings.isEmpty()) return

        val now = System.currentTimeMillis()
        val prevPt = lastGpsRecordedPtM
        val movedEnough = prevPt == null || hypot(localPos.x - prevPt.x, localPos.y - prevPt.y) >= 1.0
        val intervalElapsed = (now - lastGpsRecordedWallMs) >= state.scanIntervalMs
        if (!movedEnough && !intervalElapsed) return

        lastGpsRecordedPtM = localPos
        lastGpsRecordedWallMs = now

        val calOffset = state.activeCalibrationOffset
        recordSample(
            buildingId = building.id,
            floorId = floor.id,
            posM = localPos,
            source = if (calOffset != null) PositionSource.GPS_CALIBRATED else PositionSource.GPS,
            gpsFix = fix,
            calibrationOffsetM = calOffset,
            readings = readings
        )
    }

    private fun recordSample(
        buildingId: String,
        floorId: String,
        posM: Point2D,
        source: PositionSource,
        gpsFix: GpsFix?,
        calibrationOffsetM: Point2D?,
        readings: List<WifiReading>
    ) {
        val sample = SurveySample(
            id = newId(),
            timestampUtc = DateTimeFormatter.ISO_INSTANT.format(Instant.now().atOffset(ZoneOffset.UTC)),
            buildingId = buildingId,
            floorId = floorId,
            posM = posM,
            source = source,
            gpsLat = gpsFix?.rawLat,
            gpsLon = gpsFix?.rawLon,
            gpsAccuracyM = gpsFix?.accuracyM?.toDouble(),
            calibrationOffsetM = calibrationOffsetM,
            readings = readings
        )
        val updatedSession = _uiState.value.session.copy(
            samples = _uiState.value.session.samples + sample
        )
        persistSessionUpdate(updatedSession)
    }

    private fun persistSessionUpdate(updatedSession: SurveySession) {
        _uiState.update { it.copy(session = updatedSession) }
        viewModelScope.launch {
            bundleRepository.saveDraftSession(updatedSession)
        }
        recomputeHeatmap()
    }

    private fun applyLoadedBundle(
        bundle: LoadedProjectBundle,
        preserveSession: SurveySession?
    ) {
        val firstBuilding = bundle.project.buildings.firstOrNull()
        val firstFloor = firstBuilding?.floors?.firstOrNull()
        val hasTiles = bundleRepository.hasCachedTiles()
        val sessionToUse = preserveSession ?: SurveySession(
            name = "${bundle.project.name} Survey",
            createdUtc = Instant.now().toString(),
            deviceModel = "${Build.MANUFACTURER} ${Build.MODEL}".trim(),
            samples = bundle.project.surveys.firstOrNull()?.samples.orEmpty()
        )
        lastGpsRecordedPtM = null
        _uiState.update {
            it.copy(
                project = bundle.project,
                activeBuildingId = firstBuilding?.id,
                activeFloorId = firstFloor?.id,
                session = sessionToUse,
                sourceLabel = bundle.sourceLabel,
                hasTilesDb = hasTiles,
                showMapBackdrop = hasTiles,
                selectedTapPtM = null,
                isCalibratingTap = false,
                autoSwitchBuildingSuggestion = null,
                resetViewNonce = it.resetViewNonce + 1
            )
        }
        recomputeHeatmap()
    }

    private fun updateLiveGpsState(fix: GpsFix?) {
        val state = _uiState.value
        if (fix == null) {
            _uiState.update {
                it.copy(
                    liveRawGpsFix = null,
                    liveRawProjectedPtM = null,
                    liveLocalPosM = null,
                    liveInsideFloor = false,
                    liveRoomName = null,
                    liveBearingText = null,
                    autoSwitchBuildingSuggestion = null
                )
            }
            return
        }

        val building = state.activeBuilding
        val floor = state.activeFloor
        val anchor = building?.geoAnchor
        if (building == null || floor == null || anchor == null) {
            _uiState.update { it.copy(liveRawGpsFix = fix) }
            return
        }

        val rawLocal = GeoMath.latLonToLocalM(fix.smoothedLat, fix.smoothedLon, anchor)
        val calOffset = floor.id.let { state.calibrationOffsetsByFloor[it] }
        val effectiveLocal = CalibrationMath.applyOffset(rawLocal, calOffset)
        val inside = GeoMath.pointInPolygon(effectiveLocal, floor.outerPolygonM)
        val room = if (inside) GeoMath.findRoomAtPoint(effectiveLocal, floor) else null

        val bounds = GeoMath.polygonBounds(floor.outerPolygonM)
        val centerPt = Point2D(0.5 * (bounds.minX + bounds.maxX), 0.5 * (bounds.minY + bounds.maxY))
        val (distM, cardinal) = GeoMath.distanceAndCardinalBearing(
            fromPt = effectiveLocal,
            toPt = centerPt,
            anchorRotationDeg = anchor.rotationDeg
        )
        val bearingText = if (!inside) {
            "${"%.1f".format(distM)}m $cardinal of ${building.name}"
        } else {
            null
        }

        val suggestedSwitch = if (!inside) {
            state.project.buildings.firstOrNull { candidate ->
                if (candidate.id == building.id) return@firstOrNull false
                val candAnchor = candidate.geoAnchor ?: return@firstOrNull false
                val candFloor0 = candidate.floors.firstOrNull() ?: return@firstOrNull false
                val candPt = GeoMath.latLonToLocalM(fix.smoothedLat, fix.smoothedLon, candAnchor)
                GeoMath.pointInPolygon(candPt, candFloor0.outerPolygonM)
            }
        } else {
            null
        }

        _uiState.update {
            it.copy(
                liveRawGpsFix = fix,
                liveRawProjectedPtM = rawLocal,
                liveLocalPosM = effectiveLocal,
                liveInsideFloor = inside,
                liveRoomName = room?.name,
                liveBearingText = bearingText,
                autoSwitchBuildingSuggestion = suggestedSwitch
            )
        }
    }

    private fun recomputeHeatmap() {
        val state = _uiState.value
        val floor = state.activeFloor ?: return
        val samples = state.activeFloorSamples
        val targetSsids = state.project.getTargetSsids()
        val deadZone = state.deadZoneDbm

        heatmapJob?.cancel()
        heatmapJob = viewModelScope.launch {
            val rendered = withContext(Dispatchers.Default) {
                HeatmapEngine.renderIdwHeatmapBitmap(
                    floor = floor,
                    samples = samples,
                    targetSsids = targetSsids.ifEmpty { null },
                    resolutionM = 0.35,
                    deadZoneDbm = deadZone
                )
            }
            _uiState.update { it.copy(renderedHeatmap = rendered) }
        }
    }

    private fun pickStrongestMatchedReading(
        readings: List<WifiReading>,
        targetSsids: List<String>
    ): WifiReading? {
        if (readings.isEmpty()) return null
        if (targetSsids.isEmpty()) return readings.maxByOrNull { it.rssiDbm }
        val set = targetSsids.toSet()
        return readings.filter { it.ssid.trim() in set }.maxByOrNull { it.rssiDbm }
            ?: readings.maxByOrNull { it.rssiDbm }
    }

    override fun onCleared() {
        super.onCleared()
        onAppBackgrounded()
        bundleRepository.closeTilesDatabase()
    }
}
