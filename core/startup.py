"""Windows "Run on startup" toggle via the per-user Run registry key.

Uses HKCU so it needs no administrator rights. The command launches the
built exe (or, from source, the current Python against ``main.py``) with the
``--autostart`` flag, which starts the app with the watcher running.
"""
from __future__ import annotations

import sys
from pathlib import Path

from config import RUN_KEY, RUN_VALUE_NAME

try:
    import winreg  # stdlib, Windows only
except ImportError:  # pragma: no cover - non-Windows dev
    winreg = None


def _launch_command() -> str:
    """Absolute command string that re-launches this app at login."""
    if getattr(sys, "frozen", False):
        # The built exe is the whole app; there is no main.py to pass.
        return f'"{sys.executable}" --autostart'
    main_py = Path(__file__).resolve().parent.parent / "main.py"
    return f'"{sys.executable}" "{main_py}" --autostart'


def is_enabled() -> bool:
    """True if the Run key currently points at this app."""
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, RUN_VALUE_NAME)
            return bool(value)
    except FileNotFoundError:
        return False


def set_enabled(enabled: bool) -> None:
    """Add or remove the startup entry."""
    if winreg is None:
        raise RuntimeError("Registry access is only available on Windows.")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if enabled:
            winreg.SetValueEx(key, RUN_VALUE_NAME, 0, winreg.REG_SZ, _launch_command())
        else:
            try:
                winreg.DeleteValue(key, RUN_VALUE_NAME)
            except FileNotFoundError:
                pass
