"""Floating interactive corner viewport minimap overlay for QGraphicsView."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QGraphicsView, QWidget

from desktop.ui.theme import component_color


class MiniMapWidget(QWidget):
    """Floating interactive corner minimap overlay tracking & panning a QGraphicsView."""

    def __init__(
        self,
        view: QGraphicsView,
        parent: QWidget | None = None,
        *,
        width_px: int = 190,
        height_px: int = 135,
    ) -> None:
        super().__init__(parent or view)
        self._view = view
        self._dragging = False
        self._world_rect = QRectF(-200.0, -200.0, 1200.0, 800.0)
        self.setFixedSize(width_px, height_px)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Mini-Map (M to toggle) — Click or drag to pan viewport")

        self._view.horizontalScrollBar().valueChanged.connect(self.update)
        self._view.verticalScrollBar().valueChanged.connect(self.update)
        if self._view.scene() is not None:
            self._view.scene().changed.connect(self.update)

    def attach_scene_signals(self) -> None:
        """Connect to the current scene's changed signal if replaced."""
        if self._view.scene() is not None:
            self._view.scene().changed.connect(self.update)

    def _compute_world_rect(self) -> QRectF:
        scene = self._view.scene()
        if scene is None:
            return QRectF(-200.0, -200.0, 1200.0, 800.0)
        items_rect = scene.itemsBoundingRect()
        vp_rect = self._view.mapToScene(self._view.viewport().rect()).boundingRect()
        if items_rect.isNull() or items_rect.isEmpty():
            combined = vp_rect
        else:
            combined = items_rect.united(vp_rect)
        if combined.isNull() or combined.width() < 10.0 or combined.height() < 10.0:
            combined = QRectF(-200.0, -200.0, 1200.0, 800.0)
        margin = max(80.0, 0.12 * max(combined.width(), combined.height()))
        return combined.adjusted(-margin, -margin, margin, margin)

    def _scene_to_mini(self, pt: QPointF, world: QRectF) -> QPointF:
        pad = 8.0
        avail_w = max(1.0, self.width() - 2.0 * pad)
        avail_h = max(1.0, self.height() - 2.0 * pad)
        scale = min(avail_w / max(world.width(), 1.0), avail_h / max(world.height(), 1.0))
        ox = pad + (avail_w - world.width() * scale) * 0.5
        oy = pad + (avail_h - world.height() * scale) * 0.5
        return QPointF(
            ox + (pt.x() - world.left()) * scale,
            oy + (pt.y() - world.top()) * scale,
        )

    def _mini_to_scene(self, pos: QPointF, world: QRectF) -> QPointF:
        pad = 8.0
        avail_w = max(1.0, self.width() - 2.0 * pad)
        avail_h = max(1.0, self.height() - 2.0 * pad)
        scale = min(avail_w / max(world.width(), 1.0), avail_h / max(world.height(), 1.0))
        ox = pad + (avail_w - world.width() * scale) * 0.5
        oy = pad + (avail_h - world.height() * scale) * 0.5
        return QPointF(
            world.left() + (pos.x() - ox) / max(scale, 1e-6),
            world.top() + (pos.y() - oy) / max(scale, 1e-6),
        )

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Dark CAD backdrop
        painter.setPen(QPen(QColor("#334155"), 1.2))
        painter.setBrush(QColor(15, 23, 42, 220))
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 6, 6)

        scene = self._view.scene()
        if scene is None:
            return
        self._world_rect = self._compute_world_rect()

        # Draw simplified cables & component nodes
        for item in scene.items():
            if hasattr(item, "cable") and hasattr(item, "endpoints_scene"):
                p1, p2 = item.endpoints_scene()
                m1 = self._scene_to_mini(p1, self._world_rect)
                m2 = self._scene_to_mini(p2, self._world_rect)
                painter.setPen(QPen(QColor(item.cable.style.color_hex), 1.0))
                painter.drawLine(m1, m2)
            elif hasattr(item, "component") and hasattr(item, "sceneBoundingRect"):
                rect = item.sceneBoundingRect()
                tl = self._scene_to_mini(rect.topLeft(), self._world_rect)
                br = self._scene_to_mini(rect.bottomRight(), self._world_rect)
                col = component_color(item.component.component_type)
                painter.setPen(QPen(col, 1.0))
                painter.setBrush(QColor(col.red(), col.green(), col.blue(), 140))
                painter.drawRect(QRectF(tl, br))

        # Draw live viewport rectangle
        vp_scene = self._view.mapToScene(self._view.viewport().rect()).boundingRect()
        vtl = self._scene_to_mini(vp_scene.topLeft(), self._world_rect)
        vbr = self._scene_to_mini(vp_scene.bottomRight(), self._world_rect)
        vp_mini = QRectF(vtl, vbr).intersected(QRectF(self.rect()).adjusted(2, 2, -2, -2))
        painter.setPen(QPen(QColor("#38BDF8"), 1.5))
        painter.setBrush(QColor(56, 189, 248, 35))
        painter.drawRect(vp_mini)

    def _pan_to_pos(self, pos: QPointF) -> None:
        world = self._compute_world_rect()
        target = self._mini_to_scene(pos, world)
        self._view.centerOn(target)
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._pan_to_pos(event.position())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._dragging:
            self._pan_to_pos(event.position())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self._pan_to_pos(event.position())
            event.accept()
            return
        super().mouseReleaseEvent(event)
