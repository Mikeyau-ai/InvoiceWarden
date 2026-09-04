"""Tests for the logic that decides where an invoice goes.

Standard library only (``unittest``, ``tempfile``, ``unittest.mock``) so it
runs anywhere the app runs, with no test dependency to install:

    python -m unittest discover -s tests      (or run_tests.bat)

Scope is deliberately the parts that fail *silently*: document classification,
reference extraction, credit-to-job linking, the duplicate guard and the
routing gates. Network calls are faked - nothing here touches a real mailbox,
ServiceM8 or an AI provider.
"""
from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import core.router as router_mod                                    # noqa: E402
from core.crypto import SecretBox                                   # noqa: E402
from core.database import Database                                  # noqa: E402
import core.parser_ai as parser_ai                                 # noqa: E402
from core.parser_ai import (                                        # noqa: E402
    NON_INVOICE_KINDS,
    ParseResult,
    _looks_like_credit,
    _regex_fallback,
    _supplier_from_sender,
    classify_document,
)
from core.settings_store import Settings                            # noqa: E402
from integrations.provider_base import Provider, UploadResult       # noqa: E402


class FakeService(Provider):
    """Stands in for ServiceM8: always configured, always succeeds."""

    key = "servicem8"
    label = "ServiceM8"
    category = "service"

    def configured(self) -> bool:
        """Pretend the API key is present."""
        return True

    def upload_invoice(self, ctx) -> UploadResult:
        """Record the job it was asked to file against."""
        return UploadResult(True, self.label, f"job {ctx.job_number}")


class TempDbCase(unittest.TestCase):
    """Base class giving each test a throwaway database and settings."""

    def setUp(self) -> None:
        """Create an isolated database in a temp directory."""
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.db = Database(self.tmp / "test.sqlite3")
        self.settings = Settings(self.db, SecretBox())
        self.settings.set("service.provider", "servicem8")
        self.settings.set("accounting.provider", "none")
        self.events: list[tuple] = []

    def tearDown(self) -> None:
        """Close the database handle."""
        self.db.close()

    def emit(self, **event) -> None:
        """Collect emitted log events the way the GUI would persist them."""
        self.events.append((event.get("level"), event.get("action"),
                            event.get("message", "")))

    def actions(self) -> list[str]:
        """Just the action names emitted so far."""
        return [a for _l, a, _m in self.events]

    def make_pdf(self, name: str = "invoice.pdf") -> pathlib.Path:
        """A small file standing in for an attachment.

        Content is derived from the name: two different documents must not
        share bytes, or the duplicate guard will correctly block the second.
        """
        path = self.tmp / name
        path.write_bytes(b"%PDF-1.4 " + name.encode())
        return path

    def route(self, parsed: ParseResult, path: pathlib.Path,
              sender: str = "accounts@acme.com.au", job_floor: int = 0,
              job_ceiling: int = 0) -> str:
        """Run the router with the service provider faked out."""
        self.events.clear()
        r = router_mod.Router(self.db, self.settings, emit=self.emit)
        with patch.object(router_mod, "build_service_provider",
                          lambda s: FakeService(s)):
            return r.route(parsed, [path], "subject", sender,
                           job_floor=job_floor, job_ceiling=job_ceiling)


class TestClassification(unittest.TestCase):
    """Only invoices and credits are payable - everything else is skipped."""

    def test_kinds(self) -> None:
        """Each document kind is recognised from its wording."""
        cases = [
            ("TAX INVOICE  Invoice No 4021  Job 10160", "invoice"),
            ("CREDIT NOTE CN-100", "credit"),
            ("Adjustment Note for job 10160", "credit"),
            ("STATEMENT OF ACCOUNT  Balance brought forward", "statement"),
            ("Monthly Statement for August", "statement"),
            ("Remittance Advice - payment sent", "remittance"),
            ("QUOTATION  Quote No 88  valid 30 days", "quote"),
            ("DELIVERY DOCKET  packing slip 42", "delivery"),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(classify_document(text), expected)

    def test_invoice_wins_over_incidental_mentions(self) -> None:
        """A real invoice that references a quote is still an invoice."""
        self.assertEqual(
            classify_document("Tax Invoice 900 - relates to Quote No 88"),
            "invoice")

    def test_unknown_defaults_to_invoice(self) -> None:
        """Skipping a real invoice is worse than filing an odd document."""
        self.assertEqual(classify_document("Some random letter"), "invoice")

    def test_non_invoice_kinds_are_not_payable(self) -> None:
        """The skip set must not accidentally include payable kinds."""
        self.assertNotIn("invoice", NON_INVOICE_KINDS)
        self.assertNotIn("credit", NON_INVOICE_KINDS)


class TestCreditDetection(unittest.TestCase):
    """'Credit' is the reliable signal, but not every 'credit' is a credit."""

    def test_real_credits(self) -> None:
        """Documents that genuinely are credits."""
        for text in ("CREDIT NOTE CN-100", "Credit Memo for job 10160",
                     "Adjustment Note", "Refund for overpayment",
                     "This is a credit for the returned parts"):
            with self.subTest(text=text):
                self.assertTrue(_looks_like_credit(text))

    def test_payment_wording_is_not_a_credit(self) -> None:
        """Ordinary invoice wording must not be misread as a credit."""
        for text in ("TAX INVOICE. Payment by credit card accepted.",
                     "Invoice - Credit terms: 30 days",
                     "Invoice. Your credit limit is $5,000",
                     "Invoice. Credit card surcharge 1.5%"):
            with self.subTest(text=text):
                self.assertFalse(_looks_like_credit(text))


class TestReferenceExtraction(unittest.TestCase):
    """The job number is the field that has to be right."""

    def test_labels_and_candidates(self) -> None:
        """Job and invoice numbers are told apart, and candidates collected."""
        r = _regex_fallback("Invoice", "Invoice No. 5567   Job #10160", "", "")
        self.assertEqual(r.job_number, "10160")
        self.assertEqual(r.invoice_ref, "5567")
        self.assertIn("10160", r.job_candidates)
        self.assertIn("5567", r.job_candidates)

    def test_label_variants(self) -> None:
        """Suppliers label these fields many different ways."""
        cases = [
            ("Work Order 88231", "88231"),
            ("Job Reference: 10160", "10160"),
            ("W/O 10160", "10160"),
            ("Service Call 4102", "4102"),
        ]
        for body, expected in cases:
            with self.subTest(body=body):
                self.assertEqual(_regex_fallback("", body, "", "").job_number,
                                 expected)

    def test_invoice_word_is_not_a_reference(self) -> None:
        """'Invoice' must not be read as 'inv' + 'oice' (a real past bug)."""
        r = _regex_fallback("Invoice", "", "", "")
        self.assertNotIn("oice", r.invoice_ref)

    def test_filename_is_used_when_text_is_sparse(self) -> None:
        """A scanned PDF often leaves only its filename to go on."""
        r = _regex_fallback("Invoice", "", "", "testco10160.pdf")
        self.assertEqual(r.job_number, "10160")


class TestSupplierIdentification(unittest.TestCase):
    """This is accounts payable: the supplier issued the invoice, not us."""

    def test_sender_domain(self) -> None:
        """A supplier's own domain is a strong signal."""
        self.assertEqual(_supplier_from_sender("accounts@acmeplumbing.com.au"),
                         "acmeplumbing")

    def test_generic_hosts_ignored(self) -> None:
        """A personal mailbox says nothing about the supplier."""
        for addr in ("x@gmail.com", "mikey@live.com.au", "a@outlook.com"):
            with self.subTest(addr=addr):
                self.assertEqual(_supplier_from_sender(addr), "")

    def test_bill_to_is_not_the_supplier(self) -> None:
        """The Bill To party is the recipient - us - and must be ignored."""
        r = _regex_fallback("Invoice", "Bill To: My Own Company Pty Ltd", "",
                            "", "accounts@acmeplumbing.com.au")
        self.assertNotIn("My Own Company", r.customer_name)
        self.assertEqual(r.customer_name, "acmeplumbing")


class TestDuplicateGuard(TempDbCase):
    """The same document must never be filed twice on the same platform."""

    def setUp(self) -> None:
        """Record one successful upload to compare against."""
        super().setUp()
        self.db.record_document_sent(
            file_hash="HASH1", customer_name="Testco", invoice_ref="INV-1",
            doc_type="invoice", platform="ServiceM8", remote_id="r",
            job_number="10160", filename="a.pdf")

    def test_same_bytes_blocked(self) -> None:
        """A re-sent identical file is a duplicate."""
        self.assertIsNotNone(self.db.document_already_sent(
            "HASH1", "Anything", "", "invoice", "ServiceM8"))

    def test_same_reference_blocked(self) -> None:
        """A re-issued PDF with the same invoice number is a duplicate."""
        self.assertIsNotNone(self.db.document_already_sent(
            "OTHER", "Testco", "INV-1", "invoice", "ServiceM8"))

    def test_other_platform_allowed(self) -> None:
        """A failed platform must still be retryable."""
        self.assertIsNone(self.db.document_already_sent(
            "HASH1", "Testco", "INV-1", "invoice", "Xero"))

    def test_credit_with_same_number_allowed(self) -> None:
        """A credit reusing the invoice number is a different document."""
        self.assertIsNone(self.db.document_already_sent(
            "OTHER", "Testco", "INV-1", "credit", "ServiceM8"))


class TestCreditLinking(TempDbCase):
    """Credits quote an invoice, not a job, so the job comes from history."""

    def setUp(self) -> None:
        """Two invoices already filed against different jobs."""
        super().setUp()
        for ref, job in (("INV-0001", "10160"), ("INV-0002", "10999")):
            self.db.record_document_sent(
                file_hash=f"h{ref}", customer_name="Testco", invoice_ref=ref,
                doc_type="invoice", platform="ServiceM8", remote_id="r",
                job_number=job, filename=f"{ref}.pdf")

    def test_exact_invoice_match(self) -> None:
        """Quoting a known invoice gives that invoice's job."""
        job, how = self.db.find_job_for_credit("Testco", ["INV-0001"], "ServiceM8")
        self.assertEqual(job, "10160")
        self.assertIn("matched invoice", how)

    def test_falls_back_to_most_recent(self) -> None:
        """With nothing quoted, the newest invoice is a labelled guess."""
        job, how = self.db.find_job_for_credit("Testco", [], "ServiceM8")
        self.assertEqual(job, "10999")
        self.assertIn("most recent", how)

    def test_unknown_supplier_gives_nothing(self) -> None:
        """No history means no guess at all."""
        self.assertEqual(self.db.find_job_for_credit("Nobody", ["X"], "ServiceM8"),
                         ("", ""))


class TestRoutingGates(TempDbCase):
    """What the router files, holds and skips."""

    def test_statement_is_skipped(self) -> None:
        """Non-payable documents never reach a job."""
        result = self.route(ParseResult(customer_name="Acme",
                                        doc_type="statement", confidence=0.9),
                            self.make_pdf())
        self.assertEqual(result, "not_an_invoice")
        self.assertIn("skipped", self.actions())
        self.assertEqual(self.db.list_customers(), [])

    def test_low_confidence_does_not_invent_a_supplier(self) -> None:
        """A doubtful reading holds the invoice instead of creating a record."""
        result = self.route(ParseResult(customer_name="Maybe Co",
                                        job_number="10160", confidence=0.2),
                            self.make_pdf())
        self.assertEqual(result, "pending_new_customer")
        self.assertIn("held", self.actions())
        self.assertEqual(self.db.list_customers(), [])

    def test_confident_supplier_is_added_and_routed(self) -> None:
        """A confident reading files the invoice and flags the supplier NEW."""
        result = self.route(ParseResult(customer_name="Acme Plumbing",
                                        job_number="10160", confidence=0.8,
                                        job_candidates=["10160"]),
                            self.make_pdf())
        self.assertEqual(result, "routed")
        self.assertIn("uploaded", self.actions())
        customer = self.db.find_customer_by_name("Acme Plumbing")
        self.assertIsNotNone(customer)
        self.assertTrue(customer["servicem8_enabled"])   # service: on
        self.assertFalse(customer["accounting_enabled"])  # accounting: manual
        self.assertEqual(customer["file_types"], "pdf")
        self.assertEqual(customer["reviewed"], 0)         # shows as NEW

    def test_known_supplier_routes_regardless_of_confidence(self) -> None:
        """The confidence gate applies to creation only, never to routing."""
        self.db.upsert_customer({"name": "Known Co", "servicem8_enabled": True,
                                 "file_types": ["pdf"]})
        result = self.route(ParseResult(customer_name="Known Co",
                                        job_number="10160", confidence=0.01,
                                        job_candidates=["10160"]),
                            self.make_pdf())
        self.assertEqual(result, "routed")
        self.assertIn("uploaded", self.actions())

    def test_catch_up_floor_blocks_old_jobs(self) -> None:
        """A catch-up floor leaves a job below it silently unfiled."""
        self.db.upsert_customer({"name": "Known Co", "servicem8_enabled": True,
                                 "file_types": ["pdf"]})
        result = self.route(ParseResult(customer_name="Known Co",
                                        job_number="9500", confidence=0.9,
                                        job_candidates=["9500"]),
                            self.make_pdf(), job_floor=10000)
        self.assertEqual(result, "no_route")
        self.assertNotIn("uploaded", self.actions())

    def test_catch_up_floor_allows_new_jobs(self) -> None:
        """A job at or above the floor is filed as normal."""
        self.db.upsert_customer({"name": "Known Co", "servicem8_enabled": True,
                                 "file_types": ["pdf"]})
        result = self.route(ParseResult(customer_name="Known Co",
                                        job_number="10160", confidence=0.9,
                                        job_candidates=["10160"]),
                            self.make_pdf(), job_floor=10000)
        self.assertEqual(result, "routed")
        self.assertIn("uploaded", self.actions())

    def test_catch_up_floor_blocks_unreadable_job(self) -> None:
        """Under a floor, an invoice with no readable job number is skipped too."""
        self.db.upsert_customer({"name": "Known Co", "servicem8_enabled": True,
                                 "file_types": ["pdf"]})
        result = self.route(ParseResult(customer_name="Known Co",
                                        job_number="", confidence=0.9,
                                        job_candidates=["7"]),
                            self.make_pdf(), job_floor=10000)
        self.assertEqual(result, "no_route")
        self.assertNotIn("uploaded", self.actions())

    def test_no_floor_files_every_job(self) -> None:
        """With no floor (the default), even a low job number is filed."""
        self.db.upsert_customer({"name": "Known Co", "servicem8_enabled": True,
                                 "file_types": ["pdf"]})
        result = self.route(ParseResult(customer_name="Known Co",
                                        job_number="12", confidence=0.9,
                                        job_candidates=["12"]),
                            self.make_pdf())
        self.assertEqual(result, "routed")
        self.assertIn("uploaded", self.actions())

    def test_catch_up_range_files_only_jobs_inside(self) -> None:
        """A catch-up range files a job within [floor, ceiling]..."""
        self.db.upsert_customer({"name": "Known Co", "servicem8_enabled": True,
                                 "file_types": ["pdf"]})
        result = self.route(ParseResult(customer_name="Known Co",
                                        job_number="15005", confidence=0.9,
                                        job_candidates=["15005"]),
                            self.make_pdf(), job_floor=15000, job_ceiling=15010)
        self.assertEqual(result, "routed")
        self.assertIn("uploaded", self.actions())

    def test_catch_up_range_skips_jobs_above_the_ceiling(self) -> None:
        """...and leaves a job past the ceiling silently unfiled."""
        self.db.upsert_customer({"name": "Known Co", "servicem8_enabled": True,
                                 "file_types": ["pdf"]})
        result = self.route(ParseResult(customer_name="Known Co",
                                        job_number="15050", confidence=0.9,
                                        job_candidates=["15050"]),
                            self.make_pdf(), job_floor=15000, job_ceiling=15010)
        self.assertEqual(result, "no_route")
        self.assertNotIn("uploaded", self.actions())

    def test_catch_up_range_can_target_a_single_job(self) -> None:
        """Floor == ceiling: only that one job number is filed."""
        self.db.upsert_customer({"name": "Known Co", "servicem8_enabled": True,
                                 "file_types": ["pdf"]})
        inside = self.route(ParseResult(customer_name="Known Co",
                                        job_number="15001", confidence=0.9,
                                        job_candidates=["15001"]),
                            self.make_pdf("a.pdf"), job_floor=15001, job_ceiling=15001)
        self.assertEqual(inside, "routed")
        outside = self.route(ParseResult(customer_name="Known Co",
                                         job_number="15002", confidence=0.9,
                                         job_candidates=["15002"]),
                             self.make_pdf("b.pdf"), job_floor=15001, job_ceiling=15001)
        self.assertEqual(outside, "no_route")

    def test_credit_without_job_is_linked(self) -> None:
        """A credit is filed against the job of the invoice it quotes."""
        self.db.upsert_customer({"name": "Testco", "servicem8_enabled": True,
                                 "file_types": ["pdf"]})
        self.route(ParseResult(customer_name="Testco", job_number="10160",
                               invoice_ref="INV-0001", confidence=0.9,
                               job_candidates=["10160"]),
                   self.make_pdf("inv.pdf"))
        self.route(ParseResult(customer_name="Testco", invoice_ref="CN-1",
                               doc_type="credit", confidence=0.9,
                               job_candidates=["INV-0001"]),
                   self.make_pdf("credit.pdf"))
        self.assertIn("credit linked", self.actions())
        self.assertIn("uploaded", self.actions())


class TestPendingQueue(TempDbCase):
    """Queued invoices must be findable again, or they are lost silently."""

    def test_status_defaults_so_replay_finds_it(self) -> None:
        """A past bug wrote an empty status, hiding the row from the replay."""
        self.db.add_pending(extracted_name="Testco", file_path="x.pdf")
        rows = self.db.list_pending("pending_new_customer")
        self.assertEqual(len(rows), 1)

    def test_repair_heals_legacy_rows(self) -> None:
        """Rows written by the buggy version are recovered on next replay."""
        self.db._exec("INSERT INTO pending_invoices(ts, extracted_name, status) "
                      "VALUES('t', 'Old', '')")
        self.assertEqual(self.db.repair_pending_status(), 1)
        self.assertEqual(len(self.db.list_pending()), 1)


try:
    import customtkinter  # noqa: F401  (app dependency; skip GUI tests without it)
    _HAVE_CTK = True
except Exception:
    _HAVE_CTK = False


@unittest.skipUnless(_HAVE_CTK, "customtkinter not importable")
class TestActivityLogView(unittest.TestCase):
    """The simple/advanced split and the green success colouring."""

    def test_routine_chatter_is_hidden_in_simple_view(self) -> None:
        """INFO progress lines are noise; the simple view drops them."""
        from gui.logs_tab import is_noise_line

        for action in ("found", "parsed", "poll", "cleanup", "skipped"):
            self.assertTrue(is_noise_line("INFO", action), action)

    def test_outcomes_and_warnings_always_show(self) -> None:
        """Anything at WARN/ERROR, and every non-chatter INFO action, stays."""
        from gui.logs_tab import is_noise_line

        self.assertFalse(is_noise_line("INFO", "uploaded"))
        self.assertFalse(is_noise_line("INFO", "new customer"))
        self.assertFalse(is_noise_line("WARN", "duplicate"))
        self.assertFalse(is_noise_line("WARN", "skipped"))   # user skipped a prompt
        self.assertFalse(is_noise_line("ERROR", "error"))

    def test_successful_filing_is_green(self) -> None:
        """A completed upload/credit-link gets the SUCCESS (green) tag."""
        from gui.logs_tab import line_tag

        self.assertEqual(line_tag("INFO", "uploaded"), "SUCCESS")
        self.assertEqual(line_tag("INFO", "credit linked"), "SUCCESS")
        self.assertEqual(line_tag("INFO", "found"), "INFO")
        self.assertEqual(line_tag("WARN", "duplicate"), "WARN")
        self.assertEqual(line_tag("ERROR", "error"), "ERROR")


class TestAiSelfTest(TempDbCase):
    """The Settings 'Test AI' button must build its prompt without crashing."""

    def test_prompt_placeholders_are_pinned(self) -> None:
        """Every {name} in the prompt is one both call sites know to supply.

        Adding a placeholder (as {sender} was) without updating the self-test
        call site made the button raise KeyError before any network request.
        """
        import string

        names = {f for _, f, _, _ in string.Formatter().parse(parser_ai._PROMPT) if f}
        self.assertEqual(names, {"sender", "subject", "filenames", "body", "attachment"})

    def test_self_test_builds_prompt_and_grades_the_reply(self) -> None:
        """End to end with the network faked: no KeyError, reads the reply."""
        self.settings.set("ai.provider", "gemini")
        self.settings.set("ai.gemini_api_key", "test-key")
        seen = {}

        def fake_call(provider, api_key, model, prompt, base_url=""):
            """Stand in for the provider HTTP call; capture the built prompt."""
            seen["prompt"] = prompt
            return ('{"customer_name": "Acme Pty Ltd", "job_number": "10160", '
                    '"invoice_ref": "INV-1042"}')

        with patch.object(parser_ai, "_call_provider", fake_call):
            ok, detail = parser_ai.test_ai_provider(self.settings)

        self.assertTrue(ok, detail)
        self.assertIn("accounts@acme.com.au", seen["prompt"])   # {sender} filled

    def test_self_test_reports_a_missing_key_cleanly(self) -> None:
        """No key saved is a clear message, not a crash."""
        self.settings.set("ai.provider", "gemini")
        self.settings.set("ai.gemini_api_key", "")
        ok, detail = parser_ai.test_ai_provider(self.settings)
        self.assertFalse(ok)
        self.assertIn("API key", detail)


class TestUpdateCheckErrors(unittest.TestCase):
    """The manual check must distinguish 'up to date' from 'could not check'."""

    def test_network_failure_raises_not_silently_current(self) -> None:
        """A dead connection raises UpdateCheckError rather than returning None."""
        import urllib.error

        from core import updater

        with patch.object(updater.urllib.request, "urlopen",
                          side_effect=urllib.error.URLError("no route to host")):
            with self.assertRaises(updater.UpdateCheckError):
                updater.check_now()

    def test_rate_limit_message_is_explained(self) -> None:
        """A 403 with no remaining quota is reported as rate-limiting."""
        import urllib.error

        from core import updater

        err = urllib.error.HTTPError(
            updater._API_LATEST, 403, "rate limited",
            {"X-RateLimit-Remaining": "0"}, None)
        with patch.object(updater.urllib.request, "urlopen", side_effect=err):
            with self.assertRaises(updater.UpdateCheckError) as caught:
                updater.check_now()
        self.assertIn("rate-limiting", str(caught.exception))

    def test_auto_check_stays_a_silent_no_op(self) -> None:
        """start_check()'s path still swallows failures (no exception, no result)."""
        import urllib.error

        from core import updater

        with patch.object(updater.urllib.request, "urlopen",
                          side_effect=urllib.error.URLError("offline")):
            self.assertIsNone(updater._fetch_latest())


class TestSourceHygiene(unittest.TestCase):
    """Guards against a class of bug that is invisible on screen."""

    def test_no_stray_control_characters(self) -> None:
        """Literal control bytes where an escape was intended break regexes.

        38 backspace characters once reached the source where ``\\b`` word
        boundaries were meant, silently stopping those patterns matching.
        """
        root = pathlib.Path(__file__).resolve().parent.parent
        offenders = []
        for path in root.rglob("*.py"):
            if any(part in {"build", "dist", ".venv", "__pycache__"}
                   for part in path.parts):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for char in text:
                if ord(char) < 32 and char not in "\n\r\t":
                    offenders.append(f"{path.name}: U+{ord(char):04X}")
                    break
        self.assertEqual(offenders, [], f"control characters found: {offenders}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
