"""Tab 2 (Part B) Precision Multi-Floor CAD Vector Editor (Section 4.6)."""

from __future__ import annotations

import copy
import io
import math
import time
from collections.abc import Callable
from enum import StrEnum

from PIL import Image
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QFont,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QTransform,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
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
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSlider,
    QSplitter,
    QStackedWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from desktop.coords import PIXELS_PER_METER, local_m_to_scene, scene_to_local_m
from desktop.floor_geometry import (
    clip_and_cut_room_polygon,
    project_point_to_walls,
    reconcile_doors_after_wall_change,
    snap_editor_point,
)
from desktop.state import ProjectState
from shared.geo_math import polygon_area_m2, true_north_screen_cw_deg
from shared.models import (
    BlueprintUnderlay,
    Building,
    Door,
    Floor,
    Point2D,
    Room,
    Stairs,
    VerticalHole,
)

Z_ONION = -50.0
Z_BLUEPRINT = -40.0
Z_FLOOR_OUTER = 0.0
Z_ROOM = 10.0
Z_STAIRS = 20.0
Z_HOLE = 30.0
Z_DOOR = 35.0
Z_DRAFT = 55.0
Z_SNAP_GUIDE = 60.0
Z_HANDLE = 70.0


class ToolMode(StrEnum):
    SELECT = "select"
    EDIT_OUTLINE = "edit_outline"
    DRAW_ROOM = "draw_room"
    PLACE_DOOR = "place_door"
    DRAW_STAIRS = "draw_stairs"
    PLACE_HOLE = "place_hole"
    ALIGN_VIEW_EDGE = "align_view_edge"
    CALIBRATE_BLUEPRINT = "calibrate_blueprint"
    PAN = "pan"


_TOOL_CURSORS: dict[ToolMode, Qt.CursorShape] = {
    ToolMode.SELECT: Qt.CursorShape.ArrowCursor,
    ToolMode.EDIT_OUTLINE: Qt.CursorShape.CrossCursor,
    ToolMode.DRAW_ROOM: Qt.CursorShape.CrossCursor,
    ToolMode.PLACE_DOOR: Qt.CursorShape.CrossCursor,
    ToolMode.DRAW_STAIRS: Qt.CursorShape.CrossCursor,
    ToolMode.PLACE_HOLE: Qt.CursorShape.CrossCursor,
    ToolMode.ALIGN_VIEW_EDGE: Qt.CursorShape.CrossCursor,
    ToolMode.CALIBRATE_BLUEPRINT: Qt.CursorShape.CrossCursor,
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
    # Wall threshold opening
    path.moveTo(-half_w, 0.0)
    path.lineTo(half_w, 0.0)
    # Door leaf perpendicular to wall
    path.moveTo(hinge_x, 0.0)
    path.lineTo(hinge_x, swing_dir * w_px)
    # Swing arc from leaf tip to latch side
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


class _CornerHandle(QGraphicsEllipseItem):
    """Draggable corner vertex handle (z=70) for Room or Floor outline editing."""

    def __init__(self, kind: str, entity_id: str, index: int, pt_m: Point2D) -> None:
        super().__init__(-6.0, -6.0, 12.0, 12.0)
        self.kind = kind  # "room" | "outline"
        self.entity_id = entity_id
        self.index = index
        self.setPos(local_m_to_scene(pt_m))
        self.setZValue(Z_HANDLE)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, False)
        self.setBrush(QBrush(QColor("#38BDF8")))
        self.setPen(QPen(QColor("#FFFFFF"), 1.5))
        self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))


class _MidpointHandle(QGraphicsEllipseItem):
    """Clickable '+' midpoint handle (z=70) to insert a vertex on an edge."""

    def __init__(self, kind: str, entity_id: str, insert_index: int, pt_m: Point2D) -> None:
        super().__init__(-5.0, -5.0, 10.0, 10.0)
        self.kind = kind
        self.entity_id = entity_id
        self.insert_index = insert_index
        self.setPos(local_m_to_scene(pt_m))
        self.setZValue(Z_HANDLE)
        self.setBrush(QBrush(QColor("#22C55E")))
        self.setPen(QPen(QColor("#FFFFFF"), 1.2))
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))


class FloorEditorView(QGraphicsView):
    """
    Precision Multi-Floor CAD Vector Editor view implementing Section 4.6.

    Strict non-panning invariant: DragMode is NEVER ScrollHandDrag unless Pan Tool (H),
    Middle-Mouse drag, or Space+Left-Drag is active.
    """

    tool_changed = Signal(str)
    selection_entity_changed = Signal(str, str)  # (kind, entity_id)
    view_rotation_changed = Signal(float)
    status_hint = Signal(str)
    onion_skin_changed = Signal(bool)

    def __init__(
        self,
        state: ProjectState,
        *,
        calibration_prompt: Callable[[str], float | None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._state = state
        self._calibration_prompt = calibration_prompt
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

        # Explicit view transform state (rebuilt cleanly — zero floating-point drift)
        self._zoom: float = 1.0
        self._view_rotation_deg: float = 0.0

        # Tool & options
        self._tool: ToolMode = ToolMode.SELECT
        self._clip_to_outer: bool = True
        self._auto_cut_rooms: bool = True
        self._onion_skin_enabled: bool = False

        # Panning state (Middle-mouse / Space+Drag / Hand tool)
        self._space_held: bool = False
        self._panning_active: bool = False
        self._pan_used_while_space: bool = False
        self._pan_last_pos: QPoint = QPoint()

        # Selection & Handle dragging
        self._selected_kind: str = ""
        self._selected_id: str = ""
        self._dragging_handle: _CornerHandle | None = None
        self._dragging_door_id: str | None = None
        self._door_drag_original: Door | None = None
        self._room_drag_original: Room | None = None
        self._outline_drag_original: list[Point2D] | None = None

        # Safe double-click & draft room drawing state (Section 4.6C)
        self._draft_room_pts: list[Point2D] = []
        self._typed_length_buf: str = ""
        self._last_cursor_m: Point2D = Point2D(0.0, 0.0)
        self._last_snap_kind: str = ""
        self._last_snap_guide: tuple[Point2D, Point2D] | None = None
        self._ignore_next_release: bool = False
        self._post_finish_guard_until: float = 0.0

        # Stairs drag state
        self._stairs_start_m: Point2D | None = None
        self._stairs_current_m: Point2D | None = None

        # Door placement hover preview
        self._door_preview_hit_pos: Point2D | None = None
        self._door_preview_angle: float = 0.0
        self._door_preview_valid: bool = False

        # Blueprint calibration 2-point state
        self._calib_pts_m: list[Point2D] = []

        # Scene item registries
        self._room_items: dict[str, QGraphicsPolygonItem] = {}
        self._door_items: dict[str, QGraphicsPathItem] = {}
        self._stairs_items: dict[str, QGraphicsPolygonItem] = {}
        self._hole_items: dict[str, QGraphicsEllipseItem] = {}
        self._corner_handles: list[_CornerHandle] = []
        self._midpoint_handles: list[_MidpointHandle] = []
        self._draft_items: list[QGraphicsItem] = []

        self._apply_view_transform()

    # ------------------------------------------------------------------
    # Context & Accessors
    # ------------------------------------------------------------------

    @property
    def tool_mode(self) -> ToolMode:
        return self._tool

    @property
    def view_rotation_deg(self) -> float:
        return self._view_rotation_deg

    @property
    def onion_skin_enabled(self) -> bool:
        return self._onion_skin_enabled

    @property
    def clip_to_outer(self) -> bool:
        return self._clip_to_outer

    @property
    def auto_cut_rooms(self) -> bool:
        return self._auto_cut_rooms

    def current_building(self) -> Building | None:
        if not self._building_id:
            return None
        return self._state.project.get_building(self._building_id)

    def current_floor(self) -> Floor | None:
        if not self._building_id or not self._floor_id:
            return None
        return self._state.project.get_floor(self._building_id, self._floor_id)

    def true_north_angle_deg(self) -> float:
        """Exact Section 4.2C & 4.6B True North CW screen angle."""
        bldg = self.current_building()
        anchor_rot = bldg.geo_anchor.rotation_deg if bldg and bldg.geo_anchor else 0.0
        return true_north_screen_cw_deg(self._view_rotation_deg, anchor_rot)

    def typed_length_hud_text(self) -> str:
        if not self._typed_length_buf:
            return ""
        return f"Length: {self._typed_length_buf} m (Enter to place)"

    def set_floor_context(self, building_id: str | None, floor_id: str | None) -> None:
        self._building_id = building_id
        self._floor_id = floor_id
        self._clear_drafts()
        self._selected_kind = ""
        self._selected_id = ""
        floor = self.current_floor()
        self._view_rotation_deg = floor.editor_view_rotation_deg if floor is not None else 0.0
        self._apply_view_transform()
        self.view_rotation_changed.emit(self._view_rotation_deg)
        self.rebuild_scene()

    def set_tool(self, tool: ToolMode) -> None:
        if tool != self._tool:
            self._clear_drafts()
        self._tool = tool
        if tool == ToolMode.PAN:
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        else:
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.viewport().setCursor(_TOOL_CURSORS.get(tool, Qt.CursorShape.ArrowCursor))
        self._rebuild_handles()
        self.tool_changed.emit(tool.value)
        self.viewport().update()

    def set_clip_to_outer(self, enabled: bool) -> None:
        self._clip_to_outer = bool(enabled)

    def set_auto_cut_rooms(self, enabled: bool) -> None:
        self._auto_cut_rooms = bool(enabled)

    def set_onion_skin(self, enabled: bool) -> None:
        self._onion_skin_enabled = bool(enabled)
        self.onion_skin_changed.emit(self._onion_skin_enabled)
        self.rebuild_scene()

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

    def select_entity(self, kind: str, entity_id: str) -> None:
        self._selected_kind = kind
        self._selected_id = entity_id
        self.rebuild_scene()
        self.selection_entity_changed.emit(kind, entity_id)

    # ------------------------------------------------------------------
    # Scene Rendering
    # ------------------------------------------------------------------

    def rebuild_scene(self) -> None:
        self._scene.clear()
        self._room_items.clear()
        self._door_items.clear()
        self._stairs_items.clear()
        self._hole_items.clear()
        self._corner_handles.clear()
        self._midpoint_handles.clear()
        self._draft_items.clear()

        bldg = self.current_building()
        floor = self.current_floor()
        if bldg is None or floor is None:
            return

        # 1. Onion-Skinning (level_index - 1 at 25% opacity)
        if self._onion_skin_enabled:
            below = next(
                (f for f in bldg.floors if f.level_index == floor.level_index - 1),
                None,
            )
            if below is not None:
                self._render_onion_floor(below)

        # 2. Blueprint Underlay
        if floor.blueprint is not None and floor.blueprint.visible:
            self._render_blueprint(floor.blueprint)

        # 3. Floor Outer Polygon
        if len(floor.outer_polygon_m) >= 3:
            outer_item = QGraphicsPolygonItem(_poly_to_qpolygon(floor.outer_polygon_m))
            outer_item.setZValue(Z_FLOOR_OUTER)
            outer_item.setBrush(QBrush(QColor(15, 23, 42, 220)))
            outer_item.setPen(QPen(QColor("#94A3B8"), 2.5))
            self._scene.addItem(outer_item)

        # 4. Rooms
        for room in floor.rooms:
            if len(room.polygon_m) < 3:
                continue
            is_sel = self._selected_kind == "room" and self._selected_id == room.id
            r_item = QGraphicsPolygonItem(_poly_to_qpolygon(room.polygon_m))
            r_item.setZValue(Z_ROOM)
            fill = QColor(room.fill_color_hex)
            if not fill.isValid():
                fill = QColor("#1E293B")
            fill.setAlpha(155 if is_sel else 125)
            r_item.setBrush(QBrush(fill))
            r_item.setPen(
                QPen(QColor("#F59E0B" if is_sel else "#38BDF8"), 2.5 if is_sel else 1.8)
            )
            self._scene.addItem(r_item)
            self._room_items[room.id] = r_item

            # Room label at centroid
            cx = sum(p.x for p in room.polygon_m) / len(room.polygon_m)
            cy = sum(p.y for p in room.polygon_m) / len(room.polygon_m)
            lbl = QGraphicsSimpleTextItem(room.name)
            lbl.setBrush(QBrush(QColor("#E2E8F0")))
            font = QFont("Segoe UI", 9, QFont.Weight.DemiBold)
            lbl.setFont(font)
            br = lbl.boundingRect()
            c_scene = local_m_to_scene(Point2D(cx, cy))
            lbl.setPos(c_scene.x() - br.width() * 0.5, c_scene.y() - br.height() * 0.5)
            lbl.setZValue(Z_ROOM + 1.0)
            self._scene.addItem(lbl)

        # 5. Stairs
        for stairs in floor.stairs:
            if len(stairs.polygon_m) < 3:
                continue
            is_sel = self._selected_kind == "stairs" and self._selected_id == stairs.id
            s_item = QGraphicsPolygonItem(_poly_to_qpolygon(stairs.polygon_m))
            s_item.setZValue(Z_STAIRS)
            s_item.setBrush(QBrush(QColor(168, 85, 247, 70)))
            s_item.setPen(
                QPen(QColor("#F59E0B" if is_sel else "#A855F7"), 2.0)
            )
            self._scene.addItem(s_item)
            self._stairs_items[stairs.id] = s_item
            self._render_stairs_hatch(stairs)

        # 6. Ghost Vertical Holes from adjacent floors (level_index +- 1)
        for adj_floor in bldg.floors:
            if abs(adj_floor.level_index - floor.level_index) == 1:
                for gh in adj_floor.holes:
                    r_px = gh.radius_m * PIXELS_PER_METER
                    pos = local_m_to_scene(gh.position_m)
                    g_item = QGraphicsEllipseItem(-r_px, -r_px, 2 * r_px, 2 * r_px)
                    g_item.setPos(pos)
                    g_item.setZValue(Z_HOLE - 2.0)
                    g_item.setBrush(QBrush(QColor(250, 204, 21, 30)))
                    pen = QPen(QColor(250, 204, 21, 130), 1.5, Qt.PenStyle.DashLine)
                    g_item.setPen(pen)
                    self._scene.addItem(g_item)

        # 7. Vertical Holes on current floor
        for hole in floor.holes:
            is_sel = self._selected_kind == "hole" and self._selected_id == hole.id
            r_px = max(8.0, hole.radius_m * PIXELS_PER_METER)
            h_item = QGraphicsEllipseItem(-r_px, -r_px, 2 * r_px, 2 * r_px)
            h_item.setPos(local_m_to_scene(hole.position_m))
            h_item.setZValue(Z_HOLE)
            h_item.setBrush(QBrush(QColor(250, 204, 21, 110)))
            h_item.setPen(
                QPen(QColor("#FFFFFF" if is_sel else "#FACC15"), 2.5 if is_sel else 1.8)
            )
            self._scene.addItem(h_item)
            self._hole_items[hole.id] = h_item

            tag = QGraphicsSimpleTextItem(f"{hole.label} [{hole.riser_group_id}]")
            tag.setBrush(QBrush(QColor("#FDE68A")))
            tag.setFont(QFont("Segoe UI", 8))
            pt_s = local_m_to_scene(hole.position_m)
            tag.setPos(pt_s.x() + r_px + 4.0, pt_s.y() - 7.0)
            tag.setZValue(Z_HOLE + 1.0)
            self._scene.addItem(tag)

        # 8. Doors (wall-locked)
        for door in floor.doors:
            is_sel = self._selected_kind == "door" and self._selected_id == door.id
            d_item = QGraphicsPathItem(_door_graphics_path(door))
            d_item.setPos(local_m_to_scene(door.position_m))
            # Scene Y is inverted, so CCW math angle_deg becomes -angle_deg in Qt rotation
            d_item.setRotation(-door.angle_deg)
            d_item.setZValue(Z_DOOR)
            d_item.setPen(
                QPen(QColor("#F59E0B" if is_sel else "#22C55E"), 2.6 if is_sel else 2.0)
            )
            self._scene.addItem(d_item)
            self._door_items[door.id] = d_item

        self._rebuild_handles()
        self._rebuild_draft_overlays()

    def _render_onion_floor(self, below: Floor) -> None:
        if len(below.outer_polygon_m) >= 3:
            item = QGraphicsPolygonItem(_poly_to_qpolygon(below.outer_polygon_m))
            item.setZValue(Z_ONION)
            item.setBrush(QBrush(QColor(148, 163, 184, 25)))
            item.setPen(QPen(QColor(148, 163, 184, 64), 1.5, Qt.PenStyle.DashLine))
            self._scene.addItem(item)
        for room in below.rooms:
            if len(room.polygon_m) < 3:
                continue
            r_item = QGraphicsPolygonItem(_poly_to_qpolygon(room.polygon_m))
            r_item.setZValue(Z_ONION + 1.0)
            r_item.setBrush(QBrush(QColor(56, 189, 248, 20)))
            r_item.setPen(QPen(QColor(56, 189, 248, 64), 1.2, Qt.PenStyle.DashLine))
            self._scene.addItem(r_item)

    def _render_blueprint(self, bp: BlueprintUnderlay) -> None:
        raw = self._state.blueprint_bytes.get(bp.archive_path)
        if not raw:
            return
        qimg = QImage.fromData(raw)
        if qimg.isNull():
            return
        pix = QPixmap.fromImage(qimg)
        item = QGraphicsPixmapItem(pix)
        item.setZValue(Z_BLUEPRINT)
        item.setOpacity(max(0.0, min(1.0, bp.opacity)))
        scale = bp.meters_per_pixel * PIXELS_PER_METER
        item.setScale(scale)
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
                    QPointF(x * PIXELS_PER_METER, -min_y * PIXELS_PER_METER).x(),
                    -min_y * PIXELS_PER_METER,
                    x * PIXELS_PER_METER,
                    -max_y * PIXELS_PER_METER,
                )
                ln.setPen(QPen(QColor(168, 85, 247, 150), 1.0))
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
                ln.setPen(QPen(QColor(168, 85, 247, 150), 1.0))
                ln.setZValue(Z_STAIRS + 0.5)
                self._scene.addItem(ln)

        cx = (min_x + max_x) * 0.5
        cy = (min_y + max_y) * 0.5
        rad = math.radians(stairs.direction_deg)
        arrow_len = max(0.4, min(w, h) * 0.35)
        p0 = local_m_to_scene(Point2D(cx, cy))
        p1 = local_m_to_scene(
            Point2D(cx + arrow_len * math.cos(rad), cy + arrow_len * math.sin(rad))
        )
        arr = QGraphicsLineItem(p0.x(), p0.y(), p1.x(), p1.y())
        arr.setPen(QPen(QColor("#E9D5FF"), 2.0))
        arr.setZValue(Z_STAIRS + 1.0)
        self._scene.addItem(arr)

    def _clear_handle_items(self) -> None:
        for h in self._corner_handles:
            if h.scene() is self._scene:
                self._scene.removeItem(h)
        for m in self._midpoint_handles:
            if m.scene() is self._scene:
                self._scene.removeItem(m)
        self._corner_handles.clear()
        self._midpoint_handles.clear()

    def _rebuild_handles(self) -> None:
        self._clear_handle_items()
        floor = self.current_floor()
        if floor is None:
            return

        if self._tool == ToolMode.EDIT_OUTLINE and len(floor.outer_polygon_m) >= 3:
            self._add_polygon_handles("outline", floor.id, floor.outer_polygon_m)
        elif self._tool == ToolMode.SELECT and self._selected_kind == "room":
            room = next((r for r in floor.rooms if r.id == self._selected_id), None)
            if room is not None and len(room.polygon_m) >= 3:
                self._add_polygon_handles("room", room.id, room.polygon_m)

    def _add_polygon_handles(self, kind: str, entity_id: str, pts_m: list[Point2D]) -> None:
        n = len(pts_m)
        for i, pt in enumerate(pts_m):
            ch = _CornerHandle(kind, entity_id, i, pt)
            self._scene.addItem(ch)
            self._corner_handles.append(ch)

            nxt = pts_m[(i + 1) % n]
            mid = Point2D((pt.x + nxt.x) * 0.5, (pt.y + nxt.y) * 0.5)
            mh = _MidpointHandle(kind, entity_id, i + 1, mid)
            self._scene.addItem(mh)
            self._midpoint_handles.append(mh)

    def _clear_draft_items(self) -> None:
        for item in self._draft_items:
            if item.scene() is self._scene:
                self._scene.removeItem(item)
        self._draft_items.clear()

    def _rebuild_draft_overlays(self) -> None:
        self._clear_draft_items()

        # Snap guide line
        if self._last_snap_guide is not None:
            a, b = self._last_snap_guide
            sa = local_m_to_scene(a)
            sb = local_m_to_scene(b)
            guide = QGraphicsLineItem(sa.x(), sa.y(), sb.x(), sb.y())
            guide.setZValue(Z_SNAP_GUIDE)
            guide.setPen(QPen(QColor("#38BDF8"), 1.0, Qt.PenStyle.DashLine))
            self._scene.addItem(guide)
            self._draft_items.append(guide)

        # Draft Room Polygon
        if self._tool == ToolMode.DRAW_ROOM and self._draft_room_pts:
            path = QPainterPath()
            p0 = local_m_to_scene(self._draft_room_pts[0])
            path.moveTo(p0)
            for pt in self._draft_room_pts[1:]:
                path.lineTo(local_m_to_scene(pt))
            path.lineTo(local_m_to_scene(self._last_cursor_m))

            draft_path = QGraphicsPathItem(path)
            draft_path.setZValue(Z_DRAFT)
            draft_path.setPen(QPen(QColor("#22C55E"), 2.0, Qt.PenStyle.DashLine))
            self._scene.addItem(draft_path)
            self._draft_items.append(draft_path)

            # Start-vertex glowing ring (12 px close target)
            ring = QGraphicsEllipseItem(-10.0, -10.0, 20.0, 20.0)
            ring.setPos(p0)
            ring.setZValue(Z_DRAFT + 1.0)
            ring.setBrush(QBrush(QColor(34, 197, 94, 80)))
            ring.setPen(QPen(QColor("#22C55E"), 2.0))
            self._scene.addItem(ring)
            self._draft_items.append(ring)

        # Draft Stairs
        if (
            self._tool == ToolMode.DRAW_STAIRS
            and self._stairs_start_m is not None
            and self._stairs_current_m is not None
        ):
            a = self._stairs_start_m
            b = self._stairs_current_m
            rect_pts = [
                Point2D(min(a.x, b.x), min(a.y, b.y)),
                Point2D(max(a.x, b.x), min(a.y, b.y)),
                Point2D(max(a.x, b.x), max(a.y, b.y)),
                Point2D(min(a.x, b.x), max(a.y, b.y)),
            ]
            poly = QGraphicsPolygonItem(_poly_to_qpolygon(rect_pts))
            poly.setZValue(Z_DRAFT)
            poly.setBrush(QBrush(QColor(168, 85, 247, 70)))
            poly.setPen(QPen(QColor("#A855F7"), 2.0, Qt.PenStyle.DashLine))
            self._scene.addItem(poly)
            self._draft_items.append(poly)

        # Door placement preview
        if self._tool == ToolMode.PLACE_DOOR:
            pos_m = self._door_preview_hit_pos or self._last_cursor_m
            pos_s = local_m_to_scene(pos_m)
            if self._door_preview_valid and self._door_preview_hit_pos is not None:
                preview_door = Door(position_m=pos_m, angle_deg=self._door_preview_angle)
                d_prev = QGraphicsPathItem(_door_graphics_path(preview_door))
                d_prev.setPos(pos_s)
                d_prev.setRotation(-self._door_preview_angle)
                d_prev.setZValue(Z_DRAFT)
                d_prev.setPen(QPen(QColor("#22C55E"), 2.5))
                self._scene.addItem(d_prev)
                self._draft_items.append(d_prev)
            else:
                no_entry = QGraphicsEllipseItem(-10.0, -10.0, 20.0, 20.0)
                no_entry.setPos(pos_s)
                no_entry.setZValue(Z_DRAFT)
                no_entry.setBrush(QBrush(QColor(239, 68, 68, 70)))
                no_entry.setPen(QPen(QColor("#EF4444"), 2.0))
                self._scene.addItem(no_entry)
                self._draft_items.append(no_entry)

        # Blueprint calibration points
        if self._tool == ToolMode.CALIBRATE_BLUEPRINT and self._calib_pts_m:
            for pt in self._calib_pts_m:
                mk = QGraphicsEllipseItem(-6.0, -6.0, 12.0, 12.0)
                mk.setPos(local_m_to_scene(pt))
                mk.setZValue(Z_DRAFT)
                mk.setBrush(QBrush(QColor("#F59E0B")))
                mk.setPen(QPen(QColor("#FFFFFF"), 1.5))
                self._scene.addItem(mk)
                self._draft_items.append(mk)

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        super().drawBackground(painter, rect)
        painter.fillRect(rect, QColor("#0B1120"))
        step = PIXELS_PER_METER  # 1.0 m major grid
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

        # 1. Top-Right True North Compass (exact Section 4.2C & 4.6B)
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

        # 2. Bottom-Left Dynamic Metric Scale Bar (1 / 2 / 5 / 10 / 20 m)
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

        # 3. Typed Metric Wall Length HUD Pill (Section 4.6C)
        hud_text = self.typed_length_hud_text()
        if hud_text:
            painter.setBrush(QBrush(QColor(2, 132, 199, 235)))
            painter.setPen(QPen(QColor("#38BDF8"), 1.5))
            pill_rect = QRectF(16.0, 14.0, 240.0, 28.0)
            painter.drawRoundedRect(pill_rect, 6.0, 6.0)
            painter.setPen(QPen(QColor("#FFFFFF"), 1.0))
            painter.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
            painter.drawText(pill_rect, Qt.AlignmentFlag.AlignCenter, hud_text)

        painter.restore()

    # ------------------------------------------------------------------
    # Helpers: Snapping, Room Commit, Typed Length, Calibration
    # ------------------------------------------------------------------

    def _clear_drafts(self) -> None:
        self._draft_room_pts.clear()
        self._typed_length_buf = ""
        self._stairs_start_m = None
        self._stairs_current_m = None
        self._calib_pts_m.clear()
        self._last_snap_guide = None
        self._dragging_handle = None
        self._dragging_door_id = None
        self._clear_draft_items()

    def _undo_last_draft_vertex(self) -> None:
        if self._draft_room_pts:
            self._draft_room_pts.pop()
            self._rebuild_draft_overlays()
            self.viewport().update()

    def _view_to_local_m(self, view_pos: QPointF) -> Point2D:
        return scene_to_local_m(self.mapToScene(view_pos.toPoint()))

    def _snap_cursor(
        self,
        raw_m: Point2D,
        *,
        anchor_pt_m: Point2D | None = None,
        shift_lock: bool = False,
        exclude_room_id: str | None = None,
    ) -> Point2D:
        floor = self.current_floor()
        if floor is None:
            return raw_m
        snap = snap_editor_point(
            raw_m,
            floor,
            view_rotation_deg=self._view_rotation_deg,
            anchor_pt_m=anchor_pt_m,
            shift_lock=shift_lock,
            exclude_room_id=exclude_room_id,
        )
        self._last_snap_kind = snap.kind
        self._last_snap_guide = snap.guide_points
        return snap.point_m

    def _next_room_name(self, floor: Floor) -> str:
        existing = {r.name.lower() for r in floor.rooms}
        idx = len(floor.rooms) + 1
        while f"Room {idx}".lower() in existing:
            idx += 1
        return f"Room {idx}"

    def _commit_draft_room(self) -> Room | None:
        floor = self.current_floor()
        if floor is None or self._building_id is None or len(self._draft_room_pts) < 3:
            self._clear_drafts()
            return None

        raw_pts = list(self._draft_room_pts)
        self._draft_room_pts.clear()
        self._typed_length_buf = ""
        self._last_snap_guide = None

        if self._clip_to_outer or self._auto_cut_rooms:
            final_pts = clip_and_cut_room_polygon(
                raw_pts,
                floor,
                clip_to_outer=self._clip_to_outer,
                cut_existing_rooms=self._auto_cut_rooms,
            )
            if final_pts is None:
                self.status_hint.emit("Room discarded (area < 0.25 m² after clipping/cutting).")
                self.rebuild_scene()
                return None
        else:
            if polygon_area_m2(raw_pts) < 0.25:
                self.status_hint.emit("Room discarded (area < 0.25 m²).")
                self.rebuild_scene()
                return None
            final_pts = raw_pts

        room = Room(name=self._next_room_name(floor), polygon_m=final_pts)
        # Reconcile doors against prospective walls
        temp_floor = copy.deepcopy(floor)
        temp_floor.rooms.append(copy.deepcopy(room))
        reconcile_doors_after_wall_change(temp_floor, max_dist_m=0.5)

        self._state.add_room(
            self._building_id,
            floor.id,
            room,
            reconciled_doors=temp_floor.doors,
        )
        self._selected_kind = "room"
        self._selected_id = room.id
        self._post_finish_guard_until = time.monotonic() + 0.25
        self.set_tool(ToolMode.SELECT)
        self.rebuild_scene()
        self.selection_entity_changed.emit("room", room.id)
        return room

    def _place_typed_length_vertex(self, length_m: float, shift_lock: bool = False) -> None:
        if not self._draft_room_pts or length_m <= 0.0:
            return
        anchor = self._draft_room_pts[-1]
        dx = self._last_cursor_m.x - anchor.x
        dy = self._last_cursor_m.y - anchor.y
        if math.hypot(dx, dy) < 1e-6:
            rad = math.radians(self._view_rotation_deg)
            dx, dy = math.cos(rad), math.sin(rad)
        angle = math.atan2(dy, dx)
        if shift_lock:
            rot_rad = math.radians(self._view_rotation_deg)
            rel = angle - rot_rad
            angle = round(rel / (math.pi / 4.0)) * (math.pi / 4.0) + rot_rad
        new_pt = Point2D(
            anchor.x + length_m * math.cos(angle),
            anchor.y + length_m * math.sin(angle),
        )
        self._draft_room_pts.append(new_pt)
        self._last_cursor_m = new_pt
        self._typed_length_buf = ""
        self._rebuild_draft_overlays()
        self.viewport().update()

    def _finish_blueprint_calibration(self) -> None:
        floor = self.current_floor()
        if (
            floor is None
            or self._building_id is None
            or floor.blueprint is None
            or len(self._calib_pts_m) < 2
        ):
            self._calib_pts_m.clear()
            return
        p1, p2 = self._calib_pts_m[0], self._calib_pts_m[1]
        self._calib_pts_m.clear()
        cur_dist_m = math.hypot(p2.x - p1.x, p2.y - p1.y)
        if cur_dist_m < 1e-4:
            self.status_hint.emit("Calibration points too close; select two distinct points.")
            self._rebuild_draft_overlays()
            return
        prev_mpp = max(1e-6, floor.blueprint.meters_per_pixel)
        image_px_dist = cur_dist_m / prev_mpp

        if self._calibration_prompt is not None:
            real_m = self._calibration_prompt("Enter real-world distance in meters:")
        else:
            val, ok = QInputDialog.getDouble(
                self,
                "Calibrate Blueprint Scale",
                "Real-world distance between the two points (meters):",
                cur_dist_m,
                0.05,
                500.0,
                3,
            )
            real_m = float(val) if ok else None

        if real_m is None or real_m <= 0.0:
            self._rebuild_draft_overlays()
            return

        updated_bp = copy.deepcopy(floor.blueprint)
        updated_bp.meters_per_pixel = float(real_m) / image_px_dist
        self._state.set_floor_blueprint(self._building_id, floor.id, updated_bp)
        self.set_tool(ToolMode.SELECT)
        self.status_hint.emit(
            f"Blueprint calibrated: {updated_bp.meters_per_pixel:.5f} m/px"
        )

    def _hit_test_handle(self, scene_pos: QPointF) -> _CornerHandle | _MidpointHandle | None:
        tol_scene = 10.0 / max(self._zoom, 0.05)
        for ch in self._corner_handles:
            if math.hypot(scene_pos.x() - ch.pos().x(), scene_pos.y() - ch.pos().y()) <= tol_scene:
                return ch
        for mh in self._midpoint_handles:
            if math.hypot(scene_pos.x() - mh.pos().x(), scene_pos.y() - mh.pos().y()) <= tol_scene:
                return mh
        return None

    def _hit_test_selectable_entity(self, pt_m: Point2D) -> tuple[str, str] | None:
        floor = self.current_floor()
        if floor is None:
            return None

        # 1. Doors (within 0.6 m)
        for door in reversed(floor.doors):
            if math.hypot(pt_m.x - door.position_m.x, pt_m.y - door.position_m.y) <= max(
                0.45, door.width_m * 0.55
            ):
                return ("door", door.id)

        # 2. Vertical holes
        for hole in reversed(floor.holes):
            if math.hypot(pt_m.x - hole.position_m.x, pt_m.y - hole.position_m.y) <= max(
                0.35, hole.radius_m + 0.15
            ):
                return ("hole", hole.id)

        # 3. Stairs
        from shared.geo_math import point_in_polygon

        for stairs in reversed(floor.stairs):
            if point_in_polygon(pt_m, stairs.polygon_m):
                return ("stairs", stairs.id)

        # 4. Rooms
        for room in reversed(floor.rooms):
            if point_in_polygon(pt_m, room.polygon_m):
                return ("room", room.id)

        return None

    def _find_adjacent_hole_snap(self, pt_m: Point2D, tol_m: float = 0.4) -> VerticalHole | None:
        bldg = self.current_building()
        floor = self.current_floor()
        if bldg is None or floor is None:
            return None
        best_hole: VerticalHole | None = None
        best_dist = tol_m
        for other in bldg.floors:
            if abs(other.level_index - floor.level_index) != 1:
                continue
            for hole in other.holes:
                d = math.hypot(pt_m.x - hole.position_m.x, pt_m.y - hole.position_m.y)
                if d <= best_dist:
                    best_dist = d
                    best_hole = hole
        return best_hole

    # ------------------------------------------------------------------
    # Mouse & Keyboard Events (Strict Non-Panning State Machine)
    # ------------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        # 1. Middle-mouse or Space+Left always pans
        if event.button() == Qt.MouseButton.MiddleButton or (
            self._space_held and event.button() == Qt.MouseButton.LeftButton
        ):
            self._panning_active = True
            if self._space_held:
                self._pan_used_while_space = True
            self._pan_last_pos = event.position().toPoint()
            self.viewport().setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))
            event.accept()
            return

        # 2. Hand tool (H) pans cleanly
        if self._tool == ToolMode.PAN:
            super().mousePressEvent(event)
            return

        if time.monotonic() < self._post_finish_guard_until:
            event.accept()
            return

        floor = self.current_floor()
        if floor is None or self._building_id is None:
            event.accept()
            return

        scene_pos = self.mapToScene(event.position().toPoint())
        raw_m = scene_to_local_m(scene_pos)
        shift_held = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)

        # Right-click handling
        if event.button() == Qt.MouseButton.RightButton:
            if self._tool == ToolMode.DRAW_ROOM and self._draft_room_pts:
                menu = QMenu(self)
                if len(self._draft_room_pts) >= 3:
                    finish_act = menu.addAction("Finish Room")
                    finish_act.triggered.connect(self._commit_draft_room)
                undo_act = menu.addAction("Undo Last Vertex")
                undo_act.triggered.connect(self._undo_last_draft_vertex)
                cancel_act = menu.addAction("Cancel Room Draft")
                cancel_act.triggered.connect(
                    lambda: (self._clear_drafts(), self.set_tool(ToolMode.SELECT))
                )
                menu.exec(self.viewport().mapToGlobal(event.position().toPoint()))
                event.accept()
                return
            handle = self._hit_test_handle(scene_pos)
            if isinstance(handle, _CornerHandle):
                self._delete_handle_vertex(handle)
                event.accept()
                return
            event.accept()
            return

        if event.button() != Qt.MouseButton.LeftButton:
            event.accept()
            return

        # Tool dispatch (DragMode is NoDrag — left-click NEVER pans!)
        if self._tool == ToolMode.DRAW_ROOM:
            # Check start-vertex green ring close (12 px screen radius)
            if len(self._draft_room_pts) >= 3:
                start_view = self.mapFromScene(local_m_to_scene(self._draft_room_pts[0]))
                if (QPointF(start_view) - event.position()).manhattanLength() <= 12.0 or (
                    math.hypot(
                        start_view.x() - event.position().x(),
                        start_view.y() - event.position().y(),
                    )
                    <= 12.0
                ):
                    self._commit_draft_room()
                    event.accept()
                    return

            # Safe double-click deduplication: ignore click < 6 px from previous vertex
            if self._draft_room_pts:
                prev_view = self.mapFromScene(local_m_to_scene(self._draft_room_pts[-1]))
                if (
                    math.hypot(
                        prev_view.x() - event.position().x(),
                        prev_view.y() - event.position().y(),
                    )
                    < 6.0
                ):
                    event.accept()
                    return

            anchor = self._draft_room_pts[-1] if self._draft_room_pts else None
            snapped = self._snap_cursor(raw_m, anchor_pt_m=anchor, shift_lock=shift_held)
            self._draft_room_pts.append(snapped)
            self._last_cursor_m = snapped
            self._typed_length_buf = ""
            self._rebuild_draft_overlays()
            event.accept()
            return

        if self._tool == ToolMode.PLACE_DOOR:
            hit = project_point_to_walls(raw_m, floor, max_dist_m=1.5)
            if hit is None:
                self.status_hint.emit("Door blocked: click near a room wall or floor perimeter.")
                event.accept()
                return
            door = Door(position_m=hit.position_m, angle_deg=hit.angle_deg)
            self._state.add_door(self._building_id, floor.id, door)
            self.select_entity("door", door.id)
            event.accept()
            return

        if self._tool == ToolMode.DRAW_STAIRS:
            snapped = self._snap_cursor(raw_m, shift_lock=shift_held)
            self._stairs_start_m = snapped
            self._stairs_current_m = snapped
            self._rebuild_draft_overlays()
            event.accept()
            return

        if self._tool == ToolMode.PLACE_HOLE:
            adj_match = self._find_adjacent_hole_snap(raw_m, tol_m=0.4)
            if adj_match is not None:
                pos_m = Point2D(adj_match.position_m.x, adj_match.position_m.y)
                riser_group_id = adj_match.riser_group_id
                label = adj_match.label
            else:
                pos_m = self._snap_cursor(raw_m)
                existing_groups = {
                    h.riser_group_id
                    for fl in (self.current_building().floors if self.current_building() else [])
                    for h in fl.holes
                    if h.riser_group_id
                }
                idx = len(existing_groups) + 1
                while f"Riser-{idx}" in existing_groups:
                    idx += 1
                riser_group_id = f"Riser-{idx}"
                label = riser_group_id
            hole = VerticalHole(position_m=pos_m, riser_group_id=riser_group_id, label=label)
            self._state.add_hole(self._building_id, floor.id, hole)
            self.select_entity("hole", hole.id)
            event.accept()
            return

        if self._tool == ToolMode.ALIGN_VIEW_EDGE:
            hit = project_point_to_walls(raw_m, floor, max_dist_m=2.0)
            if hit is not None:
                dx = hit.segment_end.x - hit.segment_start.x
                dy = hit.segment_end.y - hit.segment_start.y
                alpha_deg = math.degrees(math.atan2(-dy, dx))
                target_rot = ((-alpha_deg + 90.0) % 180.0) - 90.0
                self.set_view_rotation(target_rot, commit_to_state=True)
                self.set_tool(ToolMode.SELECT)
                self.status_hint.emit(f"Aligned view to wall ({target_rot:.1f}°)")
            else:
                self.status_hint.emit("Click closer to a room or floor wall to align view.")
            event.accept()
            return

        if self._tool == ToolMode.CALIBRATE_BLUEPRINT:
            if floor.blueprint is None:
                self.status_hint.emit("Load a floor blueprint image before calibrating.")
                event.accept()
                return
            self._calib_pts_m.append(raw_m)
            self._rebuild_draft_overlays()
            if len(self._calib_pts_m) >= 2:
                self._finish_blueprint_calibration()
            event.accept()
            return

        if self._tool in (ToolMode.SELECT, ToolMode.EDIT_OUTLINE):
            handle = self._hit_test_handle(scene_pos)
            if isinstance(handle, _MidpointHandle):
                self._insert_handle_midpoint(handle)
                event.accept()
                return
            if isinstance(handle, _CornerHandle):
                self._dragging_handle = handle
                if handle.kind == "room":
                    rm = next((r for r in floor.rooms if r.id == handle.entity_id), None)
                    self._room_drag_original = copy.deepcopy(rm)
                else:
                    self._outline_drag_original = copy.deepcopy(floor.outer_polygon_m)
                event.accept()
                return

            if self._tool == ToolMode.SELECT:
                hit_ent = self._hit_test_selectable_entity(raw_m)
                if hit_ent is not None:
                    kind, ent_id = hit_ent
                    self.select_entity(kind, ent_id)
                    if kind == "door":
                        door = next((d for d in floor.doors if d.id == ent_id), None)
                        if door is not None:
                            self._dragging_door_id = door.id
                            self._door_drag_original = copy.deepcopy(door)
                else:
                    self.select_entity("", "")
            event.accept()
            return

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

        floor = self.current_floor()
        if floor is None:
            event.accept()
            return

        scene_pos = self.mapToScene(event.position().toPoint())
        raw_m = scene_to_local_m(scene_pos)
        shift_held = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)

        if self._dragging_handle is not None:
            handle = self._dragging_handle
            excl = handle.entity_id if handle.kind == "room" else None
            snapped = self._snap_cursor(raw_m, shift_lock=shift_held, exclude_room_id=excl)
            handle.setPos(local_m_to_scene(snapped))
            if handle.kind == "room":
                room = next((r for r in floor.rooms if r.id == handle.entity_id), None)
                if room is not None and 0 <= handle.index < len(room.polygon_m):
                    room.polygon_m[handle.index] = snapped
                    item = self._room_items.get(room.id)
                    if item is not None:
                        item.setPolygon(_poly_to_qpolygon(room.polygon_m))
            elif handle.kind == "outline":
                if 0 <= handle.index < len(floor.outer_polygon_m):
                    floor.outer_polygon_m[handle.index] = snapped
                    self.rebuild_scene()
            event.accept()
            return

        if self._dragging_door_id is not None:
            hit = project_point_to_walls(raw_m, floor, max_dist_m=6.0)
            if hit is not None:
                door = next((d for d in floor.doors if d.id == self._dragging_door_id), None)
                if door is not None:
                    door.position_m = hit.position_m
                    door.angle_deg = hit.angle_deg
                    d_item = self._door_items.get(door.id)
                    if d_item is not None:
                        d_item.setPos(local_m_to_scene(door.position_m))
                        d_item.setRotation(-door.angle_deg)
            event.accept()
            return

        if self._tool == ToolMode.DRAW_ROOM:
            anchor = self._draft_room_pts[-1] if self._draft_room_pts else None
            self._last_cursor_m = self._snap_cursor(
                raw_m, anchor_pt_m=anchor, shift_lock=shift_held
            )
            self._rebuild_draft_overlays()
            event.accept()
            return

        if self._tool == ToolMode.DRAW_STAIRS and self._stairs_start_m is not None:
            self._stairs_current_m = self._snap_cursor(raw_m, shift_lock=shift_held)
            self._rebuild_draft_overlays()
            event.accept()
            return

        if self._tool == ToolMode.PLACE_DOOR:
            hit = project_point_to_walls(raw_m, floor, max_dist_m=1.5)
            self._last_cursor_m = raw_m
            if hit is not None:
                self._door_preview_valid = True
                self._door_preview_hit_pos = hit.position_m
                self._door_preview_angle = hit.angle_deg
            else:
                self._door_preview_valid = False
                self._door_preview_hit_pos = None
            self._rebuild_draft_overlays()
            event.accept()
            return

        self._last_cursor_m = raw_m
        event.accept()

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

        if self._tool == ToolMode.PAN:
            super().mouseReleaseEvent(event)
            return

        if self._ignore_next_release:
            self._ignore_next_release = False
            event.accept()
            return

        if time.monotonic() < self._post_finish_guard_until:
            event.accept()
            return

        floor = self.current_floor()
        if floor is None or self._building_id is None:
            event.accept()
            return

        # Commit Corner Handle drag
        if self._dragging_handle is not None:
            handle = self._dragging_handle
            self._dragging_handle = None
            if handle.kind == "room" and self._room_drag_original is not None:
                orig_room = self._room_drag_original
                self._room_drag_original = None
                edited = next((r for r in floor.rooms if r.id == orig_room.id), None)
                if edited is not None:
                    edited_pts = list(edited.polygon_m)
                    # Restore original briefly so QUndoCommand records clean old -> new transition
                    edited.polygon_m = list(orig_room.polygon_m)
                    if self._clip_to_outer or self._auto_cut_rooms:
                        final_pts = clip_and_cut_room_polygon(
                            edited_pts,
                            floor,
                            exclude_room_id=orig_room.id,
                            clip_to_outer=self._clip_to_outer,
                            cut_existing_rooms=self._auto_cut_rooms,
                        )
                    else:
                        final_pts = edited_pts if polygon_area_m2(edited_pts) >= 0.25 else None

                    if final_pts is not None:
                        new_room = copy.deepcopy(orig_room)
                        new_room.polygon_m = final_pts
                        temp_floor = copy.deepcopy(floor)
                        for idx, r in enumerate(temp_floor.rooms):
                            if r.id == new_room.id:
                                temp_floor.rooms[idx] = copy.deepcopy(new_room)
                                break
                        reconcile_doors_after_wall_change(temp_floor, max_dist_m=0.5)
                        self._state.update_room(
                            self._building_id,
                            floor.id,
                            new_room.id,
                            new_room,
                            reconciled_doors=temp_floor.doors,
                            text="Reshape Room",
                        )
                self.rebuild_scene()
            elif handle.kind == "outline" and self._outline_drag_original is not None:
                orig_poly = self._outline_drag_original
                self._outline_drag_original = None
                new_poly = list(floor.outer_polygon_m)
                floor.outer_polygon_m = list(orig_poly)
                if polygon_area_m2(new_poly) >= 0.5:
                    temp_floor = copy.deepcopy(floor)
                    temp_floor.outer_polygon_m = list(new_poly)
                    reconcile_doors_after_wall_change(temp_floor, max_dist_m=0.5)
                    self._state.edit_floor_outline(
                        self._building_id,
                        floor.id,
                        new_poly,
                        reconciled_doors=temp_floor.doors,
                    )
                self.rebuild_scene()
            event.accept()
            return

        # Commit Door drag (strictly wall-locked)
        if self._dragging_door_id is not None:
            door_id = self._dragging_door_id
            orig_door = self._door_drag_original
            self._dragging_door_id = None
            self._door_drag_original = None
            cur_door = next((d for d in floor.doors if d.id == door_id), None)
            if cur_door is not None and orig_door is not None:
                updated_door = copy.deepcopy(cur_door)
                cur_door.position_m = copy.deepcopy(orig_door.position_m)
                cur_door.angle_deg = orig_door.angle_deg
                self._state.update_door(
                    self._building_id, floor.id, door_id, updated_door, text="Move Door"
                )
            event.accept()
            return

        # Commit Stairs drag
        if self._tool == ToolMode.DRAW_STAIRS and self._stairs_start_m is not None:
            a = self._stairs_start_m
            b = self._stairs_current_m or a
            self._stairs_start_m = None
            self._stairs_current_m = None
            w = abs(b.x - a.x)
            h = abs(b.y - a.y)
            if w >= 0.4 and h >= 0.4:
                poly = [
                    Point2D(min(a.x, b.x), min(a.y, b.y)),
                    Point2D(max(a.x, b.x), min(a.y, b.y)),
                    Point2D(max(a.x, b.x), max(a.y, b.y)),
                    Point2D(min(a.x, b.x), max(a.y, b.y)),
                ]
                direction = math.degrees(math.atan2(b.y - a.y, b.x - a.x)) % 360.0
                stairs = Stairs(polygon_m=poly, direction_deg=direction)
                self._state.add_stairs(self._building_id, floor.id, stairs)
                self.select_entity("stairs", stairs.id)
            self._rebuild_draft_overlays()
            event.accept()
            return

        event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if self._tool == ToolMode.DRAW_ROOM and event.button() == Qt.MouseButton.LeftButton:
            event.accept()
            # Strip trailing vertex < 6.0 px from predecessor
            if len(self._draft_room_pts) >= 2:
                last_view = self.mapFromScene(local_m_to_scene(self._draft_room_pts[-1]))
                prev_view = self.mapFromScene(local_m_to_scene(self._draft_room_pts[-2]))
                if (
                    math.hypot(last_view.x() - prev_view.x(), last_view.y() - prev_view.y())
                    < 6.0
                ):
                    self._draft_room_pts.pop()
            if len(self._draft_room_pts) >= 3:
                self._commit_draft_room()
            self._ignore_next_release = True
            self._post_finish_guard_until = time.monotonic() + 0.25
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
            self._pan_used_while_space = False
            self.viewport().setCursor(QCursor(Qt.CursorShape.OpenHandCursor))
            event.accept()
            return

        if key == Qt.Key.Key_Escape:
            self._clear_drafts()
            self.set_tool(ToolMode.SELECT)
            self.rebuild_scene()
            event.accept()
            return

        if self._tool == ToolMode.DRAW_ROOM and self._draft_room_pts:
            txt = event.text()
            if txt and (txt.isdigit() or (txt == "." and "." not in self._typed_length_buf)):
                self._typed_length_buf += txt
                self.viewport().update()
                event.accept()
                return
            if key == Qt.Key.Key_Backspace:
                if self._typed_length_buf:
                    self._typed_length_buf = self._typed_length_buf[:-1]
                else:
                    self._draft_room_pts.pop()
                    self._rebuild_draft_overlays()
                self.viewport().update()
                event.accept()
                return
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if self._typed_length_buf:
                    try:
                        val = float(self._typed_length_buf)
                    except ValueError:
                        val = 0.0
                    shift_held = bool(mods & Qt.KeyboardModifier.ShiftModifier)
                    self._place_typed_length_vertex(val, shift_lock=shift_held)
                elif len(self._draft_room_pts) >= 3:
                    self._commit_draft_room()
                event.accept()
                return

        # Tool shortcuts
        if key == Qt.Key.Key_O and (mods & Qt.KeyboardModifier.ShiftModifier):
            self.set_onion_skin(not self._onion_skin_enabled)
            event.accept()
            return

        shortcut_map = {
            Qt.Key.Key_V: ToolMode.SELECT,
            Qt.Key.Key_E: ToolMode.EDIT_OUTLINE,
            Qt.Key.Key_R: ToolMode.DRAW_ROOM,
            Qt.Key.Key_D: ToolMode.PLACE_DOOR,
            Qt.Key.Key_S: ToolMode.DRAW_STAIRS,
            Qt.Key.Key_O: ToolMode.PLACE_HOLE,
            Qt.Key.Key_A: ToolMode.ALIGN_VIEW_EDGE,
            Qt.Key.Key_B: ToolMode.CALIBRATE_BLUEPRINT,
            Qt.Key.Key_H: ToolMode.PAN,
        }
        if key in shortcut_map and mods == Qt.KeyboardModifier.NoModifier:
            self.set_tool(shortcut_map[key])
            event.accept()
            return

        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self._delete_selected_entity()
            event.accept()
            return

        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            was_pan = self._pan_used_while_space
            self._space_held = False
            self._pan_used_while_space = False
            self.viewport().setCursor(
                QCursor(_TOOL_CURSORS.get(self._tool, Qt.CursorShape.ArrowCursor))
            )
            if not was_pan and self._selected_kind == "door" and self._selected_id:
                self.flip_selected_door_swing()
            event.accept()
            return
        super().keyReleaseEvent(event)

    # ------------------------------------------------------------------
    # Handle & Entity Mutations
    # ------------------------------------------------------------------

    def _insert_handle_midpoint(self, mh: _MidpointHandle) -> None:
        floor = self.current_floor()
        if floor is None or self._building_id is None:
            return
        if mh.kind == "room":
            room = next((r for r in floor.rooms if r.id == mh.entity_id), None)
            if room is None:
                return
            updated = copy.deepcopy(room)
            pos_m = scene_to_local_m(mh.pos())
            updated.polygon_m.insert(mh.insert_index, pos_m)
            self._state.update_room(
                self._building_id, floor.id, room.id, updated, text="Insert Room Vertex"
            )
        else:
            new_poly = list(floor.outer_polygon_m)
            new_poly.insert(mh.insert_index, scene_to_local_m(mh.pos()))
            self._state.edit_floor_outline(self._building_id, floor.id, new_poly)

    def _delete_handle_vertex(self, ch: _CornerHandle) -> None:
        floor = self.current_floor()
        if floor is None or self._building_id is None:
            return
        if ch.kind == "room":
            room = next((r for r in floor.rooms if r.id == ch.entity_id), None)
            if room is None or len(room.polygon_m) <= 3:
                return
            updated = copy.deepcopy(room)
            updated.polygon_m.pop(ch.index)
            self._state.update_room(
                self._building_id, floor.id, room.id, updated, text="Delete Room Vertex"
            )
        else:
            if len(floor.outer_polygon_m) <= 3:
                return
            new_poly = list(floor.outer_polygon_m)
            new_poly.pop(ch.index)
            self._state.edit_floor_outline(self._building_id, floor.id, new_poly)

    def _delete_selected_entity(self) -> None:
        floor = self.current_floor()
        if floor is None or self._building_id is None or not self._selected_id:
            return
        if self._selected_kind == "room":
            temp_floor = copy.deepcopy(floor)
            temp_floor.rooms = [r for r in temp_floor.rooms if r.id != self._selected_id]
            reconcile_doors_after_wall_change(temp_floor, max_dist_m=0.5)
            self._state.delete_room(
                self._building_id,
                floor.id,
                self._selected_id,
                reconciled_doors=temp_floor.doors,
            )
        elif self._selected_kind == "door":
            self._state.delete_door(self._building_id, floor.id, self._selected_id)
        elif self._selected_kind == "stairs":
            self._state.delete_stairs(self._building_id, floor.id, self._selected_id)
        elif self._selected_kind == "hole":
            self._state.delete_hole(self._building_id, floor.id, self._selected_id)
        self._selected_kind = ""
        self._selected_id = ""
        self.selection_entity_changed.emit("", "")

    def flip_selected_door_swing(self) -> None:
        floor = self.current_floor()
        if floor is None or self._building_id is None or self._selected_kind != "door":
            return
        door = next((d for d in floor.doors if d.id == self._selected_id), None)
        if door is None:
            return
        updated = copy.deepcopy(door)
        updated.swing_flip = not door.swing_flip
        self._state.update_door(
            self._building_id, floor.id, door.id, updated, text="Flip Door Swing"
        )

    def flip_selected_door_hinge(self) -> None:
        floor = self.current_floor()
        if floor is None or self._building_id is None or self._selected_kind != "door":
            return
        door = next((d for d in floor.doors if d.id == self._selected_id), None)
        if door is None:
            return
        updated = copy.deepcopy(door)
        updated.hinge_flip = not door.hinge_flip
        self._state.update_door(
            self._building_id, floor.id, door.id, updated, text="Flip Door Hinge"
        )


class FloorEditorWidget(QWidget):
    """Complete Tab 2 Precision Multi-Floor CAD Editor with Toolbar & Property Inspector."""

    def __init__(
        self,
        state: ProjectState,
        *,
        calibration_prompt: Callable[[str], float | None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._state = state
        self._syncing_inspector = False
        self.view = FloorEditorView(state, calibration_prompt=calibration_prompt, parent=self)
        self._tool_buttons: dict[ToolMode, QPushButton] = {}
        self._build_ui()
        self._wire_signals()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # --- CAD Tool Toolbar ---
        self.tool_bar = QToolBar("Floor CAD Tools", self)
        self.tool_bar.setMovable(False)

        tools = [
            (ToolMode.SELECT, "Select (V)"),
            (ToolMode.EDIT_OUTLINE, "Outline (E)"),
            (ToolMode.DRAW_ROOM, "Room (R)"),
            (ToolMode.PLACE_DOOR, "Door (D)"),
            (ToolMode.DRAW_STAIRS, "Stairs (S)"),
            (ToolMode.PLACE_HOLE, "Riser (O)"),
            (ToolMode.ALIGN_VIEW_EDGE, "Align Edge (A)"),
            (ToolMode.CALIBRATE_BLUEPRINT, "Calibrate BP (B)"),
            (ToolMode.PAN, "Pan (H)"),
        ]
        for mode, label in tools:
            btn = QPushButton(label, self)
            btn.setCheckable(True)
            btn.clicked.connect(lambda _=False, m=mode: self.view.set_tool(m))
            self._tool_buttons[mode] = btn
            self.tool_bar.addWidget(btn)
        self._tool_buttons[ToolMode.SELECT].setChecked(True)

        self.tool_bar.addSeparator()

        self.auto_cut_chk = QCheckBox("Auto-Cut Overlapping Walls", self)
        self.auto_cut_chk.setChecked(True)
        self.auto_cut_chk.toggled.connect(self.view.set_auto_cut_rooms)
        self.tool_bar.addWidget(self.auto_cut_chk)

        self.clip_outer_chk = QCheckBox("Clip to Floor Outline", self)
        self.clip_outer_chk.setChecked(True)
        self.clip_outer_chk.toggled.connect(self.view.set_clip_to_outer)
        self.tool_bar.addWidget(self.clip_outer_chk)

        self.onion_chk = QCheckBox("Onion-Skin (Shift+O)", self)
        self.onion_chk.setChecked(False)
        self.onion_chk.toggled.connect(self.view.set_onion_skin)
        self.tool_bar.addWidget(self.onion_chk)

        root.addWidget(self.tool_bar)

        # --- View-Rotation & Blueprint Toolbar ---
        self.view_bar = QToolBar("Floor View & Blueprint", self)
        self.view_bar.setMovable(False)

        self.rot_label = QLabel("View Rot:", self)
        self.view_bar.addWidget(self.rot_label)

        for delta, lbl in ((-90.0, "-90°"), (-15.0, "-15°"), (15.0, "+15°"), (90.0, "+90°")):
            b = QPushButton(lbl, self)
            b.clicked.connect(
                lambda _=False, d=delta: self.view.set_view_rotation(
                    self.view.view_rotation_deg + d
                )
            )
            self.view_bar.addWidget(b)

        self.view_rot_spin = QDoubleSpinBox(self)
        self.view_rot_spin.setRange(-180.0, 180.0)
        self.view_rot_spin.setDecimals(1)
        self.view_rot_spin.setSuffix("°")
        self.view_rot_spin.valueChanged.connect(self._on_rot_spin_changed)
        self.view_bar.addWidget(self.view_rot_spin)

        reset_rot_btn = QPushButton("Reset 0°", self)
        reset_rot_btn.clicked.connect(lambda: self.view.set_view_rotation(0.0))
        self.view_bar.addWidget(reset_rot_btn)

        fit_btn = QPushButton("Fit View", self)
        fit_btn.clicked.connect(self.view.fit_floor_in_view)
        self.view_bar.addWidget(fit_btn)

        self.view_bar.addSeparator()

        self.load_bp_btn = QPushButton("Load Blueprint...", self)
        self.load_bp_btn.clicked.connect(self._on_load_blueprint)
        self.view_bar.addWidget(self.load_bp_btn)

        self.bp_visible_chk = QCheckBox("Show BP", self)
        self.bp_visible_chk.setChecked(True)
        self.bp_visible_chk.toggled.connect(self._on_blueprint_props_changed)
        self.view_bar.addWidget(self.bp_visible_chk)

        self.bp_opacity_slider = QSlider(Qt.Orientation.Horizontal, self)
        self.bp_opacity_slider.setRange(5, 100)
        self.bp_opacity_slider.setValue(45)
        self.bp_opacity_slider.setFixedWidth(90)
        self.bp_opacity_slider.valueChanged.connect(self._on_blueprint_props_changed)
        self.view_bar.addWidget(self.bp_opacity_slider)

        root.addWidget(self.view_bar)

        # --- Main Splitter: Canvas | Property Inspector ---
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(self.view)

        self.inspector_panel = QWidget(splitter)
        self.inspector_panel.setMinimumWidth(230)
        self.inspector_panel.setMaximumWidth(300)
        insp_layout = QVBoxLayout(self.inspector_panel)
        insp_layout.setContentsMargins(8, 8, 8, 8)
        insp_layout.setSpacing(8)

        insp_title = QLabel("Floor Element Inspector", self.inspector_panel)
        insp_title.setStyleSheet("font-weight: 700; color: #F8FAFC;")
        insp_layout.addWidget(insp_title)

        self.insp_stack = QStackedWidget(self.inspector_panel)

        # Page 0: Empty
        empty_page = QLabel("Select a Room, Door, Stairs, or Riser Hole to edit.", self)
        empty_page.setWordWrap(True)
        empty_page.setStyleSheet("color: #94A3B8;")
        self.insp_stack.addWidget(empty_page)

        # Page 1: Room Inspector
        room_box = QGroupBox("Room Properties", self)
        rf = QFormLayout(room_box)
        self.room_name_edit = QLineEdit(room_box)
        self.room_name_edit.editingFinished.connect(self._on_room_props_committed)
        rf.addRow("Name:", self.room_name_edit)
        self.room_atten_spin = QDoubleSpinBox(room_box)
        self.room_atten_spin.setRange(0.0, 30.0)
        self.room_atten_spin.setSingleStep(1.0)
        self.room_atten_spin.setSuffix(" dB")
        self.room_atten_spin.editingFinished.connect(self._on_room_props_committed)
        rf.addRow("Wall Loss:", self.room_atten_spin)
        self.room_area_lbl = QLabel("—", room_box)
        rf.addRow("Area:", self.room_area_lbl)
        del_room_btn = QPushButton("Delete Room", room_box)
        del_room_btn.clicked.connect(self.view._delete_selected_entity)
        rf.addRow(del_room_btn)
        self.insp_stack.addWidget(room_box)

        # Page 2: Door Inspector
        door_box = QGroupBox("Wall-Locked Door", self)
        df = QFormLayout(door_box)
        self.door_width_spin = QDoubleSpinBox(door_box)
        self.door_width_spin.setRange(0.60, 3.00)
        self.door_width_spin.setSingleStep(0.05)
        self.door_width_spin.setSuffix(" m")
        self.door_width_spin.valueChanged.connect(self._on_door_width_changed)
        df.addRow("Width:", self.door_width_spin)
        flip_swing_btn = QPushButton("Flip Swing (Space)", door_box)
        flip_swing_btn.clicked.connect(self.view.flip_selected_door_swing)
        df.addRow(flip_swing_btn)
        flip_hinge_btn = QPushButton("Flip Hinge", door_box)
        flip_hinge_btn.clicked.connect(self.view.flip_selected_door_hinge)
        df.addRow(flip_hinge_btn)
        del_door_btn = QPushButton("Delete Door", door_box)
        del_door_btn.clicked.connect(self.view._delete_selected_entity)
        df.addRow(del_door_btn)
        self.insp_stack.addWidget(door_box)

        # Page 3: Hole / Stairs Inspector
        other_box = QGroupBox("Element", self)
        of = QFormLayout(other_box)
        self.other_info_lbl = QLabel("", other_box)
        self.other_info_lbl.setWordWrap(True)
        of.addRow("Details:", self.other_info_lbl)
        del_other_btn = QPushButton("Delete Element", other_box)
        del_other_btn.clicked.connect(self.view._delete_selected_entity)
        of.addRow(del_other_btn)
        self.insp_stack.addWidget(other_box)

        insp_layout.addWidget(self.insp_stack)
        insp_layout.addStretch(1)

        self.status_label = QLabel(
            "Pan: Middle-Drag, Space+Drag, or Hand (H)", self.inspector_panel
        )
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #94A3B8; font-size: 11px;")
        insp_layout.addWidget(self.status_label)

        splitter.addWidget(self.inspector_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        root.addWidget(splitter, 1)

    def _wire_signals(self) -> None:
        self.view.tool_changed.connect(self._on_tool_changed)
        self.view.view_rotation_changed.connect(self._on_view_rotation_updated)
        self.view.selection_entity_changed.connect(self._on_selection_entity_changed)
        self.view.onion_skin_changed.connect(self._on_onion_skin_updated)
        self.view.status_hint.connect(self.status_label.setText)
        self._state.floor_geometry_changed.connect(self._on_state_floor_changed)
        self._state.floor_view_rotated.connect(self._on_state_view_rotated)

    def set_floor(self, building_id: str | None, floor_id: str | None) -> None:
        self.view.set_floor_context(building_id, floor_id)
        self._refresh_blueprint_controls()

    def _on_tool_changed(self, tool_val: str) -> None:
        for mode, btn in self._tool_buttons.items():
            btn.blockSignals(True)
            btn.setChecked(mode.value == tool_val)
            btn.blockSignals(False)

    def _on_onion_skin_updated(self, enabled: bool) -> None:
        self.onion_chk.blockSignals(True)
        self.onion_chk.setChecked(enabled)
        self.onion_chk.blockSignals(False)

    def _on_view_rotation_updated(self, angle: float) -> None:
        self.view_rot_spin.blockSignals(True)
        self.view_rot_spin.setValue(angle)
        self.view_rot_spin.blockSignals(False)

    def _on_rot_spin_changed(self, value: float) -> None:
        self.view.set_view_rotation(value, commit_to_state=True)

    def _on_state_floor_changed(self, building_id: str, floor_id: str) -> None:
        if building_id == self.view._building_id and floor_id == self.view._floor_id:
            self.view.rebuild_scene()
            self._refresh_blueprint_controls()
            self._on_selection_entity_changed(self.view._selected_kind, self.view._selected_id)

    def _on_state_view_rotated(self, building_id: str, floor_id: str, rot: float) -> None:
        if building_id == self.view._building_id and floor_id == self.view._floor_id:
            if abs(self.view.view_rotation_deg - rot) > 1e-6:
                self.view.set_view_rotation(rot, commit_to_state=False)

    def _refresh_blueprint_controls(self) -> None:
        floor = self.view.current_floor()
        bp = floor.blueprint if floor is not None else None
        self._syncing_inspector = True
        try:
            self.bp_visible_chk.setEnabled(bp is not None)
            self.bp_opacity_slider.setEnabled(bp is not None)
            if bp is not None:
                self.bp_visible_chk.setChecked(bp.visible)
                self.bp_opacity_slider.setValue(int(round(bp.opacity * 100)))
        finally:
            self._syncing_inspector = False

    def _on_selection_entity_changed(self, kind: str, entity_id: str) -> None:
        floor = self.view.current_floor()
        self._syncing_inspector = True
        try:
            if floor is None or not entity_id:
                self.insp_stack.setCurrentIndex(0)
                return
            if kind == "room":
                room = next((r for r in floor.rooms if r.id == entity_id), None)
                if room is None:
                    self.insp_stack.setCurrentIndex(0)
                    return
                self.room_name_edit.setText(room.name)
                self.room_atten_spin.setValue(float(room.wall_attenuation_db))
                self.room_area_lbl.setText(f"{polygon_area_m2(room.polygon_m):.2f} m²")
                self.insp_stack.setCurrentIndex(1)
            elif kind == "door":
                door = next((d for d in floor.doors if d.id == entity_id), None)
                if door is None:
                    self.insp_stack.setCurrentIndex(0)
                    return
                self.door_width_spin.setValue(float(door.width_m))
                self.insp_stack.setCurrentIndex(2)
            elif kind in ("hole", "stairs"):
                if kind == "hole":
                    hole = next((h for h in floor.holes if h.id == entity_id), None)
                    self.other_info_lbl.setText(
                        f"Riser: {hole.label}\nGroup: {hole.riser_group_id}"
                        if hole
                        else ""
                    )
                else:
                    st = next((s for s in floor.stairs if s.id == entity_id), None)
                    self.other_info_lbl.setText(
                        f"Stairs: {st.label}\nDir: {st.direction_deg:.0f}°" if st else ""
                    )
                self.insp_stack.setCurrentIndex(3)
            else:
                self.insp_stack.setCurrentIndex(0)
        finally:
            self._syncing_inspector = False

    def _on_room_props_committed(self) -> None:
        if self._syncing_inspector or self.view._selected_kind != "room":
            return
        floor = self.view.current_floor()
        bldg = self.view.current_building()
        if floor is None or bldg is None:
            return
        room = next((r for r in floor.rooms if r.id == self.view._selected_id), None)
        if room is None:
            return
        updated = copy.deepcopy(room)
        updated.name = self.room_name_edit.text().strip() or room.name
        updated.wall_attenuation_db = float(self.room_atten_spin.value())
        self._state.update_room(bldg.id, floor.id, room.id, updated)

    def _on_door_width_changed(self, value: float) -> None:
        if self._syncing_inspector or self.view._selected_kind != "door":
            return
        floor = self.view.current_floor()
        bldg = self.view.current_building()
        if floor is None or bldg is None:
            return
        door = next((d for d in floor.doors if d.id == self._selected_id), None)
        if door is None or abs(door.width_m - value) < 1e-6:
            return
        updated = copy.deepcopy(door)
        updated.width_m = float(value)
        self._state.update_door(bldg.id, floor.id, door.id, updated)

    def _on_load_blueprint(self) -> None:
        floor = self.view.current_floor()
        bldg = self.view.current_building()
        if floor is None or bldg is None:
            return
        path_str, _ = QFileDialog.getOpenFileName(
            self,
            "Select Floor Blueprint Image",
            "",
            "Images (*.png *.jpg *.jpeg)",
        )
        if not path_str:
            return
        with Image.open(path_str) as img:
            buf = io.BytesIO()
            img.convert("RGBA").save(buf, format="PNG")
            png_bytes = buf.getvalue()
        bp = BlueprintUnderlay(
            archive_path=f"blueprints/{floor.id}.png",
            meters_per_pixel=0.02,
            opacity=0.45,
            visible=True,
        )
        self._state.set_floor_blueprint(bldg.id, floor.id, bp, png_bytes)

    def _on_blueprint_props_changed(self) -> None:
        if self._syncing_inspector:
            return
        floor = self.view.current_floor()
        bldg = self.view.current_building()
        if floor is None or bldg is None or floor.blueprint is None:
            return
        updated = copy.deepcopy(floor.blueprint)
        updated.visible = self.bp_visible_chk.isChecked()
        updated.opacity = self.bp_opacity_slider.value() / 100.0
        self._state.set_floor_blueprint(bldg.id, floor.id, updated)
