"""About / changelog window.

Shows the wordmark, version, links, and the bundled CHANGELOG.md. Opened from
Settings > Updates and by clicking the version in the header.
"""
from __future__ import annotations

import sys
import webbrowser
from pathlib import Path

import customtkinter as ctk

from core import updater
from gui.theme import C, FONT_DATA, FONT_TAGLINE, FONT_UI, FONT_WORDMARK, accent_button
from gui.theme import apply_icon
from version import APP_VERSION

REPO_URL = "https://github.com/Mikeyau-ai/InvoiceWarden"


def _load_changelog() -> str:
    """Read CHANGELOG.md from the PyInstaller bundle or the source tree."""
    candidates = []
    mei = getattr(sys, "_MEIPASS", None)
    if mei:
        candidates.append(Path(mei) / "CHANGELOG.md")
    candidates.append(Path(__file__).resolve().parent.parent / "CHANGELOG.md")
    for path in candidates:
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            continue
    return "Changelog not available in this build."


class AboutWindow(ctk.CTkToplevel):
    """Single-instance About window."""

    def __init__(self, master) -> None:
        """Build the about window and load the bundled changelog."""
        super().__init__(master)
        apply_icon(self)
        self.title("About InvoiceWarden")
        self.geometry("560x620")
        self.configure(fg_color=C["bg"])
        self.attributes("-topmost", True)
        self.after(300, lambda: self.attributes("-topmost", False))

        head = ctk.CTkFrame(self, fg_color=C["panel"])
        head.pack(fill="x")
        ctk.CTkLabel(head, text="INVOICEWARDEN", font=FONT_WORDMARK,
                     text_color=C["text"]).pack(anchor="w", padx=16, pady=(12, 0))
        ctk.CTkLabel(head, text=f"v{APP_VERSION}"
                     + ("" if updater.is_frozen() else "   (running from source)"),
                     font=FONT_TAGLINE, text_color=C["dim"]).pack(anchor="w", padx=16)
        ctk.CTkLabel(head, text="Outlook invoices -> ServiceM8 / accounting   .   by Mikey",
                     font=FONT_UI, text_color=C["dim"]).pack(anchor="w", padx=16, pady=(0, 12))

        bar = ctk.CTkFrame(self, fg_color=C["bg"])
        bar.pack(fill="x", padx=16, pady=10)
        accent_button(ctk, bar, "View on GitHub",
                      lambda: webbrowser.open(REPO_URL), colour=C["btn_off"]).pack(side="left")
        accent_button(ctk, bar, "Check for updates",
                      lambda: master.check_updates_now(lambda t: self._msg.configure(text=t)),
                      colour=C["blue"]).pack(side="left", padx=8)
        self._msg = ctk.CTkLabel(self, text="", font=FONT_UI, text_color=C["dim"])
        self._msg.pack(anchor="w", padx=16)

        ctk.CTkLabel(self, text="Changelog", font=FONT_UI,
                     text_color=C["blue"]).pack(anchor="w", padx=16, pady=(8, 2))
        box = ctk.CTkTextbox(self, font=FONT_DATA, wrap="word",
                             fg_color=C["row"], text_color=C["text"])
        box.pack(fill="both", expand=True, padx=16, pady=(0, 10))
        box.tag_config("h", foreground=C["green"])
        for line in _load_changelog().splitlines():
            if line.startswith("# "):
                continue
            tag = "h" if line.startswith("## ") else None
            box.insert("end", line.lstrip("# ") + "\n", tag or ())
        box.configure(state="disabled")

        accent_button(ctk, self, "Close", self.destroy,
                      colour=C["btn_off"]).pack(pady=(0, 12))
