"""Tests for the one-time InvoiceM8 -> InvoiceWarden data carry-over."""
import tempfile
import unittest
from pathlib import Path

from core.migrate import migrate_data_dir


class MigrateDataDirTests(unittest.TestCase):
    """Copying the old data folder across without touching the original."""

    def setUp(self):
        """Build an old-style data folder in a temp dir."""
        self.root = Path(tempfile.mkdtemp())
        self.old, self.new = self.root / "InvoiceM8", self.root / "InvoiceWarden"
        (self.old / "logs").mkdir(parents=True)
        (self.old / "updates").mkdir()
        (self.old / "invoicem8.sqlite3").write_bytes(b"db")
        (self.old / "invoicem8.sqlite3-wal").write_bytes(b"wal")
        (self.old / ".masterkey").write_bytes(b"key")
        (self.old / "logs" / "a.log").write_text("x")
        (self.old / "updates" / "big.exe").write_bytes(b"exe")

    def test_copies_and_renames_db(self):
        """DB (and sidecars) are renamed; other files copied; updates skipped."""
        self.assertTrue(migrate_data_dir(self.old, self.new,
                                         "invoicem8.sqlite3", "invoicewarden.sqlite3"))
        self.assertEqual((self.new / "invoicewarden.sqlite3").read_bytes(), b"db")
        self.assertEqual((self.new / "invoicewarden.sqlite3-wal").read_bytes(), b"wal")
        self.assertEqual((self.new / ".masterkey").read_bytes(), b"key")
        self.assertTrue((self.new / "logs" / "a.log").exists())
        self.assertFalse((self.new / "updates").exists())

    def test_original_left_intact_and_second_run_is_noop(self):
        """Old folder is untouched; running again does nothing."""
        migrate_data_dir(self.old, self.new, "invoicem8.sqlite3", "invoicewarden.sqlite3")
        self.assertTrue((self.old / "invoicem8.sqlite3").exists())
        (self.new / "invoicewarden.sqlite3").write_bytes(b"changed")
        self.assertFalse(migrate_data_dir(self.old, self.new,
                                          "invoicem8.sqlite3", "invoicewarden.sqlite3"))
        self.assertEqual((self.new / "invoicewarden.sqlite3").read_bytes(), b"changed")

    def test_no_old_folder(self):
        """A fresh install has nothing to migrate."""
        self.assertFalse(migrate_data_dir(self.root / "nope", self.new, "a", "b"))


if __name__ == "__main__":
    unittest.main()
