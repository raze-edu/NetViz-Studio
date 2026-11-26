"""Tab 2 Geo-Referenced Map & Building Manager view (`MapBuildingView`)."""

from __future__ import annotations

import copy
import logging
import re
from pathlib import Path
from typing import Any

import requests
from PySide6.QtCore import Qt, QThread, QUrl, Signal
from PySide6.QtGui import QColor, QUndoCommand
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from desktop.config import AppConfig, load_config
from desktop.state import ProjectState, sync_building_footprint
from desktop.tile_server import TileCacheServer, TilePrefetchWorker
from desktop.ui.dialogs.settings_dialog import SettingsDialog
from desktop.ui.map_tab.floor_editor import FloorEditorWidget
from desktop.ui.map_tab.map_bridge import (
    MapBridge,
    NetVizWebPage,
    apply_building_transform_to_state,
    apply_place_building_to_state,
    apply_traced_polygon_to_state,
)
from shared.geo_math import polygon_area_m2
from shared.models import Building, Floor, GeoAnchor, Point2D

LOGGER = logging.getLogger("netviz.map_view")

_LATLON_RE = re.compile(
    r"^\s*([+-]?\d+(?:\.\d+)?)\s*[,;\s]\s*([+-]?\d+(?:\.\d+)?)\s*$"
)

_PROVIDER_DISPLAY_NAMES = {
    "google": "Google Maps",
    "leaflet_satellite": "Leaflet Satellite",
    "leaflet_osm": "Leaflet OSM",
    "canvas": "Offline Tile Grid",
}


def parse_lat_lon(text: str) -> tuple[float, float] | None:
    """Parse an offline 'lat, lon' coordinate string, returning (lat, lon) or None."""
    match = _LATLON_RE.match(text or "")
    if not match:
        return None
    lat = float(match.group(1))
    lon = float(match.group(2))
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    return (lat, lon)


def geocode_address(
    query: str,
    *,
    google_api_key: str = "",
    timeout: float = 5.0,
) -> tuple[float, float, str] | None:
    """Resolve a free-text address using Google Geocoding API (if keyed) or Nominatim."""
    cleaned = query.strip()
    if not cleaned:
        return None

    if google_api_key.strip():
        try:
            resp = requests.get(
                "https://maps.googleapis.com/maps/api/geocode/json",
                params={"address": cleaned, "key": google_api_key.strip()},
                timeout=timeout,
            )
            if resp.status_code == 200:
                payload = resp.json()
                results = payload.get("results") or []
                if results:
                    loc = results[0]["geometry"]["location"]
                    addr = str(results[0].get("formatted_address") or cleaned)
                    return (float(loc["lat"]), float(loc["lng"]), addr)
        except (requests.RequestException, KeyError, ValueError, TypeError) as exc:
            LOGGER.debug("Google Geocoding fallback to Nominatim: %s", exc)

    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": cleaned, "format": "json", "limit": 1},
            headers={"User-Agent": "NetVizStudio/2.1"},
            timeout=timeout,
        )
        if resp.status_code == 200:
            items = resp.json()
            if isinstance(items, list) and items:
                top = items[0]
                return (
                    float(top["lat"]),
                    float(top["lon"]),
                    str(top.get("display_name") or cleaned),
                )
    except (requests.RequestException, KeyError, ValueError, TypeError) as exc:
        LOGGER.debug("Nominatim geocoding failed: %s", exc)
    return None


class GeocodeWorker(QThread):
    """Background geocoding worker so Location Jump never blocks the Qt UI thread."""

    location_found = Signal(float, float, str)
    geocode_failed = Signal(str)

    def __init__(
        self,
        query: str,
        *,
        google_api_key: str = "",
        timeout: float = 5.0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._query = query
        self._google_api_key = google_api_key
        self._timeout = timeout

    def run(self) -> None:
        result = geocode_address(
            self._query,
            google_api_key=self._google_api_key,
            timeout=self._timeout,
        )
        if result is None:
            self.geocode_failed.emit(f"Location not found: '{self._query}'")
            return
        lat, lon, label = result
        self.location_found.emit(lat, lon, label)


class _RemoveFloorCommand(QUndoCommand):
    """Undoable removal of a Floor from a Building."""

    def __init__(self, state: ProjectState, building_id: str, floor_id: str) -> None:
        super().__init__("Delete Floor")
        self._state = state
        self._building_id = building_id
        self._floor_id = floor_id
        bldg = state.project.get_building(building_id)
        self._prev_floors = copy.deepcopy(bldg.floors) if bldg is not None else []
        self._prev_components = copy.deepcopy(state.project.components)

    def redo(self) -> None:
        bldg = self._state.project.get_building(self._building_id)
        if bldg is not None:
            bldg.floors = [f for f in bldg.floors if f.id != self._floor_id]
            sync_building_footprint(bldg)
        changed_comps = False
        for comp in self._state.project.components:
            if comp.physical_floor_id == self._floor_id:
                comp.physical_building_id = None
                comp.physical_floor_id = None
                comp.physical_pos_m = None
                changed_comps = True
        self._state.notify_buildings_changed()
        if changed_comps:
            self._state.notify_components_changed()

    def undo(self) -> None:
        bldg = self._state.project.get_building(self._building_id)
        if bldg is not None:
            bldg.floors = copy.deepcopy(self._prev_floors)
            sync_building_footprint(bldg)
        self._state.project.components = copy.deepcopy(self._prev_components)
        self._state.notify_buildings_changed()
        self._state.notify_components_changed()


def _default_rect_polygon_m(width_m: float = 20.0, depth_m: float = 12.0) -> list[Point2D]:
    hw = width_m * 0.5
    hd = depth_m * 0.5
    return [
        Point2D(-hw, -hd),
        Point2D(hw, -hd),
        Point2D(hw, hd),
        Point2D(-hw, hd),
    ]


class MapBuildingView(QWidget):
    """Tab 2 container: Map Toolbar, API key/auth banners, Building/Floor Tree, and Geo Map."""

    open_floor_editor_requested = Signal(str, str)  # (building_id, floor_id)

    def __init__(
        self,
        state: ProjectState,
        *,
        tile_server: TileCacheServer | None = None,
        config: AppConfig | None = None,
        offline_mode: bool = False,
        enable_web_view: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._state = state
        self._config = config if config is not None else load_config()
        self._offline_mode = bool(offline_mode)
        self._enable_web_view = bool(enable_web_view)

        self._owns_tile_server = tile_server is None
        if tile_server is not None:
            self._tile_server = tile_server
        else:
            self._tile_server = TileCacheServer(
                self._state.ensure_tiles_db_path(),
                offline_mode=self._offline_mode,
            )
        if self._tile_server.port == 0:
            self._tile_server.start()

        self.bridge = MapBridge(self)
        self._missing_key_dismissed = False
        self._active_provider_name = self._config.preferred_provider
        self._syncing_inspector = False
        self._prefetch_worker: TilePrefetchWorker | None = None
        self._prefetch_dialog: QProgressDialog | None = None
        self._geocode_worker: GeocodeWorker | None = None

        self.web_page: NetVizWebPage | None = None
        self.web_view: QWebEngineView | None = None

        self._build_ui()
        self._wire_signals()
        self._refresh_banners_and_provider()
        self._refresh_building_tree()
        self._sync_floor_editor_context()
        self._configure_and_sync_map()

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # --- Top Map Toolbar ---
        self.toolbar = QToolBar("Map & Building Toolbar", self)
        self.toolbar.setMovable(False)

        self.api_key_btn = QPushButton("🔑 Map API Key", self)
        self.api_key_btn.setToolTip(
            "Configure Google Maps API Key and preferred map provider (local only)"
        )
        self.api_key_btn.clicked.connect(lambda: self.open_api_key_settings())
        self.toolbar.addWidget(self.api_key_btn)

        self.provider_label = QLabel("Provider: Google Maps", self)
        self.provider_label.setStyleSheet(
            "background-color: #1E293B; border: 1px solid #334155; "
            "border-radius: 4px; padding: 4px 8px; color: #38BDF8; font-weight: 600;"
        )
        self.toolbar.addWidget(self.provider_label)
        self.toolbar.addSeparator()

        self.trace_btn = QPushButton("Trace Building", self)
        self.trace_btn.setCheckable(True)
        self.trace_btn.setToolTip("Trace a new geo-referenced building footprint on the map")
        self.trace_btn.toggled.connect(self._on_trace_toggled)
        self.toolbar.addWidget(self.trace_btn)

        self.add_manual_btn = QPushButton("Add Manual Building", self)
        self.add_manual_btn.setToolTip("Create an unplaced building with a default Floor 0")
        self.add_manual_btn.clicked.connect(self._on_add_manual_building)
        self.toolbar.addWidget(self.add_manual_btn)

        self.place_btn = QPushButton("Place on Map", self)
        self.place_btn.setToolTip("Click on the map to anchor the selected unplaced building")
        self.place_btn.clicked.connect(self._on_place_selected_building)
        self.toolbar.addWidget(self.place_btn)

        self.toolbar.addSeparator()

        jump_lbl = QLabel("Jump:", self)
        self.toolbar.addWidget(jump_lbl)
        self.jump_input = QLineEdit(self)
        self.jump_input.setPlaceholderText("52.5200, 13.4050 or address...")
        self.jump_input.setMinimumWidth(210)
        self.jump_input.returnPressed.connect(self.jump_to_query)
        self.toolbar.addWidget(self.jump_input)

        self.jump_btn = QPushButton("Go", self)
        self.jump_btn.clicked.connect(self.jump_to_query)
        self.toolbar.addWidget(self.jump_btn)

        self.toolbar.addSeparator()

        self.cache_tiles_btn = QPushButton("Cache Map Tiles Around Buildings", self)
        self.cache_tiles_btn.setToolTip(
            "Pre-download offline OSM & Satellite tiles into tiles.sqlite for field use"
        )
        self.cache_tiles_btn.clicked.connect(self._on_cache_tiles_clicked)
        self.toolbar.addWidget(self.cache_tiles_btn)

        root.addWidget(self.toolbar)

        # --- Amber Missing Google API Key Banner ---
        self.missing_key_banner = QFrame(self)
        self.missing_key_banner.setStyleSheet(
            "QFrame { background-color: #451A03; border-bottom: 1px solid #F59E0B; }"
        )
        mk_layout = QHBoxLayout(self.missing_key_banner)
        mk_layout.setContentsMargins(10, 6, 10, 6)
        self.missing_key_label = QLabel(
            "Google Maps is selected as preferred provider, but no API key is configured. "
            "Map is currently using the free offline-capable Leaflet fallback.",
            self.missing_key_banner,
        )
        self.missing_key_label.setStyleSheet("color: #FDE68A; font-weight: 600;")
        self.missing_key_label.setWordWrap(True)
        mk_layout.addWidget(self.missing_key_label, 1)

        self.banner_set_key_btn = QPushButton("Set Google Maps API Key", self.missing_key_banner)
        self.banner_set_key_btn.setStyleSheet(
            "background-color: #D97706; border-color: #F59E0B; color: #FFFFFF; font-weight: 600;"
        )
        self.banner_set_key_btn.clicked.connect(lambda: self.open_api_key_settings())
        mk_layout.addWidget(self.banner_set_key_btn)

        self.banner_dismiss_btn = QPushButton("Dismiss", self.missing_key_banner)
        self.banner_dismiss_btn.clicked.connect(self._on_dismiss_missing_key_banner)
        mk_layout.addWidget(self.banner_dismiss_btn)
        root.addWidget(self.missing_key_banner)

        # --- Google Auth / Referrer Error Banner ---
        self.auth_error_banner = QFrame(self)
        self.auth_error_banner.setStyleSheet(
            "QFrame { background-color: #450A0A; border-bottom: 1px solid #EF4444; }"
        )
        ae_layout = QHBoxLayout(self.auth_error_banner)
        ae_layout.setContentsMargins(10, 6, 10, 6)
        self.auth_error_label = QLabel("", self.auth_error_banner)
        self.auth_error_label.setStyleSheet("color: #FCA5A5; font-weight: 600;")
        self.auth_error_label.setWordWrap(True)
        ae_layout.addWidget(self.auth_error_label, 1)

        self.auth_settings_btn = QPushButton("Check API Key", self.auth_error_banner)
        self.auth_settings_btn.clicked.connect(lambda: self.open_api_key_settings())
        ae_layout.addWidget(self.auth_settings_btn)

        self.auth_dismiss_btn = QPushButton("Dismiss", self.auth_error_banner)
        self.auth_dismiss_btn.clicked.connect(self.auth_error_banner.hide)
        ae_layout.addWidget(self.auth_dismiss_btn)
        self.auth_error_banner.hide()
        root.addWidget(self.auth_error_banner)

        # --- Main Splitter: Left Building/Floor Tree | Right Map Pane ---
        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)

        left_pane = QWidget(self.splitter)
        left_layout = QVBoxLayout(left_pane)
        left_layout.setContentsMargins(8, 8, 8, 8)
        left_layout.setSpacing(8)

        tree_header = QLabel("Buildings & Floors", left_pane)
        tree_header.setStyleSheet("font-weight: 700; font-size: 13px; color: #F8FAFC;")
        left_layout.addWidget(tree_header)

        self.building_tree = QTreeWidget(left_pane)
        self.building_tree.setHeaderLabels(["Name", "Status / Details"])
        self.building_tree.setColumnWidth(0, 150)
        self.building_tree.itemSelectionChanged.connect(self._on_tree_selection_changed)
        self.building_tree.itemDoubleClicked.connect(self._on_tree_item_double_clicked)
        left_layout.addWidget(self.building_tree, 1)

        # Tree action buttons
        row1 = QHBoxLayout()
        self.tree_add_bldg_btn = QPushButton("+ Building", left_pane)
        self.tree_add_bldg_btn.clicked.connect(self._on_add_manual_building)
        self.tree_rename_btn = QPushButton("Rename", left_pane)
        self.tree_rename_btn.clicked.connect(self._on_rename_selected)
        self.tree_delete_btn = QPushButton("Delete", left_pane)
        self.tree_delete_btn.clicked.connect(self._on_delete_selected)
        row1.addWidget(self.tree_add_bldg_btn)
        row1.addWidget(self.tree_rename_btn)
        row1.addWidget(self.tree_delete_btn)
        left_layout.addLayout(row1)

        row2 = QHBoxLayout()
        self.add_floor_copy_btn = QPushButton("+ Floor (Copy)", left_pane)
        self.add_floor_copy_btn.clicked.connect(lambda: self._on_add_floor(copy_previous=True))
        self.add_floor_blank_btn = QPushButton("+ Floor (Blank)", left_pane)
        self.add_floor_blank_btn.clicked.connect(lambda: self._on_add_floor(copy_previous=False))
        row2.addWidget(self.add_floor_copy_btn)
        row2.addWidget(self.add_floor_blank_btn)
        left_layout.addLayout(row2)

        # Building Properties & Geo Inspector
        props_group = QGroupBox("Selected Building Geo & Floor Height", left_pane)
        props_form = QFormLayout(props_group)
        props_form.setSpacing(6)

        self.status_badge_label = QLabel("No building selected", props_group)
        self.status_badge_label.setStyleSheet("color: #94A3B8; font-weight: 600;")
        props_form.addRow("Anchor:", self.status_badge_label)

        self.floor_height_spin = QDoubleSpinBox(props_group)
        self.floor_height_spin.setRange(2.0, 20.0)
        self.floor_height_spin.setSingleStep(0.1)
        self.floor_height_spin.setDecimals(2)
        self.floor_height_spin.setSuffix(" m")
        self.floor_height_spin.valueChanged.connect(self._on_floor_height_changed)
        props_form.addRow("Floor Height:", self.floor_height_spin)

        self.origin_lat_spin = QDoubleSpinBox(props_group)
        self.origin_lat_spin.setRange(-85.0, 85.0)
        self.origin_lat_spin.setDecimals(6)
        self.origin_lat_spin.setSingleStep(0.0001)
        self.origin_lat_spin.editingFinished.connect(self._on_geo_spin_committed)
        props_form.addRow("Origin Lat:", self.origin_lat_spin)

        self.origin_lon_spin = QDoubleSpinBox(props_group)
        self.origin_lon_spin.setRange(-180.0, 180.0)
        self.origin_lon_spin.setDecimals(6)
        self.origin_lon_spin.setSingleStep(0.0001)
        self.origin_lon_spin.editingFinished.connect(self._on_geo_spin_committed)
        props_form.addRow("Origin Lon:", self.origin_lon_spin)

        self.rotation_spin = QDoubleSpinBox(props_group)
        self.rotation_spin.setRange(-360.0, 360.0)
        self.rotation_spin.setDecimals(2)
        self.rotation_spin.setSingleStep(1.0)
        self.rotation_spin.setSuffix("° CW")
        self.rotation_spin.editingFinished.connect(self._on_geo_spin_committed)
        props_form.addRow("Rotation:", self.rotation_spin)

        self.area_label = QLabel("—", props_group)
        props_form.addRow("Floor 0 Area:", self.area_label)

        left_layout.addWidget(props_group)
        left_pane.setMinimumWidth(290)
        left_pane.setMaximumWidth(380)

        # --- Right Map & Floor CAD Pane ---
        self.right_pane = QWidget(self.splitter)
        right_layout = QVBoxLayout(self.right_pane)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        mode_bar = QFrame(self.right_pane)
        mode_bar.setStyleSheet(
            "QFrame { background-color: #0F172A; border-bottom: 1px solid #334155; }"
        )
        mode_layout = QHBoxLayout(mode_bar)
        mode_layout.setContentsMargins(8, 4, 8, 4)
        mode_layout.setSpacing(6)

        self.mode_map_btn = QPushButton("Geo Map View", mode_bar)
        self.mode_map_btn.setCheckable(True)
        self.mode_map_btn.setChecked(True)
        self.mode_map_btn.clicked.connect(self.show_geo_map)
        mode_layout.addWidget(self.mode_map_btn)

        self.mode_floor_btn = QPushButton("Precision Floor CAD Editor", mode_bar)
        self.mode_floor_btn.setCheckable(True)
        self.mode_floor_btn.clicked.connect(lambda: self.show_floor_editor())
        mode_layout.addWidget(self.mode_floor_btn)

        self.active_floor_label = QLabel("", mode_bar)
        self.active_floor_label.setStyleSheet("color: #94A3B8; font-size: 12px;")
        mode_layout.addStretch(1)
        mode_layout.addWidget(self.active_floor_label)
        right_layout.addWidget(mode_bar)

        self.view_stack = QStackedWidget(self.right_pane)
        if self._enable_web_view:
            self.web_page = NetVizWebPage(self)
            self.web_view = QWebEngineView(self.view_stack)
            self.web_view.setPage(self.web_page)
            # Section 6 #13 zero-race handshake: attach QWebChannel BEFORE setUrl!
            self.bridge.attach_to_page(self.web_page)
            self.web_view.setUrl(QUrl(self._tile_server.asset_url("map.html")))
            self.view_stack.addWidget(self.web_view)
        else:
            fallback_lbl = QLabel("Map WebView disabled in headless test mode", self.view_stack)
            fallback_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.view_stack.addWidget(fallback_lbl)

        self.floor_editor = FloorEditorWidget(self._state, parent=self.view_stack)
        self.view_stack.addWidget(self.floor_editor)
        right_layout.addWidget(self.view_stack, 1)

        self.splitter.addWidget(left_pane)
        self.splitter.addWidget(self.right_pane)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        root.addWidget(self.splitter, 1)

    def _wire_signals(self) -> None:
        self._state.project_reloaded.connect(self._on_project_or_buildings_changed)
        self._state.buildings_changed.connect(self._on_project_or_buildings_changed)
        self._state.floor_geometry_changed.connect(self._on_floor_geometry_changed)
        self._state.map_preferences_changed.connect(lambda: self.reload_map_settings())

        self.bridge.polygon_traced.connect(self._on_bridge_polygon_traced)
        self.bridge.building_placed.connect(self._on_bridge_building_placed)
        self.bridge.building_transformed.connect(self._on_bridge_building_transformed)
        self.bridge.building_selected.connect(self._on_bridge_building_selected)
        self.bridge.auth_error_received.connect(self._on_bridge_auth_error)
        self.bridge.provider_switched.connect(self._on_bridge_provider_switched)

    # ------------------------------------------------------------------
    # Settings & Banners
    # ------------------------------------------------------------------

    def _effective_provider(self) -> str:
        return self._config.preferred_provider or "google"

    def _refresh_banners_and_provider(self) -> None:
        pref = self._effective_provider()
        api_key = self._config.effective_google_maps_api_key()
        show_missing = pref == "google" and not api_key and not self._missing_key_dismissed
        self.missing_key_banner.setVisible(show_missing)

        display = _PROVIDER_DISPLAY_NAMES.get(
            self._active_provider_name, self._active_provider_name
        )
        if self._offline_mode:
            display = f"{display} (Offline)"
        self.provider_label.setText(f"Provider: {display}")

    def _on_dismiss_missing_key_banner(self) -> None:
        self._missing_key_dismissed = True
        self.missing_key_banner.hide()

    def open_api_key_settings(self) -> int:
        """Open SettingsDialog focused directly on the Google Maps API key input."""
        dlg = SettingsDialog(config=copy.deepcopy(self._config), state=self._state, parent=self)
        dlg.settings_changed.connect(self.reload_map_settings)
        dlg.focus_api_key()
        return dlg.exec()

    def reload_map_settings(self, new_config: AppConfig | None = None) -> None:
        """Reload map config & banners and push updated provider/key live to map JS."""
        prev_provider = self._config.preferred_provider
        prev_key = self._config.effective_google_maps_api_key()
        self._config = new_config if new_config is not None else load_config()

        if (
            self._config.preferred_provider != prev_provider
            or self._config.effective_google_maps_api_key() != prev_key
        ):
            self._missing_key_dismissed = False
            self.auth_error_banner.hide()

        self._active_provider_name = self._config.preferred_provider
        self._refresh_banners_and_provider()
        self._configure_and_sync_map()

    def _configure_and_sync_map(self) -> None:
        self.bridge.configure_map(
            preferred_provider=self._effective_provider(),
            google_api_key=self._config.effective_google_maps_api_key(),
            offline_mode=self._offline_mode,
            tile_base_url=self._tile_server.base_url,
        )
        self.bridge.sync_buildings(
            self._state.project.buildings,
            selected_building_id=self._state.selected_building_id,
        )

    # ------------------------------------------------------------------
    # Building / Floor Tree & Inspector
    # ------------------------------------------------------------------

    def _selected_building(self) -> Building | None:
        if self._state.selected_building_id:
            b = self._state.project.get_building(self._state.selected_building_id)
            if b is not None:
                return b
        return self._state.project.buildings[0] if self._state.project.buildings else None

    def _selected_floor(self) -> Floor | None:
        bldg = self._selected_building()
        if bldg is None:
            return None
        if self._state.selected_floor_id:
            for fl in bldg.floors:
                if fl.id == self._state.selected_floor_id:
                    return fl
        return bldg.floors[0] if bldg.floors else None

    def _refresh_building_tree(self) -> None:
        self.building_tree.blockSignals(True)
        try:
            self.building_tree.clear()
            selected_b_id = self._state.selected_building_id
            selected_f_id = self._state.selected_floor_id
            target_item: QTreeWidgetItem | None = None

            for bldg in self._state.project.buildings:
                b_item = QTreeWidgetItem(self.building_tree)
                b_item.setData(0, Qt.ItemDataRole.UserRole, ("building", bldg.id, None))
                b_item.setText(0, bldg.name)
                if bldg.geo_anchor is None:
                    b_item.setText(1, "Unplaced")
                    b_item.setForeground(1, QColor("#F59E0B"))
                else:
                    b_item.setText(
                        1,
                        f"{bldg.geo_anchor.origin_lat:.5f}, {bldg.geo_anchor.origin_lon:.5f} "
                        f"({bldg.geo_anchor.rotation_deg:.1f}°)",
                    )
                    b_item.setForeground(1, QColor("#38BDF8"))
                b_item.setExpanded(True)

                if bldg.id == selected_b_id and target_item is None:
                    target_item = b_item

                for floor in sorted(bldg.floors, key=lambda f: f.level_index):
                    f_item = QTreeWidgetItem(b_item)
                    f_item.setData(0, Qt.ItemDataRole.UserRole, ("floor", bldg.id, floor.id))
                    f_item.setText(0, f"L{floor.level_index}: {floor.name}")
                    f_item.setText(1, f"{len(floor.rooms)} rooms · {len(floor.doors)} doors")
                    if floor.id == selected_f_id:
                        target_item = f_item

            if target_item is not None:
                self.building_tree.setCurrentItem(target_item)
        finally:
            self.building_tree.blockSignals(False)

        self._refresh_building_inspector()

    def _refresh_building_inspector(self) -> None:
        bldg = self._selected_building()
        self._syncing_inspector = True
        try:
            if bldg is None:
                self.place_btn.setEnabled(False)
                self.status_badge_label.setText("No building selected")
                self.status_badge_label.setStyleSheet("color: #94A3B8;")
                self.floor_height_spin.setEnabled(False)
                self.origin_lat_spin.setEnabled(False)
                self.origin_lon_spin.setEnabled(False)
                self.rotation_spin.setEnabled(False)
                self.area_label.setText("—")
                return

            self.floor_height_spin.setEnabled(True)
            self.floor_height_spin.setValue(float(bldg.floor_height_m))

            if bldg.geo_anchor is None:
                self.place_btn.setEnabled(True)
                self.status_badge_label.setText("Unplaced (Click 'Place on Map')")
                self.status_badge_label.setStyleSheet(
                    "color: #FCD34D; background-color: #451A03; "
                    "border: 1px solid #F59E0B; border-radius: 4px; padding: 2px 6px;"
                )
                self.origin_lat_spin.setEnabled(False)
                self.origin_lon_spin.setEnabled(False)
                self.rotation_spin.setEnabled(False)
            else:
                self.place_btn.setEnabled(False)
                self.status_badge_label.setText("Anchored on Map")
                self.status_badge_label.setStyleSheet("color: #4ADE80; font-weight: 600;")
                self.origin_lat_spin.setEnabled(True)
                self.origin_lon_spin.setEnabled(True)
                self.rotation_spin.setEnabled(True)
                self.origin_lat_spin.setValue(bldg.geo_anchor.origin_lat)
                self.origin_lon_spin.setValue(bldg.geo_anchor.origin_lon)
                self.rotation_spin.setValue(bldg.geo_anchor.rotation_deg)

            if bldg.floors and bldg.floors[0].outer_polygon_m:
                area = polygon_area_m2(bldg.floors[0].outer_polygon_m)
                self.area_label.setText(f"{area:.1f} m²")
            else:
                self.area_label.setText("0.0 m²")
        finally:
            self._syncing_inspector = False

    def _on_tree_selection_changed(self) -> None:
        item = self.building_tree.currentItem()
        if item is None:
            return
        meta = item.data(0, Qt.ItemDataRole.UserRole)
        if not meta:
            return
        kind, bldg_id, floor_id = meta
        self._state.selected_building_id = bldg_id
        if kind == "floor" and floor_id:
            self._state.selected_floor_id = floor_id
            self._state.set_selection("floor", floor_id)
        else:
            bldg = self._state.project.get_building(bldg_id)
            if bldg is not None and bldg.floors:
                self._state.selected_floor_id = bldg.floors[0].id
            self._state.set_selection("building", bldg_id)

        self._refresh_building_inspector()
        self._sync_floor_editor_context()
        self.bridge.select_building(bldg_id)
        bldg = self._state.project.get_building(bldg_id)
        if bldg is not None and bldg.geo_anchor is not None:
            self.bridge.jump_to_latlon(
                bldg.geo_anchor.origin_lat,
                bldg.geo_anchor.origin_lon,
                18,
            )

    def _on_tree_item_double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        meta = item.data(0, Qt.ItemDataRole.UserRole)
        if not meta:
            return
        kind, bldg_id, floor_id = meta
        if kind == "floor" and floor_id:
            self.show_floor_editor(bldg_id, floor_id)
        elif kind == "building":
            bldg = self._state.project.get_building(bldg_id)
            if bldg is not None and bldg.floors:
                self.show_floor_editor(bldg_id, bldg.floors[0].id)

    def _sync_floor_editor_context(self) -> None:
        bldg = self._selected_building()
        floor = self._selected_floor()
        b_id = bldg.id if bldg is not None else None
        f_id = floor.id if floor is not None else None
        self.floor_editor.set_floor(b_id, f_id)
        if bldg is not None and floor is not None:
            self.active_floor_label.setText(f"{bldg.name} — L{floor.level_index}: {floor.name}")
        elif bldg is not None:
            self.active_floor_label.setText(bldg.name)
        else:
            self.active_floor_label.setText("No building selected")

    def show_geo_map(self) -> None:
        """Switch right pane to the interactive Geo Map."""
        self.view_stack.setCurrentIndex(0)
        self.mode_map_btn.setChecked(True)
        self.mode_floor_btn.setChecked(False)

    def show_floor_editor(
        self,
        building_id: str | None = None,
        floor_id: str | None = None,
    ) -> None:
        """Switch right pane to the Precision Floor CAD Editor for the target building/floor."""
        if building_id is not None:
            self._state.selected_building_id = building_id
        if floor_id is not None:
            self._state.selected_floor_id = floor_id
        self._refresh_building_tree()
        self._sync_floor_editor_context()
        self.view_stack.setCurrentIndex(1)
        self.mode_map_btn.setChecked(False)
        self.mode_floor_btn.setChecked(True)
        self.floor_editor.view.fit_floor_in_view()
        if building_id and floor_id:
            self.open_floor_editor_requested.emit(building_id, floor_id)

    def _on_floor_height_changed(self, value: float) -> None:
        if self._syncing_inspector:
            return
        bldg = self._selected_building()
        if bldg is None or abs(bldg.floor_height_m - value) < 1e-6:
            return
        self._state.update_building(bldg.id, floor_height_m=float(value))

    def _on_geo_spin_committed(self) -> None:
        if self._syncing_inspector:
            return
        bldg = self._selected_building()
        if bldg is None or bldg.geo_anchor is None:
            return
        new_anchor = GeoAnchor(
            origin_lat=float(self.origin_lat_spin.value()),
            origin_lon=float(self.origin_lon_spin.value()),
            rotation_deg=float(self.rotation_spin.value()) % 360.0,
        )
        self._state.set_building_anchor(bldg.id, new_anchor)

    # ------------------------------------------------------------------
    # Building / Floor CRUD
    # ------------------------------------------------------------------

    def _on_trace_toggled(self, checked: bool) -> None:
        if checked:
            self.bridge.set_mode("trace")
            self._state.status_message.emit(
                "Click map vertices to trace a building; double-click or click Finish.", 5000
            )
        else:
            self.bridge.set_mode("pan")

    def _on_add_manual_building(self) -> None:
        existing_names = {b.name.lower() for b in self._state.project.buildings}
        idx = len(self._state.project.buildings) + 1
        while f"Building {idx}".lower() in existing_names:
            idx += 1
        floor0 = Floor(
            name="Ground Floor",
            outer_polygon_m=_default_rect_polygon_m(),
            level_index=0,
        )
        bldg = Building(name=f"Building {idx}", geo_anchor=None, floors=[floor0])
        self._state.add_building(bldg)
        self._state.selected_building_id = bldg.id
        self._state.selected_floor_id = floor0.id
        self._refresh_building_tree()
        self._state.status_message.emit(
            f"Created unplaced '{bldg.name}'. Use 'Place on Map' to geo-anchor it.", 4000
        )

    def _on_place_selected_building(self) -> None:
        bldg = self._selected_building()
        if bldg is None:
            return
        self.trace_btn.setChecked(False)
        self.bridge.set_mode("place", bldg.id)
        self._state.status_message.emit(
            f"Click on the map to place '{bldg.name}'.", 5000
        )

    def _on_add_floor(self, *, copy_previous: bool) -> None:
        bldg = self._selected_building()
        if bldg is None:
            return
        next_level = (
            max((f.level_index for f in bldg.floors), default=-1) + 1
            if bldg.floors
            else 0
        )
        source_floor = self._selected_floor() or (bldg.floors[-1] if bldg.floors else None)
        if copy_previous and source_floor is not None and source_floor.outer_polygon_m:
            poly = [Point2D(pt.x, pt.y) for pt in source_floor.outer_polygon_m]
        else:
            poly = _default_rect_polygon_m()

        name = "Ground Floor" if next_level == 0 else f"Floor {next_level}"
        floor = Floor(name=name, outer_polygon_m=poly, level_index=next_level)
        self._state.add_floor(bldg.id, floor)
        self._state.selected_floor_id = floor.id
        self._refresh_building_tree()

    def _on_rename_selected(self) -> None:
        item = self.building_tree.currentItem()
        if item is None:
            return
        meta = item.data(0, Qt.ItemDataRole.UserRole)
        if not meta:
            return
        kind, bldg_id, floor_id = meta
        if kind == "floor" and floor_id:
            floor = self._state.project.get_floor(bldg_id, floor_id)
            if floor is None:
                return
            new_name, ok = QInputDialog.getText(
                self, "Rename Floor", "Floor name:", text=floor.name
            )
            if ok and new_name.strip():
                from desktop.state import UpdateFloorCommand

                self._state.undo_stack.push(
                    UpdateFloorCommand(self._state, bldg_id, floor_id, name=new_name.strip())
                )
        else:
            bldg = self._state.project.get_building(bldg_id)
            if bldg is None:
                return
            new_name, ok = QInputDialog.getText(
                self, "Rename Building", "Building name:", text=bldg.name
            )
            if ok and new_name.strip():
                self._state.update_building(bldg_id, name=new_name.strip())

    def _on_delete_selected(self) -> None:
        item = self.building_tree.currentItem()
        if item is None:
            return
        meta = item.data(0, Qt.ItemDataRole.UserRole)
        if not meta:
            return
        kind, bldg_id, floor_id = meta
        if kind == "floor" and floor_id:
            bldg = self._state.project.get_building(bldg_id)
            if bldg is None or len(bldg.floors) <= 1:
                QMessageBox.information(
                    self, "Delete Floor", "A building must keep at least one floor."
                )
                return
            self._state.undo_stack.push(_RemoveFloorCommand(self._state, bldg_id, floor_id))
        else:
            self._state.remove_building(bldg_id)

    # ------------------------------------------------------------------
    # Location Jump Bar & Tile Prefetch
    # ------------------------------------------------------------------

    def jump_to_query(self, query_text: str | None = None) -> bool:
        """Jump map to offline 'lat, lon' coordinates or geocode address online."""
        text = (query_text if query_text is not None else self.jump_input.text()).strip()
        if not text:
            return False

        coords = parse_lat_lon(text)
        if coords is not None:
            lat, lon = coords
            self.bridge.jump_to_latlon(lat, lon, 18)
            self._state.status_message.emit(
                f"Jumped to {lat:.6f}, {lon:.6f}", 3000
            )
            return True

        if self._offline_mode:
            self._state.status_message.emit(
                "Offline mode: enter 'lat, lon' coordinates (e.g. 52.5200, 13.4050).", 4000
            )
            return False

        if self._geocode_worker is not None and self._geocode_worker.isRunning():
            return False

        self.jump_btn.setEnabled(False)
        worker = GeocodeWorker(
            text,
            google_api_key=self._config.effective_google_maps_api_key(),
            timeout=5.0,
            parent=self,
        )
        worker.location_found.connect(self._on_geocode_found)
        worker.geocode_failed.connect(self._on_geocode_failed)
        worker.finished.connect(lambda: self.jump_btn.setEnabled(True))
        self._geocode_worker = worker
        worker.start()
        return True

    def _on_geocode_found(self, lat: float, lon: float, label: str) -> None:
        self.bridge.jump_to_latlon(lat, lon, 18)
        self._state.status_message.emit(f"Jumped to {label} ({lat:.5f}, {lon:.5f})", 4000)

    def _on_geocode_failed(self, error_msg: str) -> None:
        self._state.status_message.emit(error_msg, 4000)

    def start_tile_prefetch(
        self,
        *,
        tiles_db_path: Path | None = None,
        providers: tuple[str, ...] = ("osm", "esri_satellite"),
        delay_s: float = 0.05,
    ) -> TilePrefetchWorker | None:
        """Start background TilePrefetchWorker for anchored buildings."""
        anchored = [b for b in self._state.project.buildings if b.geo_anchor is not None]
        if not anchored:
            return None
        db_path = tiles_db_path or self._state.ensure_tiles_db_path()
        worker = TilePrefetchWorker(
            anchored,
            db_path,
            margin_m=self._config.offline_cache_margin_m,
            zooms=self._config.offline_cache_zooms,
            providers=providers,
            delay_s=delay_s,
            parent=self,
        )
        self._prefetch_worker = worker
        return worker

    def _on_cache_tiles_clicked(self) -> None:
        if self._offline_mode:
            QMessageBox.information(
                self,
                "Offline Mode Active",
                "Tile prefetching is disabled while --offline mode is active.",
            )
            return
        worker = self.start_tile_prefetch()
        if worker is None:
            QMessageBox.information(
                self,
                "No Anchored Buildings",
                "Trace or place at least one building on the map before caching tiles.",
            )
            return

        dlg = QProgressDialog(
            "Caching offline map tiles around buildings...",
            "Cancel",
            0,
            100,
            self,
        )
        dlg.setWindowTitle("Offline Tile Prefetch")
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        self._prefetch_dialog = dlg

        def _on_progress(done: int, total: int, status_text: str) -> None:
            if total > 0:
                dlg.setMaximum(total)
                dlg.setValue(done)
            dlg.setLabelText(f"{status_text} ({done}/{total})")

        def _on_finished(downloaded: int, skipped: int, cancelled: bool) -> None:
            dlg.close()
            state_txt = "Cancelled" if cancelled else "Completed"
            self._state.status_message.emit(
                f"Tile Cache {state_txt}: {downloaded} downloaded, {skipped} already cached.",
                5000,
            )

        dlg.canceled.connect(worker.cancel)
        worker.progress.connect(_on_progress)
        worker.finished_prefetch.connect(_on_finished)
        worker.start()
        dlg.show()

    # ------------------------------------------------------------------
    # State & Bridge Handlers
    # ------------------------------------------------------------------

    def _on_project_or_buildings_changed(self) -> None:
        self._refresh_building_tree()
        bldg = self._selected_building()
        floor = self._selected_floor()
        b_id = bldg.id if bldg is not None else None
        f_id = floor.id if floor is not None else None
        if (
            b_id != self.floor_editor.view._building_id
            or f_id != self.floor_editor.view._floor_id
        ):
            self._sync_floor_editor_context()
        else:
            self.floor_editor.view.rebuild_scene()
        self.bridge.sync_buildings(
            self._state.project.buildings,
            selected_building_id=self._state.selected_building_id,
        )

    def _on_floor_geometry_changed(self, building_id: str, floor_id: str) -> None:
        self._refresh_building_tree()
        self.bridge.sync_buildings(
            self._state.project.buildings,
            selected_building_id=self._state.selected_building_id,
        )

    def _on_bridge_polygon_traced(self, latlons: list[Any]) -> None:
        self.trace_btn.blockSignals(True)
        self.trace_btn.setChecked(False)
        self.trace_btn.blockSignals(False)
        bldg = apply_traced_polygon_to_state(self._state, latlons)
        self._state.selected_building_id = bldg.id
        if bldg.floors:
            self._state.selected_floor_id = bldg.floors[0].id
        self._refresh_building_tree()

    def _on_bridge_building_placed(self, building_id: str, lat: float, lon: float) -> None:
        apply_place_building_to_state(self._state, building_id, lat, lon)
        self._state.selected_building_id = building_id
        self._refresh_building_tree()

    def _on_bridge_building_transformed(
        self,
        building_id: str,
        origin_lat: float,
        origin_lon: float,
        rotation_deg: float,
    ) -> None:
        apply_building_transform_to_state(
            self._state, building_id, origin_lat, origin_lon, rotation_deg
        )

    def _on_bridge_building_selected(self, building_id: str) -> None:
        self._state.selected_building_id = building_id
        bldg = self._state.project.get_building(building_id)
        if bldg is not None and bldg.floors:
            self._state.selected_floor_id = bldg.floors[0].id
        self._state.set_selection("building", building_id)
        self._refresh_building_tree()

    def _on_bridge_auth_error(self, message: str) -> None:
        self.auth_error_label.setText(message)
        self.auth_error_banner.show()
        self._state.status_message.emit(message, 6000)

    def _on_bridge_provider_switched(self, provider_name: str) -> None:
        self._active_provider_name = provider_name
        self._refresh_banners_and_provider()

    def cleanup(self) -> None:
        """Stop owned servers/workers/web_view cleanly when closing."""
        if self.web_view is not None:
            self.web_view.stop()
        if self._prefetch_worker is not None and self._prefetch_worker.isRunning():
            self._prefetch_worker.cancel()
            self._prefetch_worker.wait(2000)
        if self._geocode_worker is not None and self._geocode_worker.isRunning():
            self._geocode_worker.wait(2000)
        if self._owns_tile_server:
            self._tile_server.stop()

    def closeEvent(self, event: object) -> None:  # type: ignore[override]
        self.cleanup()
        super().closeEvent(event)  # type: ignore[arg-type]
