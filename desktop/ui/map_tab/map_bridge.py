"""QWebChannel bridge, zero-race handshake, and JS console/auth error forwarding."""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage

from desktop.state import ProjectState, sync_building_footprint
from shared.geo_math import compute_centroid, polygon_latlon_to_local_m
from shared.models import Building, Floor, GeoAnchor, Point2D

LOGGER = logging.getLogger("netviz.map_js")

_AUTH_ERROR_MARKERS = (
    "gm_authFailure",
    "RefererNotAllowedMapError",
    "InvalidKeyMapError",
    "ApiNotActivatedMapError",
    "MissingKeyMapError",
    "Google Maps JavaScript API error",
)


def _is_google_auth_error(message: str) -> bool:
    lowered = message.lower()
    return any(marker.lower() in lowered for marker in _AUTH_ERROR_MARKERS)


class NetVizWebPage(QWebEnginePage):
    """Custom QWebEnginePage forwarding JS console messages and Google Maps auth errors."""

    js_auth_error = Signal(str)
    js_console_logged = Signal(int, str, int, str)

    def javaScriptConsoleMessage(
        self,
        level: QWebEnginePage.JavaScriptConsoleMessageLevel,
        message: str,
        lineNumber: int,
        sourceID: str,
    ) -> None:
        source_label = sourceID or "map"
        formatted = f"[MapJS] ({source_label}:{lineNumber}) {message}"
        if level == QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel:
            LOGGER.error(formatted)
        elif level == QWebEnginePage.JavaScriptConsoleMessageLevel.WarningMessageLevel:
            LOGGER.warning(formatted)
        else:
            LOGGER.info(formatted)

        self.js_console_logged.emit(int(level.value), message, int(lineNumber), source_label)
        if _is_google_auth_error(message):
            self.js_auth_error.emit(message)


def serialize_buildings_for_map(buildings: Sequence[Building]) -> list[dict[str, Any]]:
    """Serialize Project buildings with geo_anchor and floor0 local polygon for map JS."""
    payload: list[dict[str, Any]] = []
    for bldg in buildings:
        floor0_poly: list[dict[str, float]] = []
        if bldg.floors and bldg.floors[0].outer_polygon_m:
            floor0_poly = [pt.to_dict() for pt in bldg.floors[0].outer_polygon_m]
        payload.append(
            {
                "id": bldg.id,
                "name": bldg.name,
                "geo_anchor": bldg.geo_anchor.to_dict() if bldg.geo_anchor is not None else None,
                "footprint_latlon": [
                    [float(lat), float(lon)] for lat, lon in bldg.footprint_latlon
                ],
                "floor0_polygon_m": floor0_poly,
            }
        )
    return payload


def apply_traced_polygon_to_state(
    state: ProjectState,
    latlons: Sequence[tuple[float, float]],
    *,
    name: str | None = None,
) -> Building:
    """Create and anchor a new Building from traced WGS84 vertices per Section 4.2D."""
    cleaned = [(float(lat), float(lon)) for lat, lon in latlons]
    if len(cleaned) > 1 and cleaned[0] == cleaned[-1]:
        cleaned = cleaned[:-1]
    if len(cleaned) < 3:
        raise ValueError("Traced building polygon requires at least 3 distinct vertices")

    origin_lat, origin_lon = compute_centroid(cleaned)
    anchor = GeoAnchor(origin_lat=origin_lat, origin_lon=origin_lon, rotation_deg=0.0)
    local_pts = polygon_latlon_to_local_m(cleaned, anchor)

    if name is None:
        existing_names = {b.name.lower() for b in state.project.buildings}
        idx = len(state.project.buildings) + 1
        while f"Building {idx}".lower() in existing_names:
            idx += 1
        name = f"Building {idx}"

    floor0 = Floor(name="Ground Floor", outer_polygon_m=local_pts, level_index=0)
    bldg = Building(name=name, geo_anchor=anchor, floors=[floor0])
    sync_building_footprint(bldg)
    state.add_building(bldg)
    return bldg


def apply_place_building_to_state(
    state: ProjectState,
    building_id: str,
    lat: float,
    lon: float,
) -> None:
    """Anchor an unplaced Building at (lat, lon) preserving its Floor 0 polygon."""
    bldg = state.project.get_building(building_id)
    if bldg is None:
        return
    rotation = bldg.geo_anchor.rotation_deg if bldg.geo_anchor is not None else 0.0
    if not bldg.floors:
        default_poly = [
            Point2D(-10.0, -6.0),
            Point2D(10.0, -6.0),
            Point2D(10.0, 6.0),
            Point2D(-10.0, 6.0),
        ]
        state.add_floor(
            building_id,
            Floor(name="Ground Floor", outer_polygon_m=default_poly, level_index=0),
        )
    anchor = GeoAnchor(origin_lat=float(lat), origin_lon=float(lon), rotation_deg=rotation)
    state.set_building_anchor(building_id, anchor, text="Place Building on Map")


def apply_building_transform_to_state(
    state: ProjectState,
    building_id: str,
    origin_lat: float,
    origin_lon: float,
    rotation_deg: float,
) -> None:
    """Apply map move/rotate handle transformation to Building.geo_anchor."""
    bldg = state.project.get_building(building_id)
    if bldg is None:
        return
    normalized_rot = float(rotation_deg) % 360.0
    anchor = GeoAnchor(
        origin_lat=float(origin_lat),
        origin_lon=float(origin_lon),
        rotation_deg=normalized_rot,
    )
    state.set_building_anchor(building_id, anchor, text="Move/Rotate Building on Map")


class MapBridge(QObject):
    """QWebChannel bridge managing the Python <-> JS handshake and map events."""

    map_ready_changed = Signal(bool)
    polygon_traced = Signal(list)
    building_placed = Signal(str, float, float)
    building_transformed = Signal(str, float, float, float)
    building_selected = Signal(str)
    auth_error_received = Signal(str)
    provider_switched = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._page: QWebEnginePage | None = None
        self._channel: QWebChannel | None = None
        self._map_ready: bool = False
        self._pending_config: dict[str, Any] | None = None
        self._pending_buildings: list[dict[str, Any]] | None = None
        self._pending_selected_id: str | None = None
        self._pending_jump: tuple[float, float, int] | None = None
        self._pending_mode: tuple[str, str | None] | None = None
        self.dispatched_scripts: list[str] = []

    @property
    def is_map_ready(self) -> bool:
        return self._map_ready

    def attach_to_page(self, page: QWebEnginePage) -> QWebChannel:
        """Attach QWebChannel to page BEFORE calling setUrl to prevent handshake races."""
        channel = QWebChannel(page)
        channel.registerObject("bridge", self)
        page.setWebChannel(channel)
        self._page = page
        self._channel = channel
        if isinstance(page, NetVizWebPage):
            page.js_auth_error.connect(self.auth_error_received)
        return channel

    def reset_ready(self) -> None:
        """Mark bridge unready prior to page reload; pending updates will queue."""
        if self._map_ready:
            self._map_ready = False
            self.map_ready_changed.emit(False)

    def _dispatch_js(self, script: str) -> None:
        self.dispatched_scripts.append(script)
        if self._page is not None:
            self._page.runJavaScript(script)

    def _flush_pending(self) -> None:
        if not self._map_ready:
            return
        if self._pending_config is not None:
            cfg_json = json.dumps(self._pending_config)
            self._dispatch_js(f"window.netvizMap && window.netvizMap.configureMap({cfg_json});")
        if self._pending_buildings is not None:
            bldgs_json = json.dumps(self._pending_buildings)
            sel_json = json.dumps(self._pending_selected_id)
            self._dispatch_js(
                f"window.netvizMap && window.netvizMap.syncBuildings({bldgs_json}, {sel_json});"
            )
        if self._pending_mode is not None:
            mode, bldg_id = self._pending_mode
            self._dispatch_js(
                "window.netvizMap && window.netvizMap.setMode("
                f"{json.dumps(mode)}, {json.dumps(bldg_id)});"
            )
            self._pending_mode = None
        if self._pending_jump is not None:
            lat, lon, zoom = self._pending_jump
            self._dispatch_js(
                f"window.netvizMap && window.netvizMap.jumpToLatLon({lat}, {lon}, {int(zoom)});"
            )
            self._pending_jump = None

    def configure_map(
        self,
        *,
        preferred_provider: str,
        google_api_key: str,
        offline_mode: bool = False,
        tile_base_url: str = "",
    ) -> None:
        """Queue or immediately apply map provider & API key configuration in JS."""
        self._pending_config = {
            "preferred_provider": preferred_provider,
            "google_api_key": google_api_key,
            "offline_mode": bool(offline_mode),
            "tile_base_url": tile_base_url,
        }
        if self._map_ready:
            cfg_json = json.dumps(self._pending_config)
            self._dispatch_js(f"window.netvizMap && window.netvizMap.configureMap({cfg_json});")

    def sync_buildings(
        self,
        buildings: Sequence[Building],
        selected_building_id: str | None = None,
    ) -> None:
        """Queue or immediately sync all buildings and selected handle state to JS."""
        self._pending_buildings = serialize_buildings_for_map(buildings)
        self._pending_selected_id = selected_building_id
        if self._map_ready:
            bldgs_json = json.dumps(self._pending_buildings)
            sel_json = json.dumps(self._pending_selected_id)
            self._dispatch_js(
                f"window.netvizMap && window.netvizMap.syncBuildings({bldgs_json}, {sel_json});"
            )

    def select_building(self, building_id: str | None) -> None:
        """Highlight a building and display its move/rotate handles in JS."""
        self._pending_selected_id = building_id
        if self._map_ready:
            self._dispatch_js(
                f"window.netvizMap && window.netvizMap.selectBuilding({json.dumps(building_id)});"
            )

    def jump_to_latlon(self, lat: float, lon: float, zoom: int = 18) -> None:
        """Pan/zoom the map viewport to WGS84 (lat, lon)."""
        self._pending_jump = (float(lat), float(lon), int(zoom))
        if self._map_ready:
            self._dispatch_js(
                "window.netvizMap && window.netvizMap.jumpToLatLon("
                f"{float(lat)}, {float(lon)}, {int(zoom)});"
            )
            self._pending_jump = None

    def set_mode(self, mode: str, building_id: str | None = None) -> None:
        """Set interactive map mode: 'pan' | 'trace' | 'place'."""
        self._pending_mode = (mode, building_id)
        if self._map_ready:
            self._dispatch_js(
                "window.netvizMap && window.netvizMap.setMode("
                f"{json.dumps(mode)}, {json.dumps(building_id)});"
            )
            self._pending_mode = None

    # ------------------------------------------------------------------
    # Slots invoked from JavaScript via QWebChannel
    # ------------------------------------------------------------------

    @Slot()
    def on_map_ready(self) -> None:
        """Invoked by map_controller.js once QWebChannel and initial map container are ready."""
        self._map_ready = True
        self.map_ready_changed.emit(True)
        self._flush_pending()

    @Slot(str)
    def on_polygon_traced(self, latlons_json: str) -> None:
        """Invoked by JS when user finishes tracing a new building polygon."""
        try:
            raw = json.loads(latlons_json)
        except json.JSONDecodeError as exc:
            LOGGER.warning("Invalid polygon JSON from map JS: %s", exc)
            return
        if not isinstance(raw, list):
            return
        coords: list[tuple[float, float]] = []
        for item in raw:
            if isinstance(item, list | tuple) and len(item) >= 2:
                coords.append((float(item[0]), float(item[1])))
        if len(coords) > 1 and coords[0] == coords[-1]:
            coords = coords[:-1]
        if len(coords) >= 3:
            self.polygon_traced.emit(coords)

    @Slot(str, float, float)
    def on_building_placed(self, building_id: str, lat: float, lon: float) -> None:
        """Invoked by JS when user click-places an unanchored building on the map."""
        self.building_placed.emit(str(building_id), float(lat), float(lon))

    @Slot(str, float, float, float)
    def on_building_transformed(
        self,
        building_id: str,
        origin_lat: float,
        origin_lon: float,
        rotation_deg: float,
    ) -> None:
        """Invoked by JS when user finishes dragging Move or Rotate handle."""
        self.building_transformed.emit(
            str(building_id),
            float(origin_lat),
            float(origin_lon),
            float(rotation_deg),
        )

    @Slot(str)
    def on_building_selected(self, building_id: str) -> None:
        """Invoked by JS when user clicks a building polygon."""
        self.building_selected.emit(str(building_id))

    @Slot(str)
    def on_auth_error(self, message: str) -> None:
        """Invoked by JS when Google Maps auth/referrer failure or watchdog timeout occurs."""
        msg = str(message).strip()
        if msg:
            LOGGER.warning("[MapJS] Auth/Provider Error: %s", msg)
            self.auth_error_received.emit(msg)

    @Slot(str)
    def on_provider_switched(self, provider_name: str) -> None:
        """Invoked by JS whenever the active map engine changes."""
        self.provider_switched.emit(str(provider_name))
