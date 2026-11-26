"""Searchable Component Palette with Built-In (9), Custom Templates, Drag & Double-Click."""

from __future__ import annotations

import json

from PySide6.QtCore import QMimeData, QPoint, QSize, Qt, Signal
from PySide6.QtGui import QColor, QDrag, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from desktop.config import load_global_templates
from desktop.state import ProjectState
from desktop.ui.theme import component_color
from shared.models import ComponentTemplate
from shared.presets import BUILTIN_TEMPLATES

TEMPLATE_MIME_TYPE = "application/x-netviz-template"


def _make_swatch_icon(hex_color: QColor) -> QIcon:
    pix = QPixmap(16, 16)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(hex_color)
    painter.drawRoundedRect(1, 2, 14, 12, 3, 3)
    painter.end()
    return QIcon(pix)


class _DraggableTemplateList(QListWidget):
    """QListWidget that initiates a template QDrag carrying TEMPLATE_MIME_TYPE JSON."""

    template_double_clicked = Signal(object)  # ComponentTemplate

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._press_pos = QPoint()
        self.setDragEnabled(True)
        self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.setIconSize(QSize(16, 16))
        self.setSpacing(2)
        self.itemDoubleClicked.connect(self._on_item_double_clicked)

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        tpl = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(tpl, ComponentTemplate):
            self.template_double_clicked.emit(tpl)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def startDrag(self, supportedActions) -> None:  # type: ignore[override]
        item = self.currentItem()
        if item is None:
            return
        tpl = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(tpl, ComponentTemplate):
            return
        mime = QMimeData()
        mime.setData(TEMPLATE_MIME_TYPE, json.dumps(tpl.to_dict()).encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)

        badge = QPixmap(150, 34)
        badge.fill(QColor(15, 23, 42, 220))
        painter = QPainter(badge)
        accent = component_color(tpl.component_type, tpl.accent_color_hex)
        painter.setPen(accent)
        painter.drawRect(0, 0, 149, 33)
        painter.fillRect(0, 0, 6, 34, accent)
        painter.setPen(QColor("#F8FAFC"))
        painter.drawText(12, 22, tpl.template_name[:18])
        painter.end()
        drag.setPixmap(badge)
        drag.exec(Qt.DropAction.CopyAction)


class ComponentPalette(QWidget):
    """Left palette panel showing Built-In Types (9), My Custom Templates, and + New Template."""

    template_activated = Signal(object)  # ComponentTemplate (double-click spawn)
    new_template_requested = Signal()
    manage_templates_requested = Signal()

    def __init__(
        self,
        state: ProjectState,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._state = state
        self._builtin_expanded = True
        self._custom_expanded = True
        self.setMinimumWidth(220)
        self.setMaximumWidth(310)
        self._build_ui()
        self.refresh_templates()

        self._state.templates_changed.connect(self.refresh_templates)
        self._state.project_reloaded.connect(self.refresh_templates)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        self.search_edit = QLineEdit(self)
        self.search_edit.setPlaceholderText("Search components & templates...")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self.refresh_templates)
        root.addWidget(self.search_edit)

        # Collapsible Built-In Types (9)
        self.builtin_toggle_btn = QPushButton("▼ Built-In Types (9)", self)
        self.builtin_toggle_btn.setStyleSheet("text-align: left; font-weight: 600;")
        self.builtin_toggle_btn.clicked.connect(self._toggle_builtin)
        root.addWidget(self.builtin_toggle_btn)

        self.builtin_list = _DraggableTemplateList(self)
        self.builtin_list.template_double_clicked.connect(self.template_activated.emit)
        root.addWidget(self.builtin_list, 2)

        # Collapsible My Custom Templates
        self.custom_toggle_btn = QPushButton("▼ My Custom Templates (0)", self)
        self.custom_toggle_btn.setStyleSheet("text-align: left; font-weight: 600;")
        self.custom_toggle_btn.clicked.connect(self._toggle_custom)
        root.addWidget(self.custom_toggle_btn)

        self.custom_list = _DraggableTemplateList(self)
        self.custom_list.template_double_clicked.connect(self.template_activated.emit)
        root.addWidget(self.custom_list, 1)

        # Footer action buttons
        btn_row = QHBoxLayout()
        self.new_tpl_btn = QPushButton("+ New Template", self)
        self.new_tpl_btn.setProperty("primary", True)
        self.new_tpl_btn.clicked.connect(self.new_template_requested.emit)
        self.manage_btn = QPushButton("Manage...", self)
        self.manage_btn.clicked.connect(self.manage_templates_requested.emit)
        btn_row.addWidget(self.new_tpl_btn, 1)
        btn_row.addWidget(self.manage_btn)
        root.addLayout(btn_row)

    def _toggle_builtin(self) -> None:
        self._builtin_expanded = not self._builtin_expanded
        self.builtin_list.setVisible(self._builtin_expanded)
        arrow = "▼" if self._builtin_expanded else "▶"
        self.builtin_toggle_btn.setText(f"{arrow} Built-In Types ({len(BUILTIN_TEMPLATES)})")

    def _toggle_custom(self) -> None:
        self._custom_expanded = not self._custom_expanded
        self.custom_list.setVisible(self._custom_expanded)
        self._update_custom_header(self._merged_custom_templates())

    def _merged_custom_templates(self) -> list[tuple[ComponentTemplate, str]]:
        """Merge project custom_templates and global templates.json without duplicate IDs."""
        seen_ids: set[str] = set()
        merged: list[tuple[ComponentTemplate, str]] = []
        for tpl in self._state.project.custom_templates:
            if tpl.id not in seen_ids:
                seen_ids.add(tpl.id)
                merged.append((tpl, "Project"))
        for tpl in load_global_templates():
            if tpl.id not in seen_ids:
                seen_ids.add(tpl.id)
                merged.append((tpl, "Global"))
        return merged

    def _update_custom_header(self, entries: list[tuple[ComponentTemplate, str]]) -> None:
        arrow = "▼" if self._custom_expanded else "▶"
        self.custom_toggle_btn.setText(f"{arrow} My Custom Templates ({len(entries)})")

    def _matches_query(self, tpl: ComponentTemplate, query: str) -> bool:
        if not query:
            return True
        haystack = " ".join(
            [
                tpl.template_name,
                tpl.component_type.value,
                tpl.category,
                tpl.description,
                tpl.default_name_prefix,
            ]
        ).lower()
        return query in haystack

    def refresh_templates(self) -> None:
        """Reload built-in and custom template lists filtered by current search text."""
        query = self.search_edit.text().strip().lower()

        self.builtin_list.clear()
        for tpl in BUILTIN_TEMPLATES:
            if not self._matches_query(tpl, query):
                continue
            item = QListWidgetItem(
                _make_swatch_icon(component_color(tpl.component_type, tpl.accent_color_hex)),
                f"{tpl.template_name} ({len(tpl.port_specs)}p)",
            )
            item.setToolTip(f"{tpl.description}\nDrag onto canvas or double-click to spawn.")
            item.setData(Qt.ItemDataRole.UserRole, tpl)
            self.builtin_list.addItem(item)

        custom_entries = self._merged_custom_templates()
        self._update_custom_header(custom_entries)
        self.custom_list.clear()
        for tpl, scope_label in custom_entries:
            if not self._matches_query(tpl, query):
                continue
            item = QListWidgetItem(
                _make_swatch_icon(component_color(tpl.component_type, tpl.accent_color_hex)),
                f"{tpl.template_name} [{scope_label}] ({len(tpl.port_specs)}p)",
            )
            item.setToolTip(
                f"{tpl.description or tpl.template_name}\n"
                f"Scope: {scope_label} · Drag or double-click to spawn."
            )
            item.setData(Qt.ItemDataRole.UserRole, tpl)
            self.custom_list.addItem(item)
