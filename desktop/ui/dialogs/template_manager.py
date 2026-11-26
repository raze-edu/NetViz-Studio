"""Template Manager Dialog: CRUD over global and project ComponentTemplates."""

from __future__ import annotations

import copy
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from desktop.config import load_global_templates, save_global_templates
from desktop.state import ProjectState
from desktop.ui.theme import COMPONENT_TYPE_COLORS, component_color
from shared.bundle_io import export_templates_json, import_templates_json
from shared.models import ComponentTemplate, ComponentType, PortSpec, PortType, new_id
from shared.presets import BUILTIN_TEMPLATES

_PORT_TYPES = list(PortType)
_COMPONENT_TYPES = list(ComponentType)


def _format_component_type(ctype: ComponentType) -> str:
    return ctype.value.replace("_", " ").title()


class BulkPortBlockDialog(QDialog):
    """Modal dialog to generate a numbered block of PortSpecs."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Bulk Port-Block Generator")
        self.setMinimumWidth(360)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        form = QFormLayout()

        self.prefix_edit = QLineEdit("G", self)
        form.addRow("Label Prefix:", self.prefix_edit)

        self.start_spin = QSpinBox(self)
        self.start_spin.setRange(0, 999)
        self.start_spin.setValue(1)
        form.addRow("Start Number:", self.start_spin)

        self.count_spin = QSpinBox(self)
        self.count_spin.setRange(1, 128)
        self.count_spin.setValue(8)
        form.addRow("Port Count:", self.count_spin)

        self.pad_spin = QSpinBox(self)
        self.pad_spin.setRange(0, 4)
        self.pad_spin.setValue(0)
        form.addRow("Zero-Pad Digits:", self.pad_spin)

        self.type_combo = QComboBox(self)
        for ptype in _PORT_TYPES:
            self.type_combo.addItem(ptype.value, userData=ptype)
        idx = self.type_combo.findData(PortType.BASE1000)
        if idx >= 0:
            self.type_combo.setCurrentIndex(idx)
        form.addRow("Port Type:", self.type_combo)

        self.speed_spin = QSpinBox(self)
        self.speed_spin.setRange(0, 400_000)
        self.speed_spin.setSingleStep(1000)
        self.speed_spin.setValue(1000)
        self.speed_spin.setSuffix(" Mbps")
        form.addRow("Speed:", self.speed_spin)

        self.poe_check = QCheckBox("PoE Capable", self)
        form.addRow("", self.poe_check)

        root.addLayout(form)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel_btn = QPushButton("Cancel", self)
        cancel_btn.clicked.connect(self.reject)
        ok_btn = QPushButton("Generate Ports", self)
        ok_btn.setProperty("primary", True)
        ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)
        root.addLayout(btn_row)

    def build_port_specs(self) -> list[PortSpec]:
        """Return the generated list of PortSpec entries."""
        prefix = self.prefix_edit.text().strip() or "P"
        start = self.start_spin.value()
        count = self.count_spin.value()
        pad = self.pad_spin.value()
        type_val = self.type_combo.currentData()
        ptype = PortType(type_val) if type_val is not None else PortType.BASE1000
        speed_val = self.speed_spin.value()
        speed: int | None = speed_val if speed_val > 0 else None
        poe = self.poe_check.isChecked()

        specs: list[PortSpec] = []
        for offset in range(count):
            num = start + offset
            num_str = f"{num:0{pad}d}" if pad > 0 else str(num)
            specs.append(
                PortSpec(
                    label=f"{prefix}{num_str}",
                    port_type=ptype,
                    speed_mbps=speed,
                    poe_capable=poe,
                )
            )
        return specs


class TemplateManagerDialog(QDialog):
    """CRUD manager for Global (templates.json) and Project custom ComponentTemplates."""

    templates_updated = Signal()

    def __init__(
        self,
        state: ProjectState,
        *,
        initial_template: ComponentTemplate | None = None,
        initial_scope: str = "global",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Component Template Manager")
        self.resize(960, 640)
        self._state = state
        self._global_templates: list[ComponentTemplate] = load_global_templates()
        self._current_scope: str = initial_scope
        self._current_template_id: str | None = None
        self._populating = False

        self._build_ui()
        self._refresh_list()

        if initial_template is not None:
            self._load_draft_template(initial_template, scope=initial_scope)
        elif self.template_list.count() > 0:
            self.template_list.setCurrentRow(0)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(10)

        # Top scope & import/export bar
        top_bar = QHBoxLayout()
        top_bar.addWidget(QLabel("Library Scope:", self))
        self.scope_combo = QComboBox(self)
        self.scope_combo.addItem("Global Library (%APPDATA%/templates.json)", userData="global")
        self.scope_combo.addItem("Project Custom Templates", userData="project")
        self.scope_combo.addItem("Built-In Reference (Clone Only)", userData="builtin")
        idx = self.scope_combo.findData(self._current_scope)
        if idx >= 0:
            self.scope_combo.setCurrentIndex(idx)
        self.scope_combo.currentIndexChanged.connect(self._on_scope_changed)
        top_bar.addWidget(self.scope_combo, 1)

        self.import_btn = QPushButton("Import .netviz-templates.json...", self)
        self.import_btn.clicked.connect(self._on_import_clicked)
        self.export_btn = QPushButton("Export .netviz-templates.json...", self)
        self.export_btn.clicked.connect(self._on_export_clicked)
        top_bar.addWidget(self.import_btn)
        top_bar.addWidget(self.export_btn)
        root.addLayout(top_bar)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)

        # Left list & CRUD buttons
        left_panel = QWidget(splitter)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)

        self.template_list = QListWidget(left_panel)
        self.template_list.currentItemChanged.connect(self._on_selection_changed)
        left_layout.addWidget(self.template_list, 1)

        list_btns = QHBoxLayout()
        self.new_btn = QPushButton("+ New", left_panel)
        self.new_btn.clicked.connect(self._on_new_clicked)
        self.clone_btn = QPushButton("Clone", left_panel)
        self.clone_btn.clicked.connect(self._on_clone_clicked)
        self.copy_other_btn = QPushButton("Copy to Other Scope", left_panel)
        self.copy_other_btn.clicked.connect(self._on_copy_scope_clicked)
        self.delete_btn = QPushButton("Delete", left_panel)
        self.delete_btn.clicked.connect(self._on_delete_clicked)
        list_btns.addWidget(self.new_btn)
        list_btns.addWidget(self.clone_btn)
        list_btns.addWidget(self.copy_other_btn)
        list_btns.addWidget(self.delete_btn)
        left_layout.addLayout(list_btns)

        # Right editor form
        right_panel = QWidget(splitter)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(4, 0, 0, 0)

        meta_group = QGroupBox("Template Definition", right_panel)
        form = QFormLayout(meta_group)

        self.name_edit = QLineEdit(meta_group)
        form.addRow("Template Name:", self.name_edit)

        self.type_combo = QComboBox(meta_group)
        for ctype in _COMPONENT_TYPES:
            self.type_combo.addItem(_format_component_type(ctype), userData=ctype)
        form.addRow("Component Type:", self.type_combo)

        self.category_edit = QLineEdit(meta_group)
        form.addRow("Category:", self.category_edit)

        self.prefix_edit = QLineEdit(meta_group)
        form.addRow("Name Prefix:", self.prefix_edit)

        color_row = QHBoxLayout()
        self.color_edit = QLineEdit(meta_group)
        self.color_edit.setPlaceholderText("#38BDF8")
        self.color_pick_btn = QPushButton("Pick...", meta_group)
        self.color_pick_btn.clicked.connect(self._on_pick_color)
        color_row.addWidget(self.color_edit, 1)
        color_row.addWidget(self.color_pick_btn)
        form.addRow("Accent Color:", color_row)

        self.desc_edit = QLineEdit(meta_group)
        form.addRow("Description:", self.desc_edit)

        self.freqs_edit = QLineEdit(meta_group)
        self.freqs_edit.setPlaceholderText("e.g. 2.4, 5.0, 6.0")
        form.addRow("Default Freqs (GHz):", self.freqs_edit)

        self.ssids_edit = QLineEdit(meta_group)
        self.ssids_edit.setPlaceholderText("e.g. Corp-WiFi, Guest-WiFi")
        form.addRow("Default SSIDs:", self.ssids_edit)

        right_layout.addWidget(meta_group)

        # Default Custom Attributes K/V Table
        attr_group = QGroupBox("Default Custom Attributes", right_panel)
        attr_layout = QVBoxLayout(attr_group)
        self.attr_table = QTableWidget(0, 2, attr_group)
        self.attr_table.setHorizontalHeaderLabels(["Key", "Value"])
        self.attr_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.attr_table.setMaximumHeight(110)
        attr_layout.addWidget(self.attr_table)
        attr_btns = QHBoxLayout()
        add_attr_btn = QPushButton("+ Add Attribute", attr_group)
        add_attr_btn.clicked.connect(self._on_add_attr_row)
        del_attr_btn = QPushButton("Remove Selected", attr_group)
        del_attr_btn.clicked.connect(self._on_remove_attr_row)
        attr_btns.addWidget(add_attr_btn)
        attr_btns.addWidget(del_attr_btn)
        attr_btns.addStretch(1)
        attr_layout.addLayout(attr_btns)
        right_layout.addWidget(attr_group)

        # Ports Spec Table + Bulk Generator
        ports_group = QGroupBox("Template Port Specifications", right_panel)
        ports_layout = QVBoxLayout(ports_group)
        self.ports_table = QTableWidget(0, 5, ports_group)
        self.ports_table.setHorizontalHeaderLabels(
            ["Label", "Port Type", "Custom Type", "Speed (Mbps)", "PoE"]
        )
        self.ports_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        ports_layout.addWidget(self.ports_table, 1)

        port_btns = QHBoxLayout()
        add_port_btn = QPushButton("+ Add Port", ports_group)
        add_port_btn.clicked.connect(self._on_add_port_row)
        bulk_port_btn = QPushButton("Bulk Port-Block Generator...", ports_group)
        bulk_port_btn.clicked.connect(self._on_bulk_ports)
        del_port_btn = QPushButton("Remove Port", ports_group)
        del_port_btn.clicked.connect(self._on_remove_port_row)
        port_btns.addWidget(add_port_btn)
        port_btns.addWidget(bulk_port_btn)
        port_btns.addWidget(del_port_btn)
        port_btns.addStretch(1)
        ports_layout.addLayout(port_btns)
        right_layout.addWidget(ports_group, 1)

        # Save / Close footer
        footer = QHBoxLayout()
        self.readonly_hint = QLabel("", right_panel)
        self.readonly_hint.setStyleSheet("color: #F59E0B;")
        footer.addWidget(self.readonly_hint, 1)
        self.save_tpl_btn = QPushButton("Save Template", right_panel)
        self.save_tpl_btn.setProperty("primary", True)
        self.save_tpl_btn.clicked.connect(self._on_save_template)
        close_btn = QPushButton("Close", right_panel)
        close_btn.clicked.connect(self.accept)
        footer.addWidget(self.save_tpl_btn)
        footer.addWidget(close_btn)
        right_layout.addLayout(footer)

        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        root.addWidget(splitter, 1)

    def _active_templates(self, scope: str | None = None) -> list[ComponentTemplate]:
        s = scope or self._current_scope
        if s == "global":
            return self._global_templates
        if s == "project":
            return self._state.project.custom_templates
        return BUILTIN_TEMPLATES

    def _refresh_list(self, select_id: str | None = None) -> None:
        self.template_list.clear()
        for tpl in self._active_templates():
            item = QListWidgetItem(
                f"{tpl.template_name}  [{len(tpl.port_specs)} ports · {tpl.default_name_prefix}]"
            )
            item.setData(Qt.ItemDataRole.UserRole, tpl.id)
            self.template_list.addItem(item)
            if select_id and tpl.id == select_id:
                self.template_list.setCurrentItem(item)

        is_builtin = self._current_scope == "builtin"
        self.new_btn.setEnabled(not is_builtin)
        self.delete_btn.setEnabled(not is_builtin)
        self.save_tpl_btn.setEnabled(not is_builtin)
        self.copy_other_btn.setText(
            "Copy to Global" if self._current_scope == "project" else "Copy to Project"
        )
        self.readonly_hint.setText(
            "Built-in presets are read-only. Click 'Clone' to customize."
            if is_builtin
            else ""
        )

    def _on_scope_changed(self) -> None:
        self._current_scope = str(self.scope_combo.currentData() or "global")
        self._current_template_id = None
        self._refresh_list()
        if self.template_list.count() > 0:
            self.template_list.setCurrentRow(0)
        else:
            self._clear_editor()

    def _on_selection_changed(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        if current is None:
            return
        tpl_id = str(current.data(Qt.ItemDataRole.UserRole) or "")
        tpl = next((t for t in self._active_templates() if t.id == tpl_id), None)
        if tpl is not None:
            self._populate_editor(tpl)

    def _clear_editor(self) -> None:
        self._populating = True
        try:
            self._current_template_id = None
            self.name_edit.clear()
            self.type_combo.setCurrentIndex(0)
            self.category_edit.setText("Custom")
            self.prefix_edit.setText("DEV")
            self.color_edit.setText(COMPONENT_TYPE_COLORS[ComponentType.DEVICE])
            self.desc_edit.clear()
            self.freqs_edit.clear()
            self.ssids_edit.clear()
            self.attr_table.setRowCount(0)
            self.ports_table.setRowCount(0)
        finally:
            self._populating = False

    def _load_draft_template(self, tpl: ComponentTemplate, scope: str = "global") -> None:
        idx = self.scope_combo.findData(scope)
        if idx >= 0:
            self.scope_combo.setCurrentIndex(idx)
        self._populate_editor(tpl)
        self._current_template_id = None  # Treat initial draft as unsaved until Save is clicked

    def _populate_editor(self, tpl: ComponentTemplate) -> None:
        self._populating = True
        try:
            self._current_template_id = tpl.id
            self.name_edit.setText(tpl.template_name)
            idx = self.type_combo.findData(tpl.component_type)
            if idx >= 0:
                self.type_combo.setCurrentIndex(idx)
            self.category_edit.setText(tpl.category)
            self.prefix_edit.setText(tpl.default_name_prefix)
            self.color_edit.setText(
                tpl.accent_color_hex or COMPONENT_TYPE_COLORS.get(tpl.component_type, "#38BDF8")
            )
            self.desc_edit.setText(tpl.description)
            self.freqs_edit.setText(", ".join(str(f) for f in tpl.default_frequencies_ghz))
            self.ssids_edit.setText(", ".join(tpl.default_ssids))

            self.attr_table.setRowCount(0)
            for k, v in tpl.default_custom_attributes.items():
                self._append_attr_row(k, v)

            self.ports_table.setRowCount(0)
            for spec in tpl.port_specs:
                self._append_port_row(spec)
        finally:
            self._populating = False

    def _append_attr_row(self, key: str = "", value: str = "") -> None:
        row = self.attr_table.rowCount()
        self.attr_table.insertRow(row)
        self.attr_table.setItem(row, 0, QTableWidgetItem(key))
        self.attr_table.setItem(row, 1, QTableWidgetItem(value))

    def _append_port_row(self, spec: PortSpec | None = None) -> None:
        s = spec or PortSpec(label=f"P{self.ports_table.rowCount() + 1}")
        row = self.ports_table.rowCount()
        self.ports_table.insertRow(row)
        self.ports_table.setItem(row, 0, QTableWidgetItem(s.label))

        type_combo = QComboBox(self.ports_table)
        for ptype in _PORT_TYPES:
            type_combo.addItem(ptype.value, userData=ptype)
        idx = type_combo.findData(s.port_type)
        if idx >= 0:
            type_combo.setCurrentIndex(idx)
        self.ports_table.setCellWidget(row, 1, type_combo)

        self.ports_table.setItem(row, 2, QTableWidgetItem(s.custom_type_name or ""))
        self.ports_table.setItem(
            row, 3, QTableWidgetItem(str(s.speed_mbps) if s.speed_mbps is not None else "")
        )
        poe_item = QTableWidgetItem()
        poe_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
        poe_item.setCheckState(Qt.CheckState.Checked if s.poe_capable else Qt.CheckState.Unchecked)
        self.ports_table.setItem(row, 4, poe_item)

    def _on_add_attr_row(self) -> None:
        self._append_attr_row("", "")

    def _on_remove_attr_row(self) -> None:
        row = self.attr_table.currentRow()
        if row >= 0:
            self.attr_table.removeRow(row)

    def _on_add_port_row(self) -> None:
        self._append_port_row(None)

    def _on_remove_port_row(self) -> None:
        row = self.ports_table.currentRow()
        if row >= 0:
            self.ports_table.removeRow(row)

    def _on_bulk_ports(self) -> None:
        dlg = BulkPortBlockDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            for spec in dlg.build_port_specs():
                self._append_port_row(spec)

    def _on_pick_color(self) -> None:
        raw_ctype = self.type_combo.currentData()
        ctype = ComponentType(raw_ctype) if raw_ctype is not None else ComponentType.DEVICE
        initial = QColor(self.color_edit.text().strip())
        if not initial.isValid():
            initial = component_color(ctype)
        chosen = QColorDialog.getColor(initial, self, "Template Accent Color")
        if chosen.isValid():
            self.color_edit.setText(chosen.name().upper())

    def _collect_template_from_editor(self, *, force_new_id: bool = False) -> ComponentTemplate:
        name = self.name_edit.text().strip() or "Custom Template"
        raw_ctype = self.type_combo.currentData()
        ctype = ComponentType(raw_ctype) if raw_ctype is not None else ComponentType.DEVICE
        category = self.category_edit.text().strip() or "Custom"
        prefix = self.prefix_edit.text().strip() or "DEV"
        color_hex = self.color_edit.text().strip() or None
        desc = self.desc_edit.text().strip()

        freqs: list[float] = []
        for token in self.freqs_edit.text().split(","):
            tok = token.strip()
            if tok:
                try:
                    freqs.append(float(tok))
                except ValueError:
                    continue

        ssids = [s.strip() for s in self.ssids_edit.text().split(",") if s.strip()]

        attrs: dict[str, str] = {}
        for row in range(self.attr_table.rowCount()):
            k_item = self.attr_table.item(row, 0)
            v_item = self.attr_table.item(row, 1)
            k = k_item.text().strip() if k_item else ""
            v = v_item.text().strip() if v_item else ""
            if k:
                attrs[k] = v

        port_specs: list[PortSpec] = []
        for row in range(self.ports_table.rowCount()):
            lbl_item = self.ports_table.item(row, 0)
            lbl = lbl_item.text().strip() if lbl_item else f"P{row + 1}"
            if not lbl:
                lbl = f"P{row + 1}"
            widget = self.ports_table.cellWidget(row, 1)
            raw_pt = widget.currentData() if isinstance(widget, QComboBox) else None
            ptype = PortType(raw_pt) if raw_pt is not None else PortType.BASE1000
            custom_item = self.ports_table.item(row, 2)
            custom_name = custom_item.text().strip() if custom_item else ""
            speed_item = self.ports_table.item(row, 3)
            speed_txt = speed_item.text().strip() if speed_item else ""
            speed: int | None = None
            if speed_txt:
                try:
                    speed = int(float(speed_txt))
                except ValueError:
                    speed = None
            poe_item = self.ports_table.item(row, 4)
            poe = poe_item is not None and poe_item.checkState() == Qt.CheckState.Checked
            port_specs.append(
                PortSpec(
                    label=lbl,
                    port_type=ptype,
                    custom_type_name=custom_name or None,
                    speed_mbps=speed,
                    poe_capable=poe,
                )
            )

        tpl_id = (
            new_id()
            if (force_new_id or not self._current_template_id)
            else self._current_template_id
        )
        return ComponentTemplate(
            id=tpl_id,
            template_name=name,
            component_type=ctype,
            category=category,
            description=desc,
            default_name_prefix=prefix,
            accent_color_hex=color_hex,
            default_frequencies_ghz=freqs,
            default_ssids=ssids,
            default_custom_attributes=attrs,
            port_specs=port_specs,
            is_builtin=False,
        )

    def _persist_template(self, tpl: ComponentTemplate, scope: str) -> None:
        if scope == "global":
            existing_idx = next(
                (i for i, t in enumerate(self._global_templates) if t.id == tpl.id), -1
            )
            if existing_idx >= 0:
                self._global_templates[existing_idx] = copy.deepcopy(tpl)
            else:
                self._global_templates.append(copy.deepcopy(tpl))
            save_global_templates(self._global_templates)
            self._state.templates_changed.emit()
        elif scope == "project":
            self._state.save_custom_template(tpl)
        self.templates_updated.emit()

    def _on_save_template(self) -> None:
        if self._current_scope == "builtin":
            return
        tpl = self._collect_template_from_editor()
        self._current_template_id = tpl.id
        self._persist_template(tpl, self._current_scope)
        self._refresh_list(select_id=tpl.id)

    def _on_new_clicked(self) -> None:
        if self._current_scope == "builtin":
            return
        blank = ComponentTemplate(
            id=new_id(),
            template_name="New Custom Template",
            component_type=ComponentType.ACTIVE_SWITCH,
            category="Custom",
            default_name_prefix="SW",
            accent_color_hex=COMPONENT_TYPE_COLORS[ComponentType.ACTIVE_SWITCH],
            port_specs=[PortSpec(label="P1"), PortSpec(label="P2")],
            is_builtin=False,
        )
        self._persist_template(blank, self._current_scope)
        self._refresh_list(select_id=blank.id)

    def _on_clone_clicked(self) -> None:
        tpl = self._collect_template_from_editor(force_new_id=True)
        tpl.template_name = f"{tpl.template_name} (Copy)"
        tpl.is_builtin = False
        target_scope = "global" if self._current_scope == "builtin" else self._current_scope
        if self._current_scope == "builtin":
            idx = self.scope_combo.findData("global")
            if idx >= 0:
                self.scope_combo.setCurrentIndex(idx)
        self._persist_template(tpl, target_scope)
        self._refresh_list(select_id=tpl.id)

    def _on_copy_scope_clicked(self) -> None:
        tpl = self._collect_template_from_editor(force_new_id=True)
        tpl.is_builtin = False
        target_scope = "global" if self._current_scope == "project" else "project"
        self._persist_template(tpl, target_scope)
        QMessageBox.information(
            self,
            "Template Copied",
            f"Copied '{tpl.template_name}' to {target_scope.title()} templates.",
        )

    def _on_delete_clicked(self) -> None:
        if self._current_scope == "builtin" or not self._current_template_id:
            return
        target_id = self._current_template_id
        if self._current_scope == "global":
            self._global_templates = [t for t in self._global_templates if t.id != target_id]
            save_global_templates(self._global_templates)
            self._state.templates_changed.emit()
        else:
            self._state.remove_custom_template(target_id)
        self._current_template_id = None
        self._refresh_list()
        if self.template_list.count() > 0:
            self.template_list.setCurrentRow(0)
        else:
            self._clear_editor()
        self.templates_updated.emit()

    def _on_import_clicked(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import NetViz Templates",
            "",
            "NetViz Templates (*.netviz-templates.json *.json);;All Files (*)",
        )
        if not path:
            return
        try:
            imported = import_templates_json(Path(path))
        except Exception as exc:
            QMessageBox.warning(self, "Import Failed", f"Could not import templates:\n{exc}")
            return
        target_scope = "project" if self._current_scope == "project" else "global"
        if self._current_scope == "builtin":
            idx = self.scope_combo.findData("global")
            if idx >= 0:
                self.scope_combo.setCurrentIndex(idx)
        for tpl in imported:
            cloned = copy.deepcopy(tpl)
            cloned.is_builtin = False
            self._persist_template(cloned, target_scope)
        self._refresh_list()
        QMessageBox.information(
            self,
            "Templates Imported",
            f"Imported {len(imported)} template(s) into {target_scope.title()} library.",
        )

    def _on_export_clicked(self) -> None:
        templates = self._active_templates()
        if not templates:
            QMessageBox.information(self, "Export Templates", "No templates in current scope.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export NetViz Templates",
            "custom.netviz-templates.json",
            "NetViz Templates (*.netviz-templates.json);;JSON (*.json)",
        )
        if not path:
            return
        try:
            export_templates_json(templates, Path(path))
        except Exception as exc:
            QMessageBox.warning(self, "Export Failed", f"Could not export templates:\n{exc}")
            return
        QMessageBox.information(
            self,
            "Templates Exported",
            f"Exported {len(templates)} template(s) to {path}.",
        )
