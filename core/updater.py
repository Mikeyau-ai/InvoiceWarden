"""Self-update for frozen InvoiceWarden builds.

Mirrors the updater pattern used by the other apps (RamBo / Ashen Fall):
check GitHub Releases for a newer ``InvoiceWarden.exe``, download it, and swap it
in via a detached helper script (a running exe cannot overwrite itself).

* Stdlib only (urllib / json / threading) - adds nothing to requirements and
  can run before the GUI is up.
* Inactive when running from source (``sys.frozen`` is False): a dev tree may
  legitimately be ahead of the published release.
* Never raises for network problems - a failed check is a silent no-op.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

# Public repo whose Releases host the build. Overridable for testing.
# Renamed from Mikeyau-ai/Invoicem8; GitHub redirects the old address, which is how
# copies still on the old version find the first InvoiceWarden release.
GITHUB_REPO = os.getenv("INVOICEWARDEN_UPDATE_REPO", "Mikeyau-ai/InvoiceWarden")
_API_LATEST = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
_ASSET_NAME = "InvoiceWarden.exe"
#: Older releases (and each release's copy for installs still on the old name) use this.
_LEGACY_ASSET_NAME = "Invoice" "M8.exe"
_UA = "InvoiceWarden-Updater"
_CHECK_TIMEOUT = 8

USER_ROOT = Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "InvoiceWarden"
_UPDATE_DIR = USER_ROOT / "updates"
_STATE = USER_ROOT / "updater.json"


# -- tiny JSON state (two keys, not worth the DB) -----------------------
def _state() -> dict:
    """Read the tiny updater state file; {} if absent or corrupt."""
    try:
        return json.loads(_STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(key: str, value) -> None:
    """Merge one key into the updater state file."""
    data = _state()
    data[key] = value
    try:
        USER_ROOT.mkdir(parents=True, exist_ok=True)
        _STATE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


# -- version helpers --------------------------------------------------
def _parse_version(text: str) -> tuple[int, ...]:
    """'v1.2.3' / '1.2.3' -> (1, 2, 3). Junk -> (0,) so it always sorts oldest."""
    nums = re.findall(r"\d+", text or "")
    return tuple(int(n) for n in nums) if nums else (0,)


def current_version() -> str:
    """Running build's version."""
    try:
        from version import APP_VERSION
        return APP_VERSION
    except Exception:
        return "0.0.0"


def running_exe() -> Path:
    """Absolute path of the running executable (only meaningful when frozen)."""
    return Path(sys.executable).resolve()


def is_frozen() -> bool:
    """True when running as the built .exe rather than from source."""
    return bool(getattr(sys, "frozen", False))


def is_enabled() -> bool:
    """True only for a frozen build with the auto-check left switched on."""
    return is_frozen() and bool(_state().get("update_check", True))


def set_enabled(on: bool) -> None:
    """Persist the auto-update-check preference."""
    _save_state("update_check", bool(on))


def auto_check_pref() -> bool:
    """The stored preference regardless of frozen state (for the Settings UI)."""
    return bool(_state().get("update_check", True))


class UpdateCheckError(Exception):
    """The update check could not complete - offline, GitHub error, rate limit.

    Distinct from "checked fine, already current" so the manual check can tell
    the user their check *failed* instead of silently implying they are current.
    """


# -- update info ----------------------------------------------------
@dataclass
class UpdateInfo:
    """A newer release found on GitHub."""

    version: str
    url: str
    size: int
    notes: str

    @property
    def size_mb(self) -> float:
        """Download size in megabytes, for the dialog."""
        return self.size / (1024 * 1024)

    def note_lines(self) -> list[str]:
        """Changelog as one string per bullet; stops at the '---' rule.

        Wrapped bullets (a bullet whose text continues on indented lines) are
        joined back together so the dialog shows one entry per change.
        """
        out: list[str] = []
        for raw in (self.notes or "").splitlines():
            line = raw.strip()
            if line.startswith("---"):
                break
            if line.startswith("#") or not line:
                continue
            clean = line.replace("**", "").replace("`", "").strip()
            if line.startswith(("-", "*")):
                out.append(clean.lstrip("-*").strip())
            elif out:                       # continuation of the previous bullet
                out[-1] = f"{out[-1]} {clean}".strip()
            else:
                out.append(clean)
        return out[:15]


def _http_error_reason(exc: urllib.error.HTTPError) -> str:
    """Short human explanation for an HTTPError from the Releases API."""
    if exc.code == 403 and exc.headers.get("X-RateLimit-Remaining") == "0":
        return ("GitHub is rate-limiting update checks from your network - "
                "wait an hour, or download the update manually")
    if exc.code == 404:
        return "no published release was found on GitHub"
    return f"GitHub returned an error (HTTP {exc.code})"


def _fetch_latest(*, strict: bool = False) -> UpdateInfo | None:
    """Query the Releases API. Returns None for every failure mode.

    With ``strict=True`` a failure instead raises :class:`UpdateCheckError`
    carrying a readable reason - used by the manual "Check now" button so a
    network problem is not reported to the user as "you're up to date".
    """
    req = urllib.request.Request(
        _API_LATEST,
        headers={"User-Agent": _UA, "Accept": "application/vnd.github+json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_CHECK_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if strict:
            raise UpdateCheckError(_http_error_reason(exc)) from exc
        return None
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
        if strict:
            raise UpdateCheckError(
                "couldn't reach GitHub - check your internet connection") from exc
        return None

    tag = (data.get("tag_name") or "").lstrip("vV")
    if not tag:
        if strict:
            raise UpdateCheckError("GitHub reported no published release")
        return None
    # Prefer the new exe name; accept the old one so older releases still count.
    assets = {(a.get("name") or "").lower(): a for a in data.get("assets") or []}
    for name in (_ASSET_NAME, _LEGACY_ASSET_NAME):
        asset = assets.get(name.lower())
        if asset:
            return UpdateInfo(
                version=tag,
                url=asset.get("browser_download_url") or "",
                size=int(asset.get("size") or 0),
                notes=(data.get("body") or "").strip(),
            )
    if strict:
        raise UpdateCheckError(
            f"the latest release (v{tag}) has no {_ASSET_NAME} attached yet")
    return None


def _is_newer(info: UpdateInfo) -> bool:
    """True when this release is newer and has not been skipped."""
    if _parse_version(info.version) <= _parse_version(current_version()):
        return False
    return _state().get("skip_version") != info.version


def skip_version(version: str) -> None:
    """Remember a declined version so it is not re-offered automatically."""
    _save_state("skip_version", version)


# -- background check ------------------------------------------------
_result: UpdateInfo | None = None
_done = threading.Event()


def start_check() -> None:
    """Kick off the version check on a daemon thread. No-ops from source or
    when auto-update is disabled."""
    if not is_enabled():
        _done.set()
        return

    def _work() -> None:
        """Daemon thread body: fetch, compare, publish the result."""
        global _result
        try:
            info = _fetch_latest()
            if info and info.url and _is_newer(info):
                _result = info
        finally:
            _done.set()

    threading.Thread(target=_work, daemon=True, name="update-check").start()


def pending_update() -> UpdateInfo | None:
    """The update found by :func:`start_check`, once the check has finished."""
    return _result if _done.is_set() else None


def check_now() -> UpdateInfo | None:
    """Synchronous check that ignores the enabled flag - for the 'Check now'
    button. Returns a newer release (even a skipped one), or None if already
    current. Raises :class:`UpdateCheckError` if the check itself could not
    complete."""
    info = _fetch_latest(strict=True)
    if info and info.url and _parse_version(info.version) > _parse_version(current_version()):
        return info
    return None


# -- download + apply ---------------------------------------------
def download(info: UpdateInfo, progress_cb=None, cancel=None) -> Path | None:
    """Download the new exe to the updates dir. Returns its path or None.

    ``progress_cb(done, total)`` is called as chunks arrive; ``cancel()`` is
    polled per chunk. Never leaves a partial file behind.
    """
    _UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    dest = _UPDATE_DIR / f"InvoiceWarden-{info.version}.exe"
    part = dest.with_suffix(".exe.part")

    for old in _UPDATE_DIR.glob("InvoiceWarden-*.exe"):
        if old != dest:
            try:
                old.unlink()
            except OSError:
                pass

    req = urllib.request.Request(info.url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            total = int(resp.headers.get("Content-Length") or info.size or 0)
            got = 0
            with open(part, "wb") as fh:
                while True:
                    if cancel is not None and cancel():
                        raise InterruptedError("cancelled")
                    chunk = resp.read(256 * 1024)
                    if not chunk:
                        break
                    fh.write(chunk)
                    got += len(chunk)
                    if progress_cb:
                        progress_cb(got, total)
    except Exception:
        part.unlink(missing_ok=True)
        return None

    try:
        dest.unlink(missing_ok=True)
        part.rename(dest)
    except OSError:
        part.unlink(missing_ok=True)
        return None
    return dest


# A running exe can't overwrite itself, so a detached cmd waits for us to exit,
# copies the new file over, and relaunches. It runs in its OWN VISIBLE console
# window (CREATE_NEW_CONSOLE) with a clear InvoiceWarden banner and step-by-step
# status, so it can't be mistaken for a stray/malware process - and it holds
# the window open on a visible countdown before closing so the user can read it.
# System tools are called by absolute path so a Unix `find`/`ping` on PATH
# can't shadow the wait loop.
_APPLY_SCRIPT = r"""@echo off
setlocal
title InvoiceWarden Updater  (v{ver})
mode con: cols=80 lines=32 >nul 2>&1
color 0A

set "TASKLIST=%SystemRoot%\System32\tasklist.exe"
set "FIND=%SystemRoot%\System32\find.exe"
set "PING=%SystemRoot%\System32\ping.exe"
set "TIMEOUT=%SystemRoot%\System32\timeout.exe"

cls
echo.
echo    ##### #   # #   #  ###  ##### #### ##### #   #  ###
echo      #   ##  # #   # #   #   #   #    #    ## ## #   #
echo      #   # # # #   # #   #   #   #    ###  # # #  ###
echo      #   #  ## #   # #   #   #   #    #    #   # #   #
echo    ##### #   #   #    ###  ##### #### ##### #   #  ###
echo.
echo   ============================================================
echo    InvoiceWarden auto-updater    github.com/Mikeyau-ai/InvoiceWarden
echo   ============================================================
echo.
echo    This window is part of InvoiceWarden's built-in updater - it is
echo    not a background/malware process. It is installing update
echo    v{ver} in three steps:
echo.
echo      1. wait for InvoiceWarden to close
echo      2. copy the new InvoiceWarden.exe into place
echo      3. restart InvoiceWarden
echo.
echo    It closes itself automatically when finished.
echo   ------------------------------------------------------------
echo.

echo    [....] Waiting for InvoiceWarden (PID {pid}) to close...
set /a tries=0
:wait
"%TASKLIST%" /fi "PID eq {pid}" /nh 2>nul | "%FIND%" "{pid}" >nul
if errorlevel 1 goto ready
set /a tries+=1
if %tries% GEQ 60 goto timedout
"%PING%" -n 3 127.0.0.1 >nul
goto wait

:timedout
echo    [FAIL] InvoiceWarden did not close within 2 minutes - update cancelled.
echo           Your existing version has NOT been changed. Close
echo           InvoiceWarden fully and check for updates again.
echo.
echo    This window stays open so you can read the message above.
"%TIMEOUT%" /t 30 || "%PING%" -n 31 127.0.0.1 >nul
exit /b 1

:ready
echo    [ OK ] InvoiceWarden has closed.
"%PING%" -n 3 127.0.0.1 >nul
echo    [....] Installing v{ver}...
copy /y "{src}" "{dst}" >nul
if errorlevel 1 goto copyfail
echo    [ OK ] New version copied into place.
del /q "{src}" >nul 2>&1
echo    [....] Restarting InvoiceWarden...
rem Clear PyInstaller's onefile env vars so the fresh exe starts as a new
rem top-level process instead of failing the "parent process" security check.
set "_MEIPASS2="
set "_MEIPASS="
set "_PYI_ARCHIVE_FILE="
set "_PYI_PARENT_PROCESS_LEVEL="
set "_PYI_APPLICATION_HOME_DIR="
set "_PYIBoot_SPLASH="
set "_PYI_SPLASH_IPC="
start "" "{dst}"
echo    [ OK ] Done - InvoiceWarden v{ver} is starting.
echo.
echo   ============================================================
echo    Update complete. This window will close in 12 seconds.
echo   ============================================================
"%TIMEOUT%" /t 12 || "%PING%" -n 13 127.0.0.1 >nul
exit /b 0

:copyfail
echo    [FAIL] Could not replace:
echo             {dst}
echo           Your existing version has NOT been changed. Another
echo           copy of InvoiceWarden may still be running, or the file
echo           is locked. Try updating again in a minute.
echo.
echo    This window stays open so you can read the message above.
"%TIMEOUT%" /t 30 || "%PING%" -n 31 127.0.0.1 >nul
exit /b 1
"""


def apply(exe_path: Path, version: str = "") -> bool:
    """Launch the visible swap-script console. Caller must exit immediately."""
    dst = running_exe()
    script = _UPDATE_DIR / "apply_update.cmd"
    try:
        script.write_text(
            _APPLY_SCRIPT.format(pid=os.getpid(), src=str(exe_path),
                                 dst=str(dst), ver=version or current_version()),
            encoding="utf-8",
        )
        # Hand the helper a clean environment: PyInstaller's onefile bootloader
        # exports _PYI_* / _MEIPASS* vars that would make the relaunched exe
        # think it is a child re-exec and abort with a security-check error.
        clean_env = {k: v for k, v in os.environ.items()
                     if not k.startswith(("_PYI", "_MEI"))}
        subprocess.Popen(
            ["cmd", "/c", str(script)],
            close_fds=True,
            env=clean_env,
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
        return True
    except OSError:
        return False
