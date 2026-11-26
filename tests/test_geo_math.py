"""Phase 2 tests: canonical geo-math, golden contract vectors, compass, and tiles."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shared.geo_math import (
    bearing_screen_cw_deg,
    bounding_box_tiles,
    building_latlon_bounds_with_margin,
    compute_centroid,
    distance_and_cardinal_bearing,
    find_room_at_point,
    floor_polygon_to_latlon,
    latlon_to_local_m,
    latlon_to_tile_xy,
    local_m_to_latlon,
    meters_per_degree,
    point_in_polygon,
    polygon_area_m2,
    polygon_bounds,
    polygon_latlon_to_local_m,
    tile_xy_to_latlon_bounds,
    true_north_screen_cw_deg,
)
from shared.models import GeoAnchor, Point2D, Project, SurveySession
from shared.sample_project import build_sample_campus_project

GOLDEN_PATH = Path(__file__).resolve().parent / "golden" / "geo_and_schema_vectors.json"


def test_golden_vectors_file_exists_and_matches_all_groups() -> None:
    assert GOLDEN_PATH.is_file(), "Expected generated tests/golden/geo_and_schema_vectors.json"
    data = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))

    # 1. High-latitude Berlin site rotated ENU round-trip (< 1e-4 m, < 1e-7 deg)
    for group in data["berlin_projection_vectors"]:
        anchor = GeoAnchor.from_dict(group["anchor"])
        m_lat, m_lon = meters_per_degree(anchor.origin_lat)
        assert m_lat == pytest.approx(group["m_lat"], abs=1e-9)
        assert m_lon == pytest.approx(group["m_lon"], abs=1e-9)
        for case in group["cases"]:
            pt = Point2D.from_dict(case["local_m"])
            lat, lon = local_m_to_latlon(pt, anchor)
            assert lat == pytest.approx(case["lat"], abs=1e-7)
            assert lon == pytest.approx(case["lon"], abs=1e-7)
            rt = latlon_to_local_m(lat, lon, anchor)
            assert rt.x == pytest.approx(pt.x, abs=1e-4)
            assert rt.y == pytest.approx(pt.y, abs=1e-4)

    # 2. Equator & Southern Hemisphere sites
    for site in data["global_projection_vectors"]:
        anchor = GeoAnchor.from_dict(site["anchor"])
        latlons: list[tuple[float, float]] = []
        for case in site["cases"]:
            pt = Point2D.from_dict(case["local_m"])
            lat, lon = local_m_to_latlon(pt, anchor)
            latlons.append((lat, lon))
            assert lat == pytest.approx(case["lat"], abs=1e-7)
            assert lon == pytest.approx(case["lon"], abs=1e-7)
            rt = latlon_to_local_m(lat, lon, anchor)
            assert rt.x == pytest.approx(pt.x, abs=1e-4)
            assert rt.y == pytest.approx(pt.y, abs=1e-4)
        c_lat, c_lon = compute_centroid(latlons)
        assert c_lat == pytest.approx(site["centroid"][0], abs=1e-9)
        assert c_lon == pytest.approx(site["centroid"][1], abs=1e-9)

    # 3. Slippy tile conversions
    for tile in data["slippy_tile_vectors"]:
        tx, ty = latlon_to_tile_xy(tile["lat"], tile["lon"], tile["zoom"])
        assert (tx, ty) == (tile["tile_x"], tile["tile_y"])
        lat_n, lon_w, lat_s, lon_e = tile_xy_to_latlon_bounds(tile["zoom"], tx, ty)
        assert lat_n == pytest.approx(tile["bounds"]["lat_n"], abs=1e-7)
        assert lon_w == pytest.approx(tile["bounds"]["lon_w"], abs=1e-7)
        assert lat_s == pytest.approx(tile["bounds"]["lat_s"], abs=1e-7)
        assert lon_e == pytest.approx(tile["bounds"]["lon_e"], abs=1e-7)
        assert lat_s <= tile["lat"] <= lat_n
        assert lon_w <= tile["lon"] <= lon_e

    # 4. True North & compass angles
    for comp in data["compass_angle_vectors"]:
        tn = true_north_screen_cw_deg(comp["view_rot_deg"], comp["anchor_rot_deg"])
        br = bearing_screen_cw_deg(
            comp["view_rot_deg"], comp["anchor_rot_deg"], comp["azimuth_deg"]
        )
        assert tn == pytest.approx(comp["true_north_screen_cw_deg"], abs=1e-9)
        assert br == pytest.approx(comp["bearing_screen_cw_deg"], abs=1e-9)

    # 5. Room hit-testing & cardinal bearing
    sample = build_sample_campus_project()
    floor0 = sample.buildings[0].floors[0]
    rab = data["room_and_bearing_vectors"]
    target = Point2D.from_dict(rab["target_center_m"])
    for case in rab["cases"]:
        pt = Point2D.from_dict(case["pt_m"])
        assert point_in_polygon(pt, floor0.outer_polygon_m) is case["inside_floor"]
        room = find_room_at_point(pt, floor0)
        assert (room.name if room else None) == case["room_name"]
        dist, cardinal = distance_and_cardinal_bearing(pt, target, rab["anchor_rotation_deg"])
        assert dist == pytest.approx(case["to_center_distance_m"], abs=1e-6)
        assert cardinal == case["to_center_cardinal"]

    # 6. Schema vectors
    v21 = Project.from_dict(data["schema_vectors"]["project_v2_1_0"])
    v20 = Project.from_dict(data["schema_vectors"]["project_v2_0_0_legacy"])
    sess = SurveySession.from_dict(data["schema_vectors"]["survey_session"])
    assert v21.schema_version == "2.1.0"
    assert v20.schema_version == "2.0.0"
    assert len(sess.samples) == 25


def test_cw_rotation_physical_directions_and_compass_sanity() -> None:
    anchor_90 = GeoAnchor(origin_lat=52.52, origin_lon=13.405, rotation_deg=90.0)
    # At +90 deg CW: Local +Y points Geographic East (+lon); Local +X points Geographic South (-lat)
    lat_y, lon_y = local_m_to_latlon(Point2D(0.0, 20.0), anchor_90)
    assert lat_y == pytest.approx(anchor_90.origin_lat, abs=1e-9)
    assert lon_y > anchor_90.origin_lon

    lat_x, lon_x = local_m_to_latlon(Point2D(20.0, 0.0), anchor_90)
    assert lat_x < anchor_90.origin_lat
    assert lon_x == pytest.approx(anchor_90.origin_lon, abs=1e-9)

    # Compass sanity from Section 4.2C
    assert true_north_screen_cw_deg(view_rot_deg=0.0, anchor_rot_deg=30.0) == pytest.approx(330.0)
    assert true_north_screen_cw_deg(view_rot_deg=30.0, anchor_rot_deg=30.0) == pytest.approx(0.0)

    # Cardinal bearing accounting for +90 deg CW anchor rotation:
    # Local +Y points Geographic East -> "E"
    dist, cardinal = distance_and_cardinal_bearing(
        Point2D(0.0, 0.0), Point2D(0.0, 10.0), anchor_rotation_deg=90.0
    )
    assert dist == pytest.approx(10.0)
    assert cardinal == "E"


def test_polygon_helpers_and_tile_bbox() -> None:
    sample = build_sample_campus_project()
    bldg_a = sample.buildings[0]
    floor0 = bldg_a.floors[0]
    assert bldg_a.geo_anchor is not None

    # L-shape area: 24*10 + 14*8 = 352.0 m^2
    assert polygon_area_m2(floor0.outer_polygon_m) == pytest.approx(352.0)
    assert polygon_bounds(floor0.outer_polygon_m) == (0.0, 0.0, 24.0, 18.0)

    latlons = floor_polygon_to_latlon(floor0.outer_polygon_m, bldg_a.geo_anchor)
    back_m = polygon_latlon_to_local_m(latlons, bldg_a.geo_anchor)
    for orig, rt in zip(floor0.outer_polygon_m, back_m, strict=True):
        assert rt.x == pytest.approx(orig.x, abs=1e-6)
        assert rt.y == pytest.approx(orig.y, abs=1e-6)

    bbox = building_latlon_bounds_with_margin(sample.buildings, margin_m=250.0)
    assert bbox is not None
    tiles = bounding_box_tiles(*bbox, zooms=[16, 17])
    assert len(tiles) >= 2
    assert all(z in {16, 17} for z, _, _ in tiles)
