"""Hardened Desktop LAN Sync HTTP server, UDP 8766 responder, NIC filter, QR & ADB dialog."""

from __future__ import annotations

import ctypes
import ipaddress
import json
import logging
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import qrcode
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from desktop.state import ProjectState
from shared.bundle_io import compute_sha256_bytes, compute_sha256_file, save_netviz
from shared.models import SCHEMA_VERSION, Project, SurveySession

LOGGER = logging.getLogger("netviz.lan_sync")

DEFAULT_HTTP_PORT_RANGE = range(8765, 8776)
DEFAULT_UDP_DISCOVERY_PORT = 8766
UDP_DISCOVER_MAGIC = b"NETVIZ_DISCOVER_V1"
STREAM_CHUNK_BYTES = 65536
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB
FIREWALL_RULE_NAME = "NetViz Studio LAN Sync"

_VIRTUAL_KEYWORDS = (
    "vethernet",
    "wsl",
    "default switch",
    "docker",
    "hyper-v",
    "virtualbox",
    "vbox",
    "vmware",
    "vmnet",
    "loopback",
    "teredo",
    "isatap",
    "npcap",
)
_VPN_KEYWORDS = (
    "tailscale",
    "wireguard",
    "wg",
    "openvpn",
    "tap-",
    "tun",
    "cisco",
    "anyconnect",
    "nordlynx",
    "proton",
    "zerotier",
    "hamachi",
    "vpn",
)
_TOKEN_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_pairing_token(length: int = 6) -> str:
    """Generate a concise uppercase alphanumeric pairing token."""
    return "".join(secrets.choice(_TOKEN_ALPHABET) for _ in range(length))


@dataclass(frozen=True)
class NetworkInterfaceInfo:
    """Classified IPv4 network interface on the Windows host."""

    name: str
    ip: str
    is_virtual: bool = False
    is_loopback: bool = False
    is_link_local: bool = False
    is_vpn: bool = False

    @property
    def is_preferred_lan(self) -> bool:
        return not (self.is_virtual or self.is_loopback or self.is_link_local or self.is_vpn)

    @property
    def sort_key(self) -> tuple[int, str, str]:
        if self.is_preferred_lan:
            tier = 0
        elif self.is_vpn and not (self.is_loopback or self.is_link_local):
            tier = 1
        elif self.is_virtual and not (self.is_loopback or self.is_link_local):
            tier = 2
        else:
            tier = 3
        return (tier, self.name.lower(), self.ip)


def classify_network_interface(name: str, ip: str) -> NetworkInterfaceInfo:
    """Classify an adapter by name and IPv4 address for LAN Sync priority."""
    lowered = name.lower()
    is_loopback = ip.startswith("127.") or "loopback" in lowered
    is_link_local = ip.startswith("169.254.")
    is_virtual = any(kw in lowered for kw in _VIRTUAL_KEYWORDS)
    is_vpn = any(kw in lowered for kw in _VPN_KEYWORDS)
    return NetworkInterfaceInfo(
        name=name,
        ip=ip,
        is_virtual=is_virtual,
        is_loopback=is_loopback,
        is_link_local=is_link_local,
        is_vpn=is_vpn,
    )


def _is_valid_ipv4(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
        return isinstance(addr, ipaddress.IPv4Address)
    except ValueError:
        return False


def _enumerate_windows_powershell_nics() -> list[tuple[str, str]]:
    if sys.platform != "win32":
        return []
    ps_cmd = (
        "Get-NetIPAddress -AddressFamily IPv4 | "
        "Where-Object { $_.IPAddress -and $_.PrefixOrigin -ne 'WellKnown' } | "
        "Select-Object InterfaceAlias,IPAddress | ConvertTo-Json -Compress"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            timeout=3.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0 or not proc.stdout.strip():
        return []
    try:
        raw = json.loads(proc.stdout.strip())
    except json.JSONDecodeError:
        return []
    items = raw if isinstance(raw, list) else [raw]
    out: list[tuple[str, str]] = []
    for item in items:
        if isinstance(item, dict):
            alias = str(item.get("InterfaceAlias") or "Network")
            ip = str(item.get("IPAddress") or "").strip()
            if _is_valid_ipv4(ip):
                out.append((alias, ip))
    return out


def _enumerate_ipconfig_nics() -> list[tuple[str, str]]:
    if sys.platform != "win32":
        return []
    try:
        proc = subprocess.run(
            ["ipconfig"],
            capture_output=True,
            text=True,
            timeout=3.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []

    out: list[tuple[str, str]] = []
    current_name = "Unknown Adapter"
    for line in proc.stdout.splitlines():
        if line and not line.startswith(" ") and line.rstrip().endswith(":"):
            current_name = line.rstrip()[:-1].strip()
            continue
        if "IPv4" in line:
            match = re.search(r"(\d+\.\d+\.\d+\.\d+)", line)
            if match and _is_valid_ipv4(match.group(1)):
                out.append((current_name, match.group(1)))
    return out


def _enumerate_socket_nics() -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 80))
            primary_ip = sock.getsockname()[0]
            if _is_valid_ipv4(primary_ip):
                results.append(("Primary Route", primary_ip))
    except OSError:
        pass
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = str(info[4][0])
            if _is_valid_ipv4(ip) and all(existing_ip != ip for _, existing_ip in results):
                results.append(("Host Adapter", ip))
    except OSError:
        pass
    return results


def list_network_interfaces(
    candidates: Sequence[tuple[str, str]] | None = None,
) -> list[NetworkInterfaceInfo]:
    """Enumerate and classify IPv4 NICs, prioritizing real LAN NICs over WSL/Docker/VPN."""
    if candidates is not None:
        raw = [(name, ip) for name, ip in candidates if _is_valid_ipv4(ip)]
    else:
        raw = _enumerate_windows_powershell_nics()
        if not raw:
            raw = _enumerate_ipconfig_nics()
        if not raw:
            raw = _enumerate_socket_nics()

    deduped: list[NetworkInterfaceInfo] = []
    seen_ips: set[str] = set()
    for name, ip in raw:
        if ip in seen_ips:
            continue
        seen_ips.add(ip)
        deduped.append(classify_network_interface(name, ip))

    deduped.sort(key=lambda info: info.sort_key)
    preferred = [info for info in deduped if info.is_preferred_lan]
    if preferred:
        # Exclude loopback, link-local, virtual, and VPN when standard LAN NICs exist.
        return preferred
    non_ignored = [info for info in deduped if not info.is_loopback and not info.is_link_local]
    return non_ignored or deduped


def get_preferred_lan_ips(candidates: Sequence[tuple[str, str]] | None = None) -> list[str]:
    """Return ordered list of preferred LAN IPv4 addresses."""
    infos = list_network_interfaces(candidates=candidates)
    ips = [info.ip for info in infos if not info.is_loopback and not info.is_link_local]
    return ips or ["127.0.0.1"]


@dataclass(frozen=True)
class SnapshotInfo:
    """Metadata for the pre-staged atomic .netviz snapshot."""

    path: Path
    size_bytes: int
    sha256: str
    project_name: str
    schema_version: str


class LanSyncServer(QObject):
    """Pre-staged HTTP + UDP 8766 discovery LAN Sync server with Qt signals."""

    survey_received = Signal(object)  # SurveySession
    activity_logged = Signal(str)
    snapshot_refreshed = Signal(object)  # SnapshotInfo

    def __init__(
        self,
        *,
        token: str | None = None,
        staging_dir: str | Path | None = None,
        interface_candidates: Sequence[tuple[str, str]] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.token = token or generate_pairing_token()
        self._temp_dir = (
            None if staging_dir is not None else tempfile.TemporaryDirectory(prefix="netviz_sync_")
        )
        self._staging_dir = (
            Path(staging_dir)
            if staging_dir is not None
            else Path(self._temp_dir.name)  # type: ignore[union-attr]
        )
        self._staging_dir.mkdir(parents=True, exist_ok=True)
        self._snapshot_lock = threading.RLock()
        self._snapshot: SnapshotInfo | None = None
        self._interface_candidates = (
            list(interface_candidates) if interface_candidates is not None else None
        )

        self._httpd: ThreadingHTTPServer | None = None
        self._http_thread: threading.Thread | None = None
        self._udp_sock: socket.socket | None = None
        self._udp_thread: threading.Thread | None = None
        self._udp_stop = threading.Event()
        self._udp_bound_port: int = 0
        self.activity_history: list[str] = []

    @property
    def port(self) -> int:
        if self._httpd is None:
            return 0
        return int(self._httpd.server_address[1])

    @property
    def udp_port(self) -> int:
        return self._udp_bound_port

    @property
    def snapshot(self) -> SnapshotInfo | None:
        with self._snapshot_lock:
            return self._snapshot

    def advertised_ips(self, primary_ip: str | None = None) -> list[str]:
        ips = get_preferred_lan_ips(candidates=self._interface_candidates)
        if primary_ip and primary_ip in ips:
            return [primary_ip, *[ip for ip in ips if ip != primary_ip]]
        if primary_ip and _is_valid_ipv4(primary_ip):
            return [primary_ip, *ips]
        return ips

    def build_qr_payload(self, primary_ip: str | None = None) -> str:
        """Build compact Section 4.3A multi-IP QR JSON payload."""
        snap = self.snapshot
        payload = {
            "v": 2,
            "ips": self.advertised_ips(primary_ip=primary_ip),
            "port": self.port,
            "token": self.token,
            "project": snap.project_name if snap is not None else "NetViz Project",
        }
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)

    def refresh_snapshot(
        self,
        project: Project,
        *,
        tiles_db_path: str | Path | None = None,
        blueprints: dict[str, bytes] | None = None,
    ) -> SnapshotInfo:
        """Atomically stage staging_project.netviz and compute byte size + SHA-256."""
        staging_path = self._staging_dir / "staging_project.netviz"
        save_netviz(
            project,
            staging_path,
            tiles_db_path=tiles_db_path,
            blueprints=blueprints,
        )
        size_bytes = staging_path.stat().st_size
        sha256 = compute_sha256_file(staging_path)
        info = SnapshotInfo(
            path=staging_path,
            size_bytes=size_bytes,
            sha256=sha256,
            project_name=project.name,
            schema_version=project.schema_version,
        )
        with self._snapshot_lock:
            self._snapshot = info
        self.snapshot_refreshed.emit(info)
        self._log_activity(f"Snapshot staged ({size_bytes:,} bytes, SHA-256 {sha256[:12]}...)")
        return info

    def _log_activity(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{stamp}] {message}"
        self.activity_history.append(line)
        self.activity_logged.emit(line)

    def start(
        self,
        *,
        port_range: Sequence[int] = DEFAULT_HTTP_PORT_RANGE,
        udp_port: int | None = DEFAULT_UDP_DISCOVERY_PORT,
    ) -> int:
        """Start HTTP server on 0.0.0.0 (probing port_range) and UDP discovery responder."""
        if self._httpd is not None:
            return self.port

        parent = self

        class _Server(ThreadingHTTPServer):
            allow_reuse_address = True
            daemon_threads = True

        class _Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                LOGGER.debug("LanSyncHTTP: " + format, *args)

            def _extract_token(self) -> str:
                header_tok = (self.headers.get("X-NetViz-Token") or "").strip()
                if header_tok:
                    return header_tok
                parsed = urlparse(self.path)
                params = parse_qs(parsed.query)
                return str((params.get("token") or [""])[0]).strip()

            def _send_json(self, status: int, payload: dict[str, object]) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                path_only = urlparse(self.path).path
                client_ip = self.client_address[0]

                if path_only == "/api/ping":
                    snap = parent.snapshot
                    provided = self._extract_token()
                    payload: dict[str, object] = {
                        "ok": True,
                        "app": "NetVizStudio",
                        "schema_version": (
                            snap.schema_version if snap is not None else SCHEMA_VERSION
                        ),
                        "project_name": snap.project_name if snap is not None else "",
                        "bundle_bytes": snap.size_bytes if snap is not None else 0,
                        "bundle_sha256": snap.sha256 if snap is not None else "",
                        "token_required": True,
                    }
                    if provided:
                        payload["token_valid"] = secrets.compare_digest(provided, parent.token)
                    parent._log_activity(f"PING from {client_ip}")
                    self._send_json(200, payload)
                    return

                if path_only == "/api/project.netviz":
                    provided = self._extract_token()
                    if not secrets.compare_digest(provided, parent.token):
                        parent._log_activity(
                            f"UNAUTHORIZED project download attempt from {client_ip}"
                        )
                        self._send_json(401, {"ok": False, "error": "Invalid pairing token"})
                        return
                    snap = parent.snapshot
                    if snap is None or not snap.path.is_file():
                        self._send_json(503, {"ok": False, "error": "Snapshot not ready"})
                        return
                    with parent._snapshot_lock:
                        snap_path = snap.path
                        size_bytes = snap.size_bytes
                        sha256 = snap.sha256
                        data_available = snap_path.is_file()
                    if not data_available:
                        self._send_json(503, {"ok": False, "error": "Snapshot missing"})
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Length", str(size_bytes))
                    self.send_header("X-NetViz-SHA256", sha256)
                    self.send_header("Content-Disposition", 'attachment; filename="project.netviz"')
                    self.end_headers()
                    with snap_path.open("rb") as handle:
                        while chunk := handle.read(STREAM_CHUNK_BYTES):
                            self.wfile.write(chunk)
                    parent._log_activity(
                        f"Sent project.netviz ({size_bytes:,} bytes) to {client_ip}"
                    )
                    return

                self._send_json(404, {"ok": False, "error": "Not found"})

            def do_POST(self) -> None:  # noqa: N802
                path_only = urlparse(self.path).path
                client_ip = self.client_address[0]
                if path_only != "/api/upload-survey":
                    self._send_json(404, {"ok": False, "error": "Not found"})
                    return

                provided = self._extract_token()
                if not secrets.compare_digest(provided, parent.token):
                    parent._log_activity(f"UNAUTHORIZED survey upload from {client_ip}")
                    self._send_json(401, {"ok": False, "error": "Invalid pairing token"})
                    return

                raw_len = self.headers.get("Content-Length")
                if raw_len is None:
                    self._send_json(411, {"ok": False, "error": "Content-Length required"})
                    return
                try:
                    content_length = int(raw_len)
                except ValueError:
                    self._send_json(400, {"ok": False, "error": "Invalid Content-Length"})
                    return
                if content_length < 0 or content_length > MAX_UPLOAD_BYTES:
                    self._send_json(413, {"ok": False, "error": "Payload exceeds 50 MB limit"})
                    return

                buf = bytearray()
                remaining = content_length
                while remaining > 0:
                    chunk = self.rfile.read(min(STREAM_CHUNK_BYTES, remaining))
                    if not chunk:
                        break
                    buf.extend(chunk)
                    remaining -= len(chunk)

                if len(buf) != content_length:
                    self._send_json(400, {"ok": False, "error": "Incomplete upload body"})
                    return

                raw_bytes = bytes(buf)
                expected_sha = (self.headers.get("X-NetViz-SHA256") or "").strip().lower()
                if expected_sha:
                    actual_sha = compute_sha256_bytes(raw_bytes)
                    if not secrets.compare_digest(actual_sha, expected_sha):
                        parent._log_activity(
                            f"SHA-256 mismatch on survey upload from {client_ip}"
                        )
                        self._send_json(400, {"ok": False, "error": "SHA-256 checksum mismatch"})
                        return

                try:
                    session = SurveySession.from_json(raw_bytes.decode("utf-8"))
                except (
                    UnicodeDecodeError,
                    json.JSONDecodeError,
                    KeyError,
                    ValueError,
                    TypeError,
                ) as exc:
                    self._send_json(400, {"ok": False, "error": f"Invalid survey JSON: {exc}"})
                    return

                parent._log_activity(
                    f"Received survey '{session.name}' ({len(session.samples)} samples, "
                    f"{len(raw_bytes):,} bytes) from {client_ip}"
                )
                parent.survey_received.emit(session)
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "session_id": session.id,
                        "sample_count": len(session.samples),
                    },
                )

        last_err: OSError | None = None
        for candidate_port in port_range:
            try:
                self._httpd = _Server(("0.0.0.0", int(candidate_port)), _Handler)
                break
            except OSError as exc:
                last_err = exc
        if self._httpd is None:
            raise OSError(f"No available LAN Sync port in {list(port_range)}") from last_err

        self._http_thread = threading.Thread(
            target=self._httpd.serve_forever,
            name="NetVizLanSyncHTTP",
            daemon=True,
        )
        self._http_thread.start()

        if udp_port is not None:
            self._start_udp_responder(int(udp_port))

        self._log_activity(f"LAN Sync listening on 0.0.0.0:{self.port} (token {self.token})")
        return self.port

    def _start_udp_responder(self, udp_port: int) -> None:
        self._udp_stop.clear()
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind(("0.0.0.0", udp_port))
            sock.settimeout(0.5)
        except OSError as exc:
            LOGGER.warning("Could not bind UDP discovery port %s: %s", udp_port, exc)
            self._log_activity(f"UDP discovery unavailable on port {udp_port}: {exc}")
            return

        self._udp_sock = sock
        self._udp_bound_port = int(sock.getsockname()[1])

        def _loop() -> None:
            while not self._udp_stop.is_set():
                try:
                    data, addr = sock.recvfrom(1024)
                except TimeoutError:
                    continue
                except OSError:
                    break
                if data.strip() == UDP_DISCOVER_MAGIC:
                    snap = self.snapshot
                    reply = json.dumps(
                        {
                            "app": "NetVizStudio",
                            "host": socket.gethostname(),
                            "ips": self.advertised_ips(),
                            "port": self.port,
                            "token": self.token,
                            "project_name": snap.project_name if snap is not None else "",
                        },
                        separators=(",", ":"),
                        ensure_ascii=False,
                    ).encode("utf-8")
                    try:
                        sock.sendto(reply, addr)
                        self._log_activity(f"UDP discovery reply sent to {addr[0]}:{addr[1]}")
                    except OSError:
                        pass

        self._udp_thread = threading.Thread(
            target=_loop,
            name="NetVizUdpDiscovery",
            daemon=True,
        )
        self._udp_thread.start()

    def stop(self) -> None:
        """Stop HTTP and UDP servers and release temporary staging files."""
        self._udp_stop.set()
        if self._udp_sock is not None:
            try:
                self._udp_sock.close()
            except OSError:
                pass
            self._udp_sock = None
        if self._udp_thread is not None:
            self._udp_thread.join(timeout=2.0)
            self._udp_thread = None
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._http_thread is not None:
            self._http_thread.join(timeout=2.0)
            self._http_thread = None
        self._udp_bound_port = 0
        if self._temp_dir is not None:
            self._temp_dir.cleanup()
            self._temp_dir = None


# ---------------------------------------------------------------------------
# Windows Firewall & USB ADB Helpers
# ---------------------------------------------------------------------------


def build_firewall_netsh_command() -> str:
    """Return the exact netsh command allowing inbound LAN Sync TCP & UDP ports."""
    return (
        f'netsh advfirewall firewall add rule name="{FIREWALL_RULE_NAME}" '
        "dir=in action=allow protocol=TCP localport=8765-8775 profile=private,domain,public & "
        f'netsh advfirewall firewall add rule name="{FIREWALL_RULE_NAME} UDP" '
        "dir=in action=allow protocol=UDP localport=8766 profile=private,domain,public"
    )


def check_windows_firewall_status(port: int = 8765) -> tuple[bool, str]:
    """Check whether Windows Defender Firewall has an allow rule for NetViz Studio LAN Sync."""
    if sys.platform != "win32":
        return True, "Non-Windows host (Windows Firewall check skipped)"
    try:
        proc = subprocess.run(
            ["netsh", "advfirewall", "firewall", "show", "rule", f"name={FIREWALL_RULE_NAME}"],
            capture_output=True,
            text=True,
            timeout=3.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"Unable to query Windows Firewall: {exc}"

    output = proc.stdout or ""
    if proc.returncode == 0 and "Enabled:" in output and "Yes" in output:
        return True, f"Firewall Rule Active (port {port})"
    return False, "Windows Firewall may block phone connections"


def allow_port_in_firewall_elevated() -> tuple[bool, str]:
    """Trigger UAC-elevated netsh command to add inbound Windows Firewall rules."""
    if sys.platform != "win32":
        return False, "Windows Firewall rules apply only on Windows."
    cmd_args = f"/c {build_firewall_netsh_command()}"
    try:
        rc = ctypes.windll.shell32.ShellExecuteW(  # type: ignore[attr-defined]
            None,
            "runas",
            "cmd.exe",
            cmd_args,
            None,
            1,
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"Elevation failed: {exc}"
    if int(rc) > 32:
        return True, "UAC elevation requested; click Re-check once approved."
    return False, "UAC elevation was cancelled or denied."


def find_adb_executable(repo_root: Path | None = None) -> Path | None:
    """Locate adb.exe in .toolchains/android-sdk/platform-tools or PATH."""
    base = repo_root if repo_root is not None else Path(__file__).resolve().parent.parent
    local_adb = base / ".toolchains" / "android-sdk" / "platform-tools" / "adb.exe"
    if local_adb.is_file():
        return local_adb
    which_adb = shutil.which("adb")
    return Path(which_adb) if which_adb else None


def run_adb_reverse(port: int, repo_root: Path | None = None) -> tuple[bool, str]:
    """Run 'adb reverse tcp:<port> tcp:<port>' for zero-firewall USB Cable Sync."""
    adb = find_adb_executable(repo_root=repo_root)
    if adb is None:
        return False, "adb executable not found in .toolchains/android-sdk or PATH."
    try:
        proc = subprocess.run(
            [str(adb), "reverse", f"tcp:{port}", f"tcp:{port}"],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"Failed to execute adb: {exc}"
    if proc.returncode == 0:
        return True, f"USB Sync tunnel active: adb reverse tcp:{port} tcp:{port}"
    err = (proc.stderr or proc.stdout or "").strip()
    return False, f"adb reverse failed: {err or 'Check USB Debugging on phone'}"


# ---------------------------------------------------------------------------
# QR Rendering & LanSyncDialog
# ---------------------------------------------------------------------------


def render_qr_pixmap(payload: str, box_size: int = 7) -> QPixmap:
    """Render a crisp QR code QPixmap from payload text via qrcode + Pillow."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=box_size,
        border=2,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    pil_img = qr.make_image(fill_color="black", back_color="white").convert("RGBA")
    data = pil_img.tobytes("raw", "RGBA")
    qimg = QImage(data, pil_img.width, pil_img.height, QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(qimg.copy())


class LanSyncDialog(QDialog):
    """LAN & USB Sync dialog with NIC selector, multi-IP QR, firewall check, and live log."""

    survey_received = Signal(object)  # SurveySession

    def __init__(
        self,
        state: ProjectState,
        server: LanSyncServer | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("NetViz Studio — Mobile LAN & USB Sync")
        self.setMinimumSize(640, 620)
        self._state = state
        self._owns_server = server is None
        self.server = server if server is not None else LanSyncServer()
        self._interfaces = list_network_interfaces()
        self._build_ui()
        self._connect_signals()
        self._start_and_stage()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(10)

        # Snapshot header
        top_row = QHBoxLayout()
        self.snapshot_label = QLabel("Staging project snapshot...", self)
        self.refresh_btn = QPushButton("Refresh Snapshot", self)
        self.refresh_btn.clicked.connect(self._refresh_snapshot)
        top_row.addWidget(self.snapshot_label, 1)
        top_row.addWidget(self.refresh_btn)
        root.addLayout(top_row)

        self.tabs = QTabWidget(self)

        # --- Tab 1: Wi-Fi / LAN Sync ---
        lan_tab = QWidget(self)
        lan_layout = QVBoxLayout(lan_tab)

        nic_form = QFormLayout()
        self.nic_combo = QComboBox(lan_tab)
        for info in self._interfaces:
            tag = "LAN" if info.is_preferred_lan else ("VPN" if info.is_vpn else "Virtual")
            self.nic_combo.addItem(f"{info.name} — {info.ip} [{tag}]", userData=info.ip)
        self.nic_combo.currentIndexChanged.connect(self._update_qr_and_urls)
        nic_form.addRow("Network Interface:", self.nic_combo)

        self.url_edit = QLineEdit(lan_tab)
        self.url_edit.setReadOnly(True)
        nic_form.addRow("Sync URL:", self.url_edit)

        self.token_label = QLabel(self.server.token, lan_tab)
        self.token_label.setStyleSheet("font-weight: 700; color: #38BDF8; font-size: 14px;")
        nic_form.addRow("Pairing Token:", self.token_label)
        lan_layout.addLayout(nic_form)

        qr_box = QHBoxLayout()
        qr_box.addStretch(1)
        self.qr_label = QLabel(lan_tab)
        self.qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr_label.setMinimumSize(220, 220)
        self.qr_label.setStyleSheet("background-color: #FFFFFF; padding: 8px; border-radius: 6px;")
        qr_box.addWidget(self.qr_label)
        qr_box.addStretch(1)
        lan_layout.addLayout(qr_box)

        # Firewall diagnostics box
        fw_group = QGroupBox("Windows Defender Firewall Diagnostics", lan_tab)
        fw_layout = QVBoxLayout(fw_group)
        fw_top = QHBoxLayout()
        self.fw_status_label = QLabel("Checking Windows Firewall...", fw_group)
        self.fw_allow_btn = QPushButton("Allow Port in Windows Firewall (Admin)", fw_group)
        self.fw_allow_btn.clicked.connect(self._on_allow_firewall)
        self.fw_recheck_btn = QPushButton("Re-check", fw_group)
        self.fw_recheck_btn.clicked.connect(self._update_firewall_status)
        fw_top.addWidget(self.fw_status_label, 1)
        fw_top.addWidget(self.fw_allow_btn)
        fw_top.addWidget(self.fw_recheck_btn)
        fw_layout.addLayout(fw_top)

        cmd_row = QHBoxLayout()
        self.fw_cmd_edit = QLineEdit(build_firewall_netsh_command(), fw_group)
        self.fw_cmd_edit.setReadOnly(True)
        self.fw_copy_btn = QPushButton("Copy Command", fw_group)
        self.fw_copy_btn.clicked.connect(self._copy_firewall_command)
        cmd_row.addWidget(self.fw_cmd_edit, 1)
        cmd_row.addWidget(self.fw_copy_btn)
        fw_layout.addLayout(cmd_row)
        lan_layout.addWidget(fw_group)

        self.tabs.addTab(lan_tab, "Wi-Fi / LAN Sync")

        # --- Tab 2: USB Cable Sync (ADB Reverse) ---
        usb_tab = QWidget(self)
        usb_layout = QVBoxLayout(usb_tab)
        usb_help = QLabel(
            "Connect your Android phone via USB (with USB Debugging enabled) to bypass "
            "corporate Wi-Fi client isolation and Windows Firewall completely.\n\n"
            "Step 1: Click 'Setup USB Sync (adb reverse)' below.\n"
            "Step 2: On Android, open Import/Export and tap 'USB Sync (127.0.0.1)'.",
            usb_tab,
        )
        usb_help.setWordWrap(True)
        usb_layout.addWidget(usb_help)

        self.usb_cmd_edit = QLineEdit("adb reverse tcp:8765 tcp:8765", usb_tab)
        self.usb_cmd_edit.setReadOnly(True)
        usb_layout.addWidget(self.usb_cmd_edit)

        usb_btn_row = QHBoxLayout()
        self.usb_setup_btn = QPushButton("Setup USB Sync (adb reverse)", usb_tab)
        self.usb_setup_btn.setProperty("primary", True)
        self.usb_setup_btn.clicked.connect(self._on_setup_usb_sync)
        self.usb_status_label = QLabel("", usb_tab)
        usb_btn_row.addWidget(self.usb_setup_btn)
        usb_btn_row.addWidget(self.usb_status_label, 1)
        usb_layout.addLayout(usb_btn_row)
        usb_layout.addStretch(1)

        self.tabs.addTab(usb_tab, "USB Cable Sync")
        root.addWidget(self.tabs)

        # --- Live Connection Activity Log ---
        log_group = QGroupBox("Connection Activity Log", self)
        log_layout = QVBoxLayout(log_group)
        self.activity_log = QPlainTextEdit(log_group)
        self.activity_log.setReadOnly(True)
        self.activity_log.setMaximumHeight(130)
        log_layout.addWidget(self.activity_log)
        root.addWidget(log_group)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_btn = QPushButton("Close", self)
        close_btn.clicked.connect(self.accept)
        close_row.addWidget(close_btn)
        root.addLayout(close_row)

    def _connect_signals(self) -> None:
        self.server.activity_logged.connect(self._append_log_line)
        self.server.snapshot_refreshed.connect(self._on_snapshot_refreshed)
        self.server.survey_received.connect(self.survey_received)

    def _start_and_stage(self) -> None:
        for line in self.server.activity_history:
            self.activity_log.appendPlainText(line)
        self._refresh_snapshot()
        if self.server.port == 0:
            self.server.start()
        self.usb_cmd_edit.setText(f"adb reverse tcp:{self.server.port} tcp:{self.server.port}")
        self._update_qr_and_urls()
        self._update_firewall_status()

    def _refresh_snapshot(self) -> None:
        tiles = (
            self._state.tiles_db_path
            if self._state.tiles_db_path and self._state.tiles_db_path.is_file()
            else None
        )
        self.server.refresh_snapshot(
            self._state.project,
            tiles_db_path=tiles,
            blueprints=self._state.blueprint_bytes,
        )

    def _on_snapshot_refreshed(self, info: SnapshotInfo) -> None:
        kb = info.size_bytes / 1024.0
        self.snapshot_label.setText(
            f"Staged: <b>{info.project_name}</b> ({kb:.1f} KB · SHA-256 {info.sha256[:10]}…)"
        )
        self._update_qr_and_urls()

    def _update_qr_and_urls(self) -> None:
        selected_ip = str(self.nic_combo.currentData() or "127.0.0.1")
        port = self.server.port or 8765
        self.url_edit.setText(f"http://{selected_ip}:{port}")
        payload = self.server.build_qr_payload(primary_ip=selected_ip)
        pixmap = render_qr_pixmap(payload)
        self.qr_label.setPixmap(pixmap)
        self.qr_label.setToolTip(payload)

    def _update_firewall_status(self) -> None:
        ok, msg = check_windows_firewall_status(self.server.port or 8765)
        if ok:
            self.fw_status_label.setText(f"🟢 {msg}")
            self.fw_status_label.setStyleSheet("color: #22C55E; font-weight: 600;")
        else:
            self.fw_status_label.setText(f"🟡 {msg}")
            self.fw_status_label.setStyleSheet("color: #F59E0B; font-weight: 600;")

    def _on_allow_firewall(self) -> None:
        ok, msg = allow_port_in_firewall_elevated()
        self._append_log_line(f"[Firewall] {msg}")
        if ok:
            time.sleep(0.2)
        self._update_firewall_status()

    def _copy_firewall_command(self) -> None:
        cb = QApplication.clipboard()
        if cb is not None:
            cb.setText(self.fw_cmd_edit.text())
        self._append_log_line("[Firewall] Copied netsh command to clipboard.")

    def _on_setup_usb_sync(self) -> None:
        port = self.server.port or 8765
        ok, msg = run_adb_reverse(port)
        self.usb_status_label.setText(f"{'🟢' if ok else '🔴'} {msg}")
        self._append_log_line(f"[USB ADB] {msg}")

    def _append_log_line(self, line: str) -> None:
        self.activity_log.appendPlainText(line)

    def closeEvent(self, event: object) -> None:
        if self._owns_server:
            self.server.stop()
        super().closeEvent(event)  # type: ignore[arg-type]

    def reject(self) -> None:
        if self._owns_server:
            self.server.stop()
        super().reject()

    def accept(self) -> None:
        if self._owns_server:
            self.server.stop()
        super().accept()
