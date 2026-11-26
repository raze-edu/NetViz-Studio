"""Phase 3 tests: config, theme, SettingsDialog, ProjectState, TileCacheServer, and LAN Sync."""

from __future__ import annotations

import io
import json
import socket
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests
from PIL import Image
from PySide6.QtWidgets import QLineEdit

from desktop.config import (
    AppConfig,
    add_recent_file,
    load_config,
    load_global_templates,
    save_config,
    save_global_templates,
)
from desktop.lan_sync import (
    UDP_DISCOVER_MAGIC,
    LanSyncDialog,
    LanSyncServer,
    list_network_interfaces,
    render_qr_pixmap,
)
from desktop.state import ProjectState
from desktop.tile_server import TileCacheServer, TilePrefetchWorker
from desktop.ui.dialogs.settings_dialog import SettingsDialog
from desktop.ui.theme import (
    COMPONENT_TYPE_COLORS,
    PORT_TYPE_COLORS,
    apply_dark_theme,
    node_fill_color,
)
from shared.bundle_io import load_netviz, read_tile_from_sqlite, save_netviz, write_tile_to_sqlite
from shared.geo_math import floor_polygon_to_latlon
from shared.models import (
    BlueprintUnderlay,
    Building,
    ComponentType,
    Door,
    Floor,
    GeoAnchor,
    Point2D,
    PositionSource,
    Project,
    Room,
    Stairs,
    SurveySample,
    SurveySession,
    VerticalHole,
    WifiReading,
)
from shared.presets import BUILTIN_TEMPLATES_BY_TYPE, instantiate_from_template


def test_config_persistence_and_api_key_excluded_from_netviz(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NETVIZ_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.delenv("NETVIZ_GOOGLE_MAPS_API_KEY", raising=False)

    cfg = AppConfig(
        google_maps_api_key="AIzaSySecretLocalKey123",
        preferred_provider="google",
        offline_cache_margin_m=5000.0,  # clamped to 2000
        offline_cache_zooms=[15, 18, 22],  # 22 clamped to 20
        node_opacity_alpha=12,  # clamped to 77
    )
    add_recent_file(cfg, tmp_path / "campus.netviz")
    saved_path = save_config(cfg)
    assert saved_path.is_file()

    loaded = load_config()
    assert loaded.google_maps_api_key == "AIzaSySecretLocalKey123"
    assert loaded.effective_google_maps_api_key() == "AIzaSySecretLocalKey123"
    assert loaded.offline_cache_margin_m == 2000.0
    assert loaded.offline_cache_zooms == [15, 18, 20]
    assert loaded.node_opacity_alpha == 77
    assert len(loaded.recent_files) == 1

    # Env var fallback when config key empty
    cfg_empty = AppConfig(google_maps_api_key="")
    monkeypatch.setenv("NETVIZ_GOOGLE_MAPS_API_KEY", "AIzaEnvFallback999")
    assert cfg_empty.effective_google_maps_api_key() == "AIzaEnvFallback999"

    # Global templates round-trip
    tpl = BUILTIN_TEMPLATES_BY_TYPE[ComponentType.ACCESS_POINT]
    save_global_templates([tpl])
    loaded_tpls = load_global_templates()
    assert len(loaded_tpls) == 1
    assert loaded_tpls[0].template_name == tpl.template_name

    # API key must NEVER appear inside exported .netviz archive
    proj = Project(name="Security Audit")
    netviz_path = tmp_path / "audit.netviz"
    save_netviz(proj, netviz_path)
    with zipfile.ZipFile(netviz_path, "r") as zf:
        raw_project_json = zf.read("project.json").decode("utf-8")
    assert "AIzaSySecretLocalKey123" not in raw_project_json
    assert "AIzaEnvFallback999" not in raw_project_json
    assert "google_maps_api_key" not in raw_project_json


def test_theme_colors_and_settings_dialog_live_behavior(
    qtbot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NETVIZ_CONFIG_DIR", str(tmp_path / "cfg"))
    assert len(COMPONENT_TYPE_COLORS) == 9
    assert len(PORT_TYPE_COLORS) == 11
    assert node_fill_color(165).alpha() == 165
    assert node_fill_color(10).alpha() == 77
    assert node_fill_color(255).alpha() == 242

    state = ProjectState(autosave_enabled=False)
    try:
        dlg = SettingsDialog(config=AppConfig(), state=state)
        qtbot.addWidget(dlg)
        apply_dark_theme(dlg)
        dlg.show()

        # Masked by default
        assert dlg.api_key_edit.echoMode() == QLineEdit.EchoMode.Password
        dlg.toggle_key_btn.click()
        assert dlg.api_key_edit.echoMode() == QLineEdit.EchoMode.Normal
        dlg.toggle_key_btn.click()
        assert dlg.api_key_edit.echoMode() == QLineEdit.EchoMode.Password

        dlg.focus_api_key()
        dlg.api_key_edit.setText("AIzaLiveReloadTest")
        idx = dlg.provider_combo.findData("leaflet_satellite")
        dlg.provider_combo.setCurrentIndex(idx)
        dlg.margin_spin.setValue(400.0)

        emitted: list[AppConfig] = []
        dlg.settings_changed.connect(emitted.append)
        dlg.save_btn.click()

        assert len(emitted) == 1
        assert emitted[0].google_maps_api_key == "AIzaLiveReloadTest"
        assert emitted[0].preferred_provider == "leaflet_satellite"
        assert state.project.map_preferences.preferred_provider == "leaflet_satellite"
        assert state.project.map_preferences.offline_cache_margin_m == 400.0
        assert load_config().google_maps_api_key == "AIzaLiveReloadTest"
    finally:
        state.cleanup()


def test_project_state_undo_redo_and_footprint_single_source_of_truth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NETVIZ_CONFIG_DIR", str(tmp_path / "cfg"))
    state = ProjectState(autosave_enabled=False)
    try:
        sw = instantiate_from_template(BUILTIN_TEMPLATES_BY_TYPE[ComponentType.ACTIVE_SWITCH], [])
        ap = instantiate_from_template(
            BUILTIN_TEMPLATES_BY_TYPE[ComponentType.ACCESS_POINT], [sw.name]
        )
        state.add_component(sw)
        state.add_component(ap)
        assert len(state.project.components) == 2

        cable = state.connect_ports(sw.id, sw.ports[0].id, ap.id, ap.ports[0].id, vlan_id="10")
        assert cable is not None
        assert len(state.project.cables) == 1

        # Undo cable connect & redo
        state.undo_stack.undo()
        assert len(state.project.cables) == 0
        state.undo_stack.redo()
        assert len(state.project.cables) == 1

        # Add building with floor0 -> footprint_latlon synced automatically
        anchor_a = GeoAnchor(origin_lat=52.52, origin_lon=13.405, rotation_deg=22.5)
        poly_0 = [
            Point2D(-10.0, -5.0),
            Point2D(10.0, -5.0),
            Point2D(10.0, 5.0),
            Point2D(-10.0, 5.0),
        ]
        floor0 = Floor(name="GF", outer_polygon_m=poly_0, level_index=0)
        bldg = Building(name="HQ", geo_anchor=anchor_a, floors=[floor0])
        state.add_building(bldg)

        expected_fp = floor_polygon_to_latlon(poly_0, anchor_a)
        stored_bldg = state.project.get_building(bldg.id)
        assert stored_bldg is not None
        assert stored_bldg.footprint_latlon == pytest.approx(expected_fp)

        # Updating GeoAnchor recomputes footprint_latlon deterministically
        anchor_b = GeoAnchor(origin_lat=52.521, origin_lon=13.406, rotation_deg=45.0)
        state.set_building_anchor(bldg.id, anchor_b)
        assert state.project.get_building(bldg.id).footprint_latlon == pytest.approx(
            floor_polygon_to_latlon(poly_0, anchor_b)
        )

        # Editing floor0.outer_polygon_m recomputes footprint_latlon deterministically
        poly_1 = [
            Point2D(-12.0, -6.0),
            Point2D(12.0, -6.0),
            Point2D(12.0, 6.0),
            Point2D(-12.0, 6.0),
        ]
        state.edit_floor_outline(bldg.id, floor0.id, poly_1)
        assert state.project.get_building(bldg.id).footprint_latlon == pytest.approx(
            floor_polygon_to_latlon(poly_1, anchor_b)
        )

        # Undo outline edit restores previous footprint_latlon
        state.undo_stack.undo()
        assert state.project.get_building(bldg.id).footprint_latlon == pytest.approx(
            floor_polygon_to_latlon(poly_0, anchor_b)
        )

        # View rotation NEVER alters GeoAnchor.rotation_deg
        state.set_floor_view_rotation(bldg.id, floor0.id, 37.0)
        assert state.project.get_floor(bldg.id, floor0.id).editor_view_rotation_deg == 37.0
        assert state.project.get_building(bldg.id).geo_anchor.rotation_deg == 45.0

        # Room, door, stairs, hole, blueprint, physical placement, survey mutations + undo
        room = Room(
            name="Server Room",
            polygon_m=[Point2D(0, 0), Point2D(4, 0), Point2D(4, 4), Point2D(0, 4)],
        )
        state.add_room(bldg.id, floor0.id, room)
        state.add_door(bldg.id, floor0.id, Door(position_m=Point2D(2.0, 0.0)))
        state.add_stairs(
            bldg.id,
            floor0.id,
            Stairs(polygon_m=[Point2D(5, 0), Point2D(7, 0), Point2D(7, 2), Point2D(5, 2)]),
        )
        state.add_hole(
            bldg.id,
            floor0.id,
            VerticalHole(position_m=Point2D(1.0, 1.0), riser_group_id="R1"),
        )
        state.set_floor_blueprint(
            bldg.id,
            floor0.id,
            BlueprintUnderlay(archive_path=f"blueprints/{floor0.id}.png"),
            b"fake-png-bytes",
        )
        state.place_component_physical(ap.id, bldg.id, floor0.id, Point2D(2.0, 2.0))
        session = SurveySession(
            name="Walk 1",
            created_utc="2026-01-01T00:00:00Z",
            samples=[
                SurveySample(
                    timestamp_utc="2026-01-01T00:00:05Z",
                    building_id=bldg.id,
                    floor_id=floor0.id,
                    pos_m=Point2D(2.0, 2.0),
                    source=PositionSource.MANUAL,
                    readings=[WifiReading("Corp-WiFi", "AA:BB:CC:DD:EE:01", -52, 5180)],
                )
            ],
        )
        state.import_survey_session(session)
        assert len(state.project.surveys) == 1

        # Autosave creates a valid .netviz bundle
        rec_path = state.autosave_now()
        assert rec_path is not None and rec_path.is_file()
        loaded_rec = load_netviz(rec_path)
        assert loaded_rec.blueprints[f"blueprints/{floor0.id}.png"] == b"fake-png-bytes"

        # Delete survey sample + undo
        state.delete_survey_samples([session.samples[0].id])
        assert len(state.project.surveys[0].samples) == 0
        state.undo_stack.undo()
        assert len(state.project.surveys[0].samples) == 1
    finally:
        state.cleanup()


def test_tile_cache_server_assets_sqlite_hit_offline_and_prefetch(tmp_path: Path) -> None:
    assets_dir = tmp_path / "map_assets"
    (assets_dir / "vendor").mkdir(parents=True)
    (assets_dir / "map.html").write_text("<html><body>NetViz Map</body></html>", encoding="utf-8")
    (assets_dir / "vendor" / "leaflet.js").write_text("console.log('leaflet');", encoding="utf-8")

    db_path = tmp_path / "tiles.sqlite"
    fake_tile_png = b"\x89PNG\r\n\x1a\nFAKE_OSM_TILE"
    write_tile_to_sqlite(db_path, "osm", 18, 140702, 86081, fake_tile_png, mime="image/png")

    server = TileCacheServer(db_path, assets_dir=assets_dir, offline_mode=True)
    port = server.start()
    try:
        # 1. /assets/map.html & vendor JS
        r_html = requests.get(f"http://127.0.0.1:{port}/assets/map.html", timeout=3.0)
        assert r_html.status_code == 200
        assert "text/html" in r_html.headers["Content-Type"]
        assert "NetViz Map" in r_html.text

        r_js = requests.get(f"http://127.0.0.1:{port}/assets/vendor/leaflet.js", timeout=3.0)
        assert r_js.status_code == 200
        assert "javascript" in r_js.headers["Content-Type"]

        # Traversal protection
        r_trav = requests.get(f"http://127.0.0.1:{port}/assets/../secret.txt", timeout=3.0)
        assert r_trav.status_code in (403, 404)

        # 2. Cached SQLite tile hit offline
        r_hit = requests.get(f"http://127.0.0.1:{port}/tiles/osm/18/140702/86081", timeout=3.0)
        assert r_hit.status_code == 200
        assert r_hit.headers.get("X-NetViz-Tile-Status") == "cache-hit"
        assert r_hit.content == fake_tile_png

        # 3. Missing tile while offline -> 256x256 PNG placeholder
        r_miss = requests.get(
            f"http://127.0.0.1:{port}/tiles/esri_satellite/18/1/2.png", timeout=3.0
        )
        assert r_miss.status_code == 200
        assert r_miss.headers.get("X-NetViz-Tile-Status") == "offline-placeholder"
        img = Image.open(io.BytesIO(r_miss.content))
        assert img.size == (256, 256)

        # 4. Online fetch populates SQLite cache when offline_mode=False
        server.offline_mode = False
        upstream_bytes = io.BytesIO()
        Image.new("RGB", (256, 256), (12, 34, 56)).save(upstream_bytes, format="PNG")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = upstream_bytes.getvalue()
        mock_resp.headers = {"Content-Type": "image/png"}
        real_get = requests.get

        def _fake_get(url: str, *args: object, **kwargs: object) -> object:
            if url.startswith(f"http://127.0.0.1:{port}"):
                return real_get(url, *args, **kwargs)
            return mock_resp

        with patch("desktop.tile_server.requests.get", side_effect=_fake_get) as mock_get:
            r_up = real_get(f"http://127.0.0.1:{port}/tiles/osm/17/65000/43000", timeout=3.0)
            assert r_up.status_code == 200
            assert r_up.headers.get("X-NetViz-Tile-Status") == "upstream"
            assert mock_get.call_count == 1

        assert read_tile_from_sqlite(db_path, "osm", 17, 65000, 43000) is not None
    finally:
        server.stop()


def test_tile_prefetch_worker(qtbot, tmp_path: Path) -> None:
    db_path = tmp_path / "prefetch.sqlite"
    anchor = GeoAnchor(origin_lat=52.52, origin_lon=13.405, rotation_deg=0.0)
    bldg = Building(
        name="Campus",
        geo_anchor=anchor,
        floors=[
            Floor(
                name="GF",
                outer_polygon_m=[
                    Point2D(-5, -5),
                    Point2D(5, -5),
                    Point2D(5, 5),
                    Point2D(-5, 5),
                ],
            )
        ],
    )
    img_buf = io.BytesIO()
    Image.new("RGB", (256, 256), (20, 30, 40)).save(img_buf, format="PNG")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = img_buf.getvalue()
    mock_resp.headers = {"Content-Type": "image/png"}

    worker = TilePrefetchWorker(
        [bldg],
        db_path,
        margin_m=50.0,
        zooms=[16],
        providers=("osm",),
        delay_s=0.0,
    )
    results: list[tuple[int, int, bool]] = []
    worker.finished_prefetch.connect(lambda d, s, c: results.append((d, s, c)))

    with patch("desktop.tile_server.requests.get", return_value=mock_resp):
        with qtbot.waitSignal(worker.finished_prefetch, timeout=5000):
            worker.start()

    assert len(results) == 1
    downloaded, skipped, cancelled = results[0]
    assert downloaded >= 1
    assert skipped == 0
    assert cancelled is False


def test_windows_virtual_nic_filtering() -> None:
    candidates = [
        ("Loopback Pseudo-Interface 1", "127.0.0.1"),
        ("vEthernet (WSL (Hyper-V firewall))", "172.24.16.1"),
        ("vEthernet (Default Switch)", "172.28.112.1"),
        ("DockerNAT", "10.0.75.1"),
        ("VMware Network Adapter VMnet8", "192.168.159.1"),
        ("VirtualBox Host-Only Network", "192.168.56.1"),
        ("Tailscale", "100.88.12.5"),
        ("Local Area Connection* 10", "169.254.44.9"),
        ("Wi-Fi", "192.168.1.42"),
        ("Ethernet", "192.168.1.43"),
    ]
    filtered = list_network_interfaces(candidates=candidates)
    ips = [info.ip for info in filtered]
    assert set(ips) == {"192.168.1.42", "192.168.1.43"}
    assert all(info.is_preferred_lan for info in filtered)


def test_lan_sync_http_auth_streaming_upload_and_udp_discovery(
    qtbot,
    tmp_path: Path,
) -> None:
    project = Project(name="LAN Campus")
    tiles_db = tmp_path / "tiles.sqlite"
    write_tile_to_sqlite(tiles_db, "osm", 16, 35175, 21520, b"tile-bytes")

    server = LanSyncServer(
        token="A7K9P2",
        staging_dir=tmp_path / "stage",
        interface_candidates=[("Wi-Fi", "127.0.0.1")],
    )
    received_sessions: list[SurveySession] = []
    server.survey_received.connect(received_sessions.append)

    snap = server.refresh_snapshot(project, tiles_db_path=tiles_db)
    port = server.start(port_range=range(18765, 18780), udp_port=0)
    try:
        # 1. /api/ping without & with token
        ping_no_tok = requests.get(f"http://127.0.0.1:{port}/api/ping", timeout=3.0).json()
        assert ping_no_tok["ok"] is True
        assert ping_no_tok["app"] == "NetVizStudio"
        assert ping_no_tok["project_name"] == "LAN Campus"
        assert ping_no_tok["bundle_bytes"] == snap.size_bytes
        assert ping_no_tok["bundle_sha256"] == snap.sha256
        assert ping_no_tok["token_required"] is True
        assert "token_valid" not in ping_no_tok

        ping_bad_tok = requests.get(
            f"http://127.0.0.1:{port}/api/ping",
            headers={"X-NetViz-Token": "WRONG1"},
            timeout=3.0,
        ).json()
        assert ping_bad_tok["token_valid"] is False

        ping_ok_tok = requests.get(
            f"http://127.0.0.1:{port}/api/ping?token=A7K9P2",
            timeout=3.0,
        ).json()
        assert ping_ok_tok["token_valid"] is True

        # 2. /api/project.netviz auth (401 vs 200) + SHA-256 streaming download
        unauth = requests.get(f"http://127.0.0.1:{port}/api/project.netviz", timeout=3.0)
        assert unauth.status_code == 401

        auth_dl = requests.get(
            f"http://127.0.0.1:{port}/api/project.netviz",
            headers={"X-NetViz-Token": "A7K9P2"},
            timeout=5.0,
        )
        assert auth_dl.status_code == 200
        assert int(auth_dl.headers["Content-Length"]) == snap.size_bytes
        assert auth_dl.headers["X-NetViz-SHA256"] == snap.sha256
        dl_path = tmp_path / "downloaded.netviz"
        dl_path.write_bytes(auth_dl.content)
        bundle = load_netviz(dl_path)
        assert bundle.project.name == "LAN Campus"
        assert bundle.tiles_bytes is not None

        # 3. POST /api/upload-survey auth, SHA-256 verify, and Qt survey_received signal
        session = SurveySession(
            name="Field Walk A",
            created_utc="2026-01-01T12:00:00Z",
            device_model="Pixel 8",
            samples=[
                SurveySample(
                    timestamp_utc="2026-01-01T12:00:02Z",
                    building_id="b1",
                    floor_id="f1",
                    pos_m=Point2D(3.5, 4.2),
                    source=PositionSource.GPS_CALIBRATED,
                    gps_lat=52.52,
                    gps_lon=13.405,
                    gps_accuracy_m=3.1,
                    calibration_offset_m=Point2D(0.5, -0.3),
                    readings=[WifiReading("Corp-WiFi", "00:11:22:33:44:55", -58, 5200)],
                )
            ],
        )
        payload_bytes = session.to_json().encode("utf-8")
        from shared.bundle_io import compute_sha256_bytes

        good_sha = compute_sha256_bytes(payload_bytes)

        bad_auth = requests.post(
            f"http://127.0.0.1:{port}/api/upload-survey",
            data=payload_bytes,
            headers={"X-NetViz-Token": "NOPE00"},
            timeout=3.0,
        )
        assert bad_auth.status_code == 401

        bad_sha = requests.post(
            f"http://127.0.0.1:{port}/api/upload-survey",
            data=payload_bytes,
            headers={"X-NetViz-Token": "A7K9P2", "X-NetViz-SHA256": "0" * 64},
            timeout=3.0,
        )
        assert bad_sha.status_code == 400

        with qtbot.waitSignal(server.survey_received, timeout=3000):
            ok_up = requests.post(
                f"http://127.0.0.1:{port}/api/upload-survey",
                data=payload_bytes,
                headers={"X-NetViz-Token": "A7K9P2", "X-NetViz-SHA256": good_sha},
                timeout=3.0,
            )
        assert ok_up.status_code == 200
        up_json = ok_up.json()
        assert up_json["ok"] is True
        assert up_json["session_id"] == session.id
        assert up_json["sample_count"] == 1
        assert len(received_sessions) == 1
        assert received_sessions[0].name == "Field Walk A"

        # 4. UDP Discovery Responder
        udp_port = server.udp_port
        assert udp_port > 0
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as cli:
            cli.settimeout(3.0)
            cli.sendto(UDP_DISCOVER_MAGIC, ("127.0.0.1", udp_port))
            raw_reply, _ = cli.recvfrom(4096)
        disc = json.loads(raw_reply.decode("utf-8"))
        assert disc["app"] == "NetVizStudio"
        assert disc["port"] == port
        assert disc["token"] == "A7K9P2"
        assert disc["project_name"] == "LAN Campus"
        assert "127.0.0.1" in disc["ips"]

        # 5. QR payload & pixmap
        qr_json = json.loads(server.build_qr_payload(primary_ip="127.0.0.1"))
        assert qr_json["v"] == 2
        assert qr_json["port"] == port
        assert qr_json["token"] == "A7K9P2"
        assert not render_qr_pixmap(json.dumps(qr_json)).isNull()

        # 6. LanSyncDialog mounts cleanly with shared server
        state = ProjectState(project=project, autosave_enabled=False)
        try:
            dlg = LanSyncDialog(state=state, server=server)
            qtbot.addWidget(dlg)
            dlg.show()
            assert dlg.token_label.text() == "A7K9P2"
            assert dlg.qr_label.pixmap() is not None
            assert not dlg.qr_label.pixmap().isNull()
            dlg.close()
        finally:
            state.cleanup()
    finally:
        server.stop()
