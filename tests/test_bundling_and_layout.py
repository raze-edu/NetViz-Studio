"""Tests for Tab 1 waypoints, Manhattan routing, bundling, align/distribute, and layout."""

from __future__ import annotations

import math

import pytest
from PySide6.QtCore import QPointF, Qt

from desktop.state import ProjectState
from desktop.ui.logical_tab.logical_view import LogicalCanvasView
from desktop.ui.logical_tab.routing_controller import (
    TIER_BY_COMPONENT_TYPE,
    choose_bundle_entry_exit,
    compute_alignment_moves,
    compute_bundle_parallel_offsets,
    compute_distribution_moves,
    compute_hierarchical_layout,
    compute_orthogonal_waypoints,
    estimate_node_size,
    offset_polyline_by_normal,
)
from shared.models import (
    Cable,
    CableBundle,
    ComponentType,
    Point2D,
    Project,
)
from shared.presets import get_builtin_template, instantiate_from_template


def _assert_orthogonal_polyline(pts: list[Point2D]) -> None:
    assert len(pts) >= 2
    for i in range(len(pts) - 1):
        dx = abs(pts[i + 1].x - pts[i].x)
        dy = abs(pts[i + 1].y - pts[i].y)
        assert dx < 1e-4 or dy < 1e-4, f"Non-orthogonal segment {pts[i]} -> {pts[i + 1]}"


def test_orthogonal_manhattan_routing_all_quadrants() -> None:
    src = Point2D(200.0, 200.0)
    targets = [
        Point2D(450.0, 380.0),  # down-right
        Point2D(50.0, 380.0),   # down-left
        Point2D(450.0, 100.0),  # up-right
        Point2D(50.0, 100.0),   # up-left
        Point2D(215.0, 120.0),  # tight up-right
        Point2D(200.0, 350.0),  # straight vertical
        Point2D(400.0, 200.0),  # straight horizontal
    ]
    for dst in targets:
        wps = compute_orthogonal_waypoints(src, dst)
        _assert_orthogonal_polyline([src, *wps, dst])


def test_non_crossing_bundle_fan_out_orientation() -> None:
    entry = Point2D(300.0, 200.0)
    exit_pt = Point2D(600.0, 200.0)

    # Normal left-to-right cable: src near entry, dst near exit
    a1, a2, rev1 = choose_bundle_entry_exit(
        Point2D(240.0, 160.0), Point2D(660.0, 240.0), entry, exit_pt
    )
    assert rev1 is False
    assert a1 == entry and a2 == exit_pt

    # Opposite direction cable: src near exit, dst near entry -> MUST reverse to avoid crossing
    b1, b2, rev2 = choose_bundle_entry_exit(
        Point2D(650.0, 150.0), Point2D(250.0, 260.0), entry, exit_pt
    )
    assert rev2 is True
    assert b1 == exit_pt and b2 == entry


def test_bundle_parallel_offsets_formula_and_polyline_spacing() -> None:
    offsets = compute_bundle_parallel_offsets(4, spacing_px=5.0)
    assert offsets == pytest.approx([-7.5, -2.5, 2.5, 7.5])

    trunk = [Point2D(100.0, 200.0), Point2D(400.0, 200.0), Point2D(400.0, 350.0)]
    shifted = offset_polyline_by_normal(trunk, 5.0)
    assert len(shifted) == 3
    # First horizontal segment normal points +Y (0, +1) -> y shifted by +5.0
    assert shifted[0].y == pytest.approx(205.0)
    # Last vertical segment going +Y has normal (-1, 0) -> x shifted by -5.0
    assert shifted[-1].x == pytest.approx(395.0)


def test_expanded_bundle_renders_distinct_parallel_paths(qtbot) -> None:
    proj = Project()
    sw = instantiate_from_template(
        get_builtin_template(ComponentType.ACTIVE_SWITCH), [], Point2D(40, 40)
    )
    ap1 = instantiate_from_template(
        get_builtin_template(ComponentType.ACCESS_POINT), ["ASW-01"], Point2D(500, 40)
    )
    ap2 = instantiate_from_template(
        get_builtin_template(ComponentType.ACCESS_POINT),
        ["ASW-01", "AP-01"],
        Point2D(500, 180),
    )
    proj.components = [sw, ap1, ap2]
    c1 = Cable(sw.id, sw.ports[0].id, ap1.id, ap1.ports[0].id, logical_bundle_id="b1")
    c2 = Cable(sw.id, sw.ports[1].id, ap2.id, ap2.ports[0].id, logical_bundle_id="b1")
    bundle = CableBundle(
        id="b1",
        name="Trunk-1",
        entry_point=Point2D(280.0, 140.0),
        exit_point=Point2D(440.0, 140.0),
        member_cable_ids=[c1.id, c2.id],
        collapsed=False,
    )
    proj.cables = [c1, c2]
    proj.bundles = [bundle]

    state = ProjectState(proj, autosave_enabled=False)
    canvas = LogicalCanvasView(state)
    qtbot.addWidget(canvas)

    item1 = canvas.get_cable_item(c1.id)
    item2 = canvas.get_cable_item(c2.id)
    assert item1 is not None and item2 is not None
    poly1 = item1.current_polyline()
    poly2 = item2.current_polyline()
    # Trunk midpoints (indices 1 & 2) should be offset by 5.0 px perpendicular to trunk
    assert abs(poly1[1].y() - poly2[1].y()) == pytest.approx(5.0, abs=0.1)


def test_align_and_distribute_math_and_undo(qtbot) -> None:
    rects = {
        "a": (10.0, 20.0, 100.0, 80.0),
        "b": (200.0, 60.0, 120.0, 80.0),
        "c": (500.0, 140.0, 100.0, 80.0),
    }
    left_moves = compute_alignment_moves(rects, "left")
    assert all(pt.x == pytest.approx(10.0) for pt in left_moves.values())

    center_moves = compute_alignment_moves(rects, "center")
    # Overall span x: 10..600 -> center 305.0
    assert center_moves["b"].x + 60.0 == pytest.approx(305.0)

    dist_h = compute_distribution_moves(rects, "h")
    # Centers: a=60, c=550 -> mid b=305 -> b.x = 305 - 60 = 245
    assert dist_h["b"].x == pytest.approx(245.0)

    # Verify canvas align_selected + undo
    proj = Project()
    c1 = instantiate_from_template(get_builtin_template(ComponentType.ROUTER), [], Point2D(40, 40))
    c2 = instantiate_from_template(
        get_builtin_template(ComponentType.FIREWALL), [], Point2D(260, 180)
    )
    proj.components = [c1, c2]
    state = ProjectState(proj, autosave_enabled=False)
    canvas = LogicalCanvasView(state)
    qtbot.addWidget(canvas)

    canvas.get_node_item(c1.id).setSelected(True)
    canvas.get_node_item(c2.id).setSelected(True)
    canvas.align_selected("top")
    assert proj.get_component(c2.id).logical_pos.y == pytest.approx(40.0)
    state.undo_stack.undo()
    assert proj.get_component(c2.id).logical_pos.y == pytest.approx(180.0)


def test_four_tier_auto_layout_hierarchy_and_barycenter() -> None:
    rtr = instantiate_from_template(
        get_builtin_template(ComponentType.ROUTER), [], Point2D(500, 500)
    )
    fw = instantiate_from_template(
        get_builtin_template(ComponentType.FIREWALL), [], Point2D(10, 400)
    )
    sw = instantiate_from_template(
        get_builtin_template(ComponentType.ACTIVE_SWITCH), [], Point2D(200, 10)
    )
    pp = instantiate_from_template(
        get_builtin_template(ComponentType.PATCH_PANEL), [], Point2D(900, 10)
    )
    ap1 = instantiate_from_template(
        get_builtin_template(ComponentType.ACCESS_POINT), [], Point2D(900, 20)
    )
    ap2 = instantiate_from_template(
        get_builtin_template(ComponentType.ACCESS_POINT), ["AP-01"], Point2D(50, 20)
    )
    comps = [rtr, fw, sw, pp, ap1, ap2]
    cables = [
        Cable(rtr.id, rtr.ports[0].id, fw.id, fw.ports[0].id),
        Cable(fw.id, fw.ports[1].id, sw.id, sw.ports[0].id),
        Cable(sw.id, sw.ports[1].id, pp.id, pp.ports[0].id),
        Cable(pp.id, pp.ports[1].id, ap1.id, ap1.ports[0].id),
        Cable(sw.id, sw.ports[2].id, ap2.id, ap2.ports[0].id),
    ]

    layout = compute_hierarchical_layout(comps, cables)
    assert set(layout.keys()) == {c.id for c in comps}

    # Strict tier ordering: Tier 0 < Tier 1 < Tier 2 < Tier 3
    assert layout[rtr.id].y == pytest.approx(layout[fw.id].y)
    assert layout[fw.id].y < layout[sw.id].y
    assert layout[sw.id].y < layout[pp.id].y
    assert layout[pp.id].y < layout[ap1.id].y
    assert layout[ap1.id].y == pytest.approx(layout[ap2.id].y)
    assert TIER_BY_COMPONENT_TYPE[ComponentType.ROUTER] == 0
    assert TIER_BY_COMPONENT_TYPE[ComponentType.ACTIVE_SWITCH] == 1
    assert TIER_BY_COMPONENT_TYPE[ComponentType.PATCH_PANEL] == 2
    assert TIER_BY_COMPONENT_TYPE[ComponentType.ACCESS_POINT] == 3

    # Verify nodes in same tier do not overlap horizontally
    for t_pair in [(rtr, fw), (ap1, ap2)]:
        a, b = t_pair
        wa, _ = estimate_node_size(a)
        wb, _ = estimate_node_size(b)
        ax, bx = layout[a.id].x, layout[b.id].x
        assert (ax + wa <= bx) or (bx + wb <= ax)


def test_waypoint_insert_and_delete_via_canvas(qtbot) -> None:
    proj = Project()
    c1 = instantiate_from_template(get_builtin_template(ComponentType.ROUTER), [], Point2D(50, 50))
    c2 = instantiate_from_template(
        get_builtin_template(ComponentType.DEVICE), [], Point2D(350, 250)
    )
    cab = Cable(c1.id, c1.ports[0].id, c2.id, c2.ports[0].id)
    proj.components = [c1, c2]
    proj.cables = [cab]

    state = ProjectState(proj, autosave_enabled=False)
    canvas = LogicalCanvasView(state)
    canvas.resize(800, 600)
    qtbot.addWidget(canvas)
    canvas.show()

    p1 = canvas.get_port_item(c1.id, c1.ports[0].id).pin_scene_pos()
    p2 = canvas.get_port_item(c2.id, c2.ports[0].id).pin_scene_pos()
    mid_scene = QPointF(0.5 * (p1.x() + p2.x()), 0.5 * (p1.y() + p2.y()))
    mid_view = canvas.mapFromScene(mid_scene)

    qtbot.mouseDClick(canvas.viewport(), Qt.MouseButton.LeftButton, pos=mid_view)
    assert len(proj.cables[0].logical_waypoints) == 1
    wp = proj.cables[0].logical_waypoints[0]
    assert math.hypot(wp.x - mid_scene.x(), wp.y - mid_scene.y()) < 5.0

    # Undo removes waypoint cleanly
    state.undo_stack.undo()
    assert len(proj.cables[0].logical_waypoints) == 0
