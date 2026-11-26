package org.netviz.survey.hardware

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.hardware.GeomagneticField
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.os.Build
import android.os.Bundle
import android.os.Looper
import androidx.core.content.ContextCompat
import com.google.android.gms.common.ConnectionResult
import com.google.android.gms.common.GoogleApiAvailability
import com.google.android.gms.location.FusedLocationProviderClient
import com.google.android.gms.location.Granularity
import com.google.android.gms.location.LocationCallback
import com.google.android.gms.location.LocationRequest
import com.google.android.gms.location.LocationResult
import com.google.android.gms.location.LocationServices
import com.google.android.gms.location.Priority
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import org.netviz.survey.model.Point2D
import kotlin.math.max

object GpsSmoother {
    fun smooth(
        previous: Pair<Double, Double>?,
        rawLat: Double,
        rawLon: Double,
        accuracyM: Float
    ): Pair<Double, Double> {
        if (previous == null) return rawLat to rawLon
        val safeAcc = max(accuracyM, 1.0f)
        val alpha = (3.0f / safeAcc).coerceIn(0.15f, 0.75f).toDouble()
        val lat = previous.first + alpha * (rawLat - previous.first)
        val lon = previous.second + alpha * (rawLon - previous.second)
        return lat to lon
    }
}

object CalibrationMath {
    fun computeOffset(knownPtM: Point2D, rawProjectedPtM: Point2D): Point2D =
        Point2D(
            x = knownPtM.x - rawProjectedPtM.x,
            y = knownPtM.y - rawProjectedPtM.y
        )

    fun applyOffset(rawProjectedPtM: Point2D, offsetM: Point2D?): Point2D =
        if (offsetM == null) {
            rawProjectedPtM
        } else {
            Point2D(
                x = rawProjectedPtM.x + offsetM.x,
                y = rawProjectedPtM.y + offsetM.y
            )
        }
}

data class GpsFix(
    val rawLat: Double,
    val rawLon: Double,
    val smoothedLat: Double,
    val smoothedLon: Double,
    val accuracyM: Float,
    val provider: String,
    val timestampMs: Long
)

class LocationCompassEngine(context: Context) {
    private val appContext = context.applicationContext
    private val locationManager =
        appContext.getSystemService(Context.LOCATION_SERVICE) as? LocationManager
    private val sensorManager =
        appContext.getSystemService(Context.SENSOR_SERVICE) as? SensorManager

    private var fusedClient: FusedLocationProviderClient? = null
    private var fusedCallback: LocationCallback? = null
    private var platformListener: LocationListener? = null
    private var compassListener: SensorEventListener? = null
    private var lastSmoothed: Pair<Double, Double>? = null

    @Volatile
    private var isStarted = false

    private val _currentFix = MutableStateFlow<GpsFix?>(null)
    val currentFix: StateFlow<GpsFix?> = _currentFix.asStateFlow()

    private val _trueNorthAzimuthDeg = MutableStateFlow<Float?>(null)
    val trueNorthAzimuthDeg: StateFlow<Float?> = _trueNorthAzimuthDeg.asStateFlow()

    private val _activeProviderLabel = MutableStateFlow("Idle")
    val activeProviderLabel: StateFlow<String> = _activeProviderLabel.asStateFlow()

    fun start(isForeground: Boolean) {
        if (!isForeground) {
            stop()
            return
        }
        if (isStarted) return
        isStarted = true
        startLocationUpdates()
        startCompassUpdates()
    }

    fun stop() {
        isStarted = false
        stopLocationUpdates()
        stopCompassUpdates()
        _activeProviderLabel.value = "Idle"
    }

    private fun startLocationUpdates() {
        if (!hasLocationPermission()) {
            _activeProviderLabel.value = "Permission needed"
            return
        }

        val playAvailable = runCatching {
            GoogleApiAvailability.getInstance().isGooglePlayServicesAvailable(appContext) ==
                ConnectionResult.SUCCESS
        }.getOrDefault(false)

        if (playAvailable && startFusedLocation()) {
            _activeProviderLabel.value = "Fused"
            return
        }

        startPlatformLocationManager()
    }

    private fun startFusedLocation(): Boolean {
        return runCatching {
            val client = LocationServices.getFusedLocationProviderClient(appContext)
            val request = LocationRequest.Builder(Priority.PRIORITY_HIGH_ACCURACY, 1000L)
                .setMinUpdateIntervalMillis(500L)
                .setGranularity(Granularity.GRANULARITY_FINE)
                .build()
            val callback = object : LocationCallback() {
                override fun onLocationResult(result: LocationResult) {
                    val loc = result.lastLocation ?: return
                    onRawLocation(loc, "Fused")
                }
            }
            @Suppress("MissingPermission")
            client.requestLocationUpdates(request, callback, Looper.getMainLooper())
            fusedClient = client
            fusedCallback = callback
            true
        }.getOrDefault(false)
    }

    private fun startPlatformLocationManager() {
        val lm = locationManager ?: run {
            _activeProviderLabel.value = "No LocationManager"
            return
        }

        val listener = object : LocationListener {
            override fun onLocationChanged(location: Location) {
                onRawLocation(location, location.provider ?: "GPS")
            }

            @Deprecated("Deprecated in Android API 29")
            override fun onStatusChanged(provider: String?, status: Int, extras: Bundle?) {}
        }
        platformListener = listener

        var subscribed = false
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            runCatching {
                if (lm.hasProvider(LocationManager.FUSED_PROVIDER)) {
                    @Suppress("MissingPermission")
                    lm.requestLocationUpdates(
                        LocationManager.FUSED_PROVIDER,
                        1000L,
                        0f,
                        listener,
                        Looper.getMainLooper()
                    )
                    subscribed = true
                    _activeProviderLabel.value = "Platform Fused"
                }
            }
        }

        if (!subscribed) {
            val providers = listOf(LocationManager.GPS_PROVIDER, LocationManager.NETWORK_PROVIDER)
            for (provider in providers) {
                runCatching {
                    if (lm.isProviderEnabled(provider)) {
                        @Suppress("MissingPermission")
                        lm.requestLocationUpdates(
                            provider,
                            1000L,
                            0f,
                            listener,
                            Looper.getMainLooper()
                        )
                        subscribed = true
                    }
                }
            }
            _activeProviderLabel.value = if (subscribed) "GPS" else "Location Disabled"
        }
    }

    private fun onRawLocation(location: Location, providerLabel: String) {
        if (!isStarted) return
        val nowMs = System.currentTimeMillis()
        val ageMs = nowMs - location.time
        if (location.time > 0L && ageMs > MAX_FIX_AGE_MS) {
            return
        }
        val accuracy = if (location.hasAccuracy()) location.accuracy else 25f
        val smoothed = GpsSmoother.smooth(
            previous = lastSmoothed,
            rawLat = location.latitude,
            rawLon = location.longitude,
            accuracyM = accuracy
        )
        lastSmoothed = smoothed
        _currentFix.value = GpsFix(
            rawLat = location.latitude,
            rawLon = location.longitude,
            smoothedLat = smoothed.first,
            smoothedLon = smoothed.second,
            accuracyM = accuracy,
            provider = providerLabel,
            timestampMs = if (location.time > 0L) location.time else nowMs
        )
        _activeProviderLabel.value = providerLabel
    }

    private fun stopLocationUpdates() {
        val client = fusedClient
        val cb = fusedCallback
        if (client != null && cb != null) {
            runCatching { client.removeLocationUpdates(cb) }
        }
        fusedClient = null
        fusedCallback = null

        val listener = platformListener
        if (listener != null) {
            runCatching { locationManager?.removeUpdates(listener) }
        }
        platformListener = null
    }

    private fun startCompassUpdates() {
        val sm = sensorManager ?: return
        val sensor = sm.getDefaultSensor(Sensor.TYPE_ROTATION_VECTOR) ?: return
        val rotMatrix = FloatArray(9)
        val orientation = FloatArray(3)

        val listener = object : SensorEventListener {
            override fun onSensorChanged(event: SensorEvent) {
                if (!isStarted || event.sensor.type != Sensor.TYPE_ROTATION_VECTOR) return
                SensorManager.getRotationMatrixFromVector(rotMatrix, event.values)
                SensorManager.getOrientation(rotMatrix, orientation)
                val magneticAzimuthDeg = Math.toDegrees(orientation[0].toDouble()).toFloat()

                val fix = _currentFix.value
                val declination = if (fix != null) {
                    GeomagneticField(
                        fix.rawLat.toFloat(),
                        fix.rawLon.toFloat(),
                        0f,
                        System.currentTimeMillis()
                    ).declination
                } else {
                    0f
                }

                val trueAzimuth = (((magneticAzimuthDeg + declination) % 360f) + 360f) % 360f
                _trueNorthAzimuthDeg.value = trueAzimuth
            }

            override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {}
        }

        if (sm.registerListener(listener, sensor, SensorManager.SENSOR_DELAY_UI)) {
            compassListener = listener
        }
    }

    private fun stopCompassUpdates() {
        val listener = compassListener ?: return
        compassListener = null
        runCatching { sensorManager?.unregisterListener(listener) }
    }

    private fun hasLocationPermission(): Boolean {
        val fine = ContextCompat.checkSelfPermission(
            appContext,
            Manifest.permission.ACCESS_FINE_LOCATION
        ) == PackageManager.PERMISSION_GRANTED
        val coarse = ContextCompat.checkSelfPermission(
            appContext,
            Manifest.permission.ACCESS_COARSE_LOCATION
        ) == PackageManager.PERMISSION_GRANTED
        return fine || coarse
    }

    companion object {
        const val MAX_FIX_AGE_MS = 10_000L
    }
}
