"""Tests for the signed update-store updater and its publish tool.

Standard library only; no real network. A throwaway Ed25519 key signs the
manifests, and ``urllib.request.urlopen`` is stubbed for the download tests.

    python -m unittest discover -s tests
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from core import ed25519, updater   # noqa: E402
import release_upload               # noqa: E402

SECRET = hashlib.sha256(b"invoicewarden-test-key").digest()   # throwaway key, test only
PUB = ed25519.public_key(SECRET)
DATA = b"MZ fake exe bytes" * 100


def make_body(**over) -> dict:
    """A valid manifest body for DATA, with optional field overrides."""
    body = {"app": "invoicewarden", "version": "1.2.3", "published": "2026-01-01",
            "notes": ["One.", "Two."], "file": "InvoiceWarden-1.2.3.exe",
            "size": len(DATA), "sha256": hashlib.sha256(DATA).hexdigest()}
    body.update(over)
    return body


def sign_outer(body_text: str, secret: bytes = SECRET) -> str:
    """The `latest` response text for an exact body string."""
    sig = ed25519.sign(secret, body_text.encode("utf-8"))
    return json.dumps({"body": body_text, "sig": base64.urlsafe_b64encode(sig).decode()})


def signed(**over) -> str:
    """A signed `latest` response for a (possibly overridden) valid body."""
    return sign_outer(json.dumps(make_body(**over)))


class TestReadManifest(unittest.TestCase):
    """read_manifest accepts only genuine, well-formed manifests."""

    def test_valid_manifest_accepted(self) -> None:
        """A correctly signed, well-formed manifest is returned."""
        info = updater.read_manifest(signed(), PUB)
        self.assertEqual(info["version"], "1.2.3")

    def test_wrong_key_rejected(self) -> None:
        """A manifest signed by another key is refused."""
        other = hashlib.sha256(b"other").digest()
        with self.assertRaises(ValueError):
            updater.read_manifest(sign_outer(json.dumps(make_body()), other), PUB)

    def test_real_key_rejects_test_signature(self) -> None:
        """The built-in PUBLIC_KEY does not trust the throwaway key."""
        with self.assertRaises(ValueError):
            updater.read_manifest(signed())

    def test_tampered_body_rejected(self) -> None:
        """Changing the body after signing breaks the signature."""
        outer = json.loads(signed())
        outer["body"] = outer["body"].replace("1.2.3", "9.9.9")
        with self.assertRaises(ValueError):
            updater.read_manifest(json.dumps(outer), PUB)

    def test_malformed_outer_rejected(self) -> None:
        """Junk, missing fields and non-text bodies are 'malformed'."""
        for raw in ("not json", "{}", json.dumps({"body": 5, "sig": "AAAA"}),
                    json.dumps({"body": "x", "sig": "***"})):
            with self.assertRaises(ValueError):
                updater.read_manifest(raw, PUB)

    def test_wrong_app_rejected(self) -> None:
        """A genuinely signed manifest for another app is refused."""
        with self.assertRaises(ValueError):
            updater.read_manifest(signed(app="zeolite"), PUB)

    def test_bad_file_names_rejected(self) -> None:
        """Slashes, a leading dot and non-.exe names are refused."""
        for name in ("a/b.exe", "a\\b.exe", ".hidden.exe", "x.zip", "", None, 5):
            with self.assertRaises(ValueError, msg=repr(name)):
                updater.read_manifest(signed(file=name), PUB)

    def test_bad_sha256_or_size_rejected(self) -> None:
        """A non-int size or a fingerprint that is not 64 hex chars is refused."""
        for over in ({"sha256": "abc"}, {"sha256": "z" * 64}, {"sha256": None},
                     {"size": "100"}, {"size": 1.5}, {"size": True}, {"size": 0}):
            with self.assertRaises(ValueError, msg=repr(over)):
                updater.read_manifest(signed(**over), PUB)

    def test_bad_version_rejected(self) -> None:
        """A version that is not dotted digits (it names a local file) is refused."""
        for v in ("../../x", "1.2.3-beta", "", None):
            with self.assertRaises(ValueError, msg=repr(v)):
                updater.read_manifest(signed(version=v), PUB)


class TestFetchLatest(unittest.TestCase):
    """_fetch_latest turns a verified manifest into UpdateInfo; failures follow strict/silent rules."""

    @staticmethod
    def _resp(text: str):
        """A urlopen() stand-in returning ``text``."""
        class R(io.BytesIO):
            """Context-manager bytes buffer."""
            def __enter__(self): return self
            def __exit__(self, *a): return False
        return lambda *a, **k: R(text.encode("utf-8"))

    def test_info_built_from_manifest(self) -> None:
        """URL, size, sha and bullet-style notes come from the signed body."""
        with patch.object(updater.urllib.request, "urlopen", self._resp(signed())):
            info = updater._fetch_latest(public_key=PUB)
        self.assertEqual(info.url, updater.DOWNLOAD_URL + "InvoiceWarden-1.2.3.exe")
        self.assertEqual(info.sha256, hashlib.sha256(DATA).hexdigest())
        self.assertEqual(info.note_lines(), ["One.", "Two."])

    def test_bad_signature_silent_in_background_raises_in_manual(self) -> None:
        """Background: None. Manual: UpdateCheckError with a plain message."""
        bad = signed().replace("1.2.3", "9.9.9")
        with patch.object(updater.urllib.request, "urlopen", self._resp(bad)):
            self.assertIsNone(updater._fetch_latest(public_key=PUB))
            with self.assertRaises(updater.UpdateCheckError):
                updater._fetch_latest(strict=True, public_key=PUB)


class TestVersions(unittest.TestCase):
    """Version comparison behaviour."""

    def test_comparisons(self) -> None:
        """Numeric (not lexical) ordering; junk sorts oldest; 'v' prefix ok."""
        p = updater._parse_version
        self.assertGreater(p("1.0.42"), p("1.0.41"))
        self.assertGreater(p("1.0.10"), p("1.0.9"))
        self.assertEqual(p("v1.2.3"), (1, 2, 3))
        self.assertLess(p("junk"), p("0.0.1"))

    def test_is_newer_respects_current(self) -> None:
        """Only strictly newer versions count (state file stubbed out)."""
        info = updater.UpdateInfo("1.0.42", "u", 1, "")
        with patch.object(updater, "current_version", return_value="1.0.41"), \
                patch.object(updater, "_state", return_value={}):
            self.assertTrue(updater._is_newer(info))
        with patch.object(updater, "current_version", return_value="1.0.42"), \
                patch.object(updater, "_state", return_value={}):
            self.assertFalse(updater._is_newer(info))


class TestDownload(unittest.TestCase):
    """download() verifies size and SHA-256 before returning a path."""

    @staticmethod
    def _stub(data: bytes):
        """A urlopen() stand-in streaming ``data``."""
        class R(io.BytesIO):
            """Response with headers, usable as a context manager."""
            headers = {"Content-Length": str(len(data))}
            def __enter__(self): return self
            def __exit__(self, *a): return False
        return lambda *a, **k: R(data)

    def _run(self, data: bytes, **over):
        """Run download() against ``data`` in a temp updates dir; returns (result, dir)."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = pathlib.Path(tmp.name)
        fields = dict(version="1.2.3", url="http://x/y.exe", size=len(DATA),
                      notes="", sha256=hashlib.sha256(DATA).hexdigest())
        fields.update(over)
        with patch.object(updater, "_UPDATE_DIR", d), \
                patch.object(updater.urllib.request, "urlopen", self._stub(data)):
            return updater.download(updater.UpdateInfo(**fields)), d

    def test_good_download_kept(self) -> None:
        """Matching bytes produce a file named InvoiceWarden-<version>.exe."""
        path, _ = self._run(DATA)
        self.assertEqual(path.read_bytes(), DATA)
        self.assertEqual(path.name, "InvoiceWarden-1.2.3.exe")

    def test_sha_mismatch_aborts_and_deletes(self) -> None:
        """Right size, wrong bytes: None and nothing left behind."""
        path, d = self._run(b"X" * len(DATA))
        self.assertIsNone(path)
        self.assertEqual(list(d.glob("*.exe*")), [])

    def test_size_mismatch_aborts_and_deletes(self) -> None:
        """Short and over-long downloads are both refused and cleaned up."""
        for data in (DATA[:-1], DATA + b"extra"):
            path, d = self._run(data)
            self.assertIsNone(path)
            self.assertEqual(list(d.glob("*.exe*")), [])

    def test_missing_fingerprint_refused(self) -> None:
        """No signed sha256 means no install (fail closed)."""
        path, _ = self._run(DATA, sha256="")
        self.assertIsNone(path)


class TestApplyTarget(unittest.TestCase):
    """The swap writes over the running exe's own path whatever the download is called."""

    def test_script_targets_running_exe(self) -> None:
        """apply() puts running_exe() as the copy destination."""
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            fake_exe = d / "Inv.exe"
            with patch.object(updater, "_UPDATE_DIR", d), \
                    patch.object(updater, "running_exe", return_value=fake_exe), \
                    patch.object(updater.subprocess, "Popen") as popen:
                self.assertTrue(updater.apply(d / "InvoiceWarden-1.2.3.exe", "1.2.3"))
            script = (d / "apply_update.cmd").read_text(encoding="utf-8")
            self.assertIn(f'"{d / "InvoiceWarden-1.2.3.exe"}" "{fake_exe}"', script)
            popen.assert_called_once()


class TestReleaseUpload(unittest.TestCase):
    """The publish tool's changelog parsing and manifest building (no network)."""

    CHANGELOG = (
        "# Changelog\n\n## 1.0.42\n- **Bold.** First thing\n  wrapped on two lines.\n"
        "- Second `thing`.\n\n## 1.0.41\n- Old.\n")

    def test_notes_parsing(self) -> None:
        """Bullets are split, wrapped lines joined, markdown stripped, other versions ignored."""
        self.assertEqual(release_upload.parse_changelog_section(self.CHANGELOG, "1.0.42"),
                         ["Bold. First thing wrapped on two lines.", "Second thing."])
        self.assertEqual(release_upload.parse_changelog_section(self.CHANGELOG, "9.9.9"), [])

    def test_real_changelog_has_current_version(self) -> None:
        """CHANGELOG.md has bullets for version.py's version (the release gate)."""
        from version import APP_VERSION
        self.assertTrue(release_upload.release_notes(APP_VERSION))

    def test_manifest_built_and_signed(self) -> None:
        """build_manifest output verifies and describes the file (key check patched to the test key)."""
        with tempfile.TemporaryDirectory() as tmp:
            exe = pathlib.Path(tmp) / "InvoiceWarden-1.2.3.exe"
            exe.write_bytes(DATA)
            with patch.object(release_upload.updater, "PUBLIC_KEY", PUB):
                text = release_upload.build_manifest(exe, "1.2.3", SECRET, ["Hi."])
                info = updater.read_manifest(text, PUB)
        self.assertEqual((info["file"], info["size"], info["sha256"], info["notes"]),
                         ("InvoiceWarden-1.2.3.exe", len(DATA), hashlib.sha256(DATA).hexdigest(), ["Hi."]))

    def test_wrong_signing_key_stops_the_upload(self) -> None:
        """A key that is not the app's trusted key makes build_manifest exit."""
        with tempfile.TemporaryDirectory() as tmp:
            exe = pathlib.Path(tmp) / "InvoiceWarden-1.2.3.exe"
            exe.write_bytes(DATA)
            with self.assertRaises(SystemExit):
                release_upload.build_manifest(exe, "1.2.3", SECRET, ["Hi."])


if __name__ == "__main__":
    unittest.main()
