"""High-DPI PNG, vector SVG, and multi-page ReportLab PDF exporters for NetViz Studio."""

from __future__ import annotations

import io
import math
from html import escape
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image as RLImage,
)
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from shared.geo_math import (
    calculate_project_cable_bom,
    polygon_bounds,
    true_north_screen_cw_deg,
)
from shared.heatmap import HeatmapResult, compute_idw_heatmap
from shared.models import (
    Building,
    Component,
    ComponentType,
    Floor,
    LinePattern,
    Point2D,
    PortType,
    Project,
)
from shared.validator import validate_project

_BG_WINDOW = (11, 17, 32, 255)
_BG_SURFACE = (15, 23, 42, 255)
_BG_NODE = (30, 41, 59, 175)
_BORDER_RGB = (51, 65, 85, 255)
_TEXT_PRIMARY = (248, 250, 252, 255)
_TEXT_SECONDARY = (203, 213, 225, 255)
_TEXT_MUTED = (148, 163, 184, 255)

_COMPONENT_COLORS_HEX: dict[ComponentType, str] = {
    ComponentType.PASSIVE_SWITCH: "#64748B",
    ComponentType.ACTIVE_SWITCH: "#38BDF8",
    ComponentType.ACCESS_POINT: "#22C55E",
    ComponentType.FIREWALL: "#EF4444",
    ComponentType.ROUTER: "#F59E0B",
    ComponentType.LWL_CONVERTER: "#FACC15",
    ComponentType.POWERLAN_ADAPTER: "#10B981",
    ComponentType.PATCH_PANEL: "#94A3B8",
    ComponentType.DEVICE: "#A855F7",
}

_PORT_COLORS_HEX: dict[PortType, str] = {
    PortType.BASE100: "#60A5FA",
    PortType.BASE1000: "#38BDF8",
    PortType.BASE2500: "#06B6D4",
    PortType.BASE10G: "#06B6D4",
    PortType.LWL_SM: "#FACC15",
    PortType.LWL_MM: "#FB923C",
    PortType.SFP: "#A855F7",
    PortType.SFP_PLUS: "#A855F7",
    PortType.POWERLAN: "#10B981",
    PortType.COAX: "#94A3B8",
    PortType.CUSTOM: "#EC4899",
}


def _hex_to_rgba(hex_color: str, alpha: int = 255) -> tuple[int, int, int, int]:
    clean = hex_color.strip().lstrip("#")
    if len(clean) != 6:
        return (56, 189, 248, alpha)
    return (int(clean[0:2], 16), int(clean[2:4], 16), int(clean[4:6], 16), alpha)


def _load_font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    for candidate in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(candidate, size=max(10, size))
        except OSError:
            continue
    return ImageFont.load_default()


def _node_box_size(comp: Component) -> tuple[float, float]:
    n_ports = len(comp.ports)
    cols = max(4, min(12, n_ports)) if n_ports <= 12 else math.ceil(n_ports / 2)
    width = max(168.0, cols * 24.0 + 24.0)
    height = 76.0 if n_ports <= 12 else 98.0
    return width, height


def _logical_bounds(project: Project) -> tuple[float, float, float, float]:
    if not project.components:
        return (0.0, 0.0, 800.0, 500.0)
    xs: list[float] = []
    ys: list[float] = []
    for comp in project.components:
        w, h = _node_box_size(comp)
        xs.extend([comp.logical_pos.x - 40.0, comp.logical_pos.x + w + 40.0])
        ys.extend([comp.logical_pos.y - 40.0, comp.logical_pos.y + h + 40.0])
    return (min(xs), min(ys), max(xs), max(ys))


def _port_anchor_xy(comp: Component, port_id: str) -> tuple[float, float]:
    w, h = _node_box_size(comp)
    n_ports = len(comp.ports)
    if n_ports == 0:
        return (comp.logical_pos.x + w * 0.5, comp.logical_pos.y + h * 0.5)
    idx = 0
    for i, p in enumerate(comp.ports):
        if p.id == port_id:
            idx = i
            break
    if n_ports <= 12:
        step = w / (n_ports + 1)
        return (comp.logical_pos.x + step * (idx + 1), comp.logical_pos.y + h - 10.0)
    cols = math.ceil(n_ports / 2)
    row = idx // cols
    col = idx % cols
    step = w / (cols + 1)
    y_off = h - (24.0 if row == 0 else 10.0)
    return (comp.logical_pos.x + step * (col + 1), comp.logical_pos.y + y_off)


def _draw_dashed_line(
    draw: ImageDraw.ImageDraw,
    pts: list[tuple[float, float]],
    color: tuple[int, int, int, int],
    width: int,
    pattern: LinePattern,
    scale: float,
) -> None:
    if len(pts) < 2:
        return
    if pattern == LinePattern.SOLID:
        draw.line(pts, fill=color, width=width)
        return
    dash_len = max(4.0, (8.0 if pattern == LinePattern.DASHED else 3.0) * scale)
    gap_len = max(3.0, 5.0 * scale)
    for i in range(len(pts) - 1):
        x1, y1 = pts[i]
        x2, y2 = pts[i + 1]
        seg_len = math.hypot(x2 - x1, y2 - y1)
        if seg_len < 1e-6:
            continue
        ux, uy = (x2 - x1) / seg_len, (y2 - y1) / seg_len
        pos = 0.0
        while pos < seg_len:
            end = min(seg_len, pos + dash_len)
            draw.line(
                [(x1 + ux * pos, y1 + uy * pos), (x1 + ux * end, y1 + uy * end)],
                fill=color,
                width=width,
            )
            pos = end + gap_len


def _render_logical_image(
    project: Project,
    width_px: int,
    height_px: int,
    scale_hint: float = 1.0,
) -> Image.Image:
    base = Image.new("RGBA", (width_px, height_px), _BG_WINDOW)
    overlay = Image.new("RGBA", (width_px, height_px), (0, 0, 0, 0))
    draw = ImageDraw.Draw(base)
    ov_draw = ImageDraw.Draw(overlay)

    font_title = _load_font(int(13 * scale_hint))
    font_sub = _load_font(int(10 * scale_hint))

    min_x, min_y, max_x, max_y = _logical_bounds(project)
    span_x = max(max_x - min_x, 1.0)
    span_y = max(max_y - min_y, 1.0)
    pad = 24.0 * scale_hint
    fit = min((width_px - 2 * pad) / span_x, (height_px - 2 * pad) / span_y)
    off_x = (width_px - span_x * fit) * 0.5 - min_x * fit
    off_y = (height_px - span_y * fit) * 0.5 - min_y * fit

    def to_px(x: float, y: float) -> tuple[float, float]:
        return (x * fit + off_x, y * fit + off_y)

    # Subtle grid
    grid_step = max(24, int(40 * scale_hint))
    for gx in range(0, width_px, grid_step):
        draw.line([(gx, 0), (gx, height_px)], fill=(23, 33, 54, 255), width=1)
    for gy in range(0, height_px, grid_step):
        draw.line([(0, gy), (width_px, gy)], fill=(23, 33, 54, 255), width=1)

    # Cables
    for cable in project.cables:
        src = project.get_component(cable.source_component_id)
        dst = project.get_component(cable.target_component_id)
        if src is None or dst is None:
            continue
        p_start = to_px(*_port_anchor_xy(src, cable.source_port_id))
        p_end = to_px(*_port_anchor_xy(dst, cable.target_port_id))
        pts = [p_start, *(to_px(wp.x, wp.y) for wp in cable.logical_waypoints), p_end]
        c_rgba = _hex_to_rgba(cable.style.color_hex, 235)
        lw = max(1, int(round(cable.style.width_px * scale_hint * 0.8)))
        _draw_dashed_line(draw, pts, c_rgba, lw, cable.style.pattern, scale_hint)

        if cable.label or cable.vlan_id:
            mx = 0.5 * (p_start[0] + p_end[0])
            my = 0.5 * (p_start[1] + p_end[1])
            tag = cable.label or ""
            if cable.vlan_id:
                tag = f"{tag} (V{cable.vlan_id})".strip()
            draw.text((mx + 4, my - 8), tag, fill=_TEXT_SECONDARY, font=font_sub)

    # Translucent component nodes on overlay
    for comp in project.components:
        w, h = _node_box_size(comp)
        x0, y0 = to_px(comp.logical_pos.x, comp.logical_pos.y)
        x1, y1 = to_px(comp.logical_pos.x + w, comp.logical_pos.y + h)
        accent = _hex_to_rgba(_COMPONENT_COLORS_HEX.get(comp.component_type, "#38BDF8"), 255)
        ov_draw.rounded_rectangle(
            [x0, y0, x1, y1],
            radius=max(4, int(6 * scale_hint)),
            fill=_BG_NODE,
            outline=_BORDER_RGB,
            width=max(1, int(1.5 * scale_hint)),
        )
        header_h = max(4.0, 5.0 * fit)
        ov_draw.rectangle([x0 + 1, y0 + 1, x1 - 1, y0 + header_h], fill=accent)

        ov_draw.text(
            (x0 + 8 * scale_hint, y0 + header_h + 4 * scale_hint),
            comp.name,
            fill=_TEXT_PRIMARY,
            font=font_title,
        )
        sub_txt = comp.ip_address or comp.component_type.value
        ov_draw.text(
            (x0 + 8 * scale_hint, y0 + header_h + 20 * scale_hint),
            sub_txt,
            fill=_TEXT_MUTED,
            font=font_sub,
        )

        # Ports
        r_port = max(2.5, 3.5 * scale_hint)
        for port in comp.ports:
            px, py = to_px(*_port_anchor_xy(comp, port.id))
            p_col = _hex_to_rgba(_PORT_COLORS_HEX.get(port.port_type, "#38BDF8"), 255)
            occupied = project.is_port_occupied(comp.id, port.id)
            if occupied:
                ov_draw.ellipse([px - r_port, py - r_port, px + r_port, py + r_port], fill=p_col)
            else:
                ov_draw.ellipse(
                    [px - r_port, py - r_port, px + r_port, py + r_port],
                    fill=_BG_SURFACE,
                    outline=p_col,
                    width=max(1, int(1.5 * scale_hint)),
                )

    return Image.alpha_composite(base, overlay)


def _render_floor_image(
    project: Project,
    building: Building,
    floor: Floor,
    width_px: int,
    height_px: int,
    *,
    dead_zone_dbm: float = -70.0,
    scale_hint: float = 1.0,
) -> tuple[Image.Image, HeatmapResult | None]:
    img = Image.new("RGBA", (width_px, height_px), _BG_WINDOW)
    draw = ImageDraw.Draw(img)
    font_label = _load_font(int(11 * scale_hint))
    font_small = _load_font(int(9 * scale_hint))

    if len(floor.outer_polygon_m) < 3:
        draw.text(
            (20, 20),
            f"{building.name} / {floor.name} (Empty Floor)",
            fill=_TEXT_MUTED,
            font=font_label,
        )
        return img, None

    min_x, min_y, max_x, max_y = polygon_bounds(floor.outer_polygon_m)
    span_x = max(max_x - min_x, 1.0)
    span_y = max(max_y - min_y, 1.0)

    legend_w = int(68 * scale_hint)
    pad = 24.0 * scale_hint
    avail_w = max(40.0, width_px - 2 * pad - legend_w)
    avail_h = max(40.0, height_px - 2 * pad - 24.0 * scale_hint)
    ppm = min(avail_w / span_x, avail_h / span_y)
    ox = pad + (avail_w - span_x * ppm) * 0.5
    oy = pad + 18.0 * scale_hint + (avail_h - span_y * ppm) * 0.5

    def m_to_px(pt: Point2D) -> tuple[float, float]:
        return (ox + (pt.x - min_x) * ppm, oy + (max_y - pt.y) * ppm)

    # Outer floor polygon
    outer_px = [m_to_px(p) for p in floor.outer_polygon_m]
    draw.polygon(outer_px, fill=(20, 30, 48, 255), outline=(148, 163, 184, 255))

    # Rooms
    for room in floor.rooms:
        if len(room.polygon_m) < 3:
            continue
        r_px = [m_to_px(p) for p in room.polygon_m]
        draw.polygon(r_px, fill=_hex_to_rgba(room.fill_color_hex, 200), outline=(71, 85, 105, 255))

    # Clipped IDW heatmap (exact row 0 = max_y pixel-for-pixel alignment)
    all_samples = [s for sess in project.surveys for s in sess.samples if s.floor_id == floor.id]
    heatmap_res: HeatmapResult | None = None
    if all_samples:
        heatmap_res = compute_idw_heatmap(
            floor,
            all_samples,
            target_ssids=project.get_target_ssids() or None,
            resolution_m=0.25,
            dead_zone_dbm=dead_zone_dbm,
        )
        hm_img = Image.frombytes(
            "RGBA",
            (heatmap_res.width, heatmap_res.height),
            heatmap_res.rgba_bytes,
        )
        target_w = max(2, int(round(span_x * ppm)))
        target_h = max(2, int(round(span_y * ppm)))
        hm_resized = hm_img.resize((target_w, target_h), Image.Resampling.BILINEAR)
        img.alpha_composite(hm_resized, dest=(int(round(ox)), int(round(oy))))
        draw = ImageDraw.Draw(img)

    # Re-stroke room walls & labels clearly on top of heatmap
    for room in floor.rooms:
        if len(room.polygon_m) < 3:
            continue
        r_px = [m_to_px(p) for p in room.polygon_m]
        draw.polygon(r_px, outline=(148, 163, 184, 220))
        cx = sum(p[0] for p in r_px) / len(r_px)
        cy = sum(p[1] for p in r_px) / len(r_px)
        draw.text(
            (cx - 24 * scale_hint, cy - 6 * scale_hint),
            room.name,
            fill=_TEXT_PRIMARY,
            font=font_small,
        )

    # Doors, stairs, vertical holes
    for door in floor.doors:
        dx, dy = m_to_px(door.position_m)
        rad = math.radians(door.angle_deg)
        half = 0.5 * door.width_m * ppm
        p1 = (dx - half * math.cos(rad), dy + half * math.sin(rad))
        p2 = (dx + half * math.cos(rad), dy - half * math.sin(rad))
        draw.line([p1, p2], fill=(245, 158, 11, 255), width=max(2, int(3 * scale_hint)))

    for stair in floor.stairs:
        if len(stair.polygon_m) >= 3:
            s_px = [m_to_px(p) for p in stair.polygon_m]
            draw.polygon(s_px, fill=(51, 65, 85, 180), outline=(203, 213, 225, 255))

    for hole in floor.holes:
        hx, hy = m_to_px(hole.position_m)
        hr = max(4.0, hole.radius_m * ppm)
        draw.ellipse([hx - hr, hy - hr, hx + hr, hy + hr], outline=(56, 189, 248, 255), width=2)

    # Physical cables on this floor
    for cable in project.cables:
        src = project.get_component(cable.source_component_id)
        dst = project.get_component(cable.target_component_id)
        if src and dst and src.physical_floor_id == floor.id and dst.physical_floor_id == floor.id:
            if src.physical_pos_m and dst.physical_pos_m:
                pts = [m_to_px(src.physical_pos_m), m_to_px(dst.physical_pos_m)]
                draw.line(
                    pts,
                    fill=_hex_to_rgba(cable.style.color_hex, 230),
                    width=max(1, int(2 * scale_hint)),
                )

    # Placed components
    for comp in project.components:
        if comp.physical_floor_id != floor.id or comp.physical_pos_m is None:
            continue
        cx, cy = m_to_px(comp.physical_pos_m)
        cr = max(4.0, 5.0 * scale_hint)
        col = _hex_to_rgba(_COMPONENT_COLORS_HEX.get(comp.component_type, "#38BDF8"), 255)
        draw.ellipse([cx - cr, cy - cr, cx + cr, cy + cr], fill=col, outline=(255, 255, 255, 255))
        draw.text((cx + cr + 3, cy - cr), comp.name, fill=_TEXT_PRIMARY, font=font_small)

    # Header & coverage badge
    title = f"{building.name} — {floor.name}"
    if heatmap_res is not None:
        title += f"  |  Coverage >= {dead_zone_dbm:.0f} dBm: {heatmap_res.coverage_percent:.1f}%"
    draw.text((pad, 6 * scale_hint), title, fill=_TEXT_PRIMARY, font=font_label)

    # True North arrow
    anchor_rot = building.geo_anchor.rotation_deg if building.geo_anchor else 0.0
    north_cw = math.radians(true_north_screen_cw_deg(floor.editor_view_rotation_deg, anchor_rot))
    nx, ny = width_px - legend_w - 22 * scale_hint, 28 * scale_hint
    nr = 12 * scale_hint
    tip = (nx + nr * math.sin(north_cw), ny - nr * math.cos(north_cw))
    draw.ellipse([nx - nr, ny - nr, nx + nr, ny + nr], outline=_TEXT_MUTED, width=1)
    draw.line([(nx, ny), tip], fill=(239, 68, 68, 255), width=max(2, int(2 * scale_hint)))
    draw.text((nx - 4 * scale_hint, ny + nr + 2), "N", fill=_TEXT_SECONDARY, font=font_small)

    # dBm legend bar
    lx = width_px - legend_w + 10 * scale_hint
    ly_top = pad + 20 * scale_hint
    ly_bot = height_px - pad
    stops = [
        (-30, "#22C55E"),
        (-60, "#84CC16"),
        (-70, "#EAB308"),
        (-80, "#F97316"),
        (-95, "#EF4444"),
    ]
    bar_h = max(20.0, ly_bot - ly_top)
    for i, (dbm, hex_c) in enumerate(stops):
        y0 = ly_top + i * (bar_h / len(stops))
        y1 = ly_top + (i + 1) * (bar_h / len(stops))
        draw.rectangle([lx, y0, lx + 14 * scale_hint, y1], fill=_hex_to_rgba(hex_c, 220))
        draw.text((lx + 18 * scale_hint, y0), f"{dbm}", fill=_TEXT_SECONDARY, font=font_small)

    return img, heatmap_res


def export_floor_png(
    project: Project,
    building_id: str,
    floor_id: str,
    output_path: str | Path,
    *,
    dpi_scale: float = 2.0,
    dead_zone_dbm: float = -70.0,
) -> Path:
    """Export a single floor plan + clipped Wi-Fi heatmap as a high-DPI PNG."""
    building = project.get_building(building_id)
    floor = project.get_floor(building_id, floor_id)
    if building is None or floor is None:
        raise ValueError(f"Building {building_id!r} / floor {floor_id!r} not found")
    dest = Path(output_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    w = int(960 * dpi_scale)
    h = int(640 * dpi_scale)
    img, _ = _render_floor_image(
        project, building, floor, w, h, dead_zone_dbm=dead_zone_dbm, scale_hint=dpi_scale
    )
    img.convert("RGB").save(dest, format="PNG")
    return dest


def export_project_png(
    project: Project,
    output_path: str | Path,
    *,
    dpi_scale: float = 3.0,
    dead_zone_dbm: float = -70.0,
) -> Path:
    """Export a high-DPI PNG composite of logical topology and floor heatmaps."""
    dest = Path(output_path)
    dest.parent.mkdir(parents=True, exist_ok=True)

    scale = max(1.0, float(dpi_scale))
    canvas_w = int(1200 * scale)
    header_h = int(56 * scale)
    logical_h = int(460 * scale)

    floors: list[tuple[Building, Floor]] = [
        (b, f) for b in project.buildings for f in b.floors
    ]
    floor_rows = math.ceil(len(floors) / 2) if floors else 0
    floor_panel_h = int(320 * scale)
    canvas_h = header_h + logical_h + floor_rows * floor_panel_h + int(24 * scale)

    canvas = Image.new("RGBA", (canvas_w, canvas_h), _BG_WINDOW)
    draw = ImageDraw.Draw(canvas)
    font_h1 = _load_font(int(18 * scale))
    font_sub = _load_font(int(11 * scale))

    draw.rectangle([0, 0, canvas_w, header_h], fill=_BG_SURFACE)
    draw.text(
        (int(18 * scale), int(10 * scale)),
        f"NetViz Studio — {project.name}",
        fill=_TEXT_PRIMARY,
        font=font_h1,
    )
    total_samples = sum(len(s.samples) for s in project.surveys)
    summary = (
        f"Components: {len(project.components)}  |  Cables: {len(project.cables)}  |  "
        f"Buildings: {len(project.buildings)}  |  Survey Samples: {total_samples}"
    )
    draw.text((int(18 * scale), int(34 * scale)), summary, fill=_TEXT_SECONDARY, font=font_sub)

    logical_img = _render_logical_image(project, canvas_w, logical_h, scale_hint=scale)
    canvas.alpha_composite(logical_img, dest=(0, header_h))

    panel_w = canvas_w // 2
    for idx, (bldg, floor) in enumerate(floors):
        row = idx // 2
        col = idx % 2
        f_img, _ = _render_floor_image(
            project,
            bldg,
            floor,
            panel_w,
            floor_panel_h,
            dead_zone_dbm=dead_zone_dbm,
            scale_hint=scale * 0.85,
        )
        dest_xy = (col * panel_w, header_h + logical_h + row * floor_panel_h)
        canvas.alpha_composite(f_img, dest=dest_xy)

    canvas.convert("RGB").save(dest, format="PNG")
    return dest


def export_logical_svg(project: Project, output_path: str | Path) -> Path:
    """Export the logical network diagram alone as crisp vector SVG."""
    dest = Path(output_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    svg = _build_svg_document(project, include_floors=False)
    dest.write_text(svg, encoding="utf-8")
    return dest


def export_project_svg(project: Project, output_path: str | Path) -> Path:
    """Export crisp vector SVG containing logical topology and floor plan diagrams."""
    dest = Path(output_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    svg = _build_svg_document(project, include_floors=True)
    dest.write_text(svg, encoding="utf-8")
    return dest


def _build_svg_document(project: Project, *, include_floors: bool) -> str:
    width = 1200.0
    logical_h = 560.0
    floors = [(b, f) for b in project.buildings for f in b.floors] if include_floors else []
    floor_rows = math.ceil(len(floors) / 2)
    floor_h = 340.0
    total_h = 64.0 + logical_h + floor_rows * floor_h

    lines: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" '
            f'height="{total_h:.0f}" viewBox="0 0 {width:.0f} {total_h:.0f}">'
        ),
        f'  <rect width="{width:.0f}" height="{total_h:.0f}" fill="#0B1120"/>',
        f'  <rect x="0" y="0" width="{width:.0f}" height="56" fill="#0F172A"/>',
        (
            '  <text x="20" y="34" fill="#F8FAFC" font-family="Segoe UI, sans-serif" '
            f'font-size="18" font-weight="600">{escape(project.name)}</text>'
        ),
    ]

    min_x, min_y, max_x, max_y = _logical_bounds(project)
    span_x = max(max_x - min_x, 1.0)
    span_y = max(max_y - min_y, 1.0)
    fit = min((width - 60.0) / span_x, (logical_h - 40.0) / span_y)
    off_x = (width - span_x * fit) * 0.5 - min_x * fit
    off_y = 64.0 + (logical_h - span_y * fit) * 0.5 - min_y * fit

    def lpt(x: float, y: float) -> tuple[float, float]:
        return (x * fit + off_x, y * fit + off_y)

    # Logical cables
    for cable in project.cables:
        src = project.get_component(cable.source_component_id)
        dst = project.get_component(cable.target_component_id)
        if src is None or dst is None:
            continue
        pts = [
            lpt(*_port_anchor_xy(src, cable.source_port_id)),
            *(lpt(wp.x, wp.y) for wp in cable.logical_waypoints),
            lpt(*_port_anchor_xy(dst, cable.target_port_id)),
        ]
        pts_str = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        dash = ""
        if cable.style.pattern == LinePattern.DASHED:
            dash = ' stroke-dasharray="8 5"'
        elif cable.style.pattern == LinePattern.DOTTED:
            dash = ' stroke-dasharray="2 4"'
        elif cable.style.pattern == LinePattern.DASH_DOT:
            dash = ' stroke-dasharray="8 4 2 4"'
        lines.append(
            f'  <polyline points="{pts_str}" fill="none" stroke="{escape(cable.style.color_hex)}" '
            f'stroke-width="{cable.style.width_px:.1f}"{dash}/>'
        )

    # Logical nodes
    for comp in project.components:
        w, h = _node_box_size(comp)
        x0, y0 = lpt(comp.logical_pos.x, comp.logical_pos.y)
        x1, y1 = lpt(comp.logical_pos.x + w, comp.logical_pos.y + h)
        bw, bh = x1 - x0, y1 - y0
        accent = _COMPONENT_COLORS_HEX.get(comp.component_type, "#38BDF8")
        lines.append(
            f'  <rect x="{x0:.1f}" y="{y0:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="6" '
            f'fill="#1E293B" fill-opacity="0.65" stroke="#334155" stroke-width="1.5"/>'
        )
        lines.append(
            f'  <rect x="{x0:.1f}" y="{y0:.1f}" width="{bw:.1f}" '
            f'height="5" rx="2" fill="{accent}"/>'
        )
        lines.append(
            f'  <text x="{x0 + 8:.1f}" y="{y0 + 21:.1f}" fill="#F8FAFC" '
            'font-family="Segoe UI, sans-serif" font-size="12" '
            f'font-weight="600">{escape(comp.name)}</text>'
        )
        for port in comp.ports:
            px, py = lpt(*_port_anchor_xy(comp, port.id))
            pcol = _PORT_COLORS_HEX.get(port.port_type, "#38BDF8")
            fill = pcol if project.is_port_occupied(comp.id, port.id) else "#0F172A"
            lines.append(
                f'  <circle cx="{px:.1f}" cy="{py:.1f}" r="3.5" fill="{fill}" '
                f'stroke="{pcol}" stroke-width="1.5"/>'
            )

    # Floor plans
    panel_w = width / 2.0
    for idx, (bldg, floor) in enumerate(floors):
        row = idx // 2
        col = idx % 2
        px0 = col * panel_w
        py0 = 64.0 + logical_h + row * floor_h
        lines.append(
            f'  <text x="{px0 + 18:.1f}" y="{py0 + 22:.1f}" fill="#CBD5E1" '
            f'font-family="Segoe UI, sans-serif" font-size="13" font-weight="600">'
            f'{escape(bldg.name)} — {escape(floor.name)}</text>'
        )
        if len(floor.outer_polygon_m) < 3:
            continue
        f_min_x, f_min_y, f_max_x, f_max_y = polygon_bounds(floor.outer_polygon_m)
        f_span_x = max(f_max_x - f_min_x, 1.0)
        f_span_y = max(f_max_y - f_min_y, 1.0)
        ppm = min((panel_w - 48.0) / f_span_x, (floor_h - 56.0) / f_span_y)
        fox = px0 + (panel_w - f_span_x * ppm) * 0.5
        foy = py0 + 36.0 + (floor_h - 56.0 - f_span_y * ppm) * 0.5

        def fpt(
            pt: Point2D,
            _fox: float = fox,
            _foy: float = foy,
            _fx: float = f_min_x,
            _fy: float = f_max_y,
            _ppm: float = ppm,
        ) -> str:
            return f"{_fox + (pt.x - _fx) * _ppm:.1f},{_foy + (_fy - pt.y) * _ppm:.1f}"

        outer_pts = " ".join(fpt(p) for p in floor.outer_polygon_m)
        lines.append(
            f'  <polygon points="{outer_pts}" fill="#141E30" stroke="#94A3B8" stroke-width="1.5"/>'
        )
        for room in floor.rooms:
            if len(room.polygon_m) < 3:
                continue
            r_pts = " ".join(fpt(p) for p in room.polygon_m)
            lines.append(
                f'  <polygon points="{r_pts}" fill="{escape(room.fill_color_hex)}" '
                f'fill-opacity="0.75" stroke="#475569" stroke-width="1"/>'
            )
        for comp in project.components:
            if comp.physical_floor_id == floor.id and comp.physical_pos_m is not None:
                cx = fox + (comp.physical_pos_m.x - f_min_x) * ppm
                cy = foy + (f_max_y - comp.physical_pos_m.y) * ppm
                ccol = _COMPONENT_COLORS_HEX.get(comp.component_type, "#38BDF8")
                lines.append(
                    f'  <circle cx="{cx:.1f}" cy="{cy:.1f}" r="4.5" '
                    f'fill="{ccol}" stroke="#FFFFFF" stroke-width="1"/>'
                )

    lines.append("</svg>")
    return "\n".join(lines)


def _rl_table(rows: list[list[str]], col_widths: list[float]) -> Table:
    table = Table(rows, colWidths=col_widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F172A")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#F8FAFC")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def export_project_pdf(
    project: Project,
    output_path: str | Path,
    *,
    dead_zone_dbm: float = -70.0,
) -> Path:
    """Export a multi-page ReportLab PDF covering all 5 Section 4.8C report sections."""
    dest = Path(output_path)
    dest.parent.mkdir(parents=True, exist_ok=True)

    pagesize = landscape(A4)
    doc = SimpleDocTemplate(
        str(dest),
        pagesize=pagesize,
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title=f"NetViz Studio Report — {project.name}",
    )

    styles = getSampleStyleSheet()
    h1 = ParagraphStyle(
        "NetVizH1",
        parent=styles["Heading1"],
        fontSize=16,
        textColor=colors.HexColor("#0F172A"),
        spaceAfter=6,
    )
    h2 = ParagraphStyle(
        "NetVizH2",
        parent=styles["Heading2"],
        fontSize=12.5,
        textColor=colors.HexColor("#0284C7"),
        spaceBefore=8,
        spaceAfter=4,
    )
    body = ParagraphStyle("NetVizBody", parent=styles["Normal"], fontSize=9.5, spaceAfter=4)

    story: list[object] = []
    bom = calculate_project_cable_bom(project)
    issues = validate_project(project)
    total_samples = sum(len(s.samples) for s in project.surveys)
    all_samples = [smp for sess in project.surveys for smp in sess.samples]

    # --- 1. Project Overview & KPI Summary ---
    story.append(Paragraph(f"NetViz Studio Engineering Report — {escape(project.name)}", h1))
    ssids_text = escape(", ".join(project.get_target_ssids()) or "None")
    story.append(
        Paragraph(
            f"Schema Version: {escape(project.schema_version)} &nbsp;|&nbsp; "
            f"Target SSIDs: {ssids_text} &nbsp;|&nbsp; "
            f"Dead-Zone Threshold: {dead_zone_dbm:.0f} dBm",
            body,
        )
    )
    story.append(Paragraph("1. Project Overview & KPI Summary", h2))
    total_floors = sum(len(b.floors) for b in project.buildings)
    kpi_rows = [
        ["Metric", "Value", "Metric", "Value"],
        [
            "Components",
            str(len(project.components)),
            "Logical / Physical Cables",
            str(len(project.cables)),
        ],
        [
            "Cable Bundles",
            str(len(project.bundles)),
            "Buildings / Floors",
            f"{len(project.buildings)} / {total_floors}",
        ],
        ["Survey Sessions", str(len(project.surveys)), "Survey Samples", str(total_samples)],
        ["Estimated Total Cable", f"{bom.total_m:.1f} m", "Validation Findings", str(len(issues))],
    ]
    story.append(_rl_table(kpi_rows, [55 * mm, 45 * mm, 60 * mm, 45 * mm]))
    story.append(Spacer(1, 6 * mm))

    # --- 2. Full Port-to-Port Patch Schedule ---
    story.append(Paragraph("2. Port-to-Port Patch Schedule", h2))
    patch_rows: list[list[str]] = [
        ["Cable Label", "Source", "Src Port", "Target", "Dst Port", "Port Type", "VLAN"]
    ]
    for cable in project.cables:
        src = project.get_component(cable.source_component_id)
        dst = project.get_component(cable.target_component_id)
        sp = project.get_port(cable.source_component_id, cable.source_port_id)
        dp = project.get_port(cable.target_component_id, cable.target_port_id)
        patch_rows.append(
            [
                cable.label or cable.id[:8],
                src.name if src else cable.source_component_id[:8],
                sp.label if sp else cable.source_port_id[:8],
                dst.name if dst else cable.target_component_id[:8],
                dp.label if dp else cable.target_port_id[:8],
                sp.port_type.value if sp else "-",
                cable.vlan_id or "-",
            ]
        )
    if len(patch_rows) == 1:
        patch_rows.append(["(No cables)", "-", "-", "-", "-", "-", "-"])
    story.append(
        _rl_table(
            patch_rows,
            [42 * mm, 38 * mm, 26 * mm, 38 * mm, 26 * mm, 32 * mm, 20 * mm],
        )
    )

    # --- 3. Physical Cable Length & BOM Schedule ---
    story.append(PageBreak())
    story.append(Paragraph("3. Physical Cable Length & BOM Schedule", h2))
    slack_col = f"Total w/ {int(bom.slack_factor * 100)}% Slack (m)"
    summary_rows: list[list[str]] = [
        ["Port Type", "Runs", "Horizontal (m)", "Vertical Riser (m)", slack_col]
    ]
    for grp in bom.by_port_type:
        summary_rows.append(
            [
                grp.port_type.value,
                str(grp.run_count),
                f"{grp.horizontal_m:.2f}",
                f"{grp.vertical_riser_m:.2f}",
                f"{grp.total_m:.2f}",
            ]
        )
    summary_rows.append(
        ["ALL TYPES", str(len(bom.runs)), "-", "-", f"{bom.total_m:.2f}"]
    )
    story.append(_rl_table(summary_rows, [45 * mm, 25 * mm, 45 * mm, 45 * mm, 55 * mm]))
    story.append(Spacer(1, 5 * mm))

    run_rows: list[list[str]] = [
        ["Cable Run", "Media", "Horizontal (m)", "Riser (m)", "Total w/ Slack (m)", "Routing"]
    ]
    for run in bom.runs:
        run_rows.append(
            [
                run.label,
                run.port_type.value,
                f"{run.horizontal_m:.2f}",
                f"{run.vertical_riser_m:.2f}",
                f"{run.total_with_slack_m:.2f}",
                "Inter-Floor Riser" if run.is_inter_floor else "Same Floor",
            ]
        )
    if len(run_rows) == 1:
        run_rows.append(["(No placed cable runs)", "-", "-", "-", "-", "-"])
    story.append(_rl_table(run_rows, [55 * mm, 30 * mm, 35 * mm, 30 * mm, 40 * mm, 40 * mm]))

    # --- 4. Logical Network Diagram ---
    story.append(PageBreak())
    story.append(Paragraph("4. Logical Network Diagram", h2))
    logical_img = _render_logical_image(project, 1800, 960, scale_hint=1.5)
    logical_buf = io.BytesIO()
    logical_img.convert("RGB").save(logical_buf, format="PNG")
    logical_buf.seek(0)
    story.append(RLImage(logical_buf, width=255 * mm, height=136 * mm))

    # --- 5. Per-Building / Per-Floor Plans & Clipped Wi-Fi Survey Heatmaps ---
    for bldg in project.buildings:
        for floor in bldg.floors:
            story.append(PageBreak())
            story.append(
                Paragraph(
                    f"5. Floor Plan & Wi-Fi Heatmap — {escape(bldg.name)} / {escape(floor.name)}",
                    h2,
                )
            )
            f_img, hm = _render_floor_image(
                project,
                bldg,
                floor,
                1800,
                1000,
                dead_zone_dbm=dead_zone_dbm,
                scale_hint=1.5,
            )
            if hm is not None:
                floor_samples = sum(1 for s in all_samples if s.floor_id == floor.id)
                story.append(
                    Paragraph(
                        f"Samples on floor: {floor_samples} &nbsp;|&nbsp; "
                        f"Coverage &ge; {dead_zone_dbm:.0f} dBm: {hm.coverage_percent:.1f}%",
                        body,
                    )
                )
            else:
                story.append(
                    Paragraph("No Wi-Fi survey samples recorded for this floor.", body)
                )
            f_buf = io.BytesIO()
            f_img.convert("RGB").save(f_buf, format="PNG")
            f_buf.seek(0)
            story.append(RLImage(f_buf, width=255 * mm, height=142 * mm))

    doc.build(story)
    return dest
