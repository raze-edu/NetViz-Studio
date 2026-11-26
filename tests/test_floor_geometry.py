"""Phase 2 tests: desktop coords and Shapely floor geometry / snapping / doors."""

from __future__ import annotations

import math

import pytest
from PySide6.QtCore import QPointF, QRectF
from shapely.geometry import Polygon

from desktop.coords import (
    PIXELS_PER_METER,
    bounds_m_to_scene_rect,
    local_m_to_scene,
    scene_to_local_m,
)
from desktop.floor_geometry import (
    clip_and_cut_room_polygon,
    project_point_to_walls,
    reconcile_doors_after_wall_change,
    snap_editor_point,
)
from shared.models import Door, Floor, Point2D, Room


def test_desktop_coords_transform_and_y_inversion() -> None:
    assert PIXELS_PER_METER == 40.0
    pt = Point2D(10.0, 2.0)
    scene_pt = local_m_to_scene(pt)
    assert scene_pt == QPointF(400.0, -80.0)
    back = scene_to_local_m(scene_pt)
    assert back.x == pytest.approx(10.0)
    assert back.y == pytest.approx(2.0)

    rect = bounds_m_to_scene_rect(0.0, 0.0, 20.0, 10.0)
    assert rect == QRectF(0.0, -400.0, 800.0, 400.0)


def test_snap_editor_point_priorities_and_view_rotation() -> None:
    floor = Floor(
        name="GF",
        outer_polygon_m=[
            Point2D(0.0, 0.0),
            Point2D(20.0, 0.0),
            Point2D(20.0, 10.0),
            Point2D(0.0, 10.0),
        ],
        rooms=[
            Room(
                name="Room A",
                polygon_m=[
                    Point2D(0.0, 0.0),
                    Point2D(8.0, 0.0),
                    Point2D(8.0, 6.0),
                    Point2D(0.0, 6.0),
                ],
            )
        ],
    )

    # 1. Vertex snap (within 0.35 m) beats wall & grid
    res_v = snap_editor_point(Point2D(8.18, 5.85), floor)
    assert res_v.kind == "vertex"
    assert res_v.point_m == Point2D(8.0, 6.0)

    # 2. Wall segment projection (within 0.35 m) beats align & grid
    res_w = snap_editor_point(Point2D(4.13, 5.80), floor)
    assert res_w.kind == "wall"
    assert res_w.point_m.x == pytest.approx(4.13)
    assert res_w.point_m.y == pytest.approx(6.0)

    # 3. H/V alignment guide (within 0.25 m) beats grid
    res_a = snap_editor_point(Point2D(8.12, 8.33), floor)
    assert res_a.kind == "align"
    assert res_a.point_m.x == pytest.approx(8.0)
    assert res_a.guide_points is not None

    # 4. 0.25 m grid fallback
    res_g = snap_editor_point(Point2D(12.11, 3.38), floor)
    assert res_g.kind == "grid"
    assert res_g.point_m.x == pytest.approx(12.0)
    assert res_g.point_m.y == pytest.approx(3.5)

    # 5. Shift 45-degree angle lock relative to view_rotation_deg=35 deg
    anchor = Point2D(10.0, 5.0)
    theta = math.radians(35.0)
    # Move 4.0m along screen-right (u-axis rotated 35 deg) plus small perpendicular noise
    raw_u_plus = Point2D(
        anchor.x + 4.0 * math.cos(theta) - 0.3 * math.sin(theta),
        anchor.y + 4.0 * math.sin(theta) + 0.3 * math.cos(theta),
    )
    res_shift = snap_editor_point(
        raw_u_plus,
        floor,
        view_rotation_deg=35.0,
        anchor_pt_m=anchor,
        shift_lock=True,
    )
    dx = res_shift.point_m.x - anchor.x
    dy = res_shift.point_m.y - anchor.y
    # Verify vector aligns with view u-axis (0 deg in view coords)
    du = dx * math.cos(theta) + dy * math.sin(theta)
    dv = -dx * math.sin(theta) + dy * math.cos(theta)
    assert du > 3.5
    assert abs(dv) < 1e-6


def test_clip_and_cut_room_polygon_flush_and_sliver_rejection() -> None:
    floor = Floor(
        name="GF",
        outer_polygon_m=[
            Point2D(0.0, 0.0),
            Point2D(20.0, 0.0),
            Point2D(20.0, 10.0),
            Point2D(0.0, 10.0),
        ],
        rooms=[
            Room(
                id="r-1",
                name="Existing",
                polygon_m=[
                    Point2D(0.0, 0.0),
                    Point2D(8.0, 0.0),
                    Point2D(8.0, 8.0),
                    Point2D(0.0, 8.0),
                ],
            )
        ],
    )

    # Draw a room overlapping Existing (x=0..8) AND extending beyond outer wall (x=25, y=14)
    raw_new = [
        Point2D(4.0, 0.0),
        Point2D(25.0, 0.0),
        Point2D(25.0, 14.0),
        Point2D(4.0, 14.0),
    ]
    cut_pts = clip_and_cut_room_polygon(raw_new, floor)
    assert cut_pts is not None

    new_poly = Polygon([(p.x, p.y) for p in cut_pts])
    outer_poly = Polygon([(p.x, p.y) for p in floor.outer_polygon_m])
    existing_poly = Polygon([(p.x, p.y) for p in floor.rooms[0].polygon_m])

    # Must stay inside outer_polygon_m and have zero overlap with Existing room
    assert new_poly.difference(outer_poly).area < 1e-6
    assert new_poly.intersection(existing_poly).area < 1e-6
    # Expected area: (20*10) - (8*8) - (4*2)? Wait: x=4..8, y=8..10 is NOT in Existing!
    # So remaining area = (20-4)*10 - (8-4)*8 = 160 - 32 = 128 m^2
    assert new_poly.area == pytest.approx(128.0, abs=1e-2)

    # Sliver (< 0.25 m^2) is rejected
    sliver = [Point2D(9.0, 1.0), Point2D(9.2, 1.0), Point2D(9.2, 1.5), Point2D(9.0, 1.5)]
    assert clip_and_cut_room_polygon(sliver, floor) is None

    # Editing existing room with exclude_room_id doesn't cut against itself
    edited = clip_and_cut_room_polygon(
        [Point2D(0, 0), Point2D(9, 0), Point2D(9, 8), Point2D(0, 8)],
        floor,
        exclude_room_id="r-1",
    )
    assert edited is not None
    assert Polygon([(p.x, p.y) for p in edited]).area == pytest.approx(72.0, abs=1e-2)


def test_wall_locked_doors_and_reconciliation() -> None:
    floor = Floor(
        name="GF",
        outer_polygon_m=[
            Point2D(0.0, 0.0),
            Point2D(20.0, 0.0),
            Point2D(20.0, 10.0),
            Point2D(0.0, 10.0),
        ],
        rooms=[
            Room(
                name="Office",
                polygon_m=[
                    Point2D(0.0, 0.0),
                    Point2D(8.0, 0.0),
                    Point2D(8.0, 6.0),
                    Point2D(0.0, 6.0),
                ],
            )
        ],
    )

    # Open space rejected
    assert project_point_to_walls(Point2D(14.0, 5.0), floor, max_dist_m=1.5) is None

    # Vertical room wall hit -> angle 90 deg
    hit_v = project_point_to_walls(Point2D(8.4, 3.0), floor, max_dist_m=1.5)
    assert hit_v is not None
    assert hit_v.position_m.x == pytest.approx(8.0)
    assert hit_v.position_m.y == pytest.approx(3.0)
    assert hit_v.angle_deg == pytest.approx(90.0)

    # Horizontal wall hit -> angle 0 deg
    hit_h = project_point_to_walls(Point2D(4.0, 6.2), floor, max_dist_m=1.5)
    assert hit_h is not None
    assert hit_h.position_m.y == pytest.approx(6.0)
    assert hit_h.angle_deg == pytest.approx(0.0)

    # Reconcile doors after wall edit: one door within 0.5m re-snaps, one orphaned door removed
    d_keep = Door(id="d-keep", position_m=Point2D(8.25, 3.0), angle_deg=0.0)
    d_orphan = Door(id="d-orphan", position_m=Point2D(13.0, 5.0), angle_deg=0.0)
    floor.doors = [d_keep, d_orphan]

    removed = reconcile_doors_after_wall_change(floor, max_dist_m=0.5)
    assert [d.id for d in removed] == ["d-orphan"]
    assert [d.id for d in floor.doors] == ["d-keep"]
    assert floor.doors[0].position_m.x == pytest.approx(8.0)
    assert floor.doors[0].angle_deg == pytest.approx(90.0)
