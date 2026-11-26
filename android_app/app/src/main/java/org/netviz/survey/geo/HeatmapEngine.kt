package org.netviz.survey.geo

import android.graphics.Bitmap
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import org.netviz.survey.model.Floor
import org.netviz.survey.model.Point2D
import org.netviz.survey.model.SurveySample
import org.netviz.survey.model.normalizeMac
import kotlin.math.ceil
import kotlin.math.hypot
import kotlin.math.max
import kotlin.math.pow
import kotlin.math.roundToInt

object HeatmapEngine {
    private data class ColorStop(val dbm: Double, val r: Int, val g: Int, val b: Int)

    private val COLOR_STOPS = listOf(
        ColorStop(-95.0, 239, 68, 68),   // #EF4444
        ColorStop(-80.0, 249, 115, 22),  // #F97316
        ColorStop(-70.0, 234, 179, 8),   // #EAB308
        ColorStop(-60.0, 132, 204, 22),  // #84CC16
        ColorStop(-30.0, 34, 197, 94)    // #22C55E
    )

    private const val BASE_ALPHA: Double = 210.0

    data class HeatmapGridResult(
        val argbPixels: IntArray,
        val width: Int,
        val height: Int,
        val bounds: GeoMath.Bounds2D,
        val resolutionM: Double,
        val validPixelCount: Int,
        val aboveThresholdCount: Int,
        val coveragePercent: Double
    ) {
        fun pixelAt(col: Int, row: Int): Int {
            require(col in 0 until width && row in 0 until height) {
                "Pixel ($col, $row) out of bounds ($width x $height)"
            }
            return argbPixels[row * width + col]
        }
    }

    data class RenderedHeatmap(
        val imageBitmap: ImageBitmap,
        val grid: HeatmapGridResult
    )

    fun frequencyToBand(frequencyMhz: Int): String? = when (frequencyMhz) {
        in 2400..2500 -> "2.4"
        in 5000..5924 -> "5"
        in 5925..7125 -> "6"
        else -> null
    }

    fun rssiToRgb(rssiDbm: Double): Triple<Int, Int, Int> {
        val first = COLOR_STOPS.first()
        val last = COLOR_STOPS.last()
        if (rssiDbm <= first.dbm) return Triple(first.r, first.g, first.b)
        if (rssiDbm >= last.dbm) return Triple(last.r, last.g, last.b)

        for (i in 0 until COLOR_STOPS.size - 1) {
            val s0 = COLOR_STOPS[i]
            val s1 = COLOR_STOPS[i + 1]
            if (rssiDbm >= s0.dbm && rssiDbm <= s1.dbm) {
                val t = (rssiDbm - s0.dbm) / (s1.dbm - s0.dbm)
                val r = (s0.r + t * (s1.r - s0.r)).roundToInt()
                val g = (s0.g + t * (s1.g - s0.g)).roundToInt()
                val b = (s0.b + t * (s1.b - s0.b)).roundToInt()
                return Triple(r, g, b)
            }
        }
        return Triple(last.r, last.g, last.b)
    }

    fun filterSamplesForFloor(
        samples: List<SurveySample>,
        floorId: String,
        targetSsids: List<String>? = null,
        bssidFilter: String? = null,
        bandFilter: String? = null
    ): List<Pair<Point2D, Double>> {
        val ssidSet = targetSsids
            ?.map { it.trim() }
            ?.filter { it.isNotEmpty() }
            ?.toSet()
            ?.takeIf { it.isNotEmpty() }
        val bssidNorm = bssidFilter?.let { normalizeMac(it) }.orEmpty()
        val band = bandFilter?.trim()?.takeIf { it.isNotEmpty() && !it.equals("all", ignoreCase = true) }

        val points = mutableListOf<Pair<Point2D, Double>>()
        for (sample in samples) {
            if (sample.floorId != floorId) continue
            var bestRssi: Int? = null
            for (reading in sample.readings) {
                if (ssidSet != null && reading.ssid.trim() !in ssidSet) continue
                if (bssidNorm.isNotEmpty() && normalizeMac(reading.bssid) != bssidNorm) continue
                if (band != null && frequencyToBand(reading.frequencyMhz) != band) continue
                if (bestRssi == null || reading.rssiDbm > bestRssi) {
                    bestRssi = reading.rssiDbm
                }
            }
            if (bestRssi != null) {
                points.add(sample.posM to bestRssi.toDouble())
            }
        }
        return points
    }

    private fun alphaForMinDistance(minDist: Double, maxRadiusM: Double): Int {
        if (minDist > maxRadiusM) return 0
        val fadeStart = 0.75 * maxRadiusM
        if (minDist <= fadeStart) return BASE_ALPHA.toInt()
        val t = 1.0 - (minDist - fadeStart) / max(maxRadiusM - fadeStart, 1e-6)
        return (BASE_ALPHA * t).roundToInt().coerceIn(0, 255)
    }

    fun computeIdwGrid(
        floor: Floor,
        samples: List<SurveySample>,
        targetSsids: List<String>? = null,
        bssidFilter: String? = null,
        bandFilter: String? = null,
        resolutionM: Double = 0.35,
        power: Double = 2.0,
        maxRadiusM: Double = 25.0,
        deadZoneDbm: Double = -70.0
    ): HeatmapGridResult {
        if (floor.outerPolygonM.size < 3) {
            return HeatmapGridResult(
                argbPixels = IntArray(4),
                width = 2,
                height = 2,
                bounds = GeoMath.Bounds2D(0.0, 0.0, 0.0, 0.0),
                resolutionM = resolutionM,
                validPixelCount = 0,
                aboveThresholdCount = 0,
                coveragePercent = 0.0
            )
        }

        val bounds = GeoMath.polygonBounds(floor.outerPolygonM)
        val spanX = max(bounds.maxX - bounds.minX, 1e-3)
        val spanY = max(bounds.maxY - bounds.minY, 1e-3)
        val width = max(2, ceil(spanX / resolutionM).toInt())
        val height = max(2, ceil(spanY / resolutionM).toInt())

        val points = filterSamplesForFloor(
            samples = samples,
            floorId = floor.id,
            targetSsids = targetSsids,
            bssidFilter = bssidFilter,
            bandFilter = bandFilter
        )

        val argbPixels = IntArray(width * height)
        if (points.isEmpty()) {
            return HeatmapGridResult(
                argbPixels = argbPixels,
                width = width,
                height = height,
                bounds = bounds,
                resolutionM = resolutionM,
                validPixelCount = 0,
                aboveThresholdCount = 0,
                coveragePercent = 0.0
            )
        }

        var insideCount = 0
        var aboveCount = 0

        for (row in 0 until height) {
            // Row 0 corresponds strictly to max_y (Building Local North)
            val yM = bounds.maxY - (row + 0.5) * (bounds.maxY - bounds.minY) / height
            val rowOffset = row * width
            for (col in 0 until width) {
                val xM = bounds.minX + (col + 0.5) * (bounds.maxX - bounds.minX) / width
                if (!GeoMath.pointInPolygon(Point2D(xM, yM), floor.outerPolygonM)) {
                    continue
                }
                insideCount++

                var minDist = Double.POSITIVE_INFINITY
                var exactSum = 0.0
                var exactN = 0
                var weightSum = 0.0
                var weightedVal = 0.0

                for (i in points.indices) {
                    val (pt, rssi) = points[i]
                    val d = hypot(xM - pt.x, yM - pt.y)
                    if (d < minDist) minDist = d
                    if (d < 1e-6) {
                        exactSum += rssi
                        exactN++
                    } else {
                        val w = 1.0 / d.pow(power)
                        weightSum += w
                        weightedVal += w * rssi
                    }
                }

                val alpha = alphaForMinDistance(minDist, maxRadiusM)
                if (alpha <= 0) continue

                val rssi = if (exactN > 0) exactSum / exactN else weightedVal / weightSum
                val (r, g, b) = rssiToRgb(rssi)
                argbPixels[rowOffset + col] =
                    ((alpha and 0xFF) shl 24) or
                        ((r and 0xFF) shl 16) or
                        ((g and 0xFF) shl 8) or
                        (b and 0xFF)

                if (rssi >= deadZoneDbm) {
                    aboveCount++
                }
            }
        }

        val coverage = if (insideCount > 0) 100.0 * aboveCount / insideCount else 0.0
        return HeatmapGridResult(
            argbPixels = argbPixels,
            width = width,
            height = height,
            bounds = bounds,
            resolutionM = resolutionM,
            validPixelCount = insideCount,
            aboveThresholdCount = aboveCount,
            coveragePercent = coverage
        )
    }

    fun renderIdwHeatmapBitmap(
        floor: Floor,
        samples: List<SurveySample>,
        targetSsids: List<String>? = null,
        bssidFilter: String? = null,
        bandFilter: String? = null,
        resolutionM: Double = 0.35,
        power: Double = 2.0,
        maxRadiusM: Double = 25.0,
        deadZoneDbm: Double = -70.0
    ): RenderedHeatmap {
        val grid = computeIdwGrid(
            floor = floor,
            samples = samples,
            targetSsids = targetSsids,
            bssidFilter = bssidFilter,
            bandFilter = bandFilter,
            resolutionM = resolutionM,
            power = power,
            maxRadiusM = maxRadiusM,
            deadZoneDbm = deadZoneDbm
        )
        val bitmap = Bitmap.createBitmap(grid.argbPixels, grid.width, grid.height, Bitmap.Config.ARGB_8888)
        return RenderedHeatmap(imageBitmap = bitmap.asImageBitmap(), grid = grid)
    }
}
