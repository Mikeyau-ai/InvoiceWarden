"""InvoiceWarden entry point.

    python main.py              # normal launch
    python main.py --autostart  # used by the Windows "Run on startup" entry:
                                # starts minimised-ish with the watcher running
"""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

import customtkinter as ctk

from config import LOG_DIR, DB_PATH, ensure_dirs
from core.crypto import SecretBox
from core.database import Database
from core.settings_store import Settings
from gui import theme
from gui.app import App


def _setup_logging() -> None:
    """File + console logging for diagnostics (separate from the in-app log).

    Rotating, because this is a long-running background app: an unbounded
    handler would grow the log file for the lifetime of the install.
    """
    ensure_dirs()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            RotatingFileHandler(LOG_DIR / "invoicewarden.log", encoding="utf-8",
                                maxBytes=2_000_000, backupCount=3),
            logging.StreamHandler(sys.stdout),
        ],
    )


def main() -> None:
    """Build the DB/settings/theme and run the Tk event loop."""
    # First start after the rename: bring the InvoiceM8 data across before anything opens it.
    from core.migrate import migrate_legacy
    moved = migrate_legacy()
    _setup_logging()
    if moved:
        logging.getLogger(__name__).info(moved)
    autostart = "--autostart" in sys.argv

    box = SecretBox()
    db = Database(DB_PATH)
    settings = Settings(db, box)

    theme.apply(ctk)
    app = App(db, settings, box, autostart=autostart)
    try:
        app.mainloop()
    finally:
        # Belt-and-braces: _on_close normally handles this, but make sure the
        # watcher is stopped and the DB is closed even on an abnormal exit.
        app.watcher.stop()
        db.close()


if __name__ == "__main__":
    main()
