package org.netviz.survey

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.netviz.survey.geo.HeatmapEngine
import org.netviz.survey.hardware.BurstAverager
import org.netviz.survey.hardware.CalibrationMath
import org.netviz.survey.hardware.GpsSmoother
import org.netviz.survey.hardware.ScanThrottleTracker
import org.netviz.survey.model.Floor
import org.netviz.survey.model.Point2D
import org.netviz.survey.model.PositionSource
import org.netviz.survey.model.SurveySample
import org.netviz.survey.model.SurveySession
import org.netviz.survey.model.WifiReading
import java.io.File
import java.nio.file.Files
import java.nio.file.StandardCopyOption

class HeatmapAndScannerTest {
    private fun findRepoFile(relativePath: String): File {
        var dir: File? = File(".").absoluteFile
        while (dir != null) {
            val candidate = File(dir, relativePath)
            if (candidate.isFile) return candidate
            dir = dir.parentFile
        }
        error("Could not locate $relativePath")
    }

    @Test
    fun idwHeatmapNonFlippedEnuAlignmentAndStrictClipping() {
        val floor = Floor(
            id = "f-rect",
            name = "GF",
            outerPolygonM = listOf(
                Point2D(0.0, 0.0),
                Point2D(20.0, 0.0),
                Point2D(20.0, 10.0),
                Point2D(0.0, 10.0)
            )
        )
        val samples = listOf(
            SurveySample(
                timestampUtc = "2026-11-26T10:00:00Z",
                buildingId = "b1",
                floorId = "f-rect",
                posM = Point2D(10.0, 2.0), // Southern half (near y=0)
                source = PositionSource.MANUAL,
                readings = listOf(
                    WifiReading("Corp-WiFi", "00:11:22:33:44:01", -32, 5180),
                    WifiReading("Guest-WiFi", "00:11:22:33:44:02", -86, 2412)
                )
            ),
            SurveySample(
                timestampUtc = "2026-11-26T10:01:00Z",
                buildingId = "b1",
                floorId = "f-rect",
                posM = Point2D(10.0, 8.0), // Northern half (near y=10)
                source = PositionSource.MANUAL,
                readings = listOf(
                    WifiReading("Corp-WiFi", "00:11:22:33:44:01", -90, 5180)
                )
            )
        )

        val grid = HeatmapEngine.computeIdwGrid(
            floor = floor,
            samples = samples,
            targetSsids = listOf("Corp-WiFi"),
            resolutionM = 0.5,
            deadZoneDbm = -70.0
        )

        assertEquals(40, grid.width)
        assertEquals(20, grid.height)
        assertEquals(800, grid.validPixelCount)
        assertTrue(grid.coveragePercent > 0.0 && grid.coveragePercent < 100.0)

        // Row 0 = max_y (North), so hot spot at y=2.0 MUST be in bottom half (row=15..16)
        val hotArgb = grid.pixelAt(col = 20, row = 16)
        val hotAlpha = (hotArgb ushr 24) and 0xFF
        val hotRed = (hotArgb ushr 16) and 0xFF
        val hotGreen = (hotArgb ushr 8) and 0xFF
        assertTrue(hotAlpha > 0)
        assertTrue("Expected green peak at (10,2), got R=$hotRed G=$hotGreen", hotGreen > hotRed)

        // Cold spot at y=8.0 MUST be in top half (row=3..4)
        val coldArgb = grid.pixelAt(col = 20, row = 4)
        val coldAlpha = (coldArgb ushr 24) and 0xFF
        val coldRed = (coldArgb ushr 16) and 0xFF
        val coldGreen = (coldArgb ushr 8) and 0xFF
        assertTrue(coldAlpha > 0)
        assertTrue("Expected red cold spot at (10,8), got R=$coldRed G=$coldGreen", coldRed > coldGreen)

        // Band & BSSID filter test
        val filtered24 = HeatmapEngine.computeIdwGrid(
            floor = floor,
            samples = samples,
            bandFilter = "2.4",
            bssidFilter = "00-11-22-33-44-02",
            resolutionM = 0.5,
            deadZoneDbm = -70.0
        )
        assertEquals(0.0, filtered24.coveragePercent, 1e-6)

        // L-shaped floor strict clipping outside polygon
        val lFloor = Floor(
            id = "f-l",
            name = "L-Floor",
            outerPolygonM = listOf(
                Point2D(0.0, 0.0),
                Point2D(24.0, 0.0),
                Point2D(24.0, 10.0),
                Point2D(14.0, 10.0),
                Point2D(14.0, 18.0),
                Point2D(0.0, 18.0)
            )
        )
        val lSample = SurveySample(
            timestampUtc = "2026-11-26T10:02:00Z",
            buildingId = "b1",
            floorId = "f-l",
            posM = Point2D(10.0, 5.0),
            source = PositionSource.MANUAL,
            readings = listOf(WifiReading("Corp-WiFi", "00:11:22:33:44:01", -45, 5180))
        )
        val lGrid = HeatmapEngine.computeIdwGrid(lFloor, listOf(lSample), resolutionM = 0.5)
        // Top-right corner (x~23, y~17) is inside L-notch -> MUST have alpha == 0
        val outsideArgb = lGrid.pixelAt(col = lGrid.width - 2, row = 1)
        assertEquals(0, (outsideArgb ushr 24) and 0xFF)
    }

    @Test
    fun scanThrottleTrackerDetectsStagnantHardwareTimestampsOver15s() {
        val tracker = ScanThrottleTracker(staleThresholdMs = 15_000L)
        tracker.reset(nowWallMs = 1_000L)

        assertFalse(tracker.recordScanResults(maxHardwareTimestampUs = 500_000_000L, nowWallMs = 1_000L))
        assertFalse(tracker.recordScanResults(maxHardwareTimestampUs = 500_000_000L, nowWallMs = 15_500L))
        assertTrue(tracker.recordScanResults(maxHardwareTimestampUs = 500_000_000L, nowWallMs = 16_500L))

        // Fresh hardware timestamp clears throttling state
        assertFalse(tracker.recordScanResults(maxHardwareTimestampUs = 505_000_000L, nowWallMs = 17_000L))
    }

    @Test
    fun burstAveragerNormalizesMacsAndAveragesRssi() {
        val burst1 = listOf(
            WifiReading("Corp-WiFi", "00:11:22:33:44:55", -50, 5180),
            WifiReading("Guest-WiFi", "AA:BB:CC:DD:EE:01", -70, 2412)
        )
        val burst2 = listOf(
            WifiReading("Corp-WiFi", "00-11-22-33-44-55", -56, 5180),
            WifiReading("Guest-WiFi", "aa:bb:cc:dd:ee:01", -73, 2412)
        )
        val burst3 = listOf(
            WifiReading("Corp-WiFi", "001122334455", -53, 5180),
            WifiReading("Guest-WiFi", "aa.bb.cc.dd.ee.01", -76, 2412)
        )

        val averaged = BurstAverager.averageReadings(listOf(burst1, burst2, burst3))
        assertEquals(2, averaged.size)
        assertEquals("Corp-WiFi", averaged[0].ssid)
        assertEquals(-53, averaged[0].rssiDbm)
        assertEquals("Guest-WiFi", averaged[1].ssid)
        assertEquals(-73, averaged[1].rssiDbm)
    }

    @Test
    fun calibrationOffsetAndGpsSmoothingAreExact() {
        val rawProj = Point2D(12.4, 8.1)
        val knownSpot = Point2D(15.0, 10.0)
        val offset = CalibrationMath.computeOffset(knownSpot, rawProj)
        val calibrated = CalibrationMath.applyOffset(rawProj, offset)
        assertEquals(knownSpot.x, calibrated.x, 1e-9)
        assertEquals(knownSpot.y, calibrated.y, 1e-9)

        val smoothed = GpsSmoother.smooth(
            previous = 52.5200 to 13.4050,
            rawLat = 52.5204,
            rawLon = 13.4054,
            accuracyM = 6.0f // alpha = 3/6 = 0.5
        )
        assertEquals(52.5202, smoothed.first, 1e-9)
        assertEquals(13.4052, smoothed.second, 1e-9)
    }

    @Test
    fun atomicDraftRoundTripAndStrictForegroundManifestCheck() {
        val tempDir = Files.createTempDirectory("netviz_draft_test").toFile()
        try {
            val session = SurveySession(
                name = "Atomic Draft",
                createdUtc = "2026-11-26T00:00:00Z",
                deviceModel = "UnitTest",
                samples = listOf(
                    SurveySample(
                        timestampUtc = "2026-11-26T00:00:01Z",
                        buildingId = "b1",
                        floorId = "f1",
                        posM = Point2D(3.0, 4.0),
                        source = PositionSource.GPS_CALIBRATED,
                        calibrationOffsetM = Point2D(1.0, -0.5),
                        readings = listOf(WifiReading("Corp-WiFi", "00:11:22:33:44:55", -58, 5180))
                    )
                )
            )
            val tmpFile = File(tempDir, "draft.tmp")
            val activeFile = File(tempDir, "active_survey.json")
            tmpFile.writeText(session.toJson(), Charsets.UTF_8)
            Files.move(tmpFile.toPath(), activeFile.toPath(), StandardCopyOption.REPLACE_EXISTING)

            val restored = SurveySession.fromJson(activeFile.readText(Charsets.UTF_8))
            assertEquals(session.id, restored.id)
            assertEquals(1, restored.samples.size)
            assertEquals(PositionSource.GPS_CALIBRATED, restored.samples.first().source)
        } finally {
            tempDir.deleteRecursively()
        }

        val manifestText = findRepoFile("android_app/app/src/main/AndroidManifest.xml")
            .readText(Charsets.UTF_8)
        assertFalse(manifestText.contains("ACCESS_BACKGROUND_LOCATION"))
        assertFalse(manifestText.contains("FOREGROUND_SERVICE"))
        assertFalse(manifestText.contains("<service"))
        assertTrue(manifestText.contains("network_security_config"))
        assertTrue(manifestText.contains("FileProvider"))
    }
}
