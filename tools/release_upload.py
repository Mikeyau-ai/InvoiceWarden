r"""Upload a built InvoiceWarden exe as the latest update (served by the website's /api/updates/invoicewarden).

    python tools/release_upload.py --installer dist\InvoiceWarden-X.Y.Z.exe --env-file Y:\Guardian\.env [--dry-run]

The env file holds SDS_UPDATE_SIGNING_KEY (the Ed25519 secret, base64url) and CLOUDFLARE_API_TOKEN (with R2 edit
access); neither is ever printed or written anywhere else. Steps, stopping at the first problem:
 1. version.py must match the installer's name; the release notes are that version's bullets in CHANGELOG.md.
 2. latest.json = {"body": <app, version, published, notes, file, size, sha256>, "sig": Ed25519(body)}; the
    signature must check against the public key built into core/updater.py (so a wrong key can't ship).
 3. Upload the installer, then latest.json (in that order: an app never sees a release whose file isn't there).
 4. Read latest back from the website and check it the same way the app does.
--dry-run does 1-2 and prints the body.
"""
import argparse
import base64
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import ed25519, updater   # noqa: E402  (needs ROOT on the path first)

ACCOUNT_ID = "257d47d170c46772026c2f5ae2562514"   # Cloudflare account that owns the sds-releases bucket
BUCKET = "sds-releases"


def read_env(path):
    """KEY=value lines of an env file (quotes stripped, comments skipped)."""
    out = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def release_notes(version, changelog=ROOT / "CHANGELOG.md"):
    """The bullets of CHANGELOG.md's '## <version>' section, one string each (markdown emphasis stripped).

    A bullet's wrapped continuation lines (indented, no leading dash) are joined back onto it.
    Exits if the section is missing or has no bullets.
    """
    notes = parse_changelog_section(Path(changelog).read_text(encoding="utf-8"), version)
    if not notes:
        sys.exit(f"CHANGELOG.md has no '## {version}' section with bullets: write the release notes first.")
    return notes


def parse_changelog_section(text, version):
    """Pure parser behind release_notes: the bullet strings of one version's section ([] if none)."""
    out, capturing = [], False
    for raw in text.splitlines():
        if raw.startswith("## "):
            if capturing:
                break                                     # next version's heading: our section is over
            capturing = raw[3:].strip().lstrip("vV") == version
            continue
        line = raw.strip()
        if not capturing or not line or line.startswith("#"):
            continue
        clean = line.replace("**", "").replace("`", "").strip()
        if line.startswith(("- ", "* ")):
            out.append(clean[1:].strip())
        elif out:                                         # wrapped continuation of the previous bullet
            out[-1] = f"{out[-1]} {clean}"
    return out


def build_manifest(installer, version, secret, notes, today=None):
    """The signed latest.json text for this installer; checked against the app's own public key."""
    data = Path(installer).read_bytes()
    body = json.dumps({"app": updater.APP, "version": version,
                       "published": (today or datetime.date.today()).isoformat(), "notes": notes,
                       "file": Path(installer).name, "size": len(data),
                       "sha256": hashlib.sha256(data).hexdigest()}, ensure_ascii=False)
    sig = ed25519.sign(secret, body.encode("utf-8"))
    if not ed25519.verify(updater.PUBLIC_KEY, body.encode("utf-8"), sig):
        sys.exit("This signing key is not the one InvoiceWarden trusts (core/updater.py PUBLIC_KEY). Nothing uploaded.")
    return json.dumps({"body": body, "sig": base64.urlsafe_b64encode(sig).decode()})


def r2_put(bucket, key, path, content_type, env):
    """Upload one file with wrangler (exits on failure)."""
    cmd = ["npx", "--yes", "wrangler@4", "r2", "object", "put", f"{bucket}/{key}", "--file", str(path),
           "--content-type", content_type, "--remote"]
    # creationflags=0 on purpose: this tool runs in a console and wrangler's progress should show there.
    rc = subprocess.call(cmd, env=env, shell=os.name == "nt", creationflags=0)
    if rc != 0:
        sys.exit(f"Uploading {key} failed (wrangler exited {rc}).")


def main(argv=None):
    """Parse arguments and run the steps above."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--installer", required=True)
    ap.add_argument("--env-file", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    from version import APP_VERSION as version
    installer = Path(a.installer)
    if installer.name != f"InvoiceWarden-{version}.exe" or not installer.is_file():
        sys.exit(f"Expected the {version} build (InvoiceWarden-{version}.exe), got {installer}.")
    secrets = read_env(a.env_file)
    if not secrets.get("SDS_UPDATE_SIGNING_KEY") or not secrets.get("CLOUDFLARE_API_TOKEN"):
        sys.exit("The env file needs SDS_UPDATE_SIGNING_KEY and CLOUDFLARE_API_TOKEN.")
    secret = base64.urlsafe_b64decode(secrets["SDS_UPDATE_SIGNING_KEY"])
    manifest = build_manifest(installer, version, secret, release_notes(version))
    body = json.loads(json.loads(manifest)["body"])
    print(f"Release {version}: {body['file']} ({body['size']:,} bytes, SHA-256 {body['sha256']})")
    for n in body["notes"]:
        print("  - " + n)
    if a.dry_run:
        print("Dry run: nothing uploaded.")
        return

    env = {**os.environ, "CLOUDFLARE_API_TOKEN": secrets["CLOUDFLARE_API_TOKEN"],
           "CLOUDFLARE_ACCOUNT_ID": ACCOUNT_ID, "WRANGLER_SEND_METRICS": "false"}
    r2_put(BUCKET, f"{updater.APP}/{installer.name}", installer, "application/octet-stream", env)
    with tempfile.TemporaryDirectory() as tmp:
        latest = Path(tmp) / "latest.json"
        latest.write_text(manifest, encoding="utf-8")
        r2_put(BUCKET, f"{updater.APP}/latest.json", latest, "application/json", env)

    # Read back through the website and verify exactly as the app would.
    try:
        req = urllib.request.Request(updater.LATEST_URL, headers={"User-Agent": "InvoiceWarden-Release"})
        with urllib.request.urlopen(req, timeout=30) as r:
            got = updater.read_manifest(r.read().decode("utf-8"))
    except Exception as e:                                # network, HTTP or verification failure
        got, why = None, type(e).__name__
    else:
        why = "a different version"
    if not got or got["version"] != version:
        sys.exit(f"Uploaded, but the website doesn't offer {version} yet ({why}; "
                 "its cache holds latest for up to 5 minutes; the Worker needs the RELEASES binding).")
    print(f"Live: {updater.LATEST_URL} now offers {version}.")


if __name__ == "__main__":
    main()
