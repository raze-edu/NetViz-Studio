"""Phase 8 tests: high-DPI PNG, vector SVG, and multi-page ReportLab PDF exporters."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image

from desktop.exporters import (
    export_floor_png,
    export_logical_svg,
    export_project_pdf,
    export_project_png,
    export_project_svg,
)
from shared.sample_project import build_sample_campus_project


def test_export_project_png_and_floor_png(tmp_path: Path) -> None:
    project = build_sample_campus_project()

    png_path = tmp_path / "campus_overview.png"
    out = export_project_png(project, png_path, dpi_scale=2.0)
    assert out == png_path
    assert png_path.is_file()
    raw = png_path.read_bytes()
    assert raw.startswith(b"\x89PNG\r\n\x1a\n")
    with Image.open(png_path) as img:
        assert img.width == 2400
        assert img.height > 1000

    bldg = project.buildings[0]
    floor = bldg.floors[0]
    floor_png = tmp_path / "floor0.png"
    export_floor_png(project, bldg.id, floor.id, floor_png, dpi_scale=1.5)
    assert floor_png.is_file()
    with Image.open(floor_png) as f_img:
        assert f_img.width == 1440
        assert f_img.height == 960


def test_export_project_svg_and_logical_svg(tmp_path: Path) -> None:
    project = build_sample_campus_project()

    svg_path = tmp_path / "campus.svg"
    export_project_svg(project, svg_path)
    assert svg_path.is_file()
    svg_text = svg_path.read_text(encoding="utf-8")
    root = ET.fromstring(svg_text)
    assert root.tag.endswith("svg")
    assert project.name in svg_text
    assert "Building A" in svg_text

    logical_svg = tmp_path / "logical.svg"
    export_logical_svg(project, logical_svg)
    assert logical_svg.is_file()
    l_root = ET.fromstring(logical_svg.read_text(encoding="utf-8"))
    assert l_root.tag.endswith("svg")


def test_export_project_pdf_multipage_report(tmp_path: Path) -> None:
    project = build_sample_campus_project()

    pdf_path = tmp_path / "campus_report.pdf"
    out = export_project_pdf(project, pdf_path, dead_zone_dbm=-70.0)
    assert out == pdf_path
    assert pdf_path.is_file()
    data = pdf_path.read_bytes()
    assert data.startswith(b"%PDF-")
    assert len(data) > 25_000
    # Verify multiple PDF page objects exist (overview+patch, BOM, logical diagram, 3 floors)
    assert data.count(b"/Type /Page") >= 5
