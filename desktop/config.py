"""Local-only desktop configuration, templates library, autosave, and logging paths."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shared.bundle_io import export_templates_json, import_templates_json
from shared.models import ComponentTemplate

APP_DIR_NAME = "NetVizStudio"
VALID_PROVIDERS = ("google", "leaflet_osm", "leaflet_satellite")
MIN_NODE_ALPHA = 77   # ~30%
MAX_NODE_ALPHA = 242  # ~95%
DEFAULT_NODE_ALPHA = 165  # ~65%
MIN_CACHE_MARGIN_M = 50.0
MAX_CACHE_MARGIN_M = 2000.0
DEFAULT_CACHE_MARGIN_M = 250.0
DEFAULT_CACHE_ZOOMS = [16, 17, 18, 19]


def get_config_dir() -> Path:
    """Return the local NetViz Studio config directory (respecting NETVIZ_CONFIG_DIR)."""
    override = os.environ.get("NETVIZ_CONFIG_DIR", "").strip()
    if override:
        return Path(override)
    appdata = os.environ.get("APPDATA", "").strip()
    if appdata:
        return Path(appdata) / APP_DIR_NAME
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if xdg:
        return Path(xdg) / APP_DIR_NAME
    return Path.home() / ".config" / APP_DIR_NAME


def get_config_path() -> Path:
    """Return path to local config.json."""
    return get_config_dir() / "config.json"


def get_templates_path() -> Path:
    """Return path to global custom templates.json."""
    return get_config_dir() / "templates.json"


def get_autosave_dir() -> Path:
    """Return path to autosave directory."""
    return get_config_dir() / "autosave"


def get_recovery_path() -> Path:
    """Return path to periodic crash recovery .netviz snapshot."""
    return get_autosave_dir() / "recovery.netviz"


def get_logs_dir() -> Path:
    """Return path to rotating logs directory."""
    return get_config_dir() / "logs"


def get_log_path() -> Path:
    """Return path to main desktop rotating log file."""
    return get_logs_dir() / "netviz.log"


def _clamp_alpha(value: int) -> int:
    return max(MIN_NODE_ALPHA, min(MAX_NODE_ALPHA, int(value)))


def _clamp_margin(value: float) -> float:
    return max(MIN_CACHE_MARGIN_M, min(MAX_CACHE_MARGIN_M, float(value)))


def _normalize_zooms(zooms: Sequence[int] | None) -> list[int]:
    if not zooms:
        return list(DEFAULT_CACHE_ZOOMS)
    cleaned = sorted({max(15, min(20, int(z))) for z in zooms})
    return cleaned or list(DEFAULT_CACHE_ZOOMS)


@dataclass
class AppConfig:
    """Local desktop application configuration (NEVER exported into .netviz archives)."""

    google_maps_api_key: str = ""
    preferred_provider: str = "google"
    offline_cache_margin_m: float = DEFAULT_CACHE_MARGIN_M
    offline_cache_zooms: list[int] = field(default_factory=lambda: list(DEFAULT_CACHE_ZOOMS))
    node_opacity_alpha: int = DEFAULT_NODE_ALPHA
    recent_files: list[str] = field(default_factory=list)
    last_project_dir: str = ""

    def effective_google_maps_api_key(self) -> str:
        """Return configured API key, falling back to NETVIZ_GOOGLE_MAPS_API_KEY env var."""
        key = self.google_maps_api_key.strip()
        if key:
            return key
        return os.environ.get("NETVIZ_GOOGLE_MAPS_API_KEY", "").strip()

    def to_dict(self) -> dict[str, Any]:
        provider = (
            self.preferred_provider if self.preferred_provider in VALID_PROVIDERS else "google"
        )
        return {
            "google_maps_api_key": self.google_maps_api_key,
            "preferred_provider": provider,
            "offline_cache_margin_m": _clamp_margin(self.offline_cache_margin_m),
            "offline_cache_zooms": _normalize_zooms(self.offline_cache_zooms),
            "node_opacity_alpha": _clamp_alpha(self.node_opacity_alpha),
            "recent_files": [str(p) for p in self.recent_files[:10]],
            "last_project_dir": self.last_project_dir,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AppConfig:
        raw_provider = str(data.get("preferred_provider") or "google")
        provider = raw_provider if raw_provider in VALID_PROVIDERS else "google"
        raw_margin = data.get("offline_cache_margin_m")
        margin = (
            _clamp_margin(float(raw_margin))
            if raw_margin is not None
            else DEFAULT_CACHE_MARGIN_M
        )
        raw_zooms = data.get("offline_cache_zooms")
        zooms = (
            _normalize_zooms([int(z) for z in raw_zooms])
            if isinstance(raw_zooms, list)
            else list(DEFAULT_CACHE_ZOOMS)
        )
        raw_alpha = data.get("node_opacity_alpha")
        alpha = _clamp_alpha(int(raw_alpha)) if raw_alpha is not None else DEFAULT_NODE_ALPHA
        raw_recent = data.get("recent_files") or []
        recent = [str(p) for p in raw_recent if str(p).strip()][:10]
        return cls(
            google_maps_api_key=str(data.get("google_maps_api_key") or ""),
            preferred_provider=provider,
            offline_cache_margin_m=margin,
            offline_cache_zooms=zooms,
            node_opacity_alpha=alpha,
            recent_files=recent,
            last_project_dir=str(data.get("last_project_dir") or ""),
        )


def _atomic_write_text(dest: Path, text: str) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=str(dest.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, dest)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    return dest


def load_config() -> AppConfig:
    """Load local config.json, returning defaults if missing or unreadable."""
    path = get_config_path()
    if not path.is_file():
        return AppConfig()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, Mapping):
            return AppConfig.from_dict(raw)
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        pass
    return AppConfig()


def save_config(config: AppConfig) -> Path:
    """Persist AppConfig atomically to local config.json."""
    payload = json.dumps(config.to_dict(), indent=2, ensure_ascii=False)
    return _atomic_write_text(get_config_path(), payload)


def add_recent_file(config: AppConfig, file_path: str | Path, max_items: int = 10) -> AppConfig:
    """Prepend file_path to config.recent_files deduplicated by normalized path."""
    resolved = str(Path(file_path))
    norm_new = os.path.normcase(os.path.normpath(resolved))
    filtered = [
        p
        for p in config.recent_files
        if os.path.normcase(os.path.normpath(p)) != norm_new
    ]
    config.recent_files = [resolved, *filtered][:max_items]
    config.last_project_dir = str(Path(resolved).parent)
    return config


def load_global_templates() -> list[ComponentTemplate]:
    """Load global custom ComponentTemplates from templates.json (empty list if absent)."""
    path = get_templates_path()
    if not path.is_file():
        return []
    try:
        return import_templates_json(path)
    except (OSError, json.JSONDecodeError, ValueError, KeyError, TypeError):
        return []


def save_global_templates(templates: Sequence[ComponentTemplate]) -> Path:
    """Atomically save global custom ComponentTemplates to templates.json."""
    return export_templates_json(templates, get_templates_path())
