"""Built-in component presets and reusable template instantiation/extraction."""

from __future__ import annotations

import re
from collections.abc import Iterable

from shared.models import (
    Component,
    ComponentTemplate,
    ComponentType,
    Point2D,
    Port,
    PortSpec,
    PortType,
    new_id,
)

BUILTIN_TEMPLATES: list[ComponentTemplate] = [
    ComponentTemplate(
        id="builtin-passive-switch",
        template_name="Passive Switch",
        component_type=ComponentType.PASSIVE_SWITCH,
        category="Switching",
        description="Unmanaged 5-port Gigabit Ethernet switch",
        default_name_prefix="PSW",
        accent_color_hex="#64748B",
        port_specs=[
            PortSpec(label=f"P{i}", port_type=PortType.BASE1000, speed_mbps=1000)
            for i in range(1, 6)
        ],
        is_builtin=True,
    ),
    ComponentTemplate(
        id="builtin-active-switch",
        template_name="Active Switch",
        component_type=ComponentType.ACTIVE_SWITCH,
        category="Switching",
        description="Managed 24-port PoE+ Gigabit switch with 2 SFP+ 10G uplinks",
        default_name_prefix="ASW",
        accent_color_hex="#38BDF8",
        port_specs=[
            *[
                PortSpec(
                    label=f"G{i}",
                    port_type=PortType.BASE1000,
                    speed_mbps=1000,
                    poe_capable=True,
                )
                for i in range(1, 25)
            ],
            PortSpec(label="SFP+1", port_type=PortType.SFP_PLUS, speed_mbps=10000),
            PortSpec(label="SFP+2", port_type=PortType.SFP_PLUS, speed_mbps=10000),
        ],
        is_builtin=True,
    ),
    ComponentTemplate(
        id="builtin-access-point",
        template_name="Access Point",
        component_type=ComponentType.ACCESS_POINT,
        category="Wireless",
        description="Dual-band PoE Wi-Fi Access Point",
        default_name_prefix="AP",
        accent_color_hex="#22C55E",
        default_frequencies_ghz=[2.4, 5.0],
        default_ssids=["Corp-WiFi"],
        port_specs=[
            PortSpec(
                label="eth0",
                port_type=PortType.BASE1000,
                speed_mbps=1000,
                poe_capable=True,
            ),
        ],
        is_builtin=True,
    ),
    ComponentTemplate(
        id="builtin-firewall",
        template_name="Firewall",
        component_type=ComponentType.FIREWALL,
        category="Security",
        description="Next-generation perimeter firewall with 4 copper + 2 SFP ports",
        default_name_prefix="FW",
        accent_color_hex="#EF4444",
        port_specs=[
            PortSpec(label="WAN", port_type=PortType.BASE1000, speed_mbps=1000),
            PortSpec(label="LAN", port_type=PortType.BASE1000, speed_mbps=1000),
            PortSpec(label="DMZ", port_type=PortType.BASE1000, speed_mbps=1000),
            PortSpec(label="MGMT", port_type=PortType.BASE1000, speed_mbps=1000),
            PortSpec(label="SFP1", port_type=PortType.SFP, speed_mbps=1000),
            PortSpec(label="SFP2", port_type=PortType.SFP, speed_mbps=1000),
        ],
        is_builtin=True,
    ),
    ComponentTemplate(
        id="builtin-router",
        template_name="Router",
        component_type=ComponentType.ROUTER,
        category="Routing",
        description="Multi-Gigabit WAN router with 4 Gigabit LAN ports",
        default_name_prefix="RTR",
        accent_color_hex="#F59E0B",
        port_specs=[
            PortSpec(label="WAN", port_type=PortType.BASE2500, speed_mbps=2500),
            *[
                PortSpec(label=f"LAN{i}", port_type=PortType.BASE1000, speed_mbps=1000)
                for i in range(1, 5)
            ],
        ],
        is_builtin=True,
    ),
    ComponentTemplate(
        id="builtin-lwl-converter",
        template_name="LWL Converter",
        component_type=ComponentType.LWL_CONVERTER,
        category="Media",
        description="Single-mode fiber media converter (RJ45 to LC-SM)",
        default_name_prefix="LWL",
        accent_color_hex="#FACC15",
        port_specs=[
            PortSpec(label="RJ45", port_type=PortType.BASE1000, speed_mbps=1000),
            PortSpec(label="LC-SM", port_type=PortType.LWL_SM, speed_mbps=10000),
        ],
        is_builtin=True,
    ),
    ComponentTemplate(
        id="builtin-powerlan-adapter",
        template_name="PowerLAN Adapter",
        component_type=ComponentType.POWERLAN_ADAPTER,
        category="Media",
        description="Powerline Ethernet bridge with 2 Gigabit LAN ports",
        default_name_prefix="PLC",
        accent_color_hex="#10B981",
        port_specs=[
            PortSpec(label="ETH1", port_type=PortType.BASE1000, speed_mbps=1000),
            PortSpec(label="ETH2", port_type=PortType.BASE1000, speed_mbps=1000),
            PortSpec(label="PWR", port_type=PortType.POWERLAN, speed_mbps=1200),
        ],
        is_builtin=True,
    ),
    ComponentTemplate(
        id="builtin-patch-panel",
        template_name="Patch Panel",
        component_type=ComponentType.PATCH_PANEL,
        category="Passive",
        description="24-port Cat6a rack patch panel",
        default_name_prefix="PP",
        accent_color_hex="#94A3B8",
        port_specs=[
            PortSpec(label=f"PP-{i:02d}", port_type=PortType.BASE1000, speed_mbps=1000)
            for i in range(1, 25)
        ],
        is_builtin=True,
    ),
    ComponentTemplate(
        id="builtin-device",
        template_name="End Device",
        component_type=ComponentType.DEVICE,
        category="Endpoints",
        description="Generic networked endpoint device",
        default_name_prefix="DEV",
        accent_color_hex="#A855F7",
        port_specs=[
            PortSpec(label="eth0", port_type=PortType.BASE1000, speed_mbps=1000),
        ],
        is_builtin=True,
    ),
]

BUILTIN_TEMPLATES_BY_TYPE: dict[ComponentType, ComponentTemplate] = {
    tpl.component_type: tpl for tpl in BUILTIN_TEMPLATES
}


def get_builtin_template(component_type: ComponentType) -> ComponentTemplate:
    """Return the built-in template for the given ComponentType."""
    return BUILTIN_TEMPLATES_BY_TYPE[component_type]


def _next_sequential_name(prefix: str, existing_names: Iterable[str]) -> str:
    clean_prefix = prefix.strip() or "DEV"
    used = {name.strip().lower() for name in existing_names if name and name.strip()}
    index = 1
    while True:
        candidate = f"{clean_prefix}-{index:02d}"
        if candidate.lower() not in used:
            return candidate
        index += 1


def instantiate_from_template(
    template: ComponentTemplate,
    existing_names: Iterable[str],
    pos: Point2D | None = None,
) -> Component:
    """Instantiate a new Component with sequential name and fresh UUIDs from a template."""
    ports = [
        Port(
            id=new_id(),
            label=spec.label,
            port_type=spec.port_type,
            custom_type_name=spec.custom_type_name,
            speed_mbps=spec.speed_mbps,
            poe_capable=spec.poe_capable,
        )
        for spec in template.port_specs
    ]
    logical_pos = Point2D(pos.x, pos.y) if pos is not None else Point2D(0.0, 0.0)
    return Component(
        id=new_id(),
        component_type=template.component_type,
        name=_next_sequential_name(template.default_name_prefix, existing_names),
        template_id=template.id,
        frequencies_ghz=list(template.default_frequencies_ghz),
        ssids=list(template.default_ssids),
        custom_attributes=dict(template.default_custom_attributes),
        ports=ports,
        logical_pos=logical_pos,
    )


def _derive_prefix(component: Component) -> str:
    stripped = re.sub(r"[-_ ]*\d+$", "", component.name.strip())
    if stripped:
        return stripped
    builtin = BUILTIN_TEMPLATES_BY_TYPE.get(component.component_type)
    return builtin.default_name_prefix if builtin is not None else "DEV"


def template_from_component(
    component: Component,
    template_name: str,
    category: str = "Custom",
    strip_instance_ids: bool = True,
    accent_color_hex: str | None = None,
    description: str = "",
) -> ComponentTemplate:
    """Extract a reusable ComponentTemplate from an existing Component instance."""
    builtin = BUILTIN_TEMPLATES_BY_TYPE.get(component.component_type)
    resolved_color = (
        accent_color_hex
        if accent_color_hex is not None
        else (builtin.accent_color_hex if builtin is not None else None)
    )
    port_specs = [
        PortSpec(
            label=port.label,
            port_type=port.port_type,
            custom_type_name=port.custom_type_name,
            speed_mbps=port.speed_mbps,
            poe_capable=port.poe_capable,
        )
        for port in component.ports
    ]
    template_id = (
        new_id()
        if strip_instance_ids or not component.template_id
        else component.template_id
    )
    return ComponentTemplate(
        id=template_id,
        template_name=template_name,
        component_type=component.component_type,
        category=category,
        description=description,
        default_name_prefix=_derive_prefix(component),
        accent_color_hex=resolved_color,
        default_frequencies_ghz=list(component.frequencies_ghz),
        default_ssids=list(component.ssids),
        default_custom_attributes=dict(component.custom_attributes),
        port_specs=port_specs,
        is_builtin=False,
    )
