"""Popup + window used by the Settings help affordances.

* :class:`HelpPopup`  - tiny borderless bubble shown by a row's ``?`` button.
* :class:`GuideWindow` - scrollable window with the full setup guide(s) for
  the currently-selected providers.
"""
from __future__ import annotations

import customtkinter as ctk

from gui.theme import C, FONT_HEAD, FONT_UI, accent_button
from gui.theme import apply_icon


class HelpPopup(ctk.CTkToplevel):
    """Transient help bubble anchored near the widget that opened it."""

    def __init__(self, master, title: str, text: str) -> None:
        """Build the bubble and position it at the mouse pointer."""
        super().__init__(master)
        apply_icon(self)
        self.overrideredirect(True)                     # no title bar
        self.configure(fg_color=C["border"])
        self.attributes("-topmost", True)

        inner = ctk.CTkFrame(self, fg_color=C["panel"], corner_radius=4)
        inner.pack(padx=1, pady=1, fill="both", expand=True)
        ctk.CTkLabel(inner, text=title, font=FONT_HEAD, text_color=C["blue"],
                     anchor="w", justify="left").pack(anchor="w", padx=12, pady=(10, 2))
        ctk.CTkLabel(inner, text=text, font=FONT_UI, text_color=C["text"],
                     anchor="w", justify="left", wraplength=380).pack(
            anchor="w", padx=12, pady=(0, 8))
        accent_button(ctk, inner, "Got it", self.destroy,
                      colour=C["btn_off"], width=70, height=24).pack(anchor="e", padx=12, pady=(0, 10))

        # Position at the mouse pointer, nudged so it stays on screen.
        x = self.winfo_pointerx() + 12
        y = self.winfo_pointery() + 12
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"+{min(x, sw - w - 20)}+{min(y, sh - h - 40)}")

        self.bind("<Escape>", lambda _e: self.destroy())
        # Close when focus moves elsewhere.
        self.bind("<FocusOut>", lambda _e: self.after(120, self._maybe_close))
        self.after(60, self.focus_force)

    def _maybe_close(self) -> None:
        """Close once focus has genuinely moved to another window."""
        if self.focus_get() is None:
            self.destroy()


class GuideWindow(ctk.CTkToplevel):
    """Full 'where do I get these keys' guide for one or more sections."""

    def __init__(self, master, sections: list[tuple[str, str]]) -> None:
        """Render one scrollable card per guide section."""
        super().__init__(master)
        apply_icon(self)
        self.title("InvoiceWarden - Setup guide")
        self.geometry("640x680")
        self.configure(fg_color=C["bg"])
        self.attributes("-topmost", True)
        self.after(300, lambda: self.attributes("-topmost", False))

        body = ctk.CTkScrollableFrame(self, fg_color=C["bg"])
        body.pack(fill="both", expand=True, padx=10, pady=10)
        for heading, text in sections:
            ctk.CTkLabel(body, text=heading, font=FONT_HEAD, text_color=C["blue"],
                         anchor="w").pack(anchor="w", padx=6, pady=(14, 4))
            card = ctk.CTkFrame(body, fg_color=C["panel"])
            card.pack(fill="x", padx=6)
            ctk.CTkLabel(card, text=text, font=FONT_UI, text_color=C["text"],
                         anchor="w", justify="left", wraplength=560).pack(
                anchor="w", padx=12, pady=10)
        accent_button(ctk, self, "Close", self.destroy,
                      colour=C["btn_off"]).pack(pady=(0, 10))
