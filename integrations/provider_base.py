"""Shared provider contract for every external destination.

Two categories:
  * ``service``    - field-service / job-management systems (ServiceM8, simPRO,
                     AroFlo, ...). Invoices attach to a *job*.
  * ``accounting`` - accounting / bookkeeping systems (MYOB, Xero, QBO, ...).
                     Invoices attach to a *supplier bill*.

The watcher/router only ever talk to this interface. Adding a system means
writing one subclass and listing it in :mod:`integrations.registry`.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

#: Seconds of headroom before a cached access token is treated as expired.
_TOKEN_SKEW = 60


@dataclass
class UploadContext:
    """Everything a provider needs to file one invoice attachment."""

    customer_name: str
    customer_external_id: str       # per-customer mapping stored in the DB
    job_number: str
    invoice_ref: str
    amount_total: str
    invoice_date: str
    file_path: Path
    email_subject: str
    doc_type: str = "invoice"        # "invoice" | "credit"
    #: Other reference numbers on the document, best guess first. A service
    #: provider may try these when the labelled job number is absent or wrong.
    job_candidates: list = field(default_factory=list)

    @property
    def is_credit(self) -> bool:
        """True when this document is a credit note rather than an invoice."""
        return self.doc_type == "credit"


def supplier_matches(ctx: UploadContext, remote_id: str, remote_name: str) -> bool:
    """True when a document found in the accounting system is this supplier's.

    Invoice numbers are only unique per supplier, so a bill found by number
    alone may belong to someone else. The supplier's stored accounting ID
    decides when there is one; otherwise the names are compared loosely
    (legal suffixes and punctuation ignored, one may extend the other). A
    document that names no supplier at all cannot be judged and is accepted.
    """
    from core.database import normalise_name

    remote_id, remote_name = (remote_id or "").strip(), (remote_name or "").strip()
    if not remote_id and not remote_name:
        return True
    wanted_id = (ctx.customer_external_id or "").strip()
    if wanted_id and remote_id:
        return wanted_id.lower() == remote_id.lower()
    ours, theirs = normalise_name(ctx.customer_name), normalise_name(remote_name)
    if not ours or not theirs:
        return False
    return ours == theirs or ours.startswith(theirs + " ") or theirs.startswith(ours + " ")


@dataclass
class UploadResult:
    """Outcome returned to the router / logs."""

    ok: bool
    platform: str
    detail: str
    remote_id: str = ""


class Provider:
    """Base class. Subclasses implement auth + upload for one system."""

    key: str = "base"
    label: str = "Base Provider"
    category: str = "accounting"        # "service" | "accounting"
    implemented: bool = True            # False -> shown as "(preview)" in the UI
    uses_oauth: bool = False            # True -> the Authorise button applies

    #: (setting_key, field_label, is_secret) - drives which inputs the UI shows
    setting_fields: list[tuple[str, str, bool]] = []

    def __init__(self, settings) -> None:
        """Bind the provider to the settings store it reads credentials from."""
        self._settings = settings
        self._session = None
        self._token: tuple[str, float] = ("", 0.0)   # (access_token, expires_at)

    @property
    def http(self):
        """Shared :class:`requests.Session` for this provider instance.

        Every call to the same API host then reuses one TLS connection instead
        of paying a fresh handshake per request.
        """
        if self._session is None:
            import requests

            self._session = requests.Session()
        return self._session

    def _cached_access_token(self, refresh) -> str:
        """Return a live bearer token, refreshing only when the old one expires.

        ``refresh`` is a callable returning ``(token, lifetime_seconds)``.
        Without this every request re-ran the OAuth refresh - two-plus round
        trips per upload, and for the providers that rotate refresh tokens, an
        encrypted settings write each time too.
        """
        token, expires_at = self._token
        if token and time.monotonic() < expires_at:
            return token
        token, ttl = refresh()
        self._token = (token, time.monotonic() + max(0, int(ttl) - _TOKEN_SKEW))
        return token

    def configured(self) -> bool:
        """True when every mandatory setting for this provider is present."""
        return bool(self.setting_fields) and all(
            self._settings.get(k) for k, _, _ in self.setting_fields
        )

    def missing_fields(self) -> list[str]:
        """Human labels of the settings still blank."""
        return [lbl for k, lbl, _ in self.setting_fields if not self._settings.get(k)]

    def test_connection(self) -> UploadResult:
        """Verify credentials against the live API. Subclasses must implement."""
        raise NotImplementedError

    def upload_invoice(self, ctx: UploadContext) -> UploadResult:
        """File one attachment in the remote system. Subclasses must implement."""
        raise NotImplementedError


# Backwards-compatible alias (old imports).
AccountingProvider = Provider


class NoneProvider(Provider):
    """'None / disabled' entry for either dropdown."""

    key = "none"
    label = "None / Disabled"
    implemented = False
    setting_fields = []

    def configured(self) -> bool:
        """Never configured - selecting 'none' means routing is off."""
        return False

    def test_connection(self) -> UploadResult:
        """Always fails: there is nothing to connect to."""
        return UploadResult(False, self.label, "No provider selected.")

    def upload_invoice(self, ctx: UploadContext) -> UploadResult:
        """Always fails: there is nothing to upload to."""
        return UploadResult(False, self.label, "No provider selected.")


class StubProvider(Provider):
    """A selectable provider whose API client is not wired yet.

    It still advertises its real credential fields so the deployment can be
    configured now; uploads fail loudly (and land in the retry queue) until
    the concrete client is added.
    """

    implemented = False

    def test_connection(self) -> UploadResult:
        """Report whether credentials are complete; never actually connects."""
        missing = self.missing_fields()
        if missing:
            return UploadResult(False, self.label, f"Missing: {', '.join(missing)}")
        return UploadResult(
            False, self.label,
            f"{self.label}: credentials look complete, but the upload client "
            f"is a preview and is not wired yet.",
        )

    def upload_invoice(self, ctx: UploadContext) -> UploadResult:
        """Always fails loudly so the invoice lands in the retry queue."""
        return UploadResult(
            False, self.label,
            f"{self.label} upload is not implemented in this build.",
        )
