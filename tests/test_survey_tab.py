"""Phase 8 pytest-qt tests for Tab 4 Wi-Fi Survey Analysis & Clipped Heatmaps."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGraphicsView

from desktop.coords import bounds_m_to_scene_rect, local_m_to_scene
from desktop.state import ProjectState
from desktop.ui.survey_tab import SurveyView, ToolMode
from shared.bundle_io import export_survey_session
from shared.models import (
    Building,
    Component,
    ComponentType,
    Floor,
    GeoAnchor,
    Point2D,
    PositionSource,
    Project,
    Room,
    SurveySample,
    SurveySession,
    WifiReading,
)


def _build_survey_state() -> ProjectState:
    floor0 = Floor(
        id="floor-0",
        name="Ground Floor",
        level_index=0,
        outer_polygon_m=[
            Point2D(0.0, 0.0),
            Point2D(20.0, 0.0),
            Point2D(20.0, 10.0),
            Point2D(0.0, 10.0),
        ],
        rooms=[
            Room(
                name="Hallway",
                polygon_m=[
                    Point2D(0.0, 0.0),
                    Point2D(20.0, 0.0),
                    Point2D(20.0, 10.0),
                    Point2D(0.0, 10.0),
                ],
            )
        ],
    )
    floor1 = Floor(
        id="floor-1",
        name="1st Floor",
        level_index=1,
        outer_polygon_m=[
            Point2D(0.0, 0.0),
            Point2D(20.0, 0.0),
            Point2D(20.0, 10.0),
            Point2D(0.0, 10.0),
        ],
    )
    bldg = Building(
        id="bldg-1",
        name="Campus Alpha",
        geo_anchor=GeoAnchor(origin_lat=52.52, origin_lon=13.405, rotation_deg=15.0),
        floors=[floor0, floor1],
    )
    ap1 = Component(
        id="ap-1",
        component_type=ComponentType.ACCESS_POINT,
        name="AP-Lobby-01",
        mac_address="AA:BB:CC:DD:EE:01",
        ssids=["Corp-WiFi", "Guest-WiFi"],
        physical_building_id="bldg-1",
        physical_floor_id="floor-0",
        physical_pos_m=Point2D(10.0, 2.0),
    )
    ap2 = Component(
        id="ap-2",
        component_type=ComponentType.ACCESS_POINT,
        name="AP-North-02",
        mac_address="AA:BB:CC:DD:EE:02",
        ssids=["Corp-WiFi"],
        physical_building_id="bldg-1",
        physical_floor_id="floor-0",
        physical_pos_m=Point2D(10.0, 8.0),
    )

    sess1 = SurveySession(
        id="sess-1",
        name="Morning Walk",
        created_utc="2026-11-26T08:00:00Z",
        device_model="Pixel 9",
        samples=[
            SurveySample(
                id="smp-1",
                timestamp_utc="2026-11-26T08:01:00Z",
                building_id="bldg-1",
                floor_id="floor-0",
                pos_m=Point2D(10.0, 2.0),  # Southern half (y=2)
                source=PositionSource.MANUAL,
                readings=[
                    WifiReading(
                        ssid="Corp-WiFi",
                        bssid="aa-bb-cc-dd-ee-01",  # dashed format tests normalization
                        rssi_dbm=-38,
                        frequency_mhz=5180,
                    ),
                    WifiReading(
                        ssid="Guest-WiFi",
                        bssid="AA:BB:CC:DD:EE:01",
                        rssi_dbm=-82,
                        frequency_mhz=2412,
                    ),
                ],
            ),
            SurveySample(
                id="smp-2",
                timestamp_utc="2026-11-26T08:02:00Z",
                building_id="bldg-1",
                floor_id="floor-0",
                pos_m=Point2D(10.0, 8.0),  # Northern half (y=8)
                source=PositionSource.GPS_CALIBRATED,
                readings=[
                    WifiReading(
                        ssid="Corp-WiFi",
                        bssid="AA:BB:CC:DD:EE:02",
                        rssi_dbm=-76,
                        frequency_mhz=5180,
                    ),
                ],
            ),
        ],
    )
    sess2 = SurveySession(
        id="sess-2",
        name="Afternoon Walk",
        created_utc="2026-11-26T14:00:00Z",
        device_model="Pixel 9",
        samples=[
            SurveySample(
                id="smp-3",
                timestamp_utc="2026-11-26T14:01:00Z",
                building_id="bldg-1",
                floor_id="floor-0",
                pos_m=Point2D(16.0, 5.0),
                source=PositionSource.MANUAL,
                readings=[
                    WifiReading(
                        ssid="Corp-WiFi",
                        bssid="AA:BB:CC:DD:EE:01",
                        rssi_dbm=-55,
                        frequency_mhz=6175,
                    ),
                ],
            )
        ],
    )

    proj = Project(
        name="Survey Test",
        buildings=[bldg],
        components=[ap1, ap2],
        surveys=[sess1, sess2],
    )
    return ProjectState(proj, autosave_enabled=False)


def _wait_heatmap(qtbot, view: SurveyView, expected_req_id: int | None = None) -> None:
    if expected_req_id is None:
        expected_req_id = view._heatmap_request_id
    qtbot.waitUntil(
        lambda: (
            view.canvas.last_heatmap is not None
            and view._active_worker is not None
            and not view._active_worker.isRunning()
            and view._heatmap_request_id >= expected_req_id
        ),
        timeout=5000,
    )


def test_multi_session_merge_updates_count_and_heatmap(qtbot) -> None:
    state = _build_survey_state()
    try:
        view = SurveyView(state)
        view.resize(1200, 760)
        qtbot.addWidget(view)
        view.show()
        _wait_heatmap(qtbot, view)

        # Both sessions checked -> 3 merged samples on Floor 0
        assert view.sessions_list.count() == 2
        assert len(view.canvas.pin_items) == 3
        assert "(3 samples)" in view.coverage_kpi_label.text()
        cov_both = view.canvas.last_heatmap.coverage_percent

        # Uncheck sess-2 -> only 2 samples from sess-1
        with qtbot.waitSignal(view.heatmap_updated, timeout=5000):
            view.set_session_checked("sess-2", False)

        assert len(view.canvas.pin_items) == 2
        assert "(2 samples)" in view.coverage_kpi_label.text()
        assert view.canvas.last_heatmap.coverage_percent != cov_both

        # Re-check sess-2 -> merges back to 3 samples
        with qtbot.waitSignal(view.heatmap_updated, timeout=5000):
            view.set_session_checked("sess-2", True)
        assert len(view.canvas.pin_items) == 3
        assert "(3 samples)" in view.coverage_kpi_label.text()
    finally:
        view.close()
        state.cleanup()


def test_ssid_ap_friendly_name_band_filters_and_dead_zone_slider(qtbot) -> None:
    state = _build_survey_state()
    try:
        view = SurveyView(state)
        view.resize(1200, 760)
        qtbot.addWidget(view)
        view.show()
        _wait_heatmap(qtbot, view)

        # Specific AP / BSSID dropdown displays friendly component name via find_component_by_mac
        bssid_labels = [
            view.bssid_combo.itemText(i) for i in range(view.bssid_combo.count())
        ]
        assert any("AP-Lobby-01" in lbl for lbl in bssid_labels)
        assert any("AP-North-02" in lbl for lbl in bssid_labels)

        # Filter by Guest-WiFi (only -82 dBm at 2.4 GHz) -> 0% coverage at -70 dBm threshold
        guest_idx = view.ssid_combo.findData("Guest-WiFi")
        assert guest_idx >= 0
        with qtbot.waitSignal(view.heatmap_updated, timeout=5000):
            view.ssid_combo.setCurrentIndex(guest_idx)
        assert view.canvas.last_heatmap.coverage_percent == pytest.approx(0.0)

        # Lower Dead-Zone Threshold slider to -85 dBm -> coverage rises to 100%
        with qtbot.waitSignal(view.heatmap_updated, timeout=5000):
            view.dead_zone_slider.setValue(-85)
        assert "Coverage ≥ -85 dBm" in view.coverage_kpi_label.text()
        assert view.canvas.last_heatmap.coverage_percent > 50.0

        # Filter by 6 GHz band -> only smp-3 (-55 dBm) remains active
        all_ssid_idx = view.ssid_combo.findData(None)
        with qtbot.waitSignal(view.heatmap_updated, timeout=5000):
            view.ssid_combo.setCurrentIndex(all_ssid_idx)
        band_6_idx = view.band_combo.findData("6")
        with qtbot.waitSignal(view.heatmap_updated, timeout=5000):
            view.band_combo.setCurrentIndex(band_6_idx)
        active_pins = [p for p in view.canvas.pin_items if p.matched_rssi is not None]
        assert len(active_pins) == 1
        assert active_pins[0].sample.id == "smp-3"
    finally:
        view.close()
        state.cleanup()


def test_exact_heatmap_pixmap_scene_alignment_non_flipped(qtbot) -> None:
    state = _build_survey_state()
    try:
        view = SurveyView(state)
        view.resize(1200, 760)
        qtbot.addWidget(view)
        view.show()
        _wait_heatmap(qtbot, view)

        hm_item = view.canvas.heatmap_item
        hm_res = view.canvas.last_heatmap
        assert hm_item is not None
        assert hm_res is not None

        expected_rect = bounds_m_to_scene_rect(*hm_res.bounds)
        actual_rect = hm_item.sceneBoundingRect()
        assert actual_rect.left() == pytest.approx(expected_rect.left(), abs=1e-3)
        assert actual_rect.top() == pytest.approx(expected_rect.top(), abs=1e-3)
        assert actual_rect.width() == pytest.approx(expected_rect.width(), abs=1e-3)
        assert actual_rect.height() == pytest.approx(expected_rect.height(), abs=1e-3)

        # Verify pixel at (x=10, y=2) is green (strong -38 dBm), NOT vertically flipped
        qimg = hm_item.pixmap().toImage()
        hot_scene = local_m_to_scene(Point2D(10.0, 2.0))
        px_col = int((hot_scene.x() - actual_rect.left()) / actual_rect.width() * qimg.width())
        px_row = int((hot_scene.y() - actual_rect.top()) / actual_rect.height() * qimg.height())
        col = qimg.pixelColor(px_col, px_row)
        assert col.alpha() > 0
        assert col.green() > col.red()
    finally:
        view.close()
        state.cleanup()


def test_delete_outlier_sample_supports_undo_redo(qtbot) -> None:
    state = _build_survey_state()
    try:
        view = SurveyView(state)
        view.resize(1200, 760)
        qtbot.addWidget(view)
        view.show()
        _wait_heatmap(qtbot, view)

        assert len(view.canvas.pin_items) == 3

        with qtbot.waitSignal(view.heatmap_updated, timeout=5000):
            view.delete_sample("smp-2")

        assert len(view.canvas.pin_items) == 2
        all_ids = {s.id for sess in state.project.surveys for s in sess.samples}
        assert "smp-2" not in all_ids

        # Undo restores smp-2
        with qtbot.waitSignal(view.heatmap_updated, timeout=5000):
            state.undo_stack.undo()
        assert len(view.canvas.pin_items) == 3
        all_ids_undo = {s.id for sess in state.project.surveys for s in sess.samples}
        assert "smp-2" in all_ids_undo

        # Redo removes smp-2 again
        with qtbot.waitSignal(view.heatmap_updated, timeout=5000):
            state.undo_stack.redo()
        assert len(view.canvas.pin_items) == 2
    finally:
        view.close()
        state.cleanup()


def test_import_survey_json_toast_floor_jump_and_non_panning(qtbot, tmp_path: Path) -> None:
    state = _build_survey_state()
    try:
        view = SurveyView(state)
        view.resize(1200, 760)
        qtbot.addWidget(view)
        view.show()
        _wait_heatmap(qtbot, view)

        # Non-panning invariant + Tool toggle
        assert view.canvas.dragMode() == QGraphicsView.DragMode.NoDrag
        qtbot.keyClick(view.canvas, Qt.Key.Key_H)
        assert view.canvas.tool_mode == ToolMode.PAN
        assert view.canvas.dragMode() == QGraphicsView.DragMode.ScrollHandDrag
        qtbot.keyClick(view.canvas, Qt.Key.Key_V)
        assert view.canvas.tool_mode == ToolMode.SELECT
        assert view.canvas.dragMode() == QGraphicsView.DragMode.NoDrag

        # Import external .netviz-survey.json & verify toast banner
        new_sess = SurveySession(
            id="sess-ext",
            name="LAN Field Walk",
            created_utc="2026-11-26T16:00:00Z",
            samples=[
                SurveySample(
                    id="smp-ext-1",
                    timestamp_utc="2026-11-26T16:01:00Z",
                    building_id="bldg-1",
                    floor_id="floor-1",
                    pos_m=Point2D(8.0, 5.0),
                    source=PositionSource.GPS,
                    readings=[
                        WifiReading(
                            ssid="Corp-WiFi",
                            bssid="AA:BB:CC:DD:EE:01",
                            rssi_dbm=-48,
                            frequency_mhz=5180,
                        )
                    ],
                )
            ],
        )
        survey_file = tmp_path / "field_walk.netviz-survey.json"
        export_survey_session(new_sess, survey_file)

        with qtbot.waitSignal(view.heatmap_updated, timeout=5000):
            count = view.import_from_path(survey_file)

        assert count == 1
        assert view.sessions_list.count() == 3
        assert view.toast_banner.isVisible()
        assert "LAN Field Walk" in view.toast_label.text()
        assert "1 samples" in view.toast_label.text()

        # focus_floor jumps cleanly to Floor 1 where smp-ext-1 lives
        with qtbot.waitSignal(view.heatmap_updated, timeout=5000):
            view.focus_floor("bldg-1", "floor-1")
        assert view.active_floor_id() == "floor-1"
        assert len(view.canvas.pin_items) == 1
        assert view.canvas.pin_items[0].sample.id == "smp-ext-1"
    finally:
        view.close()
        state.cleanup()
