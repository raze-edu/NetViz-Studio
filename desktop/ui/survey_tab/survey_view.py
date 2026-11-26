"""Tab 4 Wi-Fi Survey Analysis, Multi-Session Merge, Clipped IDW Heatmap & Outlier Tools."""

from __future__ import annotations

import copy
import math
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QFont,
    QImage,
    QKeyEvent,
    QKeySequence,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QPixmap,
    QPolygonF,
    QShortcut,
    QTransform,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsPixmapItem,
    QGraphicsPolygonItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSlider,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from desktop.coords import (
    PIXELS_PER_METER,
    bounds_m_to_scene_rect,
    local_m_to_scene,
)
from desktop.state import ProjectState
from desktop.ui.theme import component_color
from shared.bundle_io import import_survey_session, load_netviz
from shared.geo_math import find_room_at_point, true_north_screen_cw_deg
from shared.heatmap import (
    HeatmapResult,
    compute_idw_heatmap,
    frequency_to_band,
    rssi_to_rgb,
)
from shared.models import (
    BlueprintUnderlay,
    Building,
    ComponentType,
    Door,
    Floor,
    Point2D,
    Project,
    SurveySample,
    normalize_mac,
)

Z_BLUEPRINT = -40.0
Z_FLOOR_OUTER = 0.0
Z_ROOM = 10.0
Z_HEATMAP = 14.0
Z_ROOM_LABEL = 16.0
Z_STAIRS = 20.0
Z_HOLE = 25.0
Z_DOOR = 28.0
Z_AP_MARKER = 45.0
Z_PIN = 55.0


class ToolMode(StrEnum):
    """Strict non-panning tool modes for Tab 4 survey canvas."""

    SELECT = "select"
    PAN = "pan"


_TOOL_CURSORS: dict[ToolMode, Qt.CursorShape] = {
    ToolMode.SELECT: Qt.CursorShape.ArrowCursor,
    ToolMode.PAN: Qt.CursorShape.OpenHandCursor,
}


def _poly_to_qpolygon(pts_m: list[Point2D]) -> QPolygonF:
    return QPolygonF([local_m_to_scene(pt) for pt in pts_m])


def _door_graphics_path(door: Door) -> QPainterPath:
    """Build local-centered CAD door leaf + quarter-circle swing arc in scene pixels."""
    w_px = door.width_m * PIXELS_PER_METER
    half_w = w_px * 0.5
    hinge_x = half_w if door.hinge_flip else -half_w
    latch_x = -half_w if door.hinge_flip else half_w
    swing_dir = 1.0 if door.swing_flip else -1.0

    path = QPainterPath()
    path.moveTo(-half_w, 0.0)
    path.lineTo(half_w, 0.0)
    path.moveTo(hinge_x, 0.0)
    path.lineTo(hinge_x, swing_dir * w_px)
    rect = QRectF(hinge_x - w_px, -w_px, 2.0 * w_px, 2.0 * w_px)
    if not door.hinge_flip and not door.swing_flip:
        path.moveTo(latch_x, 0.0)
        path.arcTo(rect, 0.0, 90.0)
    elif not door.hinge_flip and door.swing_flip:
        path.moveTo(latch_x, 0.0)
        path.arcTo(rect, 0.0, -90.0)
    elif door.hinge_flip and not door.swing_flip:
        path.moveTo(latch_x, 0.0)
        path.arcTo(rect, 180.0, -90.0)
    else:
        path.moveTo(latch_x, 0.0)
        path.arcTo(rect, 180.0, 90.0)
    return path


def _best_matching_rssi(
    sample: SurveySample,
    *,
    target_ssids: list[str] | None,
    bssid_filter: str | None,
    band_filter: str | None,
) -> int | None:
    ssid_set = {s.strip() for s in target_ssids if s and s.strip()} if target_ssids else None
    bssid_norm = normalize_mac(bssid_filter) if bssid_filter else ""
    band = band_filter.strip() if band_filter and band_filter.lower() != "all" else None

    best: int | None = None
    for r in sample.readings:
        if ssid_set is not None and r.ssid.strip() not in ssid_set:
            continue
        if bssid_norm and normalize_mac(r.bssid) != bssid_norm:
            continue
        if band is not None and frequency_to_band(r.frequency_mhz) != band:
            continue
        if best is None or r.rssi_dbm > best:
            best = r.rssi_dbm
    return best


# ---------------------------------------------------------------------------
# Off-UI-Thread Vectorized IDW Worker (QThread)
# ---------------------------------------------------------------------------


class HeatmapWorker(QThread):
    """Background worker running vectorized IDW heatmap calculation off the UI thread."""

    heatmap_ready = Signal(int, object)  # (request_id, HeatmapResult)

    def __init__(
        self,
        request_id: int,
        floor: Floor,
        samples: list[SurveySample],
        *,
        target_ssids: list[str] | None,
        bssid_filter: str | None,
        band_filter: str | None,
        resolution_m: float,
        dead_zone_dbm: float,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._request_id = request_id
        self._floor = copy.deepcopy(floor)
        self._samples = copy.deepcopy(samples)
        self._target_ssids = list(target_ssids) if target_ssids is not None else None
        self._bssid_filter = bssid_filter
        self._band_filter = band_filter
        self._resolution_m = float(resolution_m)
        self._dead_zone_dbm = float(dead_zone_dbm)

    def run(self) -> None:
        if self.isInterruptionRequested():
            return
        result = compute_idw_heatmap(
            self._floor,
            self._samples,
            target_ssids=self._target_ssids,
            bssid_filter=self._bssid_filter,
            band_filter=self._band_filter,
            resolution_m=self._resolution_m,
            dead_zone_dbm=self._dead_zone_dbm,
        )
        if not self.isInterruptionRequested():
            self.heatmap_ready.emit(self._request_id, result)


# ---------------------------------------------------------------------------
# dBm Legend & Sample Inspection Dialog
# ---------------------------------------------------------------------------


class DbmLegendWidget(QWidget):
    """5-stop perceptual dBm color legend overlay (-30 to -95 dBm)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(230, 56)

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        bg = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        painter.setPen(QPen(QColor("#334155"), 1.2))
        painter.setBrush(QColor(15, 23, 42, 225))
        painter.drawRoundedRect(bg, 6.0, 6.0)

        painter.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
        painter.setPen(QColor("#CBD5E1"))
        painter.drawText(QRectF(10.0, 4.0, self.width() - 20.0, 14.0), 0, "Wi-Fi Signal (dBm)")

        bar_rect = QRectF(10.0, 21.0, self.width() - 20.0, 12.0)
        grad = QLinearGradient(bar_rect.left(), 0.0, bar_rect.right(), 0.0)
        grad.setColorAt(0.0, QColor("#EF4444"))   # -95 dBm
        grad.setColorAt(0.23, QColor("#F97316"))  # -80 dBm
        grad.setColorAt(0.38, QColor("#EAB308"))  # -70 dBm
        grad.setColorAt(0.54, QColor("#84CC16"))  # -60 dBm
        grad.setColorAt(1.0, QColor("#22C55E"))   # -30 dBm
        painter.setPen(QPen(QColor("#475569"), 1.0))
        painter.setBrush(QBrush(grad))
        painter.drawRoundedRect(bar_rect, 3.0, 3.0)

        painter.setFont(QFont("Segoe UI", 7))
        painter.setPen(QColor("#94A3B8"))
        ticks = [("-95", 10.0), ("-80", 54.0), ("-70", 92.0), ("-60", 132.0), ("-30", 196.0)]
        for text, x in ticks:
            painter.drawText(QRectF(x - 10.0, 36.0, 26.0, 14.0), Qt.AlignmentFlag.AlignCenter, text)


class _SampleReadingsDialog(QDialog):
    """Modal table inspecting all Wi-Fi BSSID readings captured at a SurveySample."""

    def __init__(
        self,
        sample: SurveySample,
        project: Project,
        room_name: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Survey Sample BSSID Readings")
        self.resize(620, 360)

        layout = QVBoxLayout(self)
        meta = QLabel(
            f"<b>Timestamp:</b> {sample.timestamp_utc} · "
            f"<b>Source:</b> {sample.source.value} · "
            f"<b>Pos:</b> ({sample.pos_m.x:.2f} m, {sample.pos_m.y:.2f} m) · "
            f"<b>Room:</b> {room_name}",
            self,
        )
        meta.setWordWrap(True)
        layout.addWidget(meta)

        table = QTableWidget(len(sample.readings), 5, self)
        table.setHorizontalHeaderLabels(["SSID", "BSSID", "AP Component", "RSSI", "Band / MHz"])
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        hdr = table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in (1, 2, 3, 4):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)

        for row, reading in enumerate(
            sorted(sample.readings, key=lambda r: r.rssi_dbm, reverse=True)
        ):
            ap_comp = project.find_component_by_mac(reading.bssid)
            ap_label = ap_comp.name if ap_comp is not None else "—"
            band = frequency_to_band(reading.frequency_mhz)
            band_str = f"{band} GHz ({reading.frequency_mhz} MHz)" if band else (
                f"{reading.frequency_mhz} MHz"
            )
            table.setItem(row, 0, QTableWidgetItem(reading.ssid or "<hidden>"))
            table.setItem(row, 1, QTableWidgetItem(reading.bssid))
            table.setItem(row, 2, QTableWidgetItem(ap_label))
            table.setItem(row, 3, QTableWidgetItem(f"{reading.rssi_dbm} dBm"))
            table.setItem(row, 4, QTableWidgetItem(band_str))

        layout.addWidget(table, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


# ---------------------------------------------------------------------------
# Survey Pin Graphics Item
# ---------------------------------------------------------------------------


class SurveyPinItem(QGraphicsEllipseItem):
    """Color-coded Wi-Fi measurement pin with rich tooltip & context menu (z = 55)."""

    def __init__(
        self,
        sample: SurveySample,
        *,
        matched_rssi: int | None,
        room_name: str,
        project: Project,
        on_inspect: Callable[[SurveySample], None] | None = None,
        on_delete: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(-5.5, -5.5, 11.0, 11.0)
        self.sample = sample
        self.matched_rssi = matched_rssi
        self._on_inspect = on_inspect
        self._on_delete = on_delete

        self.setPos(local_m_to_scene(sample.pos_m))
        self.setZValue(Z_PIN)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        if matched_rssi is not None:
            r, g, b = rssi_to_rgb(float(matched_rssi))
            fill = QColor(r, g, b)
        else:
            fill = QColor("#64748B")
        self.setBrush(QBrush(fill))
        self.setPen(QPen(QColor("#F8FAFC"), 1.5))

        lines = [
            f"Time: {sample.timestamp_utc}",
            f"Source: {sample.source.value}",
            f"Pos: ({sample.pos_m.x:.2f} m, {sample.pos_m.y:.2f} m) · Room: {room_name}",
            f"Matched RSSI: {matched_rssi} dBm"
            if matched_rssi is not None
            else "Matched RSSI: N/A (filtered out)",
        ]
        if sample.gps_accuracy_m is not None:
            lines.append(f"GPS Accuracy: ±{sample.gps_accuracy_m:.1f} m")
        for rd in sorted(sample.readings, key=lambda x: x.rssi_dbm, reverse=True)[:5]:
            ap_comp = project.find_component_by_mac(rd.bssid)
            ap_tag = f" [{ap_comp.name}]" if ap_comp is not None else ""
            lines.append(f"  • {rd.ssid or '<hidden>'} ({rd.bssid}{ap_tag}): {rd.rssi_dbm} dBm")
        self.setToolTip("\n".join(lines))

    def contextMenuEvent(self, event) -> None:  # type: ignore[override]
        menu = QMenu()
        inspect_act = menu.addAction("Inspect BSSID Readings")
        delete_act = menu.addAction("Delete Outlier Sample")
        chosen = menu.exec(event.screenPos())
        if chosen == inspect_act and self._on_inspect is not None:
            self._on_inspect(self.sample)
        elif chosen == delete_act and self._on_delete is not None:
            self._on_delete(self.sample.id)
        event.accept()


# ---------------------------------------------------------------------------
# Survey Canvas View (Non-Panning + Scale Bar + Compass + Clipped Heatmap)
# ---------------------------------------------------------------------------


class SurveyCanvasView(QGraphicsView):
    """Floor-plan survey canvas rendering clipped IDW heatmap, APs, and sample pins."""

    tool_changed = Signal(str)
    view_rotation_changed = Signal(float)
    inspect_sample_requested = Signal(object)  # SurveySample
    delete_sample_requested = Signal(str)  # sample_id
    viewport_resized = Signal()

    def __init__(self, state: ProjectState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state = state
        self._scene = QGraphicsScene(self)
        self._scene.setSceneRect(-4000.0, -4000.0, 8000.0, 8000.0)
        self.setScene(self._scene)

        self.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.TextAntialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
        )
        # Invariant #9: NEVER default ScrollHandDrag!
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._building_id: str | None = None
        self._floor_id: str | None = None
        self._zoom: float = 1.0
        self._view_rotation_deg: float = 0.0
        self._tool: ToolMode = ToolMode.SELECT

        # Panning state
        self._space_held: bool = False
        self._panning_active: bool = False
        self._pan_last_pos: QPoint = QPoint()

        # Dynamic overlays
        self._heatmap_item: QGraphicsPixmapItem | None = None
        self._last_heatmap: HeatmapResult | None = None
        self._pin_items: list[SurveyPinItem] = []

        self._apply_view_transform()

    @property
    def tool_mode(self) -> ToolMode:
        return self._tool

    @property
    def view_rotation_deg(self) -> float:
        return self._view_rotation_deg

    @property
    def heatmap_item(self) -> QGraphicsPixmapItem | None:
        return self._heatmap_item

    @property
    def last_heatmap(self) -> HeatmapResult | None:
        return self._last_heatmap

    @property
    def pin_items(self) -> list[SurveyPinItem]:
        return list(self._pin_items)

    def current_building(self) -> Building | None:
        if not self._building_id:
            return None
        return self._state.project.get_building(self._building_id)

    def current_floor(self) -> Floor | None:
        if not self._building_id or not self._floor_id:
            return None
        return self._state.project.get_floor(self._building_id, self._floor_id)

    def true_north_angle_deg(self) -> float:
        bldg = self.current_building()
        anchor_rot = bldg.geo_anchor.rotation_deg if bldg and bldg.geo_anchor else 0.0
        return true_north_screen_cw_deg(self._view_rotation_deg, anchor_rot)

    def set_floor_context(
        self,
        building_id: str | None,
        floor_id: str | None,
        *,
        fit_view: bool = False,
    ) -> None:
        self._building_id = building_id
        self._floor_id = floor_id
        floor = self.current_floor()
        self._view_rotation_deg = floor.editor_view_rotation_deg if floor is not None else 0.0
        self._apply_view_transform()
        self.view_rotation_changed.emit(self._view_rotation_deg)
        self.rebuild_floor_geometry()
        if fit_view:
            self.fit_floor_in_view()

    def set_tool(self, tool: ToolMode) -> None:
        self._tool = tool
        if tool == ToolMode.PAN:
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        else:
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.viewport().setCursor(_TOOL_CURSORS.get(tool, Qt.CursorShape.ArrowCursor))
        self.tool_changed.emit(tool.value)
        self.viewport().update()

    def set_view_rotation(self, angle_deg: float, *, commit_to_state: bool = True) -> None:
        normalized = ((float(angle_deg) + 180.0) % 360.0) - 180.0
        self._view_rotation_deg = normalized
        self._apply_view_transform()
        self.view_rotation_changed.emit(self._view_rotation_deg)
        floor = self.current_floor()
        if commit_to_state and floor is not None and self._building_id is not None:
            if abs(floor.editor_view_rotation_deg - normalized) > 1e-6:
                self._state.set_floor_view_rotation(self._building_id, floor.id, normalized)
        self.viewport().update()

    def fit_floor_in_view(self) -> None:
        floor = self.current_floor()
        if floor is None or not floor.outer_polygon_m:
            return
        poly = _poly_to_qpolygon(floor.outer_polygon_m)
        rect = poly.boundingRect().adjusted(-80.0, -80.0, 80.0, 80.0)
        if rect.width() <= 1.0 or rect.height() <= 1.0:
            return
        vp = self.viewport().rect()
        if vp.width() <= 10 or vp.height() <= 10:
            return
        scale_x = vp.width() / rect.width()
        scale_y = vp.height() / rect.height()
        self._zoom = max(0.15, min(6.0, min(scale_x, scale_y) * 0.88))
        self._apply_view_transform()
        self.centerOn(rect.center())

    def _apply_view_transform(self) -> None:
        self.setTransform(
            QTransform().scale(self._zoom, self._zoom).rotate(self._view_rotation_deg)
        )

    def rebuild_floor_geometry(self) -> None:
        self._scene.clear()
        self._heatmap_item = None
        self._pin_items.clear()

        bldg = self.current_building()
        floor = self.current_floor()
        if bldg is None or floor is None:
            return

        if floor.blueprint is not None and floor.blueprint.visible:
            self._render_blueprint(floor.blueprint)

        if len(floor.outer_polygon_m) >= 3:
            outer_item = QGraphicsPolygonItem(_poly_to_qpolygon(floor.outer_polygon_m))
            outer_item.setZValue(Z_FLOOR_OUTER)
            outer_item.setBrush(QBrush(QColor(15, 23, 42, 225)))
            outer_item.setPen(QPen(QColor("#94A3B8"), 2.5))
            self._scene.addItem(outer_item)

        for room in floor.rooms:
            if len(room.polygon_m) < 3:
                continue
            r_item = QGraphicsPolygonItem(_poly_to_qpolygon(room.polygon_m))
            r_item.setZValue(Z_ROOM)
            fill = QColor(room.fill_color_hex)
            if not fill.isValid():
                fill = QColor("#1E293B")
            fill.setAlpha(120)
            r_item.setBrush(QBrush(fill))
            r_item.setPen(QPen(QColor("#38BDF8"), 1.6))
            self._scene.addItem(r_item)

            cx = sum(p.x for p in room.polygon_m) / len(room.polygon_m)
            cy = sum(p.y for p in room.polygon_m) / len(room.polygon_m)
            lbl = QGraphicsSimpleTextItem(room.name)
            lbl.setBrush(QBrush(QColor("#CBD5E1")))
            lbl.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
            br = lbl.boundingRect()
            c_scene = local_m_to_scene(Point2D(cx, cy))
            lbl.setPos(c_scene.x() - br.width() * 0.5, c_scene.y() - br.height() * 0.5)
            lbl.setZValue(Z_ROOM_LABEL)
            self._scene.addItem(lbl)

        for stairs in floor.stairs:
            if len(stairs.polygon_m) < 3:
                continue
            s_item = QGraphicsPolygonItem(_poly_to_qpolygon(stairs.polygon_m))
            s_item.setZValue(Z_STAIRS)
            s_item.setBrush(QBrush(QColor(168, 85, 247, 55)))
            s_item.setPen(QPen(QColor("#A855F7"), 1.5))
            self._scene.addItem(s_item)

        for hole in floor.holes:
            r_px = max(7.0, hole.radius_m * PIXELS_PER_METER)
            h_item = QGraphicsEllipseItem(-r_px, -r_px, 2 * r_px, 2 * r_px)
            h_item.setPos(local_m_to_scene(hole.position_m))
            h_item.setZValue(Z_HOLE)
            h_item.setBrush(QBrush(QColor(250, 204, 21, 70)))
            h_item.setPen(QPen(QColor("#FACC15"), 1.4))
            self._scene.addItem(h_item)

        for door in floor.doors:
            d_item = QGraphicsPathItem(_door_graphics_path(door))
            d_item.setPos(local_m_to_scene(door.position_m))
            d_item.setRotation(-door.angle_deg)
            d_item.setZValue(Z_DOOR)
            d_item.setPen(QPen(QColor("#22C55E"), 1.5))
            self._scene.addItem(d_item)

        # Render placed Access Point markers on this floor
        for comp in self._state.project.components:
            if (
                comp.component_type == ComponentType.ACCESS_POINT
                and comp.physical_building_id == bldg.id
                and comp.physical_floor_id == floor.id
                and comp.physical_pos_m is not None
            ):
                ap_dot = QGraphicsEllipseItem(-8.0, -8.0, 16.0, 16.0)
                ap_dot.setPos(local_m_to_scene(comp.physical_pos_m))
                ap_dot.setZValue(Z_AP_MARKER)
                ap_dot.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
                col = component_color(ComponentType.ACCESS_POINT)
                ap_dot.setBrush(QBrush(col))
                ap_dot.setPen(QPen(QColor("#F8FAFC"), 1.8))
                ap_dot.setToolTip(f"AP: {comp.name}\nMAC: {comp.mac_address or '—'}")
                self._scene.addItem(ap_dot)

                ap_lbl = QGraphicsSimpleTextItem(comp.name)
                ap_lbl.setBrush(QBrush(QColor("#F8FAFC")))
                ap_lbl.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
                ap_lbl.setPos(local_m_to_scene(comp.physical_pos_m) + QPointF(10.0, -10.0))
                ap_lbl.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
                ap_lbl.setZValue(Z_AP_MARKER + 1.0)
                self._scene.addItem(ap_lbl)

    def _render_blueprint(self, bp: BlueprintUnderlay) -> None:
        raw = self._state.blueprint_bytes.get(bp.archive_path)
        if not raw:
            return
        qimg = QImage.fromData(raw)
        if qimg.isNull():
            return
        item = QGraphicsPixmapItem(QPixmap.fromImage(qimg))
        item.setZValue(Z_BLUEPRINT)
        item.setOpacity(max(0.0, min(1.0, bp.opacity)))
        item.setScale(bp.meters_per_pixel * PIXELS_PER_METER)
        item.setRotation(bp.rotation_deg)
        item.setPos(local_m_to_scene(bp.offset_m))
        self._scene.addItem(item)

    def apply_heatmap_result(self, result: HeatmapResult) -> None:
        if self._heatmap_item is not None and self._heatmap_item.scene() is self._scene:
            self._scene.removeItem(self._heatmap_item)
            self._heatmap_item = None
        self._last_heatmap = result

        if result.valid_pixel_count <= 0 or result.width <= 0 or result.height <= 0:
            self.viewport().update()
            return

        qimg = QImage(
            result.rgba_bytes,
            result.width,
            result.height,
            result.width * 4,
            QImage.Format.Format_RGBA8888,
        ).copy()
        pix = QPixmap.fromImage(qimg)
        rect = bounds_m_to_scene_rect(*result.bounds)
        item = QGraphicsPixmapItem(pix)
        item.setZValue(Z_HEATMAP)
        item.setPos(rect.topLeft())
        item.setTransform(
            QTransform.fromScale(
                rect.width() / max(1, result.width),
                rect.height() / max(1, result.height),
            )
        )
        self._scene.addItem(item)
        self._heatmap_item = item
        self.viewport().update()

    def set_sample_pins(
        self,
        samples: list[SurveySample],
        *,
        target_ssids: list[str] | None,
        bssid_filter: str | None,
        band_filter: str | None,
    ) -> None:
        for pin in self._pin_items:
            if pin.scene() is self._scene:
                self._scene.removeItem(pin)
        self._pin_items.clear()

        floor = self.current_floor()
        if floor is None:
            return

        for smp in samples:
            if smp.floor_id != floor.id:
                continue
            matched_rssi = _best_matching_rssi(
                smp,
                target_ssids=target_ssids,
                bssid_filter=bssid_filter,
                band_filter=band_filter,
            )
            room = find_room_at_point(smp.pos_m, floor)
            room_name = room.name if room is not None else "Open Floor"
            pin = SurveyPinItem(
                smp,
                matched_rssi=matched_rssi,
                room_name=room_name,
                project=self._state.project,
                on_inspect=self.inspect_sample_requested.emit,
                on_delete=self.delete_sample_requested.emit,
            )
            self._scene.addItem(pin)
            self._pin_items.append(pin)

    # ------------------------------------------------------------------
    # Foreground / Background Rendering (Grid, True North Compass, Scale Bar)
    # ------------------------------------------------------------------

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        super().drawBackground(painter, rect)
        painter.fillRect(rect, QColor("#0B1120"))
        step = PIXELS_PER_METER
        left = int(math.floor(rect.left() / step) * step)
        top = int(math.floor(rect.top() / step) * step)
        right = int(math.ceil(rect.right()))
        bottom = int(math.ceil(rect.bottom()))
        painter.setPen(QPen(QColor("#131E33"), 1.0))
        for x in range(left, right + 1, int(step)):
            painter.drawLine(x, top, x, bottom)
        for y in range(top, bottom + 1, int(step)):
            painter.drawLine(left, y, right, y)

    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:
        super().drawForeground(painter, rect)
        painter.save()
        painter.resetTransform()
        vp = self.viewport().rect()

        # 1. Top-Right True North Compass
        cx = vp.width() - 44.0
        cy = 46.0
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(QBrush(QColor(15, 23, 42, 220)))
        painter.setPen(QPen(QColor("#334155"), 1.5))
        painter.drawEllipse(QPointF(cx, cy), 28.0, 28.0)

        tn_cw = self.true_north_angle_deg()
        painter.save()
        painter.translate(cx, cy)
        painter.rotate(tn_cw)
        arrow = QPolygonF(
            [QPointF(0.0, -21.0), QPointF(6.0, 2.0), QPointF(0.0, -3.0), QPointF(-6.0, 2.0)]
        )
        painter.setBrush(QBrush(QColor("#EF4444")))
        painter.setPen(QPen(QColor("#FCA5A5"), 1.0))
        painter.drawPolygon(arrow)
        painter.setPen(QPen(QColor("#F8FAFC"), 1.0))
        painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        painter.drawText(QRectF(-10.0, 4.0, 20.0, 14.0), Qt.AlignmentFlag.AlignCenter, "N")
        painter.restore()

        # 2. Bottom-Left Dynamic Metric Scale Bar
        px_per_m = PIXELS_PER_METER * self._zoom
        chosen_m = 5.0
        for cand in (1.0, 2.0, 5.0, 10.0, 20.0, 50.0):
            if 55.0 <= cand * px_per_m <= 165.0:
                chosen_m = cand
                break
        bar_px = chosen_m * px_per_m
        bx = 16.0
        by = vp.height() - 22.0
        painter.setBrush(QBrush(QColor(15, 23, 42, 210)))
        painter.setPen(QPen(QColor("#334155"), 1.0))
        painter.drawRoundedRect(QRectF(bx - 6.0, by - 22.0, bar_px + 12.0, 30.0), 4.0, 4.0)
        painter.setPen(QPen(QColor("#38BDF8"), 2.5))
        painter.drawLine(QPointF(bx, by), QPointF(bx + bar_px, by))
        painter.drawLine(QPointF(bx, by - 5.0), QPointF(bx, by + 2.0))
        painter.drawLine(QPointF(bx + bar_px, by - 5.0), QPointF(bx + bar_px, by + 2.0))
        painter.setPen(QPen(QColor("#F8FAFC"), 1.0))
        painter.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        painter.drawText(
            QRectF(bx, by - 21.0, bar_px, 16.0),
            Qt.AlignmentFlag.AlignCenter,
            f"{int(chosen_m)} m",
        )
        painter.restore()

    # ------------------------------------------------------------------
    # Strict Non-Panning Mouse & Keyboard Handlers
    # ------------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.MiddleButton or (
            self._space_held and event.button() == Qt.MouseButton.LeftButton
        ):
            self._panning_active = True
            self._pan_last_pos = event.position().toPoint()
            self.viewport().setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._panning_active:
            cur = event.position().toPoint()
            delta = cur - self._pan_last_pos
            self._pan_last_pos = cur
            h_bar = self.horizontalScrollBar()
            v_bar = self.verticalScrollBar()
            h_bar.setValue(h_bar.value() - delta.x())
            v_bar.setValue(v_bar.value() - delta.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._panning_active:
            self._panning_active = False
            cursor = (
                Qt.CursorShape.OpenHandCursor
                if (self._space_held or self._tool == ToolMode.PAN)
                else _TOOL_CURSORS.get(self._tool, Qt.CursorShape.ArrowCursor)
            )
            self.viewport().setCursor(QCursor(cursor))
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y()
        if delta == 0:
            return
        factor = 1.15 if delta > 0 else (1.0 / 1.15)
        self._zoom = max(0.15, min(8.0, self._zoom * factor))
        self._apply_view_transform()
        self.viewport().update()
        event.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        mods = event.modifiers()
        if key == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_held = True
            self.viewport().setCursor(QCursor(Qt.CursorShape.OpenHandCursor))
            event.accept()
            return
        if key == Qt.Key.Key_V and mods == Qt.KeyboardModifier.NoModifier:
            self.set_tool(ToolMode.SELECT)
            event.accept()
            return
        if key == Qt.Key.Key_H and mods == Qt.KeyboardModifier.NoModifier:
            self.set_tool(ToolMode.PAN)
            event.accept()
            return
        if key == Qt.Key.Key_Escape:
            self.set_tool(ToolMode.SELECT)
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_held = False
            self.viewport().setCursor(
                QCursor(_TOOL_CURSORS.get(self._tool, Qt.CursorShape.ArrowCursor))
            )
            event.accept()
            return
        super().keyReleaseEvent(event)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self.viewport_resized.emit()


# ---------------------------------------------------------------------------
# Tab 4 Root View: SurveyView
# ---------------------------------------------------------------------------


class SurveyView(QWidget):
    """Tab 4 Wi-Fi Survey Analysis & High-Res Clipped IDW Heatmaps workspace."""

    heatmap_updated = Signal(object)  # HeatmapResult

    def __init__(self, state: ProjectState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state = state
        self._syncing_controls = False
        self._heatmap_request_id = 0
        self._active_worker: HeatmapWorker | None = None
        self._retired_workers: list[HeatmapWorker] = []
        self._session_check_state: dict[str, bool] = {}

        self._build_ui()
        self._wire_signals()
        self.refresh_all(fit_view=True)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # --- Top Toolbar ---
        self.toolbar = QToolBar("Survey Analysis Tools", self)
        self.toolbar.setMovable(False)

        self.select_tool_btn = QPushButton("Select (V)", self)
        self.select_tool_btn.setCheckable(True)
        self.select_tool_btn.setChecked(True)
        self.select_tool_btn.clicked.connect(lambda: self.canvas.set_tool(ToolMode.SELECT))
        self.toolbar.addWidget(self.select_tool_btn)

        self.pan_tool_btn = QPushButton("Pan (H)", self)
        self.pan_tool_btn.setCheckable(True)
        self.pan_tool_btn.clicked.connect(lambda: self.canvas.set_tool(ToolMode.PAN))
        self.toolbar.addWidget(self.pan_tool_btn)
        self.toolbar.addSeparator()

        self.import_btn = QPushButton("Import Survey / .netviz...", self)
        self.toolbar.addWidget(self.import_btn)
        self.toolbar.addSeparator()

        self.toolbar.addWidget(QLabel("Dead-Zone Threshold:", self))
        self.dead_zone_slider = QSlider(Qt.Orientation.Horizontal, self)
        self.dead_zone_slider.setRange(-90, -50)
        self.dead_zone_slider.setValue(-70)
        self.dead_zone_slider.setFixedWidth(130)
        self.toolbar.addWidget(self.dead_zone_slider)

        self.dead_zone_value_lbl = QLabel("-70 dBm", self)
        self.dead_zone_value_lbl.setStyleSheet("font-weight: 600; color: #F8FAFC; padding: 0 4px;")
        self.toolbar.addWidget(self.dead_zone_value_lbl)

        self.coverage_kpi_label = QLabel("Coverage ≥ -70 dBm: 0.0% (0 samples)", self)
        self.coverage_kpi_label.setStyleSheet(
            "background-color: #0F172A; border: 1px solid #334155; border-radius: 5px; "
            "padding: 3px 10px; font-weight: 700; color: #38BDF8;"
        )
        self.toolbar.addWidget(self.coverage_kpi_label)
        self.toolbar.addSeparator()

        self.toolbar.addWidget(QLabel("View Rot:", self))
        for delta, lbl in ((-90.0, "-90°"), (-15.0, "-15°"), (15.0, "+15°"), (90.0, "+90°")):
            btn = QPushButton(lbl, self)
            btn.clicked.connect(
                lambda _=False, d=delta: self.canvas.set_view_rotation(
                    self.canvas.view_rotation_deg + d
                )
            )
            self.toolbar.addWidget(btn)

        self.view_rot_spin = QDoubleSpinBox(self)
        self.view_rot_spin.setRange(-180.0, 180.0)
        self.view_rot_spin.setDecimals(1)
        self.view_rot_spin.setSuffix("°")
        self.view_rot_spin.valueChanged.connect(
            lambda v: self.canvas.set_view_rotation(v, commit_to_state=True)
        )
        self.toolbar.addWidget(self.view_rot_spin)

        self.fit_btn = QPushButton("Fit View (F)", self)
        self.toolbar.addWidget(self.fit_btn)

        root.addWidget(self.toolbar)

        # --- Splitter: Left Sidebar | Survey Canvas ---
        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)

        self.sidebar = QWidget(self)
        self.sidebar.setMinimumWidth(280)
        self.sidebar.setMaximumWidth(380)
        sb_layout = QVBoxLayout(self.sidebar)
        sb_layout.setContentsMargins(8, 8, 8, 8)
        sb_layout.setSpacing(8)

        # Building & Floor Selector
        bf_grp = QGroupBox("Building & Floor", self.sidebar)
        bf_layout = QVBoxLayout(bf_grp)
        self.building_combo = QComboBox(bf_grp)
        self.floor_combo = QComboBox(bf_grp)
        bf_layout.addWidget(QLabel("Building:", bf_grp))
        bf_layout.addWidget(self.building_combo)
        bf_layout.addWidget(QLabel("Floor:", bf_grp))
        bf_layout.addWidget(self.floor_combo)
        sb_layout.addWidget(bf_grp)

        # Multi-Session Checkable List
        sess_grp = QGroupBox("Survey Sessions (Check to Merge)", self.sidebar)
        sess_layout = QVBoxLayout(sess_grp)
        self.sessions_list = QListWidget(sess_grp)
        self.sessions_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        sess_layout.addWidget(self.sessions_list)

        sess_btns = QHBoxLayout()
        self.check_all_btn = QPushButton("Select All", sess_grp)
        self.delete_session_btn = QPushButton("Delete Session", sess_grp)
        sess_btns.addWidget(self.check_all_btn)
        sess_btns.addWidget(self.delete_session_btn)
        sess_layout.addLayout(sess_btns)
        sb_layout.addWidget(sess_grp, 1)

        # Signal Filters
        flt_grp = QGroupBox("Signal Filters", self.sidebar)
        flt_layout = QVBoxLayout(flt_grp)
        self.ssid_combo = QComboBox(flt_grp)
        self.bssid_combo = QComboBox(flt_grp)
        self.band_combo = QComboBox(flt_grp)
        self.band_combo.addItem("All Bands", userData=None)
        self.band_combo.addItem("2.4 GHz", userData="2.4")
        self.band_combo.addItem("5 GHz", userData="5")
        self.band_combo.addItem("6 GHz", userData="6")

        flt_layout.addWidget(QLabel("Target SSID:", flt_grp))
        flt_layout.addWidget(self.ssid_combo)
        flt_layout.addWidget(QLabel("Specific AP / BSSID:", flt_grp))
        flt_layout.addWidget(self.bssid_combo)
        flt_layout.addWidget(QLabel("Frequency Band:", flt_grp))
        flt_layout.addWidget(self.band_combo)
        sb_layout.addWidget(flt_grp)

        # Canvas Host with floating dBm Legend & Imported Toast Banner
        self.canvas = SurveyCanvasView(self._state, self)
        self.legend = DbmLegendWidget(self.canvas.viewport())
        self.legend.show()

        self.toast_banner = QFrame(self.canvas.viewport())
        self.toast_banner.setStyleSheet(
            "background-color: rgba(6, 78, 59, 235); border: 1px solid #22C55E; "
            "border-radius: 6px; padding: 6px 12px;"
        )
        toast_row = QHBoxLayout(self.toast_banner)
        toast_row.setContentsMargins(8, 4, 8, 4)
        self.toast_label = QLabel("", self.toast_banner)
        self.toast_label.setStyleSheet("color: #ECFDF5; font-weight: 600;")
        toast_close = QPushButton("Dismiss", self.toast_banner)
        toast_close.clicked.connect(self.toast_banner.hide)
        toast_row.addWidget(self.toast_label, 1)
        toast_row.addWidget(toast_close)
        self.toast_banner.hide()

        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self.toast_banner.hide)

        self.splitter.addWidget(self.sidebar)
        self.splitter.addWidget(self.canvas)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        root.addWidget(self.splitter, 1)

        QShortcut(QKeySequence("F"), self, activated=self.canvas.fit_floor_in_view)

    def _wire_signals(self) -> None:
        self.import_btn.clicked.connect(self._on_import_clicked)
        self.building_combo.currentIndexChanged.connect(self._on_building_combo_changed)
        self.floor_combo.currentIndexChanged.connect(self._on_floor_combo_changed)
        self.sessions_list.itemChanged.connect(self._on_session_item_changed)
        self.sessions_list.customContextMenuRequested.connect(self._on_session_context_menu)
        self.check_all_btn.clicked.connect(self._check_all_sessions)
        self.delete_session_btn.clicked.connect(self._delete_selected_session)

        self.ssid_combo.currentIndexChanged.connect(self.recompute_heatmap)
        self.bssid_combo.currentIndexChanged.connect(self.recompute_heatmap)
        self.band_combo.currentIndexChanged.connect(self.recompute_heatmap)
        self.dead_zone_slider.valueChanged.connect(self._on_dead_zone_changed)
        self.fit_btn.clicked.connect(self.canvas.fit_floor_in_view)

        self.canvas.tool_changed.connect(self._on_canvas_tool_changed)
        self.canvas.view_rotation_changed.connect(self._on_canvas_view_rotation_changed)
        self.canvas.inspect_sample_requested.connect(self._on_inspect_sample)
        self.canvas.delete_sample_requested.connect(self.delete_sample)
        self.canvas.viewport_resized.connect(self._reposition_overlays)

        self._state.project_reloaded.connect(lambda: self.refresh_all(fit_view=True))
        self._state.buildings_changed.connect(self._on_state_buildings_changed)
        self._state.floor_geometry_changed.connect(self._on_state_floor_geometry_changed)
        self._state.floor_view_rotated.connect(self._on_state_floor_view_rotated)
        self._state.components_changed.connect(self._on_state_components_changed)
        self._state.surveys_changed.connect(self._on_state_surveys_changed)

    # ------------------------------------------------------------------
    # Public Helpers (Toast, Floor Jump, Import, Delete Sample)
    # ------------------------------------------------------------------

    def active_building_id(self) -> str | None:
        return self.canvas._building_id

    def active_floor_id(self) -> str | None:
        return self.canvas._floor_id

    def show_imported_toast(
        self,
        session_name: str,
        sample_count: int,
        *,
        duration_ms: int = 5500,
    ) -> None:
        """Display a non-blocking banner when a survey session is imported or received."""
        self.toast_label.setText(
            f"Survey imported: '{session_name}' ({sample_count} samples)"
        )
        self.toast_banner.adjustSize()
        self._reposition_overlays()
        self.toast_banner.show()
        self._toast_timer.start(duration_ms)

    def select_building_floor(self, building_id: str, floor_id: str) -> None:
        """Switch active building and floor across combos, canvas, pins, and heatmap."""
        b_idx = self.building_combo.findData(building_id)
        if b_idx >= 0:
            self._syncing_controls = True
            try:
                self.building_combo.setCurrentIndex(b_idx)
                self._populate_floor_combo(building_id)
                f_idx = self.floor_combo.findData(floor_id)
                if f_idx >= 0:
                    self.floor_combo.setCurrentIndex(f_idx)
            finally:
                self._syncing_controls = False
        self.canvas.set_floor_context(building_id, floor_id, fit_view=True)
        self._populate_filter_combos()
        self.recompute_heatmap()

    def focus_floor(self, building_id: str, floor_id: str) -> None:
        """Alias for MainWindow / Command Palette jumps to a floor in Tab 4."""
        self.select_building_floor(building_id, floor_id)

    def import_from_path(self, path: str | Path) -> int:
        """Import a .netviz-survey.json or .netviz file into ProjectState with Undo support."""
        src = Path(path)
        imported_count = 0
        last_name = src.name
        last_samples = 0

        if src.suffix.lower() == ".netviz":
            bundle = load_netviz(src)
            for sess in bundle.project.surveys:
                self._state.import_survey_session(sess)
                imported_count += 1
                last_name = sess.name
                last_samples = len(sess.samples)
        else:
            session = import_survey_session(src)
            self._state.import_survey_session(session)
            imported_count = 1
            last_name = session.name
            last_samples = len(session.samples)

        if imported_count > 0:
            self.show_imported_toast(last_name, last_samples)
        return imported_count

    def delete_sample(self, sample_id: str) -> None:
        """Delete an outlier SurveySample via QUndoStack."""
        self._state.delete_survey_samples([sample_id])

    def checked_session_ids(self) -> set[str]:
        ids: set[str] = set()
        for i in range(self.sessions_list.count()):
            it = self.sessions_list.item(i)
            if it.checkState() == Qt.CheckState.Checked:
                sid = it.data(Qt.ItemDataRole.UserRole)
                if sid:
                    ids.add(str(sid))
        return ids

    def set_session_checked(self, session_id: str, checked: bool) -> None:
        """Programmatically toggle a session's checked state in the merge list."""
        for i in range(self.sessions_list.count()):
            it = self.sessions_list.item(i)
            if str(it.data(Qt.ItemDataRole.UserRole) or "") == session_id:
                it.setCheckState(
                    Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
                )
                return

    # ------------------------------------------------------------------
    # Refresh & Population
    # ------------------------------------------------------------------

    def refresh_all(self, *, fit_view: bool = False) -> None:
        self._populate_building_and_floor_combos()
        b_id = self.building_combo.currentData()
        f_id = self.floor_combo.currentData()
        self.canvas.set_floor_context(
            str(b_id) if b_id else None,
            str(f_id) if f_id else None,
            fit_view=fit_view,
        )
        self._populate_sessions_list()
        self._populate_filter_combos()
        self.recompute_heatmap()
        self._reposition_overlays()

    def _populate_building_and_floor_combos(self) -> None:
        prev_b = self.canvas._building_id or self._state.selected_building_id
        prev_f = self.canvas._floor_id or self._state.selected_floor_id
        self._syncing_controls = True
        try:
            self.building_combo.clear()
            for b in self._state.project.buildings:
                self.building_combo.addItem(b.name, userData=b.id)
            if self.building_combo.count() > 0:
                idx = self.building_combo.findData(prev_b) if prev_b else 0
                self.building_combo.setCurrentIndex(max(0, idx))
                active_b = str(self.building_combo.currentData())
                self._populate_floor_combo(active_b, preferred_floor_id=prev_f)
            else:
                self.floor_combo.clear()
        finally:
            self._syncing_controls = False

    def _populate_floor_combo(
        self,
        building_id: str,
        *,
        preferred_floor_id: str | None = None,
    ) -> None:
        self.floor_combo.clear()
        bldg = self._state.project.get_building(building_id)
        if bldg is None:
            return
        for fl in sorted(bldg.floors, key=lambda f: f.level_index):
            self.floor_combo.addItem(f"{fl.name} (L{fl.level_index})", userData=fl.id)
        if self.floor_combo.count() > 0:
            idx = self.floor_combo.findData(preferred_floor_id) if preferred_floor_id else 0
            self.floor_combo.setCurrentIndex(max(0, idx))

    def _populate_sessions_list(self) -> None:
        self._syncing_controls = True
        try:
            self.sessions_list.clear()
            for sess in self._state.project.surveys:
                checked = self._session_check_state.get(sess.id, True)
                self._session_check_state[sess.id] = checked
                item = QListWidgetItem(f"{sess.name} ({len(sess.samples)} samples)")
                item.setData(Qt.ItemDataRole.UserRole, sess.id)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(
                    Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
                )
                item.setToolTip(
                    f"Device: {sess.device_model or 'Unknown'}\nCreated: {sess.created_utc}"
                )
                self.sessions_list.addItem(item)
        finally:
            self._syncing_controls = False

    def _populate_filter_combos(self) -> None:
        prev_ssid = self.ssid_combo.currentData()
        prev_bssid = self.bssid_combo.currentData()
        self._syncing_controls = True
        try:
            # Target SSIDs: project target SSIDs + observed survey SSIDs
            observed_ssids = {
                r.ssid.strip()
                for sess in self._state.project.surveys
                for smp in sess.samples
                for r in smp.readings
                if r.ssid and r.ssid.strip()
            }
            all_ssids = sorted(set(self._state.project.get_target_ssids()) | observed_ssids)

            self.ssid_combo.clear()
            self.ssid_combo.addItem("All Project SSIDs", userData=None)
            for ssid in all_ssids:
                self.ssid_combo.addItem(ssid, userData=ssid)
            if prev_ssid is not None:
                idx = self.ssid_combo.findData(prev_ssid)
                if idx >= 0:
                    self.ssid_combo.setCurrentIndex(idx)

            # BSSIDs with friendly Component.name via project.find_component_by_mac()
            observed_bssids: dict[str, str] = {}
            for sess in self._state.project.surveys:
                for smp in sess.samples:
                    for rd in smp.readings:
                        norm = normalize_mac(rd.bssid)
                        if norm and norm not in observed_bssids:
                            observed_bssids[norm] = rd.bssid

            self.bssid_combo.clear()
            self.bssid_combo.addItem("All APs / BSSIDs", userData=None)
            for _norm, raw_bssid in sorted(observed_bssids.items()):
                comp = self._state.project.find_component_by_mac(raw_bssid)
                label = f"{comp.name} ({raw_bssid})" if comp is not None else raw_bssid
                self.bssid_combo.addItem(label, userData=raw_bssid)
            if prev_bssid is not None:
                idx = self.bssid_combo.findData(prev_bssid)
                if idx >= 0:
                    self.bssid_combo.setCurrentIndex(idx)
        finally:
            self._syncing_controls = False

    # ------------------------------------------------------------------
    # Heatmap & Pin Computation (Off-UI-Thread via QThread)
    # ------------------------------------------------------------------

    def _merged_checked_samples(self) -> list[SurveySample]:
        checked_ids = self.checked_session_ids()
        merged: list[SurveySample] = []
        for sess in self._state.project.surveys:
            if sess.id in checked_ids:
                merged.extend(sess.samples)
        return merged

    def _current_filter_args(
        self,
    ) -> tuple[list[str] | None, str | None, str | None, float]:
        ssid_data = self.ssid_combo.currentData()
        target_ssids = [str(ssid_data)] if ssid_data else None
        bssid_data = self.bssid_combo.currentData()
        bssid_filter = str(bssid_data) if bssid_data else None
        band_data = self.band_combo.currentData()
        band_filter = str(band_data) if band_data else None
        dead_zone_dbm = float(self.dead_zone_slider.value())
        return target_ssids, bssid_filter, band_filter, dead_zone_dbm

    def recompute_heatmap(self) -> None:
        floor = self.canvas.current_floor()
        samples = self._merged_checked_samples()
        target_ssids, bssid_filter, band_filter, dead_zone_dbm = self._current_filter_args()

        self.canvas.set_sample_pins(
            samples,
            target_ssids=target_ssids,
            bssid_filter=bssid_filter,
            band_filter=band_filter,
        )

        floor_sample_count = (
            sum(1 for s in samples if s.floor_id == floor.id) if floor is not None else 0
        )

        if floor is None or len(floor.outer_polygon_m) < 3:
            self.coverage_kpi_label.setText(
                f"Coverage ≥ {dead_zone_dbm:.0f} dBm: 0.0% ({floor_sample_count} samples)"
            )
            return

        self._heatmap_request_id += 1
        req_id = self._heatmap_request_id

        if self._active_worker is not None and self._active_worker.isRunning():
            self._active_worker.requestInterruption()
            self._retired_workers.append(self._active_worker)

        worker = HeatmapWorker(
            req_id,
            floor,
            samples,
            target_ssids=target_ssids,
            bssid_filter=bssid_filter,
            band_filter=band_filter,
            resolution_m=0.25,
            dead_zone_dbm=dead_zone_dbm,
            parent=self,
        )
        worker.heatmap_ready.connect(self._on_worker_heatmap_ready)
        worker.finished.connect(lambda w=worker: self._retire_worker(w))
        self._active_worker = worker
        worker.start()

    def _retire_worker(self, worker: HeatmapWorker) -> None:
        if worker in self._retired_workers:
            self._retired_workers.remove(worker)
        if worker is not self._active_worker:
            worker.deleteLater()

    def _on_worker_heatmap_ready(self, request_id: int, result_obj: object) -> None:
        if request_id != self._heatmap_request_id:
            return
        if not isinstance(result_obj, HeatmapResult):
            return
        self.canvas.apply_heatmap_result(result_obj)
        floor = self.canvas.current_floor()
        samples = self._merged_checked_samples()
        floor_sample_count = (
            sum(1 for s in samples if s.floor_id == floor.id) if floor is not None else 0
        )
        thresh = self.dead_zone_slider.value()
        self.coverage_kpi_label.setText(
            f"Coverage ≥ {thresh} dBm: {result_obj.coverage_percent:.1f}% "
            f"({floor_sample_count} samples)"
        )
        self.heatmap_updated.emit(result_obj)

    # ------------------------------------------------------------------
    # Event & Signal Handlers
    # ------------------------------------------------------------------

    def _on_import_clicked(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self,
            "Import Survey Session or NetViz Archive",
            "",
            "NetViz Survey / Project (*.netviz-survey.json *.json *.netviz)",
        )
        if not path_str:
            return
        try:
            self.import_from_path(path_str)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Import Survey Failed", str(exc))

    def _on_building_combo_changed(self) -> None:
        if self._syncing_controls:
            return
        b_id = self.building_combo.currentData()
        self._syncing_controls = True
        try:
            self._populate_floor_combo(str(b_id) if b_id else "")
        finally:
            self._syncing_controls = False
        f_id = self.floor_combo.currentData()
        self.canvas.set_floor_context(
            str(b_id) if b_id else None,
            str(f_id) if f_id else None,
            fit_view=True,
        )
        self._populate_filter_combos()
        self.recompute_heatmap()

    def _on_floor_combo_changed(self) -> None:
        if self._syncing_controls:
            return
        b_id = self.building_combo.currentData()
        f_id = self.floor_combo.currentData()
        self.canvas.set_floor_context(
            str(b_id) if b_id else None,
            str(f_id) if f_id else None,
            fit_view=True,
        )
        self._populate_filter_combos()
        self.recompute_heatmap()

    def _on_session_item_changed(self, item: QListWidgetItem) -> None:
        if self._syncing_controls:
            return
        sid = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if sid:
            self._session_check_state[sid] = item.checkState() == Qt.CheckState.Checked
        self.recompute_heatmap()

    def _check_all_sessions(self) -> None:
        self._syncing_controls = True
        try:
            for i in range(self.sessions_list.count()):
                it = self.sessions_list.item(i)
                it.setCheckState(Qt.CheckState.Checked)
                sid = str(it.data(Qt.ItemDataRole.UserRole) or "")
                if sid:
                    self._session_check_state[sid] = True
        finally:
            self._syncing_controls = False
        self.recompute_heatmap()

    def _delete_selected_session(self) -> None:
        item = self.sessions_list.currentItem()
        if item is None:
            return
        sid = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if sid:
            self._state.delete_survey_session(sid)

    def _on_session_context_menu(self, pos: QPoint) -> None:
        item = self.sessions_list.itemAt(pos)
        if item is None:
            return
        sid = str(item.data(Qt.ItemDataRole.UserRole) or "")
        menu = QMenu(self)
        del_act = menu.addAction("Delete Survey Session")
        chosen = menu.exec(self.sessions_list.viewport().mapToGlobal(pos))
        if chosen == del_act and sid:
            self._state.delete_survey_session(sid)

    def _on_dead_zone_changed(self, value: int) -> None:
        self.dead_zone_value_lbl.setText(f"{value} dBm")
        self.recompute_heatmap()

    def _on_canvas_tool_changed(self, tool_val: str) -> None:
        self.select_tool_btn.setChecked(tool_val == ToolMode.SELECT.value)
        self.pan_tool_btn.setChecked(tool_val == ToolMode.PAN.value)

    def _on_canvas_view_rotation_changed(self, angle: float) -> None:
        self.view_rot_spin.blockSignals(True)
        self.view_rot_spin.setValue(angle)
        self.view_rot_spin.blockSignals(False)

    def _on_inspect_sample(self, sample_obj: object) -> None:
        if not isinstance(sample_obj, SurveySample):
            return
        floor = self.canvas.current_floor()
        room = find_room_at_point(sample_obj.pos_m, floor) if floor is not None else None
        room_name = room.name if room is not None else "Open Floor"
        dlg = _SampleReadingsDialog(
            sample_obj, self._state.project, room_name, parent=self
        )
        dlg.exec()

    def _on_state_buildings_changed(self) -> None:
        self._populate_building_and_floor_combos()
        b_id = self.building_combo.currentData()
        f_id = self.floor_combo.currentData()
        self.canvas.set_floor_context(
            str(b_id) if b_id else None,
            str(f_id) if f_id else None,
            fit_view=False,
        )
        self.recompute_heatmap()

    def _on_state_floor_geometry_changed(self, building_id: str, floor_id: str) -> None:
        if building_id == self.canvas._building_id and floor_id == self.canvas._floor_id:
            self.canvas.rebuild_floor_geometry()
            self.recompute_heatmap()

    def _on_state_floor_view_rotated(
        self, building_id: str, floor_id: str, rot: float
    ) -> None:
        if building_id == self.canvas._building_id and floor_id == self.canvas._floor_id:
            if abs(self.canvas.view_rotation_deg - rot) > 1e-6:
                self.canvas.set_view_rotation(rot, commit_to_state=False)

    def _on_state_components_changed(self) -> None:
        self._populate_filter_combos()
        self.canvas.rebuild_floor_geometry()
        self.recompute_heatmap()

    def _on_state_surveys_changed(self) -> None:
        self._populate_sessions_list()
        self._populate_filter_combos()
        self.recompute_heatmap()

    def _reposition_overlays(self) -> None:
        vp = self.canvas.viewport()
        margin = 14
        self.legend.move(
            max(margin, vp.width() - self.legend.width() - margin),
            max(margin, vp.height() - self.legend.height() - margin),
        )
        if self.toast_banner.isVisible():
            tw = min(vp.width() - 40, max(320, self.toast_banner.sizeHint().width()))
            self.toast_banner.setFixedWidth(tw)
            self.toast_banner.move((vp.width() - tw) // 2, 12)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._reposition_overlays()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        for w in [self._active_worker, *self._retired_workers]:
            if w is not None and w.isRunning():
                w.requestInterruption()
                w.wait(1500)
        super().closeEvent(event)
