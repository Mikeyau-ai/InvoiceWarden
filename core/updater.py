"""Self-update for frozen InvoiceWarden builds.

Checks the Sixth Day Studios update store (sixthdaystudios.com) for a newer
InvoiceWarden, downloads it, and swaps it in via a detached helper script (a
running exe cannot overwrite itself).

The website serves ``latest``: {"body": "<json text>", "sig": "<base64url
Ed25519 signature of body>"}. The body is {app, version, published, notes[],
file, size, sha256}. It is signed on the publisher's PC by
tools/release_upload.py with a key that never leaves it and is checked here
against PUBLIC_KEY, so a tampered website or bucket cannot push a bad update.
The downloaded file's size and SHA-256 must also match the signed body before
the swap helper is ever started.

* Stdlib only (urllib / json / threading) - adds nothing to requirements and
  can run before the GUI is up.
* Inactive when running from source (``sys.frozen`` is False): a dev tree may
  legitimately be ahead of the published release.
* Never raises for network problems - a failed background check is a silent no-op.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from core import ed25519

APP = "invoicewarden"
LATEST_URL = "https://sixthdaystudios.com/api/updates/invoicewarden/latest"
DOWNLOAD_URL = "https://sixthdaystudios.com/api/updates/invoicewarden/"
# The public half of the Sixth Day Studios update key (the private half stays
# on the publisher's PC, never in this repo).
PUBLIC_KEY = base64.urlsafe_b64decode("coayBI-TlGPo3dSHnUc-j67dGqPUM1TmX5Z8pTqi3CQ=")
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
    """The update check could not complete - offline, server error, bad signature.

    Distinct from "checked fine, already current" so the manual check can tell
    the user their check *failed* instead of silently implying they are current.
    """


# -- update info ----------------------------------------------------
@dataclass
class UpdateInfo:
    """A newer release found in the update store.

    ``notes`` is the manifest's note list rendered as "- bullet" lines, the
    same bullet-text shape the update dialog has always shown. ``sha256`` is
    the signed fingerprint; :func:`download` refuses to run without one.
    """

    version: str
    url: str
    size: int
    notes: str
    sha256: str = ""

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


def read_manifest(raw, public_key: bytes = PUBLIC_KEY) -> dict:
    """The release described by a signed ``latest`` response.

    Raises ValueError unless the body is genuinely signed by ``public_key`` and
    describes an InvoiceWarden release with a safe file name, int size and
    64-hex SHA-256.
    """
    try:
        outer = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
        body, sig = outer["body"], base64.urlsafe_b64decode(outer["sig"])
        if not isinstance(body, str):
            raise TypeError("body is not text")
    except (ValueError, KeyError, TypeError) as e:
        raise ValueError(f"the update information is malformed ({e})") from None
    # The signature is checked over the exact body text BEFORE it is parsed.
    if not ed25519.verify(public_key, body.encode("utf-8"), sig):
        raise ValueError("the update information is not signed by Sixth Day Studios")
    try:
        info = json.loads(body)
    except ValueError:
        raise ValueError("the update information is malformed (body is not JSON)") from None
    if not isinstance(info, dict) or info.get("app") != APP:
        raise ValueError("the update information is for another app")
    name = info.get("file")
    if (not isinstance(name, str) or not name.endswith(".exe") or "/" in name
            or "\\" in name or name.startswith(".")):
        raise ValueError("the update names an odd file")
    size, digest = info.get("size"), info.get("sha256")
    if (not isinstance(size, int) or isinstance(size, bool) or size <= 0
            or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest)):
        raise ValueError("the update is missing its size or fingerprint")
    version = info.get("version")
    # The version becomes part of a local file name, so keep it to digits and dots.
    if not isinstance(version, str) or not re.fullmatch(r"\d+(\.\d+)*", version):
        raise ValueError("the update has an odd version number")
    if not isinstance(info.get("notes"), list):
        info["notes"] = []
    return info


def _info_from_manifest(manifest: dict) -> UpdateInfo:
    """Turn a verified manifest into the UpdateInfo the GUI consumes."""
    notes = "\n".join(f"- {n}" for n in manifest["notes"] if isinstance(n, str) and n.strip())
    return UpdateInfo(
        version=manifest["version"],
        url=DOWNLOAD_URL + urllib.parse.quote(manifest["file"]),
        size=manifest["size"],
        notes=notes,
        sha256=manifest["sha256"].lower(),
    )


def _fetch_latest(*, strict: bool = False, public_key: bytes = PUBLIC_KEY) -> UpdateInfo | None:
    """Fetch and verify the signed manifest. Returns None for every failure mode.

    With ``strict=True`` a failure instead raises :class:`UpdateCheckError`
    carrying a readable reason - used by the manual "Check now" button so a
    network problem is not reported to the user as "you're up to date".
    A manifest that fails verification is NEVER returned, in either mode.
    """
    req = urllib.request.Request(LATEST_URL, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=_CHECK_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        if strict:
            if exc.code == 404:
                raise UpdateCheckError("no published release was found") from exc
            raise UpdateCheckError(
                f"the update server returned an error (HTTP {exc.code})") from exc
        return None
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
        if strict:
            raise UpdateCheckError(
                "couldn't reach sixthdaystudios.com - check your internet connection") from exc
        return None

    try:
        return _info_from_manifest(read_manifest(raw, public_key))
    except ValueError as exc:
        if strict:
            raise UpdateCheckError(str(exc)) from exc
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
    polled per chunk. The file's size and SHA-256 are checked against the
    signed manifest values before it is renamed into place; on any mismatch (or
    a missing fingerprint) it is deleted and None is returned. Never leaves a
    partial file behind.
    """
    # Fail closed: without the signed fingerprint nothing may be installed.
    if not info.sha256 or info.size <= 0:
        return None
    _UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    # Local name is fixed by version; the swap always targets the running exe.
    dest = _UPDATE_DIR / f"InvoiceWarden-{info.version}.exe"
    part = dest.with_suffix(".exe.part")

    # Also sweeps .part leftovers from an interrupted earlier download.
    for old in [*_UPDATE_DIR.glob("InvoiceWarden-*.exe"),
                *_UPDATE_DIR.glob("InvoiceWarden-*.exe.part")]:
        if old != dest:
            try:
                old.unlink()
            except OSError:
                pass

    req = urllib.request.Request(info.url, headers={"User-Agent": _UA})
    digest = hashlib.sha256()
    got = 0
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            total = int(resp.headers.get("Content-Length") or info.size or 0)
            with open(part, "wb") as fh:
                while True:
                    if cancel is not None and cancel():
                        raise InterruptedError("cancelled")
                    chunk = resp.read(256 * 1024)
                    if not chunk:
                        break
                    got += len(chunk)
                    if got > info.size:          # more than the signed size: abort
                        raise ValueError("download larger than announced")
                    digest.update(chunk)
                    fh.write(chunk)
                    if progress_cb:
                        progress_cb(got, total)
    except Exception:
        part.unlink(missing_ok=True)
        return None

    # Size and fingerprint must match the signed manifest before anything is installed.
    if got != info.size or digest.hexdigest().lower() != info.sha256.lower():
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
echo    InvoiceWarden auto-updater    sixthdaystudios.com
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
