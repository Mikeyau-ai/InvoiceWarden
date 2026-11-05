"""Regression tests for the 2026-11 repo audit (see AUDIT_FIXLIST.md).

Each test names the audit item (F1, F2 ...) it guards. They were written to
FAIL on the pre-fix code and pass after. Everything is faked: no network, no
mailbox, no real credentials or user data.
"""
from __future__ import annotations

import pathlib
import queue
import ssl
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import core.router as router_mod                                    # noqa: E402
from core.parser_ai import InvoiceParser, ParseResult               # noqa: E402
from core.watcher import Watcher                                    # noqa: E402
from integrations.email_outlook import EmailMessage, _safe          # noqa: E402
from integrations.provider_base import UploadContext                # noqa: E402
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_core import FakeService, TempDbCase                       # noqa: E402


class FakeResp:
    """Minimal requests.Response stand-in."""

    def __init__(self, status: int = 200, data=None, headers=None) -> None:
        """Hold a status code and a JSON body."""
        self.status_code = status
        self._data = data if data is not None else {}
        self.headers = headers or {}
        self.text = ""
        self.ok = status < 400

    def raise_for_status(self) -> None:
        """Raise like requests does for 4xx/5xx."""
        import requests
        if self.status_code >= 400:
            err = requests.HTTPError(f"HTTP {self.status_code}")
            err.response = self
            raise err

    def json(self):
        """The canned body."""
        return self._data


def _ctx(tmp: pathlib.Path, ref: str = "INV-1", name: str = "a.pdf",
         job: str = "10160") -> UploadContext:
    """An upload context pointing at a real temp file."""
    path = tmp / name
    path.write_bytes(b"%PDF-1.4 x")
    return UploadContext(customer_name="Acme", customer_external_id="",
                         job_number=job, invoice_ref=ref, amount_total="10",
                         invoice_date="", file_path=path, email_subject="s",
                         job_candidates=[job])


# --------------------------------------------------------------------------
class TestImapSecurity(unittest.TestCase):
    """F1/F2: IMAP must verify TLS and must not trust a forged Date."""

    def test_f1_tls_certificates_are_verified(self) -> None:
        """IMAP4_SSL gets a context that checks the certificate and hostname."""
        from integrations.email_imap import ImapBackend
        captured = {}

        def fake_ssl(host, port, **kw):
            """Record how the connection was opened, then refuse."""
            captured.update(kw)
            raise OSError("stop")

        with patch("imaplib.IMAP4_SSL", fake_ssl):
            with self.assertRaises(RuntimeError):
                ImapBackend("h", 993, "u", "p")._connect()
        ctx = captured.get("ssl_context")
        self.assertIsNotNone(ctx, "no ssl_context passed - default is unverified")
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(ctx.check_hostname)

    def test_f2_future_date_header_is_clamped(self) -> None:
        """A message claiming to be from 2099 is treated as 'now' at most."""
        from integrations.email_imap import ImapBackend
        msg = types.SimpleNamespace(get=lambda k, d=None: "Mon, 01 Jan 2099 00:00:00 +0000")
        got = ImapBackend._received_at(msg)
        self.assertLessEqual(got, datetime.now(timezone.utc) + timedelta(seconds=5))


# --------------------------------------------------------------------------
class FakeBackend:
    """A mailbox that returns canned messages."""

    last_scan = ""

    def __init__(self, msgs) -> None:
        """Hold the messages to return."""
        self.msgs = msgs

    def fetch(self, **_kw):
        """Return the canned messages."""
        return self.msgs


class TestWatcherState(TempDbCase):
    """F2/F3: what the watcher records as 'processed'."""

    def _msg(self, mid="m1", received=None) -> EmailMessage:
        """One email with a real attachment file."""
        return EmailMessage(mid, "subj", "a@b.com",
                            received or datetime.now(timezone.utc), "body",
                            [self.make_pdf(f"{mid}.pdf")])

    def _watcher(self, msgs) -> Watcher:
        """A watcher whose only mailbox returns ``msgs``."""
        w = Watcher(self.db, self.settings, emit=self.emit)
        self._patch = patch("core.watcher.account_backends",
                            lambda s, d: [(None, FakeBackend(msgs))])
        self._patch.start()
        self.addCleanup(self._patch.stop)
        return w

    def test_f2_resume_point_ignores_future_rows(self) -> None:
        """A poisoned future received_at in the DB cannot freeze polling."""
        self.db.mark_email_processed("x", "2099-01-01T00:00:00+00:00", "s", "ok")
        w = Watcher(self.db, self.settings, emit=self.emit)
        point = w._resume_point()
        self.assertTrue(point is None or point <= datetime.now(timezone.utc))

    def test_f3_parse_failure_is_retried_not_swallowed(self) -> None:
        """A parse exception must not mark the email processed first time."""
        w = self._watcher([self._msg()])
        w._parser.parse = MagicMock(side_effect=RuntimeError("boom"))
        w._poll(since=None, unread_only=False)
        self.assertFalse(self.db.is_email_processed("m1"))

    def test_f3_parse_failure_gives_up_after_three_tries(self) -> None:
        """...but it does not loop forever: the third failure is recorded."""
        w = self._watcher([self._msg()])
        w._parser.parse = MagicMock(side_effect=RuntimeError("boom"))
        for _ in range(3):
            w._poll(since=None, unread_only=False)
        self.assertTrue(self.db.is_email_processed("m1"))
        self.assertTrue(self.db.list_errors())

    def test_f3_out_of_range_email_stays_unprocessed(self) -> None:
        """A catch-up sweep that skips an invoice must leave it for later."""
        w = self._watcher([self._msg()])
        w._parser.parse = MagicMock(return_value=ParseResult(customer_name="X"))
        w._router.route = MagicMock(return_value="out_of_range")
        w._poll(since=None, unread_only=False, job_floor=5000)
        self.assertFalse(self.db.is_email_processed("m1"))

    def test_f5_ai_failure_is_reported(self) -> None:
        """When the AI failed and regex took over, the user is told."""
        w = self._watcher([self._msg()])
        w._parser.parse = MagicMock(return_value=ParseResult(
            customer_name="X", ai_error="quota exceeded", source="regex"))
        w._router.route = MagicMock(return_value="routed")
        w._poll(since=None, unread_only=False)
        self.assertTrue(any(lvl == "WARN" and "quota exceeded" in m
                            for lvl, _a, m in self.events))


# --------------------------------------------------------------------------
class TestRouterFixes(TempDbCase):
    """F3/F5/F12/F25 router behaviour."""

    def test_f3_non_invoice_skip_is_visible(self) -> None:
        """The 'not payable' skip is a WARN, so the default log view shows it."""
        self.route(ParseResult(customer_name="Acme", doc_type="statement"),
                   self.make_pdf())
        levels = [lvl for lvl, a, _m in self.events if a == "skipped"]
        self.assertEqual(levels, ["WARN"])

    def test_f3_range_skip_reports_out_of_range(self) -> None:
        """A sweep-range skip is distinguishable from 'no route'."""
        self.db.upsert_customer({"name": "Known Co", "servicem8_enabled": True,
                                 "file_types": ["pdf"]})
        result = self.route(ParseResult(customer_name="Known Co", job_number="9500",
                                        confidence=0.9, job_candidates=["9500"]),
                            self.make_pdf(), job_floor=10000)
        self.assertEqual(result, "out_of_range")

    def test_f5_new_supplier_held_when_ai_failed(self) -> None:
        """A regex-fallback guess after an AI failure never creates a supplier."""
        result = self.route(ParseResult(customer_name="Garbage Co", job_number="1",
                                        confidence=0.9, ai_error="bad key",
                                        job_candidates=["1"]), self.make_pdf())
        self.assertEqual(result, "pending_new_customer")
        self.assertEqual(self.db.list_customers(), [])

    def test_f12_successful_retry_resolves_pending_row(self) -> None:
        """Retrying a held invoice clears its pending row (unpins the file)."""
        path = self.make_pdf("held.pdf")
        self.db.upsert_customer({"name": "Later Co", "servicem8_enabled": True,
                                 "file_types": ["pdf"]})
        self.db.add_pending(extracted_name="Later Co", file_path=str(path))
        import json
        payload = json.dumps({"customer_name": "Later Co", "job_number": "10160",
                              "job_candidates": ["10160"], "file_path": str(path)})
        r = router_mod.Router(self.db, self.settings, emit=self.emit)
        with patch.object(router_mod, "build_service_provider",
                          lambda s: FakeService(s)):
            self.assertTrue(r.retry_from_payload(payload))
        self.assertEqual(self.db.list_pending(), [])


# --------------------------------------------------------------------------
class TestParserFailure(TempDbCase):
    """F5: AI failure and odd confidence values."""

    def _parse(self, side_effect=None, reply=None) -> ParseResult:
        """Parse one email with a faked AI call."""
        self.settings.set("ai.provider", "openai")
        self.settings.set("ai.openai_api_key", "k")
        target = {"side_effect": side_effect} if side_effect else {"return_value": reply}
        with patch("core.parser_ai._call_provider", MagicMock(**target)):
            return InvoiceParser(self.settings).parse("s", "b", [], sender="a@x.com")

    def test_ai_exception_sets_ai_error(self) -> None:
        """The regex fallback result carries the reason the AI failed."""
        res = self._parse(side_effect=RuntimeError("quota exceeded"))
        self.assertIn("quota exceeded", res.ai_error)

    def test_non_numeric_confidence_keeps_ai_result(self) -> None:
        """'high' as a confidence must not throw away a good extraction."""
        res = self._parse(reply='{"customer_name": "Acme", "confidence": "high"}')
        self.assertEqual(res.customer_name, "Acme")
        self.assertEqual(res.source, "openai")


# --------------------------------------------------------------------------
class TestProviderQueries(TempDbCase):
    """F6/F7/F13: vendor query construction and error handling."""

    def test_f6_servicem8_filter_escapes_quotes(self) -> None:
        """A ' in a job reference cannot break out of the OData string."""
        from urllib.parse import unquote
        from integrations.service.servicem8 import ServiceM8Provider
        p = ServiceM8Provider(self.settings)
        p._session = MagicMock()
        p._session.get.return_value = FakeResp(200, [])
        p._lookup("generated_job_id", "1' or 'a'='a")
        url = unquote(p._session.get.call_args[0][0])
        self.assertIn("1'' or ''a''=''a", url)

    def test_f7_servicem8_auth_failure_is_not_no_match(self) -> None:
        """A 401 is reported as an API/auth error, not 'no job matched'."""
        from integrations.service.servicem8 import ServiceM8Provider
        p = ServiceM8Provider(self.settings)
        p._session = MagicMock()
        p._session.get.return_value = FakeResp(401)
        res = p.upload_invoice(_ctx(self.tmp))
        self.assertFalse(res.ok)
        self.assertNotIn("No ServiceM8 job matched", res.detail)

    def test_f6_xero_blank_reference_is_refused(self) -> None:
        """A blank invoice number is never used to 'find' an existing bill."""
        from integrations.accounting.xero import XeroProvider
        p = XeroProvider(self.settings)
        p._session = MagicMock()
        with patch.object(XeroProvider, "_headers", lambda s: {}):
            res = p.upload_invoice(_ctx(self.tmp, ref=""))
        self.assertFalse(res.ok)
        p._session.get.assert_not_called()

    def test_f6_myob_blank_reference_is_refused(self) -> None:
        """Same guard for MYOB."""
        from integrations.accounting.myob import MyobProvider
        p = MyobProvider(self.settings)
        p._session = MagicMock()
        with patch.object(MyobProvider, "_headers", lambda s: {}):
            res = p.upload_invoice(_ctx(self.tmp, ref=""))
        self.assertFalse(res.ok)
        p._session.get.assert_not_called()

    def test_f6_xero_where_clause_escapes_quotes(self) -> None:
        """A double quote in the ref cannot end the Xero string literal."""
        from urllib.parse import unquote
        from integrations.accounting.xero import XeroProvider
        p = XeroProvider(self.settings)
        p._session = MagicMock()
        p._session.get.return_value = FakeResp(200, {"Invoices": [{"InvoiceID": "I"}]})
        p._session.put.return_value = FakeResp(200)
        with patch.object(XeroProvider, "_headers", lambda s: {}):
            p.upload_invoice(_ctx(self.tmp, ref='A" OR 1==1 OR "'))
        # Either refused outright, or the quote cannot reach the query.
        if p._session.get.call_args:
            self.assertNotIn('A" OR', unquote(p._session.get.call_args[0][0]))

    def test_f13_xero_attachment_name_is_url_encoded(self) -> None:
        """Spaces and # in a file name do not corrupt the attachment URL."""
        from integrations.accounting.xero import XeroProvider
        p = XeroProvider(self.settings)
        p._session = MagicMock()
        p._session.get.return_value = FakeResp(200, {"Invoices": [{"InvoiceID": "I"}]})
        p._session.put.return_value = FakeResp(200)
        with patch.object(XeroProvider, "_headers", lambda s: {}):
            p.upload_invoice(_ctx(self.tmp, name="my inv #1.pdf"))
        url = p._session.put.call_args[0][0]
        self.assertNotIn(" ", url)
        self.assertNotIn("#", url)


# --------------------------------------------------------------------------
class TestCryptoFallback(unittest.TestCase):
    """F9: a recovered keyring must not mint a second master key."""

    def test_existing_fallback_key_is_adopted(self) -> None:
        """Keyring empty but a fallback file exists: reuse that key."""
        import core.crypto as crypto
        from cryptography.fernet import Fernet
        tmp = pathlib.Path(tempfile.mkdtemp())
        key = Fernet.generate_key()
        (tmp / ".masterkey").write_bytes(key)
        store: dict = {}
        fake = types.SimpleNamespace(
            get_password=lambda s, u: store.get((s, u)),
            set_password=lambda s, u, v: store.__setitem__((s, u), v))
        with patch.object(crypto, "keyring", fake), \
                patch.object(crypto, "_FALLBACK_KEY_FILE", tmp / ".masterkey"):
            self.assertEqual(crypto._load_or_create_key(), key)


# --------------------------------------------------------------------------
class TestSmallFixes(unittest.TestCase):
    """F10/F15/F18/F19/F20/F21."""

    def test_f10_error_events_are_persisted(self) -> None:
        """ERROR-level events reach activity_log (they used to vanish)."""
        try:
            from gui.app import App
        except Exception:
            self.skipTest("GUI not importable")
        saved = []
        fake = types.SimpleNamespace(
            _closing=False, _events=queue.Queue(),
            db=types.SimpleNamespace(add_activity=lambda **k: saved.append(k)))
        App.emit_event(fake, level="ERROR", action="poll", message="mailbox down")
        self.assertEqual(len(saved), 1)

    def test_f15_second_instance_is_refused(self) -> None:
        """Only one InvoiceWarden may own the database at a time."""
        from core import instance
        first = instance.acquire(port=50871)
        try:
            self.assertIsNotNone(first)
            self.assertIsNone(instance.acquire(port=50871))
        finally:
            if first:
                first.close()

    def test_f18_graph_asks_for_plain_text_body(self) -> None:
        """Graph is asked for text bodies so the AI isn't fed HTML/CSS."""
        from integrations.email_outlook import GraphBackend
        g = GraphBackend(MagicMock())
        sess = MagicMock()
        sess.get.return_value = FakeResp(200, {"value": []})
        with patch.object(GraphBackend, "_token", lambda s: "t"), \
                patch.object(GraphBackend, "_http", lambda s: sess):
            g.fetch(since=None, unread_only=False, allowed_ext=set())
        headers = sess.get.call_args.kwargs["headers"]
        self.assertIn("text", headers.get("Prefer", ""))

    def test_f19_release_detects_missing_build_deps(self) -> None:
        """release.py can tell when an optional library would be skipped."""
        import release
        with patch("importlib.util.find_spec", lambda name: None):
            self.assertIn("msal", release.missing_build_deps())

    def test_f20_frozen_autostart_command_is_just_the_exe(self) -> None:
        """A frozen build must not append a meaningless main.py path."""
        from core import startup
        with patch.object(sys, "frozen", True, create=True), \
                patch.object(sys, "executable", r"C:\x\InvoiceWarden.exe"):
            cmd = startup._launch_command()
        self.assertNotIn("main.py", cmd)
        self.assertTrue(cmd.endswith("--autostart"))

    def test_f21_reserved_windows_names_are_neutralised(self) -> None:
        """An attachment called CON.pdf must not be written as a device name."""
        for bad in ("CON.pdf", "nul.pdf", "COM1.pdf", "LPT3.pdf"):
            with self.subTest(bad=bad):
                self.assertNotEqual(_safe(bad).split(".")[0].upper(),
                                    bad.split(".")[0].upper())


if __name__ == "__main__":
    unittest.main(verbosity=2)
