"""ServiceM8 provider - attaches invoices to jobs via the REST API.

Auth: a ServiceM8 "Private Application" API key, sent in the ``X-API-Key``
header (ServiceM8's documented method for private apps - it is NOT HTTP Basic
auth; sending Basic auth makes ServiceM8 reply "Invalid username or password").

Flow:
  1. Look up the job by the extracted job number (``generated_job_id`` /
     ``job_number`` field on the Job object).
  2. Create an Attachment record linked to that job UUID and upload the file
     bytes to the ``.file`` sub-resource.

Docs: https://developer.servicem8.com/docs/authentication
"""
from __future__ import annotations

import mimetypes

import requests

from integrations.provider_base import Provider, UploadContext, UploadResult

API = "https://api.servicem8.com/api_1.0"


class ServiceM8Provider(Provider):
    """Attaches invoice files to a ServiceM8 job, matched by job number."""

    key = "servicem8"
    label = "ServiceM8"
    category = "service"
    setting_fields = [("servicem8.api_key", "Private App API Key", True)]

    def _headers(self) -> dict:
        """Private-app API key header sent on every ServiceM8 call."""
        return {
            "X-API-Key": self._settings.get("servicem8.api_key"),
            "Accept": "application/json",
        }

    def test_connection(self) -> UploadResult:
        """Request a single job to prove the API key works."""
        try:
            r = self.http.get(f"{API}/job.json?%24top=1", headers=self._headers(), timeout=20)
            if r.status_code == 200:
                return UploadResult(True, self.label, "Connected to ServiceM8.")
            return UploadResult(False, self.label, f"HTTP {r.status_code}: {r.text[:200]}")
        except requests.RequestException as exc:
            return UploadResult(False, self.label, f"Connection error: {exc}")

    #: Job fields a supplier's reference might correspond to. generated_job_id
    #: is what ServiceM8 shows as the job number; job_number is the internal
    #: sequence; purchase_order_number covers suppliers who quote the PO.
    JOB_FIELDS = ("generated_job_id", "job_number", "purchase_order_number")

    def _lookup(self, field: str, value: str) -> str | None:
        """UUID of the job whose ``field`` equals ``value``, or None."""
        # OData string literals escape ' by doubling it; the value comes from
        # email text, so it must not be able to end the literal.
        safe = value.replace("'", "''")
        filt = requests.utils.quote(f"{field} eq '{safe}'")
        r = self.http.get(f"{API}/job.json?%24filter={filt}",
                          headers=self._headers(), timeout=20)
        # 400/404 only mean "this field cannot be filtered that way". Auth,
        # rate-limit and server errors must surface, not read as "no match".
        if r.status_code in (400, 404):
            return None
        r.raise_for_status()
        rows = r.json()
        return rows[0]["uuid"] if rows else None

    def _find_job_uuid(self, ctx: UploadContext) -> tuple[str | None, str]:
        """Find the job, trying every reference the document offered.

        The parser cannot know which number on an invoice is the job number, so
        it hands over candidates and ServiceM8 - the system of record - decides.
        Returns (uuid, which_number_matched).
        """
        tried: list[str] = []
        for value in [ctx.job_number, *ctx.job_candidates]:
            value = (value or "").strip()
            if not value or value in tried:
                continue
            tried.append(value)
            for field in self.JOB_FIELDS:
                uuid = self._lookup(field, value)
                if uuid:
                    return uuid, value
        self._tried = tried
        return None, ""

    def upload_invoice(self, ctx: UploadContext) -> UploadResult:
        """Resolve the job, create an attachment record, upload the bytes."""
        try:
            self._tried = []
            job_uuid, matched = self._find_job_uuid(ctx)
            if not job_uuid:
                tried = ", ".join(self._tried) or "(nothing readable)"
                return UploadResult(
                    False, self.label,
                    f"No ServiceM8 job matched any reference on this document. "
                    f"Tried: {tried}.")
            # Keep the number that actually matched, so a later credit note can
            # be linked back to the same job.
            ctx.job_number = matched

            fname = ctx.file_path.name
            mime = mimetypes.guess_type(fname)[0] or "application/pdf"
            # ServiceM8 stores a flat attachment on the job; flag credits in the
            # visible name so they are obvious in the job file.
            display_name = f"CREDIT NOTE - {fname}" if ctx.is_credit else fname

            # 1. create the attachment record
            meta = {
                "related_object": "job",
                "related_object_uuid": job_uuid,
                "attachment_name": display_name,
                "file_type": "." + fname.rsplit(".", 1)[-1].lower(),
                "active": 1,
            }
            r = self.http.post(f"{API}/attachment.json", headers=self._headers(),
                              json=meta, timeout=30)
            r.raise_for_status()
            att_uuid = r.headers.get("x-record-uuid") or r.json().get("uuid", "")
            if not att_uuid:
                return UploadResult(False, self.label, "ServiceM8 did not return an attachment UUID.")

            # 2. upload the bytes
            # Stream the file object rather than reading it into memory.
            with ctx.file_path.open("rb") as fh:
                up = self.http.post(
                    f"{API}/attachment/{att_uuid}.file",
                    headers={**self._headers(), "Content-Type": mime},
                    data=fh, timeout=60,
                )
            up.raise_for_status()
            return UploadResult(True, self.label,
                                f"Attached {fname} to job {ctx.job_number}.",
                                remote_id=att_uuid)
        except requests.RequestException as exc:
            return UploadResult(False, self.label, f"ServiceM8 API error: {exc}")
