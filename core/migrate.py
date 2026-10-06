"""One-time move from the old InvoiceM8 name to InvoiceWarden.

Runs at startup, before anything opens the database. If the old data folder
(%LOCALAPPDATA%\\InvoiceM8) exists and the new one doesn't yet, the whole folder is
COPIED (not moved) to %LOCALAPPDATA%\\InvoiceWarden and the database file is renamed.
Copying keeps the old folder as a backup, and keeps every path already stored in the
database (invoices waiting for a retry point at files under the old folder) valid.

Saved keys need nothing: they're encrypted with a master key kept in Windows Credential
Manager under the OLD service name, which config.KEYRING_SERVICE deliberately still uses.

The "Start with Windows" entry (HKCU Run value) is renamed too, keeping its command.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

import config

log = logging.getLogger(__name__)

#: Old database file name inside the data folder (and its SQLite side files).
_OLD_DB = "invoicem8.sqlite3"
_SIDE = ("", "-wal", "-shm", "-journal")


def migrate_legacy(old_dir: Path | None = None, new_dir: Path | None = None) -> str:
    """Copy the InvoiceM8 data folder to the InvoiceWarden one if needed.

    Returns a short note of what happened ("" when there was nothing to do). Never
    raises: a failed copy is logged and the app starts fresh rather than not at all.
    """
    old_dir = old_dir or config.LEGACY_DATA_DIR
    new_dir = new_dir or config.DATA_DIR
    if not old_dir.exists() or new_dir.exists():
        return ""
    try:
        # Downloaded updates are throwaway; everything else comes across.
        shutil.copytree(old_dir, new_dir, ignore=shutil.ignore_patterns("updates"))
        for side in _SIDE:
            src = new_dir / (_OLD_DB + side)
            if src.exists():
                src.rename(new_dir / (config.DB_PATH.name + side))
        note = f"Moved settings and history from {old_dir} (the old folder is kept as a backup)."
        log.info(note)
    except Exception:
        log.exception("Couldn't copy the old InvoiceM8 data folder")
        return ""
    _rename_startup_entry()
    return note


def _rename_startup_entry() -> None:
    """Rename the old 'InvoiceM8' Run value to the new name, keeping its command."""
    try:
        import winreg
    except ImportError:          # not Windows
        return
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, config.RUN_KEY, 0,
                            winreg.KEY_READ | winreg.KEY_SET_VALUE) as key:
            try:
                command, kind = winreg.QueryValueEx(key, config.LEGACY_RUN_VALUE_NAME)
            except FileNotFoundError:
                return
            winreg.SetValueEx(key, config.RUN_VALUE_NAME, 0, kind, command)
            winreg.DeleteValue(key, config.LEGACY_RUN_VALUE_NAME)
    except OSError:
        log.exception("Couldn't rename the Start with Windows entry")
