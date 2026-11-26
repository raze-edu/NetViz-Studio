"""Component Inspector: attributes, interactive Port Patch Table, Bulk Ports, Template save."""

from __future__ import annotations

import copy

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from desktop.config import load_global_templates, save_global_templates
from desktop.state import ProjectState
from desktop.ui.dialogs.template_manager import BulkPortBlockDialog
from desktop.ui.theme import PORT_TYPE_COLORS, component_color
from shared.models import Component, Port, PortType, new_id
from shared.presets import template_from_component
from shared.validator import are_port_types_compatible


class _QuickConnectDialog(QDialog):
    """Quick target port picker dialog for 1-click port connection in the Inspector."""

    def __init__(
        self,
        state: ProjectState,
        source_comp: Component,
        source_port: Port,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Connect {source_comp.name}[{source_port.label}]")
        self.setMinimumWidth(420)
        self._state = state
        self._source_comp = source_comp
        self._source_port = source_port

        root = QVBoxLayout(self)
        root.addWidget(
            QLabel(
                f"Source: <b>{source_comp.name}</b> [{source_port.label} · "
                f"{source_port.port_type.value}]",
                self,
            )
        )
        self.filter_edit = QLineEdit(self)
        self.filter_edit.setPlaceholderText("Filter target component or port...")
        self.filter_edit.textChanged.connect(self._populate)
        root.addWidget(self.filter_edit)

        self.target_combo = QComboBox(self)
        root.addWidget(self.target_combo)

        self.compat_label = QLabel("", self)
        self.compat_label.setWordWrap(True)
        root.addWidget(self.compat_label)
        self.target_combo.currentIndexChanged.connect(self._update_compat_hint)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel_btn = QPushButton("Cancel", self)
        cancel_btn.clicked.connect(self.reject)
        self.connect_btn = QPushButton("Connect", self)
        self.connect_btn.setProperty("primary", True)
        self.connect_btn.clicked.connect(self.accept)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(self.connect_btn)
        root.addLayout(btn_row)

        self._populate()

    def _populate(self) -> None:
        query = self.filter_edit.text().strip().lower()
        self.target_combo.clear()
        for comp in self._state.project.components:
            for port in comp.ports:
                if comp.id == self._source_comp.id and port.id == self._source_port.id:
                    continue
                if self._state.project.is_port_occupied(comp.id, port.id):
                    continue
                label = f"{comp.name} [{port.label} · {port.port_type.value}]"
                if query and query not in label.lower():
                    continue
                self.target_combo.addItem(label, userData=(comp.id, port.id))
        self.connect_btn.setEnabled(self.target_combo.count() > 0)
        self._update_compat_hint()

    def _update_compat_hint(self) -> None:
        data = self.target_combo.currentData()
        if not data:
            self.compat_label.setText("No available free ports.")
            self.compat_label.setStyleSheet("color: #94A3B8;")
            return
        comp_id, port_id = data
        target_port = self._state.project.get_port(comp_id, port_id)
        if target_port is None:
            return
        if are_port_types_compatible(self._source_port.port_type, target_port.port_type):
            self.compat_label.setText("Media types are compatible.")
            self.compat_label.setStyleSheet("color: #22C55E;")
        else:
            self.compat_label.setText(
                f"Warning: Media mismatch ({self._source_port.port_type.value} ↔ "
                f"{target_port.port_type.value}) will be flagged in Validation Panel."
            )
            self.compat_label.setStyleSheet("color: #F59E0B;")

    def selected_target(self) -> tuple[str, str] | None:
        data = self.target_combo.currentData()
        return (str(data[0]), str(data[1])) if data else None


class _SaveAsTemplateDialog(QDialog):
    """Prompt dialog for saving a component as a reusable ComponentTemplate."""

    def __init__(self, component: Component, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Save Component as Template")
        self.setMinimumWidth(380)
        root = QVBoxLayout(self)
        form = QFormLayout()

        self.name_edit = QLineEdit(f"{component.name} Template", self)
        form.addRow("Template Name:", self.name_edit)

        self.category_edit = QLineEdit("Custom", self)
        form.addRow("Category:", self.category_edit)

        self.desc_edit = QLineEdit("", self)
        self.desc_edit.setPlaceholderText("Optional description...")
        form.addRow("Description:", self.desc_edit)

        self.scope_combo = QComboBox(self)
        self.scope_combo.addItem("Both Global & Project", userData="both")
        self.scope_combo.addItem("Global Library Only", userData="global")
        self.scope_combo.addItem("Current Project Only", userData="project")
        form.addRow("Save To:", self.scope_combo)

        root.addLayout(form)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel_btn = QPushButton("Cancel", self)
        cancel_btn.clicked.connect(self.reject)
        ok_btn = QPushButton("Save Template", self)
        ok_btn.setProperty("primary", True)
        ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)
        root.addLayout(btn_row)


class _AddSinglePortDialog(QDialog):
    """Modal dialog for adding a single Port to a Component."""

    def __init__(self, default_label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Port")
        self.setMinimumWidth(320)
        root = QVBoxLayout(self)
        form = QFormLayout()

        self.label_edit = QLineEdit(default_label, self)
        form.addRow("Label:", self.label_edit)

        self.type_combo = QComboBox(self)
        for pt in PortType:
            self.type_combo.addItem(pt.value, userData=pt)
        idx = self.type_combo.findData(PortType.BASE1000)
        if idx >= 0:
            self.type_combo.setCurrentIndex(idx)
        form.addRow("Port Type:", self.type_combo)

        self.custom_type_edit = QLineEdit(self)
        self.custom_type_edit.setPlaceholderText("Only for Custom type")
        form.addRow("Custom Type Name:", self.custom_type_edit)

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
        ok_btn = QPushButton("Add Port", self)
        ok_btn.setProperty("primary", True)
        ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)
        root.addLayout(btn_row)

    def build_port(self) -> Port:
        speed_val = self.speed_spin.value()
        raw_pt = self.type_combo.currentData()
        ptype = PortType(raw_pt) if raw_pt is not None else PortType.BASE1000
        return Port(
            id=new_id(),
            label=self.label_edit.text().strip() or "P1",
            port_type=ptype,
            custom_type_name=self.custom_type_edit.text().strip() or None,
            speed_mbps=speed_val if speed_val > 0 else None,
            poe_capable=self.poe_check.isChecked(),
        )


class ComponentInspector(QWidget):
    """Right-hand inspector for editing Component metadata, custom K/V, and Port Patch Table."""

    jump_to_component_requested = Signal(str)  # remote component_id
    media_mismatch_warning = Signal(str)

    def __init__(
        self,
        state: ProjectState,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._state = state
        self._component_id: str | None = None
        self._populating = False
        self._build_ui()

        self._state.components_changed.connect(self.refresh)
        self._state.cables_changed.connect(self.refresh)
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

        # Header summary
        header_row = QHBoxLayout()
        self.header_title = QLabel("Component Inspector", body)
        self.header_title.setStyleSheet("font-size: 14px; font-weight: 700;")
        self.util_badge = QLabel("0/0", body)
        self.util_badge.setStyleSheet(
            "background-color: #1E293B; border: 1px solid #334155; "
            "border-radius: 4px; padding: 2px 8px; font-weight: 600;"
        )
        header_row.addWidget(self.header_title, 1)
        header_row.addWidget(self.util_badge)
        root.addLayout(header_row)

        # Core properties
        props_group = QGroupBox("Device Properties", body)
        form = QFormLayout(props_group)
        form.setSpacing(6)

        self.name_edit = QLineEdit(props_group)
        self.name_edit.editingFinished.connect(self._commit_properties)
        form.addRow("Name:", self.name_edit)

        self.ip_edit = QLineEdit(props_group)
        self.ip_edit.setPlaceholderText("e.g. 10.10.1.10")
        self.ip_edit.editingFinished.connect(self._commit_properties)
        form.addRow("IP Address:", self.ip_edit)

        self.mac_edit = QLineEdit(props_group)
        self.mac_edit.setPlaceholderText("e.g. 00:11:22:33:44:55")
        self.mac_edit.editingFinished.connect(self._commit_properties)
        form.addRow("MAC Address:", self.mac_edit)

        self.freqs_edit = QLineEdit(props_group)
        self.freqs_edit.setPlaceholderText("e.g. 2.4, 5.0, 6.0")
        self.freqs_edit.editingFinished.connect(self._commit_properties)
        form.addRow("Freqs (GHz):", self.freqs_edit)

        self.ssids_edit = QLineEdit(props_group)
        self.ssids_edit.setPlaceholderText("e.g. Corp-WiFi, Guest-WiFi")
        self.ssids_edit.editingFinished.connect(self._commit_properties)
        form.addRow("SSIDs:", self.ssids_edit)

        root.addWidget(props_group)

        # Custom attributes K/V table
        attr_group = QGroupBox("Custom Attributes (Key / Value)", body)
        attr_layout = QVBoxLayout(attr_group)
        self.attr_table = QTableWidget(0, 2, attr_group)
        self.attr_table.setHorizontalHeaderLabels(["Key", "Value"])
        self.attr_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.attr_table.setMinimumHeight(100)
        self.attr_table.itemChanged.connect(self._on_attr_item_changed)
        attr_layout.addWidget(self.attr_table)

        attr_btns = QHBoxLayout()
        self.add_attr_btn = QPushButton("+ Add Key/Value", attr_group)
        self.add_attr_btn.clicked.connect(self._on_add_attr)
        self.del_attr_btn = QPushButton("Remove Row", attr_group)
        self.del_attr_btn.clicked.connect(self._on_remove_attr)
        attr_btns.addWidget(self.add_attr_btn)
        attr_btns.addWidget(self.del_attr_btn)
        attr_btns.addStretch(1)
        attr_layout.addLayout(attr_btns)
        root.addWidget(attr_group)

        # Interactive Port Patch Table
        ports_group = QGroupBox("Port Patch Table", body)
        ports_layout = QVBoxLayout(ports_group)
        self.port_table = QTableWidget(0, 5, ports_group)
        self.port_table.setHorizontalHeaderLabels(
            ["Label", "Type", "Speed / PoE", "Connected To", "Action"]
        )
        header = self.port_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.port_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.port_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.port_table.setMinimumHeight(210)
        self.port_table.itemChanged.connect(self._on_port_label_changed)
        ports_layout.addWidget(self.port_table)

        port_btns = QHBoxLayout()
        self.add_port_btn = QPushButton("+ Port", ports_group)
        self.add_port_btn.clicked.connect(self._on_add_port)
        self.bulk_ports_btn = QPushButton("Bulk Add Ports...", ports_group)
        self.bulk_ports_btn.clicked.connect(self._on_bulk_add_ports)
        self.delete_port_btn = QPushButton("Delete Port", ports_group)
        self.delete_port_btn.clicked.connect(self._on_delete_selected_port)
        port_btns.addWidget(self.add_port_btn)
        port_btns.addWidget(self.bulk_ports_btn)
        port_btns.addWidget(self.delete_port_btn)
        ports_layout.addLayout(port_btns)
        root.addWidget(ports_group, 1)

        # Component footer actions
        footer = QHBoxLayout()
        self.save_tpl_btn = QPushButton("Save Component as Template...", body)
        self.save_tpl_btn.clicked.connect(self._on_save_as_template)
        self.delete_comp_btn = QPushButton("Delete Component", body)
        self.delete_comp_btn.setStyleSheet("color: #EF4444;")
        self.delete_comp_btn.clicked.connect(self._on_delete_component)
        footer.addWidget(self.save_tpl_btn, 1)
        footer.addWidget(self.delete_comp_btn)
        root.addLayout(footer)

    def set_component(self, component_id: str | None) -> None:
        """Bind inspector to component_id and refresh controls."""
        self._component_id = component_id
        self.refresh()

    def current_component(self) -> Component | None:
        if not self._component_id:
            return None
        return self._state.project.get_component(self._component_id)

    def refresh(self) -> None:
        """Reload form and Port Patch Table from active ProjectState."""
        comp = self.current_component()
        self.setEnabled(comp is not None)
        if comp is None:
            self._populating = True
            try:
                self.header_title.setText("No Component Selected")
                self.util_badge.setText("0/0")
                self.name_edit.clear()
                self.ip_edit.clear()
                self.mac_edit.clear()
                self.freqs_edit.clear()
                self.ssids_edit.clear()
                self.attr_table.setRowCount(0)
                self.port_table.setRowCount(0)
            finally:
                self._populating = False
            return

        self._populating = True
        try:
            accent = component_color(comp.component_type).name()
            type_title = comp.component_type.value.replace("_", " ").title()
            self.header_title.setText(f"{comp.name}  ({type_title})")
            self.header_title.setStyleSheet(
                f"font-size: 14px; font-weight: 700; color: {accent};"
            )

            used = sum(
                1 for p in comp.ports if self._state.project.is_port_occupied(comp.id, p.id)
            )
            self.util_badge.setText(f"{used}/{len(comp.ports)} ports")

            self.name_edit.setText(comp.name)
            self.ip_edit.setText(comp.ip_address)
            self.mac_edit.setText(comp.mac_address)
            self.freqs_edit.setText(", ".join(str(f) for f in comp.frequencies_ghz))
            self.ssids_edit.setText(", ".join(comp.ssids))

            self.attr_table.setRowCount(0)
            for k, v in comp.custom_attributes.items():
                row = self.attr_table.rowCount()
                self.attr_table.insertRow(row)
                self.attr_table.setItem(row, 0, QTableWidgetItem(k))
                self.attr_table.setItem(row, 1, QTableWidgetItem(v))

            self.port_table.setRowCount(0)
            for port in comp.ports:
                self._insert_port_row(comp, port)
        finally:
            self._populating = False

    def _insert_port_row(self, comp: Component, port: Port) -> None:
        row = self.port_table.rowCount()
        self.port_table.insertRow(row)

        lbl_item = QTableWidgetItem(port.label)
        lbl_item.setData(Qt.ItemDataRole.UserRole, port.id)
        self.port_table.setItem(row, 0, lbl_item)

        type_str = port.custom_type_name or port.port_type.value
        type_item = QTableWidgetItem(type_str)
        type_item.setFlags(type_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        pcolor = PORT_TYPE_COLORS.get(port.port_type, "#38BDF8")
        type_item.setForeground(Qt.GlobalColor.white)
        type_item.setToolTip(f"Media type: {type_str} ({pcolor})")
        self.port_table.setItem(row, 1, type_item)

        speed_part = f"{port.speed_mbps}M" if port.speed_mbps else "—"
        poe_part = " · PoE" if port.poe_capable else ""
        speed_item = QTableWidgetItem(f"{speed_part}{poe_part}")
        speed_item.setFlags(speed_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.port_table.setItem(row, 2, speed_item)

        conn = self._state.project.get_port_connection(comp.id, port.id)
        if conn is not None:
            cable, peer_comp, peer_port = conn
            jump_btn = QPushButton(f"{peer_comp.name}[{peer_port.label}]", self.port_table)
            jump_btn.setToolTip(f"Click to jump to and pulse {peer_comp.name}")
            jump_btn.setStyleSheet(
                "text-align: left; color: #38BDF8; border: none; "
                "background: transparent; text-decoration: underline;"
            )
            peer_id = peer_comp.id
            jump_btn.clicked.connect(
                lambda _chk=False, pid=peer_id: self.jump_to_component_requested.emit(pid)
            )
            self.port_table.setCellWidget(row, 3, jump_btn)

            act_btn = QPushButton("Disconnect", self.port_table)
            cable_id = cable.id
            act_btn.clicked.connect(
                lambda _chk=False, cid=cable_id: self._state.disconnect_cable(cid)
            )
            self.port_table.setCellWidget(row, 4, act_btn)
        else:
            free_item = QTableWidgetItem("Free")
            free_item.setFlags(free_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.port_table.setItem(row, 3, free_item)

            conn_btn = QPushButton("Connect...", self.port_table)
            port_id = port.id
            conn_btn.clicked.connect(
                lambda _chk=False, pid=port_id: self._on_connect_port_clicked(pid)
            )
            self.port_table.setCellWidget(row, 4, conn_btn)

    def _commit_properties(self) -> None:
        if self._populating:
            return
        comp = self.current_component()
        if comp is None:
            return

        updated = copy.deepcopy(comp)
        new_name = self.name_edit.text().strip()
        if new_name:
            updated.name = new_name
        updated.ip_address = self.ip_edit.text().strip()
        updated.mac_address = self.mac_edit.text().strip()

        freqs: list[float] = []
        for token in self.freqs_edit.text().split(","):
            tok = token.strip()
            if tok:
                try:
                    freqs.append(float(tok))
                except ValueError:
                    continue
        updated.frequencies_ghz = freqs
        updated.ssids = [s.strip() for s in self.ssids_edit.text().split(",") if s.strip()]

        attrs: dict[str, str] = {}
        for row in range(self.attr_table.rowCount()):
            k_item = self.attr_table.item(row, 0)
            v_item = self.attr_table.item(row, 1)
            k = k_item.text().strip() if k_item else ""
            v = v_item.text().strip() if v_item else ""
            if k:
                attrs[k] = v
        updated.custom_attributes = attrs

        if updated.to_dict() != comp.to_dict():
            self._state.update_component(comp.id, updated, text=f"Edit {updated.name}")

    def _on_attr_item_changed(self, _item: QTableWidgetItem) -> None:
        if not self._populating:
            self._commit_properties()

    def _on_add_attr(self) -> None:
        self._populating = True
        try:
            row = self.attr_table.rowCount()
            self.attr_table.insertRow(row)
            self.attr_table.setItem(row, 0, QTableWidgetItem("key"))
            self.attr_table.setItem(row, 1, QTableWidgetItem("value"))
        finally:
            self._populating = False
        self._commit_properties()

    def _on_remove_attr(self) -> None:
        row = self.attr_table.currentRow()
        if row < 0:
            return
        self._populating = True
        try:
            self.attr_table.removeRow(row)
        finally:
            self._populating = False
        self._commit_properties()

    def _on_port_label_changed(self, item: QTableWidgetItem) -> None:
        if self._populating or item.column() != 0:
            return
        comp = self.current_component()
        if comp is None:
            return
        port_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
        new_label = item.text().strip()
        if not port_id or not new_label:
            return
        updated = copy.deepcopy(comp)
        for port in updated.ports:
            if port.id == port_id:
                if port.label == new_label:
                    return
                port.label = new_label
                break
        self._state.update_component(comp.id, updated, text=f"Rename Port {new_label}")

    def _on_connect_port_clicked(self, port_id: str) -> None:
        comp = self.current_component()
        if comp is None:
            return
        src_port = self._state.project.get_port(comp.id, port_id)
        if src_port is None:
            return
        dlg = _QuickConnectDialog(self._state, comp, src_port, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        target = dlg.selected_target()
        if target is None:
            return
        target_comp_id, target_port_id = target
        target_port = self._state.project.get_port(target_comp_id, target_port_id)
        cable = self._state.connect_ports(comp.id, src_port.id, target_comp_id, target_port_id)
        if cable is not None and target_port is not None:
            if not are_port_types_compatible(src_port.port_type, target_port.port_type):
                msg = (
                    f"Warning: {src_port.port_type.value} connected to "
                    f"{target_port.port_type.value} (flagged in Validation Panel)"
                )
                self.media_mismatch_warning.emit(msg)
                self._state.status_message.emit(msg, 5000)

    def _on_add_port(self) -> None:
        comp = self.current_component()
        if comp is None:
            return
        dlg = _AddSinglePortDialog(f"P{len(comp.ports) + 1}", self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._state.add_port_to_component(comp.id, dlg.build_port())

    def _on_bulk_add_ports(self) -> None:
        comp = self.current_component()
        if comp is None:
            return
        dlg = BulkPortBlockDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        specs = dlg.build_port_specs()
        if not specs:
            return
        updated = copy.deepcopy(comp)
        for spec in specs:
            updated.ports.append(
                Port(
                    id=new_id(),
                    label=spec.label,
                    port_type=spec.port_type,
                    custom_type_name=spec.custom_type_name,
                    speed_mbps=spec.speed_mbps,
                    poe_capable=spec.poe_capable,
                )
            )
        self._state.update_component(
            comp.id, updated, text=f"Bulk Add {len(specs)} Ports to {comp.name}"
        )

    def delete_port_by_id(self, port_id: str) -> None:
        """Delete a port and any attached cable inside a single composite QUndoCommand macro."""
        comp = self.current_component()
        if comp is None:
            return
        port = self._state.project.get_port(comp.id, port_id)
        if port is None:
            return
        conn = self._state.project.get_port_connection(comp.id, port_id)
        updated = copy.deepcopy(comp)
        updated.ports = [p for p in updated.ports if p.id != port_id]

        self._state.undo_stack.beginMacro(f"Delete Port {port.label}")
        try:
            if conn is not None:
                cable, _peer_comp, _peer_port = conn
                self._state.disconnect_cable(cable.id)
            self._state.update_component(comp.id, updated, text=f"Remove Port {port.label}")
        finally:
            self._state.undo_stack.endMacro()

    def _on_delete_selected_port(self) -> None:
        row = self.port_table.currentRow()
        if row < 0:
            return
        item = self.port_table.item(row, 0)
        if item is None:
            return
        port_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if port_id:
            self.delete_port_by_id(port_id)

    def _on_save_as_template(self) -> None:
        comp = self.current_component()
        if comp is None:
            return
        dlg = _SaveAsTemplateDialog(comp, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        tpl_name = dlg.name_edit.text().strip() or f"{comp.name} Template"
        category = dlg.category_edit.text().strip() or "Custom"
        description = dlg.desc_edit.text().strip()
        scope = str(dlg.scope_combo.currentData() or "both")

        tpl = template_from_component(
            comp,
            template_name=tpl_name,
            category=category,
            strip_instance_ids=True,
            description=description,
        )
        if scope in ("project", "both"):
            self._state.save_custom_template(tpl)
        if scope in ("global", "both"):
            global_tpls = load_global_templates()
            global_tpls.append(copy.deepcopy(tpl))
            save_global_templates(global_tpls)
            self._state.templates_changed.emit()

        QMessageBox.information(
            self,
            "Template Saved",
            f"Saved template '{tpl.template_name}' ({len(tpl.port_specs)} ports).",
        )

    def _on_delete_component(self) -> None:
        comp = self.current_component()
        if comp is not None:
            self._state.remove_components([comp.id])
