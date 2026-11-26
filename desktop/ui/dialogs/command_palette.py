"""Ctrl+K Global Fuzzy Command Palette for devices, cables, buildings, floors, rooms & actions."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from shared.models import ComponentTemplate, Project, normalize_mac
from shared.presets import BUILTIN_TEMPLATES


@dataclass(frozen=True)
class CommandTarget:
    """Searchable Command Palette entry with navigation metadata."""

    kind: str  # "component" | "cable" | "template" | "building" | "floor" | "room" | "action"
    title: str
    subtitle: str
    search_text: str
    entity_id: str = ""
    building_id: str = ""
    floor_id: str = ""
    action_id: str = ""


def _subsequence_score(needle: str, haystack: str) -> int | None:
    """Return fuzzy score > 0 if needle is an ordered subsequence of haystack, else None."""
    if not needle:
        return 1
    pos = -1
    gaps = 0
    first_idx = -1
    for ch in needle:
        nxt = haystack.find(ch, pos + 1)
        if nxt < 0:
            return None
        if first_idx < 0:
            first_idx = nxt
        elif nxt > pos + 1:
            gaps += nxt - pos - 1
        pos = nxt
    return max(10, 120 - gaps * 3 - first_idx)


def fuzzy_score(query: str, target_text: str) -> int | None:
    """Compute fuzzy match score (higher is better), or None when query does not match."""
    cleaned = query.strip().lower()
    if not cleaned:
        return 1
    hay = target_text.lower()
    tokens = [tok for tok in cleaned.split() if tok]
    total = 0
    for tok in tokens:
        idx = hay.find(tok)
        if idx >= 0:
            bonus = 400 if idx == 0 else (260 if hay[idx - 1] in " -_/|:" else 180)
            total += bonus - min(idx, 100)
        else:
            sub = _subsequence_score(tok, hay)
            if sub is None:
                return None
            total += sub
    return total


def build_palette_entries(
    project: Project,
    global_templates: Sequence[ComponentTemplate] | None = None,
) -> list[CommandTarget]:
    """Index all project components, cables, buildings, floors, rooms, templates, and actions."""
    entries: list[CommandTarget] = []

    for comp in project.components:
        kv_bits = " ".join(f"{k} {v}" for k, v in comp.custom_attributes.items())
        ssid_bits = " ".join(comp.ssids)
        mac_norm = normalize_mac(comp.mac_address)
        details: list[str] = [comp.component_type.value.replace("_", " ").title()]
        if comp.ip_address:
            details.append(f"IP {comp.ip_address}")
        if comp.mac_address:
            details.append(f"MAC {comp.mac_address}")
        if comp.ssids:
            details.append(f"SSIDs: {', '.join(comp.ssids)}")
        if comp.custom_attributes:
            kv_preview = ", ".join(f"{k}={v}" for k, v in list(comp.custom_attributes.items())[:3])
            details.append(kv_preview)
        search_text = (
            f"{comp.name} {comp.component_type.value} {comp.ip_address} "
            f"{comp.mac_address} {mac_norm} {ssid_bits} {kv_bits}"
        )
        entries.append(
            CommandTarget(
                kind="component",
                title=comp.name,
                subtitle=" · ".join(details),
                search_text=search_text,
                entity_id=comp.id,
                building_id=comp.physical_building_id or "",
                floor_id=comp.physical_floor_id or "",
            )
        )

    for cable in project.cables:
        src = project.get_component(cable.source_component_id)
        dst = project.get_component(cable.target_component_id)
        src_p = project.get_port(cable.source_component_id, cable.source_port_id)
        dst_p = project.get_port(cable.target_component_id, cable.target_port_id)
        src_str = f"{src.name}[{src_p.label}]" if src and src_p else cable.source_component_id
        dst_str = f"{dst.name}[{dst_p.label}]" if dst and dst_p else cable.target_component_id
        title = cable.label or f"{src_str} ↔ {dst_str}"
        vlan_tag = f"VLAN {cable.vlan_id}" if cable.vlan_id else "Untagged"
        subtitle = f"{src_str} ↔ {dst_str} · {vlan_tag}"
        search_text = f"{title} {src_str} {dst_str} vlan {cable.vlan_id} {cable.label}"
        entries.append(
            CommandTarget(
                kind="cable",
                title=title,
                subtitle=subtitle,
                search_text=search_text,
                entity_id=cable.id,
            )
        )

    for bldg in project.buildings:
        anchor_desc = (
            f"Geo ({bldg.geo_anchor.origin_lat:.5f}, {bldg.geo_anchor.origin_lon:.5f})"
            if bldg.geo_anchor is not None
            else "Unplaced on Map"
        )
        entries.append(
            CommandTarget(
                kind="building",
                title=bldg.name,
                subtitle=f"Building · {len(bldg.floors)} floor(s) · {anchor_desc}",
                search_text=f"building {bldg.name} {anchor_desc}",
                entity_id=bldg.id,
                building_id=bldg.id,
            )
        )
        for floor in bldg.floors:
            entries.append(
                CommandTarget(
                    kind="floor",
                    title=f"{bldg.name} / {floor.name}",
                    subtitle=f"Floor Level {floor.level_index} · {len(floor.rooms)} room(s)",
                    search_text=f"floor {bldg.name} {floor.name} level {floor.level_index}",
                    entity_id=floor.id,
                    building_id=bldg.id,
                    floor_id=floor.id,
                )
            )
            for room in floor.rooms:
                entries.append(
                    CommandTarget(
                        kind="room",
                        title=room.name,
                        subtitle=f"Room in {bldg.name} / {floor.name}",
                        search_text=f"room {room.name} {floor.name} {bldg.name}",
                        entity_id=room.id,
                        building_id=bldg.id,
                        floor_id=floor.id,
                    )
                )

    seen_tpl_ids: set[str] = set()
    all_templates = [
        *BUILTIN_TEMPLATES,
        *project.custom_templates,
        *(global_templates or []),
    ]
    for tpl in all_templates:
        if tpl.id in seen_tpl_ids:
            continue
        seen_tpl_ids.add(tpl.id)
        scope = "Built-in" if tpl.is_builtin else "Custom"
        entries.append(
            CommandTarget(
                kind="template",
                title=tpl.template_name,
                subtitle=f"{scope} Template · {len(tpl.port_specs)} port(s) · {tpl.category}",
                search_text=(
                    f"template {tpl.template_name} {tpl.component_type.value} "
                    f"{tpl.category} {tpl.default_name_prefix}"
                ),
                entity_id=tpl.id,
            )
        )

    quick_actions: list[tuple[str, str, str]] = [
        ("action:tab_logical", "Go to Tab 1: Logical Network", "Switch workspace tab"),
        ("action:tab_map", "Go to Tab 2: Geo Map & Floors", "Switch workspace tab"),
        ("action:tab_physical", "Go to Tab 3: Physical Mapping & BOM", "Switch workspace tab"),
        ("action:tab_survey", "Go to Tab 4: Wi-Fi Survey & Heatmap", "Switch workspace tab"),
        ("action:load_sample", "Load Sample Campus Project", "Open built-in Berlin campus"),
        ("action:new_project", "New Project", "Create blank network project"),
        ("action:open_project", "Open Project (.netviz)...", "Load project archive"),
        ("action:save_project", "Save Project (.netviz)", "Save current project"),
        ("action:lan_sync", "Mobile LAN / USB Sync...", "Sync with Android companion"),
        ("action:template_manager", "Template Manager...", "Edit or import/export templates"),
        ("action:settings", "Settings & Map API Key...", "Configure Google Maps & cache"),
        ("action:export_pdf", "Export Engineering PDF Report...", "Multi-page ReportLab PDF"),
        ("action:export_png", "Export High-DPI PNG...", "Composite PNG diagram"),
        ("action:export_svg", "Export Vector SVG...", "Scalable SVG diagram"),
    ]
    for action_id, title, subtitle in quick_actions:
        entries.append(
            CommandTarget(
                kind="action",
                title=title,
                subtitle=subtitle,
                search_text=f"action {title} {subtitle}",
                action_id=action_id,
            )
        )

    return entries


_KIND_BADGES: dict[str, str] = {
    "component": "[DEVICE]",
    "cable": "[CABLE]",
    "building": "[BLDG]",
    "floor": "[FLOOR]",
    "room": "[ROOM]",
    "template": "[TPL]",
    "action": "[ACTION]",
}


class CommandPaletteDialog(QDialog):
    """Modal Ctrl+K fuzzy search & quick navigation dialog."""

    target_selected = Signal(object)  # CommandTarget

    def __init__(
        self,
        project: Project,
        *,
        global_templates: Sequence[ComponentTemplate] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Quick Jump & Command Palette (Ctrl+K)")
        self.setMinimumSize(620, 440)
        self._entries = build_palette_entries(project, global_templates=global_templates)
        self.selected_target: CommandTarget | None = None
        self._build_ui()
        self._refresh_results("")

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(8)

        hint = QLabel(
            "Search devices, IPs, MACs, SSIDs, custom attributes, VLANs, buildings, "
            "floors, rooms, templates, or actions:",
            self,
        )
        hint.setStyleSheet("color: #94A3B8; font-size: 12px;")
        root.addWidget(hint)

        self.search_edit = QLineEdit(self)
        self.search_edit.setPlaceholderText(
            "Type to fuzzy-search (e.g. ASW, 10.10, VLAN 10, Lab)..."
        )
        self.search_edit.textChanged.connect(self._refresh_results)
        self.search_edit.installEventFilter(self)
        root.addWidget(self.search_edit)

        self.results_list = QListWidget(self)
        self.results_list.setAlternatingRowColors(True)
        self.results_list.itemDoubleClicked.connect(self._activate_item)
        root.addWidget(self.results_list, 1)

        footer = QLabel("↑/↓ to navigate · Enter to jump · Esc to close", self)
        footer.setStyleSheet("color: #64748B; font-size: 11px;")
        root.addWidget(footer)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.search_edit and event.type() == QEvent.Type.KeyPress:
            key_event = event  # type: ignore[assignment]
            if isinstance(key_event, QKeyEvent):
                key = key_event.key()
                if key == Qt.Key.Key_Down:
                    self._move_selection(1)
                    return True
                if key == Qt.Key.Key_Up:
                    self._move_selection(-1)
                    return True
                if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    self.activate_current()
                    return True
        return super().eventFilter(watched, event)

    def _move_selection(self, delta: int) -> None:
        count = self.results_list.count()
        if count <= 0:
            return
        cur = self.results_list.currentRow()
        nxt = max(0, min(count - 1, cur + delta))
        self.results_list.setCurrentRow(nxt)

    def _refresh_results(self, query: str) -> None:
        scored: list[tuple[int, int, CommandTarget]] = []
        for idx, entry in enumerate(self._entries):
            score = fuzzy_score(query, entry.search_text)
            if score is not None:
                scored.append((score, -idx, entry))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)

        self.results_list.clear()
        for _, _, entry in scored[:60]:
            badge = _KIND_BADGES.get(entry.kind, "[ITEM]")
            item = QListWidgetItem(f"{badge}  {entry.title}  —  {entry.subtitle}")
            item.setData(Qt.ItemDataRole.UserRole, entry)
            self.results_list.addItem(item)

        if self.results_list.count() > 0:
            self.results_list.setCurrentRow(0)

    def _activate_item(self, item: QListWidgetItem) -> None:
        target = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(target, CommandTarget):
            self.selected_target = target
            self.target_selected.emit(target)
            self.accept()

    def activate_current(self) -> None:
        """Activate the currently highlighted result item."""
        item = self.results_list.currentItem()
        if item is not None:
            self._activate_item(item)

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self.search_edit.setFocus()
        self.search_edit.selectAll()
