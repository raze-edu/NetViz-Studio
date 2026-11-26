"""pytest-qt interaction tests for Tab 1 Logical Network Visualizer (Section 4.4B & 6 #8-10)."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QGraphicsView

from desktop.config import AppConfig, load_config
from desktop.state import ProjectState
from desktop.ui.dialogs.template_manager import TemplateManagerDialog
from desktop.ui.logical_tab.graphics_items import (
    Z_CABLE,
    Z_CABLE_ACTIVE,
    Z_HANDLE,
    Z_NODE,
    Z_PORT,
    Z_PORT_PILL,
    Z_PREVIEW,
)
from desktop.ui.logical_tab.logical_view import LogicalCanvasView, LogicalView
from shared.bundle_io import export_templates_json, import_templates_json
from shared.models import Cable, ComponentType, Point2D, PortType, Project
from shared.presets import get_builtin_template, instantiate_from_template


def _send_hover_move(view: QGraphicsView, view_pos: QPoint) -> None:
    """Deliver an unpressed MouseMove event reliably regardless of OS cursor/focus."""
    vp = view.viewport()
    local_f = QPointF(view_pos)
    global_f = QPointF(vp.mapToGlobal(view_pos))
    ev = QMouseEvent(
        QEvent.Type.MouseMove,
        local_f,
        global_f,
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(vp, ev)


def _two_switch_state() -> ProjectState:
    proj = Project()
    sw_a = instantiate_from_template(
        get_builtin_template(ComponentType.ROUTER), [], pos=Point2D(80.0, 80.0)
    )
    sw_a.mac_address = "AA:BB:CC:DD:EE:01"
    sw_b = instantiate_from_template(
        get_builtin_template(ComponentType.FIREWALL), [], pos=Point2D(420.0, 220.0)
    )
    sw_b.mac_address = "AA:BB:CC:DD:EE:02"
    proj.components = [sw_a, sw_b]
    return ProjectState(proj, autosave_enabled=False)


def test_z_order_and_no_default_scroll_hand_drag(qtbot) -> None:
    state = _two_switch_state()
    c_a, c_b = state.project.components
    cab = state.connect_ports(c_a.id, c_a.ports[0].id, c_b.id, c_b.ports[0].id)
    assert cab is not None

    canvas = LogicalCanvasView(state)
    canvas.resize(900, 650)
    qtbot.addWidget(canvas)
    canvas.show()

    # Invariant #9: NEVER default ScrollHandDrag
    assert canvas.dragMode() != QGraphicsView.DragMode.ScrollHandDrag

    # Invariant #10: Exact Z-order stack
    node_a = canvas.get_node_item(c_a.id)
    port_a = canvas.get_port_item(c_a.id, c_a.ports[0].id)
    cable_item = canvas.get_cable_item(cab.id)
    assert node_a is not None and port_a is not None and cable_item is not None

    assert node_a.zValue() == Z_NODE == 20.0
    assert port_a.zValue() == Z_PORT == 40.0
    assert cable_item.zValue() == Z_CABLE == 10.0
    assert cable_item.src_pill.zValue() == Z_PORT_PILL == 50.0
    assert canvas._preview_wire.zValue() == Z_PREVIEW == 65.0
    assert canvas._lock_ring.zValue() == Z_PREVIEW == 65.0
    assert not canvas._preview_wire.isEnabled()
    assert canvas._preview_wire.acceptedMouseButtons() == Qt.MouseButton.NoButton
    assert not canvas._lock_ring.isEnabled()
    assert canvas._lock_ring.acceptedMouseButtons() == Qt.MouseButton.NoButton
    assert Z_HANDLE == 70.0


def test_drag_and_drop_port_connect_8px_off_center_without_moving_node(qtbot) -> None:
    state = _two_switch_state()
    comp_a, comp_b = state.project.components
    orig_pos_a = Point2D(comp_a.logical_pos.x, comp_a.logical_pos.y)

    canvas = LogicalCanvasView(state)
    canvas.resize(900, 650)
    qtbot.addWidget(canvas)
    canvas.show()

    port_a = canvas.get_port_item(comp_a.id, comp_a.ports[1].id)
    port_b = canvas.get_port_item(comp_b.id, comp_b.ports[1].id)
    assert port_a is not None and port_b is not None

    # Press 8 px off-center from Port A pin, drag to 8 px off-center of Port B pin
    start_view = canvas.mapFromScene(port_a.pin_scene_pos() + QPointF(8.0, 0.0))
    end_view = canvas.mapFromScene(port_b.pin_scene_pos() + QPointF(-8.0, 0.0))

    qtbot.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, pos=start_view)
    assert canvas.connect_mode == "DRAG_CONNECTING"
    qtbot.mouseMove(canvas.viewport(), pos=end_view)
    assert canvas._snap_target_port is port_b
    qtbot.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, pos=end_view)

    assert canvas.connect_mode == "IDLE"
    assert len(state.project.cables) == 1
    created = state.project.cables[0]
    assert {
        (created.source_component_id, created.source_port_id),
        (created.target_component_id, created.target_port_id),
    } == {(comp_a.id, port_a.port_id), (comp_b.id, port_b.port_id)}

    # Parent Component A MUST NOT move when dragging from its port!
    assert comp_a.logical_pos.x == pytest.approx(orig_pos_a.x)
    assert comp_a.logical_pos.y == pytest.approx(orig_pos_a.y)


def test_click_to_click_port_connect_and_escape_cancel(qtbot) -> None:
    state = _two_switch_state()
    comp_a, comp_b = state.project.components
    canvas = LogicalCanvasView(state)
    canvas.resize(900, 650)
    qtbot.addWidget(canvas)
    canvas.show()

    port_a = canvas.get_port_item(comp_a.id, comp_a.ports[0].id)
    port_b = canvas.get_port_item(comp_b.id, comp_b.ports[0].id)
    assert port_a is not None and port_b is not None

    pos_a = canvas.mapFromScene(port_a.pin_scene_pos())
    pos_b = canvas.mapFromScene(port_b.pin_scene_pos())

    # Click & release on Port A (<= 6px) -> PENDING_CLICK_CONNECT
    qtbot.mouseClick(canvas.viewport(), Qt.MouseButton.LeftButton, pos=pos_a)
    assert canvas.connect_mode == "PENDING_CLICK_CONNECT"

    # Esc cancels cleanly
    qtbot.keyClick(canvas, Qt.Key.Key_Escape)
    assert canvas.connect_mode == "IDLE"
    assert len(state.project.cables) == 0

    # Click A -> move to B -> click B commits cable
    qtbot.mouseClick(canvas.viewport(), Qt.MouseButton.LeftButton, pos=pos_a)
    assert canvas.connect_mode == "PENDING_CLICK_CONNECT"
    _send_hover_move(canvas, pos_b)
    assert canvas._snap_target_port is port_b
    qtbot.mouseClick(canvas.viewport(), Qt.MouseButton.LeftButton, pos=pos_b)

    assert canvas.connect_mode == "IDLE"
    assert len(state.project.cables) == 1


def test_occupied_port_blocks_connection_cleanly(qtbot) -> None:
    state = _two_switch_state()
    comp_a, comp_b = state.project.components
    state.connect_ports(comp_a.id, comp_a.ports[0].id, comp_b.id, comp_b.ports[0].id)

    canvas = LogicalCanvasView(state)
    canvas.resize(900, 650)
    qtbot.addWidget(canvas)
    canvas.show()

    port_a_occ = canvas.get_port_item(comp_a.id, comp_a.ports[0].id)
    port_a_free = canvas.get_port_item(comp_a.id, comp_a.ports[1].id)
    port_b_occ = canvas.get_port_item(comp_b.id, comp_b.ports[0].id)
    assert port_a_occ is not None and port_a_free is not None and port_b_occ is not None

    messages: list[str] = []
    state.status_message.connect(lambda msg, _ms: messages.append(msg))

    # Clicking occupied Port A blocks immediately without entering DRAG_CONNECTING
    qtbot.mousePress(
        canvas.viewport(),
        Qt.MouseButton.LeftButton,
        pos=canvas.mapFromScene(port_a_occ.pin_scene_pos()),
    )
    assert canvas.connect_mode == "IDLE"
    assert any("already connected" in m.lower() for m in messages)

    # Dragging from free port onto occupied Port B does NOT create a second cable
    start_v = canvas.mapFromScene(port_a_free.pin_scene_pos())
    end_v = canvas.mapFromScene(port_b_occ.pin_scene_pos())
    qtbot.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, pos=start_v)
    qtbot.mouseMove(canvas.viewport(), pos=end_v)
    assert canvas._snap_target_port is None
    qtbot.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, pos=end_v)
    assert len(state.project.cables) == 1


def test_media_mismatch_commits_and_shows_amber_warning(
    qtbot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NETVIZ_CONFIG_DIR", str(tmp_path))
    proj = Project()
    lwl = instantiate_from_template(
        get_builtin_template(ComponentType.LWL_CONVERTER), [], pos=Point2D(60.0, 80.0)
    )
    dev = instantiate_from_template(
        get_builtin_template(ComponentType.DEVICE), [], pos=Point2D(360.0, 120.0)
    )
    proj.components = [lwl, dev]
    state = ProjectState(proj, autosave_enabled=False)

    view = LogicalView(state, config=AppConfig())
    view.resize(1100, 700)
    qtbot.addWidget(view)
    view.show()

    # LC-SM fiber port on LWL_CONVERTER -> eth0 Base1000 copper port on DEVICE
    fiber_port = next(p for p in lwl.ports if p.port_type == PortType.LWL_SM)
    copper_port = dev.ports[0]

    p_src = view.canvas.get_port_item(lwl.id, fiber_port.id)
    p_dst = view.canvas.get_port_item(dev.id, copper_port.id)
    assert p_src is not None and p_dst is not None

    warnings: list[str] = []
    view.canvas.media_mismatch_warning.connect(warnings.append)

    v_src = view.canvas.mapFromScene(p_src.pin_scene_pos())
    v_dst = view.canvas.mapFromScene(p_dst.pin_scene_pos())
    qtbot.mousePress(view.canvas.viewport(), Qt.MouseButton.LeftButton, pos=v_src)
    qtbot.mouseMove(view.canvas.viewport(), pos=v_dst)
    qtbot.mouseRelease(view.canvas.viewport(), Qt.MouseButton.LeftButton, pos=v_dst)

    assert len(state.project.cables) == 1
    assert len(warnings) == 1
    assert "Warning:" in warnings[0]
    assert view.toast_banner.isVisible()


def test_translucent_alpha_live_update_and_persistence(
    qtbot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NETVIZ_CONFIG_DIR", str(tmp_path))
    state = _two_switch_state()
    view = LogicalView(state, config=AppConfig())
    view.resize(1100, 700)
    qtbot.addWidget(view)
    view.show()

    node_a = view.canvas.get_node_item(state.project.components[0].id)
    assert node_a is not None
    assert node_a.node_alpha == 165

    view.opacity_slider.setValue(110)
    assert node_a.node_alpha == 110
    reloaded = load_config()
    assert reloaded.node_opacity_alpha == 110


def test_hover_trace_and_connection_trace_bar(
    qtbot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NETVIZ_CONFIG_DIR", str(tmp_path))
    state = _two_switch_state()
    comp_a, comp_b = state.project.components
    extra = instantiate_from_template(
        get_builtin_template(ComponentType.ACCESS_POINT), ["RTR-01", "FW-01"], Point2D(100, 420)
    )
    state.project.components.append(extra)
    cab = Cable(
        comp_a.id,
        comp_a.ports[0].id,
        comp_b.id,
        comp_b.ports[0].id,
        label="WAN-Link",
        vlan_id="10",
    )
    state.project.cables.append(cab)

    view = LogicalView(state, config=AppConfig())
    view.resize(1100, 700)
    qtbot.addWidget(view)
    view.show()
    qtbot.waitExposed(view)

    port_a = view.canvas.get_port_item(comp_a.id, comp_a.ports[0].id)
    assert port_a is not None
    _send_hover_move(view.canvas, view.canvas.mapFromScene(port_a.pin_scene_pos()))

    cab_item = view.canvas.get_cable_item(cab.id)
    extra_node = view.canvas.get_node_item(extra.id)
    assert cab_item is not None and extra_node is not None
    assert cab_item.zValue() == Z_CABLE_ACTIVE == 60.0
    assert extra_node._dimmed is True
    assert port_a.pulsed is True
    assert comp_a.name in view.trace_label.text()
    assert comp_b.name in view.trace_label.text()
    assert "VLAN 10" in view.trace_label.text()


def test_ctrl_c_ctrl_v_smart_duplicate_increments_name_and_clears_mac(qtbot) -> None:
    state = _two_switch_state()
    comp_a = state.project.components[0]
    assert comp_a.mac_address != ""

    canvas = LogicalCanvasView(state)
    canvas.resize(900, 650)
    qtbot.addWidget(canvas)
    canvas.show()

    canvas.get_node_item(comp_a.id).setSelected(True)
    assert canvas.copy_selected() == 1
    pasted = canvas.paste_clipboard()
    assert len(pasted) == 1
    dup = pasted[0]
    assert dup.id != comp_a.id
    assert dup.name == "RTR-02"
    assert dup.mac_address == ""
    assert dup.logical_pos.x == pytest.approx(comp_a.logical_pos.x + 40.0)


def test_inspector_port_jump_and_composite_port_delete_undo(
    qtbot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NETVIZ_CONFIG_DIR", str(tmp_path))
    state = _two_switch_state()
    comp_a, comp_b = state.project.components
    cab = state.connect_ports(comp_a.id, comp_a.ports[0].id, comp_b.id, comp_b.ports[0].id)
    assert cab is not None

    view = LogicalView(state, config=AppConfig())
    view.resize(1100, 700)
    qtbot.addWidget(view)
    view.show()

    view.component_inspector.set_component(comp_a.id)
    # Clicking Connected To jumps to & pulses comp_b (and switches selection to comp_b)
    jump_btn = view.component_inspector.port_table.cellWidget(0, 3)
    assert jump_btn is not None
    jump_btn.click()
    assert view.canvas.get_node_item(comp_b.id)._pulsed is True

    # Re-focus comp_a and delete port -> removes port + attached cable in ONE composite undo step
    view.component_inspector.set_component(comp_a.id)
    orig_port_count = len(comp_a.ports)
    view.component_inspector.delete_port_by_id(comp_a.ports[0].id)
    assert len(state.project.get_component(comp_a.id).ports) == orig_port_count - 1
    assert len(state.project.cables) == 0

    state.undo_stack.undo()
    assert len(state.project.get_component(comp_a.id).ports) == orig_port_count
    assert len(state.project.cables) == 1


def test_template_manager_crud_bulk_ports_and_json_roundtrip(
    qtbot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NETVIZ_CONFIG_DIR", str(tmp_path))
    state = _two_switch_state()
    dlg = TemplateManagerDialog(state)
    qtbot.addWidget(dlg)

    dlg._on_new_clicked()
    dlg.name_edit.setText("Access Switch 12P")
    dlg._on_save_template()
    assert len(load_config().recent_files) == 0
    assert len(dlg._global_templates) == 1

    out_path = tmp_path / "custom.netviz-templates.json"
    export_templates_json(dlg._global_templates, out_path)
    loaded = import_templates_json(out_path)
    assert len(loaded) == 1
    assert loaded[0].template_name == "Access Switch 12P"
