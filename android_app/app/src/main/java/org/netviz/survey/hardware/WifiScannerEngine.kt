package org.netviz.survey.hardware

import android.Manifest
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.net.wifi.ScanResult
import android.net.wifi.WifiManager
import android.os.Build
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import org.netviz.survey.model.WifiReading
import org.netviz.survey.model.normalizeMac
import kotlin.math.roundToInt

class ScanThrottleTracker(private val staleThresholdMs: Long = 15_000L) {
    private var scanStartedWallMs: Long = 0L
    private var lastProgressWallMs: Long = 0L
    private var lastMaxHardwareUs: Long = -1L

    fun reset(nowWallMs: Long = System.currentTimeMillis()) {
        scanStartedWallMs = nowWallMs
        lastProgressWallMs = nowWallMs
        lastMaxHardwareUs = -1L
    }

    fun recordScanResults(
        maxHardwareTimestampUs: Long,
        nowWallMs: Long = System.currentTimeMillis()
    ): Boolean {
        if (scanStartedWallMs == 0L) {
            reset(nowWallMs)
        }
        if (maxHardwareTimestampUs > 0L && maxHardwareTimestampUs > lastMaxHardwareUs) {
            lastMaxHardwareUs = maxHardwareTimestampUs
            lastProgressWallMs = nowWallMs
            return false
        }
        return (nowWallMs - lastProgressWallMs) > staleThresholdMs
    }
}

object BurstAverager {
    fun averageReadings(bursts: List<List<WifiReading>>): List<WifiReading> {
        if (bursts.isEmpty()) return emptyList()
        val grouped = LinkedHashMap<String, MutableList<WifiReading>>()
        for (snapshot in bursts) {
            for (reading in snapshot) {
                val key = normalizeMac(reading.bssid)
                if (key.isEmpty()) continue
                grouped.getOrPut(key) { mutableListOf() }.add(reading)
            }
        }
        return grouped.values
            .map { list ->
                val bestSample = list.maxBy { it.rssiDbm }
                val nonEmptySsid = list.firstOrNull { it.ssid.isNotBlank() }?.ssid ?: bestSample.ssid
                val avgRssi = list.map { it.rssiDbm }.average().roundToInt()
                WifiReading(
                    ssid = nonEmptySsid,
                    bssid = bestSample.bssid,
                    rssiDbm = avgRssi,
                    frequencyMhz = bestSample.frequencyMhz
                )
            }
            .sortedByDescending { it.rssiDbm }
    }
}

class WifiScannerEngine(context: Context) {
    private val appContext = context.applicationContext
    private val wifiManager =
        appContext.getSystemService(Context.WIFI_SERVICE) as? WifiManager

    private val throttleTracker = ScanThrottleTracker(THROTTLE_STALE_MS)
    private var triggerJob: Job? = null
    private var pollJob: Job? = null
    private var scanReceiver: BroadcastReceiver? = null

    @Volatile
    var isForeground: Boolean = false
        private set

    private val _isScanning = MutableStateFlow(false)
    val isScanning: StateFlow<Boolean> = _isScanning.asStateFlow()

    private val _latestReadings = MutableStateFlow<List<WifiReading>>(emptyList())
    val latestReadings: StateFlow<List<WifiReading>> = _latestReadings.asStateFlow()

    private val _isScanThrottled = MutableStateFlow(false)
    val isScanThrottled: StateFlow<Boolean> = _isScanThrottled.asStateFlow()

    private val _lastScanWallMs = MutableStateFlow(0L)
    val lastScanWallMs: StateFlow<Long> = _lastScanWallMs.asStateFlow()

    fun setForeground(foreground: Boolean) {
        isForeground = foreground
        if (!foreground) {
            stopScanning()
        }
    }

    fun startScanning(scope: CoroutineScope, intervalMs: Long = DEFAULT_SCAN_INTERVAL_MS) {
        if (!isForeground) {
            stopScanning()
            return
        }
        stopScanning()

        throttleTracker.reset(System.currentTimeMillis())
        _isScanThrottled.value = false
        _isScanning.value = true

        registerScanReceiver()
        readScanResultsOnce()

        triggerJob = scope.launch(Dispatchers.Default) {
            while (isActive && isForeground && _isScanning.value) {
                triggerSingleScan()
                delay(intervalMs.coerceAtLeast(1000L))
            }
        }

        pollJob = scope.launch(Dispatchers.Default) {
            while (isActive && isForeground && _isScanning.value) {
                delay(POLL_INTERVAL_MS)
                if (isForeground && _isScanning.value) {
                    readScanResultsOnce()
                }
            }
        }
    }

    fun stopScanning() {
        _isScanning.value = false
        triggerJob?.cancel()
        triggerJob = null
        pollJob?.cancel()
        pollJob = null
        unregisterScanReceiver()
    }

    suspend fun captureBurst(
        burstCount: Int = 3,
        spacingMs: Long = 900L
    ): List<WifiReading> {
        if (!isForeground) return emptyList()
        val snapshots = mutableListOf<List<WifiReading>>()
        repeat(burstCount.coerceAtLeast(1)) { idx ->
            if (!isForeground) return@repeat
            triggerSingleScan()
            if (idx > 0) {
                delay(spacingMs)
            } else {
                delay(350L)
            }
            if (!isForeground) return@repeat
            val readings = readScanResultsOnce()
            if (readings.isNotEmpty()) {
                snapshots.add(readings)
            }
        }
        return BurstAverager.averageReadings(snapshots)
    }

    fun readScanResultsOnce(): List<WifiReading> {
        if (!isForeground) return emptyList()
        val wm = wifiManager ?: return emptyList()
        if (!hasWifiReadPermission()) return emptyList()

        val rawResults: List<ScanResult> = try {
            @Suppress("MissingPermission")
            wm.scanResults.orEmpty()
        } catch (_: SecurityException) {
            emptyList()
        }

        val maxHwUs = rawResults.maxOfOrNull { it.timestamp } ?: 0L
        val throttled = throttleTracker.recordScanResults(maxHwUs, System.currentTimeMillis())
        if (_isScanning.value) {
            _isScanThrottled.value = throttled
        }

        val parsed = rawResults
            .mapNotNull { sr ->
                val bssid = sr.BSSID?.trim().orEmpty()
                if (bssid.isEmpty()) return@mapNotNull null
                WifiReading(
                    ssid = extractSsid(sr),
                    bssid = bssid,
                    rssiDbm = sr.level,
                    frequencyMhz = sr.frequency
                )
            }
            .sortedByDescending { it.rssiDbm }

        _latestReadings.value = parsed
        _lastScanWallMs.value = System.currentTimeMillis()
        return parsed
    }

    private fun triggerSingleScan() {
        if (!isForeground) return
        val wm = wifiManager ?: return
        if (!hasWifiReadPermission()) return
        runCatching {
            @Suppress("DEPRECATION")
            wm.startScan()
        }
    }

    private fun registerScanReceiver() {
        if (scanReceiver != null) return
        val receiver = object : BroadcastReceiver() {
            override fun onReceive(context: Context?, intent: Intent?) {
                if (isForeground && _isScanning.value) {
                    readScanResultsOnce()
                }
            }
        }
        runCatching {
            ContextCompat.registerReceiver(
                appContext,
                receiver,
                IntentFilter(WifiManager.SCAN_RESULTS_AVAILABLE_ACTION),
                ContextCompat.RECEIVER_NOT_EXPORTED
            )
            scanReceiver = receiver
        }
    }

    private fun unregisterScanReceiver() {
        val receiver = scanReceiver ?: return
        scanReceiver = null
        runCatching {
            appContext.unregisterReceiver(receiver)
        }
    }

    private fun hasWifiReadPermission(): Boolean {
        val fine = ContextCompat.checkSelfPermission(
            appContext,
            Manifest.permission.ACCESS_FINE_LOCATION
        ) == PackageManager.PERMISSION_GRANTED
        val nearby = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            ContextCompat.checkSelfPermission(
                appContext,
                Manifest.permission.NEARBY_WIFI_DEVICES
            ) == PackageManager.PERMISSION_GRANTED
        } else {
            false
        }
        return fine || nearby
    }

    @Suppress("DEPRECATION")
    private fun extractSsid(result: ScanResult): String {
        val raw = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            result.wifiSsid?.toString() ?: result.SSID
        } else {
            result.SSID
        }.orEmpty().trim()
        return if (raw.length >= 2 && raw.startsWith('"') && raw.endsWith('"')) {
            raw.substring(1, raw.length - 1)
        } else {
            raw
        }
    }

    companion object {
        const val DEFAULT_SCAN_INTERVAL_MS = 2500L
        const val POLL_INTERVAL_MS = 1500L
        const val THROTTLE_STALE_MS = 15_000L
    }
}
