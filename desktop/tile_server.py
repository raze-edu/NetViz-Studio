"""Local 127.0.0.1 HTTP asset & tile cache server plus cancellable TilePrefetchWorker."""

from __future__ import annotations

import io
import logging
import mimetypes
import re
import threading
import time
from collections.abc import Sequence
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

import requests
from PIL import Image, ImageDraw
from PySide6.QtCore import QThread, Signal

from shared.bundle_io import ensure_tiles_db, read_tile_from_sqlite, write_tile_to_sqlite
from shared.geo_math import bounding_box_tiles, building_latlon_bounds_with_margin
from shared.models import Building

LOGGER = logging.getLogger("netviz.tile_server")

DEFAULT_ASSETS_DIR = Path(__file__).resolve().parent / "ui" / "map_tab" / "map_assets"
USER_AGENT = "NetVizStudio/2.1"

_UPSTREAM_TEMPLATES: dict[str, str] = {
    "osm": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    "esri_satellite": (
        "https://server.arcgisonline.com/ArcGIS/rest/services/"
        "World_Imagery/MapServer/tile/{z}/{y}/{x}"
    ),
}

_MIME_OVERRIDES: dict[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".mjs": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}

_TILE_ROUTE_RE = re.compile(
    r"^/tiles/(osm|esri_satellite)/(\d+)/(\d+)/(\d+)(?:\.[a-zA-Z0-9]+)?$"
)


@lru_cache(maxsize=1)
def generate_offline_placeholder_png() -> bytes:
    """Return a crisp 256x256 dark slate grid PNG with an 'Offline' watermark."""
    img = Image.new("RGBA", (256, 256), (15, 23, 42, 255))
    draw = ImageDraw.Draw(img)
    for pos in range(0, 256, 32):
        draw.line([(pos, 0), (pos, 255)], fill=(30, 41, 59, 255), width=1)
        draw.line([(0, pos), (255, pos)], fill=(30, 41, 59, 255), width=1)
    draw.rectangle([(0, 0), (255, 255)], outline=(51, 65, 85, 255), width=1)
    draw.text((100, 120), "Offline", fill=(148, 163, 184, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _guess_mime(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in _MIME_OVERRIDES:
        return _MIME_OVERRIDES[ext]
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


def _fetch_upstream_tile(provider: str, z: int, x: int, y: int) -> tuple[bytes, str] | None:
    template = _UPSTREAM_TEMPLATES.get(provider)
    if template is None:
        return None
    url = template.format(z=z, x=x, y=y)
    try:
        resp = requests.get(
            url,
            timeout=5.0,
            headers={"User-Agent": USER_AGENT},
        )
    except requests.RequestException as exc:
        LOGGER.debug("Tile fetch error for %s/%s/%s/%s: %s", provider, z, x, y, exc)
        return None
    if resp.status_code != 200 or not resp.content:
        return None
    ctype = resp.headers.get("Content-Type", "image/png").split(";")[0].strip()
    if not ctype.startswith("image/"):
        ctype = "image/png"
    return resp.content, ctype


class TileCacheServer:
    """Local 127.0.0.1 HTTP server for /assets/* and SQLite-cached /tiles/<provider>/<z>/<x>/<y>."""

    def __init__(
        self,
        tiles_db_path: str | Path,
        *,
        assets_dir: str | Path | None = None,
        offline_mode: bool = False,
        port: int = 0,
    ) -> None:
        self.tiles_db_path = Path(tiles_db_path)
        ensure_tiles_db(self.tiles_db_path)
        self.assets_dir = Path(assets_dir) if assets_dir is not None else DEFAULT_ASSETS_DIR
        self.offline_mode = offline_mode
        self._requested_port = port
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._db_lock = threading.Lock()

    @property
    def port(self) -> int:
        if self._httpd is None:
            return 0
        return int(self._httpd.server_address[1])

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def asset_url(self, rel_path: str = "map.html") -> str:
        cleaned = rel_path.lstrip("/")
        return f"{self.base_url}/assets/{cleaned}"

    def set_tiles_db_path(self, db_path: str | Path) -> None:
        with self._db_lock:
            self.tiles_db_path = Path(db_path)
            ensure_tiles_db(self.tiles_db_path)

    def start(self) -> int:
        if self._httpd is not None:
            return self.port

        parent = self

        class _Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                LOGGER.debug("TileServer: " + format, *args)

            def _send_bytes(
                self,
                status: int,
                content_type: str,
                body: bytes,
                extra_headers: dict[str, str] | None = None,
            ) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-cache")
                if extra_headers:
                    for key, val in extra_headers.items():
                        self.send_header(key, val)
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                raw_path = unquote(self.path.split("?", 1)[0])
                if raw_path.startswith("/assets/"):
                    self._handle_asset(raw_path[len("/assets/"):])
                    return
                match = _TILE_ROUTE_RE.match(raw_path)
                if match:
                    provider, z_s, x_s, y_s = match.groups()
                    self._handle_tile(provider, int(z_s), int(x_s), int(y_s))
                    return
                self._send_bytes(404, "text/plain; charset=utf-8", b"Not Found")

            def _handle_asset(self, rel_path: str) -> None:
                cleaned = rel_path.lstrip("/") or "map.html"
                base_resolved = parent.assets_dir.resolve()
                target = (base_resolved / cleaned).resolve()
                try:
                    target.relative_to(base_resolved)
                except ValueError:
                    self._send_bytes(403, "text/plain; charset=utf-8", b"Forbidden")
                    return
                if not target.is_file():
                    self._send_bytes(404, "text/plain; charset=utf-8", b"Asset Not Found")
                    return
                data = target.read_bytes()
                self._send_bytes(200, _guess_mime(target), data)

            def _handle_tile(self, provider: str, z: int, x: int, y: int) -> None:
                with parent._db_lock:
                    db_path = parent.tiles_db_path
                    cached = read_tile_from_sqlite(db_path, provider, z, x, y)
                if cached is not None:
                    tile_bytes, mime = cached
                    self._send_bytes(
                        200,
                        mime,
                        tile_bytes,
                        {"X-NetViz-Tile-Status": "cache-hit"},
                    )
                    return

                if not parent.offline_mode:
                    fetched = _fetch_upstream_tile(provider, z, x, y)
                    if fetched is not None:
                        tile_bytes, mime = fetched
                        with parent._db_lock:
                            write_tile_to_sqlite(
                                parent.tiles_db_path, provider, z, x, y, tile_bytes, mime=mime
                            )
                        self._send_bytes(
                            200,
                            mime,
                            tile_bytes,
                            {"X-NetViz-Tile-Status": "upstream"},
                        )
                        return

                placeholder = generate_offline_placeholder_png()
                self._send_bytes(
                    200,
                    "image/png",
                    placeholder,
                    {"X-NetViz-Tile-Status": "offline-placeholder"},
                )

        class _Server(ThreadingHTTPServer):
            allow_reuse_address = True
            daemon_threads = True

        self._httpd = _Server(("127.0.0.1", self._requested_port), _Handler)
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            name="NetVizTileCacheServer",
            daemon=True,
        )
        self._thread.start()
        return self.port

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None


class TilePrefetchWorker(QThread):
    """Background cancellable QThread prefetching offline map tiles into tiles.sqlite."""

    progress = Signal(int, int, str)  # (done, total, status_text)
    finished_prefetch = Signal(int, int, bool)  # (downloaded, skipped_cached, cancelled)

    def __init__(
        self,
        buildings: Sequence[Building],
        tiles_db_path: str | Path,
        *,
        margin_m: float = 250.0,
        zooms: Sequence[int] = (16, 17, 18, 19),
        providers: Sequence[str] = ("osm", "esri_satellite"),
        delay_s: float = 0.05,
        parent: object | None = None,
    ) -> None:
        super().__init__(parent)  # type: ignore[arg-type]
        self._buildings = list(buildings)
        self._tiles_db_path = Path(tiles_db_path)
        self._margin_m = float(margin_m)
        self._zooms = list(zooms)
        self._providers = [p for p in providers if p in _UPSTREAM_TEMPLATES]
        self._delay_s = max(0.0, float(delay_s))
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        """Request polite cancellation of the running prefetch."""
        self._cancel_event.set()

    def run(self) -> None:
        bounds = building_latlon_bounds_with_margin(self._buildings, margin_m=self._margin_m)
        if bounds is None or not self._providers:
            self.finished_prefetch.emit(0, 0, False)
            return

        ensure_tiles_db(self._tiles_db_path)
        coords = bounding_box_tiles(*bounds, self._zooms)
        tasks = [(provider, z, x, y) for provider in self._providers for z, x, y in coords]
        total = len(tasks)
        downloaded = 0
        skipped = 0

        for idx, (provider, z, x, y) in enumerate(tasks, start=1):
            if self._cancel_event.is_set():
                self.finished_prefetch.emit(downloaded, skipped, True)
                return

            if read_tile_from_sqlite(self._tiles_db_path, provider, z, x, y) is not None:
                skipped += 1
                self.progress.emit(idx, total, f"Cached {provider} z{z}/{x}/{y}")
                continue

            fetched = _fetch_upstream_tile(provider, z, x, y)
            if fetched is not None:
                tile_bytes, mime = fetched
                write_tile_to_sqlite(
                    self._tiles_db_path, provider, z, x, y, tile_bytes, mime=mime
                )
                downloaded += 1
            self.progress.emit(idx, total, f"Fetched {provider} z{z}/{x}/{y}")

            if self._delay_s > 0.0 and not self._cancel_event.is_set():
                time.sleep(self._delay_s)

        self.finished_prefetch.emit(downloaded, skipped, self._cancel_event.is_set())
