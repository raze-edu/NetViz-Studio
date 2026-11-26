"""Shapely auto-cut room clipping, magnetic snapping, and wall-locked door geometry."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from shapely.validation import make_valid

from shared.models import Door, Floor, Point2D


@dataclass(frozen=True)
class SnapResult:
    point_m: Point2D
    kind: str  # "vertex" | "wall" | "align" | "grid"
    guide_points: tuple[Point2D, Point2D] | None = None


@dataclass(frozen=True)
class WallHit:
    position_m: Point2D
    angle_deg: float
    distance_m: float
    segment_start: Point2D
    segment_end: Point2D
    host_id: str  # "__outer__" or Room.id


def _to_view_coords(pt: Point2D, view_rotation_deg: float) -> tuple[float, float]:
    theta = math.radians(view_rotation_deg)
    u = pt.x * math.cos(theta) + pt.y * math.sin(theta)
    v = -pt.x * math.sin(theta) + pt.y * math.cos(theta)
    return u, v


def _from_view_coords(u: float, v: float, view_rotation_deg: float) -> Point2D:
    theta = math.radians(view_rotation_deg)
    x = u * math.cos(theta) - v * math.sin(theta)
    y = u * math.sin(theta) + v * math.cos(theta)
    return Point2D(x, y)


def _collect_floor_vertices(
    floor: Floor,
    exclude_room_id: str | None = None,
) -> list[Point2D]:
    vertices: list[Point2D] = list(floor.outer_polygon_m)
    for room in floor.rooms:
        if exclude_room_id is not None and room.id == exclude_room_id:
            continue
        vertices.extend(room.polygon_m)
    return vertices


def _collect_wall_segments(
    floor: Floor,
    exclude_room_id: str | None = None,
) -> list[tuple[Point2D, Point2D, str]]:
    segments: list[tuple[Point2D, Point2D, str]] = []
    rings: list[tuple[str, Sequence[Point2D]]] = [("__outer__", floor.outer_polygon_m)]
    for room in floor.rooms:
        if exclude_room_id is not None and room.id == exclude_room_id:
            continue
        rings.append((room.id, room.polygon_m))
    for host_id, ring in rings:
        n = len(ring)
        if n < 2:
            continue
        for i in range(n):
            a = ring[i]
            b = ring[(i + 1) % n]
            if math.hypot(b.x - a.x, b.y - a.y) >= 1e-6:
                segments.append((a, b, host_id))
    return segments


def _project_pt_to_segment(pt: Point2D, a: Point2D, b: Point2D) -> tuple[Point2D, float]:
    dx = b.x - a.x
    dy = b.y - a.y
    len_sq = dx * dx + dy * dy
    if len_sq <= 1e-18:
        return Point2D(a.x, a.y), math.hypot(pt.x - a.x, pt.y - a.y)
    t = max(0.0, min(1.0, ((pt.x - a.x) * dx + (pt.y - a.y) * dy) / len_sq))
    proj = Point2D(a.x + t * dx, a.y + t * dy)
    return proj, math.hypot(pt.x - proj.x, pt.y - proj.y)


def snap_editor_point(
    raw_pt_m: Point2D,
    floor: Floor,
    *,
    view_rotation_deg: float = 0.0,
    anchor_pt_m: Point2D | None = None,
    shift_lock: bool = False,
    exclude_room_id: str | None = None,
    grid_step_m: float = 0.25,
    vertex_tol_m: float = 0.35,
    wall_tol_m: float = 0.35,
    align_tol_m: float = 0.25,
) -> SnapResult:
    """Snap raw_pt_m using priority: vertex (0.35m) > wall (0.35m) > H/V align (0.25m) > grid."""
    candidate = raw_pt_m
    ray_dir: tuple[float, float] | None = None

    if shift_lock and anchor_pt_m is not None:
        du, dv = _to_view_coords(
            Point2D(raw_pt_m.x - anchor_pt_m.x, raw_pt_m.y - anchor_pt_m.y),
            view_rotation_deg,
        )
        if math.hypot(du, dv) > 1e-6:
            angle = math.atan2(dv, du)
            snapped_angle = round(angle / (math.pi / 4.0)) * (math.pi / 4.0)
            ru = math.cos(snapped_angle)
            rv = math.sin(snapped_angle)
            proj_len = max(0.0, du * ru + dv * rv)
            candidate_offset = _from_view_coords(
                proj_len * ru, proj_len * rv, view_rotation_deg
            )
            candidate = Point2D(
                anchor_pt_m.x + candidate_offset.x,
                anchor_pt_m.y + candidate_offset.y,
            )
            ray_vec = _from_view_coords(ru, rv, view_rotation_deg)
            ray_dir = (ray_vec.x, ray_vec.y)

    def _on_locked_ray(pt: Point2D) -> bool:
        if ray_dir is None or anchor_pt_m is None:
            return True
        vx = pt.x - anchor_pt_m.x
        vy = pt.y - anchor_pt_m.y
        proj = vx * ray_dir[0] + vy * ray_dir[1]
        if proj < -1e-6:
            return False
        perp = abs(vx * ray_dir[1] - vy * ray_dir[0])
        return perp <= 1e-4

    vertices = _collect_floor_vertices(floor, exclude_room_id=exclude_room_id)
    segments = _collect_wall_segments(floor, exclude_room_id=exclude_room_id)

    # 1. Existing vertex within vertex_tol_m
    best_v: Point2D | None = None
    best_v_dist = vertex_tol_m
    for v in vertices:
        d = math.hypot(candidate.x - v.x, candidate.y - v.y)
        if d <= best_v_dist and _on_locked_ray(v):
            best_v_dist = d
            best_v = v
    if best_v is not None:
        return SnapResult(point_m=Point2D(best_v.x, best_v.y), kind="vertex")

    # 2. Wall segment projection within wall_tol_m
    best_w: Point2D | None = None
    best_w_dist = wall_tol_m
    for a, b, _ in segments:
        proj, d = _project_pt_to_segment(candidate, a, b)
        if d <= best_w_dist and _on_locked_ray(proj):
            best_w_dist = d
            best_w = proj
    if best_w is not None:
        return SnapResult(point_m=best_w, kind="wall")

    # 3. View-rotated H/V alignment guide with existing vertex within align_tol_m
    cand_u, cand_v = _to_view_coords(candidate, view_rotation_deg)
    best_align_pt: Point2D | None = None
    best_align_ref: Point2D | None = None
    best_align_err = align_tol_m

    for ref in vertices:
        ref_u, ref_v = _to_view_coords(ref, view_rotation_deg)
        err_u = abs(cand_u - ref_u)
        err_v = abs(cand_v - ref_v)
        if err_u <= best_align_err:
            aligned = _from_view_coords(ref_u, cand_v, view_rotation_deg)
            if _on_locked_ray(aligned):
                best_align_err = err_u
                best_align_pt = aligned
                best_align_ref = ref
        if err_v <= best_align_err:
            aligned = _from_view_coords(cand_u, ref_v, view_rotation_deg)
            if _on_locked_ray(aligned):
                best_align_err = err_v
                best_align_pt = aligned
                best_align_ref = ref

    if best_align_pt is not None and best_align_ref is not None:
        return SnapResult(
            point_m=best_align_pt,
            kind="align",
            guide_points=(Point2D(best_align_ref.x, best_align_ref.y), best_align_pt),
        )

    # 4. View-rotated grid (0.25 m)
    if ray_dir is not None and anchor_pt_m is not None:
        dx = candidate.x - anchor_pt_m.x
        dy = candidate.y - anchor_pt_m.y
        dist = math.hypot(dx, dy)
        if grid_step_m > 0:
            dist = round(dist / grid_step_m) * grid_step_m
        snapped = Point2D(anchor_pt_m.x + dist * ray_dir[0], anchor_pt_m.y + dist * ray_dir[1])
        return SnapResult(point_m=snapped, kind="grid")

    if grid_step_m > 0:
        gu = round(cand_u / grid_step_m) * grid_step_m
        gv = round(cand_v / grid_step_m) * grid_step_m
        return SnapResult(point_m=_from_view_coords(gu, gv, view_rotation_deg), kind="grid")
    return SnapResult(point_m=Point2D(candidate.x, candidate.y), kind="grid")


def _largest_polygon(geom: BaseGeometry) -> Polygon | None:
    if geom.is_empty:
        return None
    if isinstance(geom, Polygon):
        return geom
    polys = [g for g in getattr(geom, "geoms", []) if isinstance(g, Polygon) and not g.is_empty]
    if not polys:
        # Handle nested GeometryCollections cleanly
        for g in getattr(geom, "geoms", []):
            nested = _largest_polygon(g)
            if nested is not None:
                polys.append(nested)
    if not polys:
        return None
    return max(polys, key=lambda p: p.area)


def clip_and_cut_room_polygon(
    new_pts: Sequence[Point2D],
    floor: Floor,
    *,
    exclude_room_id: str | None = None,
    clip_to_outer: bool = True,
    cut_existing_rooms: bool = True,
) -> list[Point2D] | None:
    """Clip new_pts to floor.outer_polygon_m and cut overlapping rooms via Shapely."""
    if len(new_pts) < 3:
        return None

    poly = _largest_polygon(make_valid(Polygon([(p.x, p.y) for p in new_pts])))
    if poly is None or poly.area < 0.25:
        return None

    if clip_to_outer and len(floor.outer_polygon_m) >= 3:
        outer = _largest_polygon(
            make_valid(Polygon([(p.x, p.y) for p in floor.outer_polygon_m]))
        )
        if outer is not None:
            poly = _largest_polygon(make_valid(poly.intersection(outer)))
            if poly is None or poly.area < 0.25:
                return None

    if cut_existing_rooms:
        other_polys: list[Polygon] = []
        for room in floor.rooms:
            if exclude_room_id is not None and room.id == exclude_room_id:
                continue
            if len(room.polygon_m) < 3:
                continue
            rp = _largest_polygon(make_valid(Polygon([(p.x, p.y) for p in room.polygon_m])))
            if rp is not None and rp.area > 1e-6:
                other_polys.append(rp)
        if other_polys:
            others_union = unary_union(other_polys)
            poly = _largest_polygon(make_valid(poly.difference(others_union)))
            if poly is None or poly.area < 0.25:
                return None

    simplified = _largest_polygon(make_valid(poly.simplify(0.02, preserve_topology=True)))
    if simplified is None or simplified.area < 0.25:
        return None

    coords = list(simplified.exterior.coords)
    if len(coords) > 1 and coords[0] == coords[-1]:
        coords = coords[:-1]
    if len(coords) < 3:
        return None
    return [Point2D(float(x), float(y)) for x, y in coords]


def project_point_to_walls(
    pt_m: Point2D,
    floor: Floor,
    max_dist_m: float = 1.5,
) -> WallHit | None:
    """Project pt_m onto the closest wall of floor.outer_polygon_m or any room."""
    segments = _collect_wall_segments(floor)
    best_hit: WallHit | None = None
    best_dist = max_dist_m

    for a, b, host_id in segments:
        proj, dist = _project_pt_to_segment(pt_m, a, b)
        if dist <= best_dist:
            best_dist = dist
            angle_deg = math.degrees(math.atan2(b.y - a.y, b.x - a.x)) % 180.0
            best_hit = WallHit(
                position_m=proj,
                angle_deg=angle_deg,
                distance_m=dist,
                segment_start=a,
                segment_end=b,
                host_id=host_id,
            )
    return best_hit


def reconcile_doors_after_wall_change(
    floor: Floor,
    max_dist_m: float = 0.5,
) -> list[Door]:
    """Re-snap floor.doors onto surviving walls or remove orphaned doors; return removed doors."""
    kept: list[Door] = []
    removed: list[Door] = []
    for door in floor.doors:
        hit = project_point_to_walls(door.position_m, floor, max_dist_m=max_dist_m)
        if hit is None:
            removed.append(door)
        else:
            door.position_m = hit.position_m
            door.angle_deg = hit.angle_deg
            kept.append(door)
    floor.doors = kept
    return removed
