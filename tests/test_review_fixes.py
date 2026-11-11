"""Regression tests for the 2026-11-11 code review (release 1.0.45).

Everything is faked: no network, no mailbox, no real credentials or user data.
"""
from __future__ import annotations

import json
import pathlib
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import core.router as router_mod                                    # noqa: E402
from core.parser_ai import (AITransientError, InvoiceParser,        # noqa: E402
                            ParseResult)
from core.watcher import Watcher                                    # noqa: E402
from integrations import email_outlook                              # noqa: E402
from integrations.email_outlook import (EmailMessage, GraphBackend, # noqa: E402
                                        cache_bucket, unique_name)
from integrations.provider_base import (Provider, UploadContext,    # noqa: E402
                                        UploadResult)
from test_audit_fixes import FakeResp, _ctx                         # noqa: E402
from test_core import FakeService, TempDbCase                       # noqa: E402

NOW = datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    """Graph-style timestamp."""
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class FailingService(FakeService):
    """A service system whose upload always fails."""

    def upload_invoice(self, ctx) -> UploadResult:
        """Refuse every upload."""
        return UploadResult(False, self.label, "server said no")


# --------------------------------------------------------------------------
class TestAttachmentCache(unittest.TestCase):
    """Each email gets its own cache folder, each attachment its own file."""

    def test_ids_sharing_a_prefix_get_different_folders(self) -> None:
        """Outlook/Graph ids differ only at the end; folders must still differ."""
        prefix = "AAMkAGVmMDEzMTM4LTZmYWUtNDdkNC1hMDZiLTU1OGY5OTZhYmY4OABGAAAA"
        self.assertNotEqual(cache_bucket(prefix + "AAAYbvZDAAA="),
                            cache_bucket(prefix + "AAAYbvZEAAA="))

    def test_same_file_name_twice_on_one_email(self) -> None:
        """The second 'Invoice.pdf' on an email does not overwrite the first."""
        used: set[str] = set()
        self.assertNotEqual(unique_name("Invoice.pdf", used),
                            unique_name("invoice.pdf", used))


# --------------------------------------------------------------------------
class GraphCase(unittest.TestCase):
    """Drives GraphBackend.fetch against a scripted fake session."""

    def run_fetch(self, messages, attachments=None, since=None, seen=frozenset(),
                  allowed=frozenset({"pdf"})):
        """Fetch with canned list/attachment responses; returns (results, calls)."""
        import tempfile
        tmp = pathlib.Path(tempfile.mkdtemp())
        calls: list[tuple[str, dict]] = []

        def get(url, **kw):
            """Answer the message list, attachment list and file downloads."""
            calls.append((url, kw.get("params") or {}))
            if url.endswith("/$value"):
                resp = FakeResp(200)
                resp.iter_content = lambda _n: [b"%PDF bytes"]
                return resp
            if url.endswith("/attachments"):
                return FakeResp(200, {"value": attachments or []})
            return FakeResp(200, {"value": messages})

        sess = MagicMock()
        sess.get.side_effect = get
        g = GraphBackend(MagicMock())
        with patch.object(GraphBackend, "_token", lambda s: "t"), \
                patch.object(GraphBackend, "_http", lambda s: sess), \
                patch.object(email_outlook, "ATTACHMENT_CACHE", tmp):
            results = g.fetch(since=since, unread_only=False,
                              allowed_ext=set(allowed), seen_ids=seen)
        return results, calls, g

    @staticmethod
    def msg(mid: str, received: datetime) -> dict:
        """One Graph message with an attachment."""
        return {"id": mid, "subject": "s", "hasAttachments": True, "isRead": False,
                "receivedDateTime": _iso(received),
                "from": {"emailAddress": {"address": "a@b.com"}},
                "body": {"content": "hi"}}


class TestGraphFetch(GraphCase):
    """Graph: bounded first scan, and no bytes for mail already handled."""

    def test_first_scan_is_limited_to_the_lookback_window(self) -> None:
        """With nothing processed yet, year-old mail is not ingested."""
        old = self.msg("old", NOW - timedelta(days=400))
        new = self.msg("new", NOW - timedelta(days=1))
        pdf = [{"id": "a1", "name": "inv.pdf"}]
        results, calls, _g = self.run_fetch([old, new], pdf, since=None)
        self.assertEqual([m.message_id for m in results], ["new"])
        self.assertIn("receivedDateTime ge", calls[0][1].get("$filter", ""))

    def test_attachments_are_not_expanded_into_the_list(self) -> None:
        """The list call carries no attachment bytes."""
        _r, calls, _g = self.run_fetch([], since=NOW - timedelta(days=1))
        self.assertTrue(all("$expand" not in params for _u, params in calls))

    def test_seen_messages_cost_no_attachment_calls(self) -> None:
        """An email already processed is skipped before anything is downloaded."""
        m = self.msg("seen1", NOW - timedelta(hours=1))
        results, calls, _g = self.run_fetch([m], [{"id": "a1", "name": "inv.pdf"}],
                                            since=NOW - timedelta(days=1),
                                            seen={"seen1"})
        self.assertEqual(results, [])
        self.assertFalse([u for u, _p in calls if "/attachments" in u])

    def test_only_wanted_file_types_are_downloaded(self) -> None:
        """A PNG logo is listed but never fetched; the PDF is."""
        m = self.msg("m1", NOW - timedelta(hours=1))
        atts = [{"id": "logo", "name": "logo.png"}, {"id": "inv", "name": "inv.pdf"}]
        results, calls, _g = self.run_fetch([m], atts, since=NOW - timedelta(days=1))
        downloads = [u for u, _p in calls if u.endswith("/$value")]
        self.assertEqual(len(downloads), 1)
        self.assertIn("/inv/", downloads[0])
        self.assertEqual([p.name for p in results[0].attachments], ["inv.pdf"])

    def test_email_with_nothing_wanted_is_reported_as_skipped(self) -> None:
        """So the watcher can avoid looking at it again every poll."""
        m = self.msg("m1", NOW - timedelta(hours=1))
        results, _c, g = self.run_fetch([m], [{"id": "logo", "name": "logo.png"}],
                                        since=NOW - timedelta(days=1))
        self.assertEqual(results, [])
        self.assertEqual(g.skipped_ids, ["m1"])

    def test_named_sub_folder_is_resolved_to_its_id(self) -> None:
        """A display name like 'Invoices' is looked up, not put in the URL."""
        sess = MagicMock()
        sess.get.return_value = FakeResp(
            200, {"value": [{"id": "FOLDER-ID", "displayName": "Invoices"}]})
        g = GraphBackend(MagicMock(), folder="invoices")
        self.assertEqual(g._folder_ref(sess, "https://x/me", {}), "FOLDER-ID")
        self.assertEqual(GraphBackend(MagicMock(), folder="Sent Items")
                         ._folder_ref(sess, "https://x/me", {}), "sentitems")


# --------------------------------------------------------------------------
class TestImapTimeout(unittest.TestCase):
    """A silent IMAP server must not hang the watcher forever."""

    def test_connection_has_a_timeout(self) -> None:
        """IMAP4_SSL is opened with a finite timeout."""
        from integrations.email_imap import ImapBackend
        captured = {}

        def fake_ssl(host, port, **kw):
            """Record how the connection was opened, then refuse."""
            captured.update(kw)
            raise OSError("stop")

        with patch("imaplib.IMAP4_SSL", fake_ssl):
            with self.assertRaises(RuntimeError):
                ImapBackend("h", 993, "u", "p")._connect()
        self.assertTrue(captured.get("timeout"))


# --------------------------------------------------------------------------
class TestRetry(TempDbCase):
    """Error Log retries keep the document's details and do not multiply."""

    def _fail_once(self, parsed: ParseResult) -> dict:
        """Route with a failing service; return the one error row."""
        self.db.upsert_customer({"name": "Acme", "servicem8_enabled": True,
                                 "file_types": ["pdf"]})
        r = router_mod.Router(self.db, self.settings, emit=self.emit)
        with patch.object(router_mod, "build_service_provider",
                          lambda s: FailingService(s)):
            r.route(parsed, [self.make_pdf()], "subj", "a@acme.com")
        rows = self.db.list_errors()
        self.assertEqual(len(rows), 1)
        return rows[0]

    def test_payload_keeps_doc_type_and_candidates(self) -> None:
        """A failed credit note is still a credit note when retried."""
        row = self._fail_once(ParseResult(customer_name="Acme", job_number="10160",
                                          invoice_ref="CN-1", doc_type="credit",
                                          job_candidates=["10160", "INV-9"]))
        payload = json.loads(row["payload"])
        self.assertEqual(payload["doc_type"], "credit")
        self.assertEqual(payload["job_candidates"], ["10160", "INV-9"])

    def test_failed_retry_updates_the_same_row(self) -> None:
        """Retrying and failing again leaves one card, not two."""
        row = self._fail_once(ParseResult(customer_name="Acme", job_number="10160",
                                          invoice_ref="INV-1"))
        r = router_mod.Router(self.db, self.settings, emit=self.emit)
        with patch.object(router_mod, "build_service_provider",
                          lambda s: FailingService(s)):
            self.assertFalse(r.retry_from_payload(row["payload"], error_id=row["id"]))
        self.assertEqual(len(self.db.list_errors()), 1)

    def test_provider_is_reused_between_documents(self) -> None:
        """One provider instance serves every email (keeps its token/session)."""
        self.db.upsert_customer({"name": "Acme", "servicem8_enabled": True,
                                 "file_types": ["pdf"]})
        customer = self.db.find_customer_by_name("Acme")
        r = router_mod.Router(self.db, self.settings, emit=self.emit)
        with patch.object(router_mod, "build_service_provider",
                          lambda s: FakeService(s)):
            first = r._targets_for(customer)[0]
            self.assertIs(r._targets_for(customer)[0], first)
            r.reset_providers()
            self.assertIsNot(r._targets_for(customer)[0], first)


# --------------------------------------------------------------------------
class TestSupplierScopedBills(TempDbCase):
    """A bill found by number must belong to the same supplier."""

    def test_xero_does_not_attach_to_another_suppliers_bill(self) -> None:
        """Same invoice number, different supplier: draft a new bill instead."""
        from integrations.accounting.xero import XeroProvider
        p = XeroProvider(self.settings)
        p._session = MagicMock()
        p._session.get.return_value = FakeResp(200, {"Invoices": [
            {"InvoiceID": "THEIRS", "Contact": {"ContactID": "c9", "Name": "Other Pty Ltd"}}]})
        p._session.post.return_value = FakeResp(200, {"Invoices": [{"InvoiceID": "OURS"}]})
        p._session.put.return_value = FakeResp(200)
        with patch.object(XeroProvider, "_headers", lambda s: {}):
            res = p.upload_invoice(_ctx(self.tmp))
        self.assertTrue(res.ok)
        self.assertEqual(res.remote_id, "OURS")

    def test_xero_matches_same_supplier_written_differently(self) -> None:
        """'ACME PTY. LIMITED' in Xero is still Acme's bill."""
        from integrations.accounting.xero import XeroProvider
        p = XeroProvider(self.settings)
        p._session = MagicMock()
        p._session.get.return_value = FakeResp(200, {"Invoices": [
            {"InvoiceID": "MINE", "Contact": {"ContactID": "c1", "Name": "ACME PTY. LIMITED"}}]})
        p._session.put.return_value = FakeResp(200)
        with patch.object(XeroProvider, "_headers", lambda s: {}):
            res = p.upload_invoice(_ctx(self.tmp))
        self.assertEqual(res.remote_id, "MINE")
        p._session.post.assert_not_called()

    def test_myob_does_not_attach_to_another_suppliers_bill(self) -> None:
        """Same guard for MYOB."""
        from integrations.accounting.myob import MyobProvider
        p = MyobProvider(self.settings)
        p._session = MagicMock()
        p._session.get.return_value = FakeResp(200, {"Items": [
            {"UID": "THEIRS", "Supplier": {"UID": "s9", "Name": "Other Pty Ltd"}}]})
        p._session.post.return_value = FakeResp(
            200, headers={"Location": "https://api/Purchase/Bill/Item/OURS"})
        with patch.object(MyobProvider, "_headers", lambda s: {}):
            res = p.upload_invoice(_ctx(self.tmp))
        self.assertEqual(res.remote_id, "OURS")


# --------------------------------------------------------------------------
class TestServiceM8LookupOrder(TempDbCase):
    """The likeliest field is tried for every candidate before the rare ones."""

    def test_candidate_found_by_job_number_in_few_calls(self) -> None:
        """Third candidate is the job: 3 lookups, not 9."""
        from integrations.service.servicem8 import ServiceM8Provider
        p = ServiceM8Provider(self.settings)
        seen: list[tuple[str, str]] = []

        def lookup(field, value):
            """Only generated_job_id 333 exists."""
            seen.append((field, value))
            return "uuid" if (field, value) == ("generated_job_id", "333") else None

        p._lookup = lookup
        ctx = _ctx(self.tmp, job="")
        ctx.job_candidates = ["111", "222", "333"]
        self.assertEqual(p._find_job_uuid(ctx), ("uuid", "333"))
        self.assertEqual(len(seen), 3)


# --------------------------------------------------------------------------
class TestSupplierNameMatching(TempDbCase):
    """The same business written differently is one supplier."""

    def test_legal_suffix_and_punctuation_are_ignored(self) -> None:
        """'ACME PTY. LIMITED' finds 'Acme Pty Ltd'."""
        cid = self.db.upsert_customer({"name": "Acme Pty Ltd"})
        found = self.db.find_customer_by_name("ACME PTY. LIMITED")
        self.assertIsNotNone(found)
        self.assertEqual(found["id"], cid)

    def test_different_businesses_stay_separate(self) -> None:
        """'Acme Plumbing' is not 'Acme Electrical'."""
        self.db.upsert_customer({"name": "Acme Plumbing Pty Ltd"})
        self.assertIsNone(self.db.find_customer_by_name("Acme Electrical Pty Ltd"))


# --------------------------------------------------------------------------
class TestAiOutage(TempDbCase):
    """A temporary AI failure is retried, not papered over with a guess."""

    def _parse(self, exc: Exception, allow_fallback: bool) -> ParseResult:
        """Parse with the AI call raising ``exc``."""
        self.settings.set("ai.provider", "openai")
        self.settings.set("ai.openai_api_key", "k")
        with patch("core.parser_ai._call_provider", MagicMock(side_effect=exc)):
            return InvoiceParser(self.settings).parse(
                "s", "b", [], sender="a@x.com", allow_fallback=allow_fallback)

    def test_timeout_raises_when_fallback_not_allowed(self) -> None:
        """The watcher gets an exception it can retry on."""
        with self.assertRaises(AITransientError):
            self._parse(requests.Timeout("slow"), allow_fallback=False)

    def test_timeout_falls_back_on_the_last_attempt(self) -> None:
        """...and the regex reader still takes over when allowed."""
        res = self._parse(requests.Timeout("slow"), allow_fallback=True)
        self.assertTrue(res.ai_error)

    def test_permanent_failure_never_raises(self) -> None:
        """A bad key will not fix itself: fall back straight away."""
        res = self._parse(RuntimeError("bad key"), allow_fallback=False)
        self.assertIn("bad key", res.ai_error)


# --------------------------------------------------------------------------
class RecordingBackend:
    """A mailbox that records what it was asked and returns canned mail."""

    last_scan = ""

    def __init__(self, msgs=(), skipped=()) -> None:
        """Hold the canned messages and the ids to report as skipped."""
        self.msgs = list(msgs)
        self.skipped_ids = list(skipped)
        self.calls: list[dict] = []

    def fetch(self, **kw):
        """Record the arguments, return the canned messages."""
        self.calls.append(kw)
        return self.msgs


class TestWatcherPerMailbox(TempDbCase):
    """Each mailbox resumes from its own last email."""

    def _watcher(self, backends) -> Watcher:
        """A watcher over the given (row, backend) pairs."""
        w = Watcher(self.db, self.settings, emit=self.emit)
        p = patch("core.watcher.account_backends", lambda s, d: backends)
        p.start()
        self.addCleanup(p.stop)
        return w

    def test_one_mailbox_does_not_advance_anothers_resume_point(self) -> None:
        """Mailbox 2 handling new mail must not hide mailbox 1's backlog."""
        old = (NOW - timedelta(days=3)).isoformat()
        new = (NOW - timedelta(minutes=5)).isoformat()
        self.db.mark_email_processed("a", old, "s", "routed", account_id=1)
        self.db.mark_email_processed("b", new, "s", "routed", account_id=2)
        one, two = RecordingBackend(), RecordingBackend()
        w = self._watcher([({"id": 1, "address": "one"}, one),
                           ({"id": 2, "address": "two"}, two)])
        w._poll(since=None, unread_only=False)
        self.assertLess(one.calls[0]["since"], two.calls[0]["since"])

    def test_processed_email_is_recorded_against_its_mailbox(self) -> None:
        """The mailbox id is stored with each handled email."""
        msg = EmailMessage("m1", "subj", "a@b.com", NOW, "body", [self.make_pdf()])
        w = self._watcher([({"id": 7, "address": "x"}, RecordingBackend([msg]))])
        w._parser.parse = MagicMock(return_value=ParseResult(customer_name="X"))
        w._router.route = MagicMock(return_value="routed")
        w._poll(since=None, unread_only=False)
        self.assertIsNotNone(self.db.last_seen_email_time(7, own_only=True))

    def test_emails_with_nothing_wanted_are_not_refetched(self) -> None:
        """Ids a backend skipped are passed back as 'seen' on the next poll."""
        backend = RecordingBackend(skipped=["junk1"])
        w = self._watcher([(None, backend)])
        w._poll(since=None, unread_only=False)
        w._poll(since=None, unread_only=False)
        self.assertNotIn("junk1", backend.calls[0]["seen_ids"])
        self.assertIn("junk1", backend.calls[1]["seen_ids"])

    def test_failed_email_stays_inside_the_scan_window(self) -> None:
        """A newer email being handled must not push a failed one out of reach."""
        older = EmailMessage("bad", "s", "a@b.com", NOW - timedelta(hours=2),
                             "body", [self.make_pdf("bad.pdf")])
        newer = EmailMessage("good", "s", "a@b.com", NOW - timedelta(hours=1),
                             "body", [self.make_pdf("good.pdf")])
        backend = RecordingBackend([older, newer])
        w = self._watcher([(None, backend)])
        w._router.route = MagicMock(return_value="routed")
        w._parser.parse = MagicMock(side_effect=[RuntimeError("boom"),
                                                 ParseResult(customer_name="X")])
        w._poll(since=None, unread_only=False)
        backend.msgs = []
        w._poll(since=None, unread_only=False)
        self.assertLess(backend.calls[1]["since"], older.received_at)

    def test_ai_fallback_only_allowed_on_the_last_attempt(self) -> None:
        """Attempts 1 and 2 ask the parser to raise on an outage; 3 does not."""
        msg = EmailMessage("m1", "subj", "a@b.com", NOW, "body", [self.make_pdf()])
        w = self._watcher([(None, RecordingBackend([msg]))])
        w._parser.parse = MagicMock(side_effect=AITransientError("down"))
        for _ in range(3):
            w._poll(since=None, unread_only=False)
        flags = [c.kwargs["allow_fallback"] for c in w._parser.parse.call_args_list]
        self.assertEqual(flags, [False, False, True])


# --------------------------------------------------------------------------
class TestDocxTables(unittest.TestCase):
    """Word invoices keep their numbers in tables."""

    def test_table_cells_are_extracted(self) -> None:
        """Text inside a table reaches the parser."""
        try:
            import docx
        except ImportError:
            self.skipTest("python-docx not installed")
        import tempfile
        from core.parser_ai import extract_text
        path = pathlib.Path(tempfile.mkdtemp()) / "inv.docx"
        doc = docx.Document()
        table = doc.add_table(rows=1, cols=2)
        table.rows[0].cells[0].text = "Job No"
        table.rows[0].cells[1].text = "10160"
        doc.save(str(path))
        self.assertIn("10160", extract_text(path))


if __name__ == "__main__":
    unittest.main(verbosity=2)
