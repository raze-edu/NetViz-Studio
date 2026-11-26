package org.netviz.survey.geo

import org.netviz.survey.model.Floor
import org.netviz.survey.model.GeoAnchor
import org.netviz.survey.model.Point2D
import org.netviz.survey.model.Room
import kotlin.math.PI
import kotlin.math.asinh
import kotlin.math.atan
import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.floor as mathFloor
import kotlin.math.hypot
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sin
import kotlin.math.sinh
import kotlin.math.tan

object GeoMath {
    val CARDINAL_DIRECTIONS: List<String> = listOf("N", "NE", "E", "SE", "S", "SW", "W", "NW")
    private const val MAX_MERCATOR_LAT: Double = 85.05112878

    data class Bounds2D(
        val minX: Double,
        val minY: Double,
        val maxX: Double,
        val maxY: Double
    )

    data class TileBounds(
        val latN: Double,
        val lonW: Double,
        val latS: Double,
        val lonE: Double
    )

    data class TileCoord(
        val z: Int,
        val x: Int,
        val y: Int
    )

    private fun mod360(deg: Double): Double {
        val m = ((deg % 360.0) + 360.0) % 360.0
        return if (m >= 360.0 || m == -0.0) 0.0 else m
    }

    fun metersPerDegree(originLat: Double): Pair<Double, Double> {
        val phi = Math.toRadians(originLat)
        val mLat = 111132.92 - 559.82 * cos(2.0 * phi) + 1.175 * cos(4.0 * phi)
        val mLon = 111412.84 * cos(phi) - 93.5 * cos(3.0 * phi)
        return mLat to mLon
    }

    fun localMToLatLon(pt: Point2D, anchor: GeoAnchor): Pair<Double, Double> {
        val (mLat, mLon) = metersPerDegree(anchor.originLat)
        val theta = Math.toRadians(anchor.rotationDeg)
        val east = pt.x * cos(theta) + pt.y * sin(theta)
        val north = -pt.x * sin(theta) + pt.y * cos(theta)
        val lat = anchor.originLat + north / mLat
        val lon = anchor.originLon + east / mLon
        return lat to lon
    }

    fun latLonToLocalM(lat: Double, lon: Double, anchor: GeoAnchor): Point2D {
        val (mLat, mLon) = metersPerDegree(anchor.originLat)
        val theta = Math.toRadians(anchor.rotationDeg)
        val east = (lon - anchor.originLon) * mLon
        val north = (lat - anchor.originLat) * mLat
        val x = east * cos(theta) - north * sin(theta)
        val y = east * sin(theta) + north * cos(theta)
        return Point2D(x = x, y = y)
    }

    fun bearingScreenCwDeg(
        viewRotDeg: Double,
        anchorRotDeg: Double,
        azimuthDeg: Double = 0.0
    ): Double = mod360(viewRotDeg - anchorRotDeg + azimuthDeg)

    fun trueNorthScreenCwDeg(viewRotDeg: Double, anchorRotDeg: Double = 0.0): Double =
        bearingScreenCwDeg(viewRotDeg, anchorRotDeg, 0.0)

    fun computeCentroid(latlons: List<Pair<Double, Double>>): Pair<Double, Double> {
        require(latlons.isNotEmpty()) { "Cannot compute centroid of empty coordinate list" }
        val pts = if (latlons.size > 1 && latlons.first() == latlons.last()) {
            latlons.dropLast(1)
        } else {
            latlons
        }
        val n = pts.size.toDouble()
        val avgLat = pts.sumOf { it.first } / n
        val avgLon = pts.sumOf { it.second } / n
        return avgLat to avgLon
    }

    fun polygonLatLonToLocalM(
        latlons: List<Pair<Double, Double>>,
        anchor: GeoAnchor
    ): List<Point2D> = latlons.map { (lat, lon) -> latLonToLocalM(lat, lon, anchor) }

    fun floorPolygonToLatLon(
        ptsM: List<Point2D>,
        anchor: GeoAnchor
    ): List<Pair<Double, Double>> = ptsM.map { pt -> localMToLatLon(pt, anchor) }

    fun polygonAreaM2(ptsM: List<Point2D>): Double {
        val n = ptsM.size
        if (n < 3) return 0.0
        var total = 0.0
        for (i in 0 until n) {
            val p1 = ptsM[i]
            val p2 = ptsM[(i + 1) % n]
            total += p1.x * p2.y - p2.x * p1.y
        }
        return 0.5 * kotlin.math.abs(total)
    }

    fun polygonBounds(ptsM: List<Point2D>): Bounds2D {
        if (ptsM.isEmpty()) return Bounds2D(0.0, 0.0, 0.0, 0.0)
        var minX = Double.POSITIVE_INFINITY
        var minY = Double.POSITIVE_INFINITY
        var maxX = Double.NEGATIVE_INFINITY
        var maxY = Double.NEGATIVE_INFINITY
        for (pt in ptsM) {
            if (pt.x < minX) minX = pt.x
            if (pt.y < minY) minY = pt.y
            if (pt.x > maxX) maxX = pt.x
            if (pt.y > maxY) maxY = pt.y
        }
        return Bounds2D(minX, minY, maxX, maxY)
    }

    fun pointToSegmentDistance(pt: Point2D, segA: Point2D, segB: Point2D): Double {
        val dx = segB.x - segA.x
        val dy = segB.y - segA.y
        val lenSq = dx * dx + dy * dy
        if (lenSq <= 1e-18) {
            return hypot(pt.x - segA.x, pt.y - segA.y)
        }
        val t = (((pt.x - segA.x) * dx + (pt.y - segA.y) * dy) / lenSq).coerceIn(0.0, 1.0)
        val projX = segA.x + t * dx
        val projY = segA.y + t * dy
        return hypot(pt.x - projX, pt.y - projY)
    }

    fun pointInPolygon(
        ptM: Point2D,
        polygonM: List<Point2D>,
        boundaryTol: Double = 1e-6
    ): Boolean {
        val n = polygonM.size
        if (n < 3) return false

        for (i in 0 until n) {
            val a = polygonM[i]
            val b = polygonM[(i + 1) % n]
            if (pointToSegmentDistance(ptM, a, b) <= boundaryTol) {
                return true
            }
        }

        var inside = false
        val px = ptM.x
        val py = ptM.y
        var j = n - 1
        for (i in 0 until n) {
            val xi = polygonM[i].x
            val yi = polygonM[i].y
            val xj = polygonM[j].x
            val yj = polygonM[j].y
            val intersects = (yi > py) != (yj > py) &&
                (px < (xj - xi) * (py - yi) / (yj - yi) + xi)
            if (intersects) {
                inside = !inside
            }
            j = i
        }
        return inside
    }

    fun findRoomAtPoint(ptM: Point2D, floor: Floor): Room? =
        floor.rooms.firstOrNull { room -> pointInPolygon(ptM, room.polygonM) }

    fun distanceAndCardinalBearing(
        fromPt: Point2D,
        toPt: Point2D,
        anchorRotationDeg: Double = 0.0
    ): Pair<Double, String> {
        val dx = toPt.x - fromPt.x
        val dy = toPt.y - fromPt.y
        val dist = hypot(dx, dy)
        val theta = Math.toRadians(anchorRotationDeg)
        val east = dx * cos(theta) + dy * sin(theta)
        val north = -dx * sin(theta) + dy * cos(theta)
        val azimuthDeg = mod360(Math.toDegrees(atan2(east, north)))
        val idx = (((azimuthDeg + 22.5) / 45.0).toInt()) % 8
        return dist to CARDINAL_DIRECTIONS[idx]
    }

    fun latLonToTileXy(lat: Double, lon: Double, zoom: Int): Pair<Int, Int> {
        val clampedLat = lat.coerceIn(-MAX_MERCATOR_LAT, MAX_MERCATOR_LAT)
        val n = 1 shl zoom
        val latRad = Math.toRadians(clampedLat)
        val txRaw = mathFloor((lon + 180.0) / 360.0 * n).toInt()
        val tyRaw = mathFloor((1.0 - asinh(tan(latRad)) / PI) / 2.0 * n).toInt()
        val tx = txRaw.coerceIn(0, n - 1)
        val ty = tyRaw.coerceIn(0, n - 1)
        return tx to ty
    }

    fun tileXyToLatLonBounds(z: Int, x: Int, y: Int): TileBounds {
        val n = (1 shl z).toDouble()
        val lonW = x / n * 360.0 - 180.0
        val lonE = (x + 1) / n * 360.0 - 180.0
        val latN = Math.toDegrees(atan(sinh(PI * (1.0 - 2.0 * y / n))))
        val latS = Math.toDegrees(atan(sinh(PI * (1.0 - 2.0 * (y + 1) / n))))
        return TileBounds(latN = latN, lonW = lonW, latS = latS, lonE = lonE)
    }

    fun boundingBoxTiles(
        minLat: Double,
        minLon: Double,
        maxLat: Double,
        maxLon: Double,
        zooms: List<Int>
    ): List<TileCoord> {
        val tiles = mutableListOf<TileCoord>()
        for (z in zooms) {
            val (xNw, yNw) = latLonToTileXy(maxLat, minLon, z)
            val (xSe, ySe) = latLonToTileXy(minLat, maxLon, z)
            for (tx in min(xNw, xSe)..max(xNw, xSe)) {
                for (ty in min(yNw, ySe)..max(yNw, ySe)) {
                    tiles.add(TileCoord(z = z, x = tx, y = ty))
                }
            }
        }
        return tiles
    }
}
