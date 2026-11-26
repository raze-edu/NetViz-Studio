"""Reactive ProjectState, QUndoStack commands, 120s autosave, and footprint sync."""

from __future__ import annotations

import copy
import gc
import tempfile
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QUndoCommand, QUndoStack

from desktop.config import get_recovery_path
from shared.bundle_io import NetvizBundle, ensure_tiles_db, load_netviz, save_netviz
from shared.geo_math import floor_polygon_to_latlon
from shared.models import (
    BlueprintUnderlay,
    Building,
    Cable,
    CableBundle,
    CableStyle,
    Component,
    ComponentTemplate,
    Door,
    Floor,
    GeoAnchor,
    Point2D,
    Port,
    Project,
    Room,
    Stairs,
    SurveySession,
    VerticalHole,
)
from shared.validator import ValidationIssue, validate_project

AUTOSAVE_INTERVAL_MS = 120_000


def sync_building_footprint(building: Building) -> None:
    """Recompute building.footprint_latlon deterministically from floor0 & geo_anchor."""
    if building.geo_anchor is None:
        building.footprint_latlon = []
        return
    if building.floors and building.floors[0].outer_polygon_m:
        building.footprint_latlon = floor_polygon_to_latlon(
            building.floors[0].outer_polygon_m,
            building.geo_anchor,
        )
    else:
        building.footprint_latlon = []


# ---------------------------------------------------------------------------
# QUndoCommand Implementations (Logical, Map, Floor, Physical, Survey)
# ---------------------------------------------------------------------------


class AddComponentCommand(QUndoCommand):
    """Add a Component to the project."""

    def __init__(self, state: ProjectState, component: Component) -> None:
        super().__init__(f"Add {component.name}")
        self._state = state
        self._component = copy.deepcopy(component)

    def redo(self) -> None:
        if self._state.project.get_component(self._component.id) is None:
            self._state.project.components.append(copy.deepcopy(self._component))
        self._state.notify_components_changed()

    def undo(self) -> None:
        self._state.project.components = [
            c for c in self._state.project.components if c.id != self._component.id
        ]
        self._state.notify_components_changed()


class RemoveComponentsCommand(QUndoCommand):
    """Remove one or more components along with any connected cables."""

    def __init__(self, state: ProjectState, component_ids: Sequence[str]) -> None:
        super().__init__("Remove Component(s)")
        self._state = state
        id_set = set(component_ids)
        self._removed_components = [
            copy.deepcopy(c) for c in state.project.components if c.id in id_set
        ]
        self._removed_cables = [
            copy.deepcopy(cab)
            for cab in state.project.cables
            if cab.source_component_id in id_set or cab.target_component_id in id_set
        ]
        self._prev_bundles = copy.deepcopy(state.project.bundles)

    def redo(self) -> None:
        comp_ids = {c.id for c in self._removed_components}
        cable_ids = {cab.id for cab in self._removed_cables}
        self._state.project.components = [
            c for c in self._state.project.components if c.id not in comp_ids
        ]
        self._state.project.cables = [
            cab for cab in self._state.project.cables if cab.id not in cable_ids
        ]
        if cable_ids:
            surviving_bundles: list[CableBundle] = []
            for bundle in self._state.project.bundles:
                bundle.member_cable_ids = [
                    cid for cid in bundle.member_cable_ids if cid not in cable_ids
                ]
                if len(bundle.member_cable_ids) >= 2:
                    surviving_bundles.append(bundle)
                else:
                    for cid in bundle.member_cable_ids:
                        for cab in self._state.project.cables:
                            if cab.id == cid:
                                if bundle.scope == "logical":
                                    cab.logical_bundle_id = None
                                else:
                                    cab.physical_bundle_id = None
            self._state.project.bundles = surviving_bundles
        self._state.notify_components_changed()
        if self._removed_cables:
            self._state.notify_cables_changed()
            self._state.notify_bundles_changed()

    def undo(self) -> None:
        existing_comp_ids = {c.id for c in self._state.project.components}
        for comp in self._removed_components:
            if comp.id not in existing_comp_ids:
                self._state.project.components.append(copy.deepcopy(comp))
        existing_cable_ids = {c.id for c in self._state.project.cables}
        for cab in self._removed_cables:
            if cab.id not in existing_cable_ids:
                self._state.project.cables.append(copy.deepcopy(cab))
        self._state.project.bundles = copy.deepcopy(self._prev_bundles)
        self._state.notify_components_changed()
        if self._removed_cables:
            self._state.notify_cables_changed()
            self._state.notify_bundles_changed()


class MoveComponentsCommand(QUndoCommand):
    """Move logical_pos for one or more components."""

    def __init__(
        self,
        state: ProjectState,
        moves: dict[str, tuple[Point2D, Point2D]],
        text: str = "Move Component(s)",
    ) -> None:
        super().__init__(text)
        self._state = state
        self._moves = {
            cid: (copy.deepcopy(old_pos), copy.deepcopy(new_pos))
            for cid, (old_pos, new_pos) in moves.items()
        }

    def redo(self) -> None:
        for cid, (_, new_pos) in self._moves.items():
            comp = self._state.project.get_component(cid)
            if comp is not None:
                comp.logical_pos = copy.deepcopy(new_pos)
        self._state.notify_components_changed()

    def undo(self) -> None:
        for cid, (old_pos, _) in self._moves.items():
            comp = self._state.project.get_component(cid)
            if comp is not None:
                comp.logical_pos = copy.deepcopy(old_pos)
        self._state.notify_components_changed()


class UpdateComponentCommand(QUndoCommand):
    """Update a Component (attributes, ports, etc.) and prune cables for removed ports."""

    def __init__(
        self,
        state: ProjectState,
        component_id: str,
        new_component: Component,
        text: str = "Update Component",
    ) -> None:
        super().__init__(text)
        self._state = state
        self._component_id = component_id
        old_comp = state.project.get_component(component_id)
        if old_comp is None:
            raise KeyError(f"Unknown component {component_id}")
        self._old_component = copy.deepcopy(old_comp)
        self._new_component = copy.deepcopy(new_component)
        new_port_ids = {p.id for p in self._new_component.ports}
        self._orphaned_cables = [
            copy.deepcopy(cab)
            for cab in state.project.cables
            if (cab.source_component_id == component_id and cab.source_port_id not in new_port_ids)
            or (cab.target_component_id == component_id and cab.target_port_id not in new_port_ids)
        ]

    def redo(self) -> None:
        for idx, comp in enumerate(self._state.project.components):
            if comp.id == self._component_id:
                self._state.project.components[idx] = copy.deepcopy(self._new_component)
                break
        if self._orphaned_cables:
            orphan_ids = {c.id for c in self._orphaned_cables}
            self._state.project.cables = [
                c for c in self._state.project.cables if c.id not in orphan_ids
            ]
        self._state.notify_components_changed()
        if self._orphaned_cables:
            self._state.notify_cables_changed()

    def undo(self) -> None:
        for idx, comp in enumerate(self._state.project.components):
            if comp.id == self._component_id:
                self._state.project.components[idx] = copy.deepcopy(self._old_component)
                break
        if self._orphaned_cables:
            existing_ids = {c.id for c in self._state.project.cables}
            for cab in self._orphaned_cables:
                if cab.id not in existing_ids:
                    self._state.project.cables.append(copy.deepcopy(cab))
        self._state.notify_components_changed()
        if self._orphaned_cables:
            self._state.notify_cables_changed()


class ConnectCableCommand(QUndoCommand):
    """Connect two ports with a new Cable."""

    def __init__(self, state: ProjectState, cable: Cable) -> None:
        super().__init__("Connect Cable")
        self._state = state
        self._cable = copy.deepcopy(cable)

    def redo(self) -> None:
        if all(c.id != self._cable.id for c in self._state.project.cables):
            self._state.project.cables.append(copy.deepcopy(self._cable))
        self._state.notify_cables_changed()

    def undo(self) -> None:
        self._state.project.cables = [
            c for c in self._state.project.cables if c.id != self._cable.id
        ]
        self._state.notify_cables_changed()


class DisconnectCableCommand(QUndoCommand):
    """Remove a Cable and clean up bundle membership."""

    def __init__(self, state: ProjectState, cable_id: str) -> None:
        super().__init__("Disconnect Cable")
        self._state = state
        self._cable_id = cable_id
        self._removed = next(
            (copy.deepcopy(c) for c in state.project.cables if c.id == cable_id),
            None,
        )
        self._prev_bundles = copy.deepcopy(state.project.bundles)

    def redo(self) -> None:
        self._state.project.cables = [
            c for c in self._state.project.cables if c.id != self._cable_id
        ]
        surviving: list[CableBundle] = []
        for bundle in self._state.project.bundles:
            bundle.member_cable_ids = [
                cid for cid in bundle.member_cable_ids if cid != self._cable_id
            ]
            if len(bundle.member_cable_ids) >= 2:
                surviving.append(bundle)
            else:
                for cid in bundle.member_cable_ids:
                    for cab in self._state.project.cables:
                        if cab.id == cid:
                            if bundle.scope == "logical":
                                cab.logical_bundle_id = None
                            else:
                                cab.physical_bundle_id = None
        self._state.project.bundles = surviving
        self._state.notify_cables_changed()
        self._state.notify_bundles_changed()

    def undo(self) -> None:
        if self._removed is not None and all(
            c.id != self._removed.id for c in self._state.project.cables
        ):
            self._state.project.cables.append(copy.deepcopy(self._removed))
        self._state.project.bundles = copy.deepcopy(self._prev_bundles)
        self._state.notify_cables_changed()
        self._state.notify_bundles_changed()


class UpdateCableCommand(QUndoCommand):
    """Update Cable properties (label, vlan_id, style, endpoints, waypoints, segments)."""

    def __init__(
        self,
        state: ProjectState,
        cable_id: str,
        new_cable: Cable,
        text: str = "Update Cable",
    ) -> None:
        super().__init__(text)
        self._state = state
        self._cable_id = cable_id
        old_cab = next((c for c in state.project.cables if c.id == cable_id), None)
        if old_cab is None:
            raise KeyError(f"Unknown cable {cable_id}")
        self._old_cable = copy.deepcopy(old_cab)
        self._new_cable = copy.deepcopy(new_cable)

    def redo(self) -> None:
        for idx, cab in enumerate(self._state.project.cables):
            if cab.id == self._cable_id:
                self._state.project.cables[idx] = copy.deepcopy(self._new_cable)
                break
        self._state.notify_cables_changed()

    def undo(self) -> None:
        for idx, cab in enumerate(self._state.project.cables):
            if cab.id == self._cable_id:
                self._state.project.cables[idx] = copy.deepcopy(self._old_cable)
                break
        self._state.notify_cables_changed()


class SetCableWaypointsCommand(QUndoCommand):
    """Update logical waypoints of a cable."""

    def __init__(
        self,
        state: ProjectState,
        cable_id: str,
        new_waypoints: Sequence[Point2D],
    ) -> None:
        super().__init__("Edit Cable Waypoints")
        self._state = state
        self._cable_id = cable_id
        cab = next((c for c in state.project.cables if c.id == cable_id), None)
        if cab is None:
            raise KeyError(f"Unknown cable {cable_id}")
        self._old_waypoints = copy.deepcopy(cab.logical_waypoints)
        self._new_waypoints = [copy.deepcopy(pt) for pt in new_waypoints]

    def redo(self) -> None:
        for cab in self._state.project.cables:
            if cab.id == self._cable_id:
                cab.logical_waypoints = copy.deepcopy(self._new_waypoints)
                break
        self._state.notify_cables_changed()

    def undo(self) -> None:
        for cab in self._state.project.cables:
            if cab.id == self._cable_id:
                cab.logical_waypoints = copy.deepcopy(self._old_waypoints)
                break
        self._state.notify_cables_changed()


class CreateBundleCommand(QUndoCommand):
    """Create a CableBundle and assign member cables."""

    def __init__(self, state: ProjectState, bundle: CableBundle) -> None:
        super().__init__(f"Create Bundle {bundle.name}")
        self._state = state
        self._bundle = copy.deepcopy(bundle)
        self._prev_cables = copy.deepcopy(state.project.cables)

    def redo(self) -> None:
        if all(b.id != self._bundle.id for b in self._state.project.bundles):
            self._state.project.bundles.append(copy.deepcopy(self._bundle))
        member_ids = set(self._bundle.member_cable_ids)
        for cab in self._state.project.cables:
            if cab.id in member_ids:
                if self._bundle.scope == "logical":
                    cab.logical_bundle_id = self._bundle.id
                else:
                    cab.physical_bundle_id = self._bundle.id
        self._state.notify_bundles_changed()
        self._state.notify_cables_changed()

    def undo(self) -> None:
        self._state.project.bundles = [
            b for b in self._state.project.bundles if b.id != self._bundle.id
        ]
        self._state.project.cables = copy.deepcopy(self._prev_cables)
        self._state.notify_bundles_changed()
        self._state.notify_cables_changed()


class UpdateBundleCommand(QUndoCommand):
    """Update a CableBundle (waypoints, entry/exit, collapsed, style, members)."""

    def __init__(
        self,
        state: ProjectState,
        bundle_id: str,
        new_bundle: CableBundle,
        text: str = "Update Bundle",
    ) -> None:
        super().__init__(text)
        self._state = state
        self._bundle_id = bundle_id
        old_bundle = next((b for b in state.project.bundles if b.id == bundle_id), None)
        if old_bundle is None:
            raise KeyError(f"Unknown bundle {bundle_id}")
        self._old_bundle = copy.deepcopy(old_bundle)
        self._new_bundle = copy.deepcopy(new_bundle)
        self._prev_cables = copy.deepcopy(state.project.cables)

    def redo(self) -> None:
        for idx, b in enumerate(self._state.project.bundles):
            if b.id == self._bundle_id:
                self._state.project.bundles[idx] = copy.deepcopy(self._new_bundle)
                break
        member_ids = set(self._new_bundle.member_cable_ids)
        for cab in self._state.project.cables:
            if self._new_bundle.scope == "logical":
                if cab.id in member_ids:
                    cab.logical_bundle_id = self._bundle_id
                elif cab.logical_bundle_id == self._bundle_id:
                    cab.logical_bundle_id = None
            else:
                if cab.id in member_ids:
                    cab.physical_bundle_id = self._bundle_id
                elif cab.physical_bundle_id == self._bundle_id:
                    cab.physical_bundle_id = None
        self._state.notify_bundles_changed()
        self._state.notify_cables_changed()

    def undo(self) -> None:
        for idx, b in enumerate(self._state.project.bundles):
            if b.id == self._bundle_id:
                self._state.project.bundles[idx] = copy.deepcopy(self._old_bundle)
                break
        self._state.project.cables = copy.deepcopy(self._prev_cables)
        self._state.notify_bundles_changed()
        self._state.notify_cables_changed()


class DeleteBundleCommand(QUndoCommand):
    """Unbundle and remove a CableBundle."""

    def __init__(self, state: ProjectState, bundle_id: str) -> None:
        super().__init__("Unbundle Cables")
        self._state = state
        self._bundle_id = bundle_id
        self._old_bundle = next(
            (copy.deepcopy(b) for b in state.project.bundles if b.id == bundle_id),
            None,
        )
        self._prev_cables = copy.deepcopy(state.project.cables)

    def redo(self) -> None:
        self._state.project.bundles = [
            b for b in self._state.project.bundles if b.id != self._bundle_id
        ]
        for cab in self._state.project.cables:
            if cab.logical_bundle_id == self._bundle_id:
                cab.logical_bundle_id = None
            if cab.physical_bundle_id == self._bundle_id:
                cab.physical_bundle_id = None
        self._state.notify_bundles_changed()
        self._state.notify_cables_changed()

    def undo(self) -> None:
        if self._old_bundle is not None and all(
            b.id != self._old_bundle.id for b in self._state.project.bundles
        ):
            self._state.project.bundles.append(copy.deepcopy(self._old_bundle))
        self._state.project.cables = copy.deepcopy(self._prev_cables)
        self._state.notify_bundles_changed()
        self._state.notify_cables_changed()


class AddCustomTemplateCommand(QUndoCommand):
    """Add or update a custom ComponentTemplate in the project."""

    def __init__(self, state: ProjectState, template: ComponentTemplate) -> None:
        super().__init__(f"Save Template {template.template_name}")
        self._state = state
        self._template = copy.deepcopy(template)
        self._prev_templates = copy.deepcopy(state.project.custom_templates)

    def redo(self) -> None:
        self._state.project.custom_templates = [
            t for t in self._state.project.custom_templates if t.id != self._template.id
        ]
        self._state.project.custom_templates.append(copy.deepcopy(self._template))
        self._state.templates_changed.emit()

    def undo(self) -> None:
        self._state.project.custom_templates = copy.deepcopy(self._prev_templates)
        self._state.templates_changed.emit()


class RemoveCustomTemplateCommand(QUndoCommand):
    """Remove a custom ComponentTemplate from the project."""

    def __init__(self, state: ProjectState, template_id: str) -> None:
        super().__init__("Delete Template")
        self._state = state
        self._template_id = template_id
        self._prev_templates = copy.deepcopy(state.project.custom_templates)

    def redo(self) -> None:
        self._state.project.custom_templates = [
            t for t in self._state.project.custom_templates if t.id != self._template_id
        ]
        self._state.templates_changed.emit()

    def undo(self) -> None:
        self._state.project.custom_templates = copy.deepcopy(self._prev_templates)
        self._state.templates_changed.emit()


class AddBuildingCommand(QUndoCommand):
    """Add a Building and sync its footprint_latlon from floor0 + geo_anchor."""

    def __init__(self, state: ProjectState, building: Building) -> None:
        super().__init__(f"Add Building {building.name}")
        self._state = state
        self._building = copy.deepcopy(building)
        sync_building_footprint(self._building)

    def redo(self) -> None:
        if self._state.project.get_building(self._building.id) is None:
            b = copy.deepcopy(self._building)
            sync_building_footprint(b)
            self._state.project.buildings.append(b)
        self._state.notify_buildings_changed()

    def undo(self) -> None:
        self._state.project.buildings = [
            b for b in self._state.project.buildings if b.id != self._building.id
        ]
        self._state.notify_buildings_changed()


class RemoveBuildingCommand(QUndoCommand):
    """Remove a Building and unplace components situated inside it."""

    def __init__(self, state: ProjectState, building_id: str) -> None:
        super().__init__("Remove Building")
        self._state = state
        self._building_id = building_id
        self._removed_building = copy.deepcopy(state.project.get_building(building_id))
        self._prev_components = copy.deepcopy(state.project.components)

    def redo(self) -> None:
        self._state.project.buildings = [
            b for b in self._state.project.buildings if b.id != self._building_id
        ]
        changed_comps = False
        for comp in self._state.project.components:
            if comp.physical_building_id == self._building_id:
                comp.physical_building_id = None
                comp.physical_floor_id = None
                comp.physical_pos_m = None
                changed_comps = True
        self._state.notify_buildings_changed()
        if changed_comps:
            self._state.notify_components_changed()

    def undo(self) -> None:
        if self._removed_building is not None and self._state.project.get_building(
            self._building_id
        ) is None:
            self._state.project.buildings.append(copy.deepcopy(self._removed_building))
        self._state.project.components = copy.deepcopy(self._prev_components)
        self._state.notify_buildings_changed()
        self._state.notify_components_changed()


class SetBuildingAnchorCommand(QUndoCommand):
    """Update Building.geo_anchor and deterministically recompute footprint_latlon."""

    def __init__(
        self,
        state: ProjectState,
        building_id: str,
        new_anchor: GeoAnchor | None,
        text: str = "Update Building Geo Anchor",
    ) -> None:
        super().__init__(text)
        self._state = state
        self._building_id = building_id
        bldg = state.project.get_building(building_id)
        if bldg is None:
            raise KeyError(f"Unknown building {building_id}")
        self._old_anchor = copy.deepcopy(bldg.geo_anchor)
        self._old_footprint = list(bldg.footprint_latlon)
        self._new_anchor = copy.deepcopy(new_anchor)

    def redo(self) -> None:
        bldg = self._state.project.get_building(self._building_id)
        if bldg is not None:
            bldg.geo_anchor = copy.deepcopy(self._new_anchor)
            sync_building_footprint(bldg)
        self._state.notify_buildings_changed()

    def undo(self) -> None:
        bldg = self._state.project.get_building(self._building_id)
        if bldg is not None:
            bldg.geo_anchor = copy.deepcopy(self._old_anchor)
            if bldg.geo_anchor is not None and bldg.floors and bldg.floors[0].outer_polygon_m:
                sync_building_footprint(bldg)
            else:
                bldg.footprint_latlon = list(self._old_footprint)
        self._state.notify_buildings_changed()


class UpdateBuildingCommand(QUndoCommand):
    """Update Building properties (name, floor_height_m, geo_anchor) with footprint sync."""

    def __init__(
        self,
        state: ProjectState,
        building_id: str,
        *,
        name: str | None = None,
        floor_height_m: float | None = None,
        geo_anchor: GeoAnchor | None = None,
        update_anchor: bool = False,
    ) -> None:
        super().__init__("Update Building")
        self._state = state
        self._building_id = building_id
        bldg = state.project.get_building(building_id)
        if bldg is None:
            raise KeyError(f"Unknown building {building_id}")
        self._old_name = bldg.name
        self._old_height = bldg.floor_height_m
        self._old_anchor = copy.deepcopy(bldg.geo_anchor)
        self._new_name = name if name is not None else bldg.name
        self._new_height = (
            float(floor_height_m) if floor_height_m is not None else bldg.floor_height_m
        )
        self._update_anchor = update_anchor
        self._new_anchor = copy.deepcopy(geo_anchor)

    def redo(self) -> None:
        bldg = self._state.project.get_building(self._building_id)
        if bldg is not None:
            bldg.name = self._new_name
            bldg.floor_height_m = self._new_height
            if self._update_anchor:
                bldg.geo_anchor = copy.deepcopy(self._new_anchor)
                sync_building_footprint(bldg)
        self._state.notify_buildings_changed()

    def undo(self) -> None:
        bldg = self._state.project.get_building(self._building_id)
        if bldg is not None:
            bldg.name = self._old_name
            bldg.floor_height_m = self._old_height
            if self._update_anchor:
                bldg.geo_anchor = copy.deepcopy(self._old_anchor)
                sync_building_footprint(bldg)
        self._state.notify_buildings_changed()


class AddFloorCommand(QUndoCommand):
    """Add a Floor to a Building (recomputes footprint if it becomes floor0)."""

    def __init__(self, state: ProjectState, building_id: str, floor: Floor) -> None:
        super().__init__(f"Add Floor {floor.name}")
        self._state = state
        self._building_id = building_id
        self._floor = copy.deepcopy(floor)

    def redo(self) -> None:
        bldg = self._state.project.get_building(self._building_id)
        if bldg is not None:
            if all(f.id != self._floor.id for f in bldg.floors):
                bldg.floors.append(copy.deepcopy(self._floor))
            sync_building_footprint(bldg)
        self._state.notify_buildings_changed()
        self._state.notify_floor_geometry_changed(self._building_id, self._floor.id)

    def undo(self) -> None:
        bldg = self._state.project.get_building(self._building_id)
        if bldg is not None:
            bldg.floors = [f for f in bldg.floors if f.id != self._floor.id]
            sync_building_footprint(bldg)
        self._state.notify_buildings_changed()


class UpdateFloorCommand(QUndoCommand):
    """Update a Floor's metadata (name, level_index)."""

    def __init__(
        self,
        state: ProjectState,
        building_id: str,
        floor_id: str,
        *,
        name: str | None = None,
        level_index: int | None = None,
    ) -> None:
        super().__init__("Update Floor")
        self._state = state
        self._building_id = building_id
        self._floor_id = floor_id
        floor = state.project.get_floor(building_id, floor_id)
        if floor is None:
            raise KeyError(f"Unknown floor {building_id}/{floor_id}")
        self._old_name = floor.name
        self._old_level = floor.level_index
        self._new_name = name if name is not None else floor.name
        self._new_level = int(level_index) if level_index is not None else floor.level_index

    def redo(self) -> None:
        floor = self._state.project.get_floor(self._building_id, self._floor_id)
        if floor is not None:
            floor.name = self._new_name
            floor.level_index = self._new_level
        self._state.notify_buildings_changed()
        self._state.notify_floor_geometry_changed(self._building_id, self._floor_id)

    def undo(self) -> None:
        floor = self._state.project.get_floor(self._building_id, self._floor_id)
        if floor is not None:
            floor.name = self._old_name
            floor.level_index = self._old_level
        self._state.notify_buildings_changed()
        self._state.notify_floor_geometry_changed(self._building_id, self._floor_id)


class SetFloorViewRotationCommand(QUndoCommand):
    """Update Floor.editor_view_rotation_deg ONLY (NEVER mutates GeoAnchor)."""

    def __init__(
        self,
        state: ProjectState,
        building_id: str,
        floor_id: str,
        new_rotation_deg: float,
    ) -> None:
        super().__init__("Rotate Floor View")
        self._state = state
        self._building_id = building_id
        self._floor_id = floor_id
        floor = state.project.get_floor(building_id, floor_id)
        if floor is None:
            raise KeyError(f"Unknown floor {building_id}/{floor_id}")
        self._old_rotation = floor.editor_view_rotation_deg
        self._new_rotation = float(new_rotation_deg)

    def redo(self) -> None:
        floor = self._state.project.get_floor(self._building_id, self._floor_id)
        if floor is not None:
            floor.editor_view_rotation_deg = self._new_rotation
            self._state.floor_view_rotated.emit(
                self._building_id, self._floor_id, self._new_rotation
            )

    def undo(self) -> None:
        floor = self._state.project.get_floor(self._building_id, self._floor_id)
        if floor is not None:
            floor.editor_view_rotation_deg = self._old_rotation
            self._state.floor_view_rotated.emit(
                self._building_id, self._floor_id, self._old_rotation
            )


class EditFloorOutlineCommand(QUndoCommand):
    """Edit Floor.outer_polygon_m (and optional reconciled doors), syncing footprint if floor0."""

    def __init__(
        self,
        state: ProjectState,
        building_id: str,
        floor_id: str,
        new_outer_polygon_m: Sequence[Point2D],
        new_doors: Sequence[Door] | None = None,
    ) -> None:
        super().__init__("Edit Floor Outline")
        self._state = state
        self._building_id = building_id
        self._floor_id = floor_id
        floor = state.project.get_floor(building_id, floor_id)
        if floor is None:
            raise KeyError(f"Unknown floor {building_id}/{floor_id}")
        self._old_polygon = copy.deepcopy(floor.outer_polygon_m)
        self._new_polygon = [copy.deepcopy(pt) for pt in new_outer_polygon_m]
        self._old_doors = copy.deepcopy(floor.doors)
        self._new_doors = [copy.deepcopy(d) for d in new_doors] if new_doors is not None else None

    def _apply(self, polygon: list[Point2D], doors: list[Door] | None) -> None:
        bldg = self._state.project.get_building(self._building_id)
        floor = self._state.project.get_floor(self._building_id, self._floor_id)
        if bldg is None or floor is None:
            return
        floor.outer_polygon_m = copy.deepcopy(polygon)
        if doors is not None:
            floor.doors = copy.deepcopy(doors)
        is_floor0 = bool(bldg.floors and bldg.floors[0].id == self._floor_id)
        if is_floor0:
            sync_building_footprint(bldg)
            self._state.notify_buildings_changed()
        self._state.notify_floor_geometry_changed(self._building_id, self._floor_id)

    def redo(self) -> None:
        self._apply(self._new_polygon, self._new_doors)

    def undo(self) -> None:
        self._apply(self._old_polygon, self._old_doors)


class AddRoomCommand(QUndoCommand):
    """Add a Room to a Floor (optionally updating doors after wall cut/reconcile)."""

    def __init__(
        self,
        state: ProjectState,
        building_id: str,
        floor_id: str,
        room: Room,
        new_doors: Sequence[Door] | None = None,
    ) -> None:
        super().__init__(f"Add Room {room.name}")
        self._state = state
        self._building_id = building_id
        self._floor_id = floor_id
        self._room = copy.deepcopy(room)
        floor = state.project.get_floor(building_id, floor_id)
        self._old_doors = copy.deepcopy(floor.doors) if floor is not None else []
        self._new_doors = [copy.deepcopy(d) for d in new_doors] if new_doors is not None else None

    def redo(self) -> None:
        floor = self._state.project.get_floor(self._building_id, self._floor_id)
        if floor is not None:
            if all(r.id != self._room.id for r in floor.rooms):
                floor.rooms.append(copy.deepcopy(self._room))
            if self._new_doors is not None:
                floor.doors = copy.deepcopy(self._new_doors)
        self._state.notify_floor_geometry_changed(self._building_id, self._floor_id)

    def undo(self) -> None:
        floor = self._state.project.get_floor(self._building_id, self._floor_id)
        if floor is not None:
            floor.rooms = [r for r in floor.rooms if r.id != self._room.id]
            if self._new_doors is not None:
                floor.doors = copy.deepcopy(self._old_doors)
        self._state.notify_floor_geometry_changed(self._building_id, self._floor_id)


class UpdateRoomCommand(QUndoCommand):
    """Update a Room (geometry, name, color, attenuation) and optional reconciled doors."""

    def __init__(
        self,
        state: ProjectState,
        building_id: str,
        floor_id: str,
        room_id: str,
        new_room: Room,
        new_doors: Sequence[Door] | None = None,
        text: str = "Edit Room",
    ) -> None:
        super().__init__(text)
        self._state = state
        self._building_id = building_id
        self._floor_id = floor_id
        self._room_id = room_id
        floor = state.project.get_floor(building_id, floor_id)
        if floor is None:
            raise KeyError(f"Unknown floor {building_id}/{floor_id}")
        old_room = next((r for r in floor.rooms if r.id == room_id), None)
        if old_room is None:
            raise KeyError(f"Unknown room {room_id}")
        self._old_room = copy.deepcopy(old_room)
        self._new_room = copy.deepcopy(new_room)
        self._old_doors = copy.deepcopy(floor.doors)
        self._new_doors = [copy.deepcopy(d) for d in new_doors] if new_doors is not None else None

    def redo(self) -> None:
        floor = self._state.project.get_floor(self._building_id, self._floor_id)
        if floor is not None:
            for idx, r in enumerate(floor.rooms):
                if r.id == self._room_id:
                    floor.rooms[idx] = copy.deepcopy(self._new_room)
                    break
            if self._new_doors is not None:
                floor.doors = copy.deepcopy(self._new_doors)
        self._state.notify_floor_geometry_changed(self._building_id, self._floor_id)

    def undo(self) -> None:
        floor = self._state.project.get_floor(self._building_id, self._floor_id)
        if floor is not None:
            for idx, r in enumerate(floor.rooms):
                if r.id == self._room_id:
                    floor.rooms[idx] = copy.deepcopy(self._old_room)
                    break
            if self._new_doors is not None:
                floor.doors = copy.deepcopy(self._old_doors)
        self._state.notify_floor_geometry_changed(self._building_id, self._floor_id)


class DeleteRoomCommand(QUndoCommand):
    """Delete a Room from a Floor (optionally updating doors)."""

    def __init__(
        self,
        state: ProjectState,
        building_id: str,
        floor_id: str,
        room_id: str,
        new_doors: Sequence[Door] | None = None,
    ) -> None:
        super().__init__("Delete Room")
        self._state = state
        self._building_id = building_id
        self._floor_id = floor_id
        self._room_id = room_id
        floor = state.project.get_floor(building_id, floor_id)
        if floor is None:
            raise KeyError(f"Unknown floor {building_id}/{floor_id}")
        self._old_room = next((copy.deepcopy(r) for r in floor.rooms if r.id == room_id), None)
        self._old_doors = copy.deepcopy(floor.doors)
        self._new_doors = [copy.deepcopy(d) for d in new_doors] if new_doors is not None else None

    def redo(self) -> None:
        floor = self._state.project.get_floor(self._building_id, self._floor_id)
        if floor is not None:
            floor.rooms = [r for r in floor.rooms if r.id != self._room_id]
            if self._new_doors is not None:
                floor.doors = copy.deepcopy(self._new_doors)
        self._state.notify_floor_geometry_changed(self._building_id, self._floor_id)

    def undo(self) -> None:
        floor = self._state.project.get_floor(self._building_id, self._floor_id)
        if floor is not None:
            if self._old_room is not None and all(r.id != self._room_id for r in floor.rooms):
                floor.rooms.append(copy.deepcopy(self._old_room))
            if self._new_doors is not None:
                floor.doors = copy.deepcopy(self._old_doors)
        self._state.notify_floor_geometry_changed(self._building_id, self._floor_id)


class AddDoorCommand(QUndoCommand):
    """Add a wall-locked Door to a Floor."""

    def __init__(self, state: ProjectState, building_id: str, floor_id: str, door: Door) -> None:
        super().__init__("Place Door")
        self._state = state
        self._building_id = building_id
        self._floor_id = floor_id
        self._door = copy.deepcopy(door)

    def redo(self) -> None:
        floor = self._state.project.get_floor(self._building_id, self._floor_id)
        if floor is not None and all(d.id != self._door.id for d in floor.doors):
            floor.doors.append(copy.deepcopy(self._door))
        self._state.notify_floor_geometry_changed(self._building_id, self._floor_id)

    def undo(self) -> None:
        floor = self._state.project.get_floor(self._building_id, self._floor_id)
        if floor is not None:
            floor.doors = [d for d in floor.doors if d.id != self._door.id]
        self._state.notify_floor_geometry_changed(self._building_id, self._floor_id)


class UpdateDoorCommand(QUndoCommand):
    """Move, flip, resize, or update a wall-locked Door."""

    def __init__(
        self,
        state: ProjectState,
        building_id: str,
        floor_id: str,
        door_id: str,
        new_door: Door,
        text: str = "Update Door",
    ) -> None:
        super().__init__(text)
        self._state = state
        self._building_id = building_id
        self._floor_id = floor_id
        self._door_id = door_id
        floor = state.project.get_floor(building_id, floor_id)
        if floor is None:
            raise KeyError(f"Unknown floor {building_id}/{floor_id}")
        old_door = next((d for d in floor.doors if d.id == door_id), None)
        if old_door is None:
            raise KeyError(f"Unknown door {door_id}")
        self._old_door = copy.deepcopy(old_door)
        self._new_door = copy.deepcopy(new_door)

    def redo(self) -> None:
        floor = self._state.project.get_floor(self._building_id, self._floor_id)
        if floor is not None:
            for idx, d in enumerate(floor.doors):
                if d.id == self._door_id:
                    floor.doors[idx] = copy.deepcopy(self._new_door)
                    break
        self._state.notify_floor_geometry_changed(self._building_id, self._floor_id)

    def undo(self) -> None:
        floor = self._state.project.get_floor(self._building_id, self._floor_id)
        if floor is not None:
            for idx, d in enumerate(floor.doors):
                if d.id == self._door_id:
                    floor.doors[idx] = copy.deepcopy(self._old_door)
                    break
        self._state.notify_floor_geometry_changed(self._building_id, self._floor_id)


class DeleteDoorCommand(QUndoCommand):
    """Delete a Door from a Floor."""

    def __init__(self, state: ProjectState, building_id: str, floor_id: str, door_id: str) -> None:
        super().__init__("Delete Door")
        self._state = state
        self._building_id = building_id
        self._floor_id = floor_id
        self._door_id = door_id
        floor = state.project.get_floor(building_id, floor_id)
        self._old_door = (
            next((copy.deepcopy(d) for d in floor.doors if d.id == door_id), None)
            if floor is not None
            else None
        )

    def redo(self) -> None:
        floor = self._state.project.get_floor(self._building_id, self._floor_id)
        if floor is not None:
            floor.doors = [d for d in floor.doors if d.id != self._door_id]
        self._state.notify_floor_geometry_changed(self._building_id, self._floor_id)

    def undo(self) -> None:
        floor = self._state.project.get_floor(self._building_id, self._floor_id)
        if floor is not None and self._old_door is not None:
            if all(d.id != self._door_id for d in floor.doors):
                floor.doors.append(copy.deepcopy(self._old_door))
        self._state.notify_floor_geometry_changed(self._building_id, self._floor_id)


class AddStairsCommand(QUndoCommand):
    """Add Stairs to a Floor."""

    def __init__(
        self, state: ProjectState, building_id: str, floor_id: str, stairs: Stairs
    ) -> None:
        super().__init__("Add Stairs")
        self._state = state
        self._building_id = building_id
        self._floor_id = floor_id
        self._stairs = copy.deepcopy(stairs)

    def redo(self) -> None:
        floor = self._state.project.get_floor(self._building_id, self._floor_id)
        if floor is not None and all(s.id != self._stairs.id for s in floor.stairs):
            floor.stairs.append(copy.deepcopy(self._stairs))
        self._state.notify_floor_geometry_changed(self._building_id, self._floor_id)

    def undo(self) -> None:
        floor = self._state.project.get_floor(self._building_id, self._floor_id)
        if floor is not None:
            floor.stairs = [s for s in floor.stairs if s.id != self._stairs.id]
        self._state.notify_floor_geometry_changed(self._building_id, self._floor_id)


class DeleteStairsCommand(QUndoCommand):
    """Delete Stairs from a Floor."""

    def __init__(
        self, state: ProjectState, building_id: str, floor_id: str, stairs_id: str
    ) -> None:
        super().__init__("Delete Stairs")
        self._state = state
        self._building_id = building_id
        self._floor_id = floor_id
        self._stairs_id = stairs_id
        floor = state.project.get_floor(building_id, floor_id)
        self._old_stairs = (
            next((copy.deepcopy(s) for s in floor.stairs if s.id == stairs_id), None)
            if floor is not None
            else None
        )

    def redo(self) -> None:
        floor = self._state.project.get_floor(self._building_id, self._floor_id)
        if floor is not None:
            floor.stairs = [s for s in floor.stairs if s.id != self._stairs_id]
        self._state.notify_floor_geometry_changed(self._building_id, self._floor_id)

    def undo(self) -> None:
        floor = self._state.project.get_floor(self._building_id, self._floor_id)
        if floor is not None and self._old_stairs is not None:
            if all(s.id != self._stairs_id for s in floor.stairs):
                floor.stairs.append(copy.deepcopy(self._old_stairs))
        self._state.notify_floor_geometry_changed(self._building_id, self._floor_id)


class AddHoleCommand(QUndoCommand):
    """Add a VerticalHole riser to a Floor."""

    def __init__(
        self, state: ProjectState, building_id: str, floor_id: str, hole: VerticalHole
    ) -> None:
        super().__init__("Add Vertical Riser Hole")
        self._state = state
        self._building_id = building_id
        self._floor_id = floor_id
        self._hole = copy.deepcopy(hole)

    def redo(self) -> None:
        floor = self._state.project.get_floor(self._building_id, self._floor_id)
        if floor is not None and all(h.id != self._hole.id for h in floor.holes):
            floor.holes.append(copy.deepcopy(self._hole))
        self._state.notify_floor_geometry_changed(self._building_id, self._floor_id)

    def undo(self) -> None:
        floor = self._state.project.get_floor(self._building_id, self._floor_id)
        if floor is not None:
            floor.holes = [h for h in floor.holes if h.id != self._hole.id]
        self._state.notify_floor_geometry_changed(self._building_id, self._floor_id)


class DeleteHoleCommand(QUndoCommand):
    """Delete a VerticalHole riser from a Floor."""

    def __init__(self, state: ProjectState, building_id: str, floor_id: str, hole_id: str) -> None:
        super().__init__("Delete Vertical Riser Hole")
        self._state = state
        self._building_id = building_id
        self._floor_id = floor_id
        self._hole_id = hole_id
        floor = state.project.get_floor(building_id, floor_id)
        self._old_hole = (
            next((copy.deepcopy(h) for h in floor.holes if h.id == hole_id), None)
            if floor is not None
            else None
        )

    def redo(self) -> None:
        floor = self._state.project.get_floor(self._building_id, self._floor_id)
        if floor is not None:
            floor.holes = [h for h in floor.holes if h.id != self._hole_id]
        self._state.notify_floor_geometry_changed(self._building_id, self._floor_id)

    def undo(self) -> None:
        floor = self._state.project.get_floor(self._building_id, self._floor_id)
        if floor is not None and self._old_hole is not None:
            if all(h.id != self._hole_id for h in floor.holes):
                floor.holes.append(copy.deepcopy(self._old_hole))
        self._state.notify_floor_geometry_changed(self._building_id, self._floor_id)


class SetBlueprintUnderlayCommand(QUndoCommand):
    """Set, update, or clear a floor's BlueprintUnderlay and associated PNG/JPG bytes."""

    def __init__(
        self,
        state: ProjectState,
        building_id: str,
        floor_id: str,
        blueprint: BlueprintUnderlay | None,
        image_bytes: bytes | None = None,
    ) -> None:
        super().__init__("Update Blueprint Underlay")
        self._state = state
        self._building_id = building_id
        self._floor_id = floor_id
        floor = state.project.get_floor(building_id, floor_id)
        if floor is None:
            raise KeyError(f"Unknown floor {building_id}/{floor_id}")
        self._old_blueprint = copy.deepcopy(floor.blueprint)
        self._old_bytes = (
            state.blueprint_bytes.get(self._old_blueprint.archive_path)
            if self._old_blueprint is not None
            else None
        )
        self._new_blueprint = copy.deepcopy(blueprint)
        self._new_bytes = image_bytes

    def redo(self) -> None:
        floor = self._state.project.get_floor(self._building_id, self._floor_id)
        if floor is not None:
            if self._old_blueprint is not None and (
                self._new_blueprint is None
                or self._new_blueprint.archive_path != self._old_blueprint.archive_path
            ):
                self._state.blueprint_bytes.pop(self._old_blueprint.archive_path, None)
            floor.blueprint = copy.deepcopy(self._new_blueprint)
            if self._new_blueprint is not None and self._new_bytes is not None:
                self._state.blueprint_bytes[self._new_blueprint.archive_path] = self._new_bytes
        self._state.notify_floor_geometry_changed(self._building_id, self._floor_id)

    def undo(self) -> None:
        floor = self._state.project.get_floor(self._building_id, self._floor_id)
        if floor is not None:
            if self._new_blueprint is not None and (
                self._old_blueprint is None
                or self._new_blueprint.archive_path != self._old_blueprint.archive_path
            ):
                self._state.blueprint_bytes.pop(self._new_blueprint.archive_path, None)
            floor.blueprint = copy.deepcopy(self._old_blueprint)
            if self._old_blueprint is not None and self._old_bytes is not None:
                self._state.blueprint_bytes[self._old_blueprint.archive_path] = self._old_bytes
        self._state.notify_floor_geometry_changed(self._building_id, self._floor_id)


class PlacePhysicalComponentCommand(QUndoCommand):
    """Place, move, or unplace a Component on a building floor plan."""

    def __init__(
        self,
        state: ProjectState,
        component_id: str,
        building_id: str | None,
        floor_id: str | None,
        pos_m: Point2D | None,
    ) -> None:
        super().__init__("Place Component on Floor" if pos_m is not None else "Unplace Component")
        self._state = state
        self._component_id = component_id
        comp = state.project.get_component(component_id)
        if comp is None:
            raise KeyError(f"Unknown component {component_id}")
        self._old_building_id = comp.physical_building_id
        self._old_floor_id = comp.physical_floor_id
        self._old_pos_m = copy.deepcopy(comp.physical_pos_m)
        self._new_building_id = building_id
        self._new_floor_id = floor_id
        self._new_pos_m = copy.deepcopy(pos_m)

    def redo(self) -> None:
        comp = self._state.project.get_component(self._component_id)
        if comp is not None:
            comp.physical_building_id = self._new_building_id
            comp.physical_floor_id = self._new_floor_id
            comp.physical_pos_m = copy.deepcopy(self._new_pos_m)
        self._state.notify_components_changed()

    def undo(self) -> None:
        comp = self._state.project.get_component(self._component_id)
        if comp is not None:
            comp.physical_building_id = self._old_building_id
            comp.physical_floor_id = self._old_floor_id
            comp.physical_pos_m = copy.deepcopy(self._old_pos_m)
        self._state.notify_components_changed()


class ImportSurveySessionCommand(QUndoCommand):
    """Import a new SurveySession or merge samples into an existing session with matching id."""

    def __init__(self, state: ProjectState, session: SurveySession) -> None:
        super().__init__(f"Import Survey '{session.name}'")
        self._state = state
        self._session = copy.deepcopy(session)
        self._prev_surveys = copy.deepcopy(state.project.surveys)

    def redo(self) -> None:
        existing = next(
            (s for s in self._state.project.surveys if s.id == self._session.id),
            None,
        )
        if existing is None:
            self._state.project.surveys.append(copy.deepcopy(self._session))
        else:
            existing_sample_ids = {smp.id for smp in existing.samples}
            for smp in self._session.samples:
                if smp.id not in existing_sample_ids:
                    existing.samples.append(copy.deepcopy(smp))
        self._state.notify_surveys_changed()

    def undo(self) -> None:
        self._state.project.surveys = copy.deepcopy(self._prev_surveys)
        self._state.notify_surveys_changed()


class DeleteSurveySamplesCommand(QUndoCommand):
    """Delete one or more outlier SurveySamples across project survey sessions."""

    def __init__(self, state: ProjectState, sample_ids: Sequence[str]) -> None:
        super().__init__("Delete Survey Sample(s)")
        self._state = state
        self._sample_ids = set(sample_ids)
        self._prev_surveys = copy.deepcopy(state.project.surveys)

    def redo(self) -> None:
        for session in self._state.project.surveys:
            session.samples = [s for s in session.samples if s.id not in self._sample_ids]
        self._state.notify_surveys_changed()

    def undo(self) -> None:
        self._state.project.surveys = copy.deepcopy(self._prev_surveys)
        self._state.notify_surveys_changed()


class DeleteSurveySessionCommand(QUndoCommand):
    """Remove an entire SurveySession."""

    def __init__(self, state: ProjectState, session_id: str) -> None:
        super().__init__("Delete Survey Session")
        self._state = state
        self._session_id = session_id
        self._prev_surveys = copy.deepcopy(state.project.surveys)

    def redo(self) -> None:
        self._state.project.surveys = [
            s for s in self._state.project.surveys if s.id != self._session_id
        ]
        self._state.notify_surveys_changed()

    def undo(self) -> None:
        self._state.project.surveys = copy.deepcopy(self._prev_surveys)
        self._state.notify_surveys_changed()


# ---------------------------------------------------------------------------
# ProjectState
# ---------------------------------------------------------------------------


class ProjectState(QObject):
    """Central reactive application state wrapping Project, QUndoStack, tiles DB, and autosave."""

    project_reloaded = Signal()
    dirty_changed = Signal(bool)
    components_changed = Signal()
    cables_changed = Signal()
    bundles_changed = Signal()
    templates_changed = Signal()
    buildings_changed = Signal()
    floor_geometry_changed = Signal(str, str)  # (building_id, floor_id)
    floor_view_rotated = Signal(str, str, float)  # (building_id, floor_id, view_rot_deg)
    surveys_changed = Signal()
    selection_changed = Signal(str, str)  # (entity_type, entity_id)
    validation_updated = Signal(list)  # list[ValidationIssue]
    status_message = Signal(str, int)
    map_preferences_changed = Signal()
    autosave_completed = Signal(str)

    def __init__(
        self,
        project: Project | None = None,
        *,
        autosave_enabled: bool = True,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.project: Project = project if project is not None else Project()
        self.undo_stack = QUndoStack(self)
        self.project_path: Path | None = None
        self.tiles_db_path: Path | None = None
        self.blueprint_bytes: dict[str, bytes] = {}
        self.selected_component_id: str | None = None
        self.selected_cable_id: str | None = None
        self.selected_building_id: str | None = None
        self.selected_floor_id: str | None = None
        self._dirty: bool = False
        self._temp_dir = tempfile.TemporaryDirectory(
            prefix="netviz_state_",
            ignore_cleanup_errors=True,
        )

        self.undo_stack.cleanChanged.connect(self._on_clean_changed)

        self.autosave_timer = QTimer(self)
        self.autosave_timer.setInterval(AUTOSAVE_INTERVAL_MS)
        self.autosave_timer.timeout.connect(self.autosave_now)
        if autosave_enabled:
            self.autosave_timer.start()

    @property
    def dirty(self) -> bool:
        return self._dirty

    def _on_clean_changed(self, clean: bool) -> None:
        new_dirty = not clean
        if new_dirty != self._dirty:
            self._dirty = new_dirty
            self.dirty_changed.emit(self._dirty)

    def mark_dirty(self) -> None:
        if not self._dirty:
            self._dirty = True
            self.dirty_changed.emit(True)

    def ensure_tiles_db_path(self) -> Path:
        """Return a valid SQLite tiles.sqlite path, creating a workspace file if needed."""
        if self.tiles_db_path is None:
            self.tiles_db_path = Path(self._temp_dir.name) / "tiles.sqlite"
        return ensure_tiles_db(self.tiles_db_path)

    def cleanup(self) -> None:
        """Stop autosave timer and clean temporary working directory."""
        self.autosave_timer.stop()
        gc.collect()
        self._temp_dir.cleanup()

    def revalidate(self) -> list[ValidationIssue]:
        issues = validate_project(self.project)
        self.validation_updated.emit(issues)
        return issues

    def notify_components_changed(self) -> None:
        self.components_changed.emit()
        self.revalidate()

    def notify_cables_changed(self) -> None:
        self.cables_changed.emit()
        self.revalidate()

    def notify_bundles_changed(self) -> None:
        self.bundles_changed.emit()

    def notify_buildings_changed(self) -> None:
        self.buildings_changed.emit()
        self.revalidate()

    def notify_floor_geometry_changed(self, building_id: str, floor_id: str) -> None:
        self.floor_geometry_changed.emit(building_id, floor_id)
        self.revalidate()

    def notify_surveys_changed(self) -> None:
        self.surveys_changed.emit()

    def set_selection(self, entity_type: str, entity_id: str) -> None:
        if entity_type == "component":
            self.selected_component_id = entity_id or None
            self.selected_cable_id = None
        elif entity_type == "cable":
            self.selected_cable_id = entity_id or None
            self.selected_component_id = None
        elif entity_type == "building":
            self.selected_building_id = entity_id or None
        elif entity_type == "floor":
            self.selected_floor_id = entity_id or None
        self.selection_changed.emit(entity_type, entity_id)

    def load_project(
        self,
        project: Project,
        *,
        blueprints: dict[str, bytes] | None = None,
        tiles_db_path: Path | None = None,
        project_path: Path | None = None,
    ) -> None:
        """Replace active project, reset undo stack, sync building footprints, and emit reload."""
        self.project = project
        for bldg in self.project.buildings:
            if bldg.geo_anchor is not None:
                sync_building_footprint(bldg)
        self.blueprint_bytes = dict(blueprints or {})
        self.tiles_db_path = tiles_db_path
        self.project_path = project_path
        self.undo_stack.clear()
        self.undo_stack.setClean()
        self._dirty = False
        self.dirty_changed.emit(False)
        if self.project.buildings:
            self.selected_building_id = self.project.buildings[0].id
            if self.project.buildings[0].floors:
                self.selected_floor_id = self.project.buildings[0].floors[0].id
            else:
                self.selected_floor_id = None
        else:
            self.selected_building_id = None
            self.selected_floor_id = None
        self.selected_component_id = None
        self.selected_cable_id = None
        self.project_reloaded.emit()
        self.revalidate()

    def new_project(self, name: str = "Untitled Network") -> None:
        self.load_project(Project(name=name))

    def load_from_file(self, path: str | Path) -> NetvizBundle:
        src = Path(path)
        extracted_tiles = Path(self._temp_dir.name) / "tiles.sqlite"
        extracted_tiles.unlink(missing_ok=True)
        bundle = load_netviz(src, extract_tiles_to=extracted_tiles)
        self.load_project(
            bundle.project,
            blueprints=bundle.blueprints,
            tiles_db_path=bundle.tiles_db_path,
            project_path=src,
        )
        return bundle

    def save_to_file(self, path: str | Path | None = None) -> Path:
        dest = Path(path) if path is not None else self.project_path
        if dest is None:
            raise ValueError("No project file path specified")
        tiles = self.tiles_db_path if self.tiles_db_path and self.tiles_db_path.is_file() else None
        saved = save_netviz(
            self.project,
            dest,
            tiles_db_path=tiles,
            blueprints=self.blueprint_bytes,
        )
        self.project_path = saved
        self.undo_stack.setClean()
        self._dirty = False
        self.dirty_changed.emit(False)
        return saved

    def autosave_now(self, target_path: str | Path | None = None) -> Path | None:
        """Write atomic recovery snapshot to autosave/recovery.netviz (or target_path)."""
        dest = Path(target_path) if target_path is not None else get_recovery_path()
        tiles = self.tiles_db_path if self.tiles_db_path and self.tiles_db_path.is_file() else None
        saved = save_netviz(
            self.project,
            dest,
            tiles_db_path=tiles,
            blueprints=self.blueprint_bytes,
        )
        self.autosave_completed.emit(str(saved))
        return saved

    # ------------------------------------------------------------------
    # High-level Undoable Mutation Helpers
    # ------------------------------------------------------------------

    def add_component(self, component: Component) -> Component:
        self.undo_stack.push(AddComponentCommand(self, component))
        return component

    def remove_components(self, component_ids: Sequence[str]) -> None:
        if component_ids:
            self.undo_stack.push(RemoveComponentsCommand(self, component_ids))

    def move_components(self, moves: dict[str, tuple[Point2D, Point2D]]) -> None:
        if moves:
            self.undo_stack.push(MoveComponentsCommand(self, moves))

    def update_component(
        self, component_id: str, new_component: Component, text: str = "Update Component"
    ) -> None:
        self.undo_stack.push(
            UpdateComponentCommand(self, component_id, new_component, text=text)
        )

    def connect_ports(
        self,
        source_component_id: str,
        source_port_id: str,
        target_component_id: str,
        target_port_id: str,
        *,
        label: str = "",
        vlan_id: str = "",
        style: CableStyle | None = None,
    ) -> Cable | None:
        if source_component_id == target_component_id and source_port_id == target_port_id:
            return None
        if self.project.is_port_occupied(source_component_id, source_port_id):
            return None
        if self.project.is_port_occupied(target_component_id, target_port_id):
            return None
        cable = Cable(
            source_component_id=source_component_id,
            source_port_id=source_port_id,
            target_component_id=target_component_id,
            target_port_id=target_port_id,
            label=label,
            vlan_id=vlan_id,
            style=style if style is not None else CableStyle(),
        )
        self.undo_stack.push(ConnectCableCommand(self, cable))
        return cable

    def disconnect_cable(self, cable_id: str) -> None:
        self.undo_stack.push(DisconnectCableCommand(self, cable_id))

    def update_cable(
        self, cable_id: str, new_cable: Cable, text: str = "Update Cable"
    ) -> None:
        self.undo_stack.push(UpdateCableCommand(self, cable_id, new_cable, text=text))

    def set_cable_waypoints(self, cable_id: str, waypoints: Sequence[Point2D]) -> None:
        self.undo_stack.push(SetCableWaypointsCommand(self, cable_id, waypoints))

    def create_bundle(self, bundle: CableBundle) -> CableBundle:
        self.undo_stack.push(CreateBundleCommand(self, bundle))
        return bundle

    def update_bundle(
        self, bundle_id: str, new_bundle: CableBundle, text: str = "Update Bundle"
    ) -> None:
        self.undo_stack.push(UpdateBundleCommand(self, bundle_id, new_bundle, text=text))

    def delete_bundle(self, bundle_id: str) -> None:
        self.undo_stack.push(DeleteBundleCommand(self, bundle_id))

    def save_custom_template(self, template: ComponentTemplate) -> None:
        self.undo_stack.push(AddCustomTemplateCommand(self, template))

    def remove_custom_template(self, template_id: str) -> None:
        self.undo_stack.push(RemoveCustomTemplateCommand(self, template_id))

    def add_building(self, building: Building) -> Building:
        sync_building_footprint(building)
        self.undo_stack.push(AddBuildingCommand(self, building))
        return building

    def remove_building(self, building_id: str) -> None:
        self.undo_stack.push(RemoveBuildingCommand(self, building_id))

    def set_building_anchor(
        self,
        building_id: str,
        anchor: GeoAnchor | None,
        text: str = "Move/Rotate Building on Map",
    ) -> None:
        self.undo_stack.push(SetBuildingAnchorCommand(self, building_id, anchor, text=text))

    def update_building(
        self,
        building_id: str,
        *,
        name: str | None = None,
        floor_height_m: float | None = None,
        geo_anchor: GeoAnchor | None = None,
        update_anchor: bool = False,
    ) -> None:
        self.undo_stack.push(
            UpdateBuildingCommand(
                self,
                building_id,
                name=name,
                floor_height_m=floor_height_m,
                geo_anchor=geo_anchor,
                update_anchor=update_anchor,
            )
        )

    def add_floor(self, building_id: str, floor: Floor) -> Floor:
        self.undo_stack.push(AddFloorCommand(self, building_id, floor))
        return floor

    def set_floor_view_rotation(
        self, building_id: str, floor_id: str, view_rotation_deg: float
    ) -> None:
        self.undo_stack.push(
            SetFloorViewRotationCommand(self, building_id, floor_id, view_rotation_deg)
        )

    def edit_floor_outline(
        self,
        building_id: str,
        floor_id: str,
        outer_polygon_m: Sequence[Point2D],
        reconciled_doors: Sequence[Door] | None = None,
    ) -> None:
        self.undo_stack.push(
            EditFloorOutlineCommand(
                self,
                building_id,
                floor_id,
                outer_polygon_m,
                new_doors=reconciled_doors,
            )
        )

    def add_room(
        self,
        building_id: str,
        floor_id: str,
        room: Room,
        reconciled_doors: Sequence[Door] | None = None,
    ) -> Room:
        self.undo_stack.push(
            AddRoomCommand(self, building_id, floor_id, room, new_doors=reconciled_doors)
        )
        return room

    def update_room(
        self,
        building_id: str,
        floor_id: str,
        room_id: str,
        new_room: Room,
        reconciled_doors: Sequence[Door] | None = None,
        text: str = "Edit Room",
    ) -> None:
        self.undo_stack.push(
            UpdateRoomCommand(
                self,
                building_id,
                floor_id,
                room_id,
                new_room,
                new_doors=reconciled_doors,
                text=text,
            )
        )

    def delete_room(
        self,
        building_id: str,
        floor_id: str,
        room_id: str,
        reconciled_doors: Sequence[Door] | None = None,
    ) -> None:
        self.undo_stack.push(
            DeleteRoomCommand(
                self, building_id, floor_id, room_id, new_doors=reconciled_doors
            )
        )

    def add_door(self, building_id: str, floor_id: str, door: Door) -> Door:
        self.undo_stack.push(AddDoorCommand(self, building_id, floor_id, door))
        return door

    def update_door(
        self,
        building_id: str,
        floor_id: str,
        door_id: str,
        new_door: Door,
        text: str = "Update Door",
    ) -> None:
        self.undo_stack.push(
            UpdateDoorCommand(self, building_id, floor_id, door_id, new_door, text=text)
        )

    def delete_door(self, building_id: str, floor_id: str, door_id: str) -> None:
        self.undo_stack.push(DeleteDoorCommand(self, building_id, floor_id, door_id))

    def add_stairs(self, building_id: str, floor_id: str, stairs: Stairs) -> Stairs:
        self.undo_stack.push(AddStairsCommand(self, building_id, floor_id, stairs))
        return stairs

    def delete_stairs(self, building_id: str, floor_id: str, stairs_id: str) -> None:
        self.undo_stack.push(DeleteStairsCommand(self, building_id, floor_id, stairs_id))

    def add_hole(self, building_id: str, floor_id: str, hole: VerticalHole) -> VerticalHole:
        self.undo_stack.push(AddHoleCommand(self, building_id, floor_id, hole))
        return hole

    def delete_hole(self, building_id: str, floor_id: str, hole_id: str) -> None:
        self.undo_stack.push(DeleteHoleCommand(self, building_id, floor_id, hole_id))

    def set_floor_blueprint(
        self,
        building_id: str,
        floor_id: str,
        blueprint: BlueprintUnderlay | None,
        image_bytes: bytes | None = None,
    ) -> None:
        self.undo_stack.push(
            SetBlueprintUnderlayCommand(self, building_id, floor_id, blueprint, image_bytes)
        )

    def place_component_physical(
        self,
        component_id: str,
        building_id: str | None,
        floor_id: str | None,
        pos_m: Point2D | None,
    ) -> None:
        self.undo_stack.push(
            PlacePhysicalComponentCommand(self, component_id, building_id, floor_id, pos_m)
        )

    def import_survey_session(self, session: SurveySession) -> None:
        self.undo_stack.push(ImportSurveySessionCommand(self, session))

    def delete_survey_samples(self, sample_ids: Sequence[str]) -> None:
        if sample_ids:
            self.undo_stack.push(DeleteSurveySamplesCommand(self, sample_ids))

    def delete_survey_session(self, session_id: str) -> None:
        self.undo_stack.push(DeleteSurveySessionCommand(self, session_id))

    def add_port_to_component(self, component_id: str, port: Port) -> None:
        comp = self.project.get_component(component_id)
        if comp is None:
            return
        updated = copy.deepcopy(comp)
        updated.ports.append(copy.deepcopy(port))
        self.update_component(component_id, updated, text=f"Add Port {port.label}")
