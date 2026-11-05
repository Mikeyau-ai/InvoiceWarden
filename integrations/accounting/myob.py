"""MYOB AccountRight / MYOB Business provider.

Auth is OAuth2 (bearer) plus a company-file token:
  * Authorization: Bearer <access_token>       (refreshed from refresh_token)
  * x-myobapi-key: <client_id>
  * x-myobapi-version: v2
  * x-myobapi-cftoken: base64(cf_username:cf_password)

The one-time OAuth consent is done via :func:`authorize_interactive` (opens a
browser, user pastes the ``code`` back). After that the refresh token is
stored encrypted and used headlessly.

Upload: this attaches the invoice PsF to a Supplier Bill / Purchase. Because
MYOB's document model varies by product, ``upload_invoice`` creates a
Purchase/Bill draft referencing the invoice number and attaches the file via
the ``/Attachment`` endpoint. Verify field names against your company file.

Docs: https://developer.myob.com/api/accountright/v2/
"""
from __future__ import annotations

import base64
import webbrowser

import requests

from integrations.provider_base import Provider, UploadContext, UploadResult

AUTH_URL = "https://secure.myob.com/oauth2/account/authorize"
TOKEN_URL = "https://secure.myob.com/oauth2/v1/authorize"
API_ROOT = "https://api.myob.com/accountright"

#: MYOB embeds the attachment as base64 in the request body, so a very
#: large file would be held in memory twice over. Refuse past this.
_MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024


class MyobProvider(Provider):
    """Attaches invoice files to an AccountRight supplier bill."""

    key = "myob"
    category = "accounting"
    uses_oauth = True
    label = "MYOB"
    setting_fields = [
        ("myob.client_id", "Client ID (API Key)", True),
        ("myob.client_secret", "Client Secret", True),
        ("myob.redirect_uri", "Redirect URI", True),
        ("myob.cf_username", "Company File Username", True),
        ("myob.cf_password", "Company File Password", True),
        ("myob.company_file_id", "Company File ID (GUID)", False),
        ("myob.refresh_token", "OAuth Refresh Token", True),
    ]

    # -- OAuth ---------------------------------------------------------
    def authorize_interactive(self) -> str:
        """Open the consent page. Returns the URL the user should visit."""
        params = {
            "client_id": self._settings.get("myob.client_id"),
            "redirect_uri": self._settings.get("myob.redirect_uri"),
            "response_type": "code",
            "scope": "CompanyFile",
        }
        url = AUTH_URL + "?" + requests.compat.urlencode(params)
        webbrowser.open(url)
        return url

    def exchange_code(self, code: str) -> None:
        """Swap an auth code for tokens and persist the refresh token."""
        data = {
            "client_id": self._settings.get("myob.client_id"),
            "client_secret": self._settings.get("myob.client_secret"),
            "scope": "CompanyFile",
            "code": code.strip(),
            "redirect_uri": self._settings.get("myob.redirect_uri"),
            "grant_type": "authorization_code",
        }
        r = self.http.post(TOKEN_URL, data=data, timeout=30)
        r.raise_for_status()
        self._settings.set("myob.refresh_token", r.json()["refresh_token"])

    def _refresh(self) -> tuple[str, int]:
        """Exchange the stored refresh token for a new bearer token."""
        data = {
            "client_id": self._settings.get("myob.client_id"),
            "client_secret": self._settings.get("myob.client_secret"),
            "refresh_token": self._settings.get("myob.refresh_token"),
            "grant_type": "refresh_token",
        }
        r = self.http.post(TOKEN_URL, data=data, timeout=30)
        r.raise_for_status()
        payload = r.json()
        if payload.get("refresh_token"):
            self._settings.set("myob.refresh_token", payload["refresh_token"])
        return payload["access_token"], payload.get("expires_in", 1200)

    def _access_token(self) -> str:
        """A valid bearer token, reused until it is close to expiring."""
        return self._cached_access_token(self._refresh)

    def _headers(self) -> dict:
        """Bearer + API key + company-file token headers for every call."""
        cf_token = base64.b64encode(
            f"{self._settings.get('myob.cf_username')}:{self._settings.get('myob.cf_password')}".encode()
        ).decode()
        return {
            "Authorization": f"Bearer {self._access_token()}",
            "x-myobapi-key": self._settings.get("myob.client_id"),
            "x-myobapi-version": "v2",
            "x-myobapi-cftoken": cf_token,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _cf_base(self) -> str:
        """API root scoped to the configured company file."""
        return f"{API_ROOT}/{self._settings.get('myob.company_file_id')}"

    # -- provider API -------------------------------------------------
    def test_connection(self) -> UploadResult:
        """Hit the API root to prove auth and the company file work."""
        try:
            r = self.http.get(f"{API_ROOT}/", headers=self._headers(), timeout=25)
            if r.status_code == 200:
                return UploadResult(True, self.label, "Authenticated with MYOB.")
            return UploadResult(False, self.label, f"HTTP {r.status_code}: {r.text[:200]}")
        except requests.RequestException as exc:
            return UploadResult(False, self.label, f"MYOB error: {exc}")

    def upload_invoice(self, ctx: UploadContext) -> UploadResult:
        """Attach the invoice/credit file to a supplier bill in the company file.

        MYOB models a supplier credit as a Bill with a negative total (there is
        no separate 'supplier credit note' resource in the AccountRight API),
        so a credit note is created as a negative-amount draft bill and clearly
        described. Review it in MYOB before applying.
        """
        ref = (ctx.invoice_ref or "").strip()
        if not ref:
            # A blank number would match (and attach to) an unrelated bill.
            return UploadResult(False, self.label,
                                "No invoice number was read, so it cannot be "
                                "matched safely in MYOB. Set the reference and retry.")
        try:
            headers = self._headers()
            base = self._cf_base()
            credit = ctx.is_credit

            total = ctx.amount_total or "0"
            if credit and not str(total).lstrip().startswith("-"):
                total = f"-{total}"

            # Locate an existing bill by number, else create a draft.
            flt = requests.utils.quote("Number eq '" + ref.replace("'", "''") + "'")
            find = self.http.get(f"{base}/Purchase/Bill/Item?$filter={flt}",
                                headers=headers, timeout=25)
            find.raise_for_status()
            items = find.json().get("Items", [])

            if items:
                bill_uid = items[0]["UID"]
            else:
                label = "Credit note import" if credit else "Invoice import"
                draft = {
                    "Supplier": {"Name": ctx.customer_name},
                    "Number": ctx.invoice_ref or None,
                    "Date": ctx.invoice_date or None,
                    "Lines": [{
                        "Type": "Transaction",
                        "Description": f"{label} - {ctx.email_subject}"[:255],
                        "Total": total,
                    }],
                }
                created = self.http.post(f"{base}/Purchase/Bill/Item",
                                        headers=headers, json=draft, timeout=30)
                created.raise_for_status()
                bill_uid = created.headers.get("Location", "").rstrip("/").split("/")[-1]

            # MYOB takes the file as base64 inside the JSON body, so it
            # cannot be streamed. Bound it rather than blowing up on a huge
            # scan with an opaque memory or HTTP error.
            size = ctx.file_path.stat().st_size
            if size > _MAX_ATTACHMENT_BYTES:
                return UploadResult(
                    False, self.label,
                    f"{ctx.file_path.name} is {size / 1e6:.1f} MB; MYOB "
                    f"attachments must stay under "
                    f"{_MAX_ATTACHMENT_BYTES / 1e6:.0f} MB.")
            with ctx.file_path.open("rb") as fh:
                blob = base64.b64encode(fh.read()).decode()
            attach = {
                "Origin": "SupplierBill",
                "OwnerUID": bill_uid,
                "FileName": ctx.file_path.name,
                "FileContentBase64": blob,
            }
            up = self.http.post(f"{base}/Attachment", headers=headers, json=attach, timeout=60)
            up.raise_for_status()
            noun = "credit (negative bill)" if credit else "bill"
            return UploadResult(True, self.label,
                                f"Attached {ctx.file_path.name} to MYOB {noun} {ctx.invoice_ref}.",
                                remote_id=bill_uid)
        except requests.RequestException as exc:
            return UploadResult(False, self.label, f"MYOB upload failed: {exc}")
