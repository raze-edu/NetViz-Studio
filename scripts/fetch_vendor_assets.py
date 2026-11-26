"""Download and SHA-256 verify vendored Leaflet 1.9.4 JS & CSS assets."""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_VENDOR_DIR = (
    REPO_ROOT / "desktop" / "ui" / "map_tab" / "map_assets" / "vendor"
)

LEAFLET_VERSION = "1.9.4"


@dataclass(frozen=True)
class VendorAssetSpec:
    filename: str
    urls: tuple[str, ...]
    sha256: str
    min_bytes: int


VENDOR_ASSETS: tuple[VendorAssetSpec, ...] = (
    VendorAssetSpec(
        filename="leaflet.js",
        urls=(
            f"https://unpkg.com/leaflet@{LEAFLET_VERSION}/dist/leaflet.js",
            f"https://cdn.jsdelivr.net/npm/leaflet@{LEAFLET_VERSION}/dist/leaflet.js",
        ),
        sha256="db49d009c841f5ca34a888c96511ae936fd9f5533e90d8b2c4d57596f4e5641a",
        min_bytes=100_000,
    ),
    VendorAssetSpec(
        filename="leaflet.css",
        urls=(
            f"https://unpkg.com/leaflet@{LEAFLET_VERSION}/dist/leaflet.css",
            f"https://cdn.jsdelivr.net/npm/leaflet@{LEAFLET_VERSION}/dist/leaflet.css",
        ),
        sha256="a7837102824184820dfa198d1ebcd109ff6d0ff9a2672a074b9a1b4d147d04c6",
        min_bytes=10_000,
    ),
)


def compute_sha256(data: bytes) -> str:
    """Return lowercase hex SHA-256 digest for data."""
    return hashlib.sha256(data).hexdigest()


def is_valid_asset(path: Path, spec: VendorAssetSpec) -> bool:
    """Return True if path exists, meets min_bytes, and matches expected SHA-256."""
    if not path.is_file():
        return False
    data = path.read_bytes()
    if len(data) < spec.min_bytes:
        return False
    return compute_sha256(data) == spec.sha256


def _atomic_write_bytes(dest: Path, data: bytes) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp_", suffix=dest.suffix, dir=str(dest.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, dest)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _download_with_fallbacks(spec: VendorAssetSpec, timeout: float) -> bytes:
    errors: list[str] = []
    for url in spec.urls:
        try:
            resp = requests.get(
                url,
                timeout=timeout,
                headers={"User-Agent": "NetVizStudio/2.1"},
            )
            if resp.status_code != 200 or not resp.content:
                errors.append(f"{url}: HTTP {resp.status_code}")
                continue
            digest = compute_sha256(resp.content)
            if digest != spec.sha256:
                errors.append(
                    f"{url}: SHA-256 mismatch (expected {spec.sha256}, got {digest})"
                )
                continue
            if len(resp.content) < spec.min_bytes:
                errors.append(f"{url}: file too small ({len(resp.content)} bytes)")
                continue
            return resp.content
        except requests.RequestException as exc:
            errors.append(f"{url}: {exc}")
    raise RuntimeError(
        f"Failed to download verified {spec.filename} from any mirror: {'; '.join(errors)}"
    )


def fetch_vendor_assets(
    target_dir: Path | None = None,
    *,
    force: bool = False,
    timeout: float = 20.0,
) -> dict[str, Path]:
    """Ensure verified Leaflet 1.9.4 vendor assets exist in target_dir."""
    dest_dir = Path(target_dir) if target_dir is not None else DEFAULT_VENDOR_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, Path] = {}

    for spec in VENDOR_ASSETS:
        dest = dest_dir / spec.filename
        if not force and is_valid_asset(dest, spec):
            print(f"[SKIP] {spec.filename} already verified (sha256={spec.sha256[:16]}...)")
            results[spec.filename] = dest
            continue

        data = _download_with_fallbacks(spec, timeout=timeout)
        _atomic_write_bytes(dest, data)
        print(
            f"[OK]   {spec.filename} ({len(data)} bytes, sha256={spec.sha256[:16]}...) -> {dest}"
        )
        results[spec.filename] = dest

    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch and SHA-256 verify Leaflet 1.9.4 vendor assets for NetViz Studio."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download assets even if verified files already exist.",
    )
    parser.add_argument(
        "--target-dir",
        type=Path,
        default=DEFAULT_VENDOR_DIR,
        help="Destination directory for vendored assets.",
    )
    args = parser.parse_args(argv)
    try:
        fetch_vendor_assets(target_dir=args.target_dir, force=args.force)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
