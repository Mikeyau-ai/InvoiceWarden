"""Tests for the Activity page's wording and the activity counter.

The page's text helpers are pure functions, so they're tested without opening a window.
Run with ``python -m unittest discover -s tests``.
"""
from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from core.database import Database                       # noqa: E402
from gui.activity_page import plain_line, problem_text, status_text  # noqa: E402


def _row(**kw):
    """An activity/error row as the database returns it (dict-like)."""
    base = dict(action="", customer_name="", invoice_ref="", platform="", message="",
                stage="", error="", filename="", ts="")
    base.update(kw)
    return base


class StatusTextTests(unittest.TestCase):
    """The status card's title and subtitle."""

    def test_off(self):
        """Off says so and says what to do."""
        title, sub = status_text(False, 1, None, 5, datetime.now(timezone.utc))
        self.assertEqual(title, "Off")
        self.assertIn("Turn it on", sub)

    def test_on_counts_mailboxes_and_times(self):
        """On shows the mailbox count, when it last checked and when it checks next."""
        now = datetime.now(timezone.utc)
        title, sub = status_text(True, 2, now - timedelta(minutes=2), 5, now)
        self.assertEqual(title, "On: watching 2 mailboxes")
        self.assertIn("Checked 2 min ago", sub)
        self.assertIn("next check in 3 min", sub)

    def test_on_before_first_check(self):
        """Just switched on: no 'checked' time yet."""
        title, sub = status_text(True, 1, None, 5, datetime.now(timezone.utc))
        self.assertEqual(title, "On: watching 1 mailbox")
        self.assertIn("Starting up", sub)


class PlainLineTests(unittest.TestCase):
    """Which activity rows show in the plain list, and how they read."""

    def test_uploaded_reads_as_a_sentence(self):
        """A filed invoice names supplier, invoice, destination and detail."""
        text, tag = plain_line(_row(action="uploaded", customer_name="Tradelink",
                                    invoice_ref="55102", platform="ServiceM8",
                                    message="[invoice] Attached to job 4471"))
        self.assertEqual(tag, "ok")
        self.assertIn("Tradelink · invoice 55102: filed to ServiceM8", text)
        self.assertIn("Attached to job 4471", text)
        self.assertNotIn("[invoice]", text)

    def test_routine_steps_are_hidden(self):
        """Polls, parsing and housekeeping stay in the full log only."""
        for action in ("poll", "parsed", "found", "cleanup", "watcher", "skipped"):
            self.assertIsNone(plain_line(_row(action=action)), action)

    def test_held_points_at_needs_attention(self):
        """An invoice held back says where to look."""
        text, tag = plain_line(_row(action="held", customer_name="Reece"))
        self.assertEqual(tag, "warn")
        self.assertIn("Needs attention", text)


class ProblemTextTests(unittest.TestCase):
    """Error-log rows turned into a headline and what to do."""

    def test_upload_failure_strips_platform_prefix(self):
        """'[ServiceM8] reason' becomes 'to ServiceM8' plus the reason, punctuated."""
        head, todo = problem_text(_row(stage="upload", customer_name="Tradelink",
                                       invoice_ref="55190",
                                       error="[ServiceM8] Job 9999 not found"))
        self.assertEqual(head, "Couldn't file Tradelink · invoice 55190 to ServiceM8")
        self.assertEqual(todo, "Job 9999 not found. Press Retry once it's fixed.")

    def test_unknown_supplier(self):
        """A held invoice from an unknown supplier says to add them."""
        head, todo = problem_text(_row(stage="supplier", customer_name="Reece Plumbing"))
        self.assertIn("New supplier not on your list: Reece Plumbing", head)
        self.assertIn("Suppliers", todo)


class CountActivityTests(unittest.TestCase):
    """Database.count_activity, which feeds the 'filed today/this week' tiles."""

    def test_counts_only_matching_actions_since_a_time(self):
        """Older rows and other actions aren't counted."""
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(pathlib.Path(tmp) / "t.sqlite3")
            now = datetime.now(timezone.utc)
            iso = lambda d: d.isoformat(timespec="seconds")
            db.add_activity(ts=iso(now - timedelta(days=3)), action="uploaded")
            db.add_activity(ts=iso(now - timedelta(minutes=5)), action="uploaded")
            db.add_activity(ts=iso(now - timedelta(minutes=4)), action="credit linked")
            db.add_activity(ts=iso(now - timedelta(minutes=3)), action="poll")
            since = iso(now - timedelta(hours=1))
            self.assertEqual(db.count_activity(("uploaded", "credit linked"), since), 2)
            db.close()


if __name__ == "__main__":
    unittest.main()
