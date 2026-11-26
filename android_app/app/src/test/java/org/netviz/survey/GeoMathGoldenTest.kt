package org.netviz.survey

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.double
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.netviz.survey.geo.GeoMath
import org.netviz.survey.model.GeoAnchor
import org.netviz.survey.model.Point2D
import org.netviz.survey.model.Project
import org.netviz.survey.model.SCHEMA_VERSION
import org.netviz.survey.model.SurveySession
import java.io.File

class GeoMathGoldenTest {
    private fun findRepoFile(relativePath: String): File {
        var dir: File? = File(".").absoluteFile
        while (dir != null) {
            val candidate = File(dir, relativePath)
            if (candidate.isFile) return candidate
            dir = dir.parentFile
        }
        error("Could not locate $relativePath from working directory ${File(".").absolutePath}")
    }

    @Test
    fun goldenContractVectorsMatchPythonBitForBit() {
        val goldenFile = findRepoFile("tests/golden/geo_and_schema_vectors.json")
        val root = Json.parseToJsonElement(goldenFile.readText(Charsets.UTF_8)).jsonObject

        // 1. High-latitude Berlin site across 4 rotations
        val berlinGroups = root.getValue("berlin_projection_vectors").jsonArray
        assertEquals(4, berlinGroups.size)
        for (groupEl in berlinGroups) {
            val group = groupEl.jsonObject
            val anchorObj = group.getValue("anchor").jsonObject
            val anchor = GeoAnchor(
                originLat = anchorObj.getValue("origin_lat").jsonPrimitive.double,
                originLon = anchorObj.getValue("origin_lon").jsonPrimitive.double,
                rotationDeg = anchorObj.getValue("rotation_deg").jsonPrimitive.double
            )
            val (mLat, mLon) = GeoMath.metersPerDegree(anchor.originLat)
            assertEquals(group.getValue("m_lat").jsonPrimitive.double, mLat, 1e-9)
            assertEquals(group.getValue("m_lon").jsonPrimitive.double, mLon, 1e-9)

            for (caseEl in group.getValue("cases").jsonArray) {
                val c = caseEl.jsonObject
                val ptObj = c.getValue("local_m").jsonObject
                val pt = Point2D(
                    x = ptObj.getValue("x").jsonPrimitive.double,
                    y = ptObj.getValue("y").jsonPrimitive.double
                )
                val (lat, lon) = GeoMath.localMToLatLon(pt, anchor)
                assertEquals(c.getValue("lat").jsonPrimitive.double, lat, 1e-7)
                assertEquals(c.getValue("lon").jsonPrimitive.double, lon, 1e-7)

                val rt = GeoMath.latLonToLocalM(lat, lon, anchor)
                assertEquals(pt.x, rt.x, 1e-4)
                assertEquals(pt.y, rt.y, 1e-4)
            }
        }

        // 2. Equator & Southern Hemisphere sites + centroids
        val globalSites = root.getValue("global_projection_vectors").jsonArray
        assertEquals(2, globalSites.size)
        for (siteEl in globalSites) {
            val site = siteEl.jsonObject
            val anchorObj = site.getValue("anchor").jsonObject
            val anchor = GeoAnchor(
                originLat = anchorObj.getValue("origin_lat").jsonPrimitive.double,
                originLon = anchorObj.getValue("origin_lon").jsonPrimitive.double,
                rotationDeg = anchorObj.getValue("rotation_deg").jsonPrimitive.double
            )
            val (mLat, mLon) = GeoMath.metersPerDegree(anchor.originLat)
            assertEquals(site.getValue("m_lat").jsonPrimitive.double, mLat, 1e-9)
            assertEquals(site.getValue("m_lon").jsonPrimitive.double, mLon, 1e-9)

            val latlons = mutableListOf<Pair<Double, Double>>()
            for (caseEl in site.getValue("cases").jsonArray) {
                val c = caseEl.jsonObject
                val ptObj = c.getValue("local_m").jsonObject
                val pt = Point2D(
                    x = ptObj.getValue("x").jsonPrimitive.double,
                    y = ptObj.getValue("y").jsonPrimitive.double
                )
                val (lat, lon) = GeoMath.localMToLatLon(pt, anchor)
                latlons.add(lat to lon)
                assertEquals(c.getValue("lat").jsonPrimitive.double, lat, 1e-7)
                assertEquals(c.getValue("lon").jsonPrimitive.double, lon, 1e-7)

                val rt = GeoMath.latLonToLocalM(lat, lon, anchor)
                assertEquals(pt.x, rt.x, 1e-4)
                assertEquals(pt.y, rt.y, 1e-4)
            }
            val expectedCentroid = site.getValue("centroid").jsonArray
            val (cLat, cLon) = GeoMath.computeCentroid(latlons)
            assertEquals(expectedCentroid[0].jsonPrimitive.double, cLat, 1e-9)
            assertEquals(expectedCentroid[1].jsonPrimitive.double, cLon, 1e-9)
        }

        // 3. Slippy tile corner conversions
        val tileVectors = root.getValue("slippy_tile_vectors").jsonArray
        assertEquals(6, tileVectors.size)
        for (tileEl in tileVectors) {
            val t = tileEl.jsonObject
            val lat = t.getValue("lat").jsonPrimitive.double
            val lon = t.getValue("lon").jsonPrimitive.double
            val zoom = t.getValue("zoom").jsonPrimitive.int
            val (tx, ty) = GeoMath.latLonToTileXy(lat, lon, zoom)
            assertEquals(t.getValue("tile_x").jsonPrimitive.int, tx)
            assertEquals(t.getValue("tile_y").jsonPrimitive.int, ty)

            val bounds = GeoMath.tileXyToLatLonBounds(zoom, tx, ty)
            val expBounds = t.getValue("bounds").jsonObject
            assertEquals(expBounds.getValue("lat_n").jsonPrimitive.double, bounds.latN, 1e-7)
            assertEquals(expBounds.getValue("lon_w").jsonPrimitive.double, bounds.lonW, 1e-7)
            assertEquals(expBounds.getValue("lat_s").jsonPrimitive.double, bounds.latS, 1e-7)
            assertEquals(expBounds.getValue("lon_e").jsonPrimitive.double, bounds.lonE, 1e-7)
            assertTrue(lat in bounds.latS..bounds.latN)
            assertTrue(lon in bounds.lonW..bounds.lonE)
        }

        // 4. True North & compass heading screen angles
        val compassVectors = root.getValue("compass_angle_vectors").jsonArray
        assertEquals(6, compassVectors.size)
        for (compEl in compassVectors) {
            val c = compEl.jsonObject
            val anchorRot = c.getValue("anchor_rot_deg").jsonPrimitive.double
            val viewRot = c.getValue("view_rot_deg").jsonPrimitive.double
            val azimuth = c.getValue("azimuth_deg").jsonPrimitive.double

            val tn = GeoMath.trueNorthScreenCwDeg(viewRot, anchorRot)
            val br = GeoMath.bearingScreenCwDeg(viewRot, anchorRot, azimuth)
            assertEquals(c.getValue("true_north_screen_cw_deg").jsonPrimitive.double, tn, 1e-9)
            assertEquals(c.getValue("bearing_screen_cw_deg").jsonPrimitive.double, br, 1e-9)
        }

        // 6. Schema vectors (v2.1.0, v2.0.0 legacy, SurveySession) + 5. Room hit-testing & bearing
        val schemaObj = root.getValue("schema_vectors").jsonObject
        val v21 = Project.fromJson(schemaObj.getValue("project_v2_1_0").toString())
        val v20 = Project.fromJson(schemaObj.getValue("project_v2_0_0_legacy").toString())
        val session = SurveySession.fromJson(schemaObj.getValue("survey_session").toString())

        assertEquals(SCHEMA_VERSION, v21.schemaVersion)
        assertEquals(2, v21.buildings.size)
        assertTrue(v21.components.size >= 9)
        assertEquals("2.0.0", v20.schemaVersion)
        assertEquals("Legacy v2.0.0 Campus", v20.name)
        assertEquals("Lobby", v20.buildings.first().floors.first().rooms.first().name)
        assertEquals(0.0, v20.buildings.first().floors.first().editorViewRotationDeg, 1e-9)
        assertEquals(3.2, v20.buildings.first().floorHeightM, 1e-9)
        assertEquals(25, session.samples.size)

        // Verify Project round-trip & helper methods
        val roundTripped = Project.fromJson(v21.toJson())
        assertEquals(v21.id, roundTripped.id)
        assertEquals(v21.components.size, roundTripped.components.size)
        assertEquals(v21.cables.size, roundTripped.cables.size)
        assertTrue(roundTripped.getTargetSsids().contains("Corp-WiFi"))

        val ap = roundTripped.components.firstOrNull { it.macAddress.isNotBlank() }
        assertNotNull(ap)
        val normalizedVariant = ap!!.macAddress.replace(":", "-").lowercase()
        assertEquals(ap.id, roundTripped.findComponentByMac(normalizedVariant)?.id)
        assertNull(roundTripped.findComponentByMac(""))

        val firstCable = roundTripped.cables.first()
        assertTrue(roundTripped.isPortOccupied(firstCable.sourceComponentId, firstCable.sourcePortId))
        assertFalse(
            roundTripped.isPortOccupied(
                firstCable.sourceComponentId,
                firstCable.sourcePortId,
                excludeCableId = firstCable.id
            )
        )
        assertNotNull(
            roundTripped.getPortConnection(firstCable.sourceComponentId, firstCable.sourcePortId)
        )

        // 5. Room hit-testing & off-building distance + geographic cardinal bearing
        val floor0 = v21.buildings.first().floors.first()
        val rab = root.getValue("room_and_bearing_vectors").jsonObject
        val anchorRot = rab.getValue("anchor_rotation_deg").jsonPrimitive.double
        val targetObj = rab.getValue("target_center_m").jsonObject
        val target = Point2D(
            x = targetObj.getValue("x").jsonPrimitive.double,
            y = targetObj.getValue("y").jsonPrimitive.double
        )

        for (caseEl in rab.getValue("cases").jsonArray) {
            val c = caseEl.jsonObject
            val ptObj = c.getValue("pt_m").jsonObject
            val pt = Point2D(
                x = ptObj.getValue("x").jsonPrimitive.double,
                y = ptObj.getValue("y").jsonPrimitive.double
            )
            assertEquals(
                c.getValue("inside_floor").jsonPrimitive.boolean,
                GeoMath.pointInPolygon(pt, floor0.outerPolygonM)
            )
            val room = GeoMath.findRoomAtPoint(pt, floor0)
            assertEquals(c.getValue("room_name").jsonPrimitive.contentOrNull, room?.name)

            val (dist, cardinal) = GeoMath.distanceAndCardinalBearing(pt, target, anchorRot)
            assertEquals(c.getValue("to_center_distance_m").jsonPrimitive.double, dist, 1e-6)
            assertEquals(c.getValue("to_center_cardinal").jsonPrimitive.content, cardinal)
        }
    }

    @Test
    fun bundledSampleCampusAssetParsesCleanly() {
        val assetFile = findRepoFile("android_app/app/src/main/assets/sample_campus.json")
        val project = Project.fromJson(assetFile.readText(Charsets.UTF_8))
        assertEquals(SCHEMA_VERSION, project.schemaVersion)
        assertEquals(2, project.buildings.size)
        assertEquals(1, project.surveys.size)
        assertEquals(25, project.surveys.first().samples.size)

        val bldgA = project.buildings.first()
        val floor0 = bldgA.floors.first()
        assertEquals(352.0, GeoMath.polygonAreaM2(floor0.outerPolygonM), 1e-6)
        val anchor = bldgA.geoAnchor!!
        val projectedLatLons = GeoMath.floorPolygonToLatLon(floor0.outerPolygonM, anchor)
        val backM = GeoMath.polygonLatLonToLocalM(projectedLatLons, anchor)
        floor0.outerPolygonM.zip(backM).forEach { (orig, rt) ->
            assertEquals(orig.x, rt.x, 1e-6)
            assertEquals(orig.y, rt.y, 1e-6)
        }
    }
}
