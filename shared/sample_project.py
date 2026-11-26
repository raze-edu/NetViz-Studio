"""Realistic Berlin Sample Campus Project builder for NetViz Studio v2.1."""

from __future__ import annotations

import math

from shared.geo_math import floor_polygon_to_latlon, local_m_to_latlon
from shared.models import (
    Building,
    Cable,
    CableBundle,
    CableStyle,
    Component,
    ComponentTemplate,
    ComponentType,
    Door,
    Floor,
    GeoAnchor,
    LinePattern,
    MapPreferences,
    PhysicalCableSegment,
    Point2D,
    PortSpec,
    PortType,
    PositionSource,
    Project,
    Room,
    Stairs,
    SurveySample,
    SurveySession,
    VerticalHole,
    WifiReading,
    new_id,
)
from shared.presets import get_builtin_template, instantiate_from_template


def _port_by_label(comp: Component, label: str) -> str:
    for port in comp.ports:
        if port.label == label:
            return port.id
    raise KeyError(f"Component {comp.name} has no port named {label!r}")


def _rssi_from_distance(dist_m: float, wall_count: int = 0) -> int:
    d = max(dist_m, 1.0)
    val = int(round(-36.0 - 20.0 * math.log10(d) - wall_count * 5.0))
    return max(-92, min(-32, val))


def build_sample_campus_project() -> Project:
    """Build the realistic Berlin Campus sample project per Section 4.11."""
    # --- Buildings & Floors ---
    anchor_a = GeoAnchor(origin_lat=52.520000, origin_lon=13.405000, rotation_deg=22.5)
    bldg_a_id = new_id()
    a_f0_id = new_id()
    a_f1_id = new_id()

    a_f0_outer = [
        Point2D(0.0, 0.0),
        Point2D(24.0, 0.0),
        Point2D(24.0, 10.0),
        Point2D(14.0, 10.0),
        Point2D(14.0, 18.0),
        Point2D(0.0, 18.0),
    ]
    a_f0_hole = VerticalHole(
        id=new_id(),
        position_m=Point2D(2.0, 2.0),
        riser_group_id="RISER-A",
        label="Main Fiber Riser",
        radius_m=0.35,
    )
    a_f0 = Floor(
        id=a_f0_id,
        name="Ground Floor",
        level_index=0,
        outer_polygon_m=a_f0_outer,
        editor_view_rotation_deg=0.0,
        rooms=[
            Room(
                name="Server Room",
                polygon_m=[Point2D(0, 0), Point2D(8, 0), Point2D(8, 8), Point2D(0, 8)],
                fill_color_hex="#1E3A5F",
                wall_attenuation_db=9.0,
            ),
            Room(
                name="Utility",
                polygon_m=[Point2D(0, 8), Point2D(8, 8), Point2D(8, 10), Point2D(0, 10)],
                fill_color_hex="#1E293B",
                wall_attenuation_db=6.0,
            ),
            Room(
                name="Reception",
                polygon_m=[Point2D(8, 0), Point2D(16, 0), Point2D(16, 10), Point2D(8, 10)],
                fill_color_hex="#1E293B",
                wall_attenuation_db=5.0,
            ),
            Room(
                name="Open Office",
                polygon_m=[Point2D(16, 0), Point2D(24, 0), Point2D(24, 10), Point2D(16, 10)],
                fill_color_hex="#172554",
                wall_attenuation_db=4.5,
            ),
            Room(
                name="Lab North",
                polygon_m=[Point2D(0, 10), Point2D(14, 10), Point2D(14, 18), Point2D(0, 18)],
                fill_color_hex="#1E293B",
                wall_attenuation_db=6.5,
            ),
        ],
        doors=[
            Door(position_m=Point2D(12.0, 0.0), angle_deg=0.0, width_m=1.4),
            Door(position_m=Point2D(8.0, 4.0), angle_deg=90.0, width_m=0.9),
            Door(position_m=Point2D(16.0, 5.0), angle_deg=90.0, width_m=1.0),
            Door(position_m=Point2D(11.0, 10.0), angle_deg=0.0, width_m=1.0),
        ],
        stairs=[
            Stairs(
                polygon_m=[
                    Point2D(11.0, 11.0),
                    Point2D(13.6, 11.0),
                    Point2D(13.6, 15.0),
                    Point2D(11.0, 15.0),
                ],
                label="Main Stairs",
                direction_deg=90.0,
            )
        ],
        holes=[a_f0_hole],
    )

    a_f1_outer = [
        Point2D(0.0, 0.0),
        Point2D(24.0, 0.0),
        Point2D(24.0, 10.0),
        Point2D(0.0, 10.0),
    ]
    a_f1_hole = VerticalHole(
        id=new_id(),
        position_m=Point2D(2.0, 2.0),
        riser_group_id="RISER-A",
        label="Main Fiber Riser",
        radius_m=0.35,
    )
    a_f1 = Floor(
        id=a_f1_id,
        name="1st Floor",
        level_index=1,
        outer_polygon_m=a_f1_outer,
        editor_view_rotation_deg=0.0,
        rooms=[
            Room(
                name="IDF Closet",
                polygon_m=[Point2D(0, 0), Point2D(5, 0), Point2D(5, 5), Point2D(0, 5)],
                fill_color_hex="#1E3A5F",
                wall_attenuation_db=8.0,
            ),
            Room(
                name="Breakout",
                polygon_m=[Point2D(0, 5), Point2D(5, 5), Point2D(5, 10), Point2D(0, 10)],
                fill_color_hex="#1E293B",
                wall_attenuation_db=5.0,
            ),
            Room(
                name="Boardroom",
                polygon_m=[Point2D(5, 0), Point2D(15, 0), Point2D(15, 10), Point2D(5, 10)],
                fill_color_hex="#172554",
                wall_attenuation_db=6.0,
            ),
            Room(
                name="Engineering",
                polygon_m=[Point2D(15, 0), Point2D(24, 0), Point2D(24, 10), Point2D(15, 10)],
                fill_color_hex="#1E293B",
                wall_attenuation_db=5.0,
            ),
        ],
        doors=[
            Door(position_m=Point2D(5.0, 2.5), angle_deg=90.0, width_m=0.9),
            Door(position_m=Point2D(15.0, 5.0), angle_deg=90.0, width_m=1.0),
            Door(position_m=Point2D(2.5, 5.0), angle_deg=0.0, width_m=0.9),
        ],
        stairs=[
            Stairs(
                polygon_m=[
                    Point2D(11.5, 5.5),
                    Point2D(14.1, 5.5),
                    Point2D(14.1, 9.5),
                    Point2D(11.5, 9.5),
                ],
                label="Main Stairs",
                direction_deg=90.0,
            )
        ],
        holes=[a_f1_hole],
    )

    building_a = Building(
        id=bldg_a_id,
        name="Building A (HQ)",
        geo_anchor=anchor_a,
        floor_height_m=3.2,
        footprint_latlon=floor_polygon_to_latlon(a_f0_outer, anchor_a),
        floors=[a_f0, a_f1],
    )

    anchor_b = GeoAnchor(origin_lat=52.520350, origin_lon=13.405550, rotation_deg=-15.0)
    bldg_b_id = new_id()
    b_f0_id = new_id()
    b_f0_outer = [
        Point2D(0.0, 0.0),
        Point2D(16.0, 0.0),
        Point2D(16.0, 12.0),
        Point2D(0.0, 12.0),
    ]
    b_f0 = Floor(
        id=b_f0_id,
        name="Ground Floor",
        level_index=0,
        outer_polygon_m=b_f0_outer,
        rooms=[
            Room(
                name="Tech Closet",
                polygon_m=[Point2D(0, 0), Point2D(5, 0), Point2D(5, 5), Point2D(0, 5)],
                fill_color_hex="#1E3A5F",
                wall_attenuation_db=8.0,
            ),
            Room(
                name="Loading Dock",
                polygon_m=[Point2D(0, 5), Point2D(5, 5), Point2D(5, 12), Point2D(0, 12)],
                fill_color_hex="#1E293B",
                wall_attenuation_db=5.5,
            ),
            Room(
                name="Warehouse Bay",
                polygon_m=[Point2D(5, 0), Point2D(16, 0), Point2D(16, 12), Point2D(5, 12)],
                fill_color_hex="#172554",
                wall_attenuation_db=4.0,
            ),
        ],
        doors=[
            Door(position_m=Point2D(5.0, 2.5), angle_deg=90.0, width_m=0.9),
            Door(position_m=Point2D(10.0, 0.0), angle_deg=0.0, width_m=2.4),
        ],
        holes=[
            VerticalHole(
                id=new_id(),
                position_m=Point2D(1.5, 1.5),
                riser_group_id="RISER-B",
                label="Campus Fiber Entry",
                radius_m=0.30,
            )
        ],
    )
    building_b = Building(
        id=bldg_b_id,
        name="Building B (Warehouse)",
        geo_anchor=anchor_b,
        floor_height_m=4.0,
        footprint_latlon=floor_polygon_to_latlon(b_f0_outer, anchor_b),
        floors=[b_f0],
    )

    # --- Custom Reusable Template ---
    custom_ap_template = ComponentTemplate(
        id=new_id(),
        template_name="Wi-Fi 6E Tri-Band AP",
        component_type=ComponentType.ACCESS_POINT,
        category="Wireless",
        description="Tri-band 2.4/5/6 GHz enterprise AP with 2.5G PoE uplink + passthrough",
        default_name_prefix="AP6E",
        accent_color_hex="#16A34A",
        default_frequencies_ghz=[2.4, 5.0, 6.0],
        default_ssids=["Corp-WiFi", "Guest-WiFi"],
        default_custom_attributes={"vendor": "NetViz", "model": "AP-6E-Pro"},
        port_specs=[
            PortSpec(label="eth0", port_type=PortType.BASE2500, speed_mbps=2500, poe_capable=True),
            PortSpec(label="eth1", port_type=PortType.BASE1000, speed_mbps=1000),
        ],
        is_builtin=False,
    )

    # --- Components (All 9 Built-In ComponentTypes + Custom Template) ---
    used_names: list[str] = []

    def _spawn(
        tpl: ComponentTemplate,
        *,
        logical: Point2D,
        building_id: str,
        floor_id: str,
        phys: Point2D,
        ip: str = "",
        mac: str = "",
        ssids: list[str] | None = None,
    ) -> Component:
        comp = instantiate_from_template(tpl, used_names, pos=logical)
        used_names.append(comp.name)
        comp.ip_address = ip
        comp.mac_address = mac
        comp.physical_building_id = building_id
        comp.physical_floor_id = floor_id
        comp.physical_pos_m = phys
        if ssids is not None:
            comp.ssids = ssids
        return comp

    rtr = _spawn(
        get_builtin_template(ComponentType.ROUTER),
        logical=Point2D(80.0, 80.0),
        building_id=bldg_a_id,
        floor_id=a_f0_id,
        phys=Point2D(2.0, 6.0),
        ip="10.10.0.1",
        mac="00:11:22:00:00:01",
    )
    fw = _spawn(
        get_builtin_template(ComponentType.FIREWALL),
        logical=Point2D(320.0, 80.0),
        building_id=bldg_a_id,
        floor_id=a_f0_id,
        phys=Point2D(3.8, 6.0),
        ip="10.10.0.2",
        mac="00:11:22:00:00:02",
    )
    asw = _spawn(
        get_builtin_template(ComponentType.ACTIVE_SWITCH),
        logical=Point2D(580.0, 120.0),
        building_id=bldg_a_id,
        floor_id=a_f0_id,
        phys=Point2D(5.5, 4.5),
        ip="10.10.1.10",
        mac="00:11:22:00:01:10",
    )
    pp = _spawn(
        get_builtin_template(ComponentType.PATCH_PANEL),
        logical=Point2D(580.0, 360.0),
        building_id=bldg_a_id,
        floor_id=a_f0_id,
        phys=Point2D(3.5, 2.5),
    )
    lwl = _spawn(
        get_builtin_template(ComponentType.LWL_CONVERTER),
        logical=Point2D(900.0, 100.0),
        building_id=bldg_a_id,
        floor_id=a_f0_id,
        phys=Point2D(6.5, 2.5),
        ip="10.10.1.20",
        mac="00:11:22:00:01:20",
    )
    psw = _spawn(
        get_builtin_template(ComponentType.PASSIVE_SWITCH),
        logical=Point2D(900.0, 340.0),
        building_id=bldg_a_id,
        floor_id=a_f1_id,
        phys=Point2D(3.0, 2.5),
    )
    ap1 = _spawn(
        get_builtin_template(ComponentType.ACCESS_POINT),
        logical=Point2D(280.0, 500.0),
        building_id=bldg_a_id,
        floor_id=a_f0_id,
        phys=Point2D(19.0, 5.0),
        ip="10.10.2.11",
        mac="00:11:22:33:44:01",
        ssids=["Corp-WiFi", "Guest-WiFi"],
    )
    ap2 = _spawn(
        get_builtin_template(ComponentType.ACCESS_POINT),
        logical=Point2D(620.0, 540.0),
        building_id=bldg_a_id,
        floor_id=a_f1_id,
        phys=Point2D(12.0, 5.0),
        ip="10.10.2.12",
        mac="00:11:22:33:44:02",
        ssids=["Corp-WiFi", "Guest-WiFi"],
    )
    ap3 = _spawn(
        custom_ap_template,
        logical=Point2D(1180.0, 500.0),
        building_id=bldg_b_id,
        floor_id=b_f0_id,
        phys=Point2D(10.5, 6.0),
        ip="10.10.2.13",
        mac="00:11:22:33:44:03",
        ssids=["Corp-WiFi", "Guest-WiFi"],
    )
    plc = _spawn(
        get_builtin_template(ComponentType.POWERLAN_ADAPTER),
        logical=Point2D(1180.0, 280.0),
        building_id=bldg_b_id,
        floor_id=b_f0_id,
        phys=Point2D(2.5, 2.5),
        ip="10.10.3.10",
        mac="00:11:22:00:03:10",
    )
    dev = _spawn(
        get_builtin_template(ComponentType.DEVICE),
        logical=Point2D(900.0, 540.0),
        building_id=bldg_a_id,
        floor_id=a_f1_id,
        phys=Point2D(20.0, 5.0),
        ip="10.10.10.50",
        mac="00:11:22:00:10:50",
    )

    components = [rtr, fw, asw, pp, lwl, psw, ap1, ap2, ap3, plc, dev]

    # --- Logical & Physical Cables + Bundle ---
    bundle_id = new_id()
    c_wan_fw = Cable(
        source_component_id=rtr.id,
        source_port_id=_port_by_label(rtr, "LAN1"),
        target_component_id=fw.id,
        target_port_id=_port_by_label(fw, "WAN"),
        label="Uplink-FW",
        vlan_id="1",
        style=CableStyle(color_hex="#F59E0B", pattern=LinePattern.SOLID, width_px=3.0),
    )
    c_fw_asw = Cable(
        source_component_id=fw.id,
        source_port_id=_port_by_label(fw, "LAN"),
        target_component_id=asw.id,
        target_port_id=_port_by_label(asw, "G1"),
        label="Core-Trunk",
        vlan_id="10",
        style=CableStyle(color_hex="#EF4444", pattern=LinePattern.SOLID, width_px=3.0),
    )
    c_asw_ap1 = Cable(
        source_component_id=asw.id,
        source_port_id=_port_by_label(asw, "G2"),
        target_component_id=ap1.id,
        target_port_id=_port_by_label(ap1, "eth0"),
        label="PoE-AP01",
        vlan_id="20",
        logical_bundle_id=bundle_id,
        style=CableStyle(color_hex="#22C55E", pattern=LinePattern.SOLID, width_px=2.5),
    )
    c_asw_pp = Cable(
        source_component_id=asw.id,
        source_port_id=_port_by_label(asw, "G3"),
        target_component_id=pp.id,
        target_port_id=_port_by_label(pp, "PP-01"),
        label="Rack-Patch-01",
        vlan_id="10",
        logical_bundle_id=bundle_id,
        style=CableStyle(color_hex="#38BDF8", pattern=LinePattern.SOLID, width_px=2.5),
    )
    c_pp_psw_riser = Cable(
        source_component_id=pp.id,
        source_port_id=_port_by_label(pp, "PP-02"),
        target_component_id=psw.id,
        target_port_id=_port_by_label(psw, "P1"),
        label="Riser-A-F0-F1",
        vlan_id="10",
        style=CableStyle(color_hex="#38BDF8", pattern=LinePattern.DASHED, width_px=2.8),
        physical_segments=[
            PhysicalCableSegment(
                floor_id=a_f0_id,
                waypoints_m=[Point2D(3.5, 2.5), Point2D(2.0, 2.0)],
                end_hole_id=a_f0_hole.id,
            ),
            PhysicalCableSegment(
                floor_id=a_f1_id,
                waypoints_m=[Point2D(2.0, 2.0), Point2D(3.0, 2.5)],
                start_hole_id=a_f1_hole.id,
            ),
        ],
    )
    c_psw_ap2 = Cable(
        source_component_id=psw.id,
        source_port_id=_port_by_label(psw, "P2"),
        target_component_id=ap2.id,
        target_port_id=_port_by_label(ap2, "eth0"),
        label="F1-AP02",
        vlan_id="20",
        style=CableStyle(color_hex="#22C55E", pattern=LinePattern.SOLID, width_px=2.5),
    )
    c_psw_dev = Cable(
        source_component_id=psw.id,
        source_port_id=_port_by_label(psw, "P3"),
        target_component_id=dev.id,
        target_port_id=_port_by_label(dev, "eth0"),
        label="Eng-Workstation",
        vlan_id="10",
        style=CableStyle(color_hex="#A855F7", pattern=LinePattern.SOLID, width_px=2.2),
    )
    c_asw_lwl = Cable(
        source_component_id=asw.id,
        source_port_id=_port_by_label(asw, "G4"),
        target_component_id=lwl.id,
        target_port_id=_port_by_label(lwl, "RJ45"),
        label="Campus-Fiber-Feed",
        vlan_id="30",
        style=CableStyle(color_hex="#FACC15", pattern=LinePattern.DASH_DOT, width_px=2.5),
    )
    c_plc_ap3 = Cable(
        source_component_id=plc.id,
        source_port_id=_port_by_label(plc, "ETH1"),
        target_component_id=ap3.id,
        target_port_id=_port_by_label(ap3, "eth0"),
        label="Warehouse-AP",
        vlan_id="20",
        style=CableStyle(color_hex="#10B981", pattern=LinePattern.SOLID, width_px=2.5),
    )

    cables = [
        c_wan_fw,
        c_fw_asw,
        c_asw_ap1,
        c_asw_pp,
        c_pp_psw_riser,
        c_psw_ap2,
        c_psw_dev,
        c_asw_lwl,
        c_plc_ap3,
    ]

    bundles = [
        CableBundle(
            id=bundle_id,
            name="Core Rack Bundle",
            scope="logical",
            entry_point=Point2D(520.0, 260.0),
            exit_point=Point2D(460.0, 380.0),
            member_cable_ids=[c_asw_ap1.id, c_asw_pp.id],
            collapsed=True,
        )
    ]

    # --- 25-Sample Multi-SSID Wi-Fi Survey ---
    survey_specs: list[tuple[str, str, Point2D, PositionSource, int]] = [
        # 12 samples on Building A Ground Floor (AP-01 @ 19.0, 5.0)
        (bldg_a_id, a_f0_id, Point2D(2.5, 4.0), PositionSource.MANUAL, 2),
        (bldg_a_id, a_f0_id, Point2D(6.0, 4.0), PositionSource.MANUAL, 1),
        (bldg_a_id, a_f0_id, Point2D(9.5, 3.5), PositionSource.GPS_CALIBRATED, 1),
        (bldg_a_id, a_f0_id, Point2D(12.5, 5.0), PositionSource.GPS_CALIBRATED, 1),
        (bldg_a_id, a_f0_id, Point2D(15.0, 5.0), PositionSource.GPS_CALIBRATED, 1),
        (bldg_a_id, a_f0_id, Point2D(18.0, 4.5), PositionSource.GPS_CALIBRATED, 0),
        (bldg_a_id, a_f0_id, Point2D(21.5, 4.5), PositionSource.GPS_CALIBRATED, 0),
        (bldg_a_id, a_f0_id, Point2D(22.0, 8.0), PositionSource.MANUAL, 0),
        (bldg_a_id, a_f0_id, Point2D(17.5, 8.5), PositionSource.MANUAL, 0),
        (bldg_a_id, a_f0_id, Point2D(11.0, 12.0), PositionSource.MANUAL, 1),
        (bldg_a_id, a_f0_id, Point2D(6.0, 14.0), PositionSource.GPS, 2),
        (bldg_a_id, a_f0_id, Point2D(2.5, 16.0), PositionSource.GPS, 2),
        # 8 samples on Building A 1st Floor (AP-02 @ 12.0, 5.0)
        (bldg_a_id, a_f1_id, Point2D(2.0, 3.0), PositionSource.MANUAL, 1),
        (bldg_a_id, a_f1_id, Point2D(4.0, 7.5), PositionSource.MANUAL, 1),
        (bldg_a_id, a_f1_id, Point2D(7.5, 5.0), PositionSource.GPS_CALIBRATED, 0),
        (bldg_a_id, a_f1_id, Point2D(11.5, 5.0), PositionSource.GPS_CALIBRATED, 0),
        (bldg_a_id, a_f1_id, Point2D(14.0, 3.0), PositionSource.GPS_CALIBRATED, 0),
        (bldg_a_id, a_f1_id, Point2D(17.5, 5.0), PositionSource.GPS_CALIBRATED, 1),
        (bldg_a_id, a_f1_id, Point2D(21.0, 4.0), PositionSource.MANUAL, 1),
        (bldg_a_id, a_f1_id, Point2D(22.0, 8.5), PositionSource.MANUAL, 1),
        # 5 samples on Building B Ground Floor (AP6E-01 @ 10.5, 6.0)
        (bldg_b_id, b_f0_id, Point2D(2.5, 2.5), PositionSource.MANUAL, 1),
        (bldg_b_id, b_f0_id, Point2D(6.5, 3.0), PositionSource.GPS, 0),
        (bldg_b_id, b_f0_id, Point2D(10.5, 6.0), PositionSource.GPS_CALIBRATED, 0),
        (bldg_b_id, b_f0_id, Point2D(13.5, 9.5), PositionSource.GPS_CALIBRATED, 0),
        (bldg_b_id, b_f0_id, Point2D(2.5, 9.5), PositionSource.MANUAL, 1),
    ]

    samples: list[SurveySample] = []
    for idx, (b_id, f_id, pt, src, walls) in enumerate(survey_specs):
        anchor = anchor_a if b_id == bldg_a_id else anchor_b
        ap_pos = (
            Point2D(19.0, 5.0)
            if f_id == a_f0_id
            else (Point2D(12.0, 5.0) if f_id == a_f1_id else Point2D(10.5, 6.0))
        )
        ap_bssid = (
            ap1.mac_address
            if f_id == a_f0_id
            else (ap2.mac_address if f_id == a_f1_id else ap3.mac_address)
        )
        dist = math.hypot(pt.x - ap_pos.x, pt.y - ap_pos.y)
        rssi_5g = _rssi_from_distance(dist, walls)
        rssi_24g = min(-32, rssi_5g + 4)
        lat, lon = local_m_to_latlon(pt, anchor)
        cal_offset = Point2D(1.2, -0.8) if src == PositionSource.GPS_CALIBRATED else None

        samples.append(
            SurveySample(
                timestamp_utc=f"2026-11-25T10:{idx:02d}:00Z",
                building_id=b_id,
                floor_id=f_id,
                pos_m=pt,
                source=src,
                gps_lat=lat if src != PositionSource.MANUAL else None,
                gps_lon=lon if src != PositionSource.MANUAL else None,
                gps_accuracy_m=3.2 if src != PositionSource.MANUAL else None,
                calibration_offset_m=cal_offset,
                readings=[
                    WifiReading(
                        ssid="Corp-WiFi",
                        bssid=ap_bssid,
                        rssi_dbm=rssi_5g,
                        frequency_mhz=5180,
                    ),
                    WifiReading(
                        ssid="Guest-WiFi",
                        bssid=ap_bssid,
                        rssi_dbm=rssi_24g,
                        frequency_mhz=2412,
                    ),
                ],
            )
        )

    survey_session = SurveySession(
        name="Campus Baseline Walkthrough",
        created_utc="2026-11-25T10:00:00Z",
        device_model="Pixel 8 Pro (Demo Baseline)",
        samples=samples,
    )

    return Project(
        name="Berlin Campus Sample Network",
        custom_templates=[custom_ap_template],
        components=components,
        cables=cables,
        bundles=bundles,
        buildings=[building_a, building_b],
        surveys=[survey_session],
        map_preferences=MapPreferences(
            preferred_provider="google",
            offline_cache_margin_m=250.0,
            offline_cache_zooms=[16, 17, 18, 19],
        ),
    )
