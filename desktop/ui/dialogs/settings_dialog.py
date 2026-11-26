"""Settings dialog for Google Maps API key, map provider, and offline tile cache options."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from desktop.config import (
    MAX_CACHE_MARGIN_M,
    MIN_CACHE_MARGIN_M,
    AppConfig,
    load_config,
    save_config,
)
from desktop.state import ProjectState

_PROVIDER_OPTIONS: list[tuple[str, str]] = [
    ("Google Maps (Primary + Leaflet Fallback)", "google"),
    ("Leaflet Satellite (Free)", "leaflet_satellite"),
    ("Leaflet OpenStreetMap (Free)", "leaflet_osm"),
]


class SettingsDialog(QDialog):
    """Settings dialog for Google Maps API Key, provider selection, and offline cache."""

    settings_changed = Signal(object)  # AppConfig

    def __init__(
        self,
        config: AppConfig | None = None,
        state: ProjectState | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("NetViz Studio — Settings")
        self.setMinimumWidth(520)
        self._config = config if config is not None else load_config()
        self._state = state
        self._zoom_checks: dict[int, QCheckBox] = {}
        self._build_ui()
        self._populate_from_config()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(12)

        # --- Map & Google API Key Group ---
        map_group = QGroupBox("Geo-Map & Google Maps API Key", self)
        form = QFormLayout(map_group)
        form.setSpacing(8)

        key_row = QHBoxLayout()
        self.api_key_edit = QLineEdit(self)
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setPlaceholderText("AIza... (stored locally in config.json only)")
        self.toggle_key_btn = QPushButton("Show", self)
        self.toggle_key_btn.setCheckable(True)
        self.toggle_key_btn.setFixedWidth(72)
        self.toggle_key_btn.toggled.connect(self._on_toggle_key_visibility)
        key_row.addWidget(self.api_key_edit, 1)
        key_row.addWidget(self.toggle_key_btn)
        form.addRow("Google Maps API Key:", key_row)

        self.help_note = QLabel(
            "<b>Google Cloud Setup:</b> Enable <i>Maps JavaScript API</i> (required) and "
            "<i>Geocoding API</i> (optional, for address jump search). "
            "If using HTTP Referrer restrictions, allow <code>127.0.0.1/*</code> "
            "or leave unrestricted.<br>"
            "<b>Privacy &amp; Security:</b> Your API key is stored strictly locally in "
            "<code>%APPDATA%/NetVizStudio/config.json</code> and is <b>never</b> exported into "
            "<code>.netviz</code> project archives.",
            self,
        )
        self.help_note.setWordWrap(True)
        self.help_note.setStyleSheet("color: #94A3B8; font-size: 12px;")
        form.addRow("", self.help_note)

        self.provider_combo = QComboBox(self)
        for label, value in _PROVIDER_OPTIONS:
            self.provider_combo.addItem(label, userData=value)
        form.addRow("Preferred Provider:", self.provider_combo)

        root.addWidget(map_group)

        # --- Offline Tile Cache Group ---
        cache_group = QGroupBox("Offline Map Tile Cache (tiles.sqlite)", self)
        cache_form = QFormLayout(cache_group)
        cache_form.setSpacing(8)

        self.margin_spin = QDoubleSpinBox(self)
        self.margin_spin.setRange(MIN_CACHE_MARGIN_M, MAX_CACHE_MARGIN_M)
        self.margin_spin.setSingleStep(50.0)
        self.margin_spin.setDecimals(0)
        self.margin_spin.setSuffix(" m")
        cache_form.addRow("Offline Cache Margin:", self.margin_spin)

        zoom_row = QHBoxLayout()
        for zoom in range(15, 21):
            cb = QCheckBox(f"Z{zoom}", self)
            self._zoom_checks[zoom] = cb
            zoom_row.addWidget(cb)
        zoom_row.addStretch(1)
        cache_form.addRow("Offline Cache Zooms:", zoom_row)

        root.addWidget(cache_group)

        # --- Action Buttons ---
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.cancel_btn = QPushButton("Cancel", self)
        self.cancel_btn.clicked.connect(self.reject)
        self.save_btn = QPushButton("Save && Reload Map", self)
        self.save_btn.setProperty("primary", True)
        self.save_btn.setDefault(True)
        self.save_btn.clicked.connect(self._on_save_clicked)
        btn_row.addWidget(self.cancel_btn)
        btn_row.addWidget(self.save_btn)
        root.addLayout(btn_row)

    def _populate_from_config(self) -> None:
        self.api_key_edit.setText(self._config.google_maps_api_key)
        idx = self.provider_combo.findData(self._config.preferred_provider)
        if idx >= 0:
            self.provider_combo.setCurrentIndex(idx)
        self.margin_spin.setValue(float(self._config.offline_cache_margin_m))
        active_zooms = set(self._config.offline_cache_zooms)
        for z, cb in self._zoom_checks.items():
            cb.setChecked(z in active_zooms)

    def _on_toggle_key_visibility(self, checked: bool) -> None:
        if checked:
            self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Normal)
            self.toggle_key_btn.setText("Hide")
        else:
            self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
            self.toggle_key_btn.setText("Show")

    def focus_api_key(self) -> None:
        """Focus and select the Google Maps API key input field."""
        self.api_key_edit.setFocus()
        self.api_key_edit.selectAll()

    def build_updated_config(self) -> AppConfig:
        """Read current UI widgets into an updated AppConfig instance."""
        selected_zooms = [z for z, cb in sorted(self._zoom_checks.items()) if cb.isChecked()]
        if not selected_zooms:
            selected_zooms = [16, 17, 18, 19]
        provider = str(self.provider_combo.currentData() or "google")
        self._config.google_maps_api_key = self.api_key_edit.text().strip()
        self._config.preferred_provider = provider
        self._config.offline_cache_margin_m = float(self.margin_spin.value())
        self._config.offline_cache_zooms = selected_zooms
        return self._config

    def _on_save_clicked(self) -> None:
        updated = self.build_updated_config()
        save_config(updated)
        if self._state is not None:
            prefs = self._state.project.map_preferences
            prefs.preferred_provider = updated.preferred_provider
            prefs.offline_cache_margin_m = updated.offline_cache_margin_m
            prefs.offline_cache_zooms = list(updated.offline_cache_zooms)
            self._state.map_preferences_changed.emit()
        self.settings_changed.emit(updated)
        self.accept()
