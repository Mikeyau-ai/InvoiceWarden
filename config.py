"""Global paths and constants for InvoiceWarden.

Everything the app persists lives under %LOCALAPPDATA%\\InvoiceWarden so the
project folder stays clean and the data survives a code update.
"""
from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "InvoiceWarden"

#: The app's name before 2026-10 (see core/migrate.py).
LEGACY_NAME = "InvoiceM8"

#: Entra "Application (client) ID" shipped with InvoiceWarden so customers never
#: have to touch Azure. Public-client IDs are not secrets (Thunderbird, Postman
#: and others ship theirs the same way) - the sign-in still happens against the
#: user's own Microsoft account, and no client secret exists. A site can still
#: override it in Settings to use their own app registration.
DEFAULT_GRAPH_CLIENT_ID = "c5efd32b-1477-4928-a238-c726076895d4"

#: Hard cap on monitored mailboxes.
MAX_MAIL_ACCOUNTS = 10

# Base data directory (created on first run).
if os.name == "nt":
    _base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
else:  # allow running/tests on non-Windows
    _base = Path.home() / ".local" / "share"

DATA_DIR = _base / APP_NAME
DB_PATH = DATA_DIR / "invoicewarden.sqlite3"
#: Where InvoiceM8 kept everything; copied to DATA_DIR once by core/migrate.py.
LEGACY_DATA_DIR = _base / LEGACY_NAME
ATTACHMENT_CACHE = DATA_DIR / "attachments"
LOG_DIR = DATA_DIR / "logs"

# keyring service name under which the Fernet master key is stored (DPAPI-backed).
# Deliberately still the OLD name: every saved key was encrypted with this master key,
# so renaming it would make them all unreadable after the InvoiceWarden rename.
KEYRING_SERVICE = "InvoiceM8-master-key"
KEYRING_USERNAME = "fernet"

# Registry key used by the "Run on startup" toggle.
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE_NAME = APP_NAME
LEGACY_RUN_VALUE_NAME = LEGACY_NAME

# Watcher defaults.
DEFAULT_POLL_MINUTES = 5
# How long a processed email's cached attachments are kept on disk. They are
# only needed while an upload might still be retried; see core.housekeeping.
CACHE_RETENTION_DAYS = 30
SUPPORTED_FILE_TYPES = ["pdf", "docx", "csv", "xlsx", "png", "jpg"]
DEFAULT_FILE_TYPES = ["pdf"]


def ensure_dirs() -> None:
    """Create all runtime directories. Safe to call repeatedly."""
    for d in (DATA_DIR, ATTACHMENT_CACHE, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)


def resource_path(name: str) -> Path:
    """Absolute path to a bundled asset, from source or a PyInstaller build.

    PyInstaller unpacks datas into ``sys._MEIPASS`` at runtime; running from
    source they sit next to this file.
    """
    import sys

    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / name


#: Window/taskbar icon - the same file PyInstaller stamps into the exe.
ICON_PATH = resource_path("assets/icon.ico")
