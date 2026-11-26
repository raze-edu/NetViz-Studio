"""MainWindow hosting Tabs 1-4, Menus, Toolbar, Validation Dock, Ctrl+K, Trace Bar & Sync."""

from __future__ import annotations

import copy
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDockWidget,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from desktop.config import (
    AppConfig,
    add_recent_file,
    load_config,
    load_global_templates,
    save_config,
)
from desktop.exporters import export_project_pdf, export_project_png, export_project_svg
from desktop.lan_sync import LanSyncDialog, LanSyncServer
from desktop.state import ProjectState
from desktop.tile_server import TileCacheServer
from desktop.ui.dialogs.command_palette import CommandPaletteDialog, CommandTarget
from desktop.ui.dialogs.settings_dialog import SettingsDialog
from desktop.ui.dialogs.template_manager import TemplateManagerDialog
from desktop.ui.dialogs.validation_panel import ValidationPanelWidget
from desktop.ui.logical_tab.logical_view import LogicalView
from desktop.ui.map_tab.map_building_view import MapBuildingView
from desktop.ui.physical_tab.physical_view import PhysicalMappingView
from desktop.ui.survey_tab.survey_view import SurveyView
from shared.bundle_io import import_survey_session
from shared.models import SurveySession
from shared.sample_project import build_sample_campus_project
from shared.validator import ValidationIssue


class MainWindow(QMainWindow):
    """Main NetViz Studio v2.1 Desktop Window uniting all 4 live-linked tabs."""

    def __init__(
        self,
        state: ProjectState | None = None,
        *,
        config: AppConfig | None = None,
        offline_mode: bool = False,
        enable_web_view: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.state = state if state is not None else ProjectState()
        self._owns_state = state is None
        self.config = config if config is not None else load_config()
        self.offline_mode = bool(offline_mode)
        self.enable_web_view = bool(enable_web_view)

        self.tile_server = TileCacheServer(
            self.state.ensure_tiles_db_path(),
            offline_mode=self.offline_mode,
        )
        self.tile_server.start()

        self.lan_sync_server: LanSyncServer | None = None
        self.setMinimumSize(1180, 760)

        self._build_central_tabs()
        self._build_validation_dock()
        self._build_actions_and_menus()
        self._build_main_toolbar()
        self._build_status_bar()
        self._wire_signals()
        self._update_window_title()
        self._rebuild_recent_files_menu()

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _build_central_tabs(self) -> None:
        host = QWidget(self)
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.tabs = QTabWidget(host)
        self.logical_view = LogicalView(self.state, config=self.config, parent=self.tabs)
        self.map_view = MapBuildingView(
            self.state,
            tile_server=self.tile_server,
            config=self.config,
            offline_mode=self.offline_mode,
            enable_web_view=self.enable_web_view,
            parent=self.tabs,
        )
        self.physical_view = PhysicalMappingView(self.state, parent=self.tabs)
        self.survey_view = SurveyView(self.state, parent=self.tabs)

        self.tabs.addTab(self.logical_view, "1. Logical Network")
        self.tabs.addTab(self.map_view, "2. Geo Map & Floors")
        self.tabs.addTab(self.physical_view, "3. Physical Mapping & BOM")
        self.tabs.addTab(self.survey_view, "4. Wi-Fi Survey & Heatmap")
        layout.addWidget(self.tabs, 1)

        # Persistent global Connection Trace Bar synced across Tab 1 & Tab 3
        trace_bar = QFrame(host)
        trace_bar.setStyleSheet(
            "background-color: #0F172A; border-top: 1px solid #334155; padding: 3px 10px;"
        )
        trace_row = QHBoxLayout(trace_bar)
        trace_row.setContentsMargins(6, 2, 6, 2)
        prefix = QLabel("CONNECTION TRACE:", trace_bar)
        prefix.setStyleSheet("color: #38BDF8; font-weight: 700; font-size: 11px;")
        self.global_trace_label = QLabel(
            "Hover any port, cable, or bundle in Tab 1 or Tab 3 to trace its connection",
            trace_bar,
        )
        self.global_trace_label.setStyleSheet("color: #CBD5E1; font-size: 12px;")
        trace_row.addWidget(prefix)
        trace_row.addWidget(self.global_trace_label, 1)
        layout.addWidget(trace_bar)

        self.setCentralWidget(host)

    def _build_validation_dock(self) -> None:
        self.validation_panel = ValidationPanelWidget(self.state, self)
        self.validation_dock = QDockWidget("Validation Issues (9 Rules)", self)
        self.validation_dock.setObjectName("ValidationDock")
        self.validation_dock.setWidget(self.validation_panel)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.validation_dock)

    def _build_actions_and_menus(self) -> None:
        mb = self.menuBar()

        # --- File Menu ---
        file_menu = mb.addMenu("&File")
        self.act_new = QAction("New Project", self)
        self.act_new.setShortcut(QKeySequence.StandardKey.New)
        self.act_new.triggered.connect(self.new_project)
        file_menu.addAction(self.act_new)

        self.act_open = QAction("Open .netviz...", self)
        self.act_open.setShortcut(QKeySequence.StandardKey.Open)
        self.act_open.triggered.connect(self.open_project_dialog)
        file_menu.addAction(self.act_open)

        self.recent_menu = QMenu("Recent Files", self)
        file_menu.addMenu(self.recent_menu)

        self.act_save = QAction("Save", self)
        self.act_save.setShortcut(QKeySequence.StandardKey.Save)
        self.act_save.triggered.connect(self.save_project)
        file_menu.addAction(self.act_save)

        self.act_save_as = QAction("Save As...", self)
        self.act_save_as.setShortcut(QKeySequence.StandardKey.SaveAs)
        self.act_save_as.triggered.connect(self.save_project_as_dialog)
        file_menu.addAction(self.act_save_as)

        file_menu.addSeparator()
        self.act_load_sample = QAction("Load Sample Campus Project", self)
        self.act_load_sample.triggered.connect(self.load_sample_campus_project)
        file_menu.addAction(self.act_load_sample)

        self.act_import_survey = QAction("Import Survey (.netviz-survey.json)...", self)
        self.act_import_survey.triggered.connect(self.import_survey_dialog)
        file_menu.addAction(self.act_import_survey)

        export_menu = file_menu.addMenu("Export")
        self.act_export_png = QAction("Export High-DPI PNG...", self)
        self.act_export_png.triggered.connect(lambda: self.export_png())
        self.act_export_svg = QAction("Export Vector SVG...", self)
        self.act_export_svg.triggered.connect(lambda: self.export_svg())
        self.act_export_pdf = QAction("Export PDF Engineering Report...", self)
        self.act_export_pdf.triggered.connect(lambda: self.export_pdf())
        export_menu.addAction(self.act_export_png)
        export_menu.addAction(self.act_export_svg)
        export_menu.addAction(self.act_export_pdf)

        file_menu.addSeparator()
        self.act_settings = QAction("Settings...", self)
        self.act_settings.setShortcut(QKeySequence("Ctrl+,"))
        self.act_settings.triggered.connect(self.open_settings_dialog)
        file_menu.addAction(self.act_settings)

        self.act_exit = QAction("Exit", self)
        self.act_exit.triggered.connect(self.close)
        file_menu.addAction(self.act_exit)

        # --- Edit Menu ---
        edit_menu = mb.addMenu("&Edit")
        self.act_undo = self.state.undo_stack.createUndoAction(self, "Undo")
        self.act_undo.setShortcut(QKeySequence.StandardKey.Undo)
        self.act_redo = self.state.undo_stack.createRedoAction(self, "Redo")
        self.act_redo.setShortcuts([QKeySequence.StandardKey.Redo, QKeySequence("Ctrl+Y")])
        edit_menu.addAction(self.act_undo)
        edit_menu.addAction(self.act_redo)
        edit_menu.addSeparator()

        self.act_palette = QAction("Command Palette...", self)
        self.act_palette.setShortcut(QKeySequence("Ctrl+K"))
        self.act_palette.triggered.connect(self.open_command_palette)
        edit_menu.addAction(self.act_palette)

        # --- View Menu ---
        view_menu = mb.addMenu("&View")
        for idx, title in enumerate(
            (
                "Tab 1: Logical Network",
                "Tab 2: Geo Map & Floors",
                "Tab 3: Physical Mapping & BOM",
                "Tab 4: Wi-Fi Survey & Heatmap",
            )
        ):
            act = QAction(title, self)
            act.setShortcut(QKeySequence(f"Ctrl+{idx + 1}"))
            act.triggered.connect(
                lambda _chk=False, tab_idx=idx: self.tabs.setCurrentIndex(tab_idx)
            )
            view_menu.addAction(act)
        view_menu.addSeparator()

        self.act_toggle_validation = self.validation_dock.toggleViewAction()
        self.act_toggle_validation.setText("Validation Panel")
        view_menu.addAction(self.act_toggle_validation)

        self.act_toggle_minimap = QAction("Toggle Logical Mini-Map (M)", self)
        self.act_toggle_minimap.triggered.connect(self.logical_view.minimap_btn.toggle)
        view_menu.addAction(self.act_toggle_minimap)

        # --- Tools Menu ---
        tools_menu = mb.addMenu("&Tools")
        self.act_lan_sync = QAction("LAN / USB Mobile Sync...", self)
        self.act_lan_sync.triggered.connect(self.open_lan_sync_dialog)
        tools_menu.addAction(self.act_lan_sync)

        self.act_templates = QAction("Template Manager...", self)
        self.act_templates.triggered.connect(self.open_template_manager)
        tools_menu.addAction(self.act_templates)

        self.act_cache_tiles = QAction("Cache Offline Map Tiles", self)
        self.act_cache_tiles.triggered.connect(self._trigger_tile_prefetch)
        tools_menu.addAction(self.act_cache_tiles)

        # --- Help Menu ---
        help_menu = mb.addMenu("&Help")
        act_about = QAction("About NetViz Studio", self)
        act_about.triggered.connect(self._show_about_dialog)
        help_menu.addAction(act_about)

        # Global shortcuts
        QShortcut(QKeySequence("Ctrl+K"), self, activated=self.open_command_palette)

    def _build_main_toolbar(self) -> None:
        tb = QToolBar("Main Toolbar", self)
        tb.setObjectName("MainToolbar")
        tb.setMovable(False)

        tb.addAction(self.act_new)
        tb.addAction(self.act_open)
        tb.addAction(self.act_save)
        tb.addSeparator()

        sample_btn = QPushButton("Sample Campus", self)
        sample_btn.clicked.connect(self.load_sample_campus_project)
        tb.addWidget(sample_btn)

        sync_btn = QPushButton("LAN / USB Sync", self)
        sync_btn.setProperty("primary", True)
        sync_btn.clicked.connect(self.open_lan_sync_dialog)
        tb.addWidget(sync_btn)

        tpl_btn = QPushButton("Templates", self)
        tpl_btn.clicked.connect(self.open_template_manager)
        tb.addWidget(tpl_btn)

        pdf_btn = QPushButton("Export PDF", self)
        pdf_btn.clicked.connect(lambda: self.export_pdf())
        tb.addWidget(pdf_btn)
        tb.addSeparator()

        palette_btn = QPushButton("Search (Ctrl+K)", self)
        palette_btn.clicked.connect(self.open_command_palette)
        tb.addWidget(palette_btn)

        val_btn = QPushButton("Validation", self)
        val_btn.setCheckable(True)
        val_btn.setChecked(True)
        val_btn.toggled.connect(self.validation_dock.setVisible)
        self.validation_dock.visibilityChanged.connect(val_btn.setChecked)
        tb.addWidget(val_btn)

        settings_btn = QPushButton("⚙ Settings", self)
        settings_btn.clicked.connect(self.open_settings_dialog)
        tb.addWidget(settings_btn)

        self.addToolBar(tb)

    def _build_status_bar(self) -> None:
        sb = self.statusBar()
        self.status_hint_label = QLabel("Ready", self)
        sb.addWidget(self.status_hint_label, 1)

    def _wire_signals(self) -> None:
        self.state.dirty_changed.connect(lambda _dirty: self._update_window_title())
        self.state.project_reloaded.connect(self._on_project_reloaded)
        self.state.status_message.connect(self._show_status_message)
        self.state.autosave_completed.connect(
            lambda path: self._show_status_message(f"Autosaved recovery snapshot: {path}", 2500)
        )

        self.logical_view.canvas.connection_trace_changed.connect(
            self.global_trace_label.setText
        )
        self.physical_view.canvas.connection_trace_changed.connect(
            self.global_trace_label.setText
        )
        self.validation_panel.issue_activated.connect(self.jump_to_validation_issue)

    # ------------------------------------------------------------------
    # State & Project Actions
    # ------------------------------------------------------------------

    def _show_status_message(self, text: str, timeout_ms: int = 4000) -> None:
        self.status_hint_label.setText(text)
        self.statusBar().showMessage(text, timeout_ms)

    def _update_window_title(self) -> None:
        name = self.state.project.name or "Untitled Network"
        star = " *" if self.state.dirty else ""
        path_suffix = f" — {self.state.project_path.name}" if self.state.project_path else ""
        self.setWindowTitle(f"NetViz Studio v2.1 — {name}{path_suffix}{star}")

    def _on_project_reloaded(self) -> None:
        self.tile_server.set_tiles_db_path(self.state.ensure_tiles_db_path())
        self._update_window_title()
        self.validation_panel.refresh()

    def _rebuild_recent_files_menu(self) -> None:
        self.recent_menu.clear()
        if not self.config.recent_files:
            empty = QAction("(No recent files)", self)
            empty.setEnabled(False)
            self.recent_menu.addAction(empty)
            return
        for raw_path in self.config.recent_files:
            act = QAction(raw_path, self)
            act.triggered.connect(lambda _chk=False, p=raw_path: self.open_project(p))
            self.recent_menu.addAction(act)

    def new_project(self) -> None:
        self.state.new_project()
        self._show_status_message("Created new blank project.", 3000)

    def load_sample_campus_project(self) -> None:
        """Load the built-in Berlin Sample Campus Project into ProjectState."""
        sample = build_sample_campus_project()
        self.state.load_project(sample)
        self._show_status_message(
            f"Loaded Sample Campus Project ({len(sample.components)} devices, "
            f"{len(sample.buildings)} buildings).",
            5000,
        )

    def open_project(self, path: str | Path) -> bool:
        src = Path(path)
        if not src.is_file():
            QMessageBox.warning(self, "File Not Found", f"Project file not found:\n{src}")
            return False
        try:
            self.state.load_from_file(src)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Open Failed", f"Could not open {src}:\n{exc}")
            return False
        add_recent_file(self.config, src)
        save_config(self.config)
        self._rebuild_recent_files_menu()
        self._show_status_message(f"Opened {src.name}", 4000)
        return True

    def open_project_dialog(self) -> None:
        start_dir = self.config.last_project_dir or ""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open NetViz Project",
            start_dir,
            "NetViz Archives (*.netviz);;All Files (*.*)",
        )
        if path:
            self.open_project(path)

    def save_project(self) -> bool:
        if self.state.project_path is None:
            return self.save_project_as_dialog()
        saved = self.state.save_to_file(self.state.project_path)
        add_recent_file(self.config, saved)
        save_config(self.config)
        self._rebuild_recent_files_menu()
        self._update_window_title()
        self._show_status_message(f"Saved {saved.name}", 3000)
        return True

    def save_project_as_dialog(self) -> bool:
        start_dir = self.config.last_project_dir or ""
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save NetViz Project",
            start_dir,
            "NetViz Archives (*.netviz)",
        )
        if not path:
            return False
        dest = Path(path)
        if dest.suffix.lower() != ".netviz":
            dest = dest.with_suffix(".netviz")
        saved = self.state.save_to_file(dest)
        add_recent_file(self.config, saved)
        save_config(self.config)
        self._rebuild_recent_files_menu()
        self._update_window_title()
        self._show_status_message(f"Saved {saved.name}", 3000)
        return True

    def import_survey_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Field Survey Session",
            self.config.last_project_dir or "",
            "NetViz Survey (*.netviz-survey.json *.json)",
        )
        if not path:
            return
        try:
            session = import_survey_session(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Import Failed", f"Could not import survey:\n{exc}")
            return
        self.handle_received_survey(session)

    # ------------------------------------------------------------------
    # Exporters
    # ------------------------------------------------------------------

    def export_png(self, output_path: str | Path | None = None) -> Path | None:
        dest = Path(output_path) if output_path is not None else None
        if dest is None:
            chosen, _ = QFileDialog.getSaveFileName(
                self,
                "Export High-DPI PNG",
                self.config.last_project_dir or "",
                "PNG Image (*.png)",
            )
            if not chosen:
                return None
            dest = Path(chosen)
        result = export_project_png(self.state.project, dest)
        self._show_status_message(f"Exported PNG: {result.name}", 4000)
        return result

    def export_svg(self, output_path: str | Path | None = None) -> Path | None:
        dest = Path(output_path) if output_path is not None else None
        if dest is None:
            chosen, _ = QFileDialog.getSaveFileName(
                self,
                "Export Vector SVG",
                self.config.last_project_dir or "",
                "SVG Diagram (*.svg)",
            )
            if not chosen:
                return None
            dest = Path(chosen)
        result = export_project_svg(self.state.project, dest)
        self._show_status_message(f"Exported SVG: {result.name}", 4000)
        return result

    def export_pdf(self, output_path: str | Path | None = None) -> Path | None:
        dest = Path(output_path) if output_path is not None else None
        if dest is None:
            chosen, _ = QFileDialog.getSaveFileName(
                self,
                "Export Engineering PDF Report",
                self.config.last_project_dir or "",
                "PDF Document (*.pdf)",
            )
            if not chosen:
                return None
            dest = Path(chosen)
        result = export_project_pdf(self.state.project, dest)
        self._show_status_message(f"Exported PDF report: {result.name}", 4000)
        return result

    # ------------------------------------------------------------------
    # Dialogs & LAN Sync
    # ------------------------------------------------------------------

    def open_settings_dialog(self) -> int:
        dlg = SettingsDialog(
            config=copy.deepcopy(self.config),
            state=self.state,
            parent=self,
        )
        dlg.settings_changed.connect(self._on_settings_changed)
        return dlg.exec()

    def _on_settings_changed(self, updated: AppConfig) -> None:
        self.config = updated
        self.map_view.reload_map_settings(updated)
        self._show_status_message("Settings saved and map reloaded.", 3500)

    def open_template_manager(self) -> int:
        dlg = TemplateManagerDialog(self.state, parent=self)
        return dlg.exec()

    def ensure_lan_sync_server(self) -> LanSyncServer:
        if self.lan_sync_server is None:
            self.lan_sync_server = LanSyncServer(parent=self)
            self.lan_sync_server.survey_received.connect(self.handle_received_survey)
        return self.lan_sync_server

    def open_lan_sync_dialog(self) -> int:
        server = self.ensure_lan_sync_server()
        dlg = LanSyncDialog(self.state, server=server, parent=self)
        return dlg.exec()

    def handle_received_survey(self, session: SurveySession) -> None:
        """Import a SurveySession from LAN Sync or file import into ProjectState & Tab 4."""
        self.state.import_survey_session(session)
        self.survey_view.show_imported_toast(session.name, len(session.samples))
        if session.samples:
            first = session.samples[0]
            if first.building_id and first.floor_id:
                self.survey_view.focus_floor(first.building_id, first.floor_id)
        self._show_status_message(
            f"Imported survey '{session.name}' ({len(session.samples)} samples).",
            5000,
        )

    def _trigger_tile_prefetch(self) -> None:
        self.tabs.setCurrentIndex(1)
        self.map_view._on_cache_tiles_clicked()

    def _show_about_dialog(self) -> None:
        QMessageBox.about(
            self,
            "About NetViz Studio v2.1",
            "<b>NetViz Studio v2.1</b><br>"
            "Offline-first Logical/Physical Network CAD, Geo-Mapping, "
            "and Wi-Fi Field Survey Suite.",
        )

    # ------------------------------------------------------------------
    # Command Palette (Ctrl+K) & Validation Jump Routing
    # ------------------------------------------------------------------

    def open_command_palette(self) -> int:
        dlg = CommandPaletteDialog(
            self.state.project,
            global_templates=load_global_templates(),
            parent=self,
        )
        dlg.target_selected.connect(self.jump_to_target)
        return dlg.exec()

    def jump_to_target(self, target: CommandTarget) -> None:
        """Switch to the appropriate tab and select/focus the CommandTarget."""
        if target.kind == "component":
            self.tabs.setCurrentIndex(0)
            self.state.set_selection("component", target.entity_id)
            self.logical_view.canvas.jump_to_and_pulse(target.entity_id)
            self._show_status_message(f"Jumped to component '{target.title}'", 3000)
            return

        if target.kind == "cable":
            self.tabs.setCurrentIndex(0)
            self.state.set_selection("cable", target.entity_id)
            item = self.logical_view.canvas.get_cable_item(target.entity_id)
            if item is not None:
                self.logical_view.canvas.scene().clearSelection()
                item.setSelected(True)
                self.logical_view.canvas.centerOn(item)
            self._show_status_message(f"Selected cable '{target.title}'", 3000)
            return

        if target.kind == "template":
            self.tabs.setCurrentIndex(0)
            self.logical_view.palette.search_edit.setText(target.title)
            self._show_status_message(f"Filtered palette for template '{target.title}'", 3000)
            return

        if target.kind == "building":
            self.tabs.setCurrentIndex(1)
            self.state.selected_building_id = target.building_id
            self.state.set_selection("building", target.building_id)
            self.map_view.show_geo_map()
            self.map_view._refresh_building_tree()
            self._show_status_message(f"Focused building '{target.title}'", 3000)
            return

        if target.kind in ("floor", "room"):
            self.tabs.setCurrentIndex(1)
            self.map_view.show_floor_editor(target.building_id, target.floor_id)
            self._show_status_message(f"Opened floor editor for '{target.title}'", 3000)
            return

        if target.kind == "action":
            self._execute_palette_action(target.action_id)

    def _execute_palette_action(self, action_id: str) -> None:
        dispatch = {
            "action:tab_logical": lambda: self.tabs.setCurrentIndex(0),
            "action:tab_map": lambda: self.tabs.setCurrentIndex(1),
            "action:tab_physical": lambda: self.tabs.setCurrentIndex(2),
            "action:tab_survey": lambda: self.tabs.setCurrentIndex(3),
            "action:load_sample": self.load_sample_campus_project,
            "action:new_project": self.new_project,
            "action:open_project": self.open_project_dialog,
            "action:save_project": self.save_project,
            "action:lan_sync": self.open_lan_sync_dialog,
            "action:template_manager": self.open_template_manager,
            "action:settings": self.open_settings_dialog,
            "action:export_pdf": self.export_pdf,
            "action:export_png": self.export_png,
            "action:export_svg": self.export_svg,
        }
        handler = dispatch.get(action_id)
        if handler is not None:
            handler()

    def jump_to_validation_issue(self, issue: ValidationIssue) -> None:
        """Jump directly to the entity flagged by a ValidationIssue."""
        if issue.entity_type == "component":
            self.jump_to_target(
                CommandTarget(
                    kind="component",
                    title=issue.rule_code,
                    subtitle=issue.message,
                    search_text="",
                    entity_id=issue.entity_id,
                )
            )
            return

        if issue.entity_type == "port":
            comp_id = issue.extra.get("component_id", "")
            if comp_id:
                self.jump_to_target(
                    CommandTarget(
                        kind="component",
                        title=issue.rule_code,
                        subtitle=issue.message,
                        search_text="",
                        entity_id=comp_id,
                    )
                )
            return

        if issue.entity_type == "cable":
            self.jump_to_target(
                CommandTarget(
                    kind="cable",
                    title=issue.rule_code,
                    subtitle=issue.message,
                    search_text="",
                    entity_id=issue.entity_id,
                )
            )
            return

        if issue.entity_type == "building":
            self.jump_to_target(
                CommandTarget(
                    kind="building",
                    title=issue.rule_code,
                    subtitle=issue.message,
                    search_text="",
                    entity_id=issue.entity_id,
                    building_id=issue.entity_id,
                )
            )
            return

        if issue.entity_type == "door":
            b_id = issue.extra.get("building_id", "")
            f_id = issue.extra.get("floor_id", "")
            if b_id and f_id:
                self.tabs.setCurrentIndex(1)
                self.map_view.show_floor_editor(b_id, f_id)
                self._show_status_message(issue.message, 4000)

    # ------------------------------------------------------------------
    # Clean Shutdown
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:  # type: ignore[override]
        save_config(self.config)
        self.map_view.cleanup()
        self.survey_view.close()
        if self.lan_sync_server is not None:
            self.lan_sync_server.stop()
        self.tile_server.stop()
        if self._owns_state:
            self.state.cleanup()
        super().closeEvent(event)
