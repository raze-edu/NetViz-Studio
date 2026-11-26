"""Phase 2 tests: 9-rule live network and floor-plan validator."""

from __future__ import annotations

from shared.models import (
    Building,
    Cable,
    Component,
    ComponentType,
    Door,
    Floor,
    GeoAnchor,
    Point2D,
    Port,
    PortType,
    Project,
    VerticalHole,
)
from shared.sample_project import build_sample_campus_project
from shared.validator import validate_project


def test_sample_campus_project_has_zero_validation_issues() -> None:
    project = build_sample_campus_project()
    issues = validate_project(project)
    assert issues == []


def test_all_nine_validation_rules() -> None:
    b_anchored = Building(
        id="b1",
        name="Main",
        geo_anchor=GeoAnchor(52.52, 13.405),
        floors=[
            Floor(
                id="f0",
                name="GF",
                level_index=0,
                outer_polygon_m=[Point2D(0, 0), Point2D(10, 0), Point2D(10, 10), Point2D(0, 10)],
                doors=[
                    Door(id="d-ok", position_m=Point2D(5.0, 0.0)),
                    Door(id="d-bad", position_m=Point2D(5.0, 5.0)),  # 9. DOOR_DETACHED
                ],
                holes=[VerticalHole(id="h0", position_m=Point2D(1, 1), riser_group_id="R1")],
            ),
            Floor(
                id="f1",
                name="1F",
                level_index=1,
                outer_polygon_m=[Point2D(0, 0), Point2D(10, 0), Point2D(10, 10), Point2D(0, 10)],
                holes=[],  # No matching R1 hole -> triggers CROSS_FLOOR_NO_RISER
            ),
        ],
    )
    b_unanchored = Building(
        id="b2",
        name="Annex",
        geo_anchor=None,  # 8. BUILDING_UNANCHORED
        floors=[],
    )

    p1 = Port(id="p1", label="eth0", port_type=PortType.BASE1000)
    p2 = Port(id="p2", label="LC", port_type=PortType.LWL_SM)
    p3 = Port(id="p3", label="eth0", port_type=PortType.BASE1000)
    p4 = Port(id="p4", label="SFP+1", port_type=PortType.SFP_PLUS)

    c1 = Component(
        id="c1",
        component_type=ComponentType.ACTIVE_SWITCH,
        name="SW-1",
        ip_address="10.0.0.1",
        mac_address="aa:bb:cc:dd:ee:ff",
        ports=[p1, p4],
        physical_building_id="b1",
        physical_floor_id="f0",
        physical_pos_m=Point2D(2, 2),
    )
    c2 = Component(
        id="c2",
        component_type=ComponentType.LWL_CONVERTER,
        name="LWL-1",
        ip_address="10.0.0.1",  # 1. DUP_IP
        mac_address="AA-BB-CC-DD-EE-FF",  # 2. DUP_MAC
        ports=[p2],
        physical_building_id="b1",
        physical_floor_id="f1",
        physical_pos_m=Point2D(3, 3),
    )
    c3 = Component(
        id="c3",
        component_type=ComponentType.ACCESS_POINT,
        name="AP-NoSSID",
        ssids=[],  # 5. AP_NO_SSID
        ports=[p3],
        # 6. UNPLACED_COMPONENT (missing physical placement)
    )

    # 3. PORT_MEDIA_MISMATCH (Base1000 <-> LWL_SM) & 7. CROSS_FLOOR_NO_RISER (f0 <-> f1)
    cab_mismatch = Cable(
        id="cab-1",
        source_component_id="c1",
        source_port_id="p1",
        target_component_id="c2",
        target_port_id="p2",
    )
    # 4. PORT_DOUBLE_OCCUPIED (p1 used again!)
    cab_double = Cable(
        id="cab-2",
        source_component_id="c1",
        source_port_id="p1",
        target_component_id="c3",
        target_port_id="p3",
    )
    # Compatible SFP+ <-> LWL_SM cable should NOT add a second PORT_MEDIA_MISMATCH
    cab_fiber_ok = Cable(
        id="cab-3",
        source_component_id="c1",
        source_port_id="p4",
        target_component_id="c2",
        target_port_id="p2",
    )

    proj = Project(
        components=[c1, c2, c3],
        cables=[cab_mismatch, cab_double, cab_fiber_ok],
        buildings=[b_anchored, b_unanchored],
    )
    issues = validate_project(proj)
    codes = {issue.rule_code for issue in issues}

    expected_codes = {
        "DUP_IP",
        "DUP_MAC",
        "PORT_MEDIA_MISMATCH",
        "PORT_DOUBLE_OCCUPIED",
        "AP_NO_SSID",
        "UNPLACED_COMPONENT",
        "CROSS_FLOOR_NO_RISER",
        "BUILDING_UNANCHORED",
        "DOOR_DETACHED",
    }
    assert codes == expected_codes

    # Verify cab-3 (SFP+ <-> LWL_SM) did NOT trigger PORT_MEDIA_MISMATCH
    mismatch_cables = {i.entity_id for i in issues if i.rule_code == "PORT_MEDIA_MISMATCH"}
    assert mismatch_cables == {"cab-1"}

    # Verify d-bad triggered DOOR_DETACHED and d-ok did not
    detached_doors = {i.entity_id for i in issues if i.rule_code == "DOOR_DETACHED"}
    assert detached_doors == {"d-bad"}
