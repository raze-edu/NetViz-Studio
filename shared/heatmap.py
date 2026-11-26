"""Vectorized NumPy + pure-Python IDW Wi-Fi heatmap and analytical pre-survey estimator."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from shared.geo_math import point_in_polygon, polygon_bounds
from shared.models import (
    ComponentType,
    Floor,
    Point2D,
    Project,
    SurveySample,
    normalize_mac,
)

try:
    import numpy as np

    _HAS_NUMPY = True
except ImportError:  # pragma: no cover
    np = None  # type: ignore[assignment]
    _HAS_NUMPY = False

# 5-Stop Perceptual dBm Colormap (Section 4.8A)
_COLOR_STOPS: list[tuple[float, tuple[int, int, int]]] = [
    (-95.0, (239, 68, 68)),   # #EF4444
    (-80.0, (249, 115, 22)),  # #F97316
    (-70.0, (234, 179, 8)),   # #EAB308
    (-60.0, (132, 204, 22)),  # #84CC16
    (-30.0, (34, 197, 94)),   # #22C55E
]

_BASE_ALPHA = 210.0


@dataclass
class HeatmapResult:
    rgba_bytes: bytes
    width: int
    height: int
    bounds: tuple[float, float, float, float]  # (min_x, min_y, max_x, max_y)
    resolution_m: float
    valid_pixel_count: int
    above_threshold_count: int
    coverage_percent: float


def frequency_to_band(frequency_mhz: int) -> str | None:
    """Classify a Wi-Fi center frequency in MHz into '2.4', '5', or '6'."""
    if 2400 <= frequency_mhz <= 2500:
        return "2.4"
    if 5000 <= frequency_mhz < 5925:
        return "5"
    if 5925 <= frequency_mhz <= 7125:
        return "6"
    return None


def rssi_to_rgb(rssi_dbm: float) -> tuple[int, int, int]:
    """Map RSSI dBm using the 5-stop Section 4.8A colormap."""
    if rssi_dbm <= _COLOR_STOPS[0][0]:
        return _COLOR_STOPS[0][1]
    if rssi_dbm >= _COLOR_STOPS[-1][0]:
        return _COLOR_STOPS[-1][1]
    for i in range(len(_COLOR_STOPS) - 1):
        db0, c0 = _COLOR_STOPS[i]
        db1, c1 = _COLOR_STOPS[i + 1]
        if db0 <= rssi_dbm <= db1:
            t = (rssi_dbm - db0) / (db1 - db0)
            r = int(round(c0[0] + t * (c1[0] - c0[0])))
            g = int(round(c0[1] + t * (c1[1] - c0[1])))
            b = int(round(c0[2] + t * (c1[2] - c0[2])))
            return (r, g, b)
    return _COLOR_STOPS[-1][1]


def filter_samples_for_floor(
    samples: Sequence[SurveySample],
    floor_id: str,
    target_ssids: Sequence[str] | None = None,
    bssid_filter: str | None = None,
    band_filter: str | None = None,
) -> list[tuple[Point2D, float]]:
    """Aggregate strongest matching AP RSSI per sample on floor_id."""
    ssid_set = {s.strip() for s in target_ssids if s and s.strip()} if target_ssids else None
    bssid_norm = normalize_mac(bssid_filter) if bssid_filter else ""
    band = band_filter.strip() if band_filter and band_filter.lower() != "all" else None

    points: list[tuple[Point2D, float]] = []
    for sample in samples:
        if sample.floor_id != floor_id:
            continue
        best_rssi: int | None = None
        for reading in sample.readings:
            if ssid_set is not None and reading.ssid.strip() not in ssid_set:
                continue
            if bssid_norm and normalize_mac(reading.bssid) != bssid_norm:
                continue
            if band is not None and frequency_to_band(reading.frequency_mhz) != band:
                continue
            if best_rssi is None or reading.rssi_dbm > best_rssi:
                best_rssi = reading.rssi_dbm
        if best_rssi is not None:
            points.append((sample.pos_m, float(best_rssi)))
    return points


def _grid_dimensions(
    bounds: tuple[float, float, float, float],
    resolution_m: float,
) -> tuple[int, int]:
    min_x, min_y, max_x, max_y = bounds
    span_x = max(max_x - min_x, 1e-3)
    span_y = max(max_y - min_y, 1e-3)
    width = max(2, math.ceil(span_x / resolution_m))
    height = max(2, math.ceil(span_y / resolution_m))
    return width, height


def _alpha_for_min_distance(min_dist: float, max_radius_m: float) -> int:
    if min_dist > max_radius_m:
        return 0
    fade_start = 0.75 * max_radius_m
    if min_dist <= fade_start:
        return int(_BASE_ALPHA)
    t = 1.0 - (min_dist - fade_start) / max(max_radius_m - fade_start, 1e-6)
    return max(0, min(255, int(round(_BASE_ALPHA * t))))


def _render_from_rssi_grid(
    rssi_grid: list[list[float | None]],
    alpha_grid: list[list[int]],
    inside_count: int,
    dead_zone_dbm: float,
) -> tuple[bytes, int, float]:
    height = len(rssi_grid)
    width = len(rssi_grid[0]) if height else 0
    buf = bytearray(width * height * 4)
    above_count = 0

    idx = 0
    for row in range(height):
        for col in range(width):
            rssi = rssi_grid[row][col]
            alpha = alpha_grid[row][col]
            if rssi is not None and alpha > 0:
                r, g, b = rssi_to_rgb(rssi)
                buf[idx] = r
                buf[idx + 1] = g
                buf[idx + 2] = b
                buf[idx + 3] = alpha
                if rssi >= dead_zone_dbm:
                    above_count += 1
            idx += 4

    coverage = (100.0 * above_count / inside_count) if inside_count > 0 else 0.0
    return bytes(buf), above_count, coverage


def _compute_idw_pure_python(
    floor: Floor,
    points: Sequence[tuple[Point2D, float]],
    bounds: tuple[float, float, float, float],
    width: int,
    height: int,
    resolution_m: float,
    power: float,
    max_radius_m: float,
    dead_zone_dbm: float,
) -> HeatmapResult:
    min_x, min_y, max_x, max_y = bounds
    span_x = max_x - min_x
    span_y = max_y - min_y

    rssi_grid: list[list[float | None]] = [[None] * width for _ in range(height)]
    alpha_grid: list[list[int]] = [[0] * width for _ in range(height)]
    inside_count = 0

    for row in range(height):
        y_m = max_y - (row + 0.5) * span_y / height
        for col in range(width):
            x_m = min_x + (col + 0.5) * span_x / width
            if not point_in_polygon(Point2D(x_m, y_m), floor.outer_polygon_m):
                continue
            inside_count += 1
            if not points:
                continue

            min_dist = math.inf
            exact_sum = 0.0
            exact_n = 0
            weight_sum = 0.0
            weighted_val = 0.0

            for pt, rssi in points:
                d = math.hypot(x_m - pt.x, y_m - pt.y)
                if d < min_dist:
                    min_dist = d
                if d < 1e-6:
                    exact_sum += rssi
                    exact_n += 1
                else:
                    w = 1.0 / (d**power)
                    weight_sum += w
                    weighted_val += w * rssi

            alpha = _alpha_for_min_distance(min_dist, max_radius_m)
            if alpha <= 0:
                continue
            alpha_grid[row][col] = alpha
            rssi_grid[row][col] = (
                (exact_sum / exact_n) if exact_n > 0 else (weighted_val / weight_sum)
            )

    valid_pixel_count = inside_count if points else 0
    rgba_bytes, above_count, coverage = _render_from_rssi_grid(
        rssi_grid, alpha_grid, valid_pixel_count, dead_zone_dbm
    )
    return HeatmapResult(
        rgba_bytes=rgba_bytes,
        width=width,
        height=height,
        bounds=bounds,
        resolution_m=resolution_m,
        valid_pixel_count=valid_pixel_count,
        above_threshold_count=above_count,
        coverage_percent=coverage,
    )


def _points_in_polygon_numpy(
    grid_x: np.ndarray,
    grid_y: np.ndarray,
    polygon_m: Sequence[Point2D],
) -> np.ndarray:
    """Vectorized ray-casting + inclusive boundary tolerance for an arbitrary polygon."""
    n = len(polygon_m)
    if n < 3:
        return np.zeros(grid_x.shape, dtype=bool)

    inside = np.zeros(grid_x.shape, dtype=bool)
    on_edge = np.zeros(grid_x.shape, dtype=bool)
    tol_sq = 1e-12

    j = n - 1
    for i in range(n):
        xi, yi = polygon_m[i].x, polygon_m[i].y
        xj, yj = polygon_m[j].x, polygon_m[j].y

        dx = xj - xi
        dy = yj - yi
        len_sq = dx * dx + dy * dy
        if len_sq <= 1e-18:
            dist_sq = (grid_x - xi) ** 2 + (grid_y - yi) ** 2
        else:
            t = np.clip(((grid_x - xi) * dx + (grid_y - yi) * dy) / len_sq, 0.0, 1.0)
            dist_sq = (grid_x - (xi + t * dx)) ** 2 + (grid_y - (yi + t * dy)) ** 2
        on_edge |= dist_sq <= tol_sq

        if yi != yj:
            cond_y = (yi > grid_y) != (yj > grid_y)
            x_intersect = (xj - xi) * (grid_y - yi) / (yj - yi) + xi
            inside ^= cond_y & (grid_x < x_intersect)
        j = i

    return inside | on_edge


def _colorize_numpy(
    rssi_flat: np.ndarray,
    alpha_flat: np.ndarray,
    inside_mask: np.ndarray,
    dead_zone_dbm: float,
    width: int,
    height: int,
) -> tuple[bytes, int, float]:
    n_pix = width * height
    rgba = np.zeros((n_pix, 4), dtype=np.uint8)
    active = inside_mask & (alpha_flat > 0)

    if np.any(active):
        vals = rssi_flat[active]
        rgb = np.zeros((vals.size, 3), dtype=np.float64)

        low_mask = vals <= _COLOR_STOPS[0][0]
        high_mask = vals >= _COLOR_STOPS[-1][0]
        rgb[low_mask] = _COLOR_STOPS[0][1]
        rgb[high_mask] = _COLOR_STOPS[-1][1]

        for i in range(len(_COLOR_STOPS) - 1):
            db0, c0 = _COLOR_STOPS[i]
            db1, c1 = _COLOR_STOPS[i + 1]
            seg = (vals > db0) & (vals < db1)
            if np.any(seg):
                t = (vals[seg] - db0) / (db1 - db0)
                rgb[seg, 0] = c0[0] + t * (c1[0] - c0[0])
                rgb[seg, 1] = c0[1] + t * (c1[1] - c0[1])
                rgb[seg, 2] = c0[2] + t * (c1[2] - c0[2])

        rgba[active, :3] = np.clip(np.rint(rgb), 0, 255).astype(np.uint8)
        rgba[active, 3] = alpha_flat[active]

    inside_count = int(np.count_nonzero(inside_mask))
    above_count = int(np.count_nonzero(active & (rssi_flat >= dead_zone_dbm)))
    coverage = (100.0 * above_count / inside_count) if inside_count > 0 else 0.0
    return rgba.tobytes(), above_count, coverage


def _compute_idw_numpy(
    floor: Floor,
    points: Sequence[tuple[Point2D, float]],
    bounds: tuple[float, float, float, float],
    width: int,
    height: int,
    resolution_m: float,
    power: float,
    max_radius_m: float,
    dead_zone_dbm: float,
) -> HeatmapResult:
    min_x, min_y, max_x, max_y = bounds
    span_x = max_x - min_x
    span_y = max_y - min_y

    cols = np.arange(width, dtype=np.float64)
    rows = np.arange(height, dtype=np.float64)
    xs = min_x + (cols + 0.5) * span_x / width
    ys = max_y - (rows + 0.5) * span_y / height  # row 0 = max_y (North)
    grid_x, grid_y = np.meshgrid(xs, ys)

    inside_mask = _points_in_polygon_numpy(grid_x, grid_y, floor.outer_polygon_m).ravel()
    inside_count = int(np.count_nonzero(inside_mask))

    if not points or inside_count == 0:
        return HeatmapResult(
            rgba_bytes=bytes(width * height * 4),
            width=width,
            height=height,
            bounds=bounds,
            resolution_m=resolution_m,
            valid_pixel_count=0,
            above_threshold_count=0,
            coverage_percent=0.0,
        )

    px_in = grid_x.ravel()[inside_mask]
    py_in = grid_y.ravel()[inside_mask]
    sx = np.array([pt.x for pt, _ in points], dtype=np.float64)
    sy = np.array([pt.y for pt, _ in points], dtype=np.float64)
    srssi = np.array([val for _, val in points], dtype=np.float64)

    dx = px_in[:, None] - sx[None, :]
    dy = py_in[:, None] - sy[None, :]
    dists = np.hypot(dx, dy)
    min_dists = np.min(dists, axis=1)

    fade_start = 0.75 * max_radius_m
    alpha_in = np.zeros(px_in.shape, dtype=np.float64)
    within = min_dists <= max_radius_m
    solid = min_dists <= fade_start
    fading = within & ~solid
    alpha_in[solid] = _BASE_ALPHA
    alpha_in[fading] = _BASE_ALPHA * (
        1.0 - (min_dists[fading] - fade_start) / max(max_radius_m - fade_start, 1e-6)
    )
    alpha_u8_in = np.clip(np.rint(alpha_in), 0, 255).astype(np.uint8)

    exact_mask = dists < 1e-6
    any_exact = np.any(exact_mask, axis=1)
    safe_dists = np.where(exact_mask, 1.0, dists)
    weights = np.where(exact_mask, 0.0, 1.0 / (safe_dists**power))
    weight_sums = np.sum(weights, axis=1)
    rssi_in = np.where(
        any_exact,
        np.sum(np.where(exact_mask, srssi[None, :], 0.0), axis=1)
        / np.maximum(np.sum(exact_mask, axis=1), 1),
        (weights @ srssi) / np.maximum(weight_sums, 1e-12),
    )

    rssi_flat = np.full(width * height, -100.0, dtype=np.float64)
    alpha_flat = np.zeros(width * height, dtype=np.uint8)
    rssi_flat[inside_mask] = rssi_in
    alpha_flat[inside_mask] = alpha_u8_in

    rgba_bytes, above_count, coverage = _colorize_numpy(
        rssi_flat, alpha_flat, inside_mask, dead_zone_dbm, width, height
    )
    return HeatmapResult(
        rgba_bytes=rgba_bytes,
        width=width,
        height=height,
        bounds=bounds,
        resolution_m=resolution_m,
        valid_pixel_count=inside_count,
        above_threshold_count=above_count,
        coverage_percent=coverage,
    )


def compute_idw_heatmap(
    floor: Floor,
    samples: Sequence[SurveySample],
    *,
    target_ssids: Sequence[str] | None = None,
    bssid_filter: str | None = None,
    band_filter: str | None = None,
    resolution_m: float = 0.25,
    power: float = 2.0,
    max_radius_m: float = 25.0,
    dead_zone_dbm: float = -70.0,
    force_pure_python: bool = False,
) -> HeatmapResult:
    """Compute clipped IDW Wi-Fi heatmap using Section 4.8A pixel-to-ENU mapping."""
    if len(floor.outer_polygon_m) < 3:
        return HeatmapResult(
            rgba_bytes=bytes(2 * 2 * 4),
            width=2,
            height=2,
            bounds=(0.0, 0.0, 0.0, 0.0),
            resolution_m=resolution_m,
            valid_pixel_count=0,
            above_threshold_count=0,
            coverage_percent=0.0,
        )

    bounds = polygon_bounds(floor.outer_polygon_m)
    width, height = _grid_dimensions(bounds, resolution_m)
    points = filter_samples_for_floor(
        samples,
        floor.id,
        target_ssids=target_ssids,
        bssid_filter=bssid_filter,
        band_filter=band_filter,
    )

    if force_pure_python or not _HAS_NUMPY:
        return _compute_idw_pure_python(
            floor,
            points,
            bounds,
            width,
            height,
            resolution_m,
            power,
            max_radius_m,
            dead_zone_dbm,
        )
    return _compute_idw_numpy(
        floor,
        points,
        bounds,
        width,
        height,
        resolution_m,
        power,
        max_radius_m,
        dead_zone_dbm,
    )


# --- Tab 3 Pre-Survey Analytical Wall-Attenuated Path-Loss Estimator (Section 4.7) ---


def _segments_intersect(
    a1: Point2D,
    a2: Point2D,
    b1: Point2D,
    b2: Point2D,
) -> bool:
    """Return True if closed segment [a1, a2] intersects segment [b1, b2]."""

    def _orient(p: Point2D, q: Point2D, r: Point2D) -> float:
        return (q.x - p.x) * (r.y - p.y) - (q.y - p.y) * (r.x - p.x)

    o1 = _orient(a1, a2, b1)
    o2 = _orient(a1, a2, b2)
    o3 = _orient(b1, b2, a1)
    o4 = _orient(b1, b2, a2)

    if (o1 > 0) != (o2 > 0) and (o3 > 0) != (o4 > 0):
        return True
    eps = 1e-9
    for o_val, seg_a, seg_b, pt in (
        (o1, a1, a2, b1),
        (o2, a1, a2, b2),
        (o3, b1, b2, a1),
        (o4, b1, b2, a2),
    ):
        if abs(o_val) <= eps and (
            min(seg_a.x, seg_b.x) - eps <= pt.x <= max(seg_a.x, seg_b.x) + eps
            and min(seg_a.y, seg_b.y) - eps <= pt.y <= max(seg_a.y, seg_b.y) + eps
        ):
            return True
    return False


def _unique_room_walls(floor: Floor) -> list[tuple[Point2D, Point2D, float]]:
    """Collect deduplicated room wall segments with max attenuation_db per shared wall."""
    wall_map: dict[
        tuple[tuple[float, float], tuple[float, float]],
        tuple[Point2D, Point2D, float],
    ] = {}
    for room in floor.rooms:
        n = len(room.polygon_m)
        if n < 2:
            continue
        for i in range(n):
            p1 = room.polygon_m[i]
            p2 = room.polygon_m[(i + 1) % n]
            k1 = (round(p1.x, 3), round(p1.y, 3))
            k2 = (round(p2.x, 3), round(p2.y, 3))
            key = (k1, k2) if k1 <= k2 else (k2, k1)
            prev = wall_map.get(key)
            if prev is None or room.wall_attenuation_db > prev[2]:
                wall_map[key] = (p1, p2, room.wall_attenuation_db)
    return list(wall_map.values())


def estimate_presurvey_heatmap(
    project: Project,
    floor: Floor,
    *,
    resolution_m: float = 0.4,
    dead_zone_dbm: float = -70.0,
) -> HeatmapResult:
    """Analytical Tab 3 pre-survey coverage estimate using Section 4.7 log-distance + wall loss."""
    if len(floor.outer_polygon_m) < 3:
        return HeatmapResult(
            rgba_bytes=bytes(2 * 2 * 4),
            width=2,
            height=2,
            bounds=(0.0, 0.0, 0.0, 0.0),
            resolution_m=resolution_m,
            valid_pixel_count=0,
            above_threshold_count=0,
            coverage_percent=0.0,
        )

    ap_positions = [
        comp.physical_pos_m
        for comp in project.components
        if comp.component_type == ComponentType.ACCESS_POINT
        and comp.physical_floor_id == floor.id
        and comp.physical_pos_m is not None
    ]
    bounds = polygon_bounds(floor.outer_polygon_m)
    width, height = _grid_dimensions(bounds, resolution_m)

    if not ap_positions:
        return HeatmapResult(
            rgba_bytes=bytes(width * height * 4),
            width=width,
            height=height,
            bounds=bounds,
            resolution_m=resolution_m,
            valid_pixel_count=0,
            above_threshold_count=0,
            coverage_percent=0.0,
        )

    min_x, min_y, max_x, max_y = bounds
    span_x = max_x - min_x
    span_y = max_y - min_y
    walls = _unique_room_walls(floor)

    rssi_grid: list[list[float | None]] = [[None] * width for _ in range(height)]
    alpha_grid: list[list[int]] = [[0] * width for _ in range(height)]
    inside_count = 0

    for row in range(height):
        y_m = max_y - (row + 0.5) * span_y / height
        for col in range(width):
            x_m = min_x + (col + 0.5) * span_x / width
            pt = Point2D(x_m, y_m)
            if not point_in_polygon(pt, floor.outer_polygon_m):
                continue
            inside_count += 1

            best_rssi = -math.inf
            for ap_pos in ap_positions:
                d = max(math.hypot(x_m - ap_pos.x, y_m - ap_pos.y), 0.5)
                wall_loss = 0.0
                for w1, w2, atten_db in walls:
                    if _segments_intersect(ap_pos, pt, w1, w2):
                        wall_loss += atten_db
                rssi = -35.0 - 20.0 * math.log10(d) - wall_loss
                if rssi > best_rssi:
                    best_rssi = rssi

            rssi_grid[row][col] = best_rssi
            alpha_grid[row][col] = int(_BASE_ALPHA)

    rgba_bytes, above_count, coverage = _render_from_rssi_grid(
        rssi_grid, alpha_grid, inside_count, dead_zone_dbm
    )
    return HeatmapResult(
        rgba_bytes=rgba_bytes,
        width=width,
        height=height,
        bounds=bounds,
        resolution_m=resolution_m,
        valid_pixel_count=inside_count,
        above_threshold_count=above_count,
        coverage_percent=coverage,
    )
