"""Build dist/InvoiceWarden-<version>.exe and, if you say yes, publish it to the update store.

One script, driven by two questions. ``release.bat`` is a thin wrapper:

    python release.py     # run tests, then ask:  build a fresh exe?  publish it?

Non-interactive overrides (for scripting):

    --yes               answer yes to every question
    --build-only        build, never publish (the old build.bat)
    --skip-build        don't rebuild; publish the exe already in dist/
    --dry-run           sign and print the update but upload nothing
    --github-transition ONE-TIME extra step: also publish a GitHub release (see below)

Publishing hands the versioned exe to tools/release_upload.py, which signs the
update manifest and uploads both to the Sixth Day Studios update store. The
permanent download link is:

    https://sixthdaystudios.com/api/updates/invoicewarden/download

Bump version.py, add a CHANGELOG.md entry, run release.bat, answer the prompts.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from version import APP_VERSION

EXE = Path("dist/InvoiceWarden.exe")                      # what PyInstaller produces
VERSIONED_EXE = Path(f"dist/InvoiceWarden-{APP_VERSION}.exe")  # what the update store serves
SPEC = "InvoiceWarden.spec"
#: Secrets file holding SDS_UPDATE_SIGNING_KEY and CLOUDFLARE_API_TOKEN.
ENV_FILE = r"Y:\Guardian\.env"
UPLOAD_TOOL = Path("tools/release_upload.py")

# --- one-time GitHub transition (delete everything marked TRANSITION after use) ---
#: TRANSITION: repo whose latest release already-installed copies (<= 1.0.41) poll.
GITHUB_REPO = "Mikeyau-ai/InvoiceWarden"
#: TRANSITION: installs up to v1.0.40 look for an asset called InvoiceM8.exe; attach a copy.
LEGACY_EXE = Path("dist/InvoiceM8.exe")


# TRANSITION: used only by publish_github_transition().
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


def make_versioned() -> None:
    """Copy dist/InvoiceWarden.exe to dist/InvoiceWarden-<version>.exe (the file the store serves).

    The installed exe keeps its own name; only the published file is versioned.
    """
    shutil.copyfile(EXE, VERSIONED_EXE)
    print(f"  Named {VERSIONED_EXE} for the update store")


def publish_store(dry_run: bool) -> bool:
    """Sign and upload the versioned exe via tools/release_upload.py. True on success."""
    cmd = [sys.executable, str(UPLOAD_TOOL), "--installer", str(VERSIONED_EXE),
           "--env-file", ENV_FILE]
    if dry_run:
        cmd.append("--dry-run")
    return subprocess.run(cmd).returncode == 0


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


def publish_github_transition(exe_desc: str, assume_yes: bool) -> bool:
    """TRANSITION ONLY: publish the old-style GitHub release so installed copies learn the new source.

    Copies of InvoiceWarden up to 1.0.41 poll GitHub Releases for InvoiceWarden.exe
    (or, for <= 1.0.40, InvoiceM8.exe). This ONE release carries a build whose
    updater already points at sixthdaystudios.com, so after it they never
    look at GitHub again. Delete this function, gh(), LEGACY_EXE, GITHUB_REPO
    and the --github-transition flag once every install has moved over.
    """
    if gh("--version").returncode != 0:
        print("  GitHub CLI not found:  winget install GitHub.cli")
        return False
    if gh("auth", "status").returncode != 0:
        print("  Not logged in:  gh auth login")
        return False

    slug = gh("repo", "view", "--json", "nameWithOwner",
              "-q", ".nameWithOwner").stdout.strip()
    if slug.lower() != GITHUB_REPO.lower():
        print(f"  Remote is {slug} but the transition expects {GITHUB_REPO}.")
        return False

    tag = f"v{APP_VERSION}"
    exists = gh("release", "view", tag).returncode == 0
    print(f"\n  TRANSITION: {'Update' if exists else 'Publish'} GitHub release {tag} on {slug}"
          f"  (marked 'Latest' - installed copies update to it ONE LAST TIME)")
    print(f"    asset : {EXE}  ({exe_desc}) + legacy-named copy")
    if not assume_yes and not _ask("  Publish to GitHub now?", default=False):
        print("\n  GitHub release not published.\n")
        return True

    # The assets keep their old names: that is what the old updaters look for.
    shutil.copyfile(EXE, LEGACY_EXE)
    notes = (
        f"## What's new\n\n{changelog(APP_VERSION)}\n\n"
        "---\n\n"
        "Download `InvoiceWarden.exe` and run it - no install, no admin. It is "
        "unsigned, so Windows SmartScreen shows \"Windows protected your PC\": "
        "click **More info** then **Run anyway**. From this version on, updates "
        "come from sixthdaystudios.com."
    )
    if exists:
        print(f"  Release {tag} exists - updating asset + notes")
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
    return ok


def main() -> int:
    """Test, then interactively build and/or publish. Returns a shell exit code.

    Run with no arguments and answer two prompts: build a fresh exe?, then
    publish it to the update store? Overrides for scripting: ``--yes`` (yes to
    all), ``--build-only`` (build, never publish), ``--skip-build`` (publish
    the exe already in ``dist/``), ``--dry-run`` (sign and print, upload
    nothing) and, for the one-time move off GitHub, ``--github-transition``.
    """
    args = set(sys.argv[1:])
    known = {"--build-only", "--skip-build", "--yes", "--dry-run", "--github-transition"}
    unknown = args - known
    if unknown:
        # Fail loudly rather than let a typo'd flag fall through to a publish.
        print(f"  Unknown option(s): {' '.join(sorted(unknown))}")
        print("  Usage: release.py [--build-only | --skip-build] [--yes] "
              "[--dry-run] [--github-transition]")
        return 2
    build_only = "--build-only" in args
    skip_build = "--skip-build" in args
    assume_yes = "--yes" in args
    dry_run = "--dry-run" in args
    github_transition = "--github-transition" in args

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

    # The published file carries the version in its name.
    make_versioned()
    if build_only:
        print(f"\n  Done. {EXE} is ready to run.\n"
              f"  (settings + database live in %LOCALAPPDATA%\\InvoiceWarden)\n")
        return 0

    # --- question 2: publish it to the update store? ----------------
    # Publishing is outward-facing: every installed client updates to it.
    print(f"\n  Publish {VERSIONED_EXE.name} to the Sixth Day Studios update store"
          f"  (every install auto-updates to it)")
    print(f"    file  : {VERSIONED_EXE}  ({exe_desc})")
    print(f"    notes : {changelog(APP_VERSION).splitlines()[0][:72]}")
    if not assume_yes and not _ask("  Publish now?", default=False):
        print(f"\n  Not published. The build is in {VERSIONED_EXE} if you want it.\n")
        return 0
    if not publish_store(dry_run):
        print("  Publishing to the update store FAILED.")
        return 1

    # TRANSITION: the GitHub release comes last, only when asked for, and never on a dry run.
    if github_transition and not dry_run:
        return 0 if publish_github_transition(exe_desc, assume_yes) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
