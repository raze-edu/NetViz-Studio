"""Live 9-rule network and floor-plan validator (pure Python stdlib)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from shared.geo_math import point_to_segment_distance
from shared.models import (
    ComponentType,
    Floor,
    PortType,
    Project,
    normalize_mac,
)

_COPPER_TYPES = {
    PortType.BASE100,
    PortType.BASE1000,
    PortType.BASE2500,
    PortType.BASE10G,
}
_FIBER_SM_TYPES = {PortType.LWL_SM}
_FIBER_MM_TYPES = {PortType.LWL_MM}
_SFP_TYPES = {PortType.SFP, PortType.SFP_PLUS}


@dataclass(frozen=True)
class ValidationIssue:
    rule_code: str
    severity: str  # "error" | "warning" | "info"
    message: str
    entity_type: str  # "component" | "cable" | "building" | "door" | "port"
    entity_id: str
    extra: dict[str, str] = field(default_factory=dict)


def _port_media_family(port_type: PortType) -> str:
    if port_type in _COPPER_TYPES:
        return "copper"
    if port_type in _FIBER_SM_TYPES:
        return "fiber_sm"
    if port_type in _FIBER_MM_TYPES:
        return "fiber_mm"
    if port_type in _SFP_TYPES:
        return "sfp"
    if port_type == PortType.POWERLAN:
        return "powerlan"
    if port_type == PortType.COAX:
        return "coax"
    return "custom"


def are_port_types_compatible(a: PortType, b: PortType) -> bool:
    """Return True if two PortTypes are media-compatible without a converter."""
    fam_a = _port_media_family(a)
    fam_b = _port_media_family(b)
    if fam_a == fam_b:
        return True
    # SFP/SFP+ cages accept either SM or MM fiber transceivers directly.
    if (fam_a == "sfp" and fam_b in {"fiber_sm", "fiber_mm"}) or (
        fam_b == "sfp" and fam_a in {"fiber_sm", "fiber_mm"}
    ):
        return True
    return False


def _floor_wall_segments(floor: Floor) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
    rings = [floor.outer_polygon_m, *(room.polygon_m for room in floor.rooms)]
    for ring in rings:
        n = len(ring)
        if n < 2:
            continue
        for i in range(n):
            p1 = ring[i]
            p2 = ring[(i + 1) % n]
            segments.append(((p1.x, p1.y), (p2.x, p2.y)))
    return segments


def _min_door_wall_distance_m(floor: Floor, x: float, y: float) -> float:
    from shared.models import Point2D

    pt = Point2D(x, y)
    rings = [floor.outer_polygon_m, *(room.polygon_m for room in floor.rooms)]
    best = float("inf")
    for ring in rings:
        n = len(ring)
        if n < 2:
            continue
        for i in range(n):
            d = point_to_segment_distance(pt, ring[i], ring[(i + 1) % n])
            if d < best:
                best = d
    return best


def validate_project(project: Project, door_wall_tol_m: float = 0.5) -> list[ValidationIssue]:
    """Run all 9 validation rules across project components, cables, buildings, and floors."""
    issues: list[ValidationIssue] = []

    # 1. DUP_IP & 2. DUP_MAC & 5. AP_NO_SSID & 6. UNPLACED_COMPONENT
    by_ip: dict[str, list[str]] = defaultdict(list)
    by_mac: dict[str, list[str]] = defaultdict(list)
    comp_names: dict[str, str] = {}

    for comp in project.components:
        comp_names[comp.id] = comp.name
        ip = comp.ip_address.strip()
        if ip:
            by_ip[ip].append(comp.id)
        mac = normalize_mac(comp.mac_address)
        if mac:
            by_mac[mac].append(comp.id)

        if comp.component_type == ComponentType.ACCESS_POINT:
            if not any(s.strip() for s in comp.ssids):
                issues.append(
                    ValidationIssue(
                        rule_code="AP_NO_SSID",
                        severity="warning",
                        message=f"Access Point '{comp.name}' has no SSID configured.",
                        entity_type="component",
                        entity_id=comp.id,
                    )
                )

        if (
            comp.physical_building_id is None
            or comp.physical_floor_id is None
            or comp.physical_pos_m is None
            or project.get_floor(comp.physical_building_id, comp.physical_floor_id) is None
        ):
            issues.append(
                ValidationIssue(
                    rule_code="UNPLACED_COMPONENT",
                    severity="warning",
                    message=f"Component '{comp.name}' is not placed on any floor plan.",
                    entity_type="component",
                    entity_id=comp.id,
                )
            )

    for ip, comp_ids in by_ip.items():
        if len(comp_ids) > 1:
            names = ", ".join(comp_names[cid] for cid in comp_ids)
            for cid in comp_ids:
                issues.append(
                    ValidationIssue(
                        rule_code="DUP_IP",
                        severity="error",
                        message=f"Duplicate IP address {ip} shared by: {names}.",
                        entity_type="component",
                        entity_id=cid,
                        extra={"ip_address": ip},
                    )
                )

    for mac, comp_ids in by_mac.items():
        if len(comp_ids) > 1:
            names = ", ".join(comp_names[cid] for cid in comp_ids)
            for cid in comp_ids:
                issues.append(
                    ValidationIssue(
                        rule_code="DUP_MAC",
                        severity="error",
                        message=f"Duplicate MAC address {mac} shared by: {names}.",
                        entity_type="component",
                        entity_id=cid,
                        extra={"mac_address": mac},
                    )
                )

    # 3. PORT_MEDIA_MISMATCH, 4. PORT_DOUBLE_OCCUPIED, 7. CROSS_FLOOR_NO_RISER
    port_cables: dict[tuple[str, str], list[str]] = defaultdict(list)

    for cable in project.cables:
        port_cables[(cable.source_component_id, cable.source_port_id)].append(cable.id)
        port_cables[(cable.target_component_id, cable.target_port_id)].append(cable.id)

        src_comp = project.get_component(cable.source_component_id)
        dst_comp = project.get_component(cable.target_component_id)
        src_port = project.get_port(cable.source_component_id, cable.source_port_id)
        dst_port = project.get_port(cable.target_component_id, cable.target_port_id)

        if src_port is not None and dst_port is not None:
            if not are_port_types_compatible(src_port.port_type, dst_port.port_type):
                src_label = f"{src_comp.name}[{src_port.label}]" if src_comp else src_port.label
                dst_label = f"{dst_comp.name}[{dst_port.label}]" if dst_comp else dst_port.label
                issues.append(
                    ValidationIssue(
                        rule_code="PORT_MEDIA_MISMATCH",
                        severity="warning",
                        message=(
                            f"Port media mismatch on cable '{cable.label or cable.id}': "
                            f"{src_label} ({src_port.port_type.value}) <-> "
                            f"{dst_label} ({dst_port.port_type.value})."
                        ),
                        entity_type="cable",
                        entity_id=cable.id,
                    )
                )

        if (
            src_comp is not None
            and dst_comp is not None
            and src_comp.physical_building_id is not None
            and src_comp.physical_building_id == dst_comp.physical_building_id
            and src_comp.physical_floor_id is not None
            and dst_comp.physical_floor_id is not None
            and src_comp.physical_floor_id != dst_comp.physical_floor_id
        ):
            src_floor = project.get_floor(
                src_comp.physical_building_id, src_comp.physical_floor_id
            )
            dst_floor = project.get_floor(
                dst_comp.physical_building_id, dst_comp.physical_floor_id
            )
            src_groups = (
                {h.riser_group_id.strip() for h in src_floor.holes if h.riser_group_id.strip()}
                if src_floor is not None
                else set()
            )
            dst_groups = (
                {h.riser_group_id.strip() for h in dst_floor.holes if h.riser_group_id.strip()}
                if dst_floor is not None
                else set()
            )
            if not (src_groups & dst_groups):
                issues.append(
                    ValidationIssue(
                        rule_code="CROSS_FLOOR_NO_RISER",
                        severity="warning",
                        message=(
                            f"Cross-floor cable '{cable.label or cable.id}' "
                            f"({src_comp.name} -> {dst_comp.name}) has no shared vertical riser."
                        ),
                        entity_type="cable",
                        entity_id=cable.id,
                    )
                )

    for (comp_id, port_id), cable_ids in port_cables.items():
        if len(cable_ids) > 1:
            comp = project.get_component(comp_id)
            port = project.get_port(comp_id, port_id)
            label = f"{comp.name}[{port.label}]" if comp and port else f"{comp_id}:{port_id}"
            issues.append(
                ValidationIssue(
                    rule_code="PORT_DOUBLE_OCCUPIED",
                    severity="error",
                    message=f"Port {label} is connected by {len(cable_ids)} cables.",
                    entity_type="port",
                    entity_id=port_id,
                    extra={"component_id": comp_id},
                )
            )

    # 8. BUILDING_UNANCHORED & 9. DOOR_DETACHED
    for building in project.buildings:
        if building.geo_anchor is None:
            issues.append(
                ValidationIssue(
                    rule_code="BUILDING_UNANCHORED",
                    severity="warning",
                    message=f"Building '{building.name}' is not geo-anchored on the map.",
                    entity_type="building",
                    entity_id=building.id,
                )
            )
        for floor in building.floors:
            for door in floor.doors:
                dist = _min_door_wall_distance_m(floor, door.position_m.x, door.position_m.y)
                if dist > door_wall_tol_m:
                    issues.append(
                        ValidationIssue(
                            rule_code="DOOR_DETACHED",
                            severity="warning",
                            message=(
                                f"Door on '{building.name} / {floor.name}' is detached "
                                f"({dist:.2f} m from nearest wall)."
                            ),
                            entity_type="door",
                            entity_id=door.id,
                            extra={"building_id": building.id, "floor_id": floor.id},
                        )
                    )

    return issues
