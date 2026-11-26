package org.netviz.survey.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import org.netviz.survey.geo.GeoMath
import org.netviz.survey.model.Point2D
import org.netviz.survey.state.SurveyUiState
import org.netviz.survey.ui.theme.NetVizColors
import kotlin.math.hypot

@Composable
fun GpsDiagnosticsSheet(
    state: SurveyUiState,
    onCalibrateHere: () -> Unit,
    onResetCalibration: () -> Unit,
    onToggleAccuracyGate: (Boolean) -> Unit,
    onDismiss: () -> Unit
) {
    val fix = state.liveRawGpsFix
    val anchor = state.activeBuilding?.geoAnchor
    val floor = state.activeFloor
    val cal = state.activeCalibrationOffset
    val fixAgeSec = fix?.let { ((System.currentTimeMillis() - it.timestampMs).coerceAtLeast(0L) / 1000.0) }

    val centerBearing = if (state.liveLocalPosM != null && floor != null && anchor != null) {
        val b = GeoMath.polygonBounds(floor.outerPolygonM)
        val centerPt = Point2D(0.5 * (b.minX + b.maxX), 0.5 * (b.minY + b.maxY))
        val (d, dir) = GeoMath.distanceAndCardinalBearing(state.liveLocalPosM, centerPt, anchor.rotationDeg)
        "${"%.1f".format(d)} m $dir"
    } else {
        "--"
    }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("GPS & GeoAnchor Diagnostics") },
        text = {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(6.dp)
            ) {
                DiagRow("Provider", state.activeProviderLabel)
                DiagRow("Raw Lat / Lon", fix?.let { "${"%.6f".format(it.rawLat)}, ${"%.6f".format(it.rawLon)}" } ?: "--")
                DiagRow("Accuracy", fix?.let { "±${"%.1f".format(it.accuracyM)} m" } ?: "--")
                DiagRow("Fix Age", fixAgeSec?.let { "${"%.1f".format(it)} s" } ?: "--")
                DiagRow(
                    "Compass Azimuth",
                    state.trueNorthAzimuthDeg?.let { "${"%.0f".format(it)}° True North" } ?: "--"
                )
                HorizontalDivider()
                DiagRow(
                    "GeoAnchor Origin",
                    anchor?.let { "${"%.6f".format(it.originLat)}, ${"%.6f".format(it.originLon)}" } ?: "Unanchored"
                )
                DiagRow("Anchor Rotation", anchor?.let { "${"%.1f".format(it.rotationDeg)}° CW" } ?: "--")
                DiagRow(
                    "Local (X, Y)",
                    state.liveLocalPosM?.let { "(${"%.2f".format(it.x)} m, ${"%.2f".format(it.y)} m)" } ?: "--"
                )
                DiagRow("To Building Center", centerBearing)
                DiagRow(
                    "Calibration Offset",
                    cal?.let { "(${"%.2f".format(it.x)}, ${"%.2f".format(it.y)}) m [${"%.1f".format(hypot(it.x, it.y))}m]" } ?: "None"
                )
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text("Accuracy Gate (≤ ${state.maxAccuracyGateM.toInt()} m)", fontSize = 12.sp)
                    Switch(checked = state.enforceAccuracyGate, onCheckedChange = onToggleAccuracyGate)
                }
                Button(onClick = onCalibrateHere, modifier = Modifier.fillMaxWidth()) {
                    Text("Calibrate GPS Here")
                }
                if (cal != null) {
                    OutlinedButton(onClick = onResetCalibration, modifier = Modifier.fillMaxWidth()) {
                        Text("Reset Calibration")
                    }
                }
            }
        },
        confirmButton = {
            TextButton(onClick = onDismiss) { Text("Close") }
        }
    )
}

@Composable
private fun DiagRow(label: String, value: String) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween
    ) {
        Text(label, fontSize = 12.sp, color = NetVizColors.TextMuted)
        Text(value, fontSize = 12.sp, fontWeight = FontWeight.Medium)
    }
}
