"""Phase 6 pytest-qt interaction tests for Precision Multi-Floor CAD Editor (Section 4.6)."""

from __future__ import annotations

import io
import math
import time

import pytest
from PIL import Image
from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QGraphicsView
from shapely.geometry import Polygon

from desktop.coords import local_m_to_scene
from desktop.state import ProjectState
from desktop.ui.map_tab.floor_editor import FloorEditorView, FloorEditorWidget, ToolMode
from shared.geo_math import true_north_screen_cw_deg
from shared.models import (
    BlueprintUnderlay,
    Building,
    Floor,
    GeoAnchor,
    Point2D,
    Project,
    Room,
    VerticalHole,
)


def _sample_floor_state(*, anchor_rotation_deg: float = 20.0) -> tuple[ProjectState, str, str]:
    floor0 = Floor(
        name="Ground Floor",
        level_index=0,
        outer_polygon_m=[
            Point2D(0.0, 0.0),
            Point2D(20.0, 0.0),
            Point2D(20.0, 12.0),
            Point2D(0.0, 12.0),
        ],
    )
    floor1 = Floor(
        name="First Floor",
        level_index=1,
        outer_polygon_m=[
            Point2D(0.0, 0.0),
            Point2D(20.0, 0.0),
            Point2D(20.0, 12.0),
            Point2D(0.0, 12.0),
        ],
    )
    bldg = Building(
        name="HQ",
        geo_anchor=GeoAnchor(
            origin_lat=52.52,
            origin_lon=13.405,
            rotation_deg=anchor_rotation_deg,
        ),
        floors=[floor0, floor1],
    )
    proj = Project(name="CAD Test", buildings=[bldg])
    state = ProjectState(proj, autosave_enabled=False)
    return state, bldg.id, floor0.id


def _view_pt(view: FloorEditorView, pt_m: Point2D) -> QPointF:
    return QPointF(view.mapFromScene(local_m_to_scene(pt_m)))


def test_draw_room_left_clicks_draw_never_pan_and_safe_double_click(qtbot) -> None:
    state, bldg_id, floor_id = _sample_floor_state()
    try:
        editor = FloorEditorWidget(state)
        editor.resize(1000, 720)
        qtbot.addWidget(editor)
        editor.show()
        editor.set_floor(bldg_id, floor_id)
        editor.view.fit_floor_in_view()

        view = editor.view
        assert view.dragMode() != QGraphicsView.DragMode.ScrollHandDrag
        view.set_tool(ToolMode.DRAW_ROOM)
        assert view.dragMode() == QGraphicsView.DragMode.NoDrag

        h_before = view.horizontalScrollBar().value()
        v_before = view.verticalScrollBar().value()

        # Click 4 corners of a 6m x 4m room, finishing with double-click on the 4th corner
        corners = [
            Point2D(2.0, 2.0),
            Point2D(8.0, 2.0),
            Point2D(8.0, 6.0),
            Point2D(2.0, 6.0),
        ]
        for pt in corners:
            p = _view_pt(view, pt).toPoint()
            qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=p)

        # Viewport scrollbars MUST NOT have panned on left-clicks
        assert view.horizontalScrollBar().value() == h_before
        assert view.verticalScrollBar().value() == v_before
        assert len(view._draft_room_pts) == 4

        # Simulate double-click (duplicate press < 6 px + doubleClick + release)
        last_p = _view_pt(view, corners[-1]).toPoint()
        qtbot.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=last_p)
        qtbot.mouseDClick(view.viewport(), Qt.MouseButton.LeftButton, pos=last_p)
        qtbot.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=last_p)

        floor = state.project.get_floor(bldg_id, floor_id)
        assert floor is not None
        assert len(floor.rooms) == 1
        # Zero duplicate vertex from double-click!
        assert len(floor.rooms[0].polygon_m) == 4
        # Post-finish 250ms guard is active and room was NOT dragged after finish
        assert view._post_finish_guard_until > time.monotonic() - 0.05
    finally:
        state.cleanup()


def test_typed_metric_wall_length_places_exact_segment(qtbot) -> None:
    state, bldg_id, floor_id = _sample_floor_state()
    try:
        editor = FloorEditorWidget(state)
        editor.resize(1000, 720)
        qtbot.addWidget(editor)
        editor.show()
        editor.set_floor(bldg_id, floor_id)
        editor.view.fit_floor_in_view()

        view = editor.view
        view.set_tool(ToolMode.DRAW_ROOM)

        # Place vertex 0 at (2.0, 2.0)
        p0 = _view_pt(view, Point2D(2.0, 2.0)).toPoint()
        qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=p0)
        # Move cursor along +X direction (east)
        p_dir = _view_pt(view, Point2D(10.0, 2.0)).toPoint()
        qtbot.mouseMove(view.viewport(), pos=p_dir)

        # Type "4.5" and press Enter -> places exact 4.5 m segment!
        qtbot.keyClicks(view, "4.5")
        assert "4.5" in view.typed_length_hud_text()
        qtbot.keyClick(view, Qt.Key.Key_Return)

        assert len(view._draft_room_pts) == 2
        pt0, pt1 = view._draft_room_pts
        seg_len = math.hypot(pt1.x - pt0.x, pt1.y - pt0.y)
        assert seg_len == pytest.approx(4.5, abs=1e-4)
        assert pt1.x == pytest.approx(pt0.x + 4.5, abs=1e-4)
        assert pt1.y == pytest.approx(pt0.y, abs=1e-4)
    finally:
        state.cleanup()


def test_view_rotation_does_not_mutate_geo_anchor_and_compass_matches(qtbot) -> None:
    state, bldg_id, floor_id = _sample_floor_state(anchor_rotation_deg=20.0)
    try:
        editor = FloorEditorWidget(state)
        editor.resize(900, 650)
        qtbot.addWidget(editor)
        editor.show()
        editor.set_floor(bldg_id, floor_id)

        editor.view.set_view_rotation(35.0)
        floor = state.project.get_floor(bldg_id, floor_id)
        bldg = state.project.get_building(bldg_id)
        assert floor is not None and bldg is not None and bldg.geo_anchor is not None

        assert floor.editor_view_rotation_deg == pytest.approx(35.0)
        # Invariant #11: GeoAnchor.rotation_deg MUST remain untouched!
        assert bldg.geo_anchor.rotation_deg == pytest.approx(20.0)
        # Exact True North compass formula: (35 - 20) % 360 = 15 deg
        assert editor.view.true_north_angle_deg() == pytest.approx(
            true_north_screen_cw_deg(35.0, 20.0)
        )
        assert editor.view.true_north_angle_deg() == pytest.approx(15.0)
    finally:
        state.cleanup()


def test_room_corner_drag_and_overlap_auto_cuts_flush(qtbot) -> None:
    state, bldg_id, floor_id = _sample_floor_state()
    try:
        # Pre-seed Room A: [0..8] x [0..6]
        room_a = Room(
            name="Room A",
            polygon_m=[
                Point2D(0.0, 0.0),
                Point2D(8.0, 0.0),
                Point2D(8.0, 6.0),
                Point2D(0.0, 6.0),
            ],
        )
        # Pre-seed Room B: [10..16] x [0..6]
        room_b = Room(
            name="Room B",
            polygon_m=[
                Point2D(10.0, 0.0),
                Point2D(16.0, 0.0),
                Point2D(16.0, 6.0),
                Point2D(10.0, 6.0),
            ],
        )
        state.add_room(bldg_id, floor_id, room_a)
        state.add_room(bldg_id, floor_id, room_b)

        editor = FloorEditorWidget(state)
        editor.resize(1000, 720)
        qtbot.addWidget(editor)
        editor.show()
        editor.set_floor(bldg_id, floor_id)
        editor.view.fit_floor_in_view()

        view = editor.view
        view.select_entity("room", room_b.id)
        # Drag Room B's bottom-left corner (10, 0) into Room A at (5, 0)
        start_vp = _view_pt(view, Point2D(10.0, 0.0)).toPoint()
        end_vp = _view_pt(view, Point2D(5.0, 0.0)).toPoint()

        qtbot.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start_vp)
        qtbot.mouseMove(view.viewport(), pos=end_vp)
        qtbot.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end_vp)

        floor = state.project.get_floor(bldg_id, floor_id)
        assert floor is not None
        pa = Polygon([(p.x, p.y) for p in floor.rooms[0].polygon_m])
        pb = Polygon([(p.x, p.y) for p in floor.rooms[1].polygon_m])
        assert pa.intersection(pb).area < 1e-6
    finally:
        state.cleanup()


def test_wall_locked_door_blocks_open_space_and_slides_on_walls(qtbot) -> None:
    state, bldg_id, floor_id = _sample_floor_state()
    try:
        editor = FloorEditorWidget(state)
        editor.resize(1000, 720)
        qtbot.addWidget(editor)
        editor.show()
        editor.set_floor(bldg_id, floor_id)
        editor.view.fit_floor_in_view()

        view = editor.view
        view.set_tool(ToolMode.PLACE_DOOR)

        # 1. Click in open space (10, 6) -> far (> 1.5m) from all outer walls -> 0 doors!
        open_vp = _view_pt(view, Point2D(10.0, 6.0)).toPoint()
        qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=open_vp)
        floor = state.project.get_floor(bldg_id, floor_id)
        assert floor is not None
        assert len(floor.doors) == 0

        # 2. Click on bottom wall (10, 0.1) -> places 1 door snapped onto y=0
        wall_vp = _view_pt(view, Point2D(10.0, 0.1)).toPoint()
        qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=wall_vp)
        assert len(floor.doors) == 1
        door = floor.doors[0]
        assert door.position_m.y == pytest.approx(0.0, abs=1e-4)

        # 3. Switch to SELECT and drag door toward open space (14.0, 4.0) -> stays on wall!
        view.set_tool(ToolMode.SELECT)
        door_vp = _view_pt(view, door.position_m).toPoint()
        drag_vp = _view_pt(view, Point2D(14.0, 2.0)).toPoint()
        qtbot.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=door_vp)
        qtbot.mouseMove(view.viewport(), pos=drag_vp)
        qtbot.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=drag_vp)

        moved_door = floor.doors[0]
        assert moved_door.position_m.x == pytest.approx(14.0, abs=0.2)
        assert moved_door.position_m.y == pytest.approx(0.0, abs=1e-4)

        # 4. Space flips swing
        orig_swing = moved_door.swing_flip
        qtbot.keyPress(view, Qt.Key.Key_Space)
        qtbot.keyRelease(view, Qt.Key.Key_Space)
        assert floor.doors[0].swing_flip is (not orig_swing)
    finally:
        state.cleanup()


def test_stairs_riser_snap_onion_skin_and_blueprint_calibration(qtbot) -> None:
    state, bldg_id, floor0_id = _sample_floor_state()
    try:
        bldg = state.project.get_building(bldg_id)
        assert bldg is not None
        floor1 = bldg.floors[1]

        # Seed a riser hole on floor 0 at (4.0, 4.0)
        hole0 = VerticalHole(position_m=Point2D(4.0, 4.0), riser_group_id="Riser-Core")
        state.add_hole(bldg_id, floor0_id, hole0)

        # Seed a room on floor 0 for onion-skinning verification
        state.add_room(
            bldg_id,
            floor0_id,
            Room(
                name="Below Room",
                polygon_m=[
                    Point2D(1.0, 1.0),
                    Point2D(5.0, 1.0),
                    Point2D(5.0, 5.0),
                    Point2D(1.0, 5.0),
                ],
            ),
        )

        # Switch to Floor 1 with fake blueprint PNG + deterministic calibration prompt (10.0m)
        img = Image.new("RGBA", (200, 100), (30, 41, 59, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        state.set_floor_blueprint(
            bldg_id,
            floor1.id,
            BlueprintUnderlay(archive_path=f"blueprints/{floor1.id}.png", meters_per_pixel=0.05),
            buf.getvalue(),
        )

        editor = FloorEditorWidget(state, calibration_prompt=lambda _msg: 10.0)
        editor.resize(1000, 720)
        qtbot.addWidget(editor)
        editor.show()
        editor.set_floor(bldg_id, floor1.id)
        editor.view.fit_floor_in_view()

        view = editor.view
        # 1. Onion-skinning Shift+O toggles on cleanly
        qtbot.keyClick(view, Qt.Key.Key_O, Qt.KeyboardModifier.ShiftModifier)
        assert view.onion_skin_enabled is True

        # 2. Vertical hole placed within 0.4 m of floor0's (4.0, 4.0) snaps & shares riser_group_id
        view.set_tool(ToolMode.PLACE_HOLE)
        near_hole_vp = _view_pt(view, Point2D(4.2, 4.1)).toPoint()
        qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=near_hole_vp)
        assert len(floor1.holes) == 1
        assert floor1.holes[0].position_m == Point2D(4.0, 4.0)
        assert floor1.holes[0].riser_group_id == "Riser-Core"

        # 3. Draw stairs via press-drag-release
        view.set_tool(ToolMode.DRAW_STAIRS)
        st_a = _view_pt(view, Point2D(12.0, 2.0)).toPoint()
        st_b = _view_pt(view, Point2D(15.0, 4.0)).toPoint()
        qtbot.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=st_a)
        qtbot.mouseMove(view.viewport(), pos=st_b)
        qtbot.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=st_b)
        assert len(floor1.stairs) == 1

        # 4. Blueprint 2-point calibration: 5m @ 0.05m/px = 100px -> real 10.0m => 0.1m/px
        view.set_tool(ToolMode.CALIBRATE_BLUEPRINT)
        c1 = _view_pt(view, Point2D(0.0, 0.0)).toPoint()
        c2 = _view_pt(view, Point2D(5.0, 0.0)).toPoint()
        qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=c1)
        qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=c2)
        assert floor1.blueprint is not None
        assert floor1.blueprint.meters_per_pixel == pytest.approx(0.10, abs=1e-3)
    finally:
        state.cleanup()


def test_align_view_to_edge_and_map_building_view_integration(qtbot) -> None:
    state, bldg_id, floor0_id = _sample_floor_state(anchor_rotation_deg=15.0)
    try:
        # Replace floor0 outer polygon with a 30-degree tilted rectangle
        theta = math.radians(30.0)
        tilted = [
            Point2D(0.0, 0.0),
            Point2D(10.0 * math.cos(theta), 10.0 * math.sin(theta)),
            Point2D(
                10.0 * math.cos(theta) - 6.0 * math.sin(theta),
                10.0 * math.sin(theta) + 6.0 * math.cos(theta),
            ),
            Point2D(-6.0 * math.sin(theta), 6.0 * math.cos(theta)),
        ]
        state.edit_floor_outline(bldg_id, floor0_id, tilted)

        editor = FloorEditorWidget(state)
        editor.resize(900, 650)
        qtbot.addWidget(editor)
        editor.show()
        editor.set_floor(bldg_id, floor0_id)
        editor.view.fit_floor_in_view()

        editor.view.set_tool(ToolMode.ALIGN_VIEW_EDGE)
        mid_wall = Point2D(5.0 * math.cos(theta), 5.0 * math.sin(theta))
        vp = _view_pt(editor.view, mid_wall).toPoint()
        qtbot.mouseClick(editor.view.viewport(), Qt.MouseButton.LeftButton, pos=vp)

        floor0 = state.project.get_floor(bldg_id, floor0_id)
        bldg = state.project.get_building(bldg_id)
        assert floor0 is not None and bldg is not None and bldg.geo_anchor is not None
        assert floor0.editor_view_rotation_deg == pytest.approx(30.0, abs=0.5)
        assert bldg.geo_anchor.rotation_deg == pytest.approx(15.0)
    finally:
        state.cleanup()
