package org.netviz.survey.ui

import android.graphics.Bitmap
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.RectF
import android.graphics.Typeface
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.detectTransformGestures
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.asAndroidBitmap
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.withTransform
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import org.netviz.survey.geo.GeoMath
import org.netviz.survey.geo.HeatmapEngine
import org.netviz.survey.model.Building
import org.netviz.survey.model.Component
import org.netviz.survey.model.ComponentType
import org.netviz.survey.model.Floor
import org.netviz.survey.model.Point2D
import org.netviz.survey.model.SurveySample
import org.netviz.survey.ui.theme.NetVizColors
import kotlin.math.PI
import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.hypot
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt
import kotlin.math.sin

private const val PPM = 40f

@Composable
fun FloorPlanCanvas(
    building: Building?,
    floor: Floor?,
    components: List<Component>,
    samples: List<SurveySample>,
    renderedHeatmap: HeatmapEngine.RenderedHeatmap?,
    showHeatmap: Boolean,
    showMapBackdrop: Boolean,
    hasTilesDb: Boolean,
    selectedTapPtM: Point2D?,
    liveLocalPosM: Point2D?,
    liveAccuracyM: Float?,
    trueNorthAzimuthDeg: Float?,
    centerOnGpsNonce: Int,
    resetViewNonce: Int,
    readTile: (provider: String, z: Int, x: Int, y: Int) -> Bitmap?,
    onTapPointM: (Point2D) -> Unit,
    onLongPressSample: (SurveySample) -> Unit,
    modifier: Modifier = Modifier
) {
    var canvasSize by remember { mutableStateOf(IntSize.Zero) }
    var panOffset by remember { mutableStateOf(Offset.Zero) }
    var zoom by remember { mutableFloatStateOf(1f) }
    var viewRotationDeg by remember {
        mutableFloatStateOf(floor?.editorViewRotationDeg?.toFloat() ?: 0f)
    }

    fun fitFloorView() {
        val f = floor ?: return
        if (canvasSize.width <= 0 || canvasSize.height <= 0 || f.outerPolygonM.isEmpty()) return
        val b = GeoMath.polygonBounds(f.outerPolygonM)
        val spanX = max((b.maxX - b.minX).toFloat(), 2f)
        val spanY = max((b.maxY - b.minY).toFloat(), 2f)
        val fitZoom = min(
            (canvasSize.width * 0.78f) / (spanX * PPM),
            (canvasSize.height * 0.78f) / (spanY * PPM)
        ).coerceIn(0.25f, 6.0f)
        val cxM = 0.5 * (b.minX + b.maxX)
        val cyM = 0.5 * (b.minY + b.maxY)
        val rot = f.editorViewRotationDeg.toFloat()
        viewRotationDeg = rot
        zoom = fitZoom
        val preRot = Offset((cxM * PPM).toFloat(), (-cyM * PPM).toFloat())
        val rotated = rotateOffset(preRot * fitZoom, rot)
        panOffset = -rotated
    }

    LaunchedEffect(floor?.id, canvasSize, resetViewNonce) {
        fitFloorView()
    }

    LaunchedEffect(centerOnGpsNonce) {
        if (centerOnGpsNonce > 0 && liveLocalPosM != null) {
            val preRot = Offset((liveLocalPosM.x * PPM).toFloat(), (-liveLocalPosM.y * PPM).toFloat())
            panOffset = -rotateOffset(preRot * zoom, viewRotationDeg)
        }
    }

    val tilePaint = remember { Paint(Paint.FILTER_BITMAP_FLAG) }
    val roomTextPaint = remember {
        Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = Color(0xFFCBD5E1).toArgb()
            textSize = 26f
            textAlign = Paint.Align.CENTER
            typeface = Typeface.create(Typeface.DEFAULT, Typeface.BOLD)
        }
    }
    val apTextPaint = remember {
        Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = Color(0xFFF8FAFC).toArgb()
            textSize = 24f
            textAlign = Paint.Align.CENTER
            typeface = Typeface.create(Typeface.DEFAULT, Typeface.BOLD)
        }
    }
    val hudTextPaint = remember {
        Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = Color(0xFFF8FAFC).toArgb()
            textSize = 26f
            textAlign = Paint.Align.CENTER
            typeface = Typeface.create(Typeface.DEFAULT, Typeface.BOLD)
        }
    }

    Box(
        modifier = modifier
            .fillMaxSize()
            .background(NetVizColors.Background)
            .onSizeChanged { canvasSize = it }
    ) {
        Canvas(
            modifier = Modifier
                .fillMaxSize()
                .pointerInput(floor?.id, samples, canvasSize) {
                    detectTapGestures(
                        onTap = { tapPos ->
                            if (canvasSize.width <= 0 || canvasSize.height <= 0) return@detectTapGestures
                            val ptM = screenToLocalM(
                                screenPos = tapPos,
                                center = Offset(canvasSize.width / 2f, canvasSize.height / 2f),
                                pan = panOffset,
                                zoom = zoom,
                                viewRotDeg = viewRotationDeg
                            )
                            onTapPointM(ptM)
                        },
                        onLongPress = { pressPos ->
                            if (canvasSize.width <= 0 || canvasSize.height <= 0) return@detectTapGestures
                            val center = Offset(canvasSize.width / 2f, canvasSize.height / 2f)
                            val hit = samples.minByOrNull { s ->
                                val sScreen = localMToScreen(
                                    s.posM,
                                    center,
                                    panOffset,
                                    zoom,
                                    viewRotationDeg
                                )
                                hypot((sScreen.x - pressPos.x).toDouble(), (sScreen.y - pressPos.y).toDouble())
                            }
                            if (hit != null) {
                                val hitScreen = localMToScreen(
                                    hit.posM,
                                    center,
                                    panOffset,
                                    zoom,
                                    viewRotationDeg
                                )
                                val distPx = hypot(
                                    (hitScreen.x - pressPos.x).toDouble(),
                                    (hitScreen.y - pressPos.y).toDouble()
                                )
                                if (distPx <= 44.0) {
                                    onLongPressSample(hit)
                                }
                            }
                        }
                    )
                }
                .pointerInput(Unit) {
                    detectTransformGestures { centroid, pan, gestureZoom, rotation ->
                        val center = Offset(canvasSize.width / 2f, canvasSize.height / 2f)
                        val oldZoom = zoom
                        val newZoom = (oldZoom * gestureZoom).coerceIn(0.2f, 8.0f)
                        val oldRot = viewRotationDeg
                        val newRot = oldRot + rotation

                        val relCentroid = centroid - center - panOffset
                        val unscaled = rotateOffset(relCentroid / oldZoom, -oldRot)
                        val newRel = rotateOffset(unscaled * newZoom, newRot)
                        panOffset = centroid - center - newRel + pan
                        zoom = newZoom
                        viewRotationDeg = newRot
                    }
                }
        ) {
            val center = Offset(size.width / 2f, size.height / 2f)
            val anchor = building?.geoAnchor

            // 1. Sub-pixel exact offline tiles.sqlite backdrop via Matrix.setPolyToPoly (Section 4.2E)
            if (showMapBackdrop && hasTilesDb && anchor != null) {
                drawOfflineTilesBackdrop(
                    size = size,
                    center = center,
                    pan = panOffset,
                    zoom = zoom,
                    viewRotDeg = viewRotationDeg,
                    anchor = anchor,
                    readTile = readTile,
                    tilePaint = tilePaint
                )
            }

            // 2. Metric grid + building CAD geometry + heatmap + pins in transformed space
            withTransform({
                translate(left = center.x + panOffset.x, top = center.y + panOffset.y)
                rotate(degrees = viewRotationDeg, pivot = Offset.Zero)
                scale(scaleX = zoom, scaleY = zoom, pivot = Offset.Zero)
            }) {
                drawMetricGrid(floor)

                if (floor != null) {
                    drawFloorGeometry(floor, roomTextPaint)
                }

                if (showHeatmap && renderedHeatmap != null && renderedHeatmap.grid.validPixelCount > 0) {
                    val b = renderedHeatmap.grid.bounds
                    val dst = RectF(
                        (b.minX * PPM).toFloat(),
                        (-b.maxY * PPM).toFloat(),
                        (b.maxX * PPM).toFloat(),
                        (-b.minY * PPM).toFloat()
                    )
                    drawContext.canvas.nativeCanvas.drawBitmap(
                        renderedHeatmap.imageBitmap.asAndroidBitmap(),
                        null,
                        dst,
                        null
                    )
                }

                drawAccessPoints(components, floor?.id, apTextPaint, zoom)
                drawSamplePins(samples, zoom)
                selectedTapPtM?.let { drawManualTargetCrosshair(it, zoom) }

                if (liveLocalPosM != null) {
                    drawLiveGpsMarker(
                        posM = liveLocalPosM,
                        accuracyM = liveAccuracyM ?: 5f,
                        azimuthDeg = trueNorthAzimuthDeg,
                        viewRotDeg = viewRotationDeg,
                        anchorRotDeg = anchor?.rotationDeg ?: 0.0,
                        zoom = zoom
                    )
                }
            }

            // 3. Offscreen GPS indicator arrow when GPS dot lies outside viewport
            if (liveLocalPosM != null) {
                val gpsScreen = localMToScreen(liveLocalPosM, center, panOffset, zoom, viewRotationDeg)
                if (gpsScreen.x < 0f || gpsScreen.x > size.width || gpsScreen.y < 0f || gpsScreen.y > size.height) {
                    drawOffscreenGpsArrow(gpsScreen, size, hudTextPaint)
                }
            }

            // 4. Top-Right True North Compass & Bottom-Left Metric Scale Bar
            val trueNorthCwDeg = GeoMath.trueNorthScreenCwDeg(
                viewRotDeg = viewRotationDeg.toDouble(),
                anchorRotDeg = anchor?.rotationDeg ?: 0.0
            ).toFloat()
            drawTrueNorthCompass(
                topRightCenter = Offset(size.width - 54f, 64f),
                trueNorthCwDeg = trueNorthCwDeg,
                textPaint = hudTextPaint
            )
            drawMetricScaleBar(
                bottomLeft = Offset(24f, size.height - 28f),
                zoom = zoom,
                textPaint = hudTextPaint
            )
        }

        val floorRot = floor?.editorViewRotationDeg?.toFloat() ?: 0f
        if (kotlin.math.abs(viewRotationDeg - floorRot) > 2f) {
            FilledTonalButton(
                onClick = { fitFloorView() },
                shape = RoundedCornerShape(10.dp),
                modifier = Modifier
                    .align(Alignment.BottomEnd)
                    .padding(12.dp)
            ) {
                Text("Reset View", fontSize = 12.sp)
            }
        }
    }
}

private fun DrawScope.drawOfflineTilesBackdrop(
    size: Size,
    center: Offset,
    pan: Offset,
    zoom: Float,
    viewRotDeg: Float,
    anchor: org.netviz.survey.model.GeoAnchor,
    readTile: (String, Int, Int, Int) -> Bitmap?,
    tilePaint: Paint
) {
    val cornersM = listOf(
        screenToLocalM(Offset(0f, 0f), center, pan, zoom, viewRotDeg),
        screenToLocalM(Offset(size.width, 0f), center, pan, zoom, viewRotDeg),
        screenToLocalM(Offset(size.width, size.height), center, pan, zoom, viewRotDeg),
        screenToLocalM(Offset(0f, size.height), center, pan, zoom, viewRotDeg)
    )
    val latLons = cornersM.map { GeoMath.localMToLatLon(it, anchor) }
    val minLat = latLons.minOf { it.first }
    val maxLat = latLons.maxOf { it.first }
    val minLon = latLons.minOf { it.second }
    val maxLon = latLons.maxOf { it.second }

    val pxPerMeter = zoom * PPM
    val tileZoom = when {
        pxPerMeter >= 70f -> 19
        pxPerMeter >= 35f -> 18
        pxPerMeter >= 18f -> 17
        else -> 16
    }

    val tileCoords = GeoMath.boundingBoxTiles(minLat, minLon, maxLat, maxLon, listOf(tileZoom))
        .take(36)

    for (tc in tileCoords) {
        val bmp = readTile("esri_satellite", tc.z, tc.x, tc.y)
            ?: readTile("osm", tc.z, tc.x, tc.y)
            ?: continue

        val tb = GeoMath.tileXyToLatLonBounds(tc.z, tc.x, tc.y)
        val pNW = localMToScreen(GeoMath.latLonToLocalM(tb.latN, tb.lonW, anchor), center, pan, zoom, viewRotDeg)
        val pNE = localMToScreen(GeoMath.latLonToLocalM(tb.latN, tb.lonE, anchor), center, pan, zoom, viewRotDeg)
        val pSE = localMToScreen(GeoMath.latLonToLocalM(tb.latS, tb.lonE, anchor), center, pan, zoom, viewRotDeg)
        val pSW = localMToScreen(GeoMath.latLonToLocalM(tb.latS, tb.lonW, anchor), center, pan, zoom, viewRotDeg)

        val src = floatArrayOf(
            0f, 0f,
            bmp.width.toFloat(), 0f,
            bmp.width.toFloat(), bmp.height.toFloat(),
            0f, bmp.height.toFloat()
        )
        val dst = floatArrayOf(
            pNW.x, pNW.y,
            pNE.x, pNE.y,
            pSE.x, pSE.y,
            pSW.x, pSW.y
        )
        val polyMatrix = Matrix()
        if (polyMatrix.setPolyToPoly(src, 0, dst, 0, 4)) {
            drawContext.canvas.nativeCanvas.drawBitmap(bmp, polyMatrix, tilePaint)
        }
    }
}

private fun DrawScope.drawMetricGrid(floor: Floor?) {
    val b = floor?.let { GeoMath.polygonBounds(it.outerPolygonM) } ?: GeoMath.Bounds2D(-10.0, -10.0, 30.0, 30.0)
    val minX = (b.minX - 10.0).toInt()
    val maxX = (b.maxX + 10.0).toInt()
    val minY = (b.minY - 10.0).toInt()
    val maxY = (b.maxY + 10.0).toInt()
    val gridColor = Color(0xFF1E293B).copy(alpha = 0.55f)
    for (x in minX..maxX step 2) {
        drawLine(
            color = gridColor,
            start = Offset(x * PPM, -maxY * PPM),
            end = Offset(x * PPM, -minY * PPM),
            strokeWidth = 1f
        )
    }
    for (y in minY..maxY step 2) {
        drawLine(
            color = gridColor,
            start = Offset(minX * PPM, -y * PPM),
            end = Offset(maxX * PPM, -y * PPM),
            strokeWidth = 1f
        )
    }
}

private fun DrawScope.drawFloorGeometry(floor: Floor, roomTextPaint: Paint) {
    if (floor.outerPolygonM.size >= 3) {
        val outerPath = polygonToPath(floor.outerPolygonM)
        drawPath(outerPath, color = Color(0xFF0F172A).copy(alpha = 0.72f))
        drawPath(outerPath, color = Color(0xFF64748B), style = Stroke(width = 4f))
    }

    for (room in floor.rooms) {
        if (room.polygonM.size < 3) continue
        val path = polygonToPath(room.polygonM)
        val fillColor = runCatching { Color(android.graphics.Color.parseColor(room.fillColorHex)) }
            .getOrDefault(Color(0xFF1E293B))
            .copy(alpha = 0.55f)
        drawPath(path, color = fillColor)
        drawPath(path, color = Color(0xFF94A3B8), style = Stroke(width = 2.5f))

        val cx = room.polygonM.map { it.x }.average().toFloat() * PPM
        val cy = -room.polygonM.map { it.y }.average().toFloat() * PPM
        drawContext.canvas.nativeCanvas.drawText(room.name, cx, cy + 8f, roomTextPaint)
    }

    for (stairs in floor.stairs) {
        if (stairs.polygonM.size < 3) continue
        val sPath = polygonToPath(stairs.polygonM)
        drawPath(sPath, color = Color(0xFF334155).copy(alpha = 0.7f))
        drawPath(sPath, color = Color(0xFFCBD5E1), style = Stroke(width = 2f))
    }

    for (door in floor.doors) {
        val cx = (door.positionM.x * PPM).toFloat()
        val cy = (-door.positionM.y * PPM).toFloat()
        val halfW = ((door.widthM * 0.5) * PPM).toFloat()
        val rad = Math.toRadians(door.angleDeg)
        val dx = (cos(rad) * halfW).toFloat()
        val dy = (-sin(rad) * halfW).toFloat()
        drawLine(
            color = Color(0xFFFACC15),
            start = Offset(cx - dx, cy - dy),
            end = Offset(cx + dx, cy + dy),
            strokeWidth = 5f,
            cap = StrokeCap.Round
        )
    }

    for (hole in floor.holes) {
        val cx = (hole.positionM.x * PPM).toFloat()
        val cy = (-hole.positionM.y * PPM).toFloat()
        val r = (hole.radiusM * PPM).toFloat().coerceAtLeast(8f)
        drawCircle(color = NetVizColors.RiserOrange.copy(alpha = 0.35f), radius = r, center = Offset(cx, cy))
        drawCircle(
            color = NetVizColors.RiserOrange,
            radius = r,
            center = Offset(cx, cy),
            style = Stroke(width = 3f)
        )
    }
}

private fun DrawScope.drawAccessPoints(
    components: List<Component>,
    floorId: String?,
    apTextPaint: Paint,
    zoom: Float
) {
    if (floorId == null) return
    val radius = (14f / zoom).coerceIn(7f, 22f)
    for (comp in components) {
        if (comp.componentType != ComponentType.ACCESS_POINT) continue
        if (comp.physicalFloorId != floorId) continue
        val pos = comp.physicalPosM ?: continue
        val center = Offset((pos.x * PPM).toFloat(), (-pos.y * PPM).toFloat())
        drawCircle(color = Color(0xFF16A34A), radius = radius, center = center)
        drawCircle(
            color = Color.White,
            radius = radius,
            center = center,
            style = Stroke(width = (2.5f / zoom).coerceAtLeast(1.2f))
        )
        drawContext.canvas.nativeCanvas.drawText(
            comp.name,
            center.x,
            center.y - radius - 8f,
            apTextPaint
        )
    }
}

private fun DrawScope.drawSamplePins(samples: List<SurveySample>, zoom: Float) {
    val pinRadius = (10f / zoom).coerceIn(5f, 16f)
    for (sample in samples) {
        val bestRssi = sample.readings.maxOfOrNull { it.rssiDbm } ?: -90
        val color = rssiToComposeColor(bestRssi)
        val center = Offset((sample.posM.x * PPM).toFloat(), (-sample.posM.y * PPM).toFloat())
        drawCircle(color = color, radius = pinRadius, center = center)
        drawCircle(
            color = Color(0xFF0F172A),
            radius = pinRadius,
            center = center,
            style = Stroke(width = (2.5f / zoom).coerceAtLeast(1f))
        )
    }
}

private fun DrawScope.drawManualTargetCrosshair(ptM: Point2D, zoom: Float) {
    val c = Offset((ptM.x * PPM).toFloat(), (-ptM.y * PPM).toFloat())
    val arm = (22f / zoom).coerceIn(10f, 34f)
    val stroke = (3f / zoom).coerceAtLeast(1.5f)
    drawCircle(color = NetVizColors.Primary, radius = arm * 0.65f, center = c, style = Stroke(stroke))
    drawLine(NetVizColors.Primary, Offset(c.x - arm, c.y), Offset(c.x + arm, c.y), strokeWidth = stroke)
    drawLine(NetVizColors.Primary, Offset(c.x, c.y - arm), Offset(c.x, c.y + arm), strokeWidth = stroke)
}

private fun DrawScope.drawLiveGpsMarker(
    posM: Point2D,
    accuracyM: Float,
    azimuthDeg: Float?,
    viewRotDeg: Float,
    anchorRotDeg: Double,
    zoom: Float
) {
    val center = Offset((posM.x * PPM).toFloat(), (-posM.y * PPM).toFloat())
    val haloRadius = (accuracyM * PPM).coerceAtLeast(12f)
    drawCircle(
        color = NetVizColors.GpsBlue.copy(alpha = 0.16f),
        radius = haloRadius,
        center = center
    )
    drawCircle(
        color = NetVizColors.GpsBlue.copy(alpha = 0.48f),
        radius = haloRadius,
        center = center,
        style = Stroke(
            width = (2f / zoom).coerceAtLeast(1f),
            pathEffect = PathEffect.dashPathEffect(floatArrayOf(10f, 8f))
        )
    )

    // Compass cone at exact Section 4.2C screen angle (compensated since we are inside viewRot transform)
    if (azimuthDeg != null) {
        val screenCwDeg = GeoMath.bearingScreenCwDeg(
            viewRotDeg = viewRotDeg.toDouble(),
            anchorRotDeg = anchorRotDeg,
            azimuthDeg = azimuthDeg.toDouble()
        )
        // Inside the rotated canvas, undo viewRotDeg so world angle matches screen angle
        val localAngleDeg = screenCwDeg - viewRotDeg.toDouble()
        val coneLen = (72f / zoom).coerceIn(32f, 110f)
        val halfSpreadDeg = 24.0
        val rad1 = Math.toRadians(localAngleDeg - halfSpreadDeg - 90.0)
        val rad2 = Math.toRadians(localAngleDeg + halfSpreadDeg - 90.0)
        val conePath = Path().apply {
            moveTo(center.x, center.y)
            lineTo(
                center.x + (cos(rad1) * coneLen).toFloat(),
                center.y + (sin(rad1) * coneLen).toFloat()
            )
            lineTo(
                center.x + (cos(rad2) * coneLen).toFloat(),
                center.y + (sin(rad2) * coneLen).toFloat()
            )
            close()
        }
        drawPath(conePath, color = NetVizColors.GpsBlue.copy(alpha = 0.35f))
    }

    val dotRadius = (11f / zoom).coerceIn(6f, 18f)
    drawCircle(color = Color.White, radius = dotRadius + (3f / zoom), center = center)
    drawCircle(color = NetVizColors.GpsBlue, radius = dotRadius, center = center)
}

private fun DrawScope.drawOffscreenGpsArrow(
    gpsScreen: Offset,
    size: Size,
    textPaint: Paint
) {
    val cx = size.width / 2f
    val cy = size.height / 2f
    val angle = atan2(gpsScreen.y - cy, gpsScreen.x - cx)
    val margin = 44f
    val edgeX = (cx + cos(angle) * (size.width * 0.42f)).coerceIn(margin, size.width - margin)
    val edgeY = (cy + sin(angle) * (size.height * 0.42f)).coerceIn(margin, size.height - margin)
    val tip = Offset(edgeX + cos(angle) * 18f, edgeY + sin(angle) * 18f)
    val left = Offset(edgeX + cos(angle + 2.4f) * 14f, edgeY + sin(angle + 2.4f) * 14f)
    val right = Offset(edgeX + cos(angle - 2.4f) * 14f, edgeY + sin(angle - 2.4f) * 14f)
    val path = Path().apply {
        moveTo(tip.x, tip.y)
        lineTo(left.x, left.y)
        lineTo(right.x, right.y)
        close()
    }
    drawCircle(color = Color(0xFF0F172A).copy(alpha = 0.85f), radius = 26f, center = Offset(edgeX, edgeY))
    drawPath(path, color = NetVizColors.GpsBlue)
    drawContext.canvas.nativeCanvas.drawText("GPS", edgeX, edgeY + 38f, textPaint)
}

private fun DrawScope.drawTrueNorthCompass(
    topRightCenter: Offset,
    trueNorthCwDeg: Float,
    textPaint: Paint
) {
    drawCircle(
        color = NetVizColors.Surface.copy(alpha = 0.88f),
        radius = 30f,
        center = topRightCenter
    )
    drawCircle(
        color = NetVizColors.Border,
        radius = 30f,
        center = topRightCenter,
        style = Stroke(width = 2f)
    )
    val rad = Math.toRadians(trueNorthCwDeg - 90.0)
    val tip = Offset(
        topRightCenter.x + (cos(rad) * 20f).toFloat(),
        topRightCenter.y + (sin(rad) * 20f).toFloat()
    )
    val tail = Offset(
        topRightCenter.x - (cos(rad) * 12f).toFloat(),
        topRightCenter.y - (sin(rad) * 12f).toFloat()
    )
    drawLine(NetVizColors.Error, tail, tip, strokeWidth = 4f, cap = StrokeCap.Round)
    drawContext.canvas.nativeCanvas.drawText(
        "N",
        topRightCenter.x + (cos(rad) * 22f).toFloat(),
        topRightCenter.y + (sin(rad) * 22f).toFloat() + 8f,
        textPaint
    )
}

private fun DrawScope.drawMetricScaleBar(
    bottomLeft: Offset,
    zoom: Float,
    textPaint: Paint
) {
    val pxPerM = zoom * PPM
    val candidates = listOf(1, 2, 5, 10, 20, 50)
    val chosenM = candidates.firstOrNull { it * pxPerM in 70f..220f }
        ?: candidates.minBy { kotlin.math.abs(it * pxPerM - 130f) }
    val barPx = chosenM * pxPerM

    drawRect(
        color = NetVizColors.Surface.copy(alpha = 0.85f),
        topLeft = Offset(bottomLeft.x - 8f, bottomLeft.y - 36f),
        size = Size(barPx + 16f, 44f)
    )
    drawLine(
        color = NetVizColors.TextPrimary,
        start = bottomLeft,
        end = Offset(bottomLeft.x + barPx, bottomLeft.y),
        strokeWidth = 4f
    )
    drawLine(NetVizColors.TextPrimary, Offset(bottomLeft.x, bottomLeft.y - 8f), bottomLeft, strokeWidth = 3f)
    drawLine(
        NetVizColors.TextPrimary,
        Offset(bottomLeft.x + barPx, bottomLeft.y - 8f),
        Offset(bottomLeft.x + barPx, bottomLeft.y),
        strokeWidth = 3f
    )
    drawContext.canvas.nativeCanvas.drawText(
        "$chosenM m",
        bottomLeft.x + barPx / 2f,
        bottomLeft.y - 12f,
        textPaint
    )
}

private fun polygonToPath(pts: List<Point2D>): Path = Path().apply {
    if (pts.isEmpty()) return@apply
    moveTo((pts[0].x * PPM).toFloat(), (-pts[0].y * PPM).toFloat())
    for (i in 1 until pts.size) {
        lineTo((pts[i].x * PPM).toFloat(), (-pts[i].y * PPM).toFloat())
    }
    close()
}

private fun rssiToComposeColor(rssiDbm: Int): Color = when {
    rssiDbm >= -55 -> Color(0xFF22C55E)
    rssiDbm >= -67 -> Color(0xFF84CC16)
    rssiDbm >= -75 -> Color(0xFFEAB308)
    rssiDbm >= -85 -> Color(0xFFF97316)
    else -> Color(0xFFEF4444)
}

private fun rotateOffset(offset: Offset, degrees: Float): Offset {
    val rad = degrees * (PI / 180.0)
    val c = cos(rad).toFloat()
    val s = sin(rad).toFloat()
    return Offset(offset.x * c - offset.y * s, offset.x * s + offset.y * c)
}

private fun localMToScreen(
    ptM: Point2D,
    center: Offset,
    pan: Offset,
    zoom: Float,
    viewRotDeg: Float
): Offset {
    val preRot = Offset((ptM.x * PPM).toFloat(), (-ptM.y * PPM).toFloat())
    val scaled = preRot * zoom
    val rotated = rotateOffset(scaled, viewRotDeg)
    return center + pan + rotated
}

private fun screenToLocalM(
    screenPos: Offset,
    center: Offset,
    pan: Offset,
    zoom: Float,
    viewRotDeg: Float
): Point2D {
    val rel = screenPos - center - pan
    val unrotated = rotateOffset(rel, -viewRotDeg)
    val unscaled = unrotated / max(zoom, 0.01f)
    return Point2D(
        x = (unscaled.x / PPM).toDouble(),
        y = (-unscaled.y / PPM).toDouble()
    )
}
