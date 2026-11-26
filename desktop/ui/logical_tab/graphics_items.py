"""QGraphicsItems for Tab 1 Logical Network Visualizer with strict Section 4.4 Z-stacking."""

from __future__ import annotations

import math
from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPen,
)
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsPathItem,
    QStyleOptionGraphicsItem,
    QWidget,
)

from desktop.ui.logical_tab.routing_controller import (
    choose_bundle_entry_exit,
    estimate_node_size,
    offset_polyline_by_normal,
)
from desktop.ui.theme import (
    DEFAULT_NODE_ALPHA,
    component_color,
    node_fill_color,
    port_color,
)
from shared.models import Cable, CableBundle, Component, LinePattern, Point2D, Port

Z_GRID = -100.0
Z_CABLE = 10.0
Z_NODE = 20.0
Z_PORT = 40.0
Z_PORT_PILL = 50.0
Z_CABLE_ACTIVE = 60.0
Z_PREVIEW = 65.0
Z_HANDLE = 70.0


def qt_pen_style(pattern: LinePattern) -> Qt.PenStyle:
    """Convert LinePattern enum to Qt.PenStyle."""
    if pattern == LinePattern.DASHED:
        return Qt.PenStyle.DashLine
    if pattern == LinePattern.DOTTED:
        return Qt.PenStyle.DotLine
    if pattern == LinePattern.DASH_DOT:
        return Qt.PenStyle.DashDotLine
    return Qt.PenStyle.SolidLine


class PortAnchorItem(QGraphicsItem):
    """Color-coded port pin + label anchored onto a ComponentNodeItem (z = 40)."""

    def __init__(
        self,
        component_id: str,
        port: Port,
        *,
        occupied: bool = False,
        label_above: bool = True,
        parent: QGraphicsItem | None = None,
    ) -> None:
        super().__init__(parent)
        self.component_id = component_id
        self.port = port
        self.port_id = port.id
        self.occupied = occupied
        self.label_above = label_above
        self.pulsed = False
        self.dimmed = False

        self.setZValue(Z_PORT)
        # LogicalCanvasView owns all port click/drag events centrally (Section 4.4B).
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setAcceptHoverEvents(False)

        self._font = QFont("Segoe UI", 7)
        self._font.setBold(True)
        self._pin_radius = 5.0
        self._label_rect = QRectF(-12.0, -19.0 if label_above else 7.0, 24.0, 12.0)

    @property
    def key(self) -> tuple[str, str]:
        return (self.component_id, self.port_id)

    def set_occupied(self, occupied: bool) -> None:
        if self.occupied != occupied:
            self.occupied = occupied
            self.update()

    def set_pulsed(self, pulsed: bool) -> None:
        if self.pulsed != pulsed:
            self.pulsed = pulsed
            self.update()

    def pin_scene_pos(self) -> QPointF:
        """Return exact center of the port pin in scene coordinates."""
        return self.mapToScene(QPointF(0.0, 0.0))

    def label_scene_rect(self) -> QRectF:
        """Return tight scene bounding rectangle covering pin and port label."""
        return self.mapRectToScene(QRectF(-12.0, -19.0, 24.0, 26.0))

    def boundingRect(self) -> QRectF:
        return QRectF(-18.0, -22.0, 36.0, 44.0)

    def paint(
        self,
        painter: QPainter,
        _option: QStyleOptionGraphicsItem,
        _widget: QWidget | None = None,
    ) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.dimmed:
            painter.setOpacity(0.30)

        col = port_color(self.port.port_type)
        if self.pulsed:
            halo_col = QColor(col.red(), col.green(), col.blue(), 120)
            painter.setPen(QPen(QColor("#F8FAFC"), 1.6))
            painter.setBrush(halo_col)
            painter.drawEllipse(QPointF(0.0, 0.0), 9.5, 9.5)
        elif self.occupied:
            halo_col = QColor(col.red(), col.green(), col.blue(), 70)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(halo_col)
            painter.drawEllipse(QPointF(0.0, 0.0), 8.2, 8.2)

        if self.occupied:
            painter.setPen(QPen(QColor("#0F172A"), 1.2))
            painter.setBrush(col)
            painter.drawEllipse(QPointF(0.0, 0.0), self._pin_radius, self._pin_radius)
        else:
            painter.setPen(QPen(col, 2.0))
            painter.setBrush(QColor(15, 23, 42, 180))
            painter.drawEllipse(QPointF(0.0, 0.0), self._pin_radius, self._pin_radius)

        # Crisp port label
        painter.setFont(self._font)
        painter.setPen(QColor("#CBD5E1") if not self.pulsed else QColor("#F8FAFC"))
        short_lbl = self.port.label if len(self.port.label) <= 7 else self.port.label[:6] + "…"
        y_off = -19.0 if self.label_above else 7.0
        self._label_rect = QRectF(-17.0, y_off, 34.0, 12.0)
        painter.drawText(self._label_rect, Qt.AlignmentFlag.AlignCenter, short_lbl)
        painter.restore()


class ComponentNodeItem(QGraphicsItem):
    """Translucent component card (z = 20) with opaque border, accent strip, and badge."""

    def __init__(
        self,
        component: Component,
        *,
        alpha: int = DEFAULT_NODE_ALPHA,
        occupied_port_ids: set[str] | None = None,
        accent_override: str | None = None,
        on_moved_live: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self.component = component
        self._alpha = alpha
        self._occupied_port_ids = set(occupied_port_ids or set())
        self._accent_override = accent_override
        self._on_moved_live = on_moved_live
        self._pulsed = False
        self._dimmed = False

        self.width_px, self.height_px = estimate_node_size(component)
        self.port_items: dict[str, PortAnchorItem] = {}

        self.setZValue(Z_NODE)
        self.setPos(component.logical_pos.x, component.logical_pos.y)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)

        self._build_ports()

    def _build_ports(self) -> None:
        ports = self.component.ports
        n = len(ports)
        if n == 0:
            return
        if n <= 12:
            pitch = max(28.0, (self.width_px - 28.0) / max(1, n))
            start_x = 0.5 * (self.width_px - (n - 1) * pitch)
            y_pin = self.height_px - 10.0
            for idx, port in enumerate(ports):
                p_item = PortAnchorItem(
                    self.component.id,
                    port,
                    occupied=port.id in self._occupied_port_ids,
                    label_above=True,
                    parent=self,
                )
                p_item.setPos(start_x + idx * pitch, y_pin)
                self.port_items[port.id] = p_item
        else:
            cols = math.ceil(n / 2.0)
            pitch = max(28.0, (self.width_px - 28.0) / max(1, cols))
            start_x = 0.5 * (self.width_px - (cols - 1) * pitch)
            top_row_y = self.height_px - 44.0
            bot_row_y = self.height_px - 12.0
            for idx, port in enumerate(ports):
                row = 0 if idx < cols else 1
                col = idx if row == 0 else idx - cols
                y_pin = top_row_y if row == 0 else bot_row_y
                p_item = PortAnchorItem(
                    self.component.id,
                    port,
                    occupied=port.id in self._occupied_port_ids,
                    label_above=True,
                    parent=self,
                )
                p_item.setPos(start_x + col * pitch, y_pin)
                self.port_items[port.id] = p_item

    @property
    def node_alpha(self) -> int:
        return self._alpha

    def set_node_alpha(self, alpha: int) -> None:
        """Live-update translucent node body alpha without affecting border/text/ports."""
        clamped = max(77, min(242, int(alpha)))
        if self._alpha != clamped:
            self._alpha = clamped
            self.update()

    def set_occupied_ports(self, occupied_ids: set[str]) -> None:
        self._occupied_port_ids = set(occupied_ids)
        for pid, p_item in self.port_items.items():
            p_item.set_occupied(pid in self._occupied_port_ids)
        self.update()

    def set_dimmed(self, dimmed: bool) -> None:
        if self._dimmed != dimmed:
            self._dimmed = dimmed
            for p_item in self.port_items.values():
                p_item.dimmed = dimmed
            self.update()

    def set_pulsed(self, pulsed: bool) -> None:
        if self._pulsed != pulsed:
            self._pulsed = pulsed
            self.update()

    def boundingRect(self) -> QRectF:
        return QRectF(-4.0, -4.0, self.width_px + 8.0, self.height_px + 8.0)

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value: object) -> object:
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            if self._on_moved_live is not None:
                self._on_moved_live()
        return super().itemChange(change, value)

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

        rect = QRectF(0.0, 0.0, self.width_px, self.height_px)
        accent = component_color(self.component.component_type, self._accent_override)

        # Jump/trace pulse halo
        if self._pulsed:
            painter.setPen(QPen(QColor("#38BDF8"), 3.0))
            painter.setBrush(QColor(56, 189, 248, 35))
            painter.drawRoundedRect(rect.adjusted(-4, -4, 4, 4), 10, 10)

        # Translucent body fill: QColor(30, 41, 59, alpha)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(node_fill_color(self._alpha))
        painter.drawRoundedRect(rect, 8, 8)

        # 100% opaque accent header strip
        header_rect = QRectF(0.0, 0.0, self.width_px, 6.0)
        painter.setBrush(accent)
        painter.drawRoundedRect(header_rect, 4, 4)
        painter.fillRect(QRectF(0.0, 3.0, self.width_px, 3.0), accent)

        # 100% opaque 1.5 px border
        border_col = QColor("#38BDF8") if self.isSelected() else accent
        border_w = 2.2 if self.isSelected() else 1.5
        painter.setPen(QPen(border_col, border_w))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, 8, 8)

        # 100% opaque Component Name label (#F8FAFC)
        title_font = QFont("Segoe UI", 10)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.setPen(QColor("#F8FAFC"))
        painter.drawText(
            QRectF(10.0, 10.0, self.width_px - 68.0, 20.0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self.component.name,
        )

        # Subtitle (IP / type)
        sub_font = QFont("Segoe UI", 8)
        painter.setFont(sub_font)
        painter.setPen(QColor("#94A3B8"))
        type_str = self.component.component_type.value.replace("_", " ").title()
        subtitle = self.component.ip_address.strip() or type_str
        painter.drawText(
            QRectF(10.0, 28.0, self.width_px - 20.0, 16.0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            subtitle,
        )

        # 100% opaque Utilization Badge ("4/26")
        used_count = sum(1 for p in self.component.ports if p.id in self._occupied_port_ids)
        badge_text = f"{used_count}/{len(self.component.ports)}"
        badge_font = QFont("Segoe UI", 8)
        badge_font.setBold(True)
        painter.setFont(badge_font)
        fm = QFontMetricsF(badge_font)
        badge_w = max(36.0, fm.horizontalAdvance(badge_text) + 12.0)
        badge_rect = QRectF(self.width_px - badge_w - 8.0, 10.0, badge_w, 18.0)
        painter.setPen(QPen(accent, 1.0))
        painter.setBrush(QColor("#0F172A"))
        painter.drawRoundedRect(badge_rect, 4, 4)
        painter.setPen(QColor("#F8FAFC"))
        painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, badge_text)

        painter.restore()


class CablePortPillItem(QGraphicsItem):
    """Endpoint port pill ('[eth0]', '[SFP+1]') placed 18 px from port pin along cable (z = 50)."""

    def __init__(self, label: str, accent_color: QColor) -> None:
        super().__init__()
        self.label = f"[{label}]"
        self.accent_color = accent_color
        self._highlighted = False
        self._dimmed = False
        self.setZValue(Z_PORT_PILL)
        self.setEnabled(False)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

        self._font = QFont("Segoe UI", 7)
        self._font.setBold(True)
        fm = QFontMetricsF(self._font)
        w = max(28.0, fm.horizontalAdvance(self.label) + 10.0)
        self._rect = QRectF(-0.5 * w, -8.5, w, 17.0)

    def set_highlighted(self, highlighted: bool) -> None:
        if self._highlighted != highlighted:
            self._highlighted = highlighted
            self.update()

    def set_dimmed(self, dimmed: bool) -> None:
        if self._dimmed != dimmed:
            self._dimmed = dimmed
            self.update()

    def boundingRect(self) -> QRectF:
        return self._rect.adjusted(-2, -2, 2, 2)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        _widget: QWidget | None = None,
    ) -> None:
        lod = option.levelOfDetailFromTransform(painter.worldTransform())
        if lod < 0.55 and not self._highlighted:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._highlighted:
            painter.setOpacity(1.0)
        elif self._dimmed:
            painter.setOpacity(0.30)
        else:
            painter.setOpacity(0.92)

        painter.setPen(QPen(self.accent_color, 1.1))
        painter.setBrush(QColor(11, 17, 32, 235))
        painter.drawRoundedRect(self._rect, 4, 4)

        painter.setFont(self._font)
        painter.setPen(QColor("#F8FAFC"))
        painter.drawText(self._rect, Qt.AlignmentFlag.AlignCenter, self.label)
        painter.restore()


class WaypointHandle(QGraphicsEllipseItem):
    """Draggable handle for cable/bundle waypoints or bundle entry/exit points (z = 70)."""

    def __init__(
        self,
        pos: QPointF,
        *,
        kind: str,
        owner_id: str,
        index: int = -1,
        color_hex: str = "#38BDF8",
        on_moved: Callable[[str, str, int, QPointF], None] | None = None,
        on_released: Callable[[str, str, int, QPointF], None] | None = None,
        on_delete: Callable[[str, str, int], None] | None = None,
    ) -> None:
        super().__init__(-5.5, -5.5, 11.0, 11.0)
        self.kind = kind  # "cable_wp" | "bundle_wp" | "bundle_entry" | "bundle_exit"
        self.owner_id = owner_id
        self.index = index
        self._on_moved = on_moved
        self._on_released = on_released
        self._on_delete = on_delete

        self.setPos(pos)
        self.setZValue(Z_HANDLE)
        self.setPen(QPen(QColor("#0F172A"), 1.5))
        self.setBrush(QBrush(QColor(color_hex)))
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setToolTip(
            "Drag to move · Right-click to delete waypoint"
            if kind in ("cable_wp", "bundle_wp")
            else "Drag to reposition bundle trunk endpoint"
        )

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value: object) -> object:
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            if self._on_moved is not None and isinstance(value, QPointF):
                self._on_moved(self.kind, self.owner_id, self.index, value)
        return super().itemChange(change, value)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        super().mouseReleaseEvent(event)
        if self._on_released is not None:
            self._on_released(self.kind, self.owner_id, self.index, self.pos())

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.RightButton:
            if self._on_delete is not None and self.kind in ("cable_wp", "bundle_wp"):
                self._on_delete(self.kind, self.owner_id, self.index)
            event.accept()
            return
        super().mousePressEvent(event)


def _point_along_cable(a: QPointF, b: QPointF, dist_px: float = 18.0) -> QPointF:
    dx = b.x() - a.x()
    dy = b.y() - a.y()
    length = math.hypot(dx, dy)
    if length < 1e-4:
        return QPointF(a.x(), a.y() + dist_px)
    step = min(dist_px, 0.4 * length)
    return QPointF(a.x() + (dx / length) * step, a.y() + (dy / length) * step)


class RoutableCableItem(QGraphicsPathItem):
    """Logical cable item (z = 10, lifts to z = 60 on hover/select/trace) + endpoint pills."""

    def __init__(
        self,
        cable: Cable,
        src_port_item: PortAnchorItem,
        dst_port_item: PortAnchorItem,
        *,
        bundle: CableBundle | None = None,
        bundle_member_index: int = 0,
        bundle_member_count: int = 1,
    ) -> None:
        super().__init__()
        self.cable = cable
        self.src_port_item = src_port_item
        self.dst_port_item = dst_port_item
        self.bundle = bundle
        self.bundle_member_index = bundle_member_index
        self.bundle_member_count = bundle_member_count

        self._hovered = False
        self._traced = False
        self._dimmed = False
        self._points: list[QPointF] = []

        self.setZValue(Z_CABLE)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)

        self.src_pill = CablePortPillItem(
            src_port_item.port.label, port_color(src_port_item.port.port_type)
        )
        self.dst_pill = CablePortPillItem(
            dst_port_item.port.label, port_color(dst_port_item.port.port_type)
        )
        self.rebuild_path()

    def set_bundle_info(
        self,
        bundle: CableBundle | None,
        member_index: int = 0,
        member_count: int = 1,
    ) -> None:
        self.bundle = bundle
        self.bundle_member_index = member_index
        self.bundle_member_count = member_count
        self.rebuild_path()

    def endpoints_scene(self) -> tuple[QPointF, QPointF]:
        return (self.src_port_item.pin_scene_pos(), self.dst_port_item.pin_scene_pos())

    def current_polyline(self) -> list[QPointF]:
        return list(self._points)

    def _compute_polyline_points(self) -> list[QPointF]:
        p_src = self.src_port_item.pin_scene_pos()
        p_dst = self.dst_port_item.pin_scene_pos()

        if self.bundle is None:
            pts = [p_src, *(QPointF(w.x, w.y) for w in self.cable.logical_waypoints), p_dst]
            return pts

        src_m = Point2D(p_src.x(), p_src.y())
        dst_m = Point2D(p_dst.x(), p_dst.y())
        anchor_src, anchor_dst, rev = choose_bundle_entry_exit(
            src_m, dst_m, self.bundle.entry_point, self.bundle.exit_point
        )
        trunk = [anchor_src]
        wps = list(reversed(self.bundle.waypoints)) if rev else list(self.bundle.waypoints)
        trunk.extend(wps)
        trunk.append(anchor_dst)

        if not self.bundle.collapsed and self.bundle_member_count > 1:
            offset_px = (self.bundle_member_index - (self.bundle_member_count - 1) / 2.0) * 5.0
            trunk = offset_polyline_by_normal(trunk, offset_px)

        return [p_src, *(QPointF(pt.x, pt.y) for pt in trunk), p_dst]

    def rebuild_path(self) -> None:
        pts = self._compute_polyline_points()
        self._points = pts
        path = QPainterPath()
        if pts:
            path.moveTo(pts[0])
            for pt in pts[1:]:
                path.lineTo(pt)
        self.setPath(path)

        if len(pts) >= 2:
            self.src_pill.setPos(_point_along_cable(pts[0], pts[1], 18.0))
            self.dst_pill.setPos(_point_along_cable(pts[-1], pts[-2], 18.0))

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
        self.dst_pill.set_highlighted(active)
        self.src_pill.set_dimmed(self._dimmed and not active)
        self.dst_pill.set_dimmed(self._dimmed and not active)
        self.update()

    def shape(self) -> QPainterPath:
        stroker = QPainterPathStroker()
        stroker.setWidth(max(12.0, self.cable.style.width_px + 8.0))
        return stroker.createStroke(self.path())

    def paint(
        self,
        painter: QPainter,
        _option: QStyleOptionGraphicsItem,
        _widget: QWidget | None = None,
    ) -> None:
        # When inside a collapsed bundle and not highlighted/traced, CableBundleItem draws trunk
        active = self._hovered or self._traced or self.isSelected()
        if self.bundle is not None and self.bundle.collapsed and not active:
            # Draw subtle fan-out stubs only
            if len(self._points) >= 4:
                painter.save()
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                if self._dimmed:
                    painter.setOpacity(0.30)
                pen = QPen(
                    QColor(self.cable.style.color_hex),
                    max(1.5, self.cable.style.width_px * 0.8),
                    qt_pen_style(self.cable.style.pattern),
                )
                painter.setPen(pen)
                painter.drawLine(self._points[0], self._points[1])
                painter.drawLine(self._points[-2], self._points[-1])
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
                QColor(base_color.red(), base_color.green(), base_color.blue(), 110),
                self.cable.style.width_px + 5.0,
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


class CableBundleItem(QGraphicsPathItem):
    """Visual trunk item for a logical CableBundle (z = 10, lifts to z = 60 when active)."""

    def __init__(
        self,
        bundle: CableBundle,
        tooltip_text: str = "",
    ) -> None:
        super().__init__()
        self.bundle = bundle
        self._hovered = False
        self._dimmed = False
        self._tooltip_text = tooltip_text

        self.setZValue(Z_CABLE)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)
        self.setToolTip(tooltip_text)
        self.rebuild_path()

    def set_tooltip_text(self, text: str) -> None:
        self._tooltip_text = text
        self.setToolTip(text)

    def rebuild_path(self) -> None:
        pts = [
            QPointF(self.bundle.entry_point.x, self.bundle.entry_point.y),
            *(QPointF(w.x, w.y) for w in self.bundle.waypoints),
            QPointF(self.bundle.exit_point.x, self.bundle.exit_point.y),
        ]
        path = QPainterPath()
        if pts:
            path.moveTo(pts[0])
            for pt in pts[1:]:
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
            # Expanded guide sleeve beneath parallel offset member wires
            guide_pen = QPen(
                QColor(col.red(), col.green(), col.blue(), 80), 1.5, Qt.PenStyle.DashLine
            )
            painter.setPen(guide_pen)
            painter.drawPath(self.path())

        # Midpoint "<name> (N)" pill
        path = self.path()
        if not path.isEmpty():
            mid = path.pointAtPercent(0.5)
            badge = f"{self.bundle.name} ({len(self.bundle.member_cable_ids)})"
            font = QFont("Segoe UI", 8)
            font.setBold(True)
            painter.setFont(font)
            fm = QFontMetricsF(font)
            bw = max(60.0, fm.horizontalAdvance(badge) + 14.0)
            brect = QRectF(mid.x() - 0.5 * bw, mid.y() - 10.0, bw, 20.0)
            painter.setPen(QPen(col, 1.2))
            painter.setBrush(QColor(15, 23, 42, 235))
            painter.drawRoundedRect(brect, 5, 5)
            painter.setPen(QColor("#F8FAFC"))
            painter.drawText(brect, Qt.AlignmentFlag.AlignCenter, badge)

        painter.restore()
