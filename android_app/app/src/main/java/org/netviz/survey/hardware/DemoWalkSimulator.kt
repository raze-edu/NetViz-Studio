package org.netviz.survey.hardware

import org.netviz.survey.geo.GeoMath
import org.netviz.survey.model.ComponentType
import org.netviz.survey.model.Floor
import org.netviz.survey.model.Point2D
import org.netviz.survey.model.Project
import org.netviz.survey.model.WifiReading
import kotlin.math.atan2
import kotlin.math.hypot
import kotlin.math.log10
import kotlin.math.max
import kotlin.math.roundToInt
import kotlin.random.Random

class DemoWalkSimulator {
    private val rng = Random(42)

    fun buildWalkWaypoints(floor: Floor): List<Point2D> {
        val roomCenters = floor.rooms.mapNotNull { room ->
            if (room.polygonM.size < 3) {
                null
            } else {
                val cx = room.polygonM.map { it.x }.average()
                val cy = room.polygonM.map { it.y }.average()
                Point2D(cx, cy)
            }
        }
        if (roomCenters.size >= 2) {
            return densifyWaypoints(roomCenters + roomCenters.first(), stepM = 1.6)
        }
        val b = GeoMath.polygonBounds(floor.outerPolygonM)
        val cx = 0.5 * (b.minX + b.maxX)
        val cy = 0.5 * (b.minY + b.maxY)
        val dx = max((b.maxX - b.minX) * 0.3, 1.0)
        val dy = max((b.maxY - b.minY) * 0.3, 1.0)
        val loop = listOf(
            Point2D(cx - dx, cy - dy),
            Point2D(cx + dx, cy - dy),
            Point2D(cx + dx, cy + dy),
            Point2D(cx - dx, cy + dy),
            Point2D(cx - dx, cy - dy)
        )
        return densifyWaypoints(loop, stepM = 1.6)
    }

    fun synthesizeReadingsAt(
        project: Project,
        floor: Floor,
        posM: Point2D
    ): List<WifiReading> {
        val floorAps = project.components.filter {
            it.componentType == ComponentType.ACCESS_POINT &&
                it.physicalFloorId == floor.id &&
                it.physicalPosM != null
        }
        val activeAps = floorAps.ifEmpty {
            project.components.filter { it.componentType == ComponentType.ACCESS_POINT }
        }

        if (activeAps.isEmpty()) {
            return listOf(
                WifiReading("Corp-WiFi", "00:11:22:33:44:01", -56 + rng.nextInt(-3, 4), 5180),
                WifiReading("Guest-WiFi", "00:11:22:33:44:02", -62 + rng.nextInt(-3, 4), 2437)
            )
        }

        val wallSegments = collectRoomWalls(floor)
        val out = mutableListOf<WifiReading>()

        for ((apIdx, ap) in activeAps.withIndex()) {
            val apPos = ap.physicalPosM ?: Point2D(4.0 + apIdx * 6.0, 4.0)
            val dist = max(hypot(posM.x - apPos.x, posM.y - apPos.y), 0.5)
            var wallLoss = 0.0
            for ((w1, w2, atten) in wallSegments) {
                if (segmentsIntersect(apPos, posM, w1, w2)) {
                    wallLoss += atten
                }
            }
            if (ap.physicalFloorId != null && ap.physicalFloorId != floor.id) {
                wallLoss += 15.0
            }
            val ssids = ap.ssids.ifEmpty { listOf("Corp-WiFi") }
            val freqs = ap.frequenciesGhz.ifEmpty { listOf(5.0, 2.4) }
            val baseMac = ap.macAddress.ifBlank {
                "00:11:22:33:44:%02X".format(apIdx + 1)
            }

            for ((fIdx, ghz) in freqs.withIndex()) {
                val freqMhz = when {
                    ghz < 3.0 -> 2412 + (apIdx % 3) * 25
                    ghz < 5.9 -> 5180 + (apIdx % 4) * 20
                    else -> 5955 + (apIdx % 4) * 40
                }
                val bandOffset = if (ghz < 3.0) 3.0 else if (ghz > 5.9) -4.0 else 0.0
                val jitter = rng.nextDouble(-2.0, 2.0)
                val rssi = (-35.0 - 20.0 * log10(dist) - wallLoss + bandOffset + jitter)
                    .roundToInt()
                    .coerceIn(-98, -28)
                val ssid = ssids[fIdx % ssids.size]
                val bssid = deriveBssid(baseMac, fIdx)
                out.add(WifiReading(ssid = ssid, bssid = bssid, rssiDbm = rssi, frequencyMhz = freqMhz))
            }
        }
        return out.sortedByDescending { it.rssiDbm }
    }

    fun headingAzimuthDeg(
        fromPt: Point2D,
        toPt: Point2D,
        anchorRotationDeg: Double
    ): Float {
        val dx = toPt.x - fromPt.x
        val dy = toPt.y - fromPt.y
        if (hypot(dx, dy) < 1e-4) return 0f
        val theta = Math.toRadians(anchorRotationDeg)
        val east = dx * kotlin.math.cos(theta) + dy * kotlin.math.sin(theta)
        val north = -dx * kotlin.math.sin(theta) + dy * kotlin.math.cos(theta)
        val deg = Math.toDegrees(atan2(east, north))
        return (((deg % 360.0) + 360.0) % 360.0).toFloat()
    }

    private fun densifyWaypoints(loop: List<Point2D>, stepM: Double): List<Point2D> {
        if (loop.size < 2) return loop
        val pts = mutableListOf<Point2D>()
        for (i in 0 until loop.size - 1) {
            val a = loop[i]
            val b = loop[i + 1]
            val dist = hypot(b.x - a.x, b.y - a.y)
            val steps = max(1, (dist / stepM).roundToInt())
            for (s in 0 until steps) {
                val t = s.toDouble() / steps
                pts.add(Point2D(a.x + t * (b.x - a.x), a.y + t * (b.y - a.y)))
            }
        }
        return pts
    }

    private fun collectRoomWalls(floor: Floor): List<Triple<Point2D, Point2D, Double>> {
        val map = LinkedHashMap<String, Triple<Point2D, Point2D, Double>>()
        for (room in floor.rooms) {
            val poly = room.polygonM
            if (poly.size < 2) continue
            for (i in poly.indices) {
                val p1 = poly[i]
                val p2 = poly[(i + 1) % poly.size]
                val k1 = "${(p1.x * 100).roundToInt()},${(p1.y * 100).roundToInt()}"
                val k2 = "${(p2.x * 100).roundToInt()},${(p2.y * 100).roundToInt()}"
                val key = if (k1 <= k2) "$k1|$k2" else "$k2|$k1"
                val prev = map[key]
                if (prev == null || room.wallAttenuationDb > prev.third) {
                    map[key] = Triple(p1, p2, room.wallAttenuationDb)
                }
            }
        }
        return map.values.toList()
    }

    private fun segmentsIntersect(a1: Point2D, a2: Point2D, b1: Point2D, b2: Point2D): Boolean {
        fun orient(p: Point2D, q: Point2D, r: Point2D): Double =
            (q.x - p.x) * (r.y - p.y) - (q.y - p.y) * (r.x - p.x)
        val o1 = orient(a1, a2, b1)
        val o2 = orient(a1, a2, b2)
        val o3 = orient(b1, b2, a1)
        val o4 = orient(b1, b2, a2)
        return (o1 > 0) != (o2 > 0) && (o3 > 0) != (o4 > 0)
    }

    private fun deriveBssid(baseMac: String, radioIndex: Int): String {
        if (radioIndex == 0) return baseMac
        val parts = baseMac.split(":")
        if (parts.size != 6) return baseMac
        val last = parts[5].toIntOrNull(16) ?: return baseMac
        val next = (last + radioIndex) and 0xFF
        return (parts.dropLast(1) + "%02X".format(next)).joinToString(":")
    }
}
