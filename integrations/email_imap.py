"""Generic IMAP mailbox backend.

Why this exists: not every deployment can use Outlook COM (classic desktop
Outlook only, which needs a paid Microsoft 365 subscription) or Microsoft
Graph (needs an Entra app registration, which for a personal Microsoft account
means signing up for an Azure subscription first). IMAP works with Gmail,
Fastmail, Yahoo, iCloud, cPanel/business hosts and most providers using
nothing but a host name and an app password.

Note: Microsoft disabled Basic auth for personal Outlook.com mailboxes on
2024-09-16, so this backend does NOT work against outlook.com - use the Graph
backend for those, or auto-forward that mail to a provider that still allows
app passwords.

Standard library only (imaplib + email); adds no dependency.
"""
from __future__ import annotations

import email
import imaplib
import logging
import re
import ssl
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from pathlib import Path

from config import ATTACHMENT_CACHE
from integrations.email_outlook import (
    FIRST_SCAN_LOOKBACK_DAYS,
    EmailMessage,
    OutlookBackend,
    _ext_of,
    _safe,
)

log = logging.getLogger(__name__)

#: Messages per header-fetch round trip. Large enough that the first pass is a
#: handful of commands, small enough to keep the command line sane.
_HEADER_BATCH = 200

#: Upper bound on how many search hits a single fetch considers (newest first).
_SEARCH_CAP = 500

#: Friendly presets so users do not have to look up server names.
IMAP_PRESETS: dict[str, tuple[str, int]] = {
    "Gmail": ("imap.gmail.com", 993),
    "Fastmail": ("imap.fastmail.com", 993),
    "Yahoo": ("imap.mail.yahoo.com", 993),
    "iCloud": ("imap.mail.me.com", 993),
    "Zoho": ("imap.zoho.com", 993),
    "Custom": ("", 993),
}


def _decode(raw: str | None) -> str:
    """Decode an RFC 2047 encoded header into plain text."""
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw)))
    except Exception:
        return raw


class ImapBackend(OutlookBackend):
    """Reads a mailbox over IMAP4-SSL using a username + app password."""

    def __init__(self, host: str, port: int, username: str, password: str,
                 folder: str = "INBOX", use_ssl: bool = True) -> None:
        """Store connection details; nothing is opened until :meth:`fetch`."""
        self._host = (host or "").strip()
        self._port = int(port or 993)
        self._user = (username or "").strip()
        self._password = password or ""
        self._folder = (folder or "").strip() or "INBOX"
        self._ssl = use_ssl
        self.last_scan = ""

    def _connect(self):
        """Open and authenticate an IMAP connection, with actionable errors."""
        if not self._host:
            raise RuntimeError("No IMAP server set. Pick a provider preset or "
                               "enter the server host in Settings.")
        if not self._user or not self._password:
            raise RuntimeError("IMAP username and password/app-password are required.")
        try:
            if self._ssl:
                # imaplib's default context does NOT verify the server
                # certificate or hostname, so pass a verifying one explicitly.
                conn = imaplib.IMAP4_SSL(self._host, self._port,
                                         ssl_context=ssl.create_default_context())
            else:
                conn = imaplib.IMAP4(self._host, self._port)
        except Exception as exc:
            raise RuntimeError(
                f"Could not reach {self._host}:{self._port} - {exc}") from exc
        try:
            conn.login(self._user, self._password)
        except imaplib.IMAP4.error as exc:
            raise RuntimeError(
                f"IMAP login refused for {self._user}: {exc}. Most providers "
                "need an APP PASSWORD (not your normal password) with 2FA "
                "turned on. outlook.com no longer allows app passwords at all - "
                "use the Graph backend for those accounts."
            ) from exc
        return conn

    def _folder_names(self, conn) -> list[str]:
        """Best-effort list of folder names, for a helpful error message."""
        names: list[str] = []
        try:
            typ, lines = conn.list()
            if typ == "OK":
                for line in lines or []:
                    text = line.decode(errors="replace")
                    names.append(text.split(' "/" ')[-1].strip().strip('"'))
        except Exception:
            pass
        return names

    def _select(self, conn) -> int:
        """Select the target folder read-only, returning its message count."""
        typ, data = conn.select(f'"{self._folder}"', readonly=True)
        if typ != "OK":
            names = self._folder_names(conn)
            raise RuntimeError(
                f"IMAP folder '{self._folder}' not found."
                + (f" Available: {', '.join(names[:25])}" if names else ""))
        try:
            return int(data[0])
        except (TypeError, ValueError, IndexError):
            return -1

    @staticmethod
    def _split_fetch(raw) -> dict[bytes, bytes]:
        """Map sequence number -> payload from a multi-message FETCH response.

        imaplib returns a flat list in which each message contributes a
        ``(b'<seq> (BODY[...] {<n>}', b'<payload>')`` tuple; the leading
        sequence number is the only reliable way to match a payload back to the
        message it came from.
        """
        out: dict[bytes, bytes] = {}
        for part in raw or []:
            if not isinstance(part, tuple) or len(part) < 2:
                continue
            match = re.match(rb"\s*(\d+)", part[0] or b"")
            if match:
                out[match.group(1)] = part[1]
        return out

    def _scan_headers(self, conn, seqs: list[bytes]) -> dict[bytes, object]:
        """Fetch just the headers for a batch of messages, in few round trips.

        This is the cheap first pass: it costs a few hundred bytes per message
        instead of the full body plus every attachment, and lets the date and
        already-processed filters run before anything large is transferred.
        """
        found: dict[bytes, object] = {}
        for start in range(0, len(seqs), _HEADER_BATCH):
            chunk = seqs[start:start + _HEADER_BATCH]
            typ, raw = conn.fetch(
                b",".join(chunk),
                "(BODY.PEEK[HEADER.FIELDS (DATE MESSAGE-ID SUBJECT FROM)])")
            if typ != "OK":
                continue
            for seq, payload in self._split_fetch(raw).items():
                try:
                    found[seq] = email.message_from_bytes(payload)
                except Exception:
                    continue
        return found

    def fetch(self, since, unread_only, allowed_ext, seen_ids=frozenset(),
              headers_only=False):
        """Return messages carrying attachments that are newer than ``since``.

        Two passes: headers for everything the server matched, then full bodies
        only for the messages that survive the date and dedupe filters.
        """
        floor = since or (datetime.now(timezone.utc)
                          - timedelta(days=FIRST_SCAN_LOOKBACK_DAYS))
        conn = self._connect()
        try:
            total = self._select(conn)

            # IMAP SINCE is day-granular; search a day wider and filter exactly
            # in Python so nothing is lost at the boundary.
            criteria = ["SINCE", (floor - timedelta(days=1)).strftime("%d-%b-%Y")]
            if unread_only:
                criteria.insert(0, "UNSEEN")
            typ, data = conn.search(None, *criteria)
            if typ != "OK":
                raise RuntimeError(f"IMAP search failed: {typ}")
            matched = (data[0] or b"").split()

            # Pass 1 - headers only, newest first, bounded.
            candidates = list(reversed(matched[-_SEARCH_CAP:]))
            headers = self._scan_headers(conn, candidates)

            older = already = 0
            wanted: list[tuple[bytes, str, object]] = []
            for seq in candidates:
                head = headers.get(seq)
                if head is None:
                    continue
                received = self._received_at(head)
                if received <= floor:
                    older += 1
                    continue
                msg_id = (head.get("Message-ID") or f"uid-{seq.decode()}").strip()
                if msg_id in seen_ids:
                    already += 1
                    continue
                wanted.append((seq, msg_id, received))

            # Pass 2 - full body, only for what survived.
            no_attach = 0
            results: list[EmailMessage] = []
            for seq, msg_id, received in wanted:
                # BODY.PEEK so reading does not flag the message as seen.
                typ, raw = conn.fetch(seq, "(BODY.PEEK[])")
                if typ != "OK" or not raw or not raw[0]:
                    continue
                msg = email.message_from_bytes(raw[0][1])
                saved = self._save_attachments(msg, seq, allowed_ext,
                                               write=not headers_only)
                if not saved:
                    no_attach += 1
                    continue

                results.append(EmailMessage(
                    message_id=msg_id,
                    subject=_decode(msg.get("Subject")),
                    sender=_decode(msg.get("From")),
                    received_at=received,
                    body=self._body_text(msg)[:20000],
                    attachments=[] if headers_only else saved,
                    is_unread=True,
                ))

            self.last_scan = (
                f"IMAP {self._user} @ {self._host}, folder '{self._folder}' "
                f"({total} message(s) total). {len(matched)} matched the date search"
                f"{' (unread only)' if unread_only else ''}; "
                f"{older} older than the window; "
                f"{already} already processed; "
                f"{no_attach} had no usable attachment; "
                f"{len(results)} queued for processing."
            )
            if not matched:
                self.last_scan += (" Nothing matched - check the folder name and "
                                   "that the invoice is recent.")
            return results
        finally:
            try:
                conn.logout()
            except Exception:
                pass

    @staticmethod
    def _received_at(msg) -> datetime:
        """Parse the Date header into an aware UTC datetime, never in the future.

        The header is written by the sender, so a forged far-future date would
        otherwise become the watcher's resume point and hide all later mail.
        """
        now = datetime.now(timezone.utc)
        try:
            dt = parsedate_to_datetime(msg.get("Date"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return min(dt.astimezone(timezone.utc), now)
        except Exception:
            return now

    @staticmethod
    def _body_text(msg) -> str:
        """Best-effort plain-text body."""
        if not msg.is_multipart():
            try:
                return (msg.get_payload(decode=True) or b"").decode(errors="replace")
            except Exception:
                return ""
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get_filename():
                try:
                    return (part.get_payload(decode=True) or b"").decode(errors="replace")
                except Exception:
                    continue
        return ""

    @staticmethod
    def _save_attachments(msg, uid: bytes, allowed_ext: set[str],
                          write: bool = True) -> list[Path]:
        """Write file attachments to the cache, one sub-folder per message.

        With ``write=False`` the matching attachments are still identified (so
        the caller gets a truthy count) but no bytes reach disk - the
        headers-only probe used by the "Test mailbox" button.
        """
        out: list[Path] = []
        digits = "".join(ch for ch in uid.decode(errors="replace") if ch.isdigit())
        bucket = ATTACHMENT_CACHE / f"imap-{digits or '0'}"
        for part in msg.walk():
            filename = part.get_filename()
            if not filename:
                continue
            fname = _safe(_decode(filename))
            if allowed_ext and _ext_of(fname) not in allowed_ext:
                continue
            try:
                payload = part.get_payload(decode=True)
            except Exception:
                continue
            if not payload:
                continue
            dest = bucket / fname
            if write:
                bucket.mkdir(parents=True, exist_ok=True)
                # Identical bytes already cached: keep the path, skip the write.
                if not (dest.exists() and dest.stat().st_size == len(payload)):
                    dest.write_bytes(payload)
            out.append(dest)
        return out
