"""Phase 1 tests: pure-stdlib shared models, presets, bundle I/O, and sample project."""

from __future__ import annotations

import ast
import json
import zipfile
from pathlib import Path

import pytest

from shared.bundle_io import (
    compute_sha256_bytes,
    compute_sha256_file,
    export_survey_session,
    export_templates_json,
    import_survey_session,
    import_templates_json,
    load_netviz,
    read_tile_from_sqlite,
    save_netviz,
    write_tile_to_sqlite,
)
from shared.models import (
    SCHEMA_VERSION,
    Building,
    Cable,
    Component,
    ComponentTemplate,
    ComponentType,
    Floor,
    GeoAnchor,
    Point2D,
    Port,
    PortType,
    PositionSource,
    Project,
)
from shared.presets import (
    BUILTIN_TEMPLATES,
    get_builtin_template,
    instantiate_from_template,
    template_from_component,
)
from shared.sample_project import build_sample_campus_project

FORBIDDEN_SHARED_MODULES = {"pydantic", "PySide6", "shapely", "kivy"}


def test_shared_has_zero_forbidden_imports() -> None:
    shared_dir = Path(__file__).resolve().parent.parent / "shared"
    py_files = sorted(shared_dir.glob("*.py"))
    assert py_files, "Expected Python modules inside shared/"

    for py_file in py_files:
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    assert top not in FORBIDDEN_SHARED_MODULES, (
                        f"Forbidden import {alias.name!r} in {py_file.name}"
                    )
            elif isinstance(node, ast.ImportFrom) and node.module:
                top = node.module.split(".")[0]
                assert top not in FORBIDDEN_SHARED_MODULES, (
                    f"Forbidden import from {node.module!r} in {py_file.name}"
                )


def test_schema_v21_json_roundtrip_and_wire_format() -> None:
    project = build_sample_campus_project()
    raw = project.to_dict()

    assert raw["schema_version"] == SCHEMA_VERSION == "2.1.0"
    assert isinstance(raw["buildings"][0]["footprint_latlon"][0], list)
    assert len(raw["buildings"][0]["footprint_latlon"][0]) == 2
    assert set(raw["components"][0]["logical_pos"].keys()) == {"x", "y"}

    restored = Project.from_json(project.to_json())
    assert restored.to_dict() == raw


def test_legacy_v20_backward_compatibility_and_unknown_keys() -> None:
    legacy_payload = {
        "schema_version": "2.0.0",
        "id": "proj-v20",
        "name": "Legacy Site",
        "future_project_flag": {"nested": True},
        "components": [
            {
                "id": "ap-1",
                "component_type": "access_point",
                "name": "AP-Legacy",
                "ssids": ["OfficeNet"],
                "mac_address": "AA:BB:CC:DD:EE:FF",
                "ports": [
                    {
                        "id": "p-1",
                        "label": "eth0",
                        "port_type": "Base1000",
                        "future_port_meta": 42,
                    }
                ],
                "logical_pos": {"x": 10, "y": 20},
                "unknown_comp_field": "ignored",
            }
        ],
        "cables": [
            {
                "id": "cab-1",
                "source_component_id": "ap-1",
                "source_port_id": "p-1",
                "target_component_id": "ap-1",
                "target_port_id": "p-1",
                "future_cable_prop": "ok",
            }
        ],
        "buildings": [
            {
                "id": "b-1",
                "name": "Main",
                "geo_anchor": {"origin_lat": 52.5, "origin_lon": 13.4},
                "footprint_latlon": [[52.5, 13.4], [52.5, 13.401], [52.501, 13.4]],
                "floors": [
                    {
                        "id": "f-1",
                        "name": "GF",
                        "outer_polygon_m": [{"x": 0, "y": 0}, {"x": 10, "y": 0}, {"x": 0, "y": 10}],
                        "rooms": [
                            {
                                "id": "r-1",
                                "name": "Hall",
                                "polygon_m": [
                                    {"x": 0, "y": 0},
                                    {"x": 5, "y": 0},
                                    {"x": 0, "y": 5},
                                ],
                            }
                        ],
                        "doors": [{"id": "d-1", "position_m": {"x": 2, "y": 0}}],
                        "future_floor_key": 123,
                    }
                ],
            }
        ],
        "surveys": [
            {
                "id": "s-1",
                "name": "Walk 1",
                "created_utc": "2025-01-01T00:00:00Z",
                "samples": [
                    {
                        "id": "smp-1",
                        "timestamp_utc": "2025-01-01T00:01:00Z",
                        "building_id": "b-1",
                        "floor_id": "f-1",
                        "pos_m": {"x": 1.0, "y": 2.0},
                        "source": "gps",
                        "readings": [
                            {
                                "ssid": "OfficeNet",
                                "bssid": "aa:bb:cc:dd:ee:ff",
                                "rssi_dbm": -58,
                                "frequency_mhz": 5180,
                                "unknown_reading_tag": "x",
                            }
                        ],
                    }
                ],
            }
        ],
    }

    proj = Project.from_dict(legacy_payload)
    assert proj.schema_version == "2.0.0"
    assert proj.name == "Legacy Site"
    assert proj.custom_templates == []
    assert proj.map_preferences.preferred_provider == "google"
    assert proj.map_preferences.offline_cache_zooms == [16, 17, 18, 19]
    assert proj.buildings[0].floor_height_m == 3.2
    assert proj.buildings[0].geo_anchor is not None
    assert proj.buildings[0].geo_anchor.rotation_deg == 0.0
    assert proj.buildings[0].floors[0].editor_view_rotation_deg == 0.0
    assert proj.buildings[0].floors[0].blueprint is None
    assert proj.buildings[0].floors[0].rooms[0].wall_attenuation_db == 6.0
    assert proj.buildings[0].floors[0].doors[0].swing_flip is False
    assert proj.buildings[0].floors[0].doors[0].hinge_flip is False
    assert proj.cables[0].vlan_id == ""
    assert proj.cables[0].logical_bundle_id is None
    assert proj.surveys[0].samples[0].calibration_offset_m is None


def test_project_helper_methods() -> None:
    p_sw = Port(id="p-sw", label="G1", port_type=PortType.BASE1000, poe_capable=True)
    p_sw_free = Port(id="p-sw-free", label="G2", port_type=PortType.BASE1000)
    p_ap = Port(id="p-ap", label="eth0", port_type=PortType.BASE1000, poe_capable=True)

    sw = Component(
        id="c-sw",
        component_type=ComponentType.ACTIVE_SWITCH,
        name="ASW-01",
        mac_address="00:11:22:33:44:55",
        ports=[p_sw, p_sw_free],
    )
    ap = Component(
        id="c-ap",
        component_type=ComponentType.ACCESS_POINT,
        name="AP-01",
        mac_address="AA-BB-CC-DD-EE-FF",
        ssids=["Corp-WiFi", " Guest-WiFi ", ""],
        ports=[p_ap],
    )
    ap2 = Component(
        id="c-ap2",
        component_type=ComponentType.ACCESS_POINT,
        name="AP-02",
        mac_address="1122.3344.5566",
        ssids=["Corp-WiFi", "IoT"],
    )
    cable = Cable(
        id="cab-1",
        source_component_id="c-sw",
        source_port_id="p-sw",
        target_component_id="c-ap",
        target_port_id="p-ap",
    )
    floor0 = Floor(
        id="f-0",
        name="GF",
        outer_polygon_m=[Point2D(0, 0), Point2D(5, 0), Point2D(0, 5)],
    )
    bldg = Building(id="b-0", name="HQ", geo_anchor=GeoAnchor(52.52, 13.405), floors=[floor0])

    proj = Project(components=[sw, ap, ap2], cables=[cable], buildings=[bldg])

    assert proj.get_component("c-sw") is sw
    assert proj.get_component("missing") is None
    assert proj.get_port("c-sw", "p-sw") is p_sw
    assert proj.get_port("c-sw", "missing") is None
    assert proj.get_port("missing", "p-sw") is None

    assert proj.get_target_ssids() == ["Corp-WiFi", "Guest-WiFi", "IoT"]

    assert proj.is_port_occupied("c-sw", "p-sw") is True
    assert proj.is_port_occupied("c-ap", "p-ap") is True
    assert proj.is_port_occupied("c-sw", "p-sw", exclude_cable_id="cab-1") is False
    assert proj.is_port_occupied("c-sw", "p-sw-free") is False

    conn_from_sw = proj.get_port_connection("c-sw", "p-sw")
    assert conn_from_sw == (cable, ap, p_ap)
    conn_from_ap = proj.get_port_connection("c-ap", "p-ap")
    assert conn_from_ap == (cable, sw, p_sw)
    assert proj.get_port_connection("c-sw", "p-sw-free") is None

    assert proj.get_building("b-0") is bldg
    assert proj.get_building("missing") is None
    assert proj.get_floor("b-0", "f-0") is floor0
    assert proj.get_floor("b-0", "missing") is None
    assert proj.get_floor("missing", "f-0") is None

    assert proj.find_component_by_mac("aa:bb:cc:dd:ee:ff") is ap
    assert proj.find_component_by_mac("AABB.CCDD.EEFF") is ap
    assert proj.find_component_by_mac("11:22:33:44:55:66") is ap2
    assert proj.find_component_by_mac("00-11-22-33-44-55") is sw
    assert proj.find_component_by_mac("") is None
    assert proj.find_component_by_mac("ff:ff:ff:ff:ff:ff") is None


def test_builtin_presets_and_template_helpers() -> None:
    assert len(BUILTIN_TEMPLATES) == 9
    expected_types = set(ComponentType)
    assert {tpl.component_type for tpl in BUILTIN_TEMPLATES} == expected_types

    asw_tpl = get_builtin_template(ComponentType.ACTIVE_SWITCH)
    assert asw_tpl.default_name_prefix == "ASW"
    assert len(asw_tpl.port_specs) == 26
    poe_base1000 = [
        p for p in asw_tpl.port_specs if p.port_type == PortType.BASE1000 and p.poe_capable
    ]
    assert len(poe_base1000) == 24
    assert sum(1 for p in asw_tpl.port_specs if p.port_type == PortType.SFP_PLUS) == 2

    ap_tpl = get_builtin_template(ComponentType.ACCESS_POINT)
    assert ap_tpl.default_frequencies_ghz == [2.4, 5.0]
    assert ap_tpl.default_ssids == ["Corp-WiFi"]

    c1 = instantiate_from_template(asw_tpl, existing_names=["asw-01"], pos=Point2D(100.0, 200.0))
    c2 = instantiate_from_template(asw_tpl, existing_names=["asw-01", c1.name])
    assert c1.name == "ASW-02"
    assert c2.name == "ASW-03"
    assert c1.id != c2.id
    assert c1.template_id == asw_tpl.id
    assert {p.id for p in c1.ports}.isdisjoint({p.id for p in c2.ports})
    assert c1.logical_pos == Point2D(100.0, 200.0)

    c1.custom_attributes["rack"] = "A1"
    extracted = template_from_component(
        c1, template_name="Rack Core Switch", strip_instance_ids=True
    )
    assert isinstance(extracted, ComponentTemplate)
    assert extracted.id not in {c1.id, asw_tpl.id}
    assert extracted.is_builtin is False
    assert extracted.default_name_prefix == "ASW"
    assert extracted.default_custom_attributes == {"rack": "A1"}
    assert len(extracted.port_specs) == len(c1.ports)


def test_netviz_zip_roundtrip_tiles_blueprints_and_json_io(tmp_path: Path) -> None:
    project = build_sample_campus_project()
    floor0_id = project.buildings[0].floors[0].id
    bp_path = f"blueprints/{floor0_id}.png"
    bp_bytes = b"\x89PNG\r\n\x1a\nFAKE_BLUEPRINT_DATA"

    tiles_db = tmp_path / "source_tiles.sqlite"
    tile_bytes = b"\x89PNG\r\n\x1a\nFAKE_TILE_256"
    write_tile_to_sqlite(
        tiles_db,
        "osm",
        18,
        140840,
        85923,
        tile_bytes,
        mime="image/png",
        fetched_at=1700000000,
    )

    loaded_tile = read_tile_from_sqlite(tiles_db, "osm", 18, 140840, 85923)
    assert loaded_tile == (tile_bytes, "image/png")
    assert read_tile_from_sqlite(tiles_db, "osm", 18, 1, 1) is None

    bundle_path = tmp_path / "campus.netviz"
    save_netviz(project, bundle_path, tiles_db_path=tiles_db, blueprints={bp_path: bp_bytes})

    assert compute_sha256_file(bundle_path) == compute_sha256_bytes(bundle_path.read_bytes())

    with zipfile.ZipFile(bundle_path, "r") as zf:
        names = set(zf.namelist())
        assert "project.json" in names
        assert "tiles.sqlite" in names
        assert bp_path in names

    extracted_tiles = tmp_path / "extracted_tiles.sqlite"
    loaded_bundle = load_netviz(bundle_path, extract_tiles_to=extracted_tiles)
    assert loaded_bundle.project.to_dict() == project.to_dict()
    assert loaded_bundle.blueprints == {bp_path: bp_bytes}
    assert loaded_bundle.tiles_db_path == extracted_tiles
    assert read_tile_from_sqlite(extracted_tiles, "osm", 18, 140840, 85923) == (
        tile_bytes,
        "image/png",
    )

    with pytest.raises(ValueError):
        save_netviz(project, tmp_path / "bad.netviz", blueprints={"../escape.png": b"123"})

    survey_path = tmp_path / "walk.netviz-survey.json"
    session = project.surveys[0]
    export_survey_session(session, survey_path)
    assert import_survey_session(survey_path).to_dict() == session.to_dict()

    templates_path = tmp_path / "custom.netviz-templates.json"
    export_templates_json(project.custom_templates, templates_path)
    imported_tpls = import_templates_json(templates_path)
    assert [t.to_dict() for t in imported_tpls] == [t.to_dict() for t in project.custom_templates]

    raw_list_path = tmp_path / "list_only.netviz-templates.json"
    raw_list_path.write_text(
        json.dumps([t.to_dict() for t in project.custom_templates]),
        encoding="utf-8",
    )
    assert len(import_templates_json(raw_list_path)) == len(project.custom_templates)


def test_sample_campus_project_integrity() -> None:
    project = build_sample_campus_project()

    assert len(project.buildings) == 2
    bldg_a, bldg_b = project.buildings
    assert bldg_a.geo_anchor is not None
    assert bldg_a.geo_anchor.origin_lat == pytest.approx(52.520000)
    assert bldg_a.geo_anchor.origin_lon == pytest.approx(13.405000)
    assert bldg_a.geo_anchor.rotation_deg == pytest.approx(22.5)
    assert len(bldg_a.floors) == 2
    assert len(bldg_a.floors[0].outer_polygon_m) == 6  # L-shape
    assert len(bldg_a.floors[1].outer_polygon_m) == 4  # Rectangular
    assert len(bldg_a.floors[0].rooms) >= 4
    assert len(bldg_a.floors[0].doors) >= 3
    assert len(bldg_a.floors[0].stairs) >= 1
    assert bldg_a.floors[0].holes[0].riser_group_id == bldg_a.floors[1].holes[0].riser_group_id

    assert bldg_b.geo_anchor is not None
    assert len(bldg_b.floors) == 1

    # All 9 ComponentTypes represented + 1 custom template
    assert {c.component_type for c in project.components} == set(ComponentType)
    assert len(project.custom_templates) >= 1

    # All components physically placed and valid references
    for comp in project.components:
        assert comp.physical_building_id is not None
        assert comp.physical_floor_id is not None
        assert comp.physical_pos_m is not None
        assert project.get_floor(comp.physical_building_id, comp.physical_floor_id) is not None

    # Cables reference valid ports & include VLANs, bundle, and inter-floor riser segments
    assert any(c.vlan_id for c in project.cables)
    assert len(project.bundles) >= 1
    assert all(
        any(c.id == cid for c in project.cables)
        for b in project.bundles
        for cid in b.member_cable_ids
    )
    riser_cables = [c for c in project.cables if len(c.physical_segments) == 2]
    assert len(riser_cables) >= 1
    assert riser_cables[0].physical_segments[0].end_hole_id == bldg_a.floors[0].holes[0].id
    assert riser_cables[0].physical_segments[1].start_hole_id == bldg_a.floors[1].holes[0].id

    # 25-sample multi-SSID Wi-Fi survey
    assert len(project.surveys) == 1
    assert len(project.surveys[0].samples) == 25
    assert project.get_target_ssids() == ["Corp-WiFi", "Guest-WiFi"]
    assert all(len(s.readings) >= 2 for s in project.surveys[0].samples)
    assert any(s.source == PositionSource.GPS_CALIBRATED for s in project.surveys[0].samples)
