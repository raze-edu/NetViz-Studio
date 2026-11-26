"""Pure stdlib .netviz bundle, survey/template JSON, SQLite tile, and SHA-256 I/O."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import time
import zipfile
from collections.abc import Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from shared.models import SCHEMA_VERSION, ComponentTemplate, Project, SurveySession

PROJECT_JSON_ENTRY = "project.json"
TILES_SQLITE_ENTRY = "tiles.sqlite"
BLUEPRINTS_PREFIX = "blueprints/"

_TILES_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tiles (
    provider TEXT NOT NULL,
    z INTEGER NOT NULL,
    x INTEGER NOT NULL,
    y INTEGER NOT NULL,
    mime TEXT NOT NULL,
    data BLOB NOT NULL,
    fetched_at INTEGER NOT NULL,
    PRIMARY KEY (provider, z, x, y)
);
"""


@dataclass
class NetvizBundle:
    """Result of loading a .netviz ZIP archive."""

    project: Project
    blueprints: dict[str, bytes] = field(default_factory=dict)
    tiles_db_path: Path | None = None
    tiles_bytes: bytes | None = None


def compute_sha256_bytes(data: bytes) -> str:
    """Return lowercase hex SHA-256 digest of in-memory bytes."""
    return hashlib.sha256(data).hexdigest()


def compute_sha256_file(path: str | Path) -> str:
    """Return lowercase hex SHA-256 digest of a file streamed in 64 KB chunks."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(65536):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_bytes(dest_path: Path, data: bytes) -> Path:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp_", suffix=".part", dir=str(dest_path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, dest_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    return dest_path


def _normalize_blueprint_entry(archive_path: str) -> str:
    cleaned = archive_path.replace("\\", "/").lstrip("/")
    posix = PurePosixPath(cleaned)
    if ".." in posix.parts or not cleaned:
        raise ValueError(f"Invalid blueprint archive path: {archive_path!r}")
    normalized = posix.as_posix()
    if not normalized.startswith(BLUEPRINTS_PREFIX):
        normalized = f"{BLUEPRINTS_PREFIX}{normalized}"
    return normalized


def save_netviz(
    project: Project,
    path: str | Path,
    tiles_db_path: str | Path | None = None,
    blueprints: Mapping[str, bytes] | None = None,
) -> Path:
    """Atomically save a Project (plus optional tiles.sqlite and blueprints) to a .netviz ZIP."""
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp_", suffix=".netviz", dir=str(dest.parent))
    tmp_path = Path(tmp_name)

    try:
        with os.fdopen(fd, "wb") as handle:
            with zipfile.ZipFile(handle, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(PROJECT_JSON_ENTRY, project.to_json(indent=2).encode("utf-8"))

                if tiles_db_path is not None:
                    tiles_src = Path(tiles_db_path)
                    if tiles_src.is_file():
                        zf.write(tiles_src, arcname=TILES_SQLITE_ENTRY)

                if blueprints:
                    for raw_name, payload in blueprints.items():
                        arcname = _normalize_blueprint_entry(raw_name)
                        zf.writestr(arcname, payload)

            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, dest)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise

    return dest


def load_netviz(
    path: str | Path,
    extract_tiles_to: str | Path | None = None,
) -> NetvizBundle:
    """Load a .netviz ZIP archive, parsing project.json, blueprints/*, and optional tiles.sqlite."""
    src = Path(path)
    with zipfile.ZipFile(src, mode="r") as zf:
        names = set(zf.namelist())
        if PROJECT_JSON_ENTRY not in names:
            raise ValueError(f"Archive {src} is missing required {PROJECT_JSON_ENTRY}")

        project = Project.from_json(zf.read(PROJECT_JSON_ENTRY).decode("utf-8"))

        blueprints: dict[str, bytes] = {}
        for name in names:
            if name.startswith(BLUEPRINTS_PREFIX) and not name.endswith("/"):
                blueprints[name] = zf.read(name)

        tiles_bytes: bytes | None = None
        tiles_db_path: Path | None = None
        if TILES_SQLITE_ENTRY in names:
            tiles_bytes = zf.read(TILES_SQLITE_ENTRY)
            if extract_tiles_to is not None:
                tiles_db_path = _atomic_write_bytes(Path(extract_tiles_to), tiles_bytes)

    return NetvizBundle(
        project=project,
        blueprints=blueprints,
        tiles_db_path=tiles_db_path,
        tiles_bytes=tiles_bytes,
    )


def export_survey_session(session: SurveySession, path: str | Path) -> Path:
    """Atomically write a SurveySession to a .netviz-survey.json file."""
    dest = Path(path)
    payload = session.to_json(indent=2).encode("utf-8")
    return _atomic_write_bytes(dest, payload)


def import_survey_session(path: str | Path) -> SurveySession:
    """Load a SurveySession from a .netviz-survey.json file."""
    text = Path(path).read_text(encoding="utf-8")
    return SurveySession.from_json(text)


def export_templates_json(templates: Sequence[ComponentTemplate], path: str | Path) -> Path:
    """Atomically write ComponentTemplates to a .netviz-templates.json file."""
    dest = Path(path)
    doc = {
        "schema_version": SCHEMA_VERSION,
        "templates": [tpl.to_dict() for tpl in templates],
    }
    payload = json.dumps(doc, indent=2, ensure_ascii=False).encode("utf-8")
    return _atomic_write_bytes(dest, payload)


def import_templates_json(path: str | Path) -> list[ComponentTemplate]:
    """Load ComponentTemplates from a .netviz-templates.json file."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, Mapping):
        items = raw.get("templates") or []
    else:
        raise ValueError("Invalid templates JSON root structure")
    return [ComponentTemplate.from_dict(item) for item in items]


def ensure_tiles_db(db_path: str | Path) -> Path:
    """Ensure the SQLite tiles database and table exist, returning its Path."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as conn:
        with conn:
            conn.execute(_TILES_SCHEMA_SQL)
    return path


def write_tile_to_sqlite(
    db_path: str | Path,
    provider: str,
    z: int,
    x: int,
    y: int,
    data: bytes,
    mime: str = "image/png",
    fetched_at: int | None = None,
) -> None:
    """Insert or replace a single map tile in tiles.sqlite."""
    path = ensure_tiles_db(db_path)
    timestamp = int(time.time()) if fetched_at is None else int(fetched_at)
    with closing(sqlite3.connect(path)) as conn:
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO tiles (provider, z, x, y, mime, data, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (provider, int(z), int(x), int(y), mime, sqlite3.Binary(data), timestamp),
            )


def read_tile_from_sqlite(
    db_path: str | Path,
    provider: str,
    z: int,
    x: int,
    y: int,
) -> tuple[bytes, str] | None:
    """Read (data, mime) for a cached tile from tiles.sqlite, or None if missing."""
    path = Path(db_path)
    if not path.is_file():
        return None
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True)) as conn:
            cursor = conn.execute(
                "SELECT data, mime FROM tiles WHERE provider = ? AND z = ? AND x = ? AND y = ?",
                (provider, int(z), int(x), int(y)),
            )
            row = cursor.fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    return bytes(row[0]), str(row[1])
