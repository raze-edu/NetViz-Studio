"""Tab 3 Live-Linked Physical Mapping, Riser Routing, Cable BOM & Pre-Survey Coverage."""

from __future__ import annotations

import copy
import math
from collections.abc import Callable
from enum import StrEnum

from PySide6.QtCore import QMimeData, QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QDrag,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QFont,
    QFontMetricsF,
    QImage,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
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
    QDoubleSpinBox,
    QFrame,
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsLineItem,
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
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSplitter,
    QStyleOptionGraphicsItem,
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
    scene_to_local_m,
)
from desktop.state import ProjectState
from desktop.ui.logical_tab.graphics_items import CablePortPillItem, qt_pen_style
from desktop.ui.logical_tab.routing_controller import (
    choose_bundle_entry_exit,
    offset_polyline_by_normal,
)
from desktop.ui.theme import component_color, port_color
from shared.geo_math import (
    calculate_project_cable_bom,
    polygon_bounds,
    true_north_screen_cw_deg,
)
from shared.heatmap import HeatmapResult, estimate_presurvey_heatmap
from shared.models import (
    BlueprintUnderlay,
    Building,
    Cable,
    CableBundle,
    Component,
    Door,
    Floor,
    PhysicalCableSegment,
    Point2D,
    Stairs,
    VerticalHole,
)

COMPONENT_MIME_TYPE = "application/x-netviz-component-id"

Z_BLUEPRINT = -40.0
Z_FLOOR_OUTER = 0.0
Z_ROOM = 10.0
Z_HEATMAP = 14.0
Z_ROOM_LABEL = 16.0
Z_STAIRS = 20.0
Z_HOLE = 25.0
Z_DOOR = 28.0
Z_CABLE = 35.0
Z_PORT_PILL = 45.0
Z_COMP = 50.0
Z_CABLE_ACTIVE = 60.0
Z_BADGE = 65.0
Z_HANDLE = 70.0


class ToolMode(StrEnum):
    """Strict non-panning tool modes for Tab 3 physical canvas."""

    SELECT = "select"
    PAN = "pan"


_TOOL_CURSORS: dict[ToolMode, Qt.CursorShape] = {
    ToolMode.SELECT: Qt.CursorShape.ArrowCursor,
    ToolMode.PAN: Qt.CursorShape.OpenHandCursor,
}


def _poly_to_qpolygon(pts_m: list[Point2D]) -> QPolygonF:
    return QPolygonF([local_m_to_scene(pt) for pt in pts_m])


def _points_close(a: Point2D, b: Point2D, tol: float = 1e-3) -> bool:
    return math.hypot(a.x - b.x, a.y - b.y) <= tol


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


def _point_along_cable(a: QPointF, b: QPointF, dist_px: float = 20.0) -> QPointF:
    dx = b.x() - a.x()
    dy = b.y() - a.y()
    length = math.hypot(dx, dy)
    if length < 1e-4:
        return QPointF(a.x(), a.y() + dist_px)
    step = min(dist_px, 0.4 * length)
    return QPointF(a.x() + (dx / length) * step, a.y() + (dy / length) * step)


def _point_to_segment_distance_sq(pt: QPointF, a: QPointF, b: QPointF) -> float:
    dx = b.x() - a.x()
    dy = b.y() - a.y()
    len_sq = dx * dx + dy * dy
    if len_sq < 1e-9:
        return (pt.x() - a.x()) ** 2 + (pt.y() - a.y()) ** 2
    t = max(0.0, min(1.0, ((pt.x() - a.x()) * dx + (pt.y() - a.y()) * dy) / len_sq))
    proj_x = a.x() + t * dx
    proj_y = a.y() + t * dy
    return (pt.x() - proj_x) ** 2 + (pt.y() - proj_y) ** 2


def _best_insert_index(pt_m: Point2D, polyline_m: list[Point2D]) -> int:
    """Return index inside intermediate waypoints where pt_m fits best along polyline_m."""
    if len(polyline_m) < 2:
        return 0
    best_seg = 0
    best_d2 = math.inf
    target = QPointF(pt_m.x, pt_m.y)
    for i in range(len(polyline_m) - 1):
        a = QPointF(polyline_m[i].x, polyline_m[i].y)
        b = QPointF(polyline_m[i + 1].x, polyline_m[i + 1].y)
        d2 = _point_to_segment_distance_sq(target, a, b)
        if d2 < best_d2:
            best_d2 = d2
            best_seg = i
    return best_seg


def find_best_riser_holes(
    floor_a: Floor,
    floor_b: Floor,
    pos_a: Point2D,
    pos_b: Point2D,
) -> tuple[VerticalHole, VerticalHole] | None:
    """Return matching (hole_a, hole_b) sharing riser_group_id minimizing horizontal distance."""
    best_dist = math.inf
    best_pair: tuple[VerticalHole, VerticalHole] | None = None
    for hole_a in floor_a.holes:
        if not hole_a.riser_group_id:
            continue
        for hole_b in floor_b.holes:
            if hole_b.riser_group_id == hole_a.riser_group_id:
                d = math.hypot(
                    pos_a.x - hole_a.position_m.x, pos_a.y - hole_a.position_m.y
                ) + math.hypot(hole_b.position_m.x - pos_b.x, hole_b.position_m.y - pos_b.y)
                if d < best_dist:
                    best_dist = d
                    best_pair = (hole_a, hole_b)
    return best_pair


def _find_hole_by_id(floor: Floor | None, hole_id: str | None) -> VerticalHole | None:
    if floor is None or hole_id is None:
        return None
    return next((h for h in floor.holes if h.id == hole_id), None)


def _extract_intermediate_waypoints(
    seg: PhysicalCableSegment,
    start_anchor: Point2D | None,
    end_anchor: Point2D | None,
) -> list[Point2D]:
    """Extract editable intermediate waypoints, stripping duplicate endpoint anchors."""
    pts = [Point2D(p.x, p.y) for p in seg.waypoints_m]
    if pts and start_anchor is not None and _points_close(pts[0], start_anchor):
        pts.pop(0)
    if pts and end_anchor is not None and _points_close(pts[-1], end_anchor):
        pts.pop()
    return pts


def _floor_center_m(floor: Floor | None) -> Point2D:
    if floor is None or not floor.outer_polygon_m:
        return Point2D(0.0, 0.0)
    min_x, min_y, max_x, max_y = polygon_bounds(floor.outer_polygon_m)
    return Point2D(0.5 * (min_x + max_x), 0.5 * (min_y + max_y))


def _format_port_type_label(port_type_val: str) -> str:
    return port_type_val


# ---------------------------------------------------------------------------
# Canvas Graphics Items
# ---------------------------------------------------------------------------


class PhysicalComponentItem(QGraphicsItem):
    """Draggable placed Component marker on a physical floor plan (z = 50)."""

    def __init__(
        self,
        component: Component,
        *,
        on_moved_live: Callable[[str, Point2D], None] | None = None,
        on_move_released: Callable[[str, Point2D], None] | None = None,
        on_context_action: Callable[[str, str], None] | None = None,
    ) -> None:
        super().__init__()
        self.component = component
        self._on_moved_live = on_moved_live
        self._on_move_released = on_move_released
        self._on_context_action = on_context_action
        self._pulsed = False
        self._dimmed = False
        self._suppress_move_signal = False

        pos_m = component.physical_pos_m or Point2D(0.0, 0.0)
        self.setPos(local_m_to_scene(pos_m))
        self.setZValue(Z_COMP)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        self.setAcceptHoverEvents(True)
        self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))

        tip_lines = [
            f"{component.name} ({component.component_type.value.replace('_', ' ').title()})",
            f"Pos: ({pos_m.x:.2f} m, {pos_m.y:.2f} m)",
        ]
        if component.ip_address:
            tip_lines.append(f"IP: {component.ip_address}")
        if component.ssids:
            tip_lines.append(f"SSIDs: {', '.join(component.ssids)}")
        self.setToolTip("\n".join(tip_lines))

    def set_dimmed(self, dimmed: bool) -> None:
        if self._dimmed != dimmed:
            self._dimmed = dimmed
            self.update()

    def set_pulsed(self, pulsed: bool) -> None:
        if self._pulsed != pulsed:
            self._pulsed = pulsed
            self.update()

    def set_scene_pos_silently(self, pos: QPointF) -> None:
        self._suppress_move_signal = True
        try:
            self.setPos(pos)
        finally:
            self._suppress_move_signal = False

    def boundingRect(self) -> QRectF:
        return QRectF(-48.0, -24.0, 96.0, 56.0)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addRoundedRect(QRectF(-16.0, -16.0, 32.0, 32.0), 8.0, 8.0)
        return path

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value: object) -> object:
        if (
            change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged
            and not self._suppress_move_signal
            and isinstance(value, QPointF)
        ):
            if self._on_moved_live is not None:
                self._on_moved_live(self.component.id, scene_to_local_m(value))
        return super().itemChange(change, value)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        super().mouseReleaseEvent(event)
        if self._on_move_released is not None:
            self._on_move_released(self.component.id, scene_to_local_m(self.pos()))

    def contextMenuEvent(self, event) -> None:  # type: ignore[override]
        menu = QMenu()
        unplace_act = menu.addAction("Unplace from Floor")
        sel_act = menu.addAction("Select Component")
        chosen = menu.exec(event.screenPos())
        if chosen == unplace_act and self._on_context_action is not None:
            self._on_context_action("unplace", self.component.id)
        elif chosen == sel_act and self._on_context_action is not None:
            self._on_context_action("select", self.component.id)
        event.accept()

    def paint(
        self,
        painter: QPainter,
        _option: QStyleOptionGraphicsItem,
        _widget: QWidget | None = None,
    ) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._dimmed:
            painter.setOpacity(0.30)

        col = component_color(self.component.component_type)
        if self._pulsed or self.isSelected():
            painter.setPen(QPen(QColor("#F8FAFC"), 2.0))
            painter.setBrush(QColor(col.red(), col.green(), col.blue(), 80))
            painter.drawRoundedRect(QRectF(-19.0, -19.0, 38.0, 38.0), 10.0, 10.0)

        card_rect = QRectF(-15.0, -15.0, 30.0, 30.0)
        painter.setPen(QPen(QColor("#F8FAFC") if self.isSelected() else col, 2.0))
        painter.setBrush(QColor(15, 23, 42, 230))
        painter.drawRoundedRect(card_rect, 7.0, 7.0)

        # Inner colored accent dot/badge
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(col)
        painter.drawEllipse(QPointF(0.0, 0.0), 7.5, 7.5)

        # Short type glyph inside badge
        short_map = {
            "access_point": "AP",
            "active_switch": "SW",
            "passive_switch": "PS",
            "firewall": "FW",
            "router": "RT",
            "lwl_converter": "FO",
            "powerlan_adapter": "PL",
            "patch_panel": "PP",
            "device": "DV",
        }
        glyph = short_map.get(self.component.component_type.value, "ND")
        gfont = QFont("Segoe UI", 6, QFont.Weight.Bold)
        painter.setFont(gfont)
        painter.setPen(QColor("#0B1120"))
        painter.drawText(QRectF(-8.0, -8.0, 16.0, 16.0), Qt.AlignmentFlag.AlignCenter, glyph)

        # Crisp component name pill below marker
        lbl_font = QFont("Segoe UI", 8, QFont.Weight.DemiBold)
        painter.setFont(lbl_font)
        fm = QFontMetricsF(lbl_font)
        tw = min(92.0, max(36.0, fm.horizontalAdvance(self.component.name) + 10.0))
        pill_rect = QRectF(-0.5 * tw, 17.0, tw, 15.0)
        painter.setPen(QPen(col, 1.0))
        painter.setBrush(QColor(11, 17, 32, 225))
        painter.drawRoundedRect(pill_rect, 4.0, 4.0)
        painter.setPen(QColor("#F8FAFC"))
        painter.drawText(pill_rect, Qt.AlignmentFlag.AlignCenter, self.component.name)
        painter.restore()


class PhysicalWaypointHandle(QGraphicsEllipseItem):
    """Draggable waypoint handle for physical cable segments or physical bundles (z = 70)."""

    def __init__(
        self,
        pos_m: Point2D,
        *,
        kind: str,
        owner_id: str,
        floor_id: str,
        index: int,
        color_hex: str = "#38BDF8",
        on_moved_live: Callable[[str, str, str, int, Point2D], None] | None = None,
        on_released: Callable[[str, str, str, int, Point2D], None] | None = None,
        on_delete: Callable[[str, str, str, int], None] | None = None,
    ) -> None:
        super().__init__(-6.5, -6.5, 13.0, 13.0)
        self.kind = kind  # "cable_wp" | "bundle_wp" | "bundle_entry" | "bundle_exit"
        self.owner_id = owner_id
        self.floor_id = floor_id
        self.index = index
        self._on_moved_live = on_moved_live
        self._on_released = on_released
        self._on_delete = on_delete
        self._suppress = False

        self.setPos(local_m_to_scene(pos_m))
        self.setZValue(Z_HANDLE)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        self.setPen(QPen(QColor("#0F172A"), 1.6))
        self.setBrush(QBrush(QColor(color_hex)))
        self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
        self.setToolTip(
            "Drag to move physical waypoint · Right-click to delete"
            if kind in ("cable_wp", "bundle_wp")
            else "Drag to reposition physical bundle trunk endpoint"
        )

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value: object) -> object:
        if (
            change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged
            and not self._suppress
            and isinstance(value, QPointF)
        ):
            if self._on_moved_live is not None:
                self._on_moved_live(
                    self.kind,
                    self.owner_id,
                    self.floor_id,
                    self.index,
                    scene_to_local_m(value),
                )
        return super().itemChange(change, value)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        super().mouseReleaseEvent(event)
        if self._on_released is not None:
            self._on_released(
                self.kind,
                self.owner_id,
                self.floor_id,
                self.index,
                scene_to_local_m(self.pos()),
            )

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.RightButton:
            if self._on_delete is not None and self.kind in ("cable_wp", "bundle_wp"):
                self._on_delete(self.kind, self.owner_id, self.floor_id, self.index)
            event.accept()
            return
        super().mousePressEvent(event)


class RiserJumpBadgeItem(QGraphicsItem):
    """Clickable/double-clickable floor-jump badge near a vertical riser hole (z = 65)."""

    def __init__(
        self,
        text: str,
        pos_scene: QPointF,
        target_building_id: str,
        target_floor_id: str,
        cable_id: str,
        *,
        y_offset_px: float = 0.0,
        on_jump_requested: Callable[[str, str], None] | None = None,
    ) -> None:
        super().__init__()
        self.badge_text = text
        self.target_building_id = target_building_id
        self.target_floor_id = target_floor_id
        self.cable_id = cable_id
        self._on_jump_requested = on_jump_requested
        self._hovered = False

        self.setPos(pos_scene.x(), pos_scene.y() + y_offset_px)
        self.setZValue(Z_BADGE)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        self.setAcceptHoverEvents(True)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setToolTip(f"{text}\nDouble-click to switch directly to that floor")

        self._font = QFont("Segoe UI", 8, QFont.Weight.Bold)
        fm = QFontMetricsF(self._font)
        w = max(92.0, fm.horizontalAdvance(self.badge_text) + 18.0)
        self._rect = QRectF(14.0, -11.0, w, 22.0)

    def boundingRect(self) -> QRectF:
        return self._rect.adjusted(-2.0, -2.0, 2.0, 2.0)

    def hoverEnterEvent(self, event) -> None:  # type: ignore[override]
        self._hovered = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:  # type: ignore[override]
        self._hovered = False
        self.update()
        super().hoverLeaveEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            if self._on_jump_requested is not None:
                self._on_jump_requested(self.target_building_id, self.target_floor_id)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def paint(
        self,
        painter: QPainter,
        _option: QStyleOptionGraphicsItem,
        _widget: QWidget | None = None,
    ) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        border = QColor("#FDE047") if self._hovered else QColor("#F59E0B")
        fill = QColor(30, 41, 59, 245) if self._hovered else QColor(15, 23, 42, 235)
        painter.setPen(QPen(border, 1.8 if self._hovered else 1.3))
        painter.setBrush(fill)
        painter.drawRoundedRect(self._rect, 5.0, 5.0)
        painter.setFont(self._font)
        painter.setPen(QColor("#FEF08A"))
        painter.drawText(self._rect, Qt.AlignmentFlag.AlignCenter, self.badge_text)
        painter.restore()


class PhysicalCableItem(QGraphicsPathItem):
    """Physical cable polyline rendered on a floor plan with port pills and trace metadata."""

    def __init__(
        self,
        cable: Cable,
        *,
        floor_id: str,
        start_m: Point2D,
        end_m: Point2D,
        waypoints_m: list[Point2D],
        src_port_label: str,
        src_port_color: QColor,
        dst_port_label: str | None,
        dst_port_color: QColor | None,
        trace_text: str,
        bundle: CableBundle | None = None,
        bundle_member_index: int = 0,
        bundle_member_count: int = 1,
        is_riser_stub: bool = False,
        on_add_waypoint: Callable[[str, QPointF], None] | None = None,
        on_hover_changed: Callable[[str, bool], None] | None = None,
    ) -> None:
        super().__init__()
        self.cable = cable
        self.floor_id = floor_id
        self.start_m = start_m
        self.end_m = end_m
        self.waypoints_m = list(waypoints_m)
        self.trace_text = trace_text
        self.bundle = bundle
        self.bundle_member_index = bundle_member_index
        self.bundle_member_count = bundle_member_count
        self.is_riser_stub = is_riser_stub
        self._on_add_waypoint = on_add_waypoint
        self._on_hover_changed = on_hover_changed

        self._hovered = False
        self._traced = False
        self._dimmed = False
        self._points_scene: list[QPointF] = []

        self.setZValue(Z_CABLE)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)
        self.setToolTip(f"{trace_text}\nDouble-click to add physical waypoint")

        self.src_pill = CablePortPillItem(src_port_label, src_port_color)
        self.src_pill.setZValue(Z_PORT_PILL)
        self.dst_pill = (
            CablePortPillItem(dst_port_label, dst_port_color or QColor("#38BDF8"))
            if dst_port_label
            else None
        )
        if self.dst_pill is not None:
            self.dst_pill.setZValue(Z_PORT_PILL)

        self.rebuild_path()

    def endpoints_scene(self) -> tuple[QPointF, QPointF]:
        return (local_m_to_scene(self.start_m), local_m_to_scene(self.end_m))

    def current_polyline_m(self) -> list[Point2D]:
        return [self.start_m, *self.waypoints_m, self.end_m]

    def rebuild_path(self) -> None:
        if self.bundle is None or self.is_riser_stub:
            pts_m = [self.start_m, *self.waypoints_m, self.end_m]
        else:
            anchor_src, anchor_dst, rev = choose_bundle_entry_exit(
                self.start_m,
                self.end_m,
                self.bundle.entry_point,
                self.bundle.exit_point,
            )
            trunk = [anchor_src]
            wps = list(reversed(self.bundle.waypoints)) if rev else list(self.bundle.waypoints)
            trunk.extend(wps)
            trunk.append(anchor_dst)
            if not self.bundle.collapsed and self.bundle_member_count > 1:
                offset_m = (
                    self.bundle_member_index - (self.bundle_member_count - 1) / 2.0
                ) * 0.14
                trunk = offset_polyline_by_normal(trunk, offset_m)
            pts_m = [self.start_m, *trunk, self.end_m]

        pts_scene = [local_m_to_scene(p) for p in pts_m]
        self._points_scene = pts_scene

        path = QPainterPath()
        if pts_scene:
            path.moveTo(pts_scene[0])
            for pt in pts_scene[1:]:
                path.lineTo(pt)
        self.setPath(path)

        if len(pts_scene) >= 2:
            self.src_pill.setPos(_point_along_cable(pts_scene[0], pts_scene[1], 22.0))
            if self.dst_pill is not None:
                self.dst_pill.setPos(_point_along_cable(pts_scene[-1], pts_scene[-2], 22.0))

    def set_trace_state(
        self,
        *,
        hovered: bool | None = None,
        traced: bool | None = None,
        dimmed: bool | None = None,
    ) -> None:
        if hovered is not None:
            self._hovered = hovered
        if traced is not None:
            self._traced = traced
        if dimmed is not None:
            self._dimmed = dimmed
        active = self._hovered or self._traced or self.isSelected()
        self.setZValue(Z_CABLE_ACTIVE if active else Z_CABLE)
        self.src_pill.set_highlighted(active)
        self.src_pill.set_dimmed(self._dimmed and not active)
        if self.dst_pill is not None:
            self.dst_pill.set_highlighted(active)
            self.dst_pill.set_dimmed(self._dimmed and not active)
        self.update()

    def hoverEnterEvent(self, event) -> None:  # type: ignore[override]
        if self._on_hover_changed is not None:
            self._on_hover_changed(self.cable.id, True)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:  # type: ignore[override]
        if self._on_hover_changed is not None:
            self._on_hover_changed(self.cable.id, False)
        super().hoverLeaveEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton and self._on_add_waypoint is not None:
            self._on_add_waypoint(self.cable.id, event.scenePos())
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def shape(self) -> QPainterPath:
        stroker = QPainterPathStroker()
        stroker.setWidth(max(14.0, self.cable.style.width_px + 10.0))
        return stroker.createStroke(self.path())

    def paint(
        self,
        painter: QPainter,
        _option: QStyleOptionGraphicsItem,
        _widget: QWidget | None = None,
    ) -> None:
        active = self._hovered or self._traced or self.isSelected()
        if (
            self.bundle is not None
            and self.bundle.collapsed
            and not active
            and not self.is_riser_stub
        ):
            if len(self._points_scene) >= 4:
                painter.save()
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                if self._dimmed:
                    painter.setOpacity(0.30)
                pen = QPen(
                    QColor(self.cable.style.color_hex),
                    max(1.5, self.cable.style.width_px * 0.85),
                    qt_pen_style(self.cable.style.pattern),
                )
                painter.setPen(pen)
                painter.drawLine(self._points_scene[0], self._points_scene[1])
                painter.drawLine(self._points_scene[-2], self._points_scene[-1])
                painter.restore()
            return

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._dimmed and not active:
            painter.setOpacity(0.30)

        base_color = QColor(self.cable.style.color_hex)
        if not base_color.isValid():
            base_color = QColor("#38BDF8")

        if active:
            glow_pen = QPen(
                QColor(base_color.red(), base_color.green(), base_color.blue(), 115),
                self.cable.style.width_px + 5.5,
            )
            glow_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            glow_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(glow_pen)
            painter.drawPath(self.path())

        pen = QPen(
            QColor("#F8FAFC") if self.isSelected() else base_color,
            self.cable.style.width_px + (0.8 if active else 0.0),
            qt_pen_style(self.cable.style.pattern),
        )
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawPath(self.path())
        painter.restore()


class PhysicalBundleItem(QGraphicsPathItem):
    """Visual trunk item for a floor-scoped physical CableBundle (z = 35 / 60)."""

    def __init__(
        self,
        bundle: CableBundle,
        *,
        on_add_waypoint: Callable[[str, QPointF], None] | None = None,
        on_hover_changed: Callable[[str, bool], None] | None = None,
    ) -> None:
        super().__init__()
        self.bundle = bundle
        self._on_add_waypoint = on_add_waypoint
        self._on_hover_changed = on_hover_changed
        self._hovered = False
        self._dimmed = False

        self.setZValue(Z_CABLE)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)
        self.setToolTip(
            f"Physical Bundle: {bundle.name} ({len(bundle.member_cable_ids)} cables)\n"
            "Double-click to add trunk waypoint"
        )
        self.rebuild_path()

    def rebuild_path(self) -> None:
        pts_m = [self.bundle.entry_point, *self.bundle.waypoints, self.bundle.exit_point]
        pts_scene = [local_m_to_scene(p) for p in pts_m]
        path = QPainterPath()
        if pts_scene:
            path.moveTo(pts_scene[0])
            for pt in pts_scene[1:]:
                path.lineTo(pt)
        self.setPath(path)

    def set_trace_state(self, *, hovered: bool | None = None, dimmed: bool | None = None) -> None:
        if hovered is not None:
            self._hovered = hovered
        if dimmed is not None:
            self._dimmed = dimmed
        active = self._hovered or self.isSelected()
        self.setZValue(Z_CABLE_ACTIVE if active else Z_CABLE)
        self.update()

    def hoverEnterEvent(self, event) -> None:  # type: ignore[override]
        if self._on_hover_changed is not None:
            self._on_hover_changed(self.bundle.id, True)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:  # type: ignore[override]
        if self._on_hover_changed is not None:
            self._on_hover_changed(self.bundle.id, False)
        super().hoverLeaveEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton and self._on_add_waypoint is not None:
            self._on_add_waypoint(self.bundle.id, event.scenePos())
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def shape(self) -> QPainterPath:
        stroker = QPainterPathStroker()
        stroker.setWidth(max(16.0, self.bundle.style.width_px + 10.0))
        return stroker.createStroke(self.path())

    def boundingRect(self) -> QRectF:
        return super().boundingRect().adjusted(-90.0, -24.0, 90.0, 24.0)

    def paint(
        self,
        painter: QPainter,
        _option: QStyleOptionGraphicsItem,
        _widget: QWidget | None = None,
    ) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        active = self._hovered or self.isSelected()
        if self._dimmed and not active:
            painter.setOpacity(0.30)

        col = QColor(self.bundle.style.color_hex)
        if not col.isValid():
            col = QColor("#F59E0B")

        if self.bundle.collapsed:
            pen = QPen(
                QColor("#F8FAFC") if self.isSelected() else col,
                max(5.0, self.bundle.style.width_px),
                qt_pen_style(self.bundle.style.pattern),
            )
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.drawPath(self.path())
        else:
            guide_pen = QPen(
                QColor(col.red(), col.green(), col.blue(), 90), 1.5, Qt.PenStyle.DashLine
            )
            painter.setPen(guide_pen)
            painter.drawPath(self.path())

        path = self.path()
        if not path.isEmpty():
            mid = path.pointAtPercent(0.5)
            badge = f"{self.bundle.name} ({len(self.bundle.member_cable_ids)})"
            font = QFont("Segoe UI", 8, QFont.Weight.Bold)
            painter.setFont(font)
            fm = QFontMetricsF(font)
            bw = max(60.0, fm.horizontalAdvance(badge) + 14.0)
            brect = QRectF(mid.x() - 0.5 * bw, mid.y() - 10.0, bw, 20.0)
            painter.setPen(QPen(col, 1.2))
            painter.setBrush(QColor(15, 23, 42, 235))
            painter.drawRoundedRect(brect, 5.0, 5.0)
            painter.setPen(QColor("#F8FAFC"))
            painter.drawText(brect, Qt.AlignmentFlag.AlignCenter, badge)

        painter.restore()


# ---------------------------------------------------------------------------
# Physical Canvas View (Strict Non-Panning + Coords + View Rotation + Riser)
# ---------------------------------------------------------------------------


class PhysicalCanvasView(QGraphicsView):
    """
    Physical floor-plan canvas for placing devices, routing cables, and previewing Wi-Fi.

    Strict non-panning invariant: DragMode is NEVER ScrollHandDrag unless Pan Tool (H),
    Middle-Mouse drag, or Space+Left-Drag is active.
    """

    tool_changed = Signal(str)
    view_rotation_changed = Signal(float)
    riser_jump_requested = Signal(str, str)  # (building_id, floor_id)
    connection_trace_changed = Signal(str)
    status_hint = Signal(str)
    live_bom_refresh_requested = Signal()
    presurvey_coverage_updated = Signal(float, int)  # (coverage_percent, ap_count)

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
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._building_id: str | None = None
        self._floor_id: str | None = None

        self._zoom: float = 1.0
        self._view_rotation_deg: float = 0.0
        self._tool: ToolMode = ToolMode.SELECT
        self._presurvey_enabled: bool = False
        self._presurvey_dead_zone_dbm: float = -70.0
        self._last_heatmap_result: HeatmapResult | None = None

        # Panning state (Middle-mouse / Space+Left-Drag / Hand tool)
        self._space_held: bool = False
        self._panning_active: bool = False
        self._pan_last_pos: QPoint = QPoint()

        # Live drag snapshots for single QUndoCommand on release
        self._comp_drag_originals: dict[str, Point2D] = {}
        self._cable_drag_originals: dict[str, Cable] = {}
        self._bundle_drag_originals: dict[str, CableBundle] = {}

        # Scene item registries
        self._comp_items: dict[str, PhysicalComponentItem] = {}
        self._cable_items: dict[str, PhysicalCableItem] = {}
        self._bundle_items: dict[str, PhysicalBundleItem] = {}
        self._riser_badges: list[RiserJumpBadgeItem] = []
        self._waypoint_handles: list[PhysicalWaypointHandle] = []
        self._heatmap_item: QGraphicsPixmapItem | None = None

        self._scene.selectionChanged.connect(self._on_scene_selection_changed)
        self._apply_view_transform()

    @property
    def tool_mode(self) -> ToolMode:
        return self._tool

    @property
    def view_rotation_deg(self) -> float:
        return self._view_rotation_deg

    @property
    def presurvey_enabled(self) -> bool:
        return self._presurvey_enabled

    @property
    def heatmap_item(self) -> QGraphicsPixmapItem | None:
        return self._heatmap_item

    @property
    def last_heatmap_result(self) -> HeatmapResult | None:
        return self._last_heatmap_result

    def current_building(self) -> Building | None:
        if not self._building_id:
            return None
        return self._state.project.get_building(self._building_id)

    def current_floor(self) -> Floor | None:
        if not self._building_id or not self._floor_id:
            return None
        return self._state.project.get_floor(self._building_id, self._floor_id)

    def true_north_angle_deg(self) -> float:
        """Exact Section 4.2C True North CW screen angle."""
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
        self.rebuild_scene()
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
        """Update Floor.editor_view_rotation_deg ONLY (never mutates GeoAnchor)."""
        normalized = ((float(angle_deg) + 180.0) % 360.0) - 180.0
        self._view_rotation_deg = normalized
        self._apply_view_transform()
        self.view_rotation_changed.emit(self._view_rotation_deg)
        floor = self.current_floor()
        if commit_to_state and floor is not None and self._building_id is not None:
            if abs(floor.editor_view_rotation_deg - normalized) > 1e-6:
                self._state.set_floor_view_rotation(self._building_id, floor.id, normalized)
        self.viewport().update()

    def set_presurvey_enabled(self, enabled: bool) -> None:
        self._presurvey_enabled = bool(enabled)
        self.rebuild_scene()

    def set_presurvey_dead_zone_dbm(self, dbm: float) -> None:
        self._presurvey_dead_zone_dbm = float(dbm)
        if self._presurvey_enabled:
            self.rebuild_scene()

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

    def select_cable(self, cable_id: str) -> None:
        self._scene.clearSelection()
        c_item = self._cable_items.get(cable_id)
        if c_item is not None:
            c_item.setSelected(True)
            self._rebuild_handles()

    def select_component(self, component_id: str) -> None:
        self._scene.clearSelection()
        item = self._comp_items.get(component_id)
        if item is not None:
            item.setSelected(True)
            item.set_pulsed(True)
            self.centerOn(item.pos())

    # ------------------------------------------------------------------
    # Scene Building
    # ------------------------------------------------------------------

    def rebuild_scene(self) -> None:
        selected_comp_ids = {
            cid for cid, item in self._comp_items.items() if item.isSelected()
        }
        selected_cable_ids = {
            cid for cid, item in self._cable_items.items() if item.isSelected()
        }
        selected_bundle_ids = {
            bid for bid, item in self._bundle_items.items() if item.isSelected()
        }

        self._scene.blockSignals(True)
        try:
            self._scene.clear()
            self._comp_items.clear()
            self._cable_items.clear()
            self._bundle_items.clear()
            self._riser_badges.clear()
            self._waypoint_handles.clear()
            self._heatmap_item = None
            self._last_heatmap_result = None

            bldg = self.current_building()
            floor = self.current_floor()
            if bldg is None or floor is None:
                return

            # 1. Blueprint Underlay
            if floor.blueprint is not None and floor.blueprint.visible:
                self._render_blueprint(floor.blueprint)

            # 2. Floor Outer Polygon
            if len(floor.outer_polygon_m) >= 3:
                outer_item = QGraphicsPolygonItem(_poly_to_qpolygon(floor.outer_polygon_m))
                outer_item.setZValue(Z_FLOOR_OUTER)
                outer_item.setBrush(QBrush(QColor(15, 23, 42, 225)))
                outer_item.setPen(QPen(QColor("#94A3B8"), 2.5))
                self._scene.addItem(outer_item)

            # 3. Rooms + Wall Outlines
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

            # 4. Pre-Survey Wall-Attenuated Wi-Fi Coverage Overlay (optional toggle)
            if self._presurvey_enabled:
                self._render_presurvey_heatmap(floor)

            # 5. Stairs
            for stairs in floor.stairs:
                if len(stairs.polygon_m) < 3:
                    continue
                s_item = QGraphicsPolygonItem(_poly_to_qpolygon(stairs.polygon_m))
                s_item.setZValue(Z_STAIRS)
                s_item.setBrush(QBrush(QColor(168, 85, 247, 60)))
                s_item.setPen(QPen(QColor("#A855F7"), 1.8))
                self._scene.addItem(s_item)
                self._render_stairs_hatch(stairs)

            # 6. Vertical Riser Holes
            for hole in floor.holes:
                r_px = max(8.0, hole.radius_m * PIXELS_PER_METER)
                h_item = QGraphicsEllipseItem(-r_px, -r_px, 2 * r_px, 2 * r_px)
                h_item.setPos(local_m_to_scene(hole.position_m))
                h_item.setZValue(Z_HOLE)
                h_item.setBrush(QBrush(QColor(250, 204, 21, 95)))
                h_item.setPen(QPen(QColor("#FACC15"), 1.8))
                self._scene.addItem(h_item)

                tag = QGraphicsSimpleTextItem(f"{hole.label} [{hole.riser_group_id}]")
                tag.setBrush(QBrush(QColor("#FDE68A")))
                tag.setFont(QFont("Segoe UI", 8))
                pt_s = local_m_to_scene(hole.position_m)
                tag.setPos(pt_s.x() + r_px + 4.0, pt_s.y() - 7.0)
                tag.setZValue(Z_HOLE + 1.0)
                self._scene.addItem(tag)

            # 7. Doors
            for door in floor.doors:
                d_item = QGraphicsPathItem(_door_graphics_path(door))
                d_item.setPos(local_m_to_scene(door.position_m))
                d_item.setRotation(-door.angle_deg)
                d_item.setZValue(Z_DOOR)
                d_item.setPen(QPen(QColor("#22C55E"), 1.8))
                self._scene.addItem(d_item)

            # 8. Physical Bundles on this floor
            floor_bundles = {
                b.id: b
                for b in self._state.project.bundles
                if b.scope == "physical" and b.floor_id == floor.id
            }
            for bundle in floor_bundles.values():
                b_item = PhysicalBundleItem(
                    bundle,
                    on_add_waypoint=self._on_add_bundle_waypoint,
                    on_hover_changed=self._on_bundle_hover_changed,
                )
                if bundle.id in selected_bundle_ids:
                    b_item.setSelected(True)
                self._scene.addItem(b_item)
                self._bundle_items[bundle.id] = b_item

            # 9. Placed Components on this floor
            for comp in self._state.project.components:
                if (
                    comp.physical_building_id == bldg.id
                    and comp.physical_floor_id == floor.id
                    and comp.physical_pos_m is not None
                ):
                    c_item = PhysicalComponentItem(
                        comp,
                        on_moved_live=self._on_component_moved_live,
                        on_move_released=self._on_component_move_released,
                        on_context_action=self._on_component_context_action,
                    )
                    if comp.id in selected_comp_ids:
                        c_item.setSelected(True)
                    self._scene.addItem(c_item)
                    self._comp_items[comp.id] = c_item

            # 10. Automatic Physical Cables (Same-Floor, Inter-Floor Riser, Cross-Building)
            self._build_physical_cables_and_badges(
                bldg, floor, floor_bundles, selected_cable_ids
            )
        finally:
            self._scene.blockSignals(False)

        self._rebuild_handles()

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

    def _render_stairs_hatch(self, stairs: Stairs) -> None:
        xs = [p.x for p in stairs.polygon_m]
        ys = [p.y for p in stairs.polygon_m]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        w = max_x - min_x
        h = max_y - min_y
        if w >= h:
            steps = max(3, int(w / 0.35))
            for i in range(1, steps):
                x = min_x + (w * i) / steps
                ln = QGraphicsLineItem(
                    x * PIXELS_PER_METER,
                    -min_y * PIXELS_PER_METER,
                    x * PIXELS_PER_METER,
                    -max_y * PIXELS_PER_METER,
                )
                ln.setPen(QPen(QColor(168, 85, 247, 135), 1.0))
                ln.setZValue(Z_STAIRS + 0.5)
                self._scene.addItem(ln)
        else:
            steps = max(3, int(h / 0.35))
            for i in range(1, steps):
                y = min_y + (h * i) / steps
                ln = QGraphicsLineItem(
                    min_x * PIXELS_PER_METER,
                    -y * PIXELS_PER_METER,
                    max_x * PIXELS_PER_METER,
                    -y * PIXELS_PER_METER,
                )
                ln.setPen(QPen(QColor(168, 85, 247, 135), 1.0))
                ln.setZValue(Z_STAIRS + 0.5)
                self._scene.addItem(ln)

    def _render_presurvey_heatmap(self, floor: Floor) -> None:
        ap_count = sum(
            1
            for c in self._state.project.components
            if c.component_type.value == "access_point"
            and c.physical_floor_id == floor.id
            and c.physical_pos_m is not None
        )
        res = estimate_presurvey_heatmap(
            self._state.project,
            floor,
            resolution_m=0.4,
            dead_zone_dbm=self._presurvey_dead_zone_dbm,
        )
        self._last_heatmap_result = res
        self.presurvey_coverage_updated.emit(res.coverage_percent, ap_count)
        if ap_count == 0 or res.width <= 0 or res.height <= 0:
            return

        qimg = QImage(
            res.rgba_bytes,
            res.width,
            res.height,
            res.width * 4,
            QImage.Format.Format_RGBA8888,
        ).copy()
        pix = QPixmap.fromImage(qimg)
        rect = bounds_m_to_scene_rect(*res.bounds)
        item = QGraphicsPixmapItem(pix)
        item.setZValue(Z_HEATMAP)
        item.setPos(rect.topLeft())
        item.setTransform(
            QTransform.fromScale(
                rect.width() / max(1, res.width),
                rect.height() / max(1, res.height),
            )
        )
        self._scene.addItem(item)
        self._heatmap_item = item

    def _build_physical_cables_and_badges(
        self,
        bldg: Building,
        floor: Floor,
        floor_bundles: dict[str, CableBundle],
        selected_cable_ids: set[str],
    ) -> None:
        hole_badge_offsets: dict[str, int] = {}

        for cable in self._state.project.cables:
            src = self._state.project.get_component(cable.source_component_id)
            dst = self._state.project.get_component(cable.target_component_id)
            if src is None or dst is None:
                continue
            if (
                src.physical_building_id is None
                or src.physical_floor_id is None
                or src.physical_pos_m is None
                or dst.physical_building_id is None
                or dst.physical_floor_id is None
                or dst.physical_pos_m is None
            ):
                continue

            src_port = self._state.project.get_port(src.id, cable.source_port_id)
            dst_port = self._state.project.get_port(dst.id, cable.target_port_id)
            src_lbl = src_port.label if src_port else "P?"
            dst_lbl = dst_port.label if dst_port else "P?"
            src_col = port_color(src_port.port_type) if src_port else QColor("#38BDF8")
            dst_col = port_color(dst_port.port_type) if dst_port else QColor("#38BDF8")
            vlan_tag = f" · VLAN {cable.vlan_id}" if cable.vlan_id else ""
            lbl_tag = cable.label or "Link"

            same_bldg = src.physical_building_id == dst.physical_building_id
            same_floor = same_bldg and src.physical_floor_id == dst.physical_floor_id

            # Case A: Both endpoints on active floor
            if same_floor and src.physical_floor_id == floor.id:
                seg = next(
                    (s for s in cable.physical_segments if s.floor_id == floor.id),
                    None,
                )
                wps = (
                    _extract_intermediate_waypoints(seg, src.physical_pos_m, dst.physical_pos_m)
                    if seg is not None
                    else []
                )
                bundle = (
                    floor_bundles.get(cable.physical_bundle_id)
                    if cable.physical_bundle_id
                    else None
                )
                m_idx = 0
                m_cnt = 1
                if bundle is not None and cable.id in bundle.member_cable_ids:
                    m_idx = bundle.member_cable_ids.index(cable.id)
                    m_cnt = len(bundle.member_cable_ids)

                dist_m = self._polyline_len_m([src.physical_pos_m, *wps, dst.physical_pos_m])
                trace_text = (
                    f"{src.name} [{src_lbl}] ──({lbl_tag}{vlan_tag} · {dist_m:.1f} m)"
                    f"──▶ {dst.name} [{dst_lbl}]"
                )
                c_item = PhysicalCableItem(
                    cable,
                    floor_id=floor.id,
                    start_m=src.physical_pos_m,
                    end_m=dst.physical_pos_m,
                    waypoints_m=wps,
                    src_port_label=src_lbl,
                    src_port_color=src_col,
                    dst_port_label=dst_lbl,
                    dst_port_color=dst_col,
                    trace_text=trace_text,
                    bundle=bundle,
                    bundle_member_index=m_idx,
                    bundle_member_count=m_cnt,
                    is_riser_stub=False,
                    on_add_waypoint=self._on_add_cable_waypoint,
                    on_hover_changed=self._on_cable_hover_changed,
                )
                if cable.id in selected_cable_ids:
                    c_item.setSelected(True)
                self._scene.addItem(c_item)
                self._scene.addItem(c_item.src_pill)
                if c_item.dst_pill is not None:
                    self._scene.addItem(c_item.dst_pill)
                self._cable_items[cable.id] = c_item
                continue

            # Case B: Inter-floor in same building where one endpoint is on active floor
            if same_bldg and (
                src.physical_floor_id == floor.id or dst.physical_floor_id == floor.id
            ):
                src_is_local = src.physical_floor_id == floor.id
                local_comp = src if src_is_local else dst
                remote_comp = dst if src_is_local else src
                local_lbl = src_lbl if src_is_local else dst_lbl
                local_col = src_col if src_is_local else dst_col
                remote_floor = self._state.project.get_floor(
                    bldg.id, remote_comp.physical_floor_id or ""
                )
                if remote_floor is None:
                    continue

                seg = next(
                    (s for s in cable.physical_segments if s.floor_id == floor.id),
                    None,
                )
                local_hole = None
                if seg is not None:
                    hole_id = seg.end_hole_id if src_is_local else seg.start_hole_id
                    local_hole = _find_hole_by_id(floor, hole_id)
                if local_hole is None:
                    pair = find_best_riser_holes(
                        floor,
                        remote_floor,
                        local_comp.physical_pos_m or Point2D(0.0, 0.0),
                        remote_comp.physical_pos_m or Point2D(0.0, 0.0),
                    )
                    if pair is not None:
                        local_hole = pair[0]

                if local_hole is not None:
                    end_m = local_hole.position_m
                    wps = (
                        _extract_intermediate_waypoints(
                            seg, local_comp.physical_pos_m, end_m
                        )
                        if seg is not None
                        else []
                    )
                    arrow = "↑" if remote_floor.level_index > floor.level_index else "↓"
                    riser_name = local_hole.riser_group_id or local_hole.label
                    badge_text = f"{arrow} {remote_floor.name} via {riser_name}"
                    trace_text = (
                        f"{src.name} [{src_lbl}] ──({lbl_tag}{vlan_tag} · "
                        f"{badge_text})──▶ {dst.name} [{dst_lbl}]"
                    )
                    c_item = PhysicalCableItem(
                        cable,
                        floor_id=floor.id,
                        start_m=local_comp.physical_pos_m or Point2D(0.0, 0.0),
                        end_m=end_m,
                        waypoints_m=wps,
                        src_port_label=local_lbl,
                        src_port_color=local_col,
                        dst_port_label=None,
                        dst_port_color=None,
                        trace_text=trace_text,
                        is_riser_stub=True,
                        on_add_waypoint=self._on_add_cable_waypoint,
                        on_hover_changed=self._on_cable_hover_changed,
                    )
                    if cable.id in selected_cable_ids:
                        c_item.setSelected(True)
                    self._scene.addItem(c_item)
                    self._scene.addItem(c_item.src_pill)
                    self._cable_items[cable.id] = c_item

                    stack_idx = hole_badge_offsets.get(local_hole.id, 0)
                    hole_badge_offsets[local_hole.id] = stack_idx + 1
                    badge = RiserJumpBadgeItem(
                        badge_text,
                        local_m_to_scene(end_m),
                        bldg.id,
                        remote_floor.id,
                        cable.id,
                        y_offset_px=stack_idx * 24.0,
                        on_jump_requested=self._emit_riser_jump,
                    )
                    self._scene.addItem(badge)
                    self._riser_badges.append(badge)
                else:
                    # No matching riser pair on these two floors
                    stub_end = local_comp.physical_pos_m or Point2D(0.0, 0.0)
                    stub_pt = Point2D(stub_end.x + 1.2, stub_end.y + 0.8)
                    badge_text = f"⚠ No Riser to {remote_floor.name}"
                    c_item = PhysicalCableItem(
                        cable,
                        floor_id=floor.id,
                        start_m=stub_end,
                        end_m=stub_pt,
                        waypoints_m=[],
                        src_port_label=local_lbl,
                        src_port_color=local_col,
                        dst_port_label=None,
                        dst_port_color=None,
                        trace_text=f"{src.name} [{src_lbl}] ──(Missing riser)──▶ {dst.name}",
                        is_riser_stub=True,
                        on_hover_changed=self._on_cable_hover_changed,
                    )
                    self._scene.addItem(c_item)
                    self._scene.addItem(c_item.src_pill)
                    self._cable_items[cable.id] = c_item

                    badge = RiserJumpBadgeItem(
                        badge_text,
                        local_m_to_scene(stub_pt),
                        bldg.id,
                        remote_floor.id,
                        cable.id,
                        on_jump_requested=self._emit_riser_jump,
                    )
                    self._scene.addItem(badge)
                    self._riser_badges.append(badge)
                continue

            # Case C: Cross-building cable touching active floor
            if not same_bldg and (
                src.physical_floor_id == floor.id or dst.physical_floor_id == floor.id
            ):
                src_is_local = src.physical_floor_id == floor.id
                local_comp = src if src_is_local else dst
                remote_comp = dst if src_is_local else src
                local_lbl = src_lbl if src_is_local else dst_lbl
                local_col = src_col if src_is_local else dst_col
                remote_bldg = self._state.project.get_building(
                    remote_comp.physical_building_id or ""
                )
                remote_floor = (
                    self._state.project.get_floor(
                        remote_bldg.id, remote_comp.physical_floor_id or ""
                    )
                    if remote_bldg is not None
                    else None
                )
                if remote_bldg is None or remote_floor is None:
                    continue
                local_pos = local_comp.physical_pos_m or Point2D(0.0, 0.0)
                stub_pt = Point2D(local_pos.x + 1.4, local_pos.y - 0.8)
                badge_text = f"↗ {remote_bldg.name} · {remote_floor.name}"
                c_item = PhysicalCableItem(
                    cable,
                    floor_id=floor.id,
                    start_m=local_pos,
                    end_m=stub_pt,
                    waypoints_m=[],
                    src_port_label=local_lbl,
                    src_port_color=local_col,
                    dst_port_label=None,
                    dst_port_color=None,
                    trace_text=(
                        f"{src.name} [{src_lbl}] ──({lbl_tag}{vlan_tag} · "
                        f"Cross-Building)──▶ {dst.name} [{dst_lbl}]"
                    ),
                    is_riser_stub=True,
                    on_hover_changed=self._on_cable_hover_changed,
                )
                self._scene.addItem(c_item)
                self._scene.addItem(c_item.src_pill)
                self._cable_items[cable.id] = c_item

                badge = RiserJumpBadgeItem(
                    badge_text,
                    local_m_to_scene(stub_pt),
                    remote_bldg.id,
                    remote_floor.id,
                    cable.id,
                    on_jump_requested=self._emit_riser_jump,
                )
                self._scene.addItem(badge)
                self._riser_badges.append(badge)

    @staticmethod
    def _polyline_len_m(pts: list[Point2D]) -> float:
        if len(pts) < 2:
            return 0.0
        return sum(
            math.hypot(pts[i + 1].x - pts[i].x, pts[i + 1].y - pts[i].y)
            for i in range(len(pts) - 1)
        )

    def _clear_handles(self) -> None:
        for h in self._waypoint_handles:
            if h.scene() is self._scene:
                self._scene.removeItem(h)
        self._waypoint_handles.clear()

    def _rebuild_handles(self) -> None:
        self._clear_handles()
        floor = self.current_floor()
        if floor is None:
            return

        for cab_id, c_item in self._cable_items.items():
            if not c_item.isSelected() or c_item.bundle is not None:
                continue
            for idx, wp_m in enumerate(c_item.waypoints_m):
                h = PhysicalWaypointHandle(
                    wp_m,
                    kind="cable_wp",
                    owner_id=cab_id,
                    floor_id=floor.id,
                    index=idx,
                    color_hex=c_item.cable.style.color_hex,
                    on_moved_live=self._on_handle_moved_live,
                    on_released=self._on_handle_released,
                    on_delete=self._on_handle_deleted,
                )
                self._scene.addItem(h)
                self._waypoint_handles.append(h)

        for b_id, b_item in self._bundle_items.items():
            if not b_item.isSelected():
                continue
            b = b_item.bundle
            he = PhysicalWaypointHandle(
                b.entry_point,
                kind="bundle_entry",
                owner_id=b_id,
                floor_id=floor.id,
                index=-1,
                color_hex="#F59E0B",
                on_moved_live=self._on_handle_moved_live,
                on_released=self._on_handle_released,
            )
            hx = PhysicalWaypointHandle(
                b.exit_point,
                kind="bundle_exit",
                owner_id=b_id,
                floor_id=floor.id,
                index=-1,
                color_hex="#F59E0B",
                on_moved_live=self._on_handle_moved_live,
                on_released=self._on_handle_released,
            )
            self._scene.addItem(he)
            self._scene.addItem(hx)
            self._waypoint_handles.extend([he, hx])
            for idx, wp_m in enumerate(b.waypoints):
                hw = PhysicalWaypointHandle(
                    wp_m,
                    kind="bundle_wp",
                    owner_id=b_id,
                    floor_id=floor.id,
                    index=idx,
                    color_hex=b.style.color_hex,
                    on_moved_live=self._on_handle_moved_live,
                    on_released=self._on_handle_released,
                    on_delete=self._on_handle_deleted,
                )
                self._scene.addItem(hw)
                self._waypoint_handles.append(hw)

    # ------------------------------------------------------------------
    # Live Dragging & Undoable Commits
    # ------------------------------------------------------------------

    def _on_component_moved_live(self, comp_id: str, new_pos_m: Point2D) -> None:
        comp = self._state.project.get_component(comp_id)
        if comp is None:
            return
        if comp_id not in self._comp_drag_originals and comp.physical_pos_m is not None:
            self._comp_drag_originals[comp_id] = copy.deepcopy(comp.physical_pos_m)
        comp.physical_pos_m = Point2D(new_pos_m.x, new_pos_m.y)

        # Update connected physical cables live
        for c_item in self._cable_items.values():
            if c_item.cable.source_component_id == comp_id:
                if c_item.is_riser_stub:
                    src_c = self._state.project.get_component(c_item.cable.source_component_id)
                    if src_c and src_c.physical_floor_id == c_item.floor_id:
                        c_item.start_m = Point2D(new_pos_m.x, new_pos_m.y)
                else:
                    c_item.start_m = Point2D(new_pos_m.x, new_pos_m.y)
                c_item.rebuild_path()
            elif c_item.cable.target_component_id == comp_id:
                if c_item.is_riser_stub:
                    dst_c = self._state.project.get_component(c_item.cable.target_component_id)
                    if dst_c and dst_c.physical_floor_id == c_item.floor_id:
                        c_item.start_m = Point2D(new_pos_m.x, new_pos_m.y)
                else:
                    c_item.end_m = Point2D(new_pos_m.x, new_pos_m.y)
                c_item.rebuild_path()

        self.live_bom_refresh_requested.emit()

    def _on_component_move_released(self, comp_id: str, final_pos_m: Point2D) -> None:
        orig_pos = self._comp_drag_originals.pop(comp_id, None)
        comp = self._state.project.get_component(comp_id)
        if comp is None or self._building_id is None or self._floor_id is None:
            return
        if orig_pos is None:
            return
        if _points_close(orig_pos, final_pos_m, tol=1e-4):
            comp.physical_pos_m = orig_pos
            return
        comp.physical_pos_m = orig_pos
        self._state.place_component_physical(
            comp_id, self._building_id, self._floor_id, final_pos_m
        )

    def _on_component_context_action(self, action: str, comp_id: str) -> None:
        if action == "unplace":
            self._state.place_component_physical(comp_id, None, None, None)
        elif action == "select":
            self._state.set_selection("component", comp_id)

    def _ensure_cable_segments_for_floor(
        self,
        cable: Cable,
        floor: Floor,
    ) -> PhysicalCableSegment | None:
        """Ensure cable.physical_segments contains editable segment(s) covering floor."""
        existing = next((s for s in cable.physical_segments if s.floor_id == floor.id), None)
        if existing is not None:
            return existing

        src = self._state.project.get_component(cable.source_component_id)
        dst = self._state.project.get_component(cable.target_component_id)
        if src is None or dst is None or src.physical_pos_m is None or dst.physical_pos_m is None:
            return None

        if src.physical_floor_id == dst.physical_floor_id == floor.id:
            seg = PhysicalCableSegment(floor_id=floor.id, waypoints_m=[])
            cable.physical_segments.append(seg)
            return seg

        if (
            src.physical_building_id is not None
            and src.physical_building_id == dst.physical_building_id
            and (src.physical_floor_id == floor.id or dst.physical_floor_id == floor.id)
        ):
            src_floor = self._state.project.get_floor(
                src.physical_building_id, src.physical_floor_id or ""
            )
            dst_floor = self._state.project.get_floor(
                dst.physical_building_id, dst.physical_floor_id or ""
            )
            if src_floor is None or dst_floor is None:
                return None
            pair = find_best_riser_holes(
                src_floor, dst_floor, src.physical_pos_m, dst.physical_pos_m
            )
            if pair is None:
                return None
            hole_src, hole_dst = pair
            seg_src = PhysicalCableSegment(
                floor_id=src_floor.id,
                waypoints_m=[],
                start_hole_id=None,
                end_hole_id=hole_src.id,
            )
            seg_dst = PhysicalCableSegment(
                floor_id=dst_floor.id,
                waypoints_m=[],
                start_hole_id=hole_dst.id,
                end_hole_id=None,
            )
            cable.physical_segments = [seg_src, seg_dst]
            return seg_src if floor.id == src_floor.id else seg_dst

        return None

    def _on_add_cable_waypoint(self, cable_id: str, scene_pos: QPointF) -> None:
        floor = self.current_floor()
        cable = next((c for c in self._state.project.cables if c.id == cable_id), None)
        c_item = self._cable_items.get(cable_id)
        if floor is None or cable is None or c_item is None:
            return

        pt_m = scene_to_local_m(scene_pos)
        updated = copy.deepcopy(cable)
        seg = self._ensure_cable_segments_for_floor(updated, floor)
        if seg is None:
            return

        cur_wps = _extract_intermediate_waypoints(seg, c_item.start_m, c_item.end_m)
        full_poly = [c_item.start_m, *cur_wps, c_item.end_m]
        insert_idx = _best_insert_index(pt_m, full_poly)
        cur_wps.insert(insert_idx, pt_m)
        seg.waypoints_m = cur_wps
        self._state.update_cable(cable.id, updated, text="Add Physical Cable Waypoint")
        self.select_cable(cable.id)

    def _on_add_bundle_waypoint(self, bundle_id: str, scene_pos: QPointF) -> None:
        bundle = next((b for b in self._state.project.bundles if b.id == bundle_id), None)
        if bundle is None:
            return
        pt_m = scene_to_local_m(scene_pos)
        updated = copy.deepcopy(bundle)
        full_poly = [updated.entry_point, *updated.waypoints, updated.exit_point]
        insert_idx = _best_insert_index(pt_m, full_poly)
        updated.waypoints.insert(insert_idx, pt_m)
        self._state.update_bundle(bundle.id, updated, text="Add Physical Bundle Waypoint")

    def _on_handle_moved_live(
        self,
        kind: str,
        owner_id: str,
        floor_id: str,
        index: int,
        new_pt_m: Point2D,
    ) -> None:
        if kind == "cable_wp":
            cable = next((c for c in self._state.project.cables if c.id == owner_id), None)
            c_item = self._cable_items.get(owner_id)
            floor = self.current_floor()
            if cable is None or c_item is None or floor is None:
                return
            if owner_id not in self._cable_drag_originals:
                self._cable_drag_originals[owner_id] = copy.deepcopy(cable)
            seg = self._ensure_cable_segments_for_floor(cable, floor)
            if seg is None:
                return
            wps = _extract_intermediate_waypoints(seg, c_item.start_m, c_item.end_m)
            if 0 <= index < len(wps):
                wps[index] = Point2D(new_pt_m.x, new_pt_m.y)
                seg.waypoints_m = wps
                c_item.waypoints_m = list(wps)
                c_item.rebuild_path()
                self.live_bom_refresh_requested.emit()
        else:
            bundle = next((b for b in self._state.project.bundles if b.id == owner_id), None)
            b_item = self._bundle_items.get(owner_id)
            if bundle is None or b_item is None:
                return
            if owner_id not in self._bundle_drag_originals:
                self._bundle_drag_originals[owner_id] = copy.deepcopy(bundle)
            if kind == "bundle_entry":
                bundle.entry_point = Point2D(new_pt_m.x, new_pt_m.y)
            elif kind == "bundle_exit":
                bundle.exit_point = Point2D(new_pt_m.x, new_pt_m.y)
            elif kind == "bundle_wp" and 0 <= index < len(bundle.waypoints):
                bundle.waypoints[index] = Point2D(new_pt_m.x, new_pt_m.y)
            b_item.rebuild_path()
            for c_item in self._cable_items.values():
                if c_item.bundle is not None and c_item.bundle.id == owner_id:
                    c_item.rebuild_path()

    def _on_handle_released(
        self,
        kind: str,
        owner_id: str,
        _floor_id: str,
        _index: int,
        _final_pt_m: Point2D,
    ) -> None:
        if kind == "cable_wp":
            orig = self._cable_drag_originals.pop(owner_id, None)
            cable = next((c for c in self._state.project.cables if c.id == owner_id), None)
            if orig is None or cable is None:
                return
            new_cable = copy.deepcopy(cable)
            cable.physical_segments = copy.deepcopy(orig.physical_segments)
            if new_cable.to_dict() != orig.to_dict():
                self._state.update_cable(
                    owner_id, new_cable, text="Move Physical Cable Waypoint"
                )
        else:
            orig_b = self._bundle_drag_originals.pop(owner_id, None)
            bundle = next((b for b in self._state.project.bundles if b.id == owner_id), None)
            if orig_b is None or bundle is None:
                return
            new_bundle = copy.deepcopy(bundle)
            bundle.entry_point = copy.deepcopy(orig_b.entry_point)
            bundle.exit_point = copy.deepcopy(orig_b.exit_point)
            bundle.waypoints = copy.deepcopy(orig_b.waypoints)
            if new_bundle.to_dict() != orig_b.to_dict():
                self._state.update_bundle(
                    owner_id, new_bundle, text="Move Physical Bundle Handle"
                )

    def _on_handle_deleted(
        self,
        kind: str,
        owner_id: str,
        floor_id: str,
        index: int,
    ) -> None:
        if kind == "cable_wp":
            cable = next((c for c in self._state.project.cables if c.id == owner_id), None)
            c_item = self._cable_items.get(owner_id)
            if cable is None or c_item is None:
                return
            updated = copy.deepcopy(cable)
            seg = next((s for s in updated.physical_segments if s.floor_id == floor_id), None)
            if seg is None:
                return
            wps = _extract_intermediate_waypoints(seg, c_item.start_m, c_item.end_m)
            if 0 <= index < len(wps):
                wps.pop(index)
                seg.waypoints_m = wps
                self._state.update_cable(
                    cable.id, updated, text="Delete Physical Cable Waypoint"
                )
        elif kind == "bundle_wp":
            bundle = next((b for b in self._state.project.bundles if b.id == owner_id), None)
            if bundle is None or not (0 <= index < len(bundle.waypoints)):
                return
            updated = copy.deepcopy(bundle)
            updated.waypoints.pop(index)
            self._state.update_bundle(
                bundle.id, updated, text="Delete Physical Bundle Waypoint"
            )

    def _on_cable_hover_changed(self, cable_id: str, hovered: bool) -> None:
        c_item = self._cable_items.get(cable_id)
        if hovered and c_item is not None:
            for cid, item in self._cable_items.items():
                item.set_trace_state(hovered=(cid == cable_id), dimmed=(cid != cable_id))
            for bid, b_item in self._bundle_items.items():
                is_owner = c_item.bundle is not None and c_item.bundle.id == bid
                b_item.set_trace_state(hovered=is_owner, dimmed=not is_owner)
            self.connection_trace_changed.emit(c_item.trace_text)
        else:
            for item in self._cable_items.values():
                item.set_trace_state(hovered=False, dimmed=False)
            for b_item in self._bundle_items.values():
                b_item.set_trace_state(hovered=False, dimmed=False)
            self.connection_trace_changed.emit(
                "Hover any physical cable or bundle to trace endpoints & riser path"
            )

    def _on_bundle_hover_changed(self, bundle_id: str, hovered: bool) -> None:
        b_item = self._bundle_items.get(bundle_id)
        if hovered and b_item is not None:
            members = set(b_item.bundle.member_cable_ids)
            for cid, c_item in self._cable_items.items():
                c_item.set_trace_state(traced=(cid in members), dimmed=(cid not in members))
            for bid, item in self._bundle_items.items():
                item.set_trace_state(hovered=(bid == bundle_id), dimmed=(bid != bundle_id))
            self.connection_trace_changed.emit(
                f"Physical Bundle '{b_item.bundle.name}' ({len(members)} cables)"
            )
        else:
            for c_item in self._cable_items.values():
                c_item.set_trace_state(traced=False, dimmed=False)
            for item in self._bundle_items.values():
                item.set_trace_state(hovered=False, dimmed=False)
            self.connection_trace_changed.emit(
                "Hover any physical cable or bundle to trace endpoints & riser path"
            )

    def _on_scene_selection_changed(self) -> None:
        self._rebuild_handles()
        selected_cables = [cid for cid, item in self._cable_items.items() if item.isSelected()]
        selected_comps = [cid for cid, item in self._comp_items.items() if item.isSelected()]
        if selected_cables:
            self._state.set_selection("cable", selected_cables[0])
        elif selected_comps:
            self._state.set_selection("component", selected_comps[0])

    def _emit_riser_jump(self, building_id: str, floor_id: str) -> None:
        self.riser_jump_requested.emit(building_id, floor_id)

    # ------------------------------------------------------------------
    # Physical Cable Bundling Actions
    # ------------------------------------------------------------------

    def bundle_selected_cables(self) -> CableBundle | None:
        """Create a scope='physical' CableBundle from 2+ selected same-floor cables."""
        floor = self.current_floor()
        if floor is None:
            return None
        selected = [
            item
            for item in self._cable_items.values()
            if item.isSelected() and not item.is_riser_stub and item.bundle is None
        ]
        if len(selected) < 2:
            self.status_hint.emit(
                "Select at least 2 unbundled same-floor physical cables to bundle."
            )
            return None

        mid_xs = [0.5 * (it.start_m.x + it.end_m.x) for it in selected]
        mid_ys = [0.5 * (it.start_m.y + it.end_m.y) for it in selected]
        cx = sum(mid_xs) / len(mid_xs)
        cy = sum(mid_ys) / len(mid_ys)
        entry = Point2D(cx - 1.2, cy)
        exit_pt = Point2D(cx + 1.2, cy)
        existing_names = {b.name for b in self._state.project.bundles}
        idx = 1
        while f"Physical Bundle {idx}" in existing_names:
            idx += 1

        bundle = CableBundle(
            name=f"Physical Bundle {idx}",
            scope="physical",
            floor_id=floor.id,
            entry_point=entry,
            exit_point=exit_pt,
            member_cable_ids=[it.cable.id for it in selected],
            collapsed=True,
        )
        return self._state.create_bundle(bundle)

    def unbundle_selected(self) -> None:
        """Dissolve selected physical bundle(s) or toggle/remove selected cables from bundle."""
        selected_bundles = [bid for bid, item in self._bundle_items.items() if item.isSelected()]
        if selected_bundles:
            for bid in selected_bundles:
                self._state.delete_bundle(bid)
            return
        selected_cables = [
            item.cable
            for item in self._cable_items.values()
            if item.isSelected() and item.cable.physical_bundle_id
        ]
        if not selected_cables:
            self.status_hint.emit("Select a physical bundle or bundled cable to unbundle.")
            return
        for cab in selected_cables:
            bid = cab.physical_bundle_id
            if not bid:
                continue
            bundle = next((b for b in self._state.project.bundles if b.id == bid), None)
            if bundle is None:
                continue
            remaining = [cid for cid in bundle.member_cable_ids if cid != cab.id]
            if len(remaining) < 2:
                self._state.delete_bundle(bid)
            else:
                updated = copy.deepcopy(bundle)
                updated.member_cable_ids = remaining
                self._state.update_bundle(bid, updated, text="Remove Cable from Physical Bundle")

    def toggle_selected_bundle_collapse(self) -> None:
        for b_item in self._bundle_items.values():
            if b_item.isSelected():
                updated = copy.deepcopy(b_item.bundle)
                updated.collapsed = not updated.collapsed
                self._state.update_bundle(
                    updated.id, updated, text="Toggle Physical Bundle Collapse"
                )
                return

    # ------------------------------------------------------------------
    # Drag-and-Drop Placement from Left Sidebar
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasFormat(COMPONENT_MIME_TYPE):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if event.mimeData().hasFormat(COMPONENT_MIME_TYPE):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        if event.mimeData().hasFormat(COMPONENT_MIME_TYPE):
            comp_id = bytes(event.mimeData().data(COMPONENT_MIME_TYPE)).decode("utf-8").strip()
            if comp_id and self._building_id and self._floor_id:
                scene_pos = self.mapToScene(event.position().toPoint())
                pos_m = scene_to_local_m(scene_pos)
                self._state.place_component_physical(
                    comp_id, self._building_id, self._floor_id, pos_m
                )
                event.acceptProposedAction()
                return
        super().dropEvent(event)

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

        # 1. Top-Right True North Compass (exact Section 4.2C & 4.7)
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

        if self._tool == ToolMode.PAN:
            super().mousePressEvent(event)
            return

        # ToolMode.SELECT — DragMode is NoDrag; left-click on empty canvas NEVER pans
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

        if self._tool == ToolMode.PAN:
            super().mouseMoveEvent(event)
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

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._tool != ToolMode.PAN:
            hit = self.itemAt(event.position().toPoint())
            if isinstance(hit, RiserJumpBadgeItem):
                self._emit_riser_jump(hit.target_building_id, hit.target_floor_id)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

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
            self._scene.clearSelection()
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


# ---------------------------------------------------------------------------
# Draggable Component List Widget
# ---------------------------------------------------------------------------


class _DraggableComponentList(QListWidget):
    """QListWidget supporting dragging unplaced components directly onto PhysicalCanvasView."""

    def startDrag(self, supported_actions: Qt.DropAction) -> None:  # type: ignore[override]
        item = self.currentItem()
        if item is None:
            return
        comp_id = item.data(Qt.ItemDataRole.UserRole)
        if not comp_id:
            return
        mime = QMimeData()
        mime.setData(COMPONENT_MIME_TYPE, str(comp_id).encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction | Qt.DropAction.MoveAction)


# ---------------------------------------------------------------------------
# Right Sidebar: Live Physical Cable Length & BOM Panel
# ---------------------------------------------------------------------------


class PhysicalBomPanel(QWidget):
    """Live Physical Cable Length & Bill-of-Materials panel grouped by PortType."""

    cable_activated = Signal(str)

    def __init__(self, state: ProjectState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state = state
        self.setMinimumWidth(270)
        self.setMaximumWidth(400)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        title = QLabel("Physical Cable Length & BOM", self)
        title.setStyleSheet("font-size: 14px; font-weight: 700; color: #38BDF8;")
        root.addWidget(title)

        # Configurable Slack %
        slack_row = QHBoxLayout()
        slack_row.addWidget(QLabel("Slack Allowance:", self))
        self.slack_spin = QDoubleSpinBox(self)
        self.slack_spin.setRange(0.0, 50.0)
        self.slack_spin.setSingleStep(1.0)
        self.slack_spin.setDecimals(1)
        self.slack_spin.setValue(10.0)
        self.slack_spin.setSuffix(" %")
        self.slack_spin.valueChanged.connect(self.refresh)
        slack_row.addWidget(self.slack_spin, 1)
        root.addLayout(slack_row)

        self.total_kpi_label = QLabel("Total: 0.0 m (0 runs)", self)
        self.total_kpi_label.setStyleSheet(
            "background-color: #0F172A; border: 1px solid #334155; border-radius: 6px; "
            "padding: 6px 10px; font-weight: 700; color: #22C55E;"
        )
        root.addWidget(self.total_kpi_label)

        # Grouped Totals by PortType
        grp_box = QGroupBox("Totals by PortType", self)
        grp_layout = QVBoxLayout(grp_box)
        self.port_summary_list = QListWidget(grp_box)
        self.port_summary_list.setMaximumHeight(160)
        grp_layout.addWidget(self.port_summary_list)
        root.addWidget(grp_box)

        # Per-Cable Run Breakdown
        run_box = QGroupBox("Per-Cable Run Breakdown", self)
        run_layout = QVBoxLayout(run_box)
        self.runs_table = QTableWidget(0, 5, run_box)
        self.runs_table.setHorizontalHeaderLabels(
            ["Cable", "Type", "Horiz", "Riser", "Total"]
        )
        self.runs_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.runs_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.runs_table.verticalHeader().setVisible(False)
        hdr = self.runs_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in (1, 2, 3, 4):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.runs_table.cellClicked.connect(self._on_run_clicked)
        run_layout.addWidget(self.runs_table)
        root.addWidget(run_box, 1)

    @property
    def slack_factor(self) -> float:
        return self.slack_spin.value() / 100.0

    def refresh(self) -> None:
        summary = calculate_project_cable_bom(
            self._state.project, slack_factor=self.slack_factor
        )
        self.total_kpi_label.setText(
            f"Total (+{self.slack_spin.value():.0f}% slack): "
            f"{summary.total_m:.1f} m ({len(summary.runs)} runs)"
        )

        self.port_summary_list.clear()
        for pt_sum in summary.by_port_type:
            run_word = "run" if pt_sum.run_count == 1 else "runs"
            text = (
                f"{_format_port_type_label(pt_sum.port_type.value)}: "
                f"{pt_sum.total_m:.1f} m ({pt_sum.run_count} {run_word})"
            )
            item = QListWidgetItem(text)
            item.setToolTip(
                f"Horizontal: {pt_sum.horizontal_m:.2f} m · "
                f"Riser: {pt_sum.vertical_riser_m:.2f} m · "
                f"Total w/ slack: {pt_sum.total_m:.2f} m"
            )
            self.port_summary_list.addItem(item)

        self.runs_table.setRowCount(len(summary.runs))
        for row_idx, run in enumerate(summary.runs):
            c_item = QTableWidgetItem(run.label)
            c_item.setData(Qt.ItemDataRole.UserRole, run.cable_id)
            mode_note = "inter-floor" if run.is_inter_floor else (
                "est." if run.is_estimated else "routed"
            )
            c_item.setToolTip(f"{run.label} ({mode_note})")
            t_item = QTableWidgetItem(run.port_type.value)
            h_item = QTableWidgetItem(f"{run.horizontal_m:.1f}m")
            r_item = QTableWidgetItem(f"{run.vertical_riser_m:.1f}m")
            tot_item = QTableWidgetItem(f"{run.total_with_slack_m:.1f}m")
            self.runs_table.setItem(row_idx, 0, c_item)
            self.runs_table.setItem(row_idx, 1, t_item)
            self.runs_table.setItem(row_idx, 2, h_item)
            self.runs_table.setItem(row_idx, 3, r_item)
            self.runs_table.setItem(row_idx, 4, tot_item)

    def _on_run_clicked(self, row: int, _col: int) -> None:
        item = self.runs_table.item(row, 0)
        if item is not None:
            cab_id = item.data(Qt.ItemDataRole.UserRole)
            if cab_id:
                self.cable_activated.emit(str(cab_id))


# ---------------------------------------------------------------------------
# Tab 3 Root View: PhysicalMappingView
# ---------------------------------------------------------------------------


class PhysicalMappingView(QWidget):
    """Complete Tab 3 Physical Mapping, Inter-Floor Riser Routing & BOM Workspace."""

    def __init__(self, state: ProjectState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state = state
        self._syncing_selectors = False
        self._build_ui()
        self._wire_signals()
        self.refresh_all(fit_view=True)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # --- Top Toolbar ---
        self.toolbar = QToolBar("Physical Mapping Tools", self)
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

        self.bundle_btn = QPushButton("Bundle Physical", self)
        self.unbundle_btn = QPushButton("Unbundle", self)
        self.collapse_bundle_btn = QPushButton("Toggle Bundle Collapse", self)
        self.toolbar.addWidget(self.bundle_btn)
        self.toolbar.addWidget(self.unbundle_btn)
        self.toolbar.addWidget(self.collapse_bundle_btn)
        self.toolbar.addSeparator()

        self.presurvey_btn = QPushButton("Pre-Survey Wi-Fi Coverage", self)
        self.presurvey_btn.setCheckable(True)
        self.presurvey_btn.setToolTip(
            "Estimate wall-attenuated Wi-Fi RSSI coverage around placed Access Points"
        )
        self.toolbar.addWidget(self.presurvey_btn)

        self.coverage_kpi_label = QLabel("", self)
        self.coverage_kpi_label.setStyleSheet("color: #94A3B8; padding: 0 6px;")
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

        # --- Main 3-Pane Splitter ---
        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)

        # Left Sidebar: Building/Floor Switcher + Searchable Unplaced & Placed Devices
        self.left_sidebar = QWidget(self)
        self.left_sidebar.setMinimumWidth(250)
        self.left_sidebar.setMaximumWidth(360)
        left_layout = QVBoxLayout(self.left_sidebar)
        left_layout.setContentsMargins(8, 8, 8, 8)
        left_layout.setSpacing(8)

        floor_group = QGroupBox("Active Building & Floor", self.left_sidebar)
        fg_layout = QVBoxLayout(floor_group)
        self.building_combo = QComboBox(floor_group)
        self.floor_combo = QComboBox(floor_group)
        fg_layout.addWidget(QLabel("Building:", floor_group))
        fg_layout.addWidget(self.building_combo)
        fg_layout.addWidget(QLabel("Floor:", floor_group))
        fg_layout.addWidget(self.floor_combo)
        left_layout.addWidget(floor_group)

        self.search_edit = QLineEdit(self.left_sidebar)
        self.search_edit.setPlaceholderText("Search devices (name, IP, MAC, type)...")
        self.search_edit.setClearButtonEnabled(True)
        left_layout.addWidget(self.search_edit)

        unplaced_grp = QGroupBox("Unplaced Logical Components", self.left_sidebar)
        ug_layout = QVBoxLayout(unplaced_grp)
        self.unplaced_list = _DraggableComponentList(unplaced_grp)
        self.unplaced_list.setDragEnabled(True)
        self.unplaced_list.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        ug_layout.addWidget(self.unplaced_list)
        self.place_center_btn = QPushButton("Place Selected at Floor Center", unplaced_grp)
        ug_layout.addWidget(self.place_center_btn)
        left_layout.addWidget(unplaced_grp, 1)

        placed_grp = QGroupBox("Placed Logical Components", self.left_sidebar)
        pg_layout = QVBoxLayout(placed_grp)
        self.placed_list = QListWidget(placed_grp)
        self.placed_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        pg_layout.addWidget(self.placed_list)
        self.unplace_btn = QPushButton("Unplace Selected", placed_grp)
        pg_layout.addWidget(self.unplace_btn)
        left_layout.addWidget(placed_grp, 1)

        self.canvas = PhysicalCanvasView(self._state, self)
        self.bom_panel = PhysicalBomPanel(self._state, self)

        self.splitter.addWidget(self.left_sidebar)
        self.splitter.addWidget(self.canvas)
        self.splitter.addWidget(self.bom_panel)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)
        root.addWidget(self.splitter, 1)

        # --- Bottom Connection Trace Bar ---
        trace_bar = QFrame(self)
        trace_bar.setStyleSheet(
            "background-color: #0F172A; border-top: 1px solid #334155; padding: 4px 10px;"
        )
        trace_row = QHBoxLayout(trace_bar)
        trace_row.setContentsMargins(6, 3, 6, 3)
        trace_prefix = QLabel("TRACE:", trace_bar)
        trace_prefix.setStyleSheet("color: #38BDF8; font-weight: 700; font-size: 11px;")
        self.trace_label = QLabel(
            "Hover any physical cable or bundle to trace endpoints & riser path", trace_bar
        )
        self.trace_label.setStyleSheet("color: #CBD5E1; font-size: 12px;")
        trace_row.addWidget(trace_prefix)
        trace_row.addWidget(self.trace_label, 1)
        root.addWidget(trace_bar)

        QShortcut(QKeySequence("F"), self, activated=self.canvas.fit_floor_in_view)

    def _wire_signals(self) -> None:
        self.building_combo.currentIndexChanged.connect(self._on_building_combo_changed)
        self.floor_combo.currentIndexChanged.connect(self._on_floor_combo_changed)
        self.search_edit.textChanged.connect(self.refresh_component_lists)

        self.unplaced_list.itemDoubleClicked.connect(self._on_unplaced_double_clicked)
        self.place_center_btn.clicked.connect(self._place_selected_unplaced_at_center)

        self.placed_list.itemDoubleClicked.connect(self._on_placed_double_clicked)
        self.placed_list.customContextMenuRequested.connect(self._on_placed_context_menu)
        self.unplace_btn.clicked.connect(self._unplace_selected)

        self.bundle_btn.clicked.connect(self.canvas.bundle_selected_cables)
        self.unbundle_btn.clicked.connect(self.canvas.unbundle_selected)
        self.collapse_bundle_btn.clicked.connect(self.canvas.toggle_selected_bundle_collapse)
        self.presurvey_btn.toggled.connect(self._on_presurvey_toggled)
        self.fit_btn.clicked.connect(self.canvas.fit_floor_in_view)

        self.canvas.tool_changed.connect(self._on_canvas_tool_changed)
        self.canvas.view_rotation_changed.connect(self._on_canvas_view_rotation_changed)
        self.canvas.riser_jump_requested.connect(self.set_active_floor)
        self.canvas.connection_trace_changed.connect(self.trace_label.setText)
        self.canvas.live_bom_refresh_requested.connect(self.bom_panel.refresh)
        self.canvas.presurvey_coverage_updated.connect(self._on_presurvey_coverage_updated)
        self.bom_panel.cable_activated.connect(self._on_bom_cable_activated)

        self._state.project_reloaded.connect(lambda: self.refresh_all(fit_view=True))
        self._state.buildings_changed.connect(self._on_state_buildings_changed)
        self._state.floor_geometry_changed.connect(self._on_state_floor_geometry_changed)
        self._state.floor_view_rotated.connect(self._on_state_floor_view_rotated)
        self._state.components_changed.connect(self._on_state_components_or_cables_changed)
        self._state.cables_changed.connect(self._on_state_components_or_cables_changed)
        self._state.bundles_changed.connect(self._on_state_components_or_cables_changed)

    # ------------------------------------------------------------------
    # Building / Floor Switching & Live Sync
    # ------------------------------------------------------------------

    def active_building_id(self) -> str | None:
        return self.canvas._building_id

    def active_floor_id(self) -> str | None:
        return self.canvas._floor_id

    def set_active_floor(self, building_id: str, floor_id: str) -> None:
        """Switch active building & floor cleanly across combos and canvas."""
        b_idx = self.building_combo.findData(building_id)
        if b_idx >= 0:
            self._syncing_selectors = True
            try:
                self.building_combo.setCurrentIndex(b_idx)
                self._populate_floor_combo(building_id)
                f_idx = self.floor_combo.findData(floor_id)
                if f_idx >= 0:
                    self.floor_combo.setCurrentIndex(f_idx)
            finally:
                self._syncing_selectors = False
        self.canvas.set_floor_context(building_id, floor_id, fit_view=True)
        self._state.set_selection("building", building_id)
        self._state.set_selection("floor", floor_id)
        self.refresh_component_lists()
        self.bom_panel.refresh()

    def refresh_all(self, *, fit_view: bool = False) -> None:
        self._populate_building_and_floor_combos()
        b_id = self.building_combo.currentData()
        f_id = self.floor_combo.currentData()
        self.canvas.set_floor_context(
            str(b_id) if b_id else None,
            str(f_id) if f_id else None,
            fit_view=fit_view,
        )
        self.refresh_component_lists()
        self.bom_panel.refresh()

    def _populate_building_and_floor_combos(self) -> None:
        prev_b = self.canvas._building_id or self._state.selected_building_id
        prev_f = self.canvas._floor_id or self._state.selected_floor_id
        self._syncing_selectors = True
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
            self._syncing_selectors = False

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

    def _on_building_combo_changed(self) -> None:
        if self._syncing_selectors:
            return
        b_id = self.building_combo.currentData()
        self._syncing_selectors = True
        try:
            self._populate_floor_combo(str(b_id) if b_id else "")
        finally:
            self._syncing_selectors = False
        f_id = self.floor_combo.currentData()
        self.canvas.set_floor_context(
            str(b_id) if b_id else None,
            str(f_id) if f_id else None,
            fit_view=True,
        )
        self.refresh_component_lists()
        self.bom_panel.refresh()

    def _on_floor_combo_changed(self) -> None:
        if self._syncing_selectors:
            return
        b_id = self.building_combo.currentData()
        f_id = self.floor_combo.currentData()
        self.canvas.set_floor_context(
            str(b_id) if b_id else None,
            str(f_id) if f_id else None,
            fit_view=True,
        )
        self.refresh_component_lists()
        self.bom_panel.refresh()

    # ------------------------------------------------------------------
    # Unplaced & Placed Logical Component Lists
    # ------------------------------------------------------------------

    def refresh_component_lists(self) -> None:
        query = self.search_edit.text().strip().lower()
        self.unplaced_list.clear()
        self.placed_list.clear()

        for comp in sorted(self._state.project.components, key=lambda c: c.name.lower()):
            haystack = " ".join(
                [
                    comp.name,
                    comp.ip_address,
                    comp.mac_address,
                    comp.component_type.value,
                    " ".join(comp.ssids),
                ]
            ).lower()
            if query and query not in haystack:
                continue

            is_placed = (
                comp.physical_building_id is not None
                and comp.physical_floor_id is not None
                and comp.physical_pos_m is not None
            )
            type_label = comp.component_type.value.replace("_", " ").title()
            if not is_placed:
                item = QListWidgetItem(f"{comp.name} · {type_label}")
                item.setData(Qt.ItemDataRole.UserRole, comp.id)
                item.setForeground(component_color(comp.component_type))
                item.setToolTip("Drag onto floor canvas or double-click to place at floor center")
                self.unplaced_list.addItem(item)
            else:
                bldg = self._state.project.get_building(comp.physical_building_id or "")
                fl = (
                    self._state.project.get_floor(
                        comp.physical_building_id or "", comp.physical_floor_id or ""
                    )
                    if bldg
                    else None
                )
                loc = f"{bldg.name} / {fl.name}" if (bldg and fl) else "Unknown Floor"
                item = QListWidgetItem(f"{comp.name} · {loc}")
                item.setData(Qt.ItemDataRole.UserRole, comp.id)
                item.setForeground(component_color(comp.component_type))
                item.setToolTip("Double-click to jump to component · Right-click to unplace")
                self.placed_list.addItem(item)

    def _place_component_at_active_center(self, comp_id: str) -> None:
        b_id = self.canvas._building_id
        f_id = self.canvas._floor_id
        floor = self.canvas.current_floor()
        if not b_id or not f_id or floor is None:
            return
        center_m = _floor_center_m(floor)
        self._state.place_component_physical(comp_id, b_id, f_id, center_m)

    def _on_unplaced_double_clicked(self, item: QListWidgetItem) -> None:
        comp_id = item.data(Qt.ItemDataRole.UserRole)
        if comp_id:
            self._place_component_at_active_center(str(comp_id))

    def _place_selected_unplaced_at_center(self) -> None:
        item = self.unplaced_list.currentItem()
        if item is not None:
            comp_id = item.data(Qt.ItemDataRole.UserRole)
            if comp_id:
                self._place_component_at_active_center(str(comp_id))

    def _on_placed_double_clicked(self, item: QListWidgetItem) -> None:
        comp_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
        comp = self._state.project.get_component(comp_id)
        if comp is None or not comp.physical_building_id or not comp.physical_floor_id:
            return
        if (
            comp.physical_building_id != self.canvas._building_id
            or comp.physical_floor_id != self.canvas._floor_id
        ):
            self.set_active_floor(comp.physical_building_id, comp.physical_floor_id)
        self.canvas.select_component(comp.id)

    def _on_placed_context_menu(self, pos: QPoint) -> None:
        item = self.placed_list.itemAt(pos)
        if item is None:
            return
        comp_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
        menu = QMenu(self)
        jump_act = menu.addAction("Jump to Component on Floor")
        unplace_act = menu.addAction("Unplace from Floor")
        chosen = menu.exec(self.placed_list.viewport().mapToGlobal(pos))
        if chosen == jump_act:
            self._on_placed_double_clicked(item)
        elif chosen == unplace_act and comp_id:
            self._state.place_component_physical(comp_id, None, None, None)

    def _unplace_selected(self) -> None:
        item = self.placed_list.currentItem()
        if item is not None:
            comp_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
            if comp_id:
                self._state.place_component_physical(comp_id, None, None, None)
                return
        for c_id, c_item in self.canvas._comp_items.items():
            if c_item.isSelected():
                self._state.place_component_physical(c_id, None, None, None)
                return

    # ------------------------------------------------------------------
    # State & Toolbar Handlers
    # ------------------------------------------------------------------

    def _on_canvas_tool_changed(self, tool_val: str) -> None:
        self.select_tool_btn.setChecked(tool_val == ToolMode.SELECT.value)
        self.pan_tool_btn.setChecked(tool_val == ToolMode.PAN.value)

    def _on_canvas_view_rotation_changed(self, angle: float) -> None:
        self.view_rot_spin.blockSignals(True)
        self.view_rot_spin.setValue(angle)
        self.view_rot_spin.blockSignals(False)

    def _on_presurvey_toggled(self, checked: bool) -> None:
        if not checked:
            self.coverage_kpi_label.setText("")
        self.canvas.set_presurvey_enabled(checked)

    def _on_presurvey_coverage_updated(self, coverage_pct: float, ap_count: int) -> None:
        if not self.presurvey_btn.isChecked():
            self.coverage_kpi_label.setText("")
            return
        if ap_count == 0:
            self.coverage_kpi_label.setText("Pre-Survey: Place an AP on this floor")
        else:
            self.coverage_kpi_label.setText(
                f"Pre-Survey ≥ -70 dBm: {coverage_pct:.1f}% ({ap_count} AP)"
            )

    def _on_bom_cable_activated(self, cable_id: str) -> None:
        cable = next((c for c in self._state.project.cables if c.id == cable_id), None)
        if cable is None:
            return
        src = self._state.project.get_component(cable.source_component_id)
        if (
            src is not None
            and src.physical_building_id
            and src.physical_floor_id
            and (
                src.physical_building_id != self.canvas._building_id
                or src.physical_floor_id != self.canvas._floor_id
            )
        ):
            self.set_active_floor(src.physical_building_id, src.physical_floor_id)
        self.canvas.select_cable(cable_id)

    def _on_state_buildings_changed(self) -> None:
        self._populate_building_and_floor_combos()
        b_id = self.building_combo.currentData()
        f_id = self.floor_combo.currentData()
        self.canvas.set_floor_context(
            str(b_id) if b_id else None,
            str(f_id) if f_id else None,
            fit_view=False,
        )
        self.refresh_component_lists()
        self.bom_panel.refresh()

    def _on_state_floor_geometry_changed(self, building_id: str, floor_id: str) -> None:
        if building_id == self.canvas._building_id and floor_id == self.canvas._floor_id:
            self.canvas.rebuild_scene()
        self.bom_panel.refresh()

    def _on_state_floor_view_rotated(
        self, building_id: str, floor_id: str, rot: float
    ) -> None:
        if building_id == self.canvas._building_id and floor_id == self.canvas._floor_id:
            if abs(self.canvas.view_rotation_deg - rot) > 1e-6:
                self.canvas.set_view_rotation(rot, commit_to_state=False)

    def _on_state_components_or_cables_changed(self) -> None:
        self.canvas.rebuild_scene()
        self.refresh_component_lists()
        self.bom_panel.refresh()
