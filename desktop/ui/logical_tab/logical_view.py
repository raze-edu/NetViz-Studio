"""Tab 1 Logical Network Visualizer: LogicalCanvasView (Bulletproof Port Connect) & LogicalView."""

from __future__ import annotations

import copy
import json
import math

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QShortcut,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsEllipseItem,
    QGraphicsLineItem,
    QGraphicsPathItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSlider,
    QSplitter,
    QStackedWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from desktop.config import (
    DEFAULT_NODE_ALPHA,
    MAX_NODE_ALPHA,
    MIN_NODE_ALPHA,
    AppConfig,
    load_config,
    save_config,
)
from desktop.state import ProjectState
from desktop.ui.dialogs.template_manager import TemplateManagerDialog
from desktop.ui.logical_tab.graphics_items import (
    Z_HANDLE,
    Z_PREVIEW,
    CableBundleItem,
    ComponentNodeItem,
    PortAnchorItem,
    RoutableCableItem,
    WaypointHandle,
)
from desktop.ui.logical_tab.routing_controller import (
    compute_alignment_moves,
    compute_distribution_moves,
    compute_hierarchical_layout,
    compute_orthogonal_waypoints,
)
from desktop.ui.widgets.cable_inspector import CableInspector
from desktop.ui.widgets.component_inspector import ComponentInspector
from desktop.ui.widgets.component_palette import TEMPLATE_MIME_TYPE, ComponentPalette
from desktop.ui.widgets.minimap_widget import MiniMapWidget
from shared.models import (
    Cable,
    CableBundle,
    Component,
    ComponentTemplate,
    Point2D,
    Port,
    new_id,
)
from shared.presets import BUILTIN_TEMPLATES, instantiate_from_template
from shared.validator import are_port_types_compatible


def _point_to_rect_distance(pt: QPointF, rect: QRectF) -> float:
    cx = max(rect.left(), min(pt.x(), rect.right()))
    cy = max(rect.top(), min(pt.y(), rect.bottom()))
    return math.hypot(pt.x() - cx, pt.y() - cy)


def _point_to_segment_dist(pt: QPointF, a: QPointF, b: QPointF) -> tuple[float, QPointF]:
    dx = b.x() - a.x()
    dy = b.y() - a.y()
    len_sq = dx * dx + dy * dy
    if len_sq < 1e-9:
        return (math.hypot(pt.x() - a.x(), pt.y() - a.y()), a)
    t = max(0.0, min(1.0, ((pt.x() - a.x()) * dx + (pt.y() - a.y()) * dy) / len_sq))
    proj = QPointF(a.x() + t * dx, a.y() + t * dy)
    return (math.hypot(pt.x() - proj.x(), pt.y() - proj.y()), proj)


def _increment_component_name(base_name: str, existing_names: set[str]) -> str:
    import re

    match = re.match(r"^(.*?)(\d+)$", base_name.strip())
    if match:
        prefix = match.group(1)
        num_str = match.group(2)
        width = len(num_str)
        num = int(num_str) + 1
        while True:
            cand = f"{prefix}{num:0{width}d}"
            if cand.lower() not in existing_names:
                return cand
            num += 1
    prefix = base_name.strip() or "DEV"
    idx = 2
    while True:
        cand = f"{prefix}-{idx:02d}"
        if cand.lower() not in existing_names:
            return cand
        idx += 1


class LogicalCanvasView(QGraphicsView):
    """
    Logical network canvas implementing Section 4.4B Bulletproof Dual-Mode Port Connecting.

    Panning occurs ONLY via Middle-Mouse drag, Space+Left-Drag, or explicit Hand Tool (H).
    """

    connection_trace_changed = Signal(str)
    media_mismatch_warning = Signal(str)
    node_alpha_changed = Signal(int)
    viewport_resized = Signal()

    def __init__(
        self,
        state: ProjectState,
        *,
        node_alpha: int = DEFAULT_NODE_ALPHA,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._state = state
        self._node_alpha = max(MIN_NODE_ALPHA, min(MAX_NODE_ALPHA, int(node_alpha)))
        self._scene = QGraphicsScene(self)
        self._scene.setSceneRect(-4000.0, -4000.0, 8000.0, 8000.0)
        self.setScene(self._scene)

        # Render & interaction settings — NEVER default ScrollHandDrag (Section 6 #9)!
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.TextAntialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)

        # Item registries
        self._node_items: dict[str, ComponentNodeItem] = {}
        self._port_items: dict[tuple[str, str], PortAnchorItem] = {}
        self._cable_items: dict[str, RoutableCableItem] = {}
        self._bundle_items: dict[str, CableBundleItem] = {}
        self._waypoint_handles: list[WaypointHandle] = []
        self._snap_guides: list[QGraphicsLineItem] = []

        # Tool & pan state
        self._hand_tool_active = False
        self._space_held = False
        self._panning_active = False
        self._pan_last_pos = QPoint()

        # Dual-mode port connection state machine: IDLE | DRAG_CONNECTING | PENDING_CLICK_CONNECT
        self._connect_mode: str = "IDLE"
        self._connect_src_port: PortAnchorItem | None = None
        self._connect_press_screen_pos = QPointF()
        self._snap_target_port: PortAnchorItem | None = None

        # Component drag tracking for single MoveComponentsCommand on release
        self._drag_initial_positions: dict[str, Point2D] = {}

        # Internal copy/paste clipboard
        self._clipboard_components: list[Component] = []

        # Preview graphics (z = 65, STRICTLY excluded from hit-testing!)
        self._preview_wire = QGraphicsPathItem()
        self._preview_wire.setZValue(Z_PREVIEW)
        self._preview_wire.setEnabled(False)
        self._preview_wire.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._preview_wire.hide()
        self._scene.addItem(self._preview_wire)

        self._lock_ring = QGraphicsEllipseItem(-11.0, -11.0, 22.0, 22.0)
        self._lock_ring.setZValue(Z_PREVIEW)
        self._lock_ring.setEnabled(False)
        self._lock_ring.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._lock_ring.hide()
        self._scene.addItem(self._lock_ring)

        self._red_x_path = QGraphicsPathItem()
        self._red_x_path.setZValue(Z_PREVIEW)
        self._red_x_path.setEnabled(False)
        self._red_x_path.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._red_x_path.setPen(QPen(QColor("#EF4444"), 2.4))
        self._red_x_path.hide()
        self._scene.addItem(self._red_x_path)

        # Red flash timer for occupied port click feedback
        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(self._hide_flash_ring)

        # Pulse timer for jump-to-component highlight
        self._pulse_timer = QTimer(self)
        self._pulse_timer.setSingleShot(True)
        self._pulse_timer.timeout.connect(self._clear_node_pulses)

        # Sync signals
        self._scene.selectionChanged.connect(self._on_scene_selection_changed)
        self._state.project_reloaded.connect(self.rebuild_scene)
        self._state.components_changed.connect(self.rebuild_scene)
        self._state.cables_changed.connect(self.rebuild_scene)
        self._state.bundles_changed.connect(self.rebuild_scene)

        self.rebuild_scene()

    # ------------------------------------------------------------------
    # Public Properties & Helpers
    # ------------------------------------------------------------------

    @property
    def connect_mode(self) -> str:
        return self._connect_mode

    @property
    def node_alpha(self) -> int:
        return self._node_alpha

    def set_node_alpha(self, alpha: int) -> None:
        """Update translucent fill alpha on all ComponentNodeItems live."""
        clamped = max(MIN_NODE_ALPHA, min(MAX_NODE_ALPHA, int(alpha)))
        self._node_alpha = clamped
        for node in self._node_items.values():
            node.set_node_alpha(clamped)
        self.node_alpha_changed.emit(clamped)

    def set_hand_tool(self, enabled: bool) -> None:
        self._hand_tool_active = enabled
        if enabled:
            self._cancel_connection()
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
            self.unsetCursor()

    def get_node_item(self, component_id: str) -> ComponentNodeItem | None:
        return self._node_items.get(component_id)

    def get_port_item(self, component_id: str, port_id: str) -> PortAnchorItem | None:
        return self._port_items.get((component_id, port_id))

    def get_cable_item(self, cable_id: str) -> RoutableCableItem | None:
        return self._cable_items.get(cable_id)

    def get_bundle_item(self, bundle_id: str) -> CableBundleItem | None:
        return self._bundle_items.get(bundle_id)

    def jump_to_and_pulse(self, component_id: str) -> None:
        """Center view on component_id, select it, and temporarily pulse its node halo."""
        node = self._node_items.get(component_id)
        if node is None:
            return
        self._clear_node_pulses()
        self._scene.clearSelection()
        node.setSelected(True)
        node.set_pulsed(True)
        self.centerOn(node)
        self._pulse_timer.start(1600)

    def _clear_node_pulses(self) -> None:
        for node in self._node_items.values():
            node.set_pulsed(False)

    def fit_view(self, selected_only: bool = False) -> None:
        items = self._scene.selectedItems() if selected_only else []
        if selected_only and items:
            rect = QRectF()
            for item in items:
                rect = rect.united(item.sceneBoundingRect())
        else:
            rect = QRectF()
            for node in self._node_items.values():
                rect = rect.united(node.sceneBoundingRect())
            for bundle in self._bundle_items.values():
                rect = rect.united(bundle.sceneBoundingRect())
        if rect.isNull() or rect.isEmpty():
            return
        self.fitInView(rect.adjusted(-60.0, -60.0, 60.0, 60.0), Qt.AspectRatioMode.KeepAspectRatio)

    # ------------------------------------------------------------------
    # Scene Rebuild & Live 60fps Cable Updates
    # ------------------------------------------------------------------

    def rebuild_scene(self) -> None:
        """Synchronize all QGraphicsItems from ProjectState."""
        selected_comp_ids = {
            cid for cid, item in self._node_items.items() if item.isSelected()
        }
        selected_cable_ids = {
            cid for cid, item in self._cable_items.items() if item.isSelected()
        }
        selected_bundle_ids = {
            bid for bid, item in self._bundle_items.items() if item.isSelected()
        }

        # Clear managed items while preserving persistent preview overlays
        for item in list(self._scene.items()):
            if item in (self._preview_wire, self._lock_ring, self._red_x_path):
                continue
            if item.parentItem() is None:
                self._scene.removeItem(item)

        self._node_items.clear()
        self._port_items.clear()
        self._cable_items.clear()
        self._bundle_items.clear()
        self._waypoint_handles.clear()
        self._snap_guides.clear()

        if self._connect_mode != "IDLE":
            self._cancel_connection(silent=True)

        project = self._state.project

        # Lookup template accent overrides
        tpl_colors: dict[str, str] = {}
        for tpl in (*BUILTIN_TEMPLATES, *project.custom_templates):
            if tpl.accent_color_hex:
                tpl_colors[tpl.id] = tpl.accent_color_hex

        # Track occupied port IDs per component
        occupied_by_comp: dict[str, set[str]] = {}
        for cab in project.cables:
            occupied_by_comp.setdefault(cab.source_component_id, set()).add(cab.source_port_id)
            occupied_by_comp.setdefault(cab.target_component_id, set()).add(cab.target_port_id)

        # Build ComponentNodeItems + register PortAnchorItems
        for comp in project.components:
            accent = tpl_colors.get(comp.template_id) if comp.template_id else None
            node = ComponentNodeItem(
                comp,
                alpha=self._node_alpha,
                occupied_port_ids=occupied_by_comp.get(comp.id, set()),
                accent_override=accent,
                on_moved_live=self._on_nodes_moved_live,
            )
            if comp.id in selected_comp_ids or comp.id == self._state.selected_component_id:
                node.setSelected(True)
            self._scene.addItem(node)
            self._node_items[comp.id] = node
            for pid, p_item in node.port_items.items():
                self._port_items[(comp.id, pid)] = p_item

        # Build logical CableBundleItems
        bundle_map: dict[str, CableBundle] = {
            b.id: b for b in project.bundles if b.scope == "logical"
        }
        cable_by_id: dict[str, Cable] = {c.id: c for c in project.cables}
        for bundle in bundle_map.values():
            tooltip_lines = [f"Bundle: {bundle.name} ({len(bundle.member_cable_ids)} links)"]
            for cid in bundle.member_cable_ids:
                cab = cable_by_id.get(cid)
                if cab is None:
                    continue
                sc = project.get_component(cab.source_component_id)
                sp = project.get_port(cab.source_component_id, cab.source_port_id)
                dc = project.get_component(cab.target_component_id)
                dp = project.get_port(cab.target_component_id, cab.target_port_id)
                s_lbl = f"{sc.name}[{sp.label}]" if sc and sp else cab.source_component_id
                d_lbl = f"{dc.name}[{dp.label}]" if dc and dp else cab.target_component_id
                vlan_part = f" VLAN {cab.vlan_id}" if cab.vlan_id else ""
                tooltip_lines.append(f"• {s_lbl} ↔ {d_lbl}{vlan_part}")
            b_item = CableBundleItem(bundle, tooltip_text="\n".join(tooltip_lines))
            if bundle.id in selected_bundle_ids:
                b_item.setSelected(True)
            self._scene.addItem(b_item)
            self._bundle_items[bundle.id] = b_item

            # Bundle entry/exit/waypoint handles (z = 70)
            entry_h = WaypointHandle(
                QPointF(bundle.entry_point.x, bundle.entry_point.y),
                kind="bundle_entry",
                owner_id=bundle.id,
                color_hex=bundle.style.color_hex,
                on_moved=self._on_handle_moved_live,
                on_released=self._on_handle_released,
            )
            exit_h = WaypointHandle(
                QPointF(bundle.exit_point.x, bundle.exit_point.y),
                kind="bundle_exit",
                owner_id=bundle.id,
                color_hex=bundle.style.color_hex,
                on_moved=self._on_handle_moved_live,
                on_released=self._on_handle_released,
            )
            self._scene.addItem(entry_h)
            self._scene.addItem(exit_h)
            self._waypoint_handles.extend([entry_h, exit_h])

            for w_idx, wp in enumerate(bundle.waypoints):
                wp_h = WaypointHandle(
                    QPointF(wp.x, wp.y),
                    kind="bundle_wp",
                    owner_id=bundle.id,
                    index=w_idx,
                    color_hex=bundle.style.color_hex,
                    on_moved=self._on_handle_moved_live,
                    on_released=self._on_handle_released,
                    on_delete=self._on_handle_delete,
                )
                self._scene.addItem(wp_h)
                self._waypoint_handles.append(wp_h)

        # Build RoutableCableItems + endpoint port pills (z = 50) + cable waypoint handles (z = 70)
        for cab in project.cables:
            src_port_item = self._port_items.get((cab.source_component_id, cab.source_port_id))
            dst_port_item = self._port_items.get((cab.target_component_id, cab.target_port_id))
            if src_port_item is None or dst_port_item is None:
                continue
            bundle = bundle_map.get(cab.logical_bundle_id) if cab.logical_bundle_id else None
            m_idx = 0
            m_cnt = 1
            if bundle is not None and cab.id in bundle.member_cable_ids:
                m_idx = bundle.member_cable_ids.index(cab.id)
                m_cnt = len(bundle.member_cable_ids)

            c_item = RoutableCableItem(
                cab,
                src_port_item,
                dst_port_item,
                bundle=bundle,
                bundle_member_index=m_idx,
                bundle_member_count=m_cnt,
            )
            if cab.id in selected_cable_ids or cab.id == self._state.selected_cable_id:
                c_item.setSelected(True)
            self._scene.addItem(c_item)
            self._scene.addItem(c_item.src_pill)
            self._scene.addItem(c_item.dst_pill)
            self._cable_items[cab.id] = c_item

            if bundle is None:
                for w_idx, wp in enumerate(cab.logical_waypoints):
                    wp_h = WaypointHandle(
                        QPointF(wp.x, wp.y),
                        kind="cable_wp",
                        owner_id=cab.id,
                        index=w_idx,
                        color_hex=cab.style.color_hex,
                        on_moved=self._on_handle_moved_live,
                        on_released=self._on_handle_released,
                        on_delete=self._on_handle_delete,
                    )
                    self._scene.addItem(wp_h)
                    self._waypoint_handles.append(wp_h)

    def _on_nodes_moved_live(self) -> None:
        """Update all connected cables & alignment snap guides at 60fps during node drag."""
        for cab_item in self._cable_items.values():
            cab_item.rebuild_path()
        self._update_drag_snap_guides()

    def _update_drag_snap_guides(self) -> None:
        for line in self._snap_guides:
            self._scene.removeItem(line)
        self._snap_guides.clear()

        if not self._drag_initial_positions:
            return
        moving_ids = set(self._drag_initial_positions.keys())
        moving_nodes = [
            self._node_items[cid] for cid in moving_ids if cid in self._node_items
        ]
        if not moving_nodes:
            return
        ref = moving_nodes[0]
        ref_cx = ref.pos().x() + 0.5 * ref.width_px
        ref_cy = ref.pos().y() + 0.5 * ref.height_px

        pen = QPen(QColor("#38BDF8"), 1.0, Qt.PenStyle.DashLine)
        for cid, other in self._node_items.items():
            if cid in moving_ids:
                continue
            ocx = other.pos().x() + 0.5 * other.width_px
            ocy = other.pos().y() + 0.5 * other.height_px
            if abs(ref_cx - ocx) <= 8.0:
                guide = self._scene.addLine(
                    ocx,
                    min(ref.pos().y(), other.pos().y()) - 40.0,
                    ocx,
                    max(ref.pos().y() + ref.height_px, other.pos().y() + other.height_px) + 40.0,
                    pen,
                )
                guide.setZValue(Z_HANDLE)
                self._snap_guides.append(guide)
            if abs(ref_cy - ocy) <= 8.0:
                guide = self._scene.addLine(
                    min(ref.pos().x(), other.pos().x()) - 40.0, ocy,
                    max(ref.pos().x() + ref.width_px, other.pos().x() + other.width_px) + 40.0, ocy,
                    pen,
                )
                guide.setZValue(Z_HANDLE)
                self._snap_guides.append(guide)

    def _on_handle_moved_live(
        self, kind: str, owner_id: str, index: int, new_pos: QPointF
    ) -> None:
        if kind == "cable_wp":
            c_item = self._cable_items.get(owner_id)
            if c_item is not None and 0 <= index < len(c_item.cable.logical_waypoints):
                c_item.cable.logical_waypoints[index] = Point2D(new_pos.x(), new_pos.y())
                c_item.rebuild_path()
        elif kind in ("bundle_entry", "bundle_exit", "bundle_wp"):
            b_item = self._bundle_items.get(owner_id)
            if b_item is not None:
                if kind == "bundle_entry":
                    b_item.bundle.entry_point = Point2D(new_pos.x(), new_pos.y())
                elif kind == "bundle_exit":
                    b_item.bundle.exit_point = Point2D(new_pos.x(), new_pos.y())
                elif 0 <= index < len(b_item.bundle.waypoints):
                    b_item.bundle.waypoints[index] = Point2D(new_pos.x(), new_pos.y())
                b_item.rebuild_path()
                for c_item in self._cable_items.values():
                    if c_item.bundle is not None and c_item.bundle.id == owner_id:
                        c_item.rebuild_path()

    def _on_handle_released(
        self, kind: str, owner_id: str, _index: int, _new_pos: QPointF
    ) -> None:
        if kind == "cable_wp":
            c_item = self._cable_items.get(owner_id)
            if c_item is not None:
                self._state.set_cable_waypoints(owner_id, c_item.cable.logical_waypoints)
        elif kind in ("bundle_entry", "bundle_exit", "bundle_wp"):
            b_item = self._bundle_items.get(owner_id)
            if b_item is not None:
                self._state.update_bundle(owner_id, b_item.bundle, text="Move Bundle Handle")

    def _on_handle_delete(self, kind: str, owner_id: str, index: int) -> None:
        if kind == "cable_wp":
            cab = next((c for c in self._state.project.cables if c.id == owner_id), None)
            if cab is not None and 0 <= index < len(cab.logical_waypoints):
                wps = list(cab.logical_waypoints)
                wps.pop(index)
                self._state.set_cable_waypoints(owner_id, wps)
        elif kind == "bundle_wp":
            bundle = next((b for b in self._state.project.bundles if b.id == owner_id), None)
            if bundle is not None and 0 <= index < len(bundle.waypoints):
                updated = copy.deepcopy(bundle)
                updated.waypoints.pop(index)
                self._state.update_bundle(owner_id, updated, text="Delete Bundle Waypoint")

    # ------------------------------------------------------------------
    # Background Grid (z = -100 equivalent)
    # ------------------------------------------------------------------

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        painter.fillRect(rect, QColor("#0B1120"))
        grid_step = 40.0
        major_step = 200.0

        left = math.floor(rect.left() / grid_step) * grid_step
        top = math.floor(rect.top() / grid_step) * grid_step

        minor_pen = QPen(QColor("#141F33"), 1.0)
        major_pen = QPen(QColor("#1E293B"), 1.2)

        x = left
        while x <= rect.right():
            painter.setPen(major_pen if abs(x % major_step) < 1e-4 else minor_pen)
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            x += grid_step

        y = top
        while y <= rect.bottom():
            painter.setPen(major_pen if abs(y % major_step) < 1e-4 else minor_pen)
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            y += grid_step

    # ------------------------------------------------------------------
    # BULLETPROOF Scale-Aware Port Hit-Testing & Dual-Mode State Machine
    # ------------------------------------------------------------------

    def find_port_at_scene_pos(
        self,
        scene_pos: QPointF,
        hit_radius_screen_px: float = 18.0,
        exclude_port_key: tuple[str, str] | None = None,
    ) -> PortAnchorItem | None:
        """
        Convert hit_radius_screen_px into scene units using current view zoom and
        iterate ALL registered PortAnchorItem instances (NEVER scene.itemAt()).
        """
        scale = max(abs(self.transform().m11()), 0.05)
        radius_scene = hit_radius_screen_px / scale

        best_port: PortAnchorItem | None = None
        best_dist = float("inf")
        for key, port_item in self._port_items.items():
            if exclude_port_key is not None and key == exclude_port_key:
                continue
            pin_dist = math.hypot(
                scene_pos.x() - port_item.pin_scene_pos().x(),
                scene_pos.y() - port_item.pin_scene_pos().y(),
            )
            rect_dist = _point_to_rect_distance(scene_pos, port_item.label_scene_rect())
            label_tol = min(radius_scene, 4.0 / scale)
            if (pin_dist <= radius_scene or rect_dist <= label_tol) and pin_dist < best_dist:
                best_dist = pin_dist
                best_port = port_item
        return best_port

    def _update_preview_graphics(self, src_pos: QPointF, cursor_scene_pos: QPointF) -> None:
        assert self._connect_src_port is not None
        src_key = self._connect_src_port.key
        candidate = self.find_port_at_scene_pos(
            cursor_scene_pos, 18.0, exclude_port_key=src_key
        )
        scale = max(abs(self.transform().m11()), 0.05)

        # Check if hovered right back over source port itself
        self_dist = math.hypot(
            cursor_scene_pos.x() - src_pos.x(),
            cursor_scene_pos.y() - src_pos.y(),
        )
        hovering_self = candidate is None and self_dist <= (14.0 / scale) and (
            self._connect_mode == "PENDING_CLICK_CONNECT"
            or math.hypot(
                cursor_scene_pos.x() - src_pos.x(),
                cursor_scene_pos.y() - src_pos.y(),
            ) > (6.0 / scale)
        )

        end_pos = cursor_scene_pos
        wire_color = QColor("#38BDF8")

        if hovering_self and self._connect_mode == "PENDING_CLICK_CONNECT":
            self._snap_target_port = None
            self._show_red_x_ring(src_pos)
            wire_color = QColor("#EF4444")
        elif candidate is not None and candidate.occupied:
            self._snap_target_port = None
            self._show_red_x_ring(candidate.pin_scene_pos())
            wire_color = QColor("#EF4444")
        elif candidate is not None:
            self._snap_target_port = candidate
            end_pos = candidate.pin_scene_pos()
            self._show_green_lock_ring(end_pos)
            wire_color = QColor("#22C55E")
        else:
            self._snap_target_port = None
            self._lock_ring.hide()
            self._red_x_path.hide()

        path = QPainterPath()
        path.moveTo(src_pos)
        path.lineTo(end_pos)
        self._preview_wire.setPath(path)
        self._preview_wire.setPen(QPen(wire_color, 2.5, Qt.PenStyle.DashLine))
        self._preview_wire.show()

    def _show_green_lock_ring(self, scene_pos: QPointF) -> None:
        self._red_x_path.hide()
        self._lock_ring.setPos(scene_pos)
        self._lock_ring.setPen(QPen(QColor("#22C55E"), 2.5))
        self._lock_ring.setBrush(QBrush(QColor(34, 197, 94, 45)))
        self._lock_ring.show()

    def _show_red_x_ring(self, scene_pos: QPointF, *, flash: bool = False) -> None:
        self._lock_ring.setPos(scene_pos)
        self._lock_ring.setPen(QPen(QColor("#EF4444"), 2.5))
        self._lock_ring.setBrush(QBrush(QColor(239, 68, 68, 50)))
        self._lock_ring.show()

        x_path = QPainterPath()
        x_path.moveTo(scene_pos.x() - 6.0, scene_pos.y() - 6.0)
        x_path.lineTo(scene_pos.x() + 6.0, scene_pos.y() + 6.0)
        x_path.moveTo(scene_pos.x() + 6.0, scene_pos.y() - 6.0)
        x_path.lineTo(scene_pos.x() - 6.0, scene_pos.y() + 6.0)
        self._red_x_path.setPath(x_path)
        self._red_x_path.show()
        if flash:
            self._flash_timer.start(750)

    def _hide_flash_ring(self) -> None:
        if self._connect_mode == "IDLE":
            self._lock_ring.hide()
            self._red_x_path.hide()

    def _cancel_connection(self, *, silent: bool = False) -> None:
        self._connect_mode = "IDLE"
        self._connect_src_port = None
        self._snap_target_port = None
        self._preview_wire.hide()
        self._lock_ring.hide()
        self._red_x_path.hide()
        if not silent:
            self._state.status_message.emit("Port connection cancelled.", 2000)

    def _notify_occupied_port(self, port_item: PortAnchorItem) -> None:
        self._show_red_x_ring(port_item.pin_scene_pos(), flash=True)
        conn = self._state.project.get_port_connection(
            port_item.component_id, port_item.port_id
        )
        peer_desc = (
            f"{conn[1].name}[{conn[2].label}]" if conn is not None else "another port"
        )
        self._state.status_message.emit(
            f"Port {port_item.port.label} is already connected to {peer_desc}. "
            "Disconnect in Inspector first.",
            4500,
        )

    def _commit_port_connection(
        self, src: PortAnchorItem, target: PortAnchorItem
    ) -> Cable | None:
        if src.key == target.key:
            return None
        if self._state.project.is_port_occupied(
            src.component_id, src.port_id
        ) or self._state.project.is_port_occupied(target.component_id, target.port_id):
            self._notify_occupied_port(target)
            return None

        cable = self._state.connect_ports(
            src.component_id,
            src.port_id,
            target.component_id,
            target.port_id,
        )
        if cable is not None:
            if not are_port_types_compatible(src.port.port_type, target.port.port_type):
                msg = (
                    f"Warning: {src.port.port_type.value} connected to "
                    f"{target.port.port_type.value} (flagged in Validation Panel)"
                )
                self.media_mismatch_warning.emit(msg)
                self._state.status_message.emit(msg, 5500)
            else:
                src_comp = self._state.project.get_component(src.component_id)
                dst_comp = self._state.project.get_component(target.component_id)
                s_name = src_comp.name if src_comp else src.component_id
                d_name = dst_comp.name if dst_comp else target.component_id
                self._state.status_message.emit(
                    f"Connected {s_name}[{src.port.label}] ↔ {d_name}[{target.port.label}]",
                    3000,
                )
        return cable

    # ------------------------------------------------------------------
    # Mouse & Keyboard Events (Centralized Ownership)
    # ------------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        # 1. Middle mouse or Space+Left always pans
        if event.button() == Qt.MouseButton.MiddleButton or (
            event.button() == Qt.MouseButton.LeftButton and self._space_held
        ):
            self._panning_active = True
            self._pan_last_pos = event.position().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return

        if self._hand_tool_active:
            super().mousePressEvent(event)
            return

        if event.button() == Qt.MouseButton.LeftButton:
            scene_pos = self.mapToScene(event.position().toPoint())

            # PENDING_CLICK_CONNECT: second click finishes or cancels
            if self._connect_mode == "PENDING_CLICK_CONNECT" and self._connect_src_port is not None:
                src = self._connect_src_port
                target = self._snap_target_port or self.find_port_at_scene_pos(
                    scene_pos, 18.0, exclude_port_key=src.key
                )
                if target is not None and not target.occupied and target.key != src.key:
                    self._commit_port_connection(src, target)
                    self._cancel_connection(silent=True)
                elif target is not None and target.occupied:
                    self._notify_occupied_port(target)
                else:
                    self._cancel_connection()
                event.accept()
                return

            # IDLE: Check port hit BEFORE super() so parent ComponentNodeItem NEVER moves!
            hit_port = self.find_port_at_scene_pos(scene_pos, 14.0)
            if hit_port is not None:
                if hit_port.occupied or self._state.project.is_port_occupied(
                    hit_port.component_id, hit_port.port_id
                ):
                    self._notify_occupied_port(hit_port)
                    event.accept()
                    return
                self._connect_mode = "DRAG_CONNECTING"
                self._connect_src_port = hit_port
                self._connect_press_screen_pos = event.position()
                self._snap_target_port = None
                self._update_preview_graphics(hit_port.pin_scene_pos(), scene_pos)
                event.accept()
                return

            # Record initial positions of movable selected / clicked nodes for MoveComponentsCommand
            super().mousePressEvent(event)
            self._drag_initial_positions.clear()
            for cid, node in self._node_items.items():
                if node.isSelected():
                    self._drag_initial_positions[cid] = Point2D(
                        node.component.logical_pos.x,
                        node.component.logical_pos.y,
                    )
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._panning_active:
            delta = event.position().toPoint() - self._pan_last_pos
            self._pan_last_pos = event.position().toPoint()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            event.accept()
            return

        scene_pos = self.mapToScene(event.position().toPoint())

        if (
            self._connect_mode in ("DRAG_CONNECTING", "PENDING_CLICK_CONNECT")
            and self._connect_src_port is not None
        ):
            self._update_preview_graphics(self._connect_src_port.pin_scene_pos(), scene_pos)
            event.accept()
            return

        super().mouseMoveEvent(event)
        if not self._drag_initial_positions:
            self._update_hover_trace(scene_pos)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._panning_active:
            self._panning_active = False
            self.setCursor(
                Qt.CursorShape.OpenHandCursor if self._space_held else Qt.CursorShape.ArrowCursor
            )
            if not self._space_held and not self._hand_tool_active:
                self.unsetCursor()
            event.accept()
            return

        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._connect_mode == "DRAG_CONNECTING"
            and self._connect_src_port is not None
        ):
            src = self._connect_src_port
            scene_pos = self.mapToScene(event.position().toPoint())
            drag_dist_px = (event.position() - self._connect_press_screen_pos).manhattanLength()
            if drag_dist_px <= 6.0:
                self._connect_mode = "PENDING_CLICK_CONNECT"
                src_comp = self._state.project.get_component(src.component_id)
                comp_name = src_comp.name if src_comp else src.component_id
                self._state.status_message.emit(
                    f"Click target port to connect {comp_name}[{src.port.label}] (Esc to cancel)",
                    0,
                )
                self._update_preview_graphics(src.pin_scene_pos(), scene_pos)
            else:
                target = self._snap_target_port or self.find_port_at_scene_pos(
                    scene_pos, 18.0, exclude_port_key=src.key
                )
                if target is not None and not target.occupied and target.key != src.key:
                    self._commit_port_connection(src, target)
                    self._cancel_connection(silent=True)
                elif target is not None and target.occupied:
                    self._notify_occupied_port(target)
                    self._cancel_connection(silent=True)
                else:
                    self._cancel_connection(silent=True)
            event.accept()
            return

        super().mouseReleaseEvent(event)

        # Clear snap guides & push single MoveComponentsCommand if any node moved
        for line in self._snap_guides:
            self._scene.removeItem(line)
        self._snap_guides.clear()

        if self._drag_initial_positions:
            moves: dict[str, tuple[Point2D, Point2D]] = {}
            for cid, old_pos in self._drag_initial_positions.items():
                node = self._node_items.get(cid)
                if node is None:
                    continue
                new_pt = Point2D(float(node.pos().x()), float(node.pos().y()))
                if math.hypot(new_pt.x - old_pos.x, new_pt.y - old_pos.y) > 0.5:
                    moves[cid] = (old_pos, new_pt)
            self._drag_initial_positions.clear()
            if moves:
                self._state.move_components(moves)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._connect_mode == "IDLE":
            scene_pos = self.mapToScene(event.position().toPoint())
            # Insert WaypointHandle if double-clicking near an unbundled cable segment
            for cid, c_item in self._cable_items.items():
                if c_item.bundle is not None:
                    continue
                pts = c_item.current_polyline()
                for i in range(len(pts) - 1):
                    dist, proj = _point_to_segment_dist(scene_pos, pts[i], pts[i + 1])
                    if dist <= 10.0:
                        wps = list(c_item.cable.logical_waypoints)
                        # Segment index i inserts waypoint at index i
                        wps.insert(i, Point2D(proj.x(), proj.y()))
                        self._state.set_cable_waypoints(cid, wps)
                        event.accept()
                        return
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        factor = 1.15 if event.angleDelta().y() > 0 else (1.0 / 1.15)
        cur_scale = self.transform().m11()
        next_scale = cur_scale * factor
        if 0.15 <= next_scale <= 4.5:
            self.scale(factor, factor)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self.viewport_resized.emit()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape and self._connect_mode != "IDLE":
            self._cancel_connection()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_held = True
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_selected()
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_held = False
            if not self._panning_active and not self._hand_tool_active:
                self.unsetCursor()
            event.accept()
            return
        super().keyReleaseEvent(event)

    # ------------------------------------------------------------------
    # Hover Trace & Dimming (Section 4.4C)
    # ------------------------------------------------------------------

    def _format_trace_text(self, cable: Cable) -> str:
        project = self._state.project
        sc = project.get_component(cable.source_component_id)
        sp = project.get_port(cable.source_component_id, cable.source_port_id)
        dc = project.get_component(cable.target_component_id)
        dp = project.get_port(cable.target_component_id, cable.target_port_id)
        src_str = f"{sc.name} [{sp.label} · {sp.port_type.value}]" if sc and sp else "?"
        dst_str = f"{dc.name} [{dp.label} · {dp.port_type.value}]" if dc and dp else "?"

        meta_parts: list[str] = []
        if cable.vlan_id:
            meta_parts.append(f"VLAN {cable.vlan_id}")
        if cable.logical_bundle_id:
            b = next((x for x in project.bundles if x.id == cable.logical_bundle_id), None)
            if b is not None:
                meta_parts.append(f"Trunk: {b.name}")
        elif cable.label:
            meta_parts.append(cable.label)
        meta_str = f"({ ' · '.join(meta_parts) })" if meta_parts else "(Direct Link)"
        return f"{src_str} ━━{meta_str}━━▶ {dst_str}"

    def _update_hover_trace(self, scene_pos: QPointF) -> None:
        # 1. Port hover
        hit_port = self.find_port_at_scene_pos(scene_pos, 12.0)
        if hit_port is not None:
            conn = self._state.project.get_port_connection(
                hit_port.component_id, hit_port.port_id
            )
            if conn is not None:
                cable, peer_comp, peer_port = conn
                self._apply_trace_highlight(
                    cable_ids={cable.id},
                    port_keys={hit_port.key, (peer_comp.id, peer_port.id)},
                    comp_ids={hit_port.component_id, peer_comp.id},
                    bundle_ids={cable.logical_bundle_id} if cable.logical_bundle_id else set(),
                )
                self.connection_trace_changed.emit(self._format_trace_text(cable))
                return
            comp = self._state.project.get_component(hit_port.component_id)
            cname = comp.name if comp else hit_port.component_id
            self._clear_trace_highlight()
            self.connection_trace_changed.emit(
                f"{cname} [{hit_port.port.label} · {hit_port.port.port_type.value}] — Free Port "
                "(Drag or click to connect)"
            )
            return

        # 2. Cable hover
        for cid, c_item in self._cable_items.items():
            if c_item.bundle is not None and c_item.bundle.collapsed:
                continue
            pts = c_item.current_polyline()
            for i in range(len(pts) - 1):
                dist, _ = _point_to_segment_dist(scene_pos, pts[i], pts[i + 1])
                if dist <= 8.0:
                    cab = c_item.cable
                    self._apply_trace_highlight(
                        cable_ids={cid},
                        port_keys={
                            (cab.source_component_id, cab.source_port_id),
                            (cab.target_component_id, cab.target_port_id),
                        },
                        comp_ids={cab.source_component_id, cab.target_component_id},
                        bundle_ids={cab.logical_bundle_id} if cab.logical_bundle_id else set(),
                    )
                    self.connection_trace_changed.emit(self._format_trace_text(cab))
                    return

        # 3. Bundle trunk hover
        for bid, b_item in self._bundle_items.items():
            if b_item.shape().contains(scene_pos):
                member_ids = set(b_item.bundle.member_cable_ids)
                port_keys: set[tuple[str, str]] = set()
                comp_ids: set[str] = set()
                for cid in member_ids:
                    c_item = self._cable_items.get(cid)
                    if c_item is not None:
                        cab = c_item.cable
                        port_keys.add((cab.source_component_id, cab.source_port_id))
                        port_keys.add((cab.target_component_id, cab.target_port_id))
                        comp_ids.add(cab.source_component_id)
                        comp_ids.add(cab.target_component_id)
                self._apply_trace_highlight(
                    cable_ids=member_ids,
                    port_keys=port_keys,
                    comp_ids=comp_ids,
                    bundle_ids={bid},
                )
                self.connection_trace_changed.emit(
                    f"Bundle '{b_item.bundle.name}' · {len(member_ids)} member cables"
                )
                return

        self._clear_trace_highlight()
        self.connection_trace_changed.emit(
            "Hover any port, cable, or bundle to trace its end-to-end connection"
        )

    def _apply_trace_highlight(
        self,
        *,
        cable_ids: set[str],
        port_keys: set[tuple[str, str]],
        comp_ids: set[str],
        bundle_ids: set[str],
    ) -> None:
        for cid, node in self._node_items.items():
            node.set_dimmed(cid not in comp_ids)
        for key, p_item in self._port_items.items():
            p_item.set_pulsed(key in port_keys)
            p_item.dimmed = p_item.component_id not in comp_ids
            p_item.update()
        for cid, c_item in self._cable_items.items():
            traced = cid in cable_ids
            c_item.set_trace_state(hovered=traced, traced=traced, dimmed=not traced)
        for bid, b_item in self._bundle_items.items():
            active = bid in bundle_ids
            b_item.set_trace_state(hovered=active, dimmed=not active)

    def _clear_trace_highlight(self) -> None:
        for node in self._node_items.values():
            node.set_dimmed(False)
        for p_item in self._port_items.values():
            p_item.set_pulsed(False)
            p_item.dimmed = False
            p_item.update()
        for c_item in self._cable_items.values():
            c_item.set_trace_state(hovered=False, traced=False, dimmed=False)
        for b_item in self._bundle_items.values():
            b_item.set_trace_state(hovered=False, dimmed=False)

    # ------------------------------------------------------------------
    # Palette Drop, Context Menu, Selection, Copy/Paste, Delete
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasFormat(TEMPLATE_MIME_TYPE):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if event.mimeData().hasFormat(TEMPLATE_MIME_TYPE):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        if event.mimeData().hasFormat(TEMPLATE_MIME_TYPE):
            raw = bytes(event.mimeData().data(TEMPLATE_MIME_TYPE)).decode("utf-8")
            tpl = ComponentTemplate.from_dict(json.loads(raw))
            scene_pos = self.mapToScene(event.position().toPoint())
            self.spawn_template(tpl, scene_pos)
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    def spawn_template(
        self, template: ComponentTemplate, scene_pos: QPointF | None = None
    ) -> Component:
        """Instantiate and add a Component from template at scene_pos (or viewport center)."""
        if scene_pos is None:
            center = self.mapToScene(self.viewport().rect().center())
            offset = (len(self._state.project.components) % 5) * 28.0
            pos = Point2D(center.x() - 90.0 + offset, center.y() - 45.0 + offset)
        else:
            pos = Point2D(scene_pos.x() - 90.0, scene_pos.y() - 45.0)
        existing_names = [c.name for c in self._state.project.components]
        comp = instantiate_from_template(template, existing_names, pos=pos)
        self._state.add_component(comp)
        self._state.set_selection("component", comp.id)
        return comp

    def _on_scene_selection_changed(self) -> None:
        selected = self._scene.selectedItems()
        for item in selected:
            if isinstance(item, ComponentNodeItem):
                self._state.set_selection("component", item.component.id)
                return
            if isinstance(item, RoutableCableItem):
                self._state.set_selection("cable", item.cable.id)
                return
            if isinstance(item, CableBundleItem):
                if item.bundle.member_cable_ids:
                    self._state.set_selection("cable", item.bundle.member_cable_ids[0])
                return
        self._state.set_selection("", "")

    def selected_component_ids(self) -> list[str]:
        return [cid for cid, item in self._node_items.items() if item.isSelected()]

    def selected_cable_ids(self) -> list[str]:
        ids = [cid for cid, item in self._cable_items.items() if item.isSelected()]
        for b_item in self._bundle_items.values():
            if b_item.isSelected():
                for cid in b_item.bundle.member_cable_ids:
                    if cid not in ids:
                        ids.append(cid)
        return ids

    def copy_selected(self) -> int:
        """Copy selected components to internal clipboard; returns count copied."""
        cids = self.selected_component_ids()
        self._clipboard_components = [
            copy.deepcopy(c)
            for c in self._state.project.components
            if c.id in set(cids)
        ]
        if self._clipboard_components:
            self._state.status_message.emit(
                f"Copied {len(self._clipboard_components)} component(s).", 2000
            )
        return len(self._clipboard_components)

    def paste_clipboard(self) -> list[Component]:
        """Smart-duplicate clipboard components (auto-increments name, clears duplicate MAC)."""
        if not self._clipboard_components:
            return []
        existing_names = {c.name.strip().lower() for c in self._state.project.components}
        pasted: list[Component] = []

        self._state.undo_stack.beginMacro("Paste Component(s)")
        try:
            for orig in self._clipboard_components:
                dup = copy.deepcopy(orig)
                dup.id = new_id()
                dup.name = _increment_component_name(orig.name, existing_names)
                existing_names.add(dup.name.lower())
                dup.mac_address = ""  # Clear duplicate MAC per Section 4.11
                dup.logical_pos = Point2D(orig.logical_pos.x + 40.0, orig.logical_pos.y + 40.0)
                dup.physical_building_id = None
                dup.physical_floor_id = None
                dup.physical_pos_m = None
                dup.ports = [
                    Port(
                        id=new_id(),
                        label=p.label,
                        port_type=p.port_type,
                        custom_type_name=p.custom_type_name,
                        speed_mbps=p.speed_mbps,
                        poe_capable=p.poe_capable,
                    )
                    for p in orig.ports
                ]
                self._state.add_component(dup)
                pasted.append(dup)
        finally:
            self._state.undo_stack.endMacro()

        if pasted:
            self._clipboard_components = [copy.deepcopy(c) for c in pasted]
            self._scene.clearSelection()
            for comp in pasted:
                node = self._node_items.get(comp.id)
                if node is not None:
                    node.setSelected(True)
            self._state.set_selection("component", pasted[-1].id)
        return pasted

    def delete_selected(self) -> None:
        comp_ids = self.selected_component_ids()
        cable_ids = self.selected_cable_ids()
        if comp_ids:
            self._state.remove_components(comp_ids)
        elif cable_ids:
            self._state.undo_stack.beginMacro("Disconnect Selected Cable(s)")
            try:
                for cid in cable_ids:
                    self._state.disconnect_cable(cid)
            finally:
                self._state.undo_stack.endMacro()

    def bundle_selected_cables(self) -> CableBundle | None:
        cids = self.selected_cable_ids()
        if len(cids) < 2:
            self._state.status_message.emit("Select at least 2 cables to bundle.", 3000)
            return None

        src_pts: list[QPointF] = []
        dst_pts: list[QPointF] = []
        for cid in cids:
            item = self._cable_items.get(cid)
            if item is not None:
                p1, p2 = item.endpoints_scene()
                src_pts.append(p1)
                dst_pts.append(p2)
        if not src_pts:
            return None
        avg_sx = sum(p.x() for p in src_pts) / len(src_pts)
        avg_sy = sum(p.y() for p in src_pts) / len(src_pts)
        avg_dx = sum(p.x() for p in dst_pts) / len(dst_pts)
        avg_dy = sum(p.y() for p in dst_pts) / len(dst_pts)
        entry = Point2D(avg_sx + 0.30 * (avg_dx - avg_sx), avg_sy + 0.30 * (avg_dy - avg_sy))
        exit_pt = Point2D(avg_sx + 0.70 * (avg_dx - avg_sx), avg_sy + 0.70 * (avg_dy - avg_sy))
        bundle_num = sum(1 for b in self._state.project.bundles if b.scope == "logical") + 1
        bundle = CableBundle(
            entry_point=entry,
            exit_point=exit_pt,
            name=f"Bundle-{bundle_num}",
            scope="logical",
            member_cable_ids=cids,
            collapsed=True,
        )
        return self._state.create_bundle(bundle)

    def unbundle_selected(self) -> None:
        for b_item in list(self._bundle_items.values()):
            if b_item.isSelected():
                self._state.delete_bundle(b_item.bundle.id)
                return
        for cid in self.selected_cable_ids():
            c_item = self._cable_items.get(cid)
            if c_item is not None and c_item.bundle is not None:
                self._state.delete_bundle(c_item.bundle.id)
                return

    def auto_orthogonal_selected(self) -> None:
        cids = self.selected_cable_ids()
        if not cids:
            cids = list(self._cable_items.keys())
        self._state.undo_stack.beginMacro("Auto-Orthogonal Cables")
        try:
            for cid in cids:
                c_item = self._cable_items.get(cid)
                if c_item is None or c_item.bundle is not None:
                    continue
                p1, p2 = c_item.endpoints_scene()
                wps = compute_orthogonal_waypoints(
                    Point2D(p1.x(), p1.y()), Point2D(p2.x(), p2.y())
                )
                self._state.set_cable_waypoints(cid, wps)
        finally:
            self._state.undo_stack.endMacro()

    def align_selected(self, mode: str) -> None:
        cids = self.selected_component_ids()
        if len(cids) < 2:
            return
        rects = {
            cid: (
                self._node_items[cid].pos().x(),
                self._node_items[cid].pos().y(),
                self._node_items[cid].width_px,
                self._node_items[cid].height_px,
            )
            for cid in cids
            if cid in self._node_items
        }
        new_positions = compute_alignment_moves(rects, mode)
        moves = {
            cid: (
                copy.deepcopy(self._node_items[cid].component.logical_pos),
                pos,
            )
            for cid, pos in new_positions.items()
        }
        if moves:
            self._state.move_components(moves)

    def distribute_selected(self, axis: str) -> None:
        cids = self.selected_component_ids()
        if len(cids) < 3:
            return
        rects = {
            cid: (
                self._node_items[cid].pos().x(),
                self._node_items[cid].pos().y(),
                self._node_items[cid].width_px,
                self._node_items[cid].height_px,
            )
            for cid in cids
            if cid in self._node_items
        }
        new_positions = compute_distribution_moves(rects, axis)
        moves = {
            cid: (
                copy.deepcopy(self._node_items[cid].component.logical_pos),
                pos,
            )
            for cid, pos in new_positions.items()
        }
        if moves:
            self._state.move_components(moves)

    def auto_layout_hierarchy(self) -> None:
        layout = compute_hierarchical_layout(
            self._state.project.components, self._state.project.cables
        )
        if not layout:
            return
        moves = {
            comp.id: (copy.deepcopy(comp.logical_pos), layout[comp.id])
            for comp in self._state.project.components
            if comp.id in layout
        }
        self._state.undo_stack.push(
            _AutoLayoutCommand(self._state, moves)
        )
        self.fit_view()

    def contextMenuEvent(self, event) -> None:  # type: ignore[override]
        menu = QMenu(self)
        cids = self.selected_cable_ids()
        comp_ids = self.selected_component_ids()

        if len(cids) >= 2:
            menu.addAction("Bundle Selected Cables", self.bundle_selected_cables)
        if any(
            self._cable_items[cid].bundle is not None
            for cid in cids
            if cid in self._cable_items
        ) or any(b.isSelected() for b in self._bundle_items.values()):
            menu.addAction("Unbundle", self.unbundle_selected)
        if cids:
            menu.addAction("Auto-Orthogonal Selected", self.auto_orthogonal_selected)
            menu.addAction("Clear Waypoints", self._clear_selected_waypoints)
        if comp_ids:
            menu.addAction("Copy Component(s) (Ctrl+C)", self.copy_selected)
        if self._clipboard_components:
            menu.addAction("Paste Component(s) (Ctrl+V)", self.paste_clipboard)
        if comp_ids or cids:
            menu.addSeparator()
            menu.addAction("Delete Selected", self.delete_selected)

        if not menu.isEmpty():
            menu.exec(event.globalPos())

    def _clear_selected_waypoints(self) -> None:
        self._state.undo_stack.beginMacro("Clear Cable Waypoints")
        try:
            for cid in self.selected_cable_ids():
                self._state.set_cable_waypoints(cid, [])
        finally:
            self._state.undo_stack.endMacro()


class _AutoLayoutCommand(
    __import__("desktop.state", fromlist=["MoveComponentsCommand"]).MoveComponentsCommand
):
    def __init__(self, state: ProjectState, moves: dict[str, tuple[Point2D, Point2D]]) -> None:
        super().__init__(state, moves, text="Auto-Layout Hierarchy")


class LogicalView(QWidget):
    """Tab 1 complete layout: Toolbar | (Palette | Canvas + MiniMap | Inspector) | Trace Bar."""

    def __init__(
        self,
        state: ProjectState,
        config: AppConfig | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._state = state
        self._config = config if config is not None else load_config()
        self._build_ui()
        self._wire_signals()
        self._refresh_empty_state()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Toolbar
        self.toolbar = QToolBar("Logical Toolbar", self)
        self.toolbar.setMovable(False)

        self.select_tool_btn = QPushButton("Select (V)", self)
        self.select_tool_btn.setCheckable(True)
        self.select_tool_btn.setChecked(True)
        self.select_tool_btn.clicked.connect(lambda: self._set_tool_mode(False))
        self.toolbar.addWidget(self.select_tool_btn)

        self.pan_tool_btn = QPushButton("Pan (H)", self)
        self.pan_tool_btn.setCheckable(True)
        self.pan_tool_btn.clicked.connect(lambda: self._set_tool_mode(True))
        self.toolbar.addWidget(self.pan_tool_btn)
        self.toolbar.addSeparator()

        # Node Opacity slider (30% - 95%, alpha 77 - 242, default 165)
        self.toolbar.addWidget(QLabel(" Node Opacity: ", self))
        self.opacity_slider = QSlider(Qt.Orientation.Horizontal, self)
        self.opacity_slider.setRange(MIN_NODE_ALPHA, MAX_NODE_ALPHA)
        self.opacity_slider.setValue(self._config.node_opacity_alpha)
        self.opacity_slider.setFixedWidth(110)
        self.opacity_pct_label = QLabel(
            f" {round(self._config.node_opacity_alpha / 255.0 * 100)}% ", self
        )
        self.toolbar.addWidget(self.opacity_slider)
        self.toolbar.addWidget(self.opacity_pct_label)
        self.toolbar.addSeparator()

        # Align & Distribute
        align_btn = QPushButton("Align ▾", self)
        align_menu = QMenu(align_btn)
        for label, mode in (
            ("Align Left", "left"),
            ("Align Center (H)", "center"),
            ("Align Right", "right"),
            ("Align Top", "top"),
            ("Align Middle (V)", "middle"),
            ("Align Bottom", "bottom"),
        ):
            act = QAction(label, align_menu)
            act.triggered.connect(lambda _chk=False, m=mode: self.canvas.align_selected(m))
            align_menu.addAction(act)
        align_menu.addSeparator()
        act_dh = QAction("Distribute Horizontally", align_menu)
        act_dh.triggered.connect(lambda: self.canvas.distribute_selected("h"))
        act_dv = QAction("Distribute Vertically", align_menu)
        act_dv.triggered.connect(lambda: self.canvas.distribute_selected("v"))
        align_menu.addAction(act_dh)
        align_menu.addAction(act_dv)
        align_btn.setMenu(align_menu)
        self.toolbar.addWidget(align_btn)

        self.auto_layout_btn = QPushButton("Auto-Layout Hierarchy", self)
        self.toolbar.addWidget(self.auto_layout_btn)
        self.toolbar.addSeparator()

        self.bundle_btn = QPushButton("Bundle", self)
        self.unbundle_btn = QPushButton("Unbundle", self)
        self.ortho_btn = QPushButton("Auto-Orthogonal", self)
        self.toolbar.addWidget(self.bundle_btn)
        self.toolbar.addWidget(self.unbundle_btn)
        self.toolbar.addWidget(self.ortho_btn)
        self.toolbar.addSeparator()

        self.fit_btn = QPushButton("Fit View (F)", self)
        self.minimap_btn = QPushButton("Mini-Map (M)", self)
        self.minimap_btn.setCheckable(True)
        self.minimap_btn.setChecked(True)
        self.toolbar.addWidget(self.fit_btn)
        self.toolbar.addWidget(self.minimap_btn)

        root.addWidget(self.toolbar)

        # Center 3-pane Splitter: Palette | Canvas | Inspector
        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.palette = ComponentPalette(self._state, self)

        self.canvas_host = QWidget(self)
        canvas_layout = QVBoxLayout(self.canvas_host)
        canvas_layout.setContentsMargins(0, 0, 0, 0)
        canvas_layout.setSpacing(0)

        self.canvas = LogicalCanvasView(
            self._state, node_alpha=self._config.node_opacity_alpha, parent=self.canvas_host
        )
        canvas_layout.addWidget(self.canvas, 1)

        # Floating MiniMap overlay in canvas viewport corner
        self.minimap = MiniMapWidget(self.canvas, parent=self.canvas.viewport())
        self.minimap.show()

        # Non-blocking Amber Toast Banner overlay
        self.toast_banner = QFrame(self.canvas.viewport())
        self.toast_banner.setStyleSheet(
            "background-color: rgba(120, 53, 15, 235); border: 1px solid #F59E0B; "
            "border-radius: 6px; padding: 6px 12px;"
        )
        toast_row = QHBoxLayout(self.toast_banner)
        toast_row.setContentsMargins(8, 4, 8, 4)
        self.toast_label = QLabel("", self.toast_banner)
        self.toast_label.setStyleSheet("color: #FEF3C7; font-weight: 600;")
        toast_close = QPushButton("Dismiss", self.toast_banner)
        toast_close.clicked.connect(self.toast_banner.hide)
        toast_row.addWidget(self.toast_label, 1)
        toast_row.addWidget(toast_close)
        self.toast_banner.hide()

        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self.toast_banner.hide)

        # Guided Empty-State Card overlay
        self.empty_card = QFrame(self.canvas.viewport())
        self.empty_card.setStyleSheet(
            "background-color: rgba(15, 23, 42, 230); border: 1px solid #334155; "
            "border-radius: 10px; padding: 16px;"
        )
        empty_layout = QVBoxLayout(self.empty_card)
        empty_title = QLabel("Start Designing Your Logical Network", self.empty_card)
        empty_title.setStyleSheet("font-size: 16px; font-weight: 700; color: #F8FAFC;")
        empty_desc = QLabel(
            "• Drag or double-click any device from the left Component Palette.\n"
            "• Drag from one colored port pin to another (or click Port A → Port B) to cable.\n"
            "• Use 'Load Sample Campus Project' from File menu to explore a full campus.",
            self.empty_card,
        )
        empty_desc.setStyleSheet("color: #CBD5E1; font-size: 12px;")
        empty_layout.addWidget(empty_title)
        empty_layout.addWidget(empty_desc)
        self.empty_card.adjustSize()

        # Right Contextual Inspector Stack
        self.inspector_stack = QStackedWidget(self)
        self.inspector_stack.setMinimumWidth(290)
        self.inspector_stack.setMaximumWidth(420)

        placeholder = QLabel(
            "Select a Component or Cable on the canvas\n"
            "to inspect ports, VLANs, attributes, or bundles.",
            self,
        )
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder.setStyleSheet("color: #94A3B8; padding: 16px;")
        self.component_inspector = ComponentInspector(self._state, self)
        self.cable_inspector = CableInspector(self._state, self)

        self.inspector_stack.addWidget(placeholder)
        self.inspector_stack.addWidget(self.component_inspector)
        self.inspector_stack.addWidget(self.cable_inspector)

        self.splitter.addWidget(self.palette)
        self.splitter.addWidget(self.canvas_host)
        self.splitter.addWidget(self.inspector_stack)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)
        root.addWidget(self.splitter, 1)

        # Bottom Connection Trace Bar
        trace_bar = QFrame(self)
        trace_bar.setStyleSheet(
            "background-color: #0F172A; border-top: 1px solid #334155; padding: 4px 10px;"
        )
        trace_row = QHBoxLayout(trace_bar)
        trace_row.setContentsMargins(6, 3, 6, 3)
        trace_prefix = QLabel("TRACE:", trace_bar)
        trace_prefix.setStyleSheet("color: #38BDF8; font-weight: 700; font-size: 11px;")
        self.trace_label = QLabel(
            "Hover any port, cable, or bundle to trace its end-to-end connection", trace_bar
        )
        self.trace_label.setStyleSheet("color: #CBD5E1; font-size: 12px;")
        trace_row.addWidget(trace_prefix)
        trace_row.addWidget(self.trace_label, 1)
        root.addWidget(trace_bar)

        # Keyboard shortcuts
        QShortcut(QKeySequence("V"), self, activated=lambda: self._set_tool_mode(False))
        QShortcut(QKeySequence("H"), self, activated=lambda: self._set_tool_mode(True))
        QShortcut(QKeySequence("F"), self, activated=lambda: self.canvas.fit_view(False))
        QShortcut(QKeySequence("Shift+F"), self, activated=lambda: self.canvas.fit_view(True))
        QShortcut(QKeySequence("M"), self, activated=self.minimap_btn.toggle)
        QShortcut(QKeySequence.StandardKey.Copy, self, activated=self.canvas.copy_selected)
        QShortcut(QKeySequence.StandardKey.Paste, self, activated=self.canvas.paste_clipboard)

    def _wire_signals(self) -> None:
        self.opacity_slider.valueChanged.connect(self._on_opacity_changed)
        self.auto_layout_btn.clicked.connect(self.canvas.auto_layout_hierarchy)
        self.bundle_btn.clicked.connect(self.canvas.bundle_selected_cables)
        self.unbundle_btn.clicked.connect(self.canvas.unbundle_selected)
        self.ortho_btn.clicked.connect(self.canvas.auto_orthogonal_selected)
        self.fit_btn.clicked.connect(lambda: self.canvas.fit_view(False))
        self.minimap_btn.toggled.connect(self.minimap.setVisible)

        self.palette.template_activated.connect(self.canvas.spawn_template)
        self.palette.new_template_requested.connect(self._open_new_template_dialog)
        self.palette.manage_templates_requested.connect(self._open_manage_templates_dialog)

        self.canvas.connection_trace_changed.connect(self.trace_label.setText)
        self.canvas.media_mismatch_warning.connect(self.show_amber_toast)
        self.canvas.viewport_resized.connect(self._reposition_overlays)
        self.component_inspector.media_mismatch_warning.connect(self.show_amber_toast)
        self.component_inspector.jump_to_component_requested.connect(
            self.canvas.jump_to_and_pulse
        )

        self._state.selection_changed.connect(self._on_state_selection_changed)
        self._state.components_changed.connect(self._refresh_empty_state)
        self._state.project_reloaded.connect(self._refresh_empty_state)

    def _set_tool_mode(self, hand: bool) -> None:
        self.select_tool_btn.setChecked(not hand)
        self.pan_tool_btn.setChecked(hand)
        self.canvas.set_hand_tool(hand)

    def _on_opacity_changed(self, value: int) -> None:
        self.canvas.set_node_alpha(value)
        self.opacity_pct_label.setText(f" {round(value / 255.0 * 100)}% ")
        self._config.node_opacity_alpha = value
        save_config(self._config)

    def show_amber_toast(self, message: str, duration_ms: int = 5500) -> None:
        self.toast_label.setText(message)
        self.toast_banner.adjustSize()
        self._reposition_overlays()
        self.toast_banner.show()
        self._toast_timer.start(duration_ms)

    def _on_state_selection_changed(self, entity_type: str, entity_id: str) -> None:
        if entity_type == "component" and entity_id:
            self.component_inspector.set_component(entity_id)
            self.inspector_stack.setCurrentIndex(1)
        elif entity_type == "cable" and entity_id:
            self.cable_inspector.set_cable(entity_id)
            self.inspector_stack.setCurrentIndex(2)
        elif not entity_id:
            self.inspector_stack.setCurrentIndex(0)

    def _open_new_template_dialog(self) -> None:
        from shared.models import ComponentType, PortSpec

        draft = ComponentTemplate(
            id=new_id(),
            template_name="New Custom Template",
            component_type=ComponentType.ACTIVE_SWITCH,
            category="Custom",
            default_name_prefix="SW",
            port_specs=[PortSpec(label="P1"), PortSpec(label="P2")],
        )
        dlg = TemplateManagerDialog(
            self._state, initial_template=draft, initial_scope="global", parent=self
        )
        dlg.exec()

    def _open_manage_templates_dialog(self) -> None:
        dlg = TemplateManagerDialog(self._state, parent=self)
        dlg.exec()

    def _refresh_empty_state(self) -> None:
        is_empty = len(self._state.project.components) == 0
        self.empty_card.setVisible(is_empty)
        self._reposition_overlays()

    def _reposition_overlays(self) -> None:
        vp = self.canvas.viewport()
        margin = 14
        self.minimap.move(
            max(margin, vp.width() - self.minimap.width() - margin),
            max(margin, vp.height() - self.minimap.height() - margin),
        )
        if self.toast_banner.isVisible():
            tw = min(vp.width() - 40, max(360, self.toast_banner.sizeHint().width()))
            self.toast_banner.setFixedWidth(tw)
            self.toast_banner.move((vp.width() - tw) // 2, 12)
        if self.empty_card.isVisible():
            self.empty_card.adjustSize()
            self.empty_card.move(
                max(10, (vp.width() - self.empty_card.width()) // 2),
                max(10, (vp.height() - self.empty_card.height()) // 2),
            )

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._reposition_overlays()
