"""Generate cross-language golden contract vectors and optional Android demo assets."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared.geo_math import (  # noqa: E402
    bearing_screen_cw_deg,
    compute_centroid,
    distance_and_cardinal_bearing,
    find_room_at_point,
    latlon_to_local_m,
    latlon_to_tile_xy,
    local_m_to_latlon,
    meters_per_degree,
    point_in_polygon,
    tile_xy_to_latlon_bounds,
    true_north_screen_cw_deg,
)
from shared.models import GeoAnchor, Point2D, Project  # noqa: E402
from shared.sample_project import build_sample_campus_project  # noqa: E402


def _legacy_v200_sample_dict() -> dict[str, object]:
    return {
        "schema_version": "2.0.0",
        "id": "legacy-v200-proj",
        "name": "Legacy v2.0.0 Campus",
        "components": [
            {
                "id": "comp-ap-1",
                "component_type": "access_point",
                "name": "AP-01",
                "mac_address": "AA:BB:CC:DD:EE:01",
                "ssids": ["Legacy-WiFi"],
                "ports": [{"id": "port-1", "label": "eth0", "port_type": "Base1000"}],
                "logical_pos": {"x": 50.0, "y": 80.0},
            }
        ],
        "cables": [],
        "buildings": [
            {
                "id": "bldg-1",
                "name": "Old Hall",
                "geo_anchor": {
                    "origin_lat": 52.520000,
                    "origin_lon": 13.405000,
                    "rotation_deg": 0.0,
                },
                "footprint_latlon": [
                    [52.520000, 13.405000],
                    [52.520000, 13.405150],
                    [52.520090, 13.405150],
                    [52.520090, 13.405000],
                ],
                "floors": [
                    {
                        "id": "floor-1",
                        "name": "Ground",
                        "level_index": 0,
                        "outer_polygon_m": [
                            {"x": 0.0, "y": 0.0},
                            {"x": 10.0, "y": 0.0},
                            {"x": 10.0, "y": 10.0},
                            {"x": 0.0, "y": 10.0},
                        ],
                        "rooms": [
                            {
                                "id": "room-1",
                                "name": "Lobby",
                                "polygon_m": [
                                    {"x": 0.0, "y": 0.0},
                                    {"x": 5.0, "y": 0.0},
                                    {"x": 5.0, "y": 5.0},
                                    {"x": 0.0, "y": 5.0},
                                ],
                            }
                        ],
                        "doors": [
                            {
                                "id": "door-1",
                                "position_m": {"x": 2.5, "y": 0.0},
                                "angle_deg": 0.0,
                                "width_m": 0.9,
                            }
                        ],
                    }
                ],
            }
        ],
        "surveys": [],
    }


def build_golden_vectors() -> dict[str, object]:
    """Build deterministic golden contract vectors covering all 6 Section 4.2G groups."""
    sample_project = build_sample_campus_project()

    # Group 1: High-latitude Berlin site (52.520000, 13.405000) across 4 rotations
    berlin_points = [
        Point2D(0.0, 0.0),
        Point2D(10.0, 0.0),
        Point2D(0.0, 15.0),
        Point2D(24.0, 10.0),
        Point2D(-7.5, 12.25),
    ]
    berlin_vectors: list[dict[str, object]] = []
    for rot in (0.0, 37.5, 90.0, -125.25):
        anchor = GeoAnchor(origin_lat=52.520000, origin_lon=13.405000, rotation_deg=rot)
        m_lat, m_lon = meters_per_degree(anchor.origin_lat)
        cases = []
        for pt in berlin_points:
            lat, lon = local_m_to_latlon(pt, anchor)
            roundtrip = latlon_to_local_m(lat, lon, anchor)
            cases.append(
                {
                    "local_m": pt.to_dict(),
                    "lat": lat,
                    "lon": lon,
                    "roundtrip_m": roundtrip.to_dict(),
                }
            )
        berlin_vectors.append(
            {
                "anchor": anchor.to_dict(),
                "m_lat": m_lat,
                "m_lon": m_lon,
                "cases": cases,
            }
        )

    # Group 2: Equator & Southern Hemisphere sites
    other_sites: list[dict[str, object]] = []
    for site_name, lat0, lon0, rot in (
        ("equator", 0.0, 0.0, 45.0),
        ("sydney", -33.8688, 151.2093, 212.0),
    ):
        anchor = GeoAnchor(origin_lat=lat0, origin_lon=lon0, rotation_deg=rot)
        m_lat, m_lon = meters_per_degree(lat0)
        pts = [Point2D(12.5, -4.0), Point2D(-8.0, 19.25)]
        cases = []
        for pt in pts:
            lat, lon = local_m_to_latlon(pt, anchor)
            rt = latlon_to_local_m(lat, lon, anchor)
            cases.append(
                {
                    "local_m": pt.to_dict(),
                    "lat": lat,
                    "lon": lon,
                    "roundtrip_m": rt.to_dict(),
                }
            )
        centroid = compute_centroid([(c["lat"], c["lon"]) for c in cases])  # type: ignore[misc]
        other_sites.append(
            {
                "site": site_name,
                "anchor": anchor.to_dict(),
                "m_lat": m_lat,
                "m_lon": m_lon,
                "centroid": [centroid[0], centroid[1]],
                "cases": cases,
            }
        )

    # Group 3: Slippy tile corner conversions at z = 16, 18, 19
    tile_cases: list[dict[str, object]] = []
    for label, lat, lon in (
        ("berlin", 52.520000, 13.405000),
        ("sydney", -33.8688, 151.2093),
    ):
        for zoom in (16, 18, 19):
            tx, ty = latlon_to_tile_xy(lat, lon, zoom)
            lat_n, lon_w, lat_s, lon_e = tile_xy_to_latlon_bounds(zoom, tx, ty)
            tile_cases.append(
                {
                    "site": label,
                    "lat": lat,
                    "lon": lon,
                    "zoom": zoom,
                    "tile_x": tx,
                    "tile_y": ty,
                    "bounds": {
                        "lat_n": lat_n,
                        "lon_w": lon_w,
                        "lat_s": lat_s,
                        "lon_e": lon_e,
                    },
                }
            )

    # Group 4: True North & compass heading screen angles
    compass_cases: list[dict[str, float]] = []
    for anchor_rot, view_rot, azimuth in (
        (0.0, 0.0, 0.0),
        (30.0, 0.0, 0.0),
        (30.0, 30.0, 0.0),
        (22.5, -15.0, 90.0),
        (-125.25, 45.0, 270.0),
        (212.0, 180.0, 45.0),
    ):
        compass_cases.append(
            {
                "anchor_rot_deg": anchor_rot,
                "view_rot_deg": view_rot,
                "azimuth_deg": azimuth,
                "true_north_screen_cw_deg": true_north_screen_cw_deg(view_rot, anchor_rot),
                "bearing_screen_cw_deg": bearing_screen_cw_deg(view_rot, anchor_rot, azimuth),
            }
        )

    # Group 5: Room hit-testing & off-building distance + geographic cardinal bearing
    floor0 = sample_project.buildings[0].floors[0]
    anchor_a = sample_project.buildings[0].geo_anchor
    assert anchor_a is not None
    room_hit_cases: list[dict[str, object]] = []
    for pt in (
        Point2D(4.0, 4.0),    # Server Room
        Point2D(12.0, 5.0),   # Reception
        Point2D(20.0, 5.0),   # Open Office
        Point2D(5.0, 14.0),   # Lab North
        Point2D(0.0, 0.0),    # Boundary inclusive -> Server Room
        Point2D(20.0, 15.0),  # Inside L-notch (outside building!)
    ):
        inside_floor = point_in_polygon(pt, floor0.outer_polygon_m)
        room = find_room_at_point(pt, floor0)
        dist_m, cardinal = distance_and_cardinal_bearing(
            pt, Point2D(12.0, 9.0), anchor_a.rotation_deg
        )
        room_hit_cases.append(
            {
                "pt_m": pt.to_dict(),
                "inside_floor": inside_floor,
                "room_name": room.name if room is not None else None,
                "to_center_distance_m": dist_m,
                "to_center_cardinal": cardinal,
            }
        )

    # Group 6: Full Project v2.1.0, legacy v2.0.0, and SurveySession JSON
    legacy_raw = _legacy_v200_sample_dict()
    # Verify Python parses legacy cleanly
    _ = Project.from_dict(legacy_raw)

    return {
        "schema_version": "2.1.0",
        "berlin_projection_vectors": berlin_vectors,
        "global_projection_vectors": other_sites,
        "slippy_tile_vectors": tile_cases,
        "compass_angle_vectors": compass_cases,
        "room_and_bearing_vectors": {
            "anchor_rotation_deg": anchor_a.rotation_deg,
            "target_center_m": {"x": 12.0, "y": 9.0},
            "cases": room_hit_cases,
        },
        "schema_vectors": {
            "project_v2_1_0": sample_project.to_dict(),
            "project_v2_0_0_legacy": legacy_raw,
            "survey_session": sample_project.surveys[0].to_dict(),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate cross-language golden vectors and optional Android demo assets."
    )
    parser.add_argument(
        "--include-android-assets",
        action="store_true",
        help="Also write android_app/app/src/main/assets/sample_campus.json.",
    )
    args = parser.parse_args(argv)

    golden_path = REPO_ROOT / "tests" / "golden" / "geo_and_schema_vectors.json"
    golden_path.parent.mkdir(parents=True, exist_ok=True)
    vectors = build_golden_vectors()
    golden_path.write_text(json.dumps(vectors, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote golden vectors: {golden_path}")

    android_assets_dir = REPO_ROOT / "android_app" / "app" / "src" / "main" / "assets"
    if args.include_android_assets or android_assets_dir.is_dir():
        android_assets_dir.mkdir(parents=True, exist_ok=True)
        demo_path = android_assets_dir / "sample_campus.json"
        sample_proj = build_sample_campus_project()
        demo_path.write_text(sample_proj.to_json(indent=2), encoding="utf-8")
        print(f"Wrote Android demo asset: {demo_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
