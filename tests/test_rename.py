"""Tests for the InvoiceM8 -> InvoiceWarden rename: the data move and the updater's asset pick.

Everything works in temp folders; the Windows startup entry is never touched.
"""
from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import config                                   # noqa: E402
from core import migrate, updater               # noqa: E402


class MigrateTests(unittest.TestCase):
    """core.migrate.migrate_legacy."""

    def setUp(self):
        """An old-style data folder with a database, its WAL file and an attachment."""
        self.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.tmp.name)
        self.old, self.new = root / "InvoiceM8", root / "InvoiceWarden"
        (self.old / "attachments" / "abc").mkdir(parents=True)
        (self.old / "updates").mkdir()
        (self.old / "invoicem8.sqlite3").write_bytes(b"db")
        (self.old / "invoicem8.sqlite3-wal").write_bytes(b"wal")
        (self.old / "attachments" / "abc" / "inv.pdf").write_bytes(b"pdf")
        (self.old / "updates" / "InvoiceM8-1.0.40.exe").write_bytes(b"exe")
        (self.old / ".masterkey").write_bytes(b"key")

    def tearDown(self):
        """Remove the temp folders."""
        self.tmp.cleanup()

    def _run(self):
        """Run the migration with the registry step stubbed out."""
        with patch.object(migrate, "_rename_startup_entry") as reg:
            note = migrate.migrate_legacy(self.old, self.new)
        return note, reg

    def test_copies_everything_and_renames_the_database(self):
        """Data comes across under the new database name; the old folder stays as a backup."""
        note, reg = self._run()
        self.assertIn("kept as a backup", note)
        self.assertEqual((self.new / config.DB_PATH.name).read_bytes(), b"db")
        self.assertEqual((self.new / (config.DB_PATH.name + "-wal")).read_bytes(), b"wal")
        self.assertFalse((self.new / "invoicem8.sqlite3").exists())
        self.assertEqual((self.new / "attachments" / "abc" / "inv.pdf").read_bytes(), b"pdf")
        self.assertEqual((self.new / ".masterkey").read_bytes(), b"key")
        self.assertFalse((self.new / "updates").exists())          # throwaway downloads skipped
        self.assertTrue((self.old / "invoicem8.sqlite3").exists())  # backup untouched
        reg.assert_called_once()

    def test_runs_only_once(self):
        """Once the new folder exists, nothing is copied again (later edits are safe)."""
        self._run()
        (self.new / config.DB_PATH.name).write_bytes(b"newer")
        note, reg = self._run()
        self.assertEqual(note, "")
        self.assertEqual((self.new / config.DB_PATH.name).read_bytes(), b"newer")
        reg.assert_not_called()

    def test_fresh_install_does_nothing(self):
        """No old folder: nothing to move."""
        missing = pathlib.Path(self.tmp.name) / "nothing-here"
        with patch.object(migrate, "_rename_startup_entry") as reg:
            self.assertEqual(migrate.migrate_legacy(missing, self.new), "")
        self.assertFalse(self.new.exists())
        reg.assert_not_called()

    def test_saved_keys_stay_readable(self):
        """The master key keeps its old Credential Manager name, or every saved key is lost."""
        self.assertEqual(config.KEYRING_SERVICE, "InvoiceM8-master-key")


class UpdaterAssetTests(unittest.TestCase):
    """The updater accepts the new exe name, falling back to the old one."""

    def _latest(self, names):
        """Run _fetch_latest against a fake GitHub reply with these asset names."""
        reply = {"tag_name": "v9.9.9", "body": "",
                 "assets": [{"name": n, "browser_download_url": f"https://x/{n}", "size": 1}
                            for n in names]}
        with patch.object(updater, "_get_json", return_value=reply, create=True), \
                patch.object(updater.urllib.request, "urlopen") as op:
            op.return_value.__enter__.return_value.read.return_value = \
                __import__("json").dumps(reply).encode()
            return updater._fetch_latest()

    def test_prefers_the_new_name(self):
        """With both attached, the new exe is chosen."""
        info = self._latest(["InvoiceM8.exe", "InvoiceWarden.exe"])
        self.assertTrue(info.url.endswith("InvoiceWarden.exe"))

    def test_old_name_still_works(self):
        """An older release with only the old name still counts as an update."""
        info = self._latest(["InvoiceM8.exe"])
        self.assertTrue(info.url.endswith("InvoiceM8.exe"))


if __name__ == "__main__":
    unittest.main()
