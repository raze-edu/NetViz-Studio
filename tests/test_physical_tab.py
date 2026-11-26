"""Phase 7 pytest-qt tests for Tab 3 Physical Mapping, Riser Routing, BOM & Pre-Survey Wi-Fi."""

from __future__ import annotations

import math

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QGraphicsView

from desktop.coords import bounds_m_to_scene_rect, local_m_to_scene
from desktop.state import ProjectState
from desktop.ui.physical_tab import PhysicalMappingView, ToolMode
from shared.geo_math import calculate_project_cable_bom, true_north_screen_cw_deg
from shared.models import (
    Building,
    Cable,
    CableStyle,
    ComponentType,
    Floor,
    GeoAnchor,
    LinePattern,
    PhysicalCableSegment,
    Point2D,
    Project,
    Room,
    VerticalHole,
)
from shared.presets import get_builtin_template, instantiate_from_template


def _build_two_floor_state(*, anchor_rotation_deg: float = 22.5) -> ProjectState:
    floor0 = Floor(
        id="floor-0",
        name="Ground Floor",
        level_index=0,
        outer_polygon_m=[
            Point2D(0.0, 0.0),
            Point2D(20.0, 0.0),
            Point2D(20.0, 12.0),
            Point2D(0.0, 12.0),
        ],
        rooms=[
            Room(
                name="Server Room",
                polygon_m=[
                    Point2D(0.0, 0.0),
                    Point2D(8.0, 0.0),
                    Point2D(8.0, 12.0),
                    Point2D(0.0, 12.0),
                ],
                wall_attenuation_db=14.0,
            ),
            Room(
                name="Lobby",
                polygon_m=[
                    Point2D(8.0, 0.0),
                    Point2D(20.0, 0.0),
                    Point2D(20.0, 12.0),
                    Point2D(8.0, 12.0),
                ],
                wall_attenuation_db=6.0,
            ),
        ],
        holes=[
            VerticalHole(
                id="hole-0",
                position_m=Point2D(2.0, 2.0),
                riser_group_id="Riser-A",
                label="Riser-A",
            )
        ],
    )
    floor1 = Floor(
        id="floor-1",
        name="1st Floor",
        level_index=1,
        outer_polygon_m=[
            Point2D(0.0, 0.0),
            Point2D(20.0, 0.0),
            Point2D(20.0, 12.0),
            Point2D(0.0, 12.0),
        ],
        rooms=[
            Room(
                name="Office West",
                polygon_m=[
                    Point2D(0.0, 0.0),
                    Point2D(10.0, 0.0),
                    Point2D(10.0, 12.0),
                    Point2D(0.0, 12.0),
                ],
                wall_attenuation_db=15.0,
            ),
            Room(
                name="Office East",
                polygon_m=[
                    Point2D(10.0, 0.0),
                    Point2D(20.0, 0.0),
                    Point2D(20.0, 12.0),
                    Point2D(10.0, 12.0),
                ],
                wall_attenuation_db=6.0,
            ),
        ],
        holes=[
            VerticalHole(
                id="hole-1",
                position_m=Point2D(2.0, 2.0),
                riser_group_id="Riser-A",
                label="Riser-A",
            )
        ],
    )
    bldg = Building(
        id="bldg-hq",
        name="HQ Building",
        geo_anchor=GeoAnchor(
            origin_lat=52.520000,
            origin_lon=13.405000,
            rotation_deg=anchor_rotation_deg,
        ),
        floor_height_m=3.5,
        floors=[floor0, floor1],
    )

    fw = instantiate_from_template(
        get_builtin_template(ComponentType.FIREWALL), [], pos=Point2D(100.0, 100.0)
    )
    sw = instantiate_from_template(
        get_builtin_template(ComponentType.ACTIVE_SWITCH), [fw.name], pos=Point2D(300.0, 100.0)
    )
    ap = instantiate_from_template(
        get_builtin_template(ComponentType.ACCESS_POINT),
        [fw.name, sw.name],
        pos=Point2D(500.0, 100.0),
    )

    cab_fw_sw = Cable(
        source_component_id=fw.id,
        source_port_id=fw.ports[1].id,
        target_component_id=sw.id,
        target_port_id=sw.ports[0].id,
        label="Core-Link",
        vlan_id="10",
        style=CableStyle(color_hex="#EF4444", pattern=LinePattern.SOLID, width_px=3.0),
    )
    cab_sw_ap = Cable(
        source_component_id=sw.id,
        source_port_id=sw.ports[1].id,
        target_component_id=ap.id,
        target_port_id=ap.ports[0].id,
        label="AP-Uplink",
        vlan_id="20",
        style=CableStyle(color_hex="#22C55E", pattern=LinePattern.DASHED, width_px=2.5),
    )

    proj = Project(
        name="Physical Tab Test",
        buildings=[bldg],
        components=[fw, sw, ap],
        cables=[cab_fw_sw, cab_sw_ap],
    )
    return ProjectState(proj, autosave_enabled=False)


def _send_mouse_drag(view: QGraphicsView, start_pt: QPoint, end_pt: QPoint) -> None:
    """Send LeftButton press -> move (with LeftButton held) -> release on viewport."""
    vp = view.viewport()
    p0 = QPointF(start_pt)
    p1 = QPointF(end_pt)
    g0 = QPointF(vp.mapToGlobal(start_pt))
    g1 = QPointF(vp.mapToGlobal(end_pt))
    QApplication.sendEvent(
        vp,
        QMouseEvent(
            QEvent.Type.MouseButtonPress,
            p0,
            g0,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        ),
    )
    QApplication.sendEvent(
        vp,
        QMouseEvent(
            QEvent.Type.MouseMove,
            p1,
            g1,
            Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        ),
    )
    QApplication.sendEvent(
        vp,
        QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            p1,
            g1,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        ),
    )


def test_place_unplace_syncs_with_state_and_undo_redo(qtbot) -> None:
    state = _build_two_floor_state()
    try:
        view = PhysicalMappingView(state)
        view.resize(1200, 760)
        qtbot.addWidget(view)
        view.show()

        fw, sw, ap = state.project.components
        assert view.active_floor_id() == "floor-0"
        assert view.unplaced_list.count() == 3
        assert view.placed_list.count() == 0

        # Place Firewall on Floor 0 via double-click in Unplaced list
        fw_item = next(
            view.unplaced_list.item(i)
            for i in range(view.unplaced_list.count())
            if view.unplaced_list.item(i).data(Qt.ItemDataRole.UserRole) == fw.id
        )
        view._on_unplaced_double_clicked(fw_item)

        assert fw.physical_building_id == "bldg-hq"
        assert fw.physical_floor_id == "floor-0"
        assert fw.physical_pos_m is not None
        assert fw.id in view.canvas._comp_items
        assert view.unplaced_list.count() == 2
        assert view.placed_list.count() == 1

        # Switch to Floor 1 and place Access Point via Place-at-Center button
        view.set_active_floor("bldg-hq", "floor-1")
        assert view.active_floor_id() == "floor-1"

        for i in range(view.unplaced_list.count()):
            it = view.unplaced_list.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == ap.id:
                view.unplaced_list.setCurrentItem(it)
                break
        view.place_center_btn.click()

        assert ap.physical_building_id == "bldg-hq"
        assert ap.physical_floor_id == "floor-1"
        assert ap.physical_pos_m is not None
        assert ap.id in view.canvas._comp_items
        assert view.unplaced_list.count() == 1
        assert view.placed_list.count() == 2

        # Search filter narrows lists cleanly
        view.search_edit.setText(ap.name)
        assert view.placed_list.count() == 1
        view.search_edit.clear()
        assert view.placed_list.count() == 2

        # Unplace AP via button and verify Undo/Redo
        for i in range(view.placed_list.count()):
            it = view.placed_list.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == ap.id:
                view.placed_list.setCurrentItem(it)
                break
        view.unplace_btn.click()

        assert ap.physical_floor_id is None
        assert ap.physical_pos_m is None
        assert ap.id not in view.canvas._comp_items
        assert view.unplaced_list.count() == 2
        assert view.placed_list.count() == 1

        # Undo restores AP placement on Floor 1
        state.undo_stack.undo()
        assert ap.physical_floor_id == "floor-1"
        assert ap.physical_pos_m is not None
        assert ap.id in view.canvas._comp_items
        assert view.placed_list.count() == 2

        # Redo unplaces AP again
        state.undo_stack.redo()
        assert ap.physical_floor_id is None
        assert ap.id not in view.canvas._comp_items
    finally:
        state.cleanup()


def test_inter_floor_riser_routing_and_double_click_jump_badge(qtbot) -> None:
    state = _build_two_floor_state()
    try:
        fw, sw, ap = state.project.components
        state.place_component_physical(sw.id, "bldg-hq", "floor-0", Point2D(6.0, 4.0))
        state.place_component_physical(ap.id, "bldg-hq", "floor-1", Point2D(12.0, 6.0))

        view = PhysicalMappingView(state)
        view.resize(1200, 760)
        qtbot.addWidget(view)
        view.show()

        # On Floor 0: switch->AP cable is routed to Riser-A with an upward jump badge
        assert view.active_floor_id() == "floor-0"
        assert len(view.canvas._riser_badges) == 1
        badge = view.canvas._riser_badges[0]
        assert "1st Floor" in badge.badge_text
        assert "Riser-A" in badge.badge_text
        assert badge.badge_text.startswith("↑")

        cab_sw_ap = state.project.cables[1]
        assert cab_sw_ap.id in view.canvas._cable_items
        c_item = view.canvas._cable_items[cab_sw_ap.id]
        assert c_item.is_riser_stub is True
        assert c_item.end_m.x == pytest.approx(2.0)
        assert c_item.end_m.y == pytest.approx(2.0)

        # Double-click the riser jump badge in the scene viewport -> jumps to Floor 1
        badge_vp_pt = (
            badge.deviceTransform(view.canvas.viewportTransform())
            .map(badge.boundingRect().center())
            .toPoint()
        )
        qtbot.mouseDClick(view.canvas.viewport(), Qt.MouseButton.LeftButton, pos=badge_vp_pt)

        assert view.active_floor_id() == "floor-1"
        assert len(view.canvas._riser_badges) == 1
        badge_down = view.canvas._riser_badges[0]
        assert "Ground Floor" in badge_down.badge_text
        assert "Riser-A" in badge_down.badge_text
        assert badge_down.badge_text.startswith("↓")

        # Double-click downward badge -> jumps straight back to Ground Floor
        down_vp_pt = (
            badge_down.deviceTransform(view.canvas.viewportTransform())
            .map(badge_down.boundingRect().center())
            .toPoint()
        )
        qtbot.mouseDClick(view.canvas.viewport(), Qt.MouseButton.LeftButton, pos=down_vp_pt)
        assert view.active_floor_id() == "floor-0"
    finally:
        state.cleanup()


def test_drag_physical_waypoint_updates_segments_and_bom_immediately(qtbot) -> None:
    state = _build_two_floor_state()
    try:
        fw, sw, _ap = state.project.components
        state.place_component_physical(fw.id, "bldg-hq", "floor-0", Point2D(2.0, 5.0))
        state.place_component_physical(sw.id, "bldg-hq", "floor-0", Point2D(10.0, 5.0))

        cab = state.project.cables[0]
        # Start with one waypoint at (6.0, 5.0) collinear (total horiz = 8.0 m)
        cab_with_wp = Cable(
            id=cab.id,
            source_component_id=cab.source_component_id,
            source_port_id=cab.source_port_id,
            target_component_id=cab.target_component_id,
            target_port_id=cab.target_port_id,
            label=cab.label,
            vlan_id=cab.vlan_id,
            style=cab.style,
            physical_segments=[
                PhysicalCableSegment(
                    floor_id="floor-0",
                    waypoints_m=[Point2D(6.0, 5.0)],
                )
            ],
        )
        state.update_cable(cab.id, cab_with_wp)

        view = PhysicalMappingView(state)
        view.resize(1200, 760)
        qtbot.addWidget(view)
        view.show()
        view.canvas.fit_floor_in_view()

        initial_bom = calculate_project_cable_bom(state.project, slack_factor=0.10)
        assert initial_bom.total_m == pytest.approx(8.0 * 1.10, rel=1e-3)
        assert "8.8 m" in view.bom_panel.total_kpi_label.text()

        # Select cable to expose waypoint handle
        view.canvas.select_cable(cab.id)
        assert len(view.canvas._waypoint_handles) == 1
        handle = view.canvas._waypoint_handles[0]

        start_vp = view.canvas.mapFromScene(handle.pos())
        target_m = Point2D(6.0, 8.0)  # Creates 5m + 5m = 10.0 m run (+10% = 11.0 m)
        end_vp = view.canvas.mapFromScene(local_m_to_scene(target_m))

        # Drag handle live -> BOM updates immediately on move AND commits on release
        vp = view.canvas.viewport()
        QApplication.sendEvent(
            vp,
            QMouseEvent(
                QEvent.Type.MouseButtonPress,
                QPointF(start_vp),
                QPointF(vp.mapToGlobal(start_vp)),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            ),
        )
        QApplication.sendEvent(
            vp,
            QMouseEvent(
                QEvent.Type.MouseMove,
                QPointF(end_vp),
                QPointF(vp.mapToGlobal(end_vp)),
                Qt.MouseButton.NoButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            ),
        )
        # Immediate BOM update BEFORE mouse release!
        live_bom_text = view.bom_panel.total_kpi_label.text()
        assert "11.0 m" in live_bom_text

        QApplication.sendEvent(
            vp,
            QMouseEvent(
                QEvent.Type.MouseButtonRelease,
                QPointF(end_vp),
                QPointF(vp.mapToGlobal(end_vp)),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
            ),
        )

        updated_cab = next(c for c in state.project.cables if c.id == cab.id)
        assert len(updated_cab.physical_segments) == 1
        moved_wp = updated_cab.physical_segments[0].waypoints_m[0]
        assert moved_wp.x == pytest.approx(6.0, abs=0.25)
        assert moved_wp.y == pytest.approx(8.0, abs=0.25)

        final_bom = calculate_project_cable_bom(state.project, slack_factor=0.10)
        expected_horiz = math.hypot(moved_wp.x - 2.0, moved_wp.y - 5.0) + math.hypot(
            10.0 - moved_wp.x, 5.0 - moved_wp.y
        )
        assert final_bom.total_m == pytest.approx(expected_horiz * 1.10, rel=1e-3)
        assert view.bom_panel.port_summary_list.count() == 1
        assert "Base1000" in view.bom_panel.port_summary_list.item(0).text()

        # Also verify double-click on a cable inserts an additional physical waypoint
        c_item = view.canvas._cable_items[cab.id]
        mid_vp = view.canvas.mapFromScene(local_m_to_scene(Point2D(4.0, 6.5)))
        qtbot.mouseDClick(vp, Qt.MouseButton.LeftButton, pos=mid_vp)
        updated_cab2 = next(c for c in state.project.cables if c.id == cab.id)
        assert len(updated_cab2.physical_segments[0].waypoints_m) == 2
        assert c_item.cable.id == cab.id
    finally:
        state.cleanup()


def test_presurvey_wall_attenuated_wifi_toggle_renders_aligned_heatmap(qtbot) -> None:
    state = _build_two_floor_state()
    try:
        _fw, _sw, ap = state.project.components
        # Place AP inside Lobby (x=12, y=6), separated from Server Room (x<8) by 14 dB wall
        state.place_component_physical(ap.id, "bldg-hq", "floor-0", Point2D(12.0, 6.0))

        view = PhysicalMappingView(state)
        view.resize(1200, 760)
        qtbot.addWidget(view)
        view.show()

        assert view.canvas.heatmap_item is None

        # Toggle ON Pre-Survey Wi-Fi Coverage
        view.presurvey_btn.setChecked(True)
        hm_item = view.canvas.heatmap_item
        hm_res = view.canvas.last_heatmap_result
        assert hm_item is not None
        assert hm_res is not None
        assert hm_res.valid_pixel_count > 0
        assert "Pre-Survey" in view.coverage_kpi_label.text()

        expected_rect = bounds_m_to_scene_rect(*hm_res.bounds)
        actual_rect = hm_item.sceneBoundingRect()
        assert actual_rect.left() == pytest.approx(expected_rect.left(), abs=1e-3)
        assert actual_rect.top() == pytest.approx(expected_rect.top(), abs=1e-3)
        assert actual_rect.width() == pytest.approx(expected_rect.width(), abs=1e-3)
        assert actual_rect.height() == pytest.approx(expected_rect.height(), abs=1e-3)

        # Toggle OFF removes heatmap cleanly
        view.presurvey_btn.setChecked(False)
        assert view.canvas.heatmap_item is None
        assert view.coverage_kpi_label.text() == ""
    finally:
        state.cleanup()


def test_non_panning_mouse_rules_and_true_north_compass(qtbot) -> None:
    state = _build_two_floor_state(anchor_rotation_deg=22.5)
    try:
        view = PhysicalMappingView(state)
        view.resize(1100, 720)
        qtbot.addWidget(view)
        view.show()
        view.canvas.fit_floor_in_view()

        canvas = view.canvas
        assert canvas.dragMode() == QGraphicsView.DragMode.NoDrag
        assert canvas.dragMode() != QGraphicsView.DragMode.ScrollHandDrag

        # Left-drag on empty canvas in SELECT mode MUST NOT pan
        h_before = canvas.horizontalScrollBar().value()
        v_before = canvas.verticalScrollBar().value()
        _send_mouse_drag(canvas, QPoint(250, 250), QPoint(380, 340))
        assert canvas.horizontalScrollBar().value() == h_before
        assert canvas.verticalScrollBar().value() == v_before

        # Space + Left-Drag DOES pan cleanly
        qtbot.keyPress(canvas, Qt.Key.Key_Space)
        _send_mouse_drag(canvas, QPoint(350, 320), QPoint(240, 220))
        qtbot.keyRelease(canvas, Qt.Key.Key_Space)
        assert (
            canvas.horizontalScrollBar().value() != h_before
            or canvas.verticalScrollBar().value() != v_before
        )

        # Hand tool (H) enables ScrollHandDrag; V returns to NoDrag
        qtbot.keyClick(canvas, Qt.Key.Key_H)
        assert canvas.tool_mode == ToolMode.PAN
        assert canvas.dragMode() == QGraphicsView.DragMode.ScrollHandDrag
        qtbot.keyClick(canvas, Qt.Key.Key_V)
        assert canvas.tool_mode == ToolMode.SELECT
        assert canvas.dragMode() == QGraphicsView.DragMode.NoDrag

        # View rotation updates Floor.editor_view_rotation_deg ONLY & True North compass formula
        canvas.set_view_rotation(40.0, commit_to_state=True)
        bldg = state.project.get_building("bldg-hq")
        floor0 = state.project.get_floor("bldg-hq", "floor-0")
        assert bldg is not None and floor0 is not None
        assert floor0.editor_view_rotation_deg == pytest.approx(40.0)
        assert bldg.geo_anchor is not None
        assert bldg.geo_anchor.rotation_deg == pytest.approx(22.5)
        expected_tn = true_north_screen_cw_deg(40.0, 22.5)
        assert canvas.true_north_angle_deg() == pytest.approx(expected_tn)
        assert canvas.true_north_angle_deg() == pytest.approx((40.0 - 22.5) % 360.0)
    finally:
        state.cleanup()
