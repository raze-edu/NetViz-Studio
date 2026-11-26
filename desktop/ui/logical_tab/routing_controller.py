"""Orthogonal Manhattan routing, bundle fan-out/offsets, Align/Distribute, and Auto-Layout."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence

from shared.models import Cable, Component, ComponentType, Point2D

TIER_BY_COMPONENT_TYPE: dict[ComponentType, int] = {
    ComponentType.ROUTER: 0,
    ComponentType.FIREWALL: 0,
    ComponentType.ACTIVE_SWITCH: 1,
    ComponentType.PASSIVE_SWITCH: 2,
    ComponentType.PATCH_PANEL: 2,
    ComponentType.LWL_CONVERTER: 2,
    ComponentType.POWERLAN_ADAPTER: 2,
    ComponentType.ACCESS_POINT: 3,
    ComponentType.DEVICE: 3,
}


def _dedupe_consecutive(points: Sequence[Point2D], tol: float = 1e-4) -> list[Point2D]:
    out: list[Point2D] = []
    for pt in points:
        if not out or math.hypot(pt.x - out[-1].x, pt.y - out[-1].y) > tol:
            out.append(Point2D(float(pt.x), float(pt.y)))
    return out


def compute_orthogonal_waypoints(
    src: Point2D,
    dst: Point2D,
    *,
    stub_len: float = 28.0,
) -> list[Point2D]:
    """Compute intermediate Manhattan waypoints so [src, *waypoints, dst] is strictly orthogonal."""
    dx = dst.x - src.x
    dy = dst.y - src.y
    if abs(dx) < 1e-4 or abs(dy) < 1e-4:
        return []

    if dy >= 2.0 * stub_len:
        mid_y = 0.5 * (src.y + dst.y)
        candidates = [Point2D(src.x, mid_y), Point2D(dst.x, mid_y)]
    elif dy >= 0.0:
        drop_y = max(src.y, dst.y) + stub_len
        candidates = [
            Point2D(src.x, drop_y),
            Point2D(dst.x, drop_y),
        ]
    else:
        src_drop_y = src.y + stub_len
        dst_drop_y = dst.y + stub_len
        if abs(dx) >= 40.0:
            mid_x = 0.5 * (src.x + dst.x)
            candidates = [
                Point2D(src.x, src_drop_y),
                Point2D(mid_x, src_drop_y),
                Point2D(mid_x, dst_drop_y),
                Point2D(dst.x, dst_drop_y),
            ]
        else:
            side_x = max(src.x, dst.x) + 40.0
            candidates = [
                Point2D(src.x, src_drop_y),
                Point2D(side_x, src_drop_y),
                Point2D(side_x, dst_drop_y),
                Point2D(dst.x, dst_drop_y),
            ]

    # Prune any redundant collinear points while preserving strict orthogonality
    full = _dedupe_consecutive([src, *candidates, dst])
    cleaned: list[Point2D] = [full[0]]
    for i in range(1, len(full) - 1):
        prev_pt = cleaned[-1]
        cur_pt = full[i]
        next_pt = full[i + 1]
        same_x = abs(prev_pt.x - cur_pt.x) < 1e-4 and abs(cur_pt.x - next_pt.x) < 1e-4
        same_y = abs(prev_pt.y - cur_pt.y) < 1e-4 and abs(cur_pt.y - next_pt.y) < 1e-4
        if not (same_x or same_y):
            cleaned.append(cur_pt)
    cleaned.append(full[-1])
    return cleaned[1:-1]


def choose_bundle_entry_exit(
    src: Point2D,
    dst: Point2D,
    entry_point: Point2D,
    exit_point: Point2D,
) -> tuple[Point2D, Point2D, bool]:
    """
    Pick non-crossing fan-out orientation for a cable entering/exiting a bundle.

    Compares dist(src, entry) + dist(dst, exit) vs dist(src, exit) + dist(dst, entry).
    Returns (src_anchor, dst_anchor, reversed_trunk).
    """
    forward_cost = math.hypot(src.x - entry_point.x, src.y - entry_point.y) + math.hypot(
        dst.x - exit_point.x, dst.y - exit_point.y
    )
    reverse_cost = math.hypot(src.x - exit_point.x, src.y - exit_point.y) + math.hypot(
        dst.x - entry_point.x, dst.y - entry_point.y
    )
    if reverse_cost + 1e-6 < forward_cost:
        return (exit_point, entry_point, True)
    return (entry_point, exit_point, False)


def compute_bundle_parallel_offsets(
    member_count: int,
    spacing_px: float = 5.0,
) -> list[float]:
    """Return symmetric normal offsets (i - (N-1)/2) * spacing_px for expanded bundles."""
    if member_count <= 0:
        return []
    return [(i - (member_count - 1) / 2.0) * spacing_px for i in range(member_count)]


def offset_polyline_by_normal(points: Sequence[Point2D], offset_px: float) -> list[Point2D]:
    """Offset a polyline perpendicular to its segments by offset_px with clean miter joins."""
    pts = _dedupe_consecutive(points)
    if len(pts) < 2 or abs(offset_px) < 1e-6:
        return [Point2D(p.x, p.y) for p in pts]

    seg_normals: list[tuple[float, float]] = []
    for i in range(len(pts) - 1):
        dx = pts[i + 1].x - pts[i].x
        dy = pts[i + 1].y - pts[i].y
        length = math.hypot(dx, dy)
        if length < 1e-6:
            seg_normals.append((0.0, 0.0))
        else:
            seg_normals.append((-dy / length, dx / length))

    result: list[Point2D] = []
    for i, pt in enumerate(pts):
        if i == 0:
            nx, ny = seg_normals[0]
            result.append(Point2D(pt.x + nx * offset_px, pt.y + ny * offset_px))
        elif i == len(pts) - 1:
            nx, ny = seg_normals[-1]
            result.append(Point2D(pt.x + nx * offset_px, pt.y + ny * offset_px))
        else:
            n1x, n1y = seg_normals[i - 1]
            n2x, n2y = seg_normals[i]
            mx = n1x + n2x
            my = n1y + n2y
            mlen = math.hypot(mx, my)
            if mlen < 1e-5:
                result.append(Point2D(pt.x + n1x * offset_px, pt.y + n1y * offset_px))
            else:
                mx /= mlen
                my /= mlen
                cos_half = max(0.35, mx * n1x + my * n1y)
                scale = offset_px / cos_half
                result.append(Point2D(pt.x + mx * scale, pt.y + my * scale))
    return result


def compute_alignment_moves(
    rects: Mapping[str, tuple[float, float, float, float]],
    mode: str,
) -> dict[str, Point2D]:
    """
    Compute aligned logical_pos moves from {id: (x, y, width, height)}.

    Supported modes: 'left', 'center', 'right', 'top', 'middle', 'bottom'.
    """
    if len(rects) < 2:
        return {}
    key = mode.strip().lower()
    xs = [r[0] for r in rects.values()]
    ys = [r[1] for r in rects.values()]
    rights = [r[0] + r[2] for r in rects.values()]
    bottoms = [r[1] + r[3] for r in rects.values()]

    moves: dict[str, Point2D] = {}
    if key == "left":
        target = min(xs)
        for cid, (_x, y, _w, _h) in rects.items():
            moves[cid] = Point2D(target, y)
    elif key == "right":
        target = max(rights)
        for cid, (_x, y, w, _h) in rects.items():
            moves[cid] = Point2D(target - w, y)
    elif key == "center":
        target = 0.5 * (min(xs) + max(rights))
        for cid, (_x, y, w, _h) in rects.items():
            moves[cid] = Point2D(target - 0.5 * w, y)
    elif key == "top":
        target = min(ys)
        for cid, (x, _y, _w, _h) in rects.items():
            moves[cid] = Point2D(x, target)
    elif key == "bottom":
        target = max(bottoms)
        for cid, (x, _y, _w, h) in rects.items():
            moves[cid] = Point2D(x, target - h)
    elif key == "middle":
        target = 0.5 * (min(ys) + max(bottoms))
        for cid, (x, _y, _w, h) in rects.items():
            moves[cid] = Point2D(x, target - 0.5 * h)
    else:
        raise ValueError(f"Unsupported alignment mode: {mode!r}")
    return moves


def compute_distribution_moves(
    rects: Mapping[str, tuple[float, float, float, float]],
    axis: str,
) -> dict[str, Point2D]:
    """
    Evenly distribute component centers along 'horizontal'/'h' or 'vertical'/'v'.
    """
    if len(rects) < 3:
        return {}
    key = axis.strip().lower()
    moves: dict[str, Point2D] = {}

    if key in ("horizontal", "h"):
        ordered = sorted(rects.items(), key=lambda kv: kv[1][0] + 0.5 * kv[1][2])
        first_c = ordered[0][1][0] + 0.5 * ordered[0][1][2]
        last_c = ordered[-1][1][0] + 0.5 * ordered[-1][1][2]
        step = (last_c - first_c) / (len(ordered) - 1)
        for idx, (cid, (_x, y, w, _h)) in enumerate(ordered):
            new_c = first_c + idx * step
            moves[cid] = Point2D(new_c - 0.5 * w, y)
    elif key in ("vertical", "v"):
        ordered = sorted(rects.items(), key=lambda kv: kv[1][1] + 0.5 * kv[1][3])
        first_c = ordered[0][1][1] + 0.5 * ordered[0][1][3]
        last_c = ordered[-1][1][1] + 0.5 * ordered[-1][1][3]
        step = (last_c - first_c) / (len(ordered) - 1)
        for idx, (cid, (x, _y, _w, h)) in enumerate(ordered):
            new_c = first_c + idx * step
            moves[cid] = Point2D(x, new_c - 0.5 * h)
    else:
        raise ValueError(f"Unsupported distribution axis: {axis!r}")
    return moves


def estimate_node_size(component: Component) -> tuple[float, float]:
    """Estimate logical canvas node (width, height) cleanly matching ComponentNodeItem."""
    n = len(component.ports)
    if n <= 12:
        cols = max(1, n)
        width = max(190.0, cols * 32.0 + 28.0)
        height = 92.0
    else:
        cols = math.ceil(n / 2.0)
        width = max(240.0, cols * 30.0 + 28.0)
        height = 122.0
    return (width, height)


def compute_hierarchical_layout(
    components: Sequence[Component],
    cables: Sequence[Cable],
    *,
    tier_gap_y: float = 190.0,
    node_gap_x: float = 60.0,
    origin_x: float = 60.0,
    origin_y: float = 60.0,
) -> dict[str, Point2D]:
    """
    Arrange components into 4 architectural tiers with barycenter crossing minimization.

    Tier 0: Router, Firewall
    Tier 1: Active Switch
    Tier 2: Passive Switch, Patch Panel, LWL Converter, PowerLAN Adapter
    Tier 3: Access Point, End Device
    """
    if not components:
        return {}

    comp_map = {c.id: c for c in components}
    tiers: dict[int, list[str]] = {0: [], 1: [], 2: [], 3: []}
    for comp in components:
        tier = TIER_BY_COMPONENT_TYPE.get(comp.component_type, 3)
        tiers[tier].append(comp.id)

    for t in tiers:
        tiers[t].sort(key=lambda cid: (comp_map[cid].logical_pos.x, comp_map[cid].name))

    # Build undirected adjacency list
    adj: dict[str, list[str]] = defaultdict(list)
    for cab in cables:
        if cab.source_component_id in comp_map and cab.target_component_id in comp_map:
            if cab.source_component_id != cab.target_component_id:
                adj[cab.source_component_id].append(cab.target_component_id)
                adj[cab.target_component_id].append(cab.source_component_id)

    tier_of: dict[str, int] = {
        cid: t for t, cids in tiers.items() for cid in cids
    }

    def _order_map() -> dict[str, float]:
        pos: dict[str, float] = {}
        for cids in tiers.values():
            for idx, cid in enumerate(cids):
                pos[cid] = float(idx)
        return pos

    # 6 alternating top-down / bottom-up barycenter sweeps
    for _ in range(6):
        positions = _order_map()
        for t in (1, 2, 3):
            upper = [cid for cid in tiers[t]]
            upper.sort(
                key=lambda cid: _barycenter_key(
                    cid, adj[cid], tier_of, positions, direction=-1, fallback=positions[cid]
                )
            )
            tiers[t] = upper
            positions = _order_map()
        for t in (2, 1, 0):
            lower = [cid for cid in tiers[t]]
            lower.sort(
                key=lambda cid: _barycenter_key(
                    cid, adj[cid], tier_of, positions, direction=1, fallback=positions[cid]
                )
            )
            tiers[t] = lower
            positions = _order_map()

    sizes = {c.id: estimate_node_size(c) for c in components}
    row_widths: dict[int, float] = {}
    for t, cids in tiers.items():
        if not cids:
            row_widths[t] = 0.0
        else:
            row_widths[t] = sum(sizes[cid][0] for cid in cids) + node_gap_x * (len(cids) - 1)

    max_row_width = max(row_widths.values(), default=0.0)
    layout: dict[str, Point2D] = {}

    active_tier_index = 0
    for t in (0, 1, 2, 3):
        cids = tiers[t]
        if not cids:
            continue
        cur_x = origin_x + max(0.0, 0.5 * (max_row_width - row_widths[t]))
        cur_y = origin_y + active_tier_index * tier_gap_y
        for cid in cids:
            layout[cid] = Point2D(cur_x, cur_y)
            cur_x += sizes[cid][0] + node_gap_x
        active_tier_index += 1

    return layout


def _barycenter_key(
    cid: str,
    neighbors: Sequence[str],
    tier_of: Mapping[str, int],
    positions: Mapping[str, float],
    *,
    direction: int,
    fallback: float,
) -> tuple[float, float, str]:
    my_tier = tier_of[cid]
    ref_vals = [
        positions[nb]
        for nb in neighbors
        if (direction < 0 and tier_of[nb] < my_tier)
        or (direction > 0 and tier_of[nb] > my_tier)
    ]
    if not ref_vals:
        # Consider all connected neighbors if none in strict direction
        ref_vals = [positions[nb] for nb in neighbors if nb in positions]
    bary = sum(ref_vals) / len(ref_vals) if ref_vals else fallback
    return (bary, fallback, cid)
