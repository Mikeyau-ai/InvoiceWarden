"""QuickBooks Online provider - uploads the invoice as an Attachable linked
to a Bill.

OAuth2 with Intuit; ``realm_id`` identifies the company. One-time consent via
:func:`authorize_interactive`.

Docs: https://developer.intuit.com/app/developer/qbo/docs/api/accounting/all-entities/attachable
"""
from __future__ import annotations

import json
import mimetypes
import webbrowser

import requests

from integrations.provider_base import Provider, UploadContext, UploadResult

AUTH_URL = "https://appcenter.intuit.com/connect/oauth2"
TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
API = "https://quickbooks.api.intuit.com/v3/company"


class QuickBooksProvider(Provider):
    """Uploads invoice files to QuickBooks Online as Attachable records."""

    key = "qbo"
    category = "accounting"
    uses_oauth = True
    label = "QuickBooks Online"
    setting_fields = [
        ("qbo.client_id", "Client ID", True),
        ("qbo.client_secret", "Client Secret", True),
        ("qbo.redirect_uri", "Redirect URI", False),
        ("qbo.realm_id", "Realm ID (Company ID)", True),
        ("qbo.refresh_token", "OAuth Refresh Token", True),
    ]
    SCOPES = "com.intuit.quickbooks.accounting"

    def authorize_interactive(self) -> str:
        """Open the Intuit consent page. Returns the URL the user visits."""
        params = {
            "client_id": self._settings.get("qbo.client_id"),
            "redirect_uri": self._settings.get("qbo.redirect_uri"),
            "response_type": "code",
            "scope": self.SCOPES,
            "state": "invoicewarden",
        }
        url = AUTH_URL + "?" + requests.compat.urlencode(params)
        webbrowser.open(url)
        return url

    def exchange_code(self, code: str) -> None:
        """Swap an auth code for tokens and persist the refresh token."""
        r = self.http.post(TOKEN_URL, data={
            "grant_type": "authorization_code",
            "code": code.strip(),
            "redirect_uri": self._settings.get("qbo.redirect_uri"),
        }, auth=(self._settings.get("qbo.client_id"),
                 self._settings.get("qbo.client_secret")),
           headers={"Accept": "application/json"}, timeout=30)
        r.raise_for_status()
        self._settings.set("qbo.refresh_token", r.json()["refresh_token"])

    def _refresh(self) -> tuple[str, int]:
        """Exchange the stored refresh token for a new access token.

        Intuit rotates the refresh token on each call, so the replacement is
        persisted here - one more reason not to do this per request.
        """
        r = self.http.post(TOKEN_URL, data={
            "grant_type": "refresh_token",
            "refresh_token": self._settings.get("qbo.refresh_token"),
        }, auth=(self._settings.get("qbo.client_id"),
                 self._settings.get("qbo.client_secret")),
           headers={"Accept": "application/json"}, timeout=30)
        r.raise_for_status()
        payload = r.json()
        self._settings.set("qbo.refresh_token", payload["refresh_token"])
        return payload["access_token"], payload.get("expires_in", 3600)

    def _access_token(self) -> str:
        """A valid bearer token, reused until it is close to expiring."""
        return self._cached_access_token(self._refresh)

    def _base(self) -> str:
        """Company-scoped API root for the configured realm."""
        return f"{API}/{self._settings.get('qbo.realm_id')}"

    def test_connection(self) -> UploadResult:
        """Read the company record to prove the credentials work."""
        try:
            r = self.http.get(
                f"{self._base()}/companyinfo/{self._settings.get('qbo.realm_id')}",
                headers={"Authorization": f"Bearer {self._access_token()}",
                         "Accept": "application/json"}, timeout=25)
            return UploadResult(r.ok, self.label,
                                "Connected to QuickBooks." if r.ok else f"HTTP {r.status_code}")
        except requests.RequestException as exc:
            return UploadResult(False, self.label, f"QBO error: {exc}")

    def upload_invoice(self, ctx: UploadContext) -> UploadResult:
        """Post the file as an Attachable with a descriptive note."""
        try:
            token = self._access_token()
            mime = mimetypes.guess_type(ctx.file_path.name)[0] or "application/pdf"
            kind = "Vendor credit" if ctx.is_credit else "Bill"
            meta = {
                "AttachableRef": [{"IncludeOnSend": False}],
                "FileName": ctx.file_path.name,
                "Note": (f"{kind} {ctx.invoice_ref} for {ctx.customer_name} "
                         f"(job {ctx.job_number})"),
            }
            with ctx.file_path.open("rb") as fh:
                files = {
                    "file_metadata_01": ("metadata.json", json.dumps(meta), "application/json"),
                    "file_content_01": (ctx.file_path.name, fh.read(), mime),
                }
                r = self.http.post(f"{self._base()}/upload",
                                  headers={"Authorization": f"Bearer {token}",
                                           "Accept": "application/json"},
                                  files=files, timeout=60)
            r.raise_for_status()
            return UploadResult(True, self.label,
                                f"Uploaded {ctx.file_path.name} to QuickBooks "
                                f"({kind.lower()}).", remote_id="")
        except requests.RequestException as exc:
            return UploadResult(False, self.label, f"QBO upload failed: {exc}")
