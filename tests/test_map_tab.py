"""Phase 5 tests: vendor Leaflet fetch, TileCacheServer, MapBridge, and MapBuildingView."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import requests
from PySide6.QtWebEngineCore import QWebEnginePage

from desktop.config import AppConfig, save_config
from desktop.state import ProjectState
from desktop.tile_server import TileCacheServer
from desktop.ui.map_tab.map_bridge import (
    MapBridge,
    NetVizWebPage,
    apply_building_transform_to_state,
    apply_place_building_to_state,
    apply_traced_polygon_to_state,
)
from desktop.ui.map_tab.map_building_view import MapBuildingView, parse_lat_lon
from scripts.fetch_vendor_assets import VENDOR_ASSETS, fetch_vendor_assets, is_valid_asset
from shared.bundle_io import read_tile_from_sqlite
from shared.geo_math import floor_polygon_to_latlon
from shared.models import Building, Floor, Point2D

REPO_ROOT = Path(__file__).resolve().parent.parent
VENDOR_DIR = REPO_ROOT / "desktop" / "ui" / "map_tab" / "map_assets" / "vendor"


def test_fetch_vendor_assets_verified_and_skip_cache(tmp_path: Path) -> None:
    # Ensure repo vendor files exist and match SHA-256
    results = fetch_vendor_assets(VENDOR_DIR)
    assert set(results.keys()) == {"leaflet.js", "leaflet.css"}
    for spec in VENDOR_ASSETS:
        asset_path = results[spec.filename]
        assert asset_path.is_file()
        assert asset_path.stat().st_size >= spec.min_bytes
        assert is_valid_asset(asset_path, spec)

    # Re-running without --force skips re-downloading (even if requests.get is disabled)
    with patch(
        "scripts.fetch_vendor_assets.requests.get",
        side_effect=AssertionError("should skip"),
    ):
        skipped = fetch_vendor_assets(VENDOR_DIR, force=False)
    assert skipped["leaflet.js"] == VENDOR_DIR / "leaflet.js"
    assert skipped["leaflet.css"] == VENDOR_DIR / "leaflet.css"


def test_tile_cache_server_serves_all_phase5_map_assets(tmp_path: Path) -> None:
    db_path = tmp_path / "tiles.sqlite"
    server = TileCacheServer(db_path, offline_mode=True)
    port = server.start()
    try:
        for asset in ("map.html", "map_controller.js", "vendor/leaflet.js", "vendor/leaflet.css"):
            resp = requests.get(f"http://127.0.0.1:{port}/assets/{asset}", timeout=3.0)
            assert resp.status_code == 200, f"Expected 200 for /assets/{asset}"
            assert len(resp.content) > 100
    finally:
        server.stop()


def test_map_bridge_handshake_queues_until_ready_and_syncs_buildings(qtbot) -> None:
    bridge = MapBridge()
    scripts_run: list[str] = []

    class _FakePage:
        def runJavaScript(self, script: str) -> None:  # noqa: N802
            scripts_run.append(script)

    bridge._page = _FakePage()  # type: ignore[assignment]

    # Queue configure_map and sync_buildings BEFORE on_map_ready()
    bldg = Building(
        name="Alpha",
        floors=[
            Floor(
                name="GF",
                outer_polygon_m=[
                    Point2D(-10.0, -5.0),
                    Point2D(10.0, -5.0),
                    Point2D(10.0, 5.0),
                    Point2D(-10.0, 5.0),
                ],
                level_index=0,
            )
        ],
    )
    bridge.configure_map(
        preferred_provider="google",
        google_api_key="AIzaTestKey",
        offline_mode=False,
        tile_base_url="http://127.0.0.1:12345",
    )
    bridge.sync_buildings([bldg], selected_building_id=bldg.id)
    bridge.jump_to_latlon(52.52, 13.405, 18)

    # Zero JS executed before on_map_ready()
    assert scripts_run == []
    assert not bridge.is_map_ready

    # Fire on_map_ready() -> all queued updates flush in order
    bridge.on_map_ready()
    assert bridge.is_map_ready
    assert len(scripts_run) == 3
    assert "configureMap" in scripts_run[0]
    assert "syncBuildings" in scripts_run[1]
    assert "jumpToLatLon" in scripts_run[2]


def test_map_bridge_trace_place_transform_and_auth_error_forwarding(
    qtbot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NETVIZ_CONFIG_DIR", str(tmp_path / "cfg"))
    state = ProjectState(autosave_enabled=False)
    try:
        # 1. Traced polygon creates anchored Building with floor0 as source of truth
        traced = [
            [52.52000, 13.40500],
            [52.52000, 13.40530],
            [52.52018, 13.40530],
            [52.52018, 13.40500],
        ]
        bridge = MapBridge()
        received_polys: list[list[tuple[float, float]]] = []
        bridge.polygon_traced.connect(received_polys.append)
        bridge.on_polygon_traced(json.dumps(traced))
        assert len(received_polys) == 1

        traced_bldg = apply_traced_polygon_to_state(state, received_polys[0], name="Campus Hall")
        assert traced_bldg.geo_anchor is not None
        assert len(traced_bldg.floors) == 1
        assert traced_bldg.footprint_latlon == pytest.approx(
            floor_polygon_to_latlon(traced_bldg.floors[0].outer_polygon_m, traced_bldg.geo_anchor)
        )
        assert traced_bldg.footprint_latlon == pytest.approx(
            [(p[0], p[1]) for p in traced], abs=1e-6
        )

        # 2. Place unanchored building on map
        manual_bldg = Building(
            name="Manual Shed",
            geo_anchor=None,
            floors=[
                Floor(
                    name="GF",
                    outer_polygon_m=[
                        Point2D(-5.0, -3.0),
                        Point2D(5.0, -3.0),
                        Point2D(5.0, 3.0),
                        Point2D(-5.0, 3.0),
                    ],
                )
            ],
        )
        state.add_building(manual_bldg)
        assert state.project.get_building(manual_bldg.id).geo_anchor is None
        apply_place_building_to_state(state, manual_bldg.id, 52.521, 13.406)
        placed = state.project.get_building(manual_bldg.id)
        assert placed is not None and placed.geo_anchor is not None
        assert placed.geo_anchor.origin_lat == pytest.approx(52.521)
        assert placed.footprint_latlon == pytest.approx(
            floor_polygon_to_latlon(placed.floors[0].outer_polygon_m, placed.geo_anchor)
        )

        # 3. Transform (Move + Rotate CW) updates GeoAnchor & footprint_latlon deterministically
        apply_building_transform_to_state(state, manual_bldg.id, 52.522, 13.407, 37.5)
        transformed = state.project.get_building(manual_bldg.id)
        assert transformed is not None and transformed.geo_anchor is not None
        assert transformed.geo_anchor.rotation_deg == pytest.approx(37.5)
        assert transformed.footprint_latlon == pytest.approx(
            floor_polygon_to_latlon(transformed.floors[0].outer_polygon_m, transformed.geo_anchor)
        )

        # 4. NetVizWebPage forwards Google auth/referrer JS console errors
        page = NetVizWebPage()
        auth_errors: list[str] = []
        bridge.attach_to_page(page)
        bridge.auth_error_received.connect(auth_errors.append)
        page.javaScriptConsoleMessage(
            QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel,
            "Google Maps JavaScript API error: RefererNotAllowedMapError",
            1,
            "https://maps.googleapis.com/maps/api/js",
        )
        assert len(auth_errors) == 1
        assert "RefererNotAllowedMapError" in auth_errors[0]
    finally:
        state.cleanup()


def test_map_building_view_banners_live_reload_jump_and_prefetch(
    qtbot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NETVIZ_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.delenv("NETVIZ_GOOGLE_MAPS_API_KEY", raising=False)

    cfg = AppConfig(
        google_maps_api_key="",
        preferred_provider="google",
        offline_cache_margin_m=100.0,
        offline_cache_zooms=[16],
    )
    save_config(cfg)

    state = ProjectState(autosave_enabled=False)
    db_path = tmp_path / "tiles.sqlite"
    tile_server = TileCacheServer(db_path, offline_mode=True)
    tile_server.start()

    try:
        view = MapBuildingView(
            state,
            tile_server=tile_server,
            config=cfg,
            offline_mode=True,
            enable_web_view=False,
        )
        qtbot.addWidget(view)
        view.show()

        # 1. Amber missing-key banner visible when preferred_provider == "google" and key empty
        assert view.missing_key_banner.isVisible()

        # 2. Setting API key via reload_map_settings hides banner and configures bridge live
        updated_cfg = AppConfig(
            google_maps_api_key="AIzaNewKeyLive123",
            preferred_provider="google",
            offline_cache_margin_m=100.0,
            offline_cache_zooms=[16],
        )
        view.reload_map_settings(updated_cfg)
        assert not view.missing_key_banner.isVisible()
        assert view.bridge._pending_config is not None
        assert view.bridge._pending_config["google_api_key"] == "AIzaNewKeyLive123"

        # 3. Auth error signal shows error banner
        view.bridge.on_auth_error("InvalidKeyMapError: key invalid")
        assert view.auth_error_banner.isVisible()
        assert "InvalidKeyMapError" in view.auth_error_label.text()

        # 4. Offline Location Jump Bar parses lat, lon and updates bridge
        assert parse_lat_lon("52.5200, 13.4050") == pytest.approx((52.52, 13.405))
        assert parse_lat_lon("999.0, 13.4050") is None
        view.jump_input.setText("52.5200, 13.4050")
        assert view.jump_to_query() is True
        assert view.bridge._pending_jump == pytest.approx((52.52, 13.405, 18))

        # 5. Manual building + Place on Map + TilePrefetchWorker writes into tiles.sqlite
        view._on_add_manual_building()
        assert len(state.project.buildings) == 1
        bldg = state.project.buildings[0]
        assert bldg.geo_anchor is None
        assert view.place_btn.isEnabled()

        view.bridge.on_building_placed(bldg.id, 52.52, 13.405)
        assert state.project.get_building(bldg.id).geo_anchor is not None
        assert not view.place_btn.isEnabled()

        fake_png = b"\x89PNG\r\n\x1a\nPREFETCH_TEST"
        with patch(
            "desktop.tile_server._fetch_upstream_tile",
            return_value=(fake_png, "image/png"),
        ):
            worker = view.start_tile_prefetch(
                tiles_db_path=db_path,
                providers=("osm",),
                delay_s=0.0,
            )
            assert worker is not None
            with qtbot.waitSignal(worker.finished_prefetch, timeout=5000) as sig:
                worker.start()
            downloaded, skipped, cancelled = sig.args
            assert downloaded >= 1
            assert not cancelled

        # Confirm tile is stored in tiles.sqlite
        from shared.geo_math import latlon_to_tile_xy

        tx, ty = latlon_to_tile_xy(52.52, 13.405, 16)
        assert read_tile_from_sqlite(db_path, "osm", 16, tx, ty) == (fake_png, "image/png")
    finally:
        view.cleanup()
        tile_server.stop()
        state.cleanup()


def test_real_webengine_loads_map_html_over_http_and_completes_handshake(
    qtbot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify QWebEngineView + QWebChannel loads map.html over HTTP and fires on_map_ready."""
    if sys.platform.startswith("win") is False:
        pytest.skip("Windows WebEngine verification")

    monkeypatch.setenv("NETVIZ_CONFIG_DIR", str(tmp_path / "cfg"))
    cfg = AppConfig(google_maps_api_key="", preferred_provider="leaflet_osm")
    state = ProjectState(autosave_enabled=False)
    db_path = tmp_path / "tiles.sqlite"
    tile_server = TileCacheServer(db_path, offline_mode=True)
    tile_server.start()

    view = MapBuildingView(
        state,
        tile_server=tile_server,
        config=cfg,
        offline_mode=True,
        enable_web_view=True,
    )
    qtbot.addWidget(view)
    view.show()
    try:
        if not view.bridge.is_map_ready:
            with qtbot.waitSignal(view.bridge.map_ready_changed, timeout=15000) as sig:
                assert sig is not None
        assert view.bridge.is_map_ready

        # Verify JS localMToLatLon matches shared.geo_math.local_m_to_latlon verbatim
        from PySide6.QtCore import QUrl

        from shared.geo_math import local_m_to_latlon
        from shared.models import GeoAnchor

        anchor = GeoAnchor(origin_lat=52.52, origin_lon=13.405, rotation_deg=37.5)
        pt = Point2D(x=14.2, y=-8.6)
        py_lat, py_lon = local_m_to_latlon(pt, anchor)

        js_result: list[object] = []
        assert view.web_page is not None
        view.web_page.runJavaScript(
            "JSON.stringify(window.netvizMap.localMToLatLon("
            "{x: 14.2, y: -8.6}, "
            "{origin_lat: 52.52, origin_lon: 13.405, rotation_deg: 37.5}));",
            js_result.append,
        )
        qtbot.waitUntil(lambda: len(js_result) == 1, timeout=5000)
        assert isinstance(js_result[0], str)
        assert json.loads(js_result[0]) == pytest.approx([py_lat, py_lon], abs=1e-10)
    finally:
        if view.web_view is not None:
            view.web_view.setUrl(QUrl("about:blank"))
        view.cleanup()
        view.close()
        qtbot.wait(150)
        tile_server.stop()
        state.cleanup()
