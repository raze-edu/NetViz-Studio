"""Single desktop coordinate transform: local ENU meters <-> Qt scene pixels."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF

from shared.models import Point2D

PIXELS_PER_METER = 40.0


def local_m_to_scene(pt: Point2D) -> QPointF:
    """Convert building-local ENU meters (+Y North/Up) to Qt scene pixels (+Y Down)."""
    return QPointF(pt.x * PIXELS_PER_METER, -pt.y * PIXELS_PER_METER)


def scene_to_local_m(qpt: QPointF) -> Point2D:
    """Convert Qt scene pixels (+Y Down) to building-local ENU meters (+Y North/Up)."""
    return Point2D(qpt.x() / PIXELS_PER_METER, -qpt.y() / PIXELS_PER_METER)


def bounds_m_to_scene_rect(
    min_x: float,
    min_y: float,
    max_x: float,
    max_y: float,
) -> QRectF:
    """Convert metric (min_x, min_y, max_x, max_y) bounds to a Qt scene QRectF."""
    return QRectF(
        min_x * PIXELS_PER_METER,
        -max_y * PIXELS_PER_METER,
        (max_x - min_x) * PIXELS_PER_METER,
        (max_y - min_y) * PIXELS_PER_METER,
    )
