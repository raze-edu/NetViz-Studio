"""Phase 8B tests: CommandPalette, ValidationPanel, MainWindow, and desktop.main CLI."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from PySide6.QtCore import Qt

from desktop.config import AppConfig, get_log_path
from desktop.main import check_crash_recovery, configure_logging, parse_args
from desktop.state import ProjectState
from desktop.ui.dialogs.command_palette import CommandPaletteDialog, CommandTarget, fuzzy_score
from desktop.ui.dialogs.validation_panel import ValidationPanelWidget
from desktop.ui.main_window import MainWindow
from shared.models import (
    Building,
    ComponentType,
    Door,
    Floor,
    Point2D,
    PositionSource,
    SurveySample,
    SurveySession,
    WifiReading,
)
from shared.presets import BUILTIN_TEMPLATES_BY_TYPE, instantiate_from_template


def test_main_window_sample_campus_all_four_tabs_and_settings_reload(
    qtbot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NETVIZ_CONFIG_DIR", str(tmp_path / "cfg"))
    state = ProjectState(autosave_enabled=False)
    win = MainWindow(state, config=AppConfig(), offline_mode=True, enable_web_view=False)
    qtbot.addWidget(win)
    try:
        win.show()
        assert win.tabs.count() == 4
        win.load_sample_campus_project()

        assert len(state.project.components) >= 9
        assert len(state.project.buildings) == 2
        assert len(win.logical_view.canvas._node_items) == len(state.project.components)
        assert win.map_view.building_tree.topLevelItemCount() == 2
        assert win.physical_view.bom_panel.total_kpi_label.text() != ""
        assert win.physical_view.bom_panel.runs_table.rowCount() > 0
        assert win.survey_view.sessions_list.count() >= 1

        # Settings reload updates MapBuildingView provider live
        updated_cfg = AppConfig(preferred_provider="leaflet_osm", google_maps_api_key="")
        win._on_settings_changed(updated_cfg)
        assert "Leaflet OSM" in win.map_view.provider_label.text()
    finally:
        win.close()
        state.cleanup()


def test_command_palette_fuzzy_search_and_jumps(
    qtbot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NETVIZ_CONFIG_DIR", str(tmp_path / "cfg"))
    assert fuzzy_score("vlan10", "Uplink VLAN 10") is not None
    assert fuzzy_score("zzzzzz", "Core-SW-01") is None

    state = ProjectState(autosave_enabled=False)
    win = MainWindow(state, config=AppConfig(), offline_mode=True, enable_web_view=False)
    qtbot.addWidget(win)
    try:
        win.load_sample_campus_project()

        # Find a component by IP, a cable by VLAN, a room by name, and a building
        target_comp = next(c for c in state.project.components if c.ip_address)
        dlg = CommandPaletteDialog(state.project, parent=win)
        qtbot.addWidget(dlg)
        dlg.target_selected.connect(win.jump_to_target)

        dlg.search_edit.setText(target_comp.ip_address)
        assert dlg.results_list.count() >= 1
        top_target = dlg.results_list.item(0).data(Qt.ItemDataRole.UserRole)
        assert isinstance(top_target, CommandTarget)
        assert top_target.kind == "component"
        assert top_target.entity_id == target_comp.id

        win.tabs.setCurrentIndex(2)
        dlg.activate_current()
        assert win.tabs.currentIndex() == 0
        assert state.selected_component_id == target_comp.id

        # Jump to a Room -> switches to Tab 2 Floor Editor
        bldg = state.project.buildings[0]
        floor0 = bldg.floors[0]
        room0 = floor0.rooms[0]
        dlg2 = CommandPaletteDialog(state.project, parent=win)
        qtbot.addWidget(dlg2)
        dlg2.target_selected.connect(win.jump_to_target)
        dlg2.search_edit.setText(room0.name)
        assert dlg2.results_list.count() >= 1
        dlg2.activate_current()
        assert win.tabs.currentIndex() == 1
        assert win.map_view.view_stack.currentIndex() == 1

        # Jump to a Cable by VLAN
        vlan_cable = next(c for c in state.project.cables if c.vlan_id)
        win.jump_to_target(
            CommandTarget(
                kind="cable",
                title=vlan_cable.label or vlan_cable.id,
                subtitle="VLAN",
                search_text="",
                entity_id=vlan_cable.id,
            )
        )
        assert win.tabs.currentIndex() == 0
        assert state.selected_cable_id == vlan_cable.id
    finally:
        win.close()
        state.cleanup()


def test_validation_panel_flags_issues_filters_and_jumps(
    qtbot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NETVIZ_CONFIG_DIR", str(tmp_path / "cfg"))
    state = ProjectState(autosave_enabled=False)
    win = MainWindow(state, config=AppConfig(), offline_mode=True, enable_web_view=False)
    qtbot.addWidget(win)
    try:
        sw1 = instantiate_from_template(BUILTIN_TEMPLATES_BY_TYPE[ComponentType.ACTIVE_SWITCH], [])
        sw1.ip_address = "10.0.0.10"
        sw2 = instantiate_from_template(
            BUILTIN_TEMPLATES_BY_TYPE[ComponentType.ACTIVE_SWITCH], [sw1.name]
        )
        sw2.ip_address = "10.0.0.10"  # DUP_IP error
        state.add_component(sw1)
        state.add_component(sw2)

        # Unanchored building with detached door
        unanchored = Building(
            name="Annex",
            geo_anchor=None,
            floors=[
                Floor(
                    name="GF",
                    outer_polygon_m=[
                        Point2D(0, 0),
                        Point2D(10, 0),
                        Point2D(10, 10),
                        Point2D(0, 10),
                    ],
                    doors=[Door(position_m=Point2D(5.0, 5.0))],  # DOOR_DETACHED
                )
            ],
        )
        state.add_building(unanchored)

        panel: ValidationPanelWidget = win.validation_panel
        issues = panel.refresh()
        codes = {i.rule_code for i in issues}
        assert "DUP_IP" in codes
        assert "BUILDING_UNANCHORED" in codes
        assert "DOOR_DETACHED" in codes

        # Filter Errors Only
        err_idx = panel.severity_combo.findData("error")
        panel.severity_combo.setCurrentIndex(err_idx)
        assert panel.tree.topLevelItemCount() >= 2
        for r in range(panel.tree.topLevelItemCount()):
            assert panel.tree.topLevelItem(r).text(0) == "ERROR"

        # Reset filter to All and click a DOOR_DETACHED item to jump to Tab 2 floor editor
        all_idx = panel.severity_combo.findData("all")
        panel.severity_combo.setCurrentIndex(all_idx)
        for r in range(panel.tree.topLevelItemCount()):
            item = panel.tree.topLevelItem(r)
            if item.text(1) == "DOOR_DETACHED":
                panel.tree.itemClicked.emit(item, 0)
                break
        assert win.tabs.currentIndex() == 1
        assert win.map_view.view_stack.currentIndex() == 1
    finally:
        win.close()
        state.cleanup()


def test_main_window_exports_png_svg_and_pdf(
    qtbot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NETVIZ_CONFIG_DIR", str(tmp_path / "cfg"))
    state = ProjectState(autosave_enabled=False)
    win = MainWindow(state, config=AppConfig(), offline_mode=True, enable_web_view=False)
    qtbot.addWidget(win)
    try:
        win.load_sample_campus_project()

        png_out = win.export_png(tmp_path / "report.png")
        svg_out = win.export_svg(tmp_path / "report.svg")
        pdf_out = win.export_pdf(tmp_path / "report.pdf")

        assert png_out is not None and png_out.is_file() and png_out.stat().st_size > 1000
        assert svg_out is not None and svg_out.is_file() and "<svg" in svg_out.read_text("utf-8")
        assert pdf_out is not None and pdf_out.is_file()
        pdf_bytes = pdf_out.read_bytes()
        assert pdf_bytes.startswith(b"%PDF") and len(pdf_bytes) > 5000
    finally:
        win.close()
        state.cleanup()


def test_main_window_lan_sync_survey_received_updates_state_and_tab4(
    qtbot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NETVIZ_CONFIG_DIR", str(tmp_path / "cfg"))
    state = ProjectState(autosave_enabled=False)
    win = MainWindow(state, config=AppConfig(), offline_mode=True, enable_web_view=False)
    qtbot.addWidget(win)
    try:
        win.load_sample_campus_project()
        initial_count = len(state.project.surveys)
        bldg = state.project.buildings[0]
        floor0 = bldg.floors[0]

        incoming = SurveySession(
            name="Pixel 9 Live Walk",
            created_utc="2026-11-26T01:00:00Z",
            device_model="Pixel 9",
            samples=[
                SurveySample(
                    timestamp_utc="2026-11-26T01:00:05Z",
                    building_id=bldg.id,
                    floor_id=floor0.id,
                    pos_m=Point2D(5.0, 5.0),
                    source=PositionSource.GPS_CALIBRATED,
                    readings=[WifiReading("Corp-WiFi", "AA:BB:CC:DD:EE:01", -49, 5180)],
                )
            ],
        )

        server = win.ensure_lan_sync_server()
        server.survey_received.emit(incoming)

        assert len(state.project.surveys) == initial_count + 1
        assert any(s.id == incoming.id for s in state.project.surveys)
        assert win.survey_view.sessions_list.count() == initial_count + 1
        assert "Pixel 9 Live Walk" in win.survey_view.toast_label.text()

        # Undo cleanly removes imported survey
        state.undo_stack.undo()
        assert len(state.project.surveys) == initial_count
    finally:
        win.close()
        state.cleanup()


def test_desktop_main_cli_logging_and_crash_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NETVIZ_CONFIG_DIR", str(tmp_path / "cfg"))
    args = parse_args(["--project", "campus.netviz", "--offline", "--debug-log"])
    assert args.project == "campus.netviz"
    assert args.offline is True
    assert args.debug_log is True

    logger = configure_logging(debug_log=True)
    assert logger.level == logging.DEBUG
    logger.info("Smoke log entry")
    for h in logger.handlers:
        h.flush()
    assert get_log_path().is_file()
    assert "Smoke log entry" in get_log_path().read_text(encoding="utf-8")
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()

    # Crash recovery check
    state_src = ProjectState(autosave_enabled=False)
    state_dst = ProjectState(autosave_enabled=False)
    try:
        state_src.new_project("Recovered Campus")
        sw = instantiate_from_template(BUILTIN_TEMPLATES_BY_TYPE[ComponentType.ACTIVE_SWITCH], [])
        state_src.add_component(sw)
        state_src.autosave_now()

        monkeypatch.setenv("NETVIZ_SKIP_RECOVERY_PROMPT", "1")
        assert check_crash_recovery(state_dst) is False

        monkeypatch.delenv("NETVIZ_SKIP_RECOVERY_PROMPT", raising=False)
        assert check_crash_recovery(state_dst, auto_accept=True) is True
        assert state_dst.project.name == "Recovered Campus"
        assert len(state_dst.project.components) == 1
        assert state_dst.dirty is True
    finally:
        state_src.cleanup()
        state_dst.cleanup()
