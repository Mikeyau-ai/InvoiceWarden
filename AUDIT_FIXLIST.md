# InvoiceWarden audit: fix list (2026-11-05)

Source: repo-wide audit following `endofproject.txt`. Regression tests for fixed
items live in `tests/test_audit_fixes.py` (written first; they failed on the old
code). Status: **FIXED** (in working tree, not committed/released) or **OPEN**.

## Fixed

| ID | Sev | Issue | Where |
|----|-----|-------|-------|
| F1 | High | IMAP TLS certificates/hostname were NOT verified (imaplib default = CERT_NONE) | `email_imap._connect` |
| F2 | High | Forged future `Date:` header froze ingestion for all mailboxes (resume point = max received_at) | `email_imap._received_at`, `watcher._resume_point` |
| F3 | High | Email marked processed on parse failure / catch-up range skip; non-invoice skips hidden in default log | `watcher._poll`, `router.route/_dispatch` |
| F5 | High | AI failure was silent; regex guess then auto-created suppliers; non-numeric `confidence` discarded a good AI result | `parser_ai.parse`, `router._auto_add_customer`, `watcher._poll` |
| F6 | Med | Unescaped values in ServiceM8/Xero/MYOB filters; blank invoice ref could match an unrelated bill | `servicem8._lookup`, `xero`, `myob` |
| F7 | Med | ServiceM8 401/429/5xx reported as "no job matched" | `servicem8._lookup` |
| F9 | Med | Recovered keyring minted a 2nd master key, orphaning secrets encrypted with the fallback key file | `crypto._load_or_create_key` |
| F10 | Med | ERROR events (mailbox down, poll failed) never persisted | `gui/app.emit_event` |
| F11 | Med | Error Log retry ran network calls on the UI thread (freeze) | `gui/errors_tab._retry` |
| F12 | Med | Successful retry left pending row, pinning the cached file forever | `router.retry_from_payload`, `database.resolve_pending_by_path` |
| F13 | Low | Xero attachment filename not URL-encoded | `xero.upload_invoice` |
| F15 | Med | (new) No single-instance guard: autostart + manual launch = two watchers, duplicate uploads | `core/instance.py`, `main.py` |
| F18 | Low | Graph returned HTML bodies, wasting the AI body budget | `email_outlook.GraphBackend.fetch` |
| F19 | Med | Build could ship without msal/pdfminer/docx (spec skips silently) | `release.missing_build_deps` |
| F20 | Low | Frozen autostart command included a bogus `main.py` path | `startup._launch_command` |
| F21 | Low | Windows reserved names (CON.pdf) allowed as attachment filenames | `email_outlook._safe` |
| F22 | Low | `updater.json` written non-atomically | `updater._save_state` |
| F23 | Med | Duplicate-check/upload/record not serialised between watcher and GUI retry | `router._dispatch` lock |
| F24 | Med | COM `ReceivedTime` (local) stamped as UTC | `email_outlook.ComBackend.fetch` |
| F25 | Low | README typos/stale text (`toolselease_upload.py`, tabs, "plain text", Azure), stale GitHub URL in `run_tests.bat` | `README.md`, `run_tests.bat` |

## Open (not fixed; needs design or is larger than a surgical change)

- Per-mailbox resume cursor (one mailbox outage still loses its backlog behind another's newest mail).
- No sender allowlist / prompt-injection hardening: unknown senders can still create suppliers (held only when the AI failed or confidence is low).
- Multi-attachment emails are parsed as ONE document (second invoice logged as duplicate).
- Dedupe keyed on provider label and supplier *name* text, not ids; no UNIQUE constraint on `processed_documents`.
- QuickBooks upload is not linked to any Bill; reads whole file into memory. MYOB base64 whole file.
- ServiceM8: up to 36 serial lookups per invoice; orphan attachment record if the file upload fails after the record is created; catch-up range checks the parsed job number, not the job finally matched.
- IMAP fallback ids/cache buckets use sequence numbers (collisions after deletions). Graph large attachments silently skipped and re-fetched each poll.
- Rotating OAuth refresh tokens: providers rebuilt per email; GUI Test buttons use separate instances.
- Dead code: whole new-customer modal path, unused DB helpers, stale settings keys, `myob_enabled` column.
- `after()` called from worker threads in `app.py` / `update_dialog.py`.
- Settings dialog re-render discards unsaved typed values; mailbox Remove has no confirmation.
- Release: single signing key with no rotation/revocation; unpinned build toolchain; no lockfile; no CI; unsigned exe.
- Missing tests: provider HTTP clients beyond the above, email backend parsing, GUI.
- Licensing: no LICENSE/NOTICE file shipped with the exe for bundled third-party code (see audit report).

## Fixed after 1.0.43 (committed, not yet released)
- F26: multi-attachment emails are parsed and routed per attachment (`watcher._poll`).
- F27: `THIRD_PARTY_NOTICES.md` added and bundled into the exe via `InvoiceWarden.spec`. The project's own LICENSE is still the owner's decision.
