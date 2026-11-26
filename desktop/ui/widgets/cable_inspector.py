"""Cable & Bundle Inspector widget: endpoints, style, VLAN, physical BOM length, bundles."""

from __future__ import annotations

import copy

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from desktop.state import ProjectState
from desktop.ui.logical_tab.routing_controller import compute_orthogonal_waypoints
from shared.geo_math import calculate_cable_physical_length
from shared.models import Cable, CableBundle, LinePattern, Point2D

_PATTERNS: list[tuple[str, LinePattern]] = [
    ("Solid", LinePattern.SOLID),
    ("Dashed", LinePattern.DASHED),
    ("Dotted", LinePattern.DOTTED),
    ("Dash-Dot", LinePattern.DASH_DOT),
]


class CableInspector(QWidget):
    """Right-hand inspector for editing Cable endpoints, style, VLAN, length, and bundle."""

    def __init__(
        self,
        state: ProjectState,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._state = state
        self._cable_id: str | None = None
        self._populating = False
        self._build_ui()

        self._state.cables_changed.connect(self.refresh)
        self._state.components_changed.connect(self.refresh)
        self._state.bundles_changed.connect(self.refresh)
        self._state.floor_geometry_changed.connect(lambda _b, _f: self.refresh())
        self._state.project_reloaded.connect(self.refresh)

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)

        body = QWidget(scroll)
        scroll.setWidget(body)
        root = QVBoxLayout(body)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(10)

        self.header_label = QLabel("Cable Inspector", body)
        self.header_label.setStyleSheet("font-size: 14px; font-weight: 700; color: #38BDF8;")
        root.addWidget(self.header_label)

        # Endpoints & Port Switcher
        ep_group = QGroupBox("Endpoints & Port Switcher", body)
        ep_form = QFormLayout(ep_group)
        self.src_comp_label = QLabel("—", ep_group)
        self.src_port_combo = QComboBox(ep_group)
        self.src_port_combo.currentIndexChanged.connect(self._on_endpoint_changed)
        ep_form.addRow("Source Component:", self.src_comp_label)
        ep_form.addRow("Source Port:", self.src_port_combo)

        self.dst_comp_label = QLabel("—", ep_group)
        self.dst_port_combo = QComboBox(ep_group)
        self.dst_port_combo.currentIndexChanged.connect(self._on_endpoint_changed)
        ep_form.addRow("Target Component:", self.dst_comp_label)
        ep_form.addRow("Target Port:", self.dst_port_combo)
        root.addWidget(ep_group)

        # Metadata & Style
        style_group = QGroupBox("Cable Metadata & Style", body)
        st_form = QFormLayout(style_group)

        self.label_edit = QLineEdit(style_group)
        self.label_edit.setPlaceholderText("e.g. Core-Trunk")
        self.label_edit.editingFinished.connect(self._commit_style_and_meta)
        st_form.addRow("Label:", self.label_edit)

        self.vlan_edit = QLineEdit(style_group)
        self.vlan_edit.setPlaceholderText("e.g. 10, 20")
        self.vlan_edit.editingFinished.connect(self._commit_style_and_meta)
        st_form.addRow("VLAN ID:", self.vlan_edit)

        color_row = QHBoxLayout()
        self.color_preview = QLabel("   ", style_group)
        self.color_preview.setFixedSize(26, 22)
        self.color_hex_edit = QLineEdit("#38BDF8", style_group)
        self.color_hex_edit.editingFinished.connect(self._commit_style_and_meta)
        self.color_btn = QPushButton("Pick...", style_group)
        self.color_btn.clicked.connect(self._on_pick_color)
        color_row.addWidget(self.color_preview)
        color_row.addWidget(self.color_hex_edit, 1)
        color_row.addWidget(self.color_btn)
        st_form.addRow("Color:", color_row)

        self.pattern_combo = QComboBox(style_group)
        for label, pat in _PATTERNS:
            self.pattern_combo.addItem(label, userData=pat)
        self.pattern_combo.currentIndexChanged.connect(self._commit_style_and_meta)
        st_form.addRow("Line Pattern:", self.pattern_combo)

        self.width_spin = QDoubleSpinBox(style_group)
        self.width_spin.setRange(1.0, 10.0)
        self.width_spin.setSingleStep(0.5)
        self.width_spin.setDecimals(1)
        self.width_spin.setSuffix(" px")
        self.width_spin.valueChanged.connect(self._commit_style_and_meta)
        st_form.addRow("Width:", self.width_spin)

        root.addWidget(style_group)

        # Physical Length Readout
        phys_group = QGroupBox("Physical Length & Routing", body)
        phys_layout = QVBoxLayout(phys_group)
        self.length_label = QLabel("Physical length: —", phys_group)
        self.length_label.setWordWrap(True)
        phys_layout.addWidget(self.length_label)

        route_btns = QHBoxLayout()
        self.ortho_btn = QPushButton("Auto-Orthogonal", phys_group)
        self.ortho_btn.clicked.connect(self._on_auto_orthogonal)
        self.clear_wp_btn = QPushButton("Clear Waypoints", phys_group)
        self.clear_wp_btn.clicked.connect(self._on_clear_waypoints)
        route_btns.addWidget(self.ortho_btn)
        route_btns.addWidget(self.clear_wp_btn)
        phys_layout.addLayout(route_btns)
        root.addWidget(phys_group)

        # Bundle Membership & Controls
        bundle_group = QGroupBox("Bundle Membership", body)
        bundle_layout = QVBoxLayout(bundle_group)
        self.bundle_status_label = QLabel("Not in a logical bundle.", bundle_group)
        self.bundle_status_label.setWordWrap(True)
        bundle_layout.addWidget(self.bundle_status_label)

        b_btns = QHBoxLayout()
        self.toggle_collapse_btn = QPushButton("Toggle Collapse", bundle_group)
        self.toggle_collapse_btn.clicked.connect(self._on_toggle_bundle_collapse)
        self.unbundle_btn = QPushButton("Unbundle", bundle_group)
        self.unbundle_btn.clicked.connect(self._on_unbundle_cable)
        b_btns.addWidget(self.toggle_collapse_btn)
        b_btns.addWidget(self.unbundle_btn)
        bundle_layout.addLayout(b_btns)
        root.addWidget(bundle_group)

        root.addStretch(1)

        self.disconnect_btn = QPushButton("Disconnect Cable", body)
        self.disconnect_btn.setStyleSheet("color: #EF4444;")
        self.disconnect_btn.clicked.connect(self._on_disconnect)
        root.addWidget(self.disconnect_btn)

    def set_cable(self, cable_id: str | None) -> None:
        self._cable_id = cable_id
        self.refresh()

    def current_cable(self) -> Cable | None:
        if not self._cable_id:
            return None
        return next((c for c in self._state.project.cables if c.id == self._cable_id), None)

    def _current_bundle(self, cable: Cable) -> CableBundle | None:
        if not cable.logical_bundle_id:
            return None
        return next(
            (b for b in self._state.project.bundles if b.id == cable.logical_bundle_id),
            None,
        )

    def refresh(self) -> None:
        cable = self.current_cable()
        self.setEnabled(cable is not None)
        if cable is None:
            self._populating = True
            try:
                self.header_label.setText("No Cable Selected")
                self.src_comp_label.setText("—")
                self.dst_comp_label.setText("—")
                self.src_port_combo.clear()
                self.dst_port_combo.clear()
                self.label_edit.clear()
                self.vlan_edit.clear()
                self.length_label.setText("Physical length: —")
                self.bundle_status_label.setText("Not in a bundle.")
                self.toggle_collapse_btn.setEnabled(False)
                self.unbundle_btn.setEnabled(False)
            finally:
                self._populating = False
            return

        self._populating = True
        try:
            src_comp = self._state.project.get_component(cable.source_component_id)
            dst_comp = self._state.project.get_component(cable.target_component_id)
            self.header_label.setText(cable.label or "Cable Connection")

            self.src_comp_label.setText(src_comp.name if src_comp else cable.source_component_id)
            self.dst_comp_label.setText(dst_comp.name if dst_comp else cable.target_component_id)

            self.src_port_combo.clear()
            if src_comp is not None:
                for p in src_comp.ports:
                    occupied = self._state.project.is_port_occupied(
                        src_comp.id, p.id, exclude_cable_id=cable.id
                    )
                    if not occupied or p.id == cable.source_port_id:
                        self.src_port_combo.addItem(
                            f"{p.label} ({p.port_type.value})", userData=p.id
                        )
                idx = self.src_port_combo.findData(cable.source_port_id)
                if idx >= 0:
                    self.src_port_combo.setCurrentIndex(idx)

            self.dst_port_combo.clear()
            if dst_comp is not None:
                for p in dst_comp.ports:
                    occupied = self._state.project.is_port_occupied(
                        dst_comp.id, p.id, exclude_cable_id=cable.id
                    )
                    if not occupied or p.id == cable.target_port_id:
                        self.dst_port_combo.addItem(
                            f"{p.label} ({p.port_type.value})", userData=p.id
                        )
                idx = self.dst_port_combo.findData(cable.target_port_id)
                if idx >= 0:
                    self.dst_port_combo.setCurrentIndex(idx)

            self.label_edit.setText(cable.label)
            self.vlan_edit.setText(cable.vlan_id)
            self.color_hex_edit.setText(cable.style.color_hex)
            self._update_color_swatch(cable.style.color_hex)

            pat_idx = self.pattern_combo.findData(cable.style.pattern)
            if pat_idx >= 0:
                self.pattern_combo.setCurrentIndex(pat_idx)
            self.width_spin.setValue(float(cable.style.width_px))

            # Physical length readout via shared/geo_math.py
            bom = calculate_cable_physical_length(self._state.project, cable)
            if bom is None:
                self.length_label.setText(
                    "Physical length: Endpoints not yet placed on a floor plan."
                )
            else:
                mode_tag = "estimated" if bom.is_estimated else "routed"
                self.length_label.setText(
                    f"Horizontal: {bom.horizontal_m:.2f} m · "
                    f"Riser: {bom.vertical_riser_m:.2f} m · "
                    f"Total (+10% slack): <b>{bom.total_with_slack_m:.2f} m</b> ({mode_tag})"
                )

            bundle = self._current_bundle(cable)
            if bundle is not None:
                state_str = "Collapsed" if bundle.collapsed else "Expanded"
                self.bundle_status_label.setText(
                    f"Bundle: <b>{bundle.name}</b> ({len(bundle.member_cable_ids)} cables · "
                    f"{state_str})"
                )
                self.toggle_collapse_btn.setEnabled(True)
                self.unbundle_btn.setEnabled(True)
            else:
                self.bundle_status_label.setText("Not in a logical bundle.")
                self.toggle_collapse_btn.setEnabled(False)
                self.unbundle_btn.setEnabled(False)
        finally:
            self._populating = False

    def _update_color_swatch(self, hex_str: str) -> None:
        col = QColor(hex_str)
        if not col.isValid():
            col = QColor("#38BDF8")
        self.color_preview.setStyleSheet(
            f"background-color: {col.name()}; border: 1px solid #475569; border-radius: 3px;"
        )

    def _on_pick_color(self) -> None:
        cable = self.current_cable()
        if cable is None:
            return
        initial = QColor(self.color_hex_edit.text().strip())
        if not initial.isValid():
            initial = QColor(cable.style.color_hex)
        chosen = QColorDialog.getColor(initial, self, "Select Cable Color")
        if chosen.isValid():
            self.color_hex_edit.setText(chosen.name().upper())
            self._commit_style_and_meta()

    def _on_endpoint_changed(self) -> None:
        if self._populating:
            return
        cable = self.current_cable()
        if cable is None:
            return
        new_src_port = str(self.src_port_combo.currentData() or cable.source_port_id)
        new_dst_port = str(self.dst_port_combo.currentData() or cable.target_port_id)
        if (
            cable.source_component_id == cable.target_component_id
            and new_src_port == new_dst_port
        ):
            self.refresh()
            return
        if new_src_port == cable.source_port_id and new_dst_port == cable.target_port_id:
            return
        updated = copy.deepcopy(cable)
        updated.source_port_id = new_src_port
        updated.target_port_id = new_dst_port
        self._state.update_cable(cable.id, updated, text="Switch Cable Endpoint Port")

    def _commit_style_and_meta(self) -> None:
        if self._populating:
            return
        cable = self.current_cable()
        if cable is None:
            return
        updated = copy.deepcopy(cable)
        updated.label = self.label_edit.text().strip()
        updated.vlan_id = self.vlan_edit.text().strip()
        col = QColor(self.color_hex_edit.text().strip())
        if col.isValid():
            updated.style.color_hex = col.name().upper()
            self._update_color_swatch(updated.style.color_hex)
        raw_pat = self.pattern_combo.currentData()
        if raw_pat is not None:
            updated.style.pattern = (
                raw_pat if isinstance(raw_pat, LinePattern) else LinePattern(str(raw_pat))
            )
        updated.style.width_px = float(self.width_spin.value())

        if updated.to_dict() != cable.to_dict():
            self._state.update_cable(cable.id, updated, text="Update Cable Properties")

    def _on_clear_waypoints(self) -> None:
        cable = self.current_cable()
        if cable is not None and cable.logical_waypoints:
            self._state.set_cable_waypoints(cable.id, [])

    def _on_auto_orthogonal(self) -> None:
        cable = self.current_cable()
        if cable is None:
            return
        src_comp = self._state.project.get_component(cable.source_component_id)
        dst_comp = self._state.project.get_component(cable.target_component_id)
        if src_comp is None or dst_comp is None:
            return
        src_pt = Point2D(src_comp.logical_pos.x + 80.0, src_comp.logical_pos.y + 60.0)
        dst_pt = Point2D(dst_comp.logical_pos.x + 80.0, dst_comp.logical_pos.y + 60.0)
        waypoints = compute_orthogonal_waypoints(src_pt, dst_pt)
        self._state.set_cable_waypoints(cable.id, waypoints)

    def _on_toggle_bundle_collapse(self) -> None:
        cable = self.current_cable()
        if cable is None:
            return
        bundle = self._current_bundle(cable)
        if bundle is None:
            return
        updated = copy.deepcopy(bundle)
        updated.collapsed = not bundle.collapsed
        self._state.update_bundle(bundle.id, updated, text="Toggle Bundle Collapse")

    def _on_unbundle_cable(self) -> None:
        cable = self.current_cable()
        if cable is None:
            return
        bundle = self._current_bundle(cable)
        if bundle is None:
            return
        remaining = [cid for cid in bundle.member_cable_ids if cid != cable.id]
        if len(remaining) < 2:
            self._state.delete_bundle(bundle.id)
        else:
            updated = copy.deepcopy(bundle)
            updated.member_cable_ids = remaining
            self._state.update_bundle(bundle.id, updated, text="Remove Cable from Bundle")

    def _on_disconnect(self) -> None:
        cable = self.current_cable()
        if cable is not None:
            self._state.disconnect_cable(cable.id)
