"""Decides where a parsed supplier invoice goes and performs the uploads.

InvoiceWarden is an ACCOUNTS PAYABLE tool: it files invoices the business has
RECEIVED from its suppliers against the right job. The "customer" tables and
columns are named from an early misreading of that and are kept for schema
stability - everything user-facing calls them suppliers.

Rules (from the spec):
  * Match extracted customer name against the local DB (name + aliases).
  * Unknown customer -> added automatically, flagged as unreviewed (NEW in
    the Customers tab) and routed straight away. Defaults: Service system ON,
    Accounting system OFF, PDF only. An invoice with no readable customer name
    is queued in ``pending_invoices`` instead, since there is nothing to file
    it under.
  * Known customer -> for each enabled toggle, call the matching provider:
      - servicem8_enabled  -> the selected Service system
      - accounting_enabled -> the selected Accounting system
  * Duplicate guard: a document (by file hash, or customer+ref+type) is never
    uploaded to the same platform twice - see processed_documents.
  * Credit notes (doc_type == "credit") are routed as credits/vendor-credits
    where the provider supports it.
Every attempt is written to activity_log or error_log.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from core.database import Database
from core.parser_ai import ParseResult
from integrations.provider_base import UploadContext
from integrations.registry import build_accounting_provider, build_service_provider

log = logging.getLogger(__name__)


class Router:
    """Routes one parsed invoice to the enabled destinations."""

    def __init__(self, db: Database, settings, on_new_customer=None, emit=None) -> None:
        """Wire the router to the DB, settings and the UI callbacks."""
        self._db = db
        self._settings = settings
        self._on_new_customer = on_new_customer          # callable(name, pending_id)
        self._emit = emit or (lambda **_: None)          # GUI log pump

    def _ctx(self, customer, parsed: ParseResult, file_path: Path,
             subject: str) -> UploadContext:
        """Assemble the provider-agnostic upload context."""
        return UploadContext(
            customer_name=customer["name"] if customer else parsed.customer_name,
            customer_external_id=(customer["accounting_contact_id"] if customer else ""),
            job_number=parsed.job_number,
            invoice_ref=parsed.invoice_ref,
            amount_total=parsed.amount_total,
            invoice_date=parsed.invoice_date,
            file_path=file_path,
            email_subject=subject,
            doc_type=parsed.doc_type,
            job_candidates=list(parsed.job_candidates or []),
        )

    @staticmethod
    def _file_hash(path: Path) -> str:
        """SHA-256 of the attachment bytes - identifies an identical file.

        Read in chunks so a large scanned PDF is never held in memory whole.
        """
        digest = hashlib.sha256()
        try:
            with path.open("rb") as fh:
                while chunk := fh.read(1 << 20):
                    digest.update(chunk)
        except OSError:
            return ""
        return digest.hexdigest()

    def route(self, parsed: ParseResult, attachments: list[Path],
              subject: str, sender: str, job_floor: int = 0,
              job_ceiling: int = 0) -> str:
        """Process every attachment for one email. Returns a short result tag.

        ``job_floor``/``job_ceiling`` are the catch-up sweep guards (0 = off):
        while either is set, an invoice is only filed against a Service-system
        job whose number is within [floor, ceiling]. They are passed in per
        call, never stored - only a deliberate catch-up sweep supplies them;
        routine polls and Error-Log retries pass 0.
        """
        from core.parser_ai import NON_INVOICE_KINDS

        # Suppliers send statements, quotes, remittances and delivery dockets
        # to the same mailbox. Filing those against a job would be wrong.
        if parsed.doc_type in NON_INVOICE_KINDS:
            for path in attachments:
                self._emit(level="INFO", customer_name=parsed.customer_name,
                           invoice_ref=parsed.invoice_ref, platform="-",
                           action="skipped", filename=path.name,
                           message=f"Not a payable document (looks like a "
                                   f"{parsed.doc_type}) - nothing filed.")
            return "not_an_invoice"

        customer = self._db.find_customer_by_name(parsed.customer_name)

        if customer is None:
            customer = self._auto_add_customer(parsed, sender)
        if customer is None:
            # Only reachable when no name could be parsed at all - there is
            # nothing to file the invoice under, so hold it for a human.
            for path in attachments:
                self._db.add_pending(
                    extracted_name=parsed.customer_name,
                    job_number=parsed.job_number,
                    invoice_ref=parsed.invoice_ref,
                    email_subject=subject,
                    email_from=sender,
                    file_path=str(path),
                    raw_json=json.dumps(parsed.as_dict()),
                )
                read = (parsed.customer_name or "").strip()
                why = (f"Supplier read as '{read}' but not confident enough to "
                       f"add it automatically"
                       if read else
                       "No supplier name could be read from this invoice")
                self._emit(level="WARN", customer_name=read or "(unknown)",
                           invoice_ref=parsed.invoice_ref, platform="-",
                           action="queued", filename=path.name,
                           message=f"{why} - add the supplier in the Suppliers "
                                   f"tab, then retry from the Error Log.")
                # Also record it as an error: the Error Log is the only screen
                # with a Retry button, so a held invoice that only lived in
                # pending_invoices was invisible and could never be actioned.
                self._db.add_error(
                    stage="supplier", customer_name=read,
                    invoice_ref=parsed.invoice_ref, filename=path.name,
                    error=f"{why}. Add the supplier, then Retry.",
                    payload=json.dumps({
                        "customer_name": read,
                        "job_number": parsed.job_number,
                        "invoice_ref": parsed.invoice_ref,
                        "amount_total": parsed.amount_total,
                        "invoice_date": parsed.invoice_date,
                        "doc_type": parsed.doc_type,
                        "job_candidates": list(parsed.job_candidates or []),
                        "file_path": str(path),
                        "email_subject": subject,
                        "platform": "-",
                    }))
            return "pending_new_customer"

        allowed = set(customer["file_types"].split(","))
        # Resolved once per email, not per attachment: building a provider
        # refreshes OAuth tokens.
        targets = self._targets_for(customer)
        routed_any = False
        for path in attachments:
            ext = path.suffix.lower().lstrip(".")
            if allowed and ext not in allowed:
                self._emit(level="INFO", customer_name=customer["name"],
                           invoice_ref=parsed.invoice_ref, platform="-",
                           action="skipped", filename=path.name,
                           message=f"File type .{ext} not enabled for this customer.")
                continue
            ctx = self._ctx(customer, parsed, path, subject)
            routed_any |= self._dispatch(customer, ctx, targets,
                                         job_floor=job_floor, job_ceiling=job_ceiling)
        return "routed" if routed_any else "no_route"

    def _targets_for(self, customer) -> list:
        """Resolve the providers this customer's toggles point at.

        ``servicem8_enabled`` is the generic "Service system" toggle and
        ``accounting_enabled`` the "Accounting system" toggle - each resolves
        to whichever provider is selected in Settings.
        """
        targets = []
        if customer["servicem8_enabled"]:
            prov = build_service_provider(self._settings)
            if prov.key != "none":
                targets.append(prov)
        if customer["accounting_enabled"]:
            prov = build_accounting_provider(self._settings)
            if prov.key != "none" and prov.key not in {t.key for t in targets}:
                targets.append(prov)
        return targets

    def _auto_add_customer(self, parsed: ParseResult, sender: str):
        """Create a customer for an unrecognised supplier and flag it as new.

        Prompting for every unknown supplier does not scale, so they are added
        silently and shown with a NEW badge in the Customers tab. Defaults
        match the safe choice: the Service system on (that is the whole point
        of the tool), the Accounting system OFF so nothing reaches the books
        without a deliberate decision, and PDF only.
        """
        name = (parsed.customer_name or "").strip()
        if not name:
            return None

        # Gate only the CREATION of a supplier, not routing: matching an
        # existing supplier is itself evidence the name is right, but inventing
        # one from a doubtful reading pollutes the list and mis-files invoices.
        floor = self._settings.get_float("customers.min_confidence", 0.4)
        if parsed.confidence < floor:
            self._emit(level="WARN", customer_name=name,
                       invoice_ref=parsed.invoice_ref, platform="-",
                       action="held",
                       message=f"Supplier '{name}' read with low confidence "
                               f"({parsed.confidence:.2f} < {floor:.2f}), so it "
                               f"was NOT added automatically. Add the supplier "
                               f"in the Suppliers tab, then retry from the "
                               f"Error Log.")
            return None
        try:
            cid = self._db.upsert_customer({
                "name": name,
                "aliases": [],
                "servicem8_enabled": True,     # email -> service system: on
                "accounting_enabled": False,   # email -> accounting: manual
                "file_types": ["pdf"],
                "notes": f"Added automatically from an invoice sent by {sender}.",
                "reviewed": 0,                 # shows as NEW until checked
            })
        except Exception as exc:
            log.exception("Auto-add failed for %s", name)
            self._emit(level="ERROR", customer_name=name, action="auto_add",
                       message=f"Could not add customer automatically: {exc}")
            return None

        self._emit(level="INFO", customer_name=name, platform="-",
                   action="new customer",
                   message="New supplier added automatically (Service upload ON, "
                           "Accounting OFF, PDF only). Review it in Customers.")
        return self._db.get_customer(cid)

    def _dispatch(self, customer, ctx: UploadContext, targets=None,
                  job_floor: int = 0, job_ceiling: int = 0) -> bool:
        """Fire each enabled provider for a single file.

        ``targets`` is the pre-resolved provider list from :meth:`_targets_for`;
        it is only rebuilt here when a caller (the error-tab retry) has none.
        ``job_floor``/``job_ceiling`` are the one-off sweep guards - see
        :meth:`route`.
        """
        if targets is None:
            targets = self._targets_for(customer)
        if not targets:
            self._emit(level="WARN", customer_name=customer["name"],
                       invoice_ref=ctx.invoice_ref, platform="-", action="no_route",
                       filename=ctx.file_path.name,
                       message="Customer matched but no upload toggles enabled.")
            return False

        file_hash = self._file_hash(ctx.file_path)
        kind = "credit note" if ctx.is_credit else "invoice"

        ok_any = False
        for provider in targets:
            if provider.category == "service" and not ctx.job_number:
                if not self._resolve_job(ctx, provider):
                    continue
            # Catch-up sweep guard: during a deliberate backlog sweep the
            # caller can restrict which Service-system jobs are filed - a lower
            # bound, an upper bound, or both. Filing against a job outside that
            # window (or one whose number could not be read) is worse than not
            # filing it, so skip it.
            if provider.category == "service" and self._outside_job_range(
                    ctx.job_number, job_floor, job_ceiling):
                log.debug("Job %r outside the sweep range [%s, %s] - not filed to %s",
                          ctx.job_number, job_floor or "-", job_ceiling or "-",
                          provider.label)
                continue
            if not provider.configured():
                self._record_error("config", ctx, provider.label,
                                   f"{provider.label} credentials are incomplete.")
                continue

            # Duplicate guard: same file bytes, or same customer+ref+type,
            # already uploaded to this platform.
            dup = self._db.document_already_sent(
                file_hash, ctx.customer_name, ctx.invoice_ref, ctx.doc_type, provider.label)
            if dup:
                self._emit(level="WARN", customer_name=ctx.customer_name,
                           invoice_ref=ctx.invoice_ref, platform=provider.label,
                           action="duplicate", filename=ctx.file_path.name,
                           message=f"Skipped - this {kind} was already sent to "
                                   f"{provider.label} on {dup['ts']}.")
                continue

            try:
                result = provider.upload_invoice(ctx)
            except Exception as exc:  # never let one provider kill the run
                log.exception("Provider %s crashed", provider.label)
                self._record_error("upload", ctx, provider.label, str(exc))
                continue

            if result.ok:
                ok_any = True
                self._db.record_document_sent(
                    file_hash=file_hash, customer_name=ctx.customer_name,
                    invoice_ref=ctx.invoice_ref, doc_type=ctx.doc_type,
                    platform=result.platform, remote_id=result.remote_id,
                    job_number=ctx.job_number, filename=ctx.file_path.name)
                msg = f"[{kind}] {result.detail}"
                # Only emit: the GUI's emit_event() persists every non-error
                # event, so writing the row here as well logged each upload
                # twice. Every other event in this class emits only.
                self._emit(level="INFO", customer_name=ctx.customer_name,
                           invoice_ref=ctx.invoice_ref, platform=result.platform,
                           action="uploaded", filename=ctx.file_path.name, message=msg)
            else:
                self._record_error("upload", ctx, result.platform, result.detail)
        return ok_any

    @staticmethod
    def _outside_job_range(job_number: str, floor: int, ceiling: int) -> bool:
        """True when a one-off sweep bound is set and this job falls outside it.

        ``floor`` and ``ceiling`` of 0 (or less) each disable that end of the
        check; with neither set the result is always False. A missing or
        non-numeric job number counts as outside any active range, so nothing
        is filed while the check cannot be applied.
        """
        if floor <= 0 and ceiling <= 0:
            return False
        digits = "".join(ch for ch in (job_number or "") if ch.isdigit())
        if not digits:
            return True
        n = int(digits)
        return (floor > 0 and n < floor) or (ceiling > 0 and n > ceiling)

    def _resolve_job(self, ctx: UploadContext, provider) -> bool:
        """Fill in a missing job number, or record why it could not be.

        Credit notes typically quote the invoice they are crediting rather than
        a job number, so look up the job we filed that invoice against. Plain
        invoices can still fall back to the other reference numbers found on
        the document, which the provider verifies against real jobs.
        """
        if ctx.is_credit:
            refs = [ctx.invoice_ref, *ctx.job_candidates]
            job, how = self._db.find_job_for_credit(ctx.customer_name, refs,
                                                    provider.label)
            if job:
                ctx.job_number = job
                # An exact invoice match is certain; "most recent invoice" is a
                # guess, so it is logged as a WARN to be checked rather than
                # buried among the routine INFO lines.
                exact = how.startswith("matched invoice")
                self._emit(level="INFO" if exact else "WARN",
                           customer_name=ctx.customer_name,
                           invoice_ref=ctx.invoice_ref, platform=provider.label,
                           action="credit linked", filename=ctx.file_path.name,
                           message=(f"Credit note filed against job {job} ({how})."
                                    + ("" if exact else " This is a best guess - "
                                       "the credit did not quote an invoice we "
                                       "have on record. Check it."))) 
                return True
            self._record_error(
                "routing", ctx, provider.label,
                "Credit note has no job number, and no earlier invoice for "
                f"'{ctx.customer_name}' is on record to link it to. Upload the "
                "original invoice first, or set the job number and retry.")
            return False

        if ctx.job_candidates:
            # Nothing labelled as a job, but the provider can test the other
            # numbers on the document against real jobs.
            return True

        self._record_error("routing", ctx, provider.label,
                           f"No job number could be read for {provider.label}.")
        return False

    def _record_error(self, stage: str, ctx: UploadContext, platform: str,
                      message: str) -> None:
        """Write an error row and mirror it to the live log pump."""
        payload = json.dumps({
            "customer_name": ctx.customer_name,
            "job_number": ctx.job_number,
            "invoice_ref": ctx.invoice_ref,
            "amount_total": ctx.amount_total,
            "invoice_date": ctx.invoice_date,
            "file_path": str(ctx.file_path),
            "email_subject": ctx.email_subject,
            "platform": platform,
        })
        self._db.add_error(stage=stage, customer_name=ctx.customer_name,
                           invoice_ref=ctx.invoice_ref, filename=ctx.file_path.name,
                           error=f"[{platform}] {message}", payload=payload)
        self._emit(level="ERROR", customer_name=ctx.customer_name,
                   invoice_ref=ctx.invoice_ref, platform=platform, action="error",
                   filename=ctx.file_path.name, message=message)

    # -- retry from the Error tab -----------------------------------
    def retry_from_payload(self, payload_json: str) -> bool:
        """Re-run a single failed upload from its stored JSON snapshot."""
        data = json.loads(payload_json)
        parsed = ParseResult(
            customer_name=data.get("customer_name", ""),
            job_number=data.get("job_number", ""),
            invoice_ref=data.get("invoice_ref", ""),
            amount_total=data.get("amount_total", ""),
            invoice_date=data.get("invoice_date", ""),
            # Without these a retried credit note would be re-filed as an
            # invoice, and would lose the references used to find its job.
            doc_type=data.get("doc_type", "invoice"),
            job_candidates=list(data.get("job_candidates") or []),
            confidence=1.0,   # a human is retrying: do not re-gate on doubt
        )
        path = Path(data["file_path"])
        if not path.exists():
            self._emit(level="ERROR", customer_name=parsed.customer_name,
                       platform=data.get("platform", "-"), action="retry",
                       message=f"Cached file missing: {path}")
            return False
        customer = self._db.find_customer_by_name(parsed.customer_name)
        if customer is None:
            self._emit(level="ERROR", customer_name=parsed.customer_name,
                       platform="-", action="retry",
                       message="Customer still not in database.")
            return False
        return self._dispatch(customer, self._ctx(customer, parsed, path,
                                                  data.get("email_subject", "")))
