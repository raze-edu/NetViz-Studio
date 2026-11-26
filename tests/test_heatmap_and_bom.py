"""Phase 2 tests: exact pixel-to-ENU heatmap alignment, clipping, parity & cable BOM."""

from __future__ import annotations

import pytest

from desktop.coords import bounds_m_to_scene_rect, local_m_to_scene
from shared.geo_math import calculate_cable_physical_length, calculate_project_cable_bom
from shared.heatmap import compute_idw_heatmap, estimate_presurvey_heatmap
from shared.models import (
    Building,
    Cable,
    Component,
    ComponentType,
    Floor,
    Point2D,
    Port,
    PortType,
    PositionSource,
    Project,
    Room,
    SurveySample,
    WifiReading,
)
from shared.sample_project import build_sample_campus_project


def _pixel_at(rgba: bytes, width: int, col: int, row: int) -> tuple[int, int, int, int]:
    idx = (row * width + col) * 4
    return (rgba[idx], rgba[idx + 1], rgba[idx + 2], rgba[idx + 3])


def test_heatmap_exact_non_flipped_enu_alignment_and_numpy_parity() -> None:
    floor = Floor(
        id="f-rect",
        name="GF",
        outer_polygon_m=[
            Point2D(0.0, 0.0),
            Point2D(20.0, 0.0),
            Point2D(20.0, 10.0),
            Point2D(0.0, 10.0),
        ],
    )
    samples = [
        SurveySample(
            timestamp_utc="2026-11-25T10:00:00Z",
            building_id="b1",
            floor_id="f-rect",
            pos_m=Point2D(10.0, 2.0),  # Southern half (near y=0), NOT y=8!
            source=PositionSource.MANUAL,
            readings=[
                WifiReading(
                    ssid="Corp-WiFi",
                    bssid="00:11:22:33:44:01",
                    rssi_dbm=-32,
                    frequency_mhz=5180,
                ),
                WifiReading(
                    ssid="Guest-WiFi",
                    bssid="00:11:22:33:44:02",
                    rssi_dbm=-85,
                    frequency_mhz=2412,
                ),
            ],
        ),
        SurveySample(
            timestamp_utc="2026-11-25T10:01:00Z",
            building_id="b1",
            floor_id="f-rect",
            pos_m=Point2D(10.0, 8.0),
            source=PositionSource.MANUAL,
            readings=[
                WifiReading(
                    ssid="Corp-WiFi",
                    bssid="00:11:22:33:44:01",
                    rssi_dbm=-90,
                    frequency_mhz=5180,
                ),
            ],
        ),
    ]

    res_np = compute_idw_heatmap(
        floor,
        samples,
        target_ssids=["Corp-WiFi"],
        resolution_m=0.5,
        dead_zone_dbm=-70.0,
        force_pure_python=False,
    )
    res_py = compute_idw_heatmap(
        floor,
        samples,
        target_ssids=["Corp-WiFi"],
        resolution_m=0.5,
        dead_zone_dbm=-70.0,
        force_pure_python=True,
    )

    assert (res_np.width, res_np.height) == (40, 20)
    assert (res_py.width, res_py.height) == (40, 20)
    assert res_np.valid_pixel_count == res_py.valid_pixel_count == 800
    assert res_np.coverage_percent == pytest.approx(res_py.coverage_percent, abs=1e-6)

    # Verify byte-level parity (max diff <= 1 across all RGBA channels)
    max_diff = max(abs(a - b) for a, b in zip(res_np.rgba_bytes, res_py.rgba_bytes, strict=True))
    assert max_diff <= 1

    # Verify exact pixel-to-scene alignment (Section 4.8A + 5 Phase 2)
    scene_rect = bounds_m_to_scene_rect(*res_np.bounds)
    hot_scene = local_m_to_scene(Point2D(10.0, 2.0))
    cold_scene = local_m_to_scene(Point2D(10.0, 8.0))

    hot_col = int((hot_scene.x() - scene_rect.left()) / scene_rect.width() * res_np.width)
    hot_row = int((hot_scene.y() - scene_rect.top()) / scene_rect.height() * res_np.height)
    cold_col = int((cold_scene.x() - scene_rect.left()) / scene_rect.width() * res_np.width)
    cold_row = int((cold_scene.y() - scene_rect.top()) / scene_rect.height() * res_np.height)

    # Row 0 = max_y (y=10), so y=2 MUST be in bottom half (hot_row >= 15), NOT flipped!
    assert hot_row >= 15
    assert cold_row <= 5

    r_hot, g_hot, b_hot, a_hot = _pixel_at(res_np.rgba_bytes, res_np.width, hot_col, hot_row)
    r_cold, g_cold, b_cold, a_cold = _pixel_at(res_np.rgba_bytes, res_np.width, cold_col, cold_row)
    assert a_hot > 0 and g_hot > r_hot  # Green peak at (10, 2)
    assert a_cold > 0 and r_cold > g_cold  # Red cold spot at (10, 8)

    # Band & BSSID filtering check
    res_24g = compute_idw_heatmap(
        floor,
        samples,
        band_filter="2.4",
        bssid_filter="00-11-22-33-44-02",
        resolution_m=0.5,
        dead_zone_dbm=-70.0,
    )
    assert res_24g.coverage_percent == pytest.approx(0.0)


def test_heatmap_strict_polygon_clipping_and_presurvey_walls() -> None:
    sample = build_sample_campus_project()
    floor0 = sample.buildings[0].floors[0]  # L-shaped floor: missing x in [14..24], y in [10..18]
    res = compute_idw_heatmap(
        floor0,
        sample.surveys[0].samples,
        target_ssids=["Corp-WiFi"],
        resolution_m=0.5,
    )
    # Top-right corner (col = width - 2, row = 1) corresponds to x~23, y~17 (outside L-shape!)
    _, _, _, alpha_outside = _pixel_at(res.rgba_bytes, res.width, res.width - 2, 1)
    assert alpha_outside == 0

    # Pre-survey wall-attenuated analytical heatmap on a room-divided floor
    floor_walled = Floor(
        id="f-wall",
        name="GF",
        outer_polygon_m=[Point2D(0, 0), Point2D(20, 0), Point2D(20, 10), Point2D(0, 10)],
        rooms=[
            Room(
                name="Shielded",
                polygon_m=[Point2D(10, 0), Point2D(20, 0), Point2D(20, 10), Point2D(10, 10)],
                wall_attenuation_db=18.0,
            )
        ],
    )
    ap = Component(
        component_type=ComponentType.ACCESS_POINT,
        name="AP-1",
        physical_building_id="b1",
        physical_floor_id="f-wall",
        physical_pos_m=Point2D(8.0, 5.0),
    )
    proj = Project(
        components=[ap],
        buildings=[Building(id="b1", name="B1", floors=[floor_walled])],
    )
    pre = estimate_presurvey_heatmap(proj, floor_walled, resolution_m=0.5, dead_zone_dbm=-65.0)
    # Left of AP (4 m away, open space): col=8, row=10
    # Right of AP (4 m away, behind 18 dB wall): col=24, row=10
    r_open, g_open, _, a_open = _pixel_at(pre.rgba_bytes, pre.width, 8, 10)
    r_wall, g_wall, _, a_wall = _pixel_at(pre.rgba_bytes, pre.width, 24, 10)
    assert a_open > 0 and a_wall > 0
    assert g_open > r_open
    assert g_wall < g_open


def test_physical_cable_length_and_bom_with_riser() -> None:
    sample = build_sample_campus_project()
    bom = calculate_project_cable_bom(sample, slack_factor=0.10)

    assert len(bom.runs) == len(sample.cables)
    assert bom.total_m == pytest.approx(sum(r.total_with_slack_m for r in bom.runs))
    assert sum(g.run_count for g in bom.by_port_type) == len(bom.runs)
    assert sum(g.total_m for g in bom.by_port_type) == pytest.approx(bom.total_m)

    # Inter-floor riser: PP-01 (3.5, 2.5) -> hole (2, 2) + 3.2m riser -> PSW-01 (3.0, 2.5)
    riser_run = next(r for r in bom.runs if r.is_inter_floor)
    expected_horiz = ((3.5 - 2.0) ** 2 + (2.5 - 2.0) ** 2) ** 0.5 + (
        (3.0 - 2.0) ** 2 + (2.5 - 2.0) ** 2
    ) ** 0.5
    assert riser_run.horizontal_m == pytest.approx(expected_horiz, abs=1e-6)
    assert riser_run.vertical_riser_m == pytest.approx(3.2, abs=1e-6)
    assert riser_run.total_with_slack_m == pytest.approx((expected_horiz + 3.2) * 1.10, abs=1e-6)

    # Verify unplaced component cable returns None
    unplaced = Component(
        id="u1",
        component_type=ComponentType.DEVICE,
        name="Unplaced",
        ports=[Port(id="up1", label="eth0", port_type=PortType.BASE1000)],
    )
    sample.components.append(unplaced)
    unplaced_cable = Cable(
        source_component_id=sample.components[0].id,
        source_port_id=sample.components[0].ports[0].id,
        target_component_id="u1",
        target_port_id="up1",
    )
    assert calculate_cable_physical_length(sample, unplaced_cable) is None
