"""Canonical WGS84 <-> Local ENU projection, tiles, geometry helpers, and cable BOM."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from shared.models import (
    Building,
    Cable,
    Floor,
    GeoAnchor,
    Point2D,
    PortType,
    Project,
    Room,
)

CARDINAL_DIRECTIONS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
_MAX_MERCATOR_LAT = 85.05112878


def meters_per_degree(origin_lat: float) -> tuple[float, float]:
    """Return WGS84 tangent-plane (m_lat, m_lon) scale constants at origin_lat."""
    phi = math.radians(origin_lat)
    m_lat = 111132.92 - 559.82 * math.cos(2.0 * phi) + 1.175 * math.cos(4.0 * phi)
    m_lon = 111412.84 * math.cos(phi) - 93.5 * math.cos(3.0 * phi)
    return m_lat, m_lon


def local_m_to_latlon(pt: Point2D, anchor: GeoAnchor) -> tuple[float, float]:
    """Convert building-local ENU meters to (lat, lon) using CW anchor.rotation_deg."""
    m_lat, m_lon = meters_per_degree(anchor.origin_lat)
    theta = math.radians(anchor.rotation_deg)
    east = pt.x * math.cos(theta) + pt.y * math.sin(theta)
    north = -pt.x * math.sin(theta) + pt.y * math.cos(theta)
    lat = anchor.origin_lat + north / m_lat
    lon = anchor.origin_lon + east / m_lon
    return (lat, lon)


def latlon_to_local_m(lat: float, lon: float, anchor: GeoAnchor) -> Point2D:
    """Convert WGS84 (lat, lon) to building-local ENU meters using CW anchor.rotation_deg."""
    m_lat, m_lon = meters_per_degree(anchor.origin_lat)
    theta = math.radians(anchor.rotation_deg)
    east = (lon - anchor.origin_lon) * m_lon
    north = (lat - anchor.origin_lat) * m_lat
    x = east * math.cos(theta) - north * math.sin(theta)
    y = east * math.sin(theta) + north * math.cos(theta)
    return Point2D(x=x, y=y)


def bearing_screen_cw_deg(
    view_rot_deg: float,
    anchor_rot_deg: float,
    azimuth_deg: float = 0.0,
) -> float:
    """Return clockwise screen angle in [0, 360) for a True-North geographic azimuth."""
    return (view_rot_deg - anchor_rot_deg + azimuth_deg) % 360.0


def true_north_screen_cw_deg(view_rot_deg: float, anchor_rot_deg: float = 0.0) -> float:
    """Return clockwise screen angle in [0, 360) for the True North compass arrow."""
    return bearing_screen_cw_deg(view_rot_deg, anchor_rot_deg, 0.0)


def compute_centroid(latlons: Sequence[tuple[float, float]]) -> tuple[float, float]:
    """Compute arithmetic centroid of a list of (lat, lon) coordinates."""
    if not latlons:
        raise ValueError("Cannot compute centroid of empty coordinate list")
    pts = list(latlons)
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    n = len(pts)
    return (sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n)


def polygon_latlon_to_local_m(
    latlons: Sequence[tuple[float, float]],
    anchor: GeoAnchor,
) -> list[Point2D]:
    """Project a WGS84 polygon ring to building-local ENU Point2D vertices."""
    return [latlon_to_local_m(lat, lon, anchor) for lat, lon in latlons]


def floor_polygon_to_latlon(
    pts_m: Sequence[Point2D],
    anchor: GeoAnchor,
) -> list[tuple[float, float]]:
    """Project building-local ENU Point2D vertices to WGS84 (lat, lon) pairs."""
    return [local_m_to_latlon(pt, anchor) for pt in pts_m]


def polygon_area_m2(pts_m: Sequence[Point2D]) -> float:
    """Compute polygon area in square meters using the Shoelace formula."""
    n = len(pts_m)
    if n < 3:
        return 0.0
    total = 0.0
    for i in range(n):
        p1 = pts_m[i]
        p2 = pts_m[(i + 1) % n]
        total += p1.x * p2.y - p2.x * p1.y
    return 0.5 * abs(total)


def polygon_bounds(pts_m: Sequence[Point2D]) -> tuple[float, float, float, float]:
    """Return (min_x, min_y, max_x, max_y) bounding box of pts_m."""
    if not pts_m:
        return (0.0, 0.0, 0.0, 0.0)
    xs = [pt.x for pt in pts_m]
    ys = [pt.y for pt in pts_m]
    return (min(xs), min(ys), max(xs), max(ys))


def point_to_segment_distance(pt: Point2D, seg_a: Point2D, seg_b: Point2D) -> float:
    """Return Euclidean distance from pt to line segment [seg_a, seg_b]."""
    dx = seg_b.x - seg_a.x
    dy = seg_b.y - seg_a.y
    len_sq = dx * dx + dy * dy
    if len_sq <= 1e-18:
        return math.hypot(pt.x - seg_a.x, pt.y - seg_a.y)
    t = ((pt.x - seg_a.x) * dx + (pt.y - seg_a.y) * dy) / len_sq
    t = max(0.0, min(1.0, t))
    proj_x = seg_a.x + t * dx
    proj_y = seg_a.y + t * dy
    return math.hypot(pt.x - proj_x, pt.y - proj_y)


def point_in_polygon(
    pt_m: Point2D,
    polygon_m: Sequence[Point2D],
    boundary_tol: float = 1e-6,
) -> bool:
    """Ray-casting point-in-polygon with inclusive boundary tolerance."""
    n = len(polygon_m)
    if n < 3:
        return False

    for i in range(n):
        a = polygon_m[i]
        b = polygon_m[(i + 1) % n]
        if point_to_segment_distance(pt_m, a, b) <= boundary_tol:
            return True

    inside = False
    px, py = pt_m.x, pt_m.y
    j = n - 1
    for i in range(n):
        xi, yi = polygon_m[i].x, polygon_m[i].y
        xj, yj = polygon_m[j].x, polygon_m[j].y
        intersects = (yi > py) != (yj > py) and (
            px < (xj - xi) * (py - yi) / (yj - yi) + xi
        )
        if intersects:
            inside = not inside
        j = i
    return inside


def find_room_at_point(pt_m: Point2D, floor: Floor) -> Room | None:
    """Return the first Room on floor containing pt_m, or None."""
    for room in floor.rooms:
        if point_in_polygon(pt_m, room.polygon_m):
            return room
    return None


def distance_and_cardinal_bearing(
    from_pt: Point2D,
    to_pt: Point2D,
    anchor_rotation_deg: float = 0.0,
) -> tuple[float, str]:
    """Return (distance_m, 8-wind geographic cardinal direction) accounting for CW anchor rot."""
    dx = to_pt.x - from_pt.x
    dy = to_pt.y - from_pt.y
    dist = math.hypot(dx, dy)
    theta = math.radians(anchor_rotation_deg)
    east = dx * math.cos(theta) + dy * math.sin(theta)
    north = -dx * math.sin(theta) + dy * math.cos(theta)
    azimuth_deg = math.degrees(math.atan2(east, north)) % 360.0
    idx = int((azimuth_deg + 22.5) // 45.0) % 8
    return dist, CARDINAL_DIRECTIONS[idx]


def latlon_to_tile_xy(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    """Convert WGS84 (lat, lon) to slippy Web Mercator tile indices (x, y) at zoom."""
    clamped_lat = max(-_MAX_MERCATOR_LAT, min(_MAX_MERCATOR_LAT, lat))
    n = 1 << zoom
    lat_rad = math.radians(clamped_lat)
    tx = int(math.floor((lon + 180.0) / 360.0 * n))
    ty = int(math.floor((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n))
    tx = max(0, min(n - 1, tx))
    ty = max(0, min(n - 1, ty))
    return tx, ty


def tile_xy_to_latlon_bounds(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """Return (lat_n, lon_w, lat_s, lon_e) WGS84 corners of Web Mercator tile (z, x, y)."""
    n = float(1 << z)
    lon_w = x / n * 360.0 - 180.0
    lon_e = (x + 1) / n * 360.0 - 180.0
    lat_n = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * y / n))))
    lat_s = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * (y + 1) / n))))
    return (lat_n, lon_w, lat_s, lon_e)


def bounding_box_tiles(
    min_lat: float,
    min_lon: float,
    max_lat: float,
    max_lon: float,
    zooms: Sequence[int],
) -> list[tuple[int, int, int]]:
    """Return all (z, x, y) slippy tiles covering the geographic bounding box."""
    tiles: list[tuple[int, int, int]] = []
    for z in zooms:
        x_nw, y_nw = latlon_to_tile_xy(max_lat, min_lon, z)
        x_se, y_se = latlon_to_tile_xy(min_lat, max_lon, z)
        for tx in range(min(x_nw, x_se), max(x_nw, x_se) + 1):
            for ty in range(min(y_nw, y_se), max(y_nw, y_se) + 1):
                tiles.append((z, tx, ty))
    return tiles


def building_latlon_bounds_with_margin(
    buildings: Sequence[Building],
    margin_m: float = 250.0,
) -> tuple[float, float, float, float] | None:
    """Return (min_lat, min_lon, max_lat, max_lon) across anchored buildings plus margin_m."""
    lats: list[float] = []
    lons: list[float] = []
    for bldg in buildings:
        if bldg.geo_anchor is None:
            continue
        if bldg.footprint_latlon:
            for lat, lon in bldg.footprint_latlon:
                lats.append(lat)
                lons.append(lon)
        elif bldg.floors and bldg.floors[0].outer_polygon_m:
            for lat, lon in floor_polygon_to_latlon(
                bldg.floors[0].outer_polygon_m, bldg.geo_anchor
            ):
                lats.append(lat)
                lons.append(lon)
        else:
            lats.append(bldg.geo_anchor.origin_lat)
            lons.append(bldg.geo_anchor.origin_lon)

    if not lats:
        return None
    min_lat, max_lat = min(lats), max(lats)
    min_lon, max_lon = min(lons), max(lons)
    mid_lat = 0.5 * (min_lat + max_lat)
    m_lat, m_lon = meters_per_degree(mid_lat)
    d_lat = margin_m / m_lat
    d_lon = margin_m / max(m_lon, 1e-6)
    return (min_lat - d_lat, min_lon - d_lon, max_lat + d_lat, max_lon + d_lon)


# --- Physical Cable Length & BOM Calculator (Section 4.7) ---


@dataclass
class CableRunBomEntry:
    cable_id: str
    label: str
    port_type: PortType
    horizontal_m: float
    vertical_riser_m: float
    total_with_slack_m: float
    is_inter_floor: bool
    is_estimated: bool


@dataclass
class PortTypeBomSummary:
    port_type: PortType
    run_count: int
    horizontal_m: float
    vertical_riser_m: float
    total_m: float


@dataclass
class ProjectBomSummary:
    slack_factor: float
    runs: list[CableRunBomEntry]
    by_port_type: list[PortTypeBomSummary]
    total_m: float


def _polyline_length(points: Sequence[Point2D]) -> float:
    if len(points) < 2:
        return 0.0
    return sum(
        math.hypot(points[i + 1].x - points[i].x, points[i + 1].y - points[i].y)
        for i in range(len(points) - 1)
    )


def _find_hole_pos(floor: Floor | None, hole_id: str | None) -> Point2D | None:
    if floor is None or hole_id is None:
        return None
    for hole in floor.holes:
        if hole.id == hole_id:
            return hole.position_m
    return None


def find_best_riser_pair(
    floor_a: Floor,
    floor_b: Floor,
    pos_a: Point2D,
    pos_b: Point2D,
) -> tuple[Point2D, Point2D] | None:
    """Find matching VerticalHole pair sharing riser_group_id minimizing horizontal run."""
    best_dist = math.inf
    best_pair: tuple[Point2D, Point2D] | None = None
    for hole_a in floor_a.holes:
        if not hole_a.riser_group_id:
            continue
        for hole_b in floor_b.holes:
            if hole_b.riser_group_id == hole_a.riser_group_id:
                d = math.hypot(pos_a.x - hole_a.position_m.x, pos_a.y - hole_a.position_m.y) + (
                    math.hypot(hole_b.position_m.x - pos_b.x, hole_b.position_m.y - pos_b.y)
                )
                if d < best_dist:
                    best_dist = d
                    best_pair = (hole_a.position_m, hole_b.position_m)
    return best_pair


def _points_close(a: Point2D, b: Point2D, tol: float = 1e-3) -> bool:
    return math.hypot(a.x - b.x, a.y - b.y) <= tol


def calculate_cable_physical_length(
    project: Project,
    cable: Cable,
    slack_factor: float = 0.10,
) -> CableRunBomEntry | None:
    """Compute physical horizontal + vertical riser + slack length for a placed cable."""
    src_comp = project.get_component(cable.source_component_id)
    dst_comp = project.get_component(cable.target_component_id)
    if src_comp is None or dst_comp is None:
        return None
    if (
        src_comp.physical_building_id is None
        or src_comp.physical_floor_id is None
        or src_comp.physical_pos_m is None
        or dst_comp.physical_building_id is None
        or dst_comp.physical_floor_id is None
        or dst_comp.physical_pos_m is None
    ):
        return None

    src_port = project.get_port(src_comp.id, cable.source_port_id)
    port_type = src_port.port_type if src_port is not None else PortType.BASE1000
    src_pos = src_comp.physical_pos_m
    dst_pos = dst_comp.physical_pos_m

    same_building = src_comp.physical_building_id == dst_comp.physical_building_id
    is_inter_floor = same_building and (src_comp.physical_floor_id != dst_comp.physical_floor_id)

    horizontal_m = 0.0
    is_estimated = not bool(cable.physical_segments)

    if cable.physical_segments:
        for idx, seg in enumerate(cable.physical_segments):
            seg_floor = (
                project.get_floor(src_comp.physical_building_id, seg.floor_id)
                or project.get_floor(dst_comp.physical_building_id, seg.floor_id)
            )
            start_anchor = _find_hole_pos(seg_floor, seg.start_hole_id)
            if start_anchor is None and idx == 0:
                start_anchor = src_pos
            end_anchor = _find_hole_pos(seg_floor, seg.end_hole_id)
            if end_anchor is None and idx == len(cable.physical_segments) - 1:
                end_anchor = dst_pos

            pts = list(seg.waypoints_m)
            if start_anchor is not None and (not pts or not _points_close(pts[0], start_anchor)):
                pts.insert(0, start_anchor)
            if end_anchor is not None and (not pts or not _points_close(pts[-1], end_anchor)):
                pts.append(end_anchor)
            horizontal_m += _polyline_length(pts)
    elif same_building and not is_inter_floor:
        horizontal_m = math.hypot(dst_pos.x - src_pos.x, dst_pos.y - src_pos.y)
    elif same_building and is_inter_floor:
        src_floor = project.get_floor(src_comp.physical_building_id, src_comp.physical_floor_id)
        dst_floor = project.get_floor(dst_comp.physical_building_id, dst_comp.physical_floor_id)
        riser_pair = (
            find_best_riser_pair(src_floor, dst_floor, src_pos, dst_pos)
            if src_floor is not None and dst_floor is not None
            else None
        )
        if riser_pair is not None:
            horizontal_m = (
                math.hypot(riser_pair[0].x - src_pos.x, riser_pair[0].y - src_pos.y)
                + math.hypot(dst_pos.x - riser_pair[1].x, dst_pos.y - riser_pair[1].y)
            )
        else:
            horizontal_m = math.hypot(dst_pos.x - src_pos.x, dst_pos.y - src_pos.y)
    else:
        b_src = project.get_building(src_comp.physical_building_id)
        b_dst = project.get_building(dst_comp.physical_building_id)
        if (
            b_src is not None
            and b_dst is not None
            and b_src.geo_anchor is not None
            and b_dst.geo_anchor is not None
        ):
            lat1, lon1 = local_m_to_latlon(src_pos, b_src.geo_anchor)
            lat2, lon2 = local_m_to_latlon(dst_pos, b_dst.geo_anchor)
            m_lat, m_lon = meters_per_degree(0.5 * (lat1 + lat2))
            horizontal_m = math.hypot((lat2 - lat1) * m_lat, (lon2 - lon1) * m_lon)
        else:
            horizontal_m = math.hypot(dst_pos.x - src_pos.x, dst_pos.y - src_pos.y)

    vertical_riser_m = 0.0
    if is_inter_floor:
        bldg = project.get_building(src_comp.physical_building_id)
        src_floor = project.get_floor(src_comp.physical_building_id, src_comp.physical_floor_id)
        dst_floor = project.get_floor(dst_comp.physical_building_id, dst_comp.physical_floor_id)
        if bldg is not None and src_floor is not None and dst_floor is not None:
            level_delta = abs(src_floor.level_index - dst_floor.level_index)
            vertical_riser_m = level_delta * bldg.floor_height_m

    total_with_slack_m = (horizontal_m + vertical_riser_m) * (1.0 + slack_factor)
    label = cable.label or f"{src_comp.name} -> {dst_comp.name}"
    return CableRunBomEntry(
        cable_id=cable.id,
        label=label,
        port_type=port_type,
        horizontal_m=horizontal_m,
        vertical_riser_m=vertical_riser_m,
        total_with_slack_m=total_with_slack_m,
        is_inter_floor=is_inter_floor,
        is_estimated=is_estimated,
    )


def calculate_project_cable_bom(
    project: Project,
    slack_factor: float = 0.10,
) -> ProjectBomSummary:
    """Compute full project physical cable BOM grouped by PortType."""
    runs: list[CableRunBomEntry] = []
    grouped: dict[PortType, list[CableRunBomEntry]] = {}
    for cable in project.cables:
        entry = calculate_cable_physical_length(project, cable, slack_factor=slack_factor)
        if entry is not None:
            runs.append(entry)
            grouped.setdefault(entry.port_type, []).append(entry)

    by_port_type = [
        PortTypeBomSummary(
            port_type=ptype,
            run_count=len(items),
            horizontal_m=sum(i.horizontal_m for i in items),
            vertical_riser_m=sum(i.vertical_riser_m for i in items),
            total_m=sum(i.total_with_slack_m for i in items),
        )
        for ptype, items in sorted(grouped.items(), key=lambda kv: kv[0].value)
    ]
    total_m = sum(r.total_with_slack_m for r in runs)
    return ProjectBomSummary(
        slack_factor=slack_factor,
        runs=runs,
        by_port_type=by_port_type,
        total_m=total_m,
    )
