"""Pure Python stdlib v2.1.0 data schema for NetViz Studio (v2.0.0 backward-compatible)."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

SCHEMA_VERSION = "2.1.0"


def new_id() -> str:
    """Generate a new UUID4 string identifier."""
    return str(uuid.uuid4())


def normalize_mac(mac: str) -> str:
    """Normalize a MAC/BSSID address by stripping separators and lowercasing."""
    return mac.replace(":", "").replace("-", "").replace(".", "").replace(" ", "").lower()


def _opt_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _opt_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _opt_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _point_list_from_raw(raw: Any) -> list[Point2D]:
    if not raw:
        return []
    return [Point2D.from_dict(item) for item in raw]


class ComponentType(StrEnum):
    PASSIVE_SWITCH = "passive_switch"
    ACTIVE_SWITCH = "active_switch"
    ACCESS_POINT = "access_point"
    FIREWALL = "firewall"
    ROUTER = "router"
    LWL_CONVERTER = "lwl_converter"
    POWERLAN_ADAPTER = "powerlan_adapter"
    PATCH_PANEL = "patch_panel"
    DEVICE = "device"


class PortType(StrEnum):
    BASE100 = "Base100"
    BASE1000 = "Base1000"
    BASE2500 = "Base2500"
    BASE10G = "Base10G"
    LWL_SM = "LWL_SM"
    LWL_MM = "LWL_MM"
    SFP = "SFP"
    SFP_PLUS = "SFP_PLUS"
    POWERLAN = "PowerLAN"
    COAX = "Coax"
    CUSTOM = "Custom"


class LinePattern(StrEnum):
    SOLID = "solid"
    DASHED = "dashed"
    DOTTED = "dotted"
    DASH_DOT = "dash_dot"


class PositionSource(StrEnum):
    MANUAL = "manual"
    GPS = "gps"
    GPS_CALIBRATED = "gps_calibrated"


@dataclass
class Point2D:
    x: float
    y: float

    def to_dict(self) -> dict[str, float]:
        return {"x": float(self.x), "y": float(self.y)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Point2D:
        return cls(
            x=float(data.get("x", 0.0)),
            y=float(data.get("y", 0.0)),
        )


@dataclass
class PortSpec:
    label: str
    port_type: PortType = PortType.BASE1000
    custom_type_name: str | None = None
    speed_mbps: int | None = 1000
    poe_capable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "port_type": self.port_type.value,
            "custom_type_name": self.custom_type_name,
            "speed_mbps": self.speed_mbps,
            "poe_capable": self.poe_capable,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PortSpec:
        raw_port_type = data.get("port_type")
        port_type = PortType(raw_port_type) if raw_port_type is not None else PortType.BASE1000
        speed_mbps = (
            _opt_int(data["speed_mbps"]) if "speed_mbps" in data else 1000
        )
        return cls(
            label=str(data.get("label", "")),
            port_type=port_type,
            custom_type_name=_opt_str(data.get("custom_type_name")),
            speed_mbps=speed_mbps,
            poe_capable=bool(data.get("poe_capable", False)),
        )


@dataclass
class Port(PortSpec):
    id: str = field(default_factory=new_id)

    def to_dict(self) -> dict[str, Any]:
        payload = super().to_dict()
        payload["id"] = self.id
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Port:
        spec = PortSpec.from_dict(data)
        raw_id = data.get("id")
        return cls(
            label=spec.label,
            port_type=spec.port_type,
            custom_type_name=spec.custom_type_name,
            speed_mbps=spec.speed_mbps,
            poe_capable=spec.poe_capable,
            id=str(raw_id) if raw_id else new_id(),
        )


@dataclass
class ComponentTemplate:
    template_name: str
    component_type: ComponentType
    id: str = field(default_factory=new_id)
    category: str = "Custom"
    description: str = ""
    default_name_prefix: str = "DEV"
    accent_color_hex: str | None = None
    default_frequencies_ghz: list[float] = field(default_factory=list)
    default_ssids: list[str] = field(default_factory=list)
    default_custom_attributes: dict[str, str] = field(default_factory=dict)
    port_specs: list[PortSpec] = field(default_factory=list)
    is_builtin: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "template_name": self.template_name,
            "component_type": self.component_type.value,
            "category": self.category,
            "description": self.description,
            "default_name_prefix": self.default_name_prefix,
            "accent_color_hex": self.accent_color_hex,
            "default_frequencies_ghz": [float(f) for f in self.default_frequencies_ghz],
            "default_ssids": [str(s) for s in self.default_ssids],
            "default_custom_attributes": {
                str(k): str(v) for k, v in self.default_custom_attributes.items()
            },
            "port_specs": [ps.to_dict() for ps in self.port_specs],
            "is_builtin": self.is_builtin,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ComponentTemplate:
        raw_id = data.get("id")
        raw_freqs = data.get("default_frequencies_ghz") or []
        raw_ssids = data.get("default_ssids") or []
        raw_attrs = data.get("default_custom_attributes") or {}
        raw_specs = data.get("port_specs") or []
        return cls(
            id=str(raw_id) if raw_id else new_id(),
            template_name=str(data.get("template_name", "Template")),
            component_type=ComponentType(data["component_type"]),
            category=str(data.get("category") or "Custom"),
            description=str(data.get("description") or ""),
            default_name_prefix=str(data.get("default_name_prefix") or "DEV"),
            accent_color_hex=_opt_str(data.get("accent_color_hex")),
            default_frequencies_ghz=[float(f) for f in raw_freqs],
            default_ssids=[str(s) for s in raw_ssids],
            default_custom_attributes={str(k): str(v) for k, v in raw_attrs.items()},
            port_specs=[PortSpec.from_dict(ps) for ps in raw_specs],
            is_builtin=bool(data.get("is_builtin", False)),
        )


@dataclass
class Component:
    component_type: ComponentType
    name: str
    id: str = field(default_factory=new_id)
    template_id: str | None = None
    ip_address: str = ""
    mac_address: str = ""
    frequencies_ghz: list[float] = field(default_factory=list)
    ssids: list[str] = field(default_factory=list)
    custom_attributes: dict[str, str] = field(default_factory=dict)
    ports: list[Port] = field(default_factory=list)
    logical_pos: Point2D = field(default_factory=lambda: Point2D(0.0, 0.0))
    physical_building_id: str | None = None
    physical_floor_id: str | None = None
    physical_pos_m: Point2D | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "component_type": self.component_type.value,
            "name": self.name,
            "template_id": self.template_id,
            "ip_address": self.ip_address,
            "mac_address": self.mac_address,
            "frequencies_ghz": [float(f) for f in self.frequencies_ghz],
            "ssids": [str(s) for s in self.ssids],
            "custom_attributes": {str(k): str(v) for k, v in self.custom_attributes.items()},
            "ports": [p.to_dict() for p in self.ports],
            "logical_pos": self.logical_pos.to_dict(),
            "physical_building_id": self.physical_building_id,
            "physical_floor_id": self.physical_floor_id,
            "physical_pos_m": (
                self.physical_pos_m.to_dict() if self.physical_pos_m is not None else None
            ),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Component:
        raw_id = data.get("id")
        raw_lpos = data.get("logical_pos")
        raw_ppos = data.get("physical_pos_m")
        return cls(
            id=str(raw_id) if raw_id else new_id(),
            component_type=ComponentType(data["component_type"]),
            name=str(data.get("name", "")),
            template_id=_opt_str(data.get("template_id")),
            ip_address=str(data.get("ip_address") or ""),
            mac_address=str(data.get("mac_address") or ""),
            frequencies_ghz=[float(f) for f in (data.get("frequencies_ghz") or [])],
            ssids=[str(s) for s in (data.get("ssids") or [])],
            custom_attributes={
                str(k): str(v) for k, v in (data.get("custom_attributes") or {}).items()
            },
            ports=[Port.from_dict(p) for p in (data.get("ports") or [])],
            logical_pos=(
                Point2D.from_dict(raw_lpos) if raw_lpos is not None else Point2D(0.0, 0.0)
            ),
            physical_building_id=_opt_str(data.get("physical_building_id")),
            physical_floor_id=_opt_str(data.get("physical_floor_id")),
            physical_pos_m=Point2D.from_dict(raw_ppos) if raw_ppos is not None else None,
        )


@dataclass
class CableStyle:
    color_hex: str = "#38BDF8"
    pattern: LinePattern = LinePattern.SOLID
    width_px: float = 2.5

    def to_dict(self) -> dict[str, Any]:
        return {
            "color_hex": self.color_hex,
            "pattern": self.pattern.value,
            "width_px": float(self.width_px),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CableStyle:
        raw_pattern = data.get("pattern")
        return cls(
            color_hex=str(data.get("color_hex") or "#38BDF8"),
            pattern=LinePattern(raw_pattern) if raw_pattern is not None else LinePattern.SOLID,
            width_px=float(data["width_px"]) if data.get("width_px") is not None else 2.5,
        )


@dataclass
class PhysicalCableSegment:
    floor_id: str
    waypoints_m: list[Point2D] = field(default_factory=list)
    start_hole_id: str | None = None
    end_hole_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "floor_id": self.floor_id,
            "waypoints_m": [pt.to_dict() for pt in self.waypoints_m],
            "start_hole_id": self.start_hole_id,
            "end_hole_id": self.end_hole_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PhysicalCableSegment:
        return cls(
            floor_id=str(data.get("floor_id", "")),
            waypoints_m=_point_list_from_raw(data.get("waypoints_m")),
            start_hole_id=_opt_str(data.get("start_hole_id")),
            end_hole_id=_opt_str(data.get("end_hole_id")),
        )


@dataclass
class Cable:
    source_component_id: str
    source_port_id: str
    target_component_id: str
    target_port_id: str
    id: str = field(default_factory=new_id)
    label: str = ""
    vlan_id: str = ""
    style: CableStyle = field(default_factory=CableStyle)
    logical_waypoints: list[Point2D] = field(default_factory=list)
    logical_bundle_id: str | None = None
    physical_segments: list[PhysicalCableSegment] = field(default_factory=list)
    physical_bundle_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_component_id": self.source_component_id,
            "source_port_id": self.source_port_id,
            "target_component_id": self.target_component_id,
            "target_port_id": self.target_port_id,
            "label": self.label,
            "vlan_id": self.vlan_id,
            "style": self.style.to_dict(),
            "logical_waypoints": [pt.to_dict() for pt in self.logical_waypoints],
            "logical_bundle_id": self.logical_bundle_id,
            "physical_segments": [seg.to_dict() for seg in self.physical_segments],
            "physical_bundle_id": self.physical_bundle_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Cable:
        raw_id = data.get("id")
        raw_style = data.get("style")
        return cls(
            id=str(raw_id) if raw_id else new_id(),
            source_component_id=str(data.get("source_component_id", "")),
            source_port_id=str(data.get("source_port_id", "")),
            target_component_id=str(data.get("target_component_id", "")),
            target_port_id=str(data.get("target_port_id", "")),
            label=str(data.get("label") or ""),
            vlan_id=str(data.get("vlan_id") or ""),
            style=CableStyle.from_dict(raw_style) if raw_style is not None else CableStyle(),
            logical_waypoints=_point_list_from_raw(data.get("logical_waypoints")),
            logical_bundle_id=_opt_str(data.get("logical_bundle_id")),
            physical_segments=[
                PhysicalCableSegment.from_dict(seg)
                for seg in (data.get("physical_segments") or [])
            ],
            physical_bundle_id=_opt_str(data.get("physical_bundle_id")),
        )


@dataclass
class CableBundle:
    entry_point: Point2D
    exit_point: Point2D
    id: str = field(default_factory=new_id)
    name: str = "Cable Bundle"
    scope: str = "logical"
    floor_id: str | None = None
    waypoints: list[Point2D] = field(default_factory=list)
    member_cable_ids: list[str] = field(default_factory=list)
    style: CableStyle = field(
        default_factory=lambda: CableStyle(color_hex="#F59E0B", width_px=5.5)
    )
    collapsed: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "entry_point": self.entry_point.to_dict(),
            "exit_point": self.exit_point.to_dict(),
            "name": self.name,
            "scope": self.scope,
            "floor_id": self.floor_id,
            "waypoints": [pt.to_dict() for pt in self.waypoints],
            "member_cable_ids": [str(cid) for cid in self.member_cable_ids],
            "style": self.style.to_dict(),
            "collapsed": self.collapsed,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CableBundle:
        raw_id = data.get("id")
        raw_style = data.get("style")
        return cls(
            id=str(raw_id) if raw_id else new_id(),
            entry_point=Point2D.from_dict(data.get("entry_point") or {}),
            exit_point=Point2D.from_dict(data.get("exit_point") or {}),
            name=str(data.get("name") or "Cable Bundle"),
            scope=str(data.get("scope") or "logical"),
            floor_id=_opt_str(data.get("floor_id")),
            waypoints=_point_list_from_raw(data.get("waypoints")),
            member_cable_ids=[str(cid) for cid in (data.get("member_cable_ids") or [])],
            style=(
                CableStyle.from_dict(raw_style)
                if raw_style is not None
                else CableStyle(color_hex="#F59E0B", width_px=5.5)
            ),
            collapsed=bool(data["collapsed"]) if "collapsed" in data else True,
        )


@dataclass
class GeoAnchor:
    origin_lat: float
    origin_lon: float
    rotation_deg: float = 0.0

    def to_dict(self) -> dict[str, float]:
        return {
            "origin_lat": float(self.origin_lat),
            "origin_lon": float(self.origin_lon),
            "rotation_deg": float(self.rotation_deg),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> GeoAnchor:
        return cls(
            origin_lat=float(data["origin_lat"]),
            origin_lon=float(data["origin_lon"]),
            rotation_deg=float(data.get("rotation_deg") or 0.0),
        )


@dataclass
class BlueprintUnderlay:
    archive_path: str
    meters_per_pixel: float = 0.02
    offset_m: Point2D = field(default_factory=lambda: Point2D(0.0, 0.0))
    rotation_deg: float = 0.0
    opacity: float = 0.45
    visible: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "archive_path": self.archive_path,
            "meters_per_pixel": float(self.meters_per_pixel),
            "offset_m": self.offset_m.to_dict(),
            "rotation_deg": float(self.rotation_deg),
            "opacity": float(self.opacity),
            "visible": self.visible,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> BlueprintUnderlay:
        raw_offset = data.get("offset_m")
        return cls(
            archive_path=str(data.get("archive_path", "")),
            meters_per_pixel=(
                float(data["meters_per_pixel"])
                if data.get("meters_per_pixel") is not None
                else 0.02
            ),
            offset_m=(
                Point2D.from_dict(raw_offset) if raw_offset is not None else Point2D(0.0, 0.0)
            ),
            rotation_deg=float(data.get("rotation_deg") or 0.0),
            opacity=float(data["opacity"]) if data.get("opacity") is not None else 0.45,
            visible=bool(data["visible"]) if "visible" in data else True,
        )


@dataclass
class Room:
    name: str
    polygon_m: list[Point2D]
    id: str = field(default_factory=new_id)
    fill_color_hex: str = "#1E293B"
    wall_attenuation_db: float = 6.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "polygon_m": [pt.to_dict() for pt in self.polygon_m],
            "fill_color_hex": self.fill_color_hex,
            "wall_attenuation_db": float(self.wall_attenuation_db),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Room:
        raw_id = data.get("id")
        return cls(
            id=str(raw_id) if raw_id else new_id(),
            name=str(data.get("name", "")),
            polygon_m=_point_list_from_raw(data.get("polygon_m")),
            fill_color_hex=str(data.get("fill_color_hex") or "#1E293B"),
            wall_attenuation_db=(
                float(data["wall_attenuation_db"])
                if data.get("wall_attenuation_db") is not None
                else 6.0
            ),
        )


@dataclass
class Door:
    position_m: Point2D
    angle_deg: float = 0.0
    width_m: float = 0.9
    swing_flip: bool = False
    hinge_flip: bool = False
    id: str = field(default_factory=new_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "position_m": self.position_m.to_dict(),
            "angle_deg": float(self.angle_deg),
            "width_m": float(self.width_m),
            "swing_flip": self.swing_flip,
            "hinge_flip": self.hinge_flip,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Door:
        raw_id = data.get("id")
        return cls(
            id=str(raw_id) if raw_id else new_id(),
            position_m=Point2D.from_dict(data.get("position_m") or {}),
            angle_deg=float(data.get("angle_deg") or 0.0),
            width_m=float(data["width_m"]) if data.get("width_m") is not None else 0.9,
            swing_flip=bool(data.get("swing_flip", False)),
            hinge_flip=bool(data.get("hinge_flip", False)),
        )


@dataclass
class Stairs:
    polygon_m: list[Point2D]
    label: str = "Stairs"
    direction_deg: float = 0.0
    id: str = field(default_factory=new_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "polygon_m": [pt.to_dict() for pt in self.polygon_m],
            "label": self.label,
            "direction_deg": float(self.direction_deg),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Stairs:
        raw_id = data.get("id")
        return cls(
            id=str(raw_id) if raw_id else new_id(),
            polygon_m=_point_list_from_raw(data.get("polygon_m")),
            label=str(data.get("label") or "Stairs"),
            direction_deg=float(data.get("direction_deg") or 0.0),
        )


@dataclass
class VerticalHole:
    position_m: Point2D
    riser_group_id: str
    label: str = "Riser"
    radius_m: float = 0.30
    id: str = field(default_factory=new_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "position_m": self.position_m.to_dict(),
            "riser_group_id": self.riser_group_id,
            "label": self.label,
            "radius_m": float(self.radius_m),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> VerticalHole:
        raw_id = data.get("id")
        return cls(
            id=str(raw_id) if raw_id else new_id(),
            position_m=Point2D.from_dict(data.get("position_m") or {}),
            riser_group_id=str(data.get("riser_group_id") or ""),
            label=str(data.get("label") or "Riser"),
            radius_m=float(data["radius_m"]) if data.get("radius_m") is not None else 0.30,
        )


@dataclass
class Floor:
    name: str
    outer_polygon_m: list[Point2D]
    level_index: int = 0
    editor_view_rotation_deg: float = 0.0
    blueprint: BlueprintUnderlay | None = None
    rooms: list[Room] = field(default_factory=list)
    doors: list[Door] = field(default_factory=list)
    stairs: list[Stairs] = field(default_factory=list)
    holes: list[VerticalHole] = field(default_factory=list)
    id: str = field(default_factory=new_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "outer_polygon_m": [pt.to_dict() for pt in self.outer_polygon_m],
            "level_index": int(self.level_index),
            "editor_view_rotation_deg": float(self.editor_view_rotation_deg),
            "blueprint": self.blueprint.to_dict() if self.blueprint is not None else None,
            "rooms": [r.to_dict() for r in self.rooms],
            "doors": [d.to_dict() for d in self.doors],
            "stairs": [s.to_dict() for s in self.stairs],
            "holes": [h.to_dict() for h in self.holes],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Floor:
        raw_id = data.get("id")
        raw_bp = data.get("blueprint")
        return cls(
            id=str(raw_id) if raw_id else new_id(),
            name=str(data.get("name", "")),
            outer_polygon_m=_point_list_from_raw(data.get("outer_polygon_m")),
            level_index=int(data.get("level_index") or 0),
            editor_view_rotation_deg=float(data.get("editor_view_rotation_deg") or 0.0),
            blueprint=BlueprintUnderlay.from_dict(raw_bp) if raw_bp is not None else None,
            rooms=[Room.from_dict(r) for r in (data.get("rooms") or [])],
            doors=[Door.from_dict(d) for d in (data.get("doors") or [])],
            stairs=[Stairs.from_dict(s) for s in (data.get("stairs") or [])],
            holes=[VerticalHole.from_dict(h) for h in (data.get("holes") or [])],
        )


@dataclass
class Building:
    name: str
    geo_anchor: GeoAnchor | None = None
    floor_height_m: float = 3.2
    footprint_latlon: list[tuple[float, float]] = field(default_factory=list)
    floors: list[Floor] = field(default_factory=list)
    id: str = field(default_factory=new_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "geo_anchor": self.geo_anchor.to_dict() if self.geo_anchor is not None else None,
            "floor_height_m": float(self.floor_height_m),
            "footprint_latlon": [[float(lat), float(lon)] for lat, lon in self.footprint_latlon],
            "floors": [f.to_dict() for f in self.floors],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Building:
        raw_id = data.get("id")
        raw_anchor = data.get("geo_anchor")
        raw_footprint = data.get("footprint_latlon") or []
        footprint: list[tuple[float, float]] = [
            (float(pt[0]), float(pt[1]))
            for pt in raw_footprint
            if isinstance(pt, list | tuple) and len(pt) >= 2
        ]
        return cls(
            id=str(raw_id) if raw_id else new_id(),
            name=str(data.get("name", "")),
            geo_anchor=GeoAnchor.from_dict(raw_anchor) if raw_anchor is not None else None,
            floor_height_m=(
                float(data["floor_height_m"])
                if data.get("floor_height_m") is not None
                else 3.2
            ),
            footprint_latlon=footprint,
            floors=[Floor.from_dict(f) for f in (data.get("floors") or [])],
        )


@dataclass
class WifiReading:
    ssid: str
    bssid: str
    rssi_dbm: int
    frequency_mhz: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "ssid": self.ssid,
            "bssid": self.bssid,
            "rssi_dbm": int(self.rssi_dbm),
            "frequency_mhz": int(self.frequency_mhz),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> WifiReading:
        return cls(
            ssid=str(data.get("ssid") or ""),
            bssid=str(data.get("bssid") or ""),
            rssi_dbm=int(data["rssi_dbm"]),
            frequency_mhz=int(data["frequency_mhz"]),
        )


@dataclass
class SurveySample:
    timestamp_utc: str
    building_id: str
    floor_id: str
    pos_m: Point2D
    source: PositionSource
    gps_lat: float | None = None
    gps_lon: float | None = None
    gps_accuracy_m: float | None = None
    calibration_offset_m: Point2D | None = None
    readings: list[WifiReading] = field(default_factory=list)
    id: str = field(default_factory=new_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "timestamp_utc": self.timestamp_utc,
            "building_id": self.building_id,
            "floor_id": self.floor_id,
            "pos_m": self.pos_m.to_dict(),
            "source": self.source.value,
            "gps_lat": self.gps_lat,
            "gps_lon": self.gps_lon,
            "gps_accuracy_m": self.gps_accuracy_m,
            "calibration_offset_m": (
                self.calibration_offset_m.to_dict()
                if self.calibration_offset_m is not None
                else None
            ),
            "readings": [r.to_dict() for r in self.readings],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SurveySample:
        raw_id = data.get("id")
        raw_cal = data.get("calibration_offset_m")
        return cls(
            id=str(raw_id) if raw_id else new_id(),
            timestamp_utc=str(data.get("timestamp_utc") or ""),
            building_id=str(data.get("building_id") or ""),
            floor_id=str(data.get("floor_id") or ""),
            pos_m=Point2D.from_dict(data.get("pos_m") or {}),
            source=PositionSource(data.get("source") or PositionSource.MANUAL.value),
            gps_lat=_opt_float(data.get("gps_lat")),
            gps_lon=_opt_float(data.get("gps_lon")),
            gps_accuracy_m=_opt_float(data.get("gps_accuracy_m")),
            calibration_offset_m=Point2D.from_dict(raw_cal) if raw_cal is not None else None,
            readings=[WifiReading.from_dict(r) for r in (data.get("readings") or [])],
        )


@dataclass
class SurveySession:
    name: str
    created_utc: str
    device_model: str = ""
    samples: list[SurveySample] = field(default_factory=list)
    id: str = field(default_factory=new_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "created_utc": self.created_utc,
            "device_model": self.device_model,
            "samples": [s.to_dict() for s in self.samples],
        }

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SurveySession:
        raw_id = data.get("id")
        return cls(
            id=str(raw_id) if raw_id else new_id(),
            name=str(data.get("name") or "Survey Session"),
            created_utc=str(data.get("created_utc") or ""),
            device_model=str(data.get("device_model") or ""),
            samples=[SurveySample.from_dict(s) for s in (data.get("samples") or [])],
        )

    @classmethod
    def from_json(cls, text: str) -> SurveySession:
        return cls.from_dict(json.loads(text))


@dataclass
class MapPreferences:
    preferred_provider: str = "google"
    offline_cache_margin_m: float = 250.0
    offline_cache_zooms: list[int] = field(default_factory=lambda: [16, 17, 18, 19])

    def to_dict(self) -> dict[str, Any]:
        return {
            "preferred_provider": self.preferred_provider,
            "offline_cache_margin_m": float(self.offline_cache_margin_m),
            "offline_cache_zooms": [int(z) for z in self.offline_cache_zooms],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> MapPreferences:
        raw_zooms = data.get("offline_cache_zooms")
        return cls(
            preferred_provider=str(data.get("preferred_provider") or "google"),
            offline_cache_margin_m=(
                float(data["offline_cache_margin_m"])
                if data.get("offline_cache_margin_m") is not None
                else 250.0
            ),
            offline_cache_zooms=(
                [int(z) for z in raw_zooms] if raw_zooms is not None else [16, 17, 18, 19]
            ),
        )


@dataclass
class Project:
    schema_version: str = SCHEMA_VERSION
    id: str = field(default_factory=new_id)
    name: str = "Untitled Network"
    custom_templates: list[ComponentTemplate] = field(default_factory=list)
    components: list[Component] = field(default_factory=list)
    cables: list[Cable] = field(default_factory=list)
    bundles: list[CableBundle] = field(default_factory=list)
    buildings: list[Building] = field(default_factory=list)
    surveys: list[SurveySession] = field(default_factory=list)
    map_preferences: MapPreferences = field(default_factory=MapPreferences)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "name": self.name,
            "custom_templates": [t.to_dict() for t in self.custom_templates],
            "components": [c.to_dict() for c in self.components],
            "cables": [c.to_dict() for c in self.cables],
            "bundles": [b.to_dict() for b in self.bundles],
            "buildings": [b.to_dict() for b in self.buildings],
            "surveys": [s.to_dict() for s in self.surveys],
            "map_preferences": self.map_preferences.to_dict(),
        }

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Project:
        raw_id = data.get("id")
        raw_map_prefs = data.get("map_preferences")
        raw_version = str(data.get("schema_version") or SCHEMA_VERSION)
        return cls(
            schema_version=raw_version,
            id=str(raw_id) if raw_id else new_id(),
            name=str(data.get("name") or "Untitled Network"),
            custom_templates=[
                ComponentTemplate.from_dict(t) for t in (data.get("custom_templates") or [])
            ],
            components=[Component.from_dict(c) for c in (data.get("components") or [])],
            cables=[Cable.from_dict(c) for c in (data.get("cables") or [])],
            bundles=[CableBundle.from_dict(b) for b in (data.get("bundles") or [])],
            buildings=[Building.from_dict(b) for b in (data.get("buildings") or [])],
            surveys=[SurveySession.from_dict(s) for s in (data.get("surveys") or [])],
            map_preferences=(
                MapPreferences.from_dict(raw_map_prefs)
                if raw_map_prefs is not None
                else MapPreferences()
            ),
        )

    @classmethod
    def from_json(cls, text: str) -> Project:
        return cls.from_dict(json.loads(text))

    def get_component(self, component_id: str) -> Component | None:
        """Return the component matching component_id, or None if not found."""
        for comp in self.components:
            if comp.id == component_id:
                return comp
        return None

    def get_port(self, component_id: str, port_id: str) -> Port | None:
        """Return the port matching port_id on component_id, or None."""
        comp = self.get_component(component_id)
        if comp is None:
            return None
        for port in comp.ports:
            if port.id == port_id:
                return port
        return None

    def get_target_ssids(self) -> list[str]:
        """Return sorted unique non-empty SSIDs configured across all components."""
        ssids = {
            ssid.strip()
            for comp in self.components
            for ssid in comp.ssids
            if ssid and ssid.strip()
        }
        return sorted(ssids)

    def is_port_occupied(
        self,
        component_id: str,
        port_id: str,
        exclude_cable_id: str | None = None,
    ) -> bool:
        """Return True if any non-excluded cable terminates on (component_id, port_id)."""
        for cable in self.cables:
            if exclude_cable_id is not None and cable.id == exclude_cable_id:
                continue
            if (
                cable.source_component_id == component_id
                and cable.source_port_id == port_id
            ) or (
                cable.target_component_id == component_id
                and cable.target_port_id == port_id
            ):
                return True
        return False

    def get_port_connection(
        self,
        component_id: str,
        port_id: str,
    ) -> tuple[Cable, Component, Port] | None:
        """Return (cable, peer_component, peer_port) connected to (component_id, port_id)."""
        for cable in self.cables:
            if (
                cable.source_component_id == component_id
                and cable.source_port_id == port_id
            ):
                peer_comp_id = cable.target_component_id
                peer_port_id = cable.target_port_id
            elif (
                cable.target_component_id == component_id
                and cable.target_port_id == port_id
            ):
                peer_comp_id = cable.source_component_id
                peer_port_id = cable.source_port_id
            else:
                continue

            peer_comp = self.get_component(peer_comp_id)
            if peer_comp is None:
                return None
            peer_port = self.get_port(peer_comp_id, peer_port_id)
            if peer_port is None:
                return None
            return cable, peer_comp, peer_port
        return None

    def get_building(self, building_id: str) -> Building | None:
        """Return the building matching building_id, or None."""
        for building in self.buildings:
            if building.id == building_id:
                return building
        return None

    def get_floor(self, building_id: str, floor_id: str) -> Floor | None:
        """Return the floor matching floor_id inside building_id, or None."""
        building = self.get_building(building_id)
        if building is None:
            return None
        for floor in building.floors:
            if floor.id == floor_id:
                return floor
        return None

    def find_component_by_mac(self, bssid: str) -> Component | None:
        """Look up a component by MAC/BSSID normalizing case and :/-/. separators."""
        target = normalize_mac(bssid)
        if not target:
            return None
        for comp in self.components:
            if normalize_mac(comp.mac_address) == target:
                return comp
        return None
