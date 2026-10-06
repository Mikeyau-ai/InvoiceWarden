"""Visual theme for InvoiceWarden - Sixth Day Studios' dark palette, shared with RamWarden.

RamBo uses a hand-rolled near-black dark theme with Segoe UI / Consolas and
flat accent buttons. We reproduce the same colours and type here on top of
CustomTkinter so the two apps feel like one product family.
"""
from __future__ import annotations

import ctypes

# --- RamBo colour palette (verbatim) --------------------------------------
C = {
    "bg":       "#121212",
    "panel":    "#1a1a1a",
    "row":      "#1f1f1f",
    "row_alt":  "#272727",
    "border":   "#2e2e2e",
    "hairline": "#262626",
    "text":     "#dcdcdc",
    "dim":      "#6b6b6b",
    "dimmer":   "#4a4a4a",
    "green":    "#4caf50",
    "red":      "#e05252",
    "yellow":   "#e0a040",
    "orange":   "#e07840",
    "blue":     "#5296e0",
    "purple":   "#b06ed8",
    "select":   "#1e4468",
    "warn":     "#c0392b",
    "chip_on":  "#2a2a2a",
    "chip_off": "#1a1a1a",
    "btn_off":  "#242424",
    # Sixth Day Studios teal (shared with RamWarden) and the "needs attention" amber.
    "teal":     "#2cc4a8",
    "teal_btn": "#0f8f7a",
    "amber":    "#e8b04a",
    "amber_bg": "#2e2617",
}

# --- Fonts (RamBo spec) -------------------------------------------------
FONT_UI = ("Segoe UI", 13)
FONT_UI_BOLD = ("Segoe UI Semibold", 13)
FONT_BTN = ("Segoe UI Semibold", 13)
FONT_DATA = ("Consolas", 12)
FONT_HEAD = ("Segoe UI Semibold", 13)
FONT_WORDMARK = ("Consolas", 22, "bold")
FONT_TAGLINE = ("Segoe UI", 12)


def shade(hex_colour: str, factor: float) -> str:
    """Lighten (factor > 1) or darken (factor < 1) a #rrggbb colour."""
    h = hex_colour.lstrip("#")
    rgb = [int(h[i:i + 2], 16) for i in (0, 2, 4)]
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(v * factor))) for v in rgb)


def dark_titlebar(window) -> None:
    """Ask DWM for a dark title bar so the frame matches the app."""
    try:
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        flag = ctypes.c_int(1)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attr, ctypes.byref(flag), ctypes.sizeof(flag)
            ) == 0:
                break
    except Exception:
        pass


def apply(ctk) -> None:
    """Push palette defaults into CustomTkinter before any widgets exist."""
    ctk.set_appearance_mode("dark")
    # Single-colour tuples keep CTk from switching on light mode.
    ctk.ThemeManager.theme["CTk"]["fg_color"] = [C["bg"], C["bg"]]
    ctk.ThemeManager.theme["CTkToplevel"]["fg_color"] = [C["bg"], C["bg"]]
    ctk.ThemeManager.theme["CTkFrame"].update(
        fg_color=[C["panel"], C["panel"]],
        top_fg_color=[C["bg"], C["bg"]],
        border_color=[C["border"], C["border"]],
    )
    ctk.ThemeManager.theme["CTkButton"].update(
        fg_color=[C["btn_off"], C["btn_off"]],
        hover_color=[shade(C["btn_off"], 1.4), shade(C["btn_off"], 1.4)],
        text_color=[C["text"], C["text"]],
        border_width=0, corner_radius=4,
    )
    ctk.ThemeManager.theme["CTkEntry"].update(
        fg_color=[C["row"], C["row"]],
        border_color=[C["border"], C["border"]],
        text_color=[C["text"], C["text"]],
        border_width=1, corner_radius=3,
    )
    for widget in ("CTkComboBox", "CTkOptionMenu"):
        ctk.ThemeManager.theme[widget].update(
            fg_color=[C["row"], C["row"]],
            button_color=[C["btn_off"], C["btn_off"]],
            button_hover_color=[C["select"], C["select"]],
            text_color=[C["text"], C["text"]],
            corner_radius=3,
        )
    ctk.ThemeManager.theme["CTkCheckBox"].update(
        fg_color=[C["teal_btn"], C["teal_btn"]],
        hover_color=[shade(C["teal_btn"], 1.15)] * 2,
        text_color=[C["text"], C["text"]],
        border_color=[C["border"], C["border"]],
    )
    ctk.ThemeManager.theme["CTkSwitch"].update(
        progress_color=[C["teal"], C["teal"]],
        button_color=[C["text"], C["text"]],
        fg_color=[C["btn_off"], C["btn_off"]],
        text_color=[C["text"], C["text"]],
    )
    if "CTkSegmentedButton" in ctk.ThemeManager.theme:
        ctk.ThemeManager.theme["CTkSegmentedButton"].update(
            fg_color=[C["bg"], C["bg"]],
            selected_color=[C["select"], C["select"]],
            selected_hover_color=[shade(C["select"], 1.2)] * 2,
            unselected_color=[C["bg"], C["bg"]],
            unselected_hover_color=[C["row"], C["row"]],
            text_color=[C["text"], C["text"]],
        )
    if "CTkScrollbar" in ctk.ThemeManager.theme:
        ctk.ThemeManager.theme["CTkScrollbar"].update(
            fg_color="transparent",
            button_color=[C["border"], C["border"]],
            button_hover_color=[C["dim"], C["dim"]],
        )
    ctk.ThemeManager.theme["CTkTextbox"].update(
        fg_color=[C["row"], C["row"]],
        border_color=[C["border"], C["border"]],
        text_color=[C["text"], C["text"]],
    )


def accent_button(ctk, parent, text, command, colour=None, **kw):
    """Flat, semibold accent button in the RamBo style (white text on colour)."""
    colour = colour or C["blue"]
    kw.setdefault("height", 30)
    kw.setdefault("corner_radius", 4)
    return ctk.CTkButton(
        parent, text=text, command=command,
        fg_color=colour, hover_color=shade(colour, 0.82),
        text_color="#ffffff", font=FONT_BTN, **kw
    )


def apply_icon(window) -> None:
    """Give a window the InvoiceWarden icon (taskbar + title bar).

    Same .ico PyInstaller stamps into the executable, so the taskbar icon and
    the file icon always match. Silently ignored if the asset is missing or the
    platform has no .ico support.

    Every window needs this call, including dialogs: CTkToplevel stamps
    CustomTkinter's own logo on itself shortly after construction, so a
    Toplevel that never calls this shows the library's blue tile instead of
    ours. We set the icon twice - once now, once after CustomTkinter's own
    deferred call has run - so ours is the one that survives.
    """
    from config import ICON_PATH

    if not ICON_PATH.exists():
        return

    def _set() -> None:
        """Stamp the .ico onto the window, ignoring platform refusals."""
        try:
            window.iconbitmap(default=str(ICON_PATH))
        except Exception:
            pass

    _set()
    try:
        window.after(300, _set)
    except Exception:
        pass
