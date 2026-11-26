"""Desktop CLI entrypoint (--project, --offline, --debug-log, rotating logs & recovery)."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Sequence
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from desktop.config import get_log_path, get_logs_dir, get_recovery_path, load_config
from desktop.state import ProjectState
from desktop.ui.main_window import MainWindow
from desktop.ui.theme import apply_dark_theme

LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 3


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for NetViz Studio Desktop."""
    parser = argparse.ArgumentParser(
        prog="netviz-studio",
        description="NetViz Studio v2.1 Desktop Application",
    )
    parser.add_argument(
        "--project",
        type=str,
        default=None,
        help="Path to a .netviz project archive to open on startup",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Run map tile server strictly offline using local SQLite cache only",
    )
    parser.add_argument(
        "--debug-log",
        action="store_true",
        help="Enable verbose DEBUG logging to rotating log file and stderr",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def configure_logging(*, debug_log: bool = False) -> logging.Logger:
    """Configure rotating file logger (%APPDATA%/NetVizStudio/logs/netviz.log, 5MB x 3)."""
    get_logs_dir().mkdir(parents=True, exist_ok=True)
    log_path = get_log_path()
    level = logging.DEBUG if debug_log else logging.INFO

    root_logger = logging.getLogger("netviz")
    root_logger.setLevel(level)

    for existing in list(root_logger.handlers):
        root_logger.removeHandler(existing)
        existing.close()

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(level)
    stream_handler.setFormatter(formatter)
    root_logger.addHandler(stream_handler)

    return root_logger


def check_crash_recovery(
    state: ProjectState,
    *,
    parent: QWidget | None = None,
    auto_accept: bool | None = None,
) -> bool:
    """Offer to restore autosave/recovery.netviz when present on startup."""
    recovery = get_recovery_path()
    if not recovery.is_file() or recovery.stat().st_size == 0:
        return False
    if os.environ.get("NETVIZ_SKIP_RECOVERY_PROMPT") == "1":
        return False

    should_restore = auto_accept
    if should_restore is None:
        reply = QMessageBox.question(
            parent,
            "Recover Unsaved Session?",
            f"An autosaved recovery snapshot was found:\n{recovery}\n\n"
            "Would you like to restore it?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        should_restore = reply == QMessageBox.StandardButton.Yes

    if not should_restore:
        return False

    try:
        state.load_from_file(recovery)
        state.project_path = None
        state.mark_dirty()
        return True
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("netviz.main").warning("Recovery restore failed: %s", exc)
        return False


def main(argv: Sequence[str] | None = None) -> int:
    """Launch NetViz Studio v2.1 Desktop."""
    args = parse_args(argv)
    logger = configure_logging(debug_log=bool(args.debug_log))
    logger.info("Starting NetViz Studio v2.1 (offline=%s)", args.offline)

    existing_app = QApplication.instance()
    app = existing_app if isinstance(existing_app, QApplication) else QApplication(sys.argv)
    apply_dark_theme(app)

    config = load_config()
    state = ProjectState()
    window = MainWindow(state, config=config, offline_mode=bool(args.offline))

    if args.project:
        proj_path = Path(args.project)
        if proj_path.is_file():
            window.open_project(proj_path)
        else:
            logger.warning("Startup --project path not found: %s", proj_path)
    else:
        check_crash_recovery(state, parent=window)

    window.show()
    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
