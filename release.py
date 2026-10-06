"""Build dist/InvoiceWarden.exe and, if you say yes, publish it as a GitHub release.

One script, driven by two questions. ``release.bat`` is a thin wrapper:

    python release.py     # run tests, then ask:  build a fresh exe?  publish it?

Non-interactive overrides (for scripting):

    --yes           answer yes to both questions
    --build-only    build, never publish (the old build.bat)
    --skip-build    don't rebuild; publish the exe already in dist/

The permanent download URL - also what installed builds poll for updates - is:

    https://github.com/<owner>/<repo>/releases/latest/download/InvoiceWarden.exe

Bump version.py, run release.bat, answer the prompts. Publishing needs the
GitHub CLI:  winget install GitHub.cli  &&  gh auth login
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from core.updater import GITHUB_REPO
from version import APP_VERSION

EXE = Path("dist/InvoiceWarden.exe")
#: The same build under the app's old name. Copies still on InvoiceM8 only look for an
#: asset with this name, so every release carries it until those have all updated.
LEGACY_EXE = Path("dist/InvoiceM8.exe")
SPEC = "InvoiceWarden.spec"


def gh(*args: str) -> subprocess.CompletedProcess:
    """Run a gh subcommand, capturing output."""
    return subprocess.run(["gh", *args], capture_output=True, text=True)


def build() -> bool:
    """Clean previous output and build the one-file exe with PyInstaller.

    Invoked as ``python -m PyInstaller`` (not the bare ``pyinstaller`` shim):
    this machine has more than one Python on PATH and the shim can resolve to
    the wrong interpreter, silently building against the wrong site-packages.
    Build from a venv with the full requirements.txt installed to bake in the
    AI / Graph / attachment features (the spec skips any that are missing).
    """
    print("  Building dist/InvoiceWarden.exe ...")
    for stale in ("build", "dist"):
        shutil.rmtree(stale, ignore_errors=True)
    if subprocess.run([sys.executable, "-m", "pip", "install", "-q",
                       "--upgrade", "pyinstaller"]).returncode != 0:
        print("  Could not install PyInstaller.")
        return False
    if subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm",
                       "--clean", SPEC]).returncode != 0:
        print("  BUILD FAILED.")
        return False
    print(f"  Built {EXE}")
    return True


def changelog(version: str) -> str:
    """The CHANGELOG.md section for this version, verbatim.

    CHANGELOG.md is the single source of truth (it is also bundled into the
    exe for the in-app About window). We don't derive notes from git tags -
    releases are tagged by `gh release create`, so the tag does not exist
    locally when this runs.
    """
    try:
        text = Path("CHANGELOG.md").read_text(encoding="utf-8")
    except OSError:
        return "- See the commit history."

    out: list[str] = []
    capturing = False
    for line in text.splitlines():
        if line.startswith("## "):
            if capturing:
                break
            capturing = line[3:].strip() == version
            continue
        if capturing and (line.strip() or out):
            out.append(line.rstrip())
    body = "\n".join(out).strip()
    return body or f"- Release {version}. See CHANGELOG.md for details."


def run_tests() -> bool:
    """Run the unit suite; a failing build must never be published."""
    print("  Running tests...")
    result = subprocess.run([sys.executable, "-m", "unittest", "discover",
                             "-s", "tests"], capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stdout[-2000:] or result.stderr[-2000:])
        return False
    print("  Tests passed.")
    return True


def _ask(question: str, *, default: bool) -> bool:
    """Y/N prompt. Blank input - or a closed stdin - takes ``default``."""
    try:
        reply = input(f"{question} {'[Y/n]' if default else '[y/N]'} ").strip().lower()
    except EOFError:
        return default
    return default if not reply else reply in ("y", "yes")


def main() -> int:
    """Test, then interactively build and/or publish. Returns a shell exit code.

    Run with no arguments and answer two prompts: build a fresh exe?, then
    publish it as GitHub release ``v<APP_VERSION>``? Overrides for scripting:
    ``--yes`` (yes to both), ``--build-only`` (build, never publish),
    ``--skip-build`` (publish the exe already in ``dist/``).
    """
    args = set(sys.argv[1:])
    unknown = args - {"--build-only", "--skip-build", "--yes"}
    if unknown:
        # Fail loudly rather than let a typo'd flag fall through to a publish.
        print(f"  Unknown option(s): {' '.join(sorted(unknown))}")
        print("  Usage: release.py [--build-only | --skip-build] [--yes]")
        return 2
    build_only = "--build-only" in args
    skip_build = "--skip-build" in args
    assume_yes = "--yes" in args

    if not run_tests():
        print("  TESTS FAILED - stopping.")
        return 1

    # --- question 1: build a fresh exe? -----------------------------
    if skip_build:
        do_build = False
    elif assume_yes or build_only:
        do_build = True
    else:
        do_build = _ask("\n  Build a fresh InvoiceWarden.exe?", default=True)

    if do_build and not build():
        return 1
    if not EXE.exists():
        print(f"  {EXE} not found - build one first.")
        return 1
    stat = EXE.stat()
    exe_desc = (f"{stat.st_size / 1_048_576:.1f} MB, built "
                f"{datetime.fromtimestamp(stat.st_mtime):%Y-%m-%d %H:%M}")
    if not do_build:
        print(f"  Using the existing {EXE}  ({exe_desc})")

    # --- question 2: publish it as a GitHub release? ---------------
    if build_only:
        print(f"\n  Done. {EXE} is ready to run.\n"
              f"  (settings + database live in %LOCALAPPDATA%\\InvoiceWarden)\n")
        return 0

    if gh("--version").returncode != 0:
        print("  GitHub CLI not found:  winget install GitHub.cli")
        return 1
    if gh("auth", "status").returncode != 0:
        print("  Not logged in:  gh auth login")
        return 1

    slug = gh("repo", "view", "--json", "nameWithOwner",
              "-q", ".nameWithOwner").stdout.strip()
    if slug.lower() != GITHUB_REPO.lower():
        print(f"  Remote is {slug} but core/updater.py checks {GITHUB_REPO}.\n"
              f"  Fix GITHUB_REPO before releasing or clients go stale.")
        return 1

    tag = f"v{APP_VERSION}"
    exists = gh("release", "view", tag).returncode == 0

    # Publishing is outward-facing and marks this build "Latest" - every
    # installed client updates to it. Show what will ship, then confirm.
    print(f"\n  {'Update' if exists else 'Publish'} GitHub release {tag} on {slug}"
          f"  (marked 'Latest' - every install auto-updates to it)")
    print(f"    asset : {EXE}  ({exe_desc})")
    print(f"    notes : {changelog(APP_VERSION).splitlines()[0][:72]}")
    if not assume_yes and not _ask("  Publish now?", default=False):
        print(f"\n  Not published. The build is in {EXE} if you want it.\n")
        return 0

    notes = (
        f"## What's new\n\n{changelog(APP_VERSION)}\n\n"
        "---\n\n"
        "Download `InvoiceWarden.exe` and run it - no install, no admin. It is "
        "unsigned, so Windows SmartScreen shows \"Windows protected your PC\": "
        "click **More info** then **Run anyway**. Existing installs update "
        "themselves from this release."
    )

    shutil.copyfile(EXE, LEGACY_EXE)
    if exists:
        print(f"  Release {tag} exists - updating assets + notes")
        r1 = gh("release", "upload", tag, str(EXE), str(LEGACY_EXE), "--clobber")
        r2 = gh("release", "edit", tag, "--notes", notes)
        ok = r1.returncode == 0 and r2.returncode == 0
        if not ok:
            print("  " + (r1.stderr.strip() or r2.stderr.strip()))
    else:
        r = gh("release", "create", tag, str(EXE), str(LEGACY_EXE),
               "--title", f"InvoiceWarden {tag}", "--notes", notes)
        ok = r.returncode == 0
        print("  " + (f"Created release {tag}" if ok else r.stderr.strip()))

    if ok:
        print(f"\n  Permanent link:\n"
              f"  https://github.com/{slug}/releases/latest/download/InvoiceWarden.exe\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
