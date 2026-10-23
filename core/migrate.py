"""One-time carry-over of an existing InvoiceM8 install to InvoiceWarden.

The app was renamed, which moved its data folder, database filename, log name
and "Run on startup" registry value. This copies (never moves or deletes) the
old data so an existing install keeps its suppliers, settings and history, and
an older build can still be run side by side. The saved-password master key is
handled in :mod:`core.crypto`, which falls back to the old credential entry.

Runs at the top of ``main()`` and is idempotent: it does nothing once the new
database exists, and any failure is logged and swallowed so the app still starts.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

import config

log = logging.getLogger(__name__)

#: Folders in the old data dir that are transient and not worth carrying over.
_SKIP_DIRS = ("updates",)


def migrate_data_dir(old: Path, new: Path, old_db: str, new_db: str) -> bool:
    """Copy ``old`` to ``new`` and rename the database files. True if it copied.

    Skipped when there is no old folder or the new database already exists.
    """
    if not old.is_dir() or (new / new_db).exists():
        return False
    new.mkdir(parents=True, exist_ok=True)
    for item in old.iterdir():
        if item.is_dir():
            if item.name in _SKIP_DIRS:
                continue
            shutil.copytree(item, new / item.name, dirs_exist_ok=True)
        elif item.name.startswith(old_db):
            # invoicem8.sqlite3, plus any -wal / -shm sidecar from a live DB.
            shutil.copy2(item, new / (new_db + item.name[len(old_db):]))
        else:
            shutil.copy2(item, new / item.name)
    return True


def migrate_run_value(old_name: str, new_name: str) -> bool:
    """Re-register the Windows startup entry under the new name. True if moved.

    The old command line is reused as-is (it still points at the installed
    exe), then the old value is removed so the app doesn't start twice.
    """
    try:
        import winreg
    except ImportError:                       # non-Windows dev box
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, config.RUN_KEY, 0,
                            winreg.KEY_READ | winreg.KEY_SET_VALUE) as key:
            try:
                value, kind = winreg.QueryValueEx(key, old_name)
            except FileNotFoundError:
                return False
            winreg.SetValueEx(key, new_name, 0, kind, value)
            winreg.DeleteValue(key, old_name)
            return True
    except OSError:
        return False


def run() -> None:
    """Run every migration step, logging and surviving any failure."""
    try:
        if migrate_data_dir(config.LEGACY_DATA_DIR, config.DATA_DIR,
                            config.LEGACY_DB_NAME, config.DB_PATH.name):
            log.info("Copied data from %s to %s", config.LEGACY_DATA_DIR,
                     config.DATA_DIR)
    except Exception:
        log.exception("Could not copy the old InvoiceM8 data folder")
    try:
        if migrate_run_value(config.LEGACY_RUN_VALUE_NAME, config.RUN_VALUE_NAME):
            log.info("Moved the Windows startup entry to %s", config.RUN_VALUE_NAME)
    except Exception:
        log.exception("Could not move the startup entry")
