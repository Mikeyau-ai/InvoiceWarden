"""Mailbox access with interchangeable backends.

* ``com``   - CLASSIC Outlook desktop via pywin32. No credentials, but needs a
              paid Microsoft 365 subscription and does not work with the new
              Outlook for Windows (no COM interface).
* ``graph`` - Microsoft Graph REST API with device-code OAuth2. The route for
              the new Outlook and outlook.com accounts.
* ``imap``  - generic IMAP (see :mod:`integrations.email_imap`) for Gmail,
              Fastmail and most providers that still accept an app password.

Every backend returns a list of :class:`EmailMessage`; attachments are written
to the attachment cache and referenced by path.

Two arguments shape how much work a fetch does:

``seen_ids``
    Message ids already recorded in ``processed_emails``. A backend must skip
    these *before* downloading their attachments - otherwise an invoice sitting
    in the inbox is re-downloaded on every poll for as long as it stays inside
    the lookback window.
``headers_only``
    Identify matching messages but do not download or write attachment bytes.
    Used by the Settings "Test mailbox" button, which only needs a count.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import ATTACHMENT_CACHE

log = logging.getLogger(__name__)

# On a fresh install (nothing processed yet) don't ingest the whole mailbox -
# only look this far back.
FIRST_SCAN_LOOKBACK_DAYS = 14

#: Cap on how many Graph pages a single fetch will walk (100 messages each).
MAX_GRAPH_PAGES = 10


@dataclass
class EmailMessage:
    """Normalised representation of one inbox item."""

    message_id: str
    subject: str
    sender: str
    received_at: datetime
    body: str
    attachments: list[Path] = field(default_factory=list)
    is_unread: bool = True


#: Windows device names that are unsafe as file names.
_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL",
                   *(f"COM{i}" for i in range(1, 10)),
                   *(f"LPT{i}" for i in range(1, 10))}


def _safe(name: str) -> str:
    """Sanitise a filename for the local cache."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "attachment"
    # Windows treats CON, NUL, COM1... (with any extension) as devices, so
    # writing a file with that name would hit the device, not the disk.
    if safe.split(".")[0].upper() in _RESERVED_NAMES:
        safe = "file_" + safe
    return safe


def cache_bucket(message_id: str) -> Path:
    """Cache sub-folder holding one message's attachments.

    Named from a hash of the WHOLE id: Outlook and Graph ids share a long
    prefix across every message in a mailbox, so a truncated id put the whole
    mailbox in one folder and same-named attachments overwrote each other.
    """
    digest = hashlib.sha1(str(message_id).encode("utf-8", "replace")).hexdigest()
    return ATTACHMENT_CACHE / digest[:24]


#: Folder names Graph accepts directly in a mailFolders path (lower-cased).
_GRAPH_WELL_KNOWN = {"inbox", "archive", "drafts", "sentitems", "deleteditems",
                     "junkemail", "outbox"}


def unique_name(fname: str, used: set[str]) -> str:
    """``fname``, or a numbered variant if this message already used it.

    Two attachments on one email can share a file name; without this the
    second overwrote the first in the cache.
    """
    name, n = fname, 1
    while name.lower() in used:
        n += 1
        name = f"{n}_{fname}"
    used.add(name.lower())
    return name


def _ext_of(filename: str) -> str:
    """Lower-case extension of a filename, without the dot ('' if none)."""
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


class OutlookBackend:
    """Base interface. ``fetch`` is the only method the watcher calls."""

    #: human-readable summary of the last fetch, shown when it returns nothing
    last_scan: str = ""
    #: ids the last fetch looked at and found no wanted attachment on. The
    #: watcher remembers these so they are not downloaded again every poll.
    skipped_ids: tuple = ()

    def fetch(self, since: datetime | None, unread_only: bool,
              allowed_ext: set[str], seen_ids: set[str] = frozenset(),
              headers_only: bool = False) -> list[EmailMessage]:
        """Return new messages carrying a wanted attachment. See module docs."""
        raise NotImplementedError


# --------------------------------------------------------------------------
# COM backend
# --------------------------------------------------------------------------
class ComBackend(OutlookBackend):
    """Reads the local Outlook profile through the COM automation model."""

    def __init__(self, account: str = "", folder: str = "Inbox") -> None:
        """Target one account/folder of the local Outlook profile."""
        self._account = (account or "").strip()
        self._folder = (folder or "").strip() or "Inbox"
        self.last_scan = ""
        self.skipped_ids = []

    def _inbox(self):
        """Resolve the target folder. Returns (folder, human_description)."""
        import win32com.client  # imported lazily so non-Windows import works

        try:
            outlook = win32com.client.Dispatch("Outlook.Application")
        except Exception as exc:
            # -2147221005 "Invalid class string" = Outlook.Application is not
            # registered. The NEW Outlook for Windows (the Store app) has no
            # COM automation interface at all, so this backend cannot work.
            raise RuntimeError(
                "Cannot reach the Outlook desktop app (COM). This usually means "
                "you have the NEW Outlook for Windows (the Microsoft Store app), "
                "which does not support COM automation. Either switch that app's "
                "'New Outlook' toggle OFF to use classic Outlook, or set "
                "Settings > Outlook > Backend to 'graph' and fill in the "
                f"Microsoft Graph fields. (underlying error: {exc})"
            ) from exc

        ns = outlook.GetNamespace("MAPI")
        note = ""

        if self._account:
            want = self._account.lower()
            seen: list[str] = []

            # 1. Exact SMTP-address match on a configured account.
            try:
                for acct in ns.Accounts:
                    smtp = (getattr(acct, "SmtpAddress", "") or "").strip()
                    if smtp:
                        seen.append(smtp)
                    if smtp.lower() == want:
                        root = acct.DeliveryStore.GetRootFolder()
                        f = self._find_folder(root, self._folder) or root.Folders["Inbox"]
                        return f, f"account '{smtp}', folder '{f.Name}'"
            except Exception:
                pass

            # 2. Substring match on account SMTP / display name (handles
            #    aliases and "Name <addr>" style entries).
            try:
                for acct in ns.Accounts:
                    hay = f"{getattr(acct, 'SmtpAddress', '')} {getattr(acct, 'DisplayName', '')}".lower()
                    if want in hay or want.split("@")[0] in hay:
                        root = acct.DeliveryStore.GetRootFolder()
                        f = self._find_folder(root, self._folder) or root.Folders["Inbox"]
                        return f, f"account '{getattr(acct, 'DisplayName', '?')}', folder '{f.Name}'"
            except Exception:
                pass

            # 3. Substring match on store display names.
            for store in ns.Stores:
                dn = (store.DisplayName or "").strip()
                if dn:
                    seen.append(dn)
                if want in dn.lower() or want.split("@")[0] in dn.lower():
                    root = store.GetRootFolder()
                    f = self._find_folder(root, self._folder) or root.Folders["Inbox"]
                    return f, f"store '{dn}', folder '{f.Name}'"

            available = ", ".join(dict.fromkeys(seen)) or "(none found)"
            note = (f"'{self._account}' did not match any Outlook account or data "
                    f"file, so the DEFAULT mailbox was used instead. Outlook on "
                    f"this PC offers: {available}. Put one of those in "
                    f"'Mailbox / account to monitor', or leave it blank to use "
                    f"the default. ")

        inbox = ns.GetDefaultFolder(6)  # 6 = olFolderInbox
        if self._folder.lower() != "inbox":
            found = self._find_folder(inbox.Parent, self._folder)
            if found:
                return found, note + f"default account, folder '{found.Name}'"
            note += f"folder '{self._folder}' not found; using Inbox. "
        try:
            owner = inbox.Store.DisplayName
        except Exception:
            owner = "default"
        return inbox, note + f"Scanned '{owner}' Inbox"

    @staticmethod
    def _find_folder(root, name: str):
        """Depth-first search for a folder by display name."""
        try:
            for f in root.Folders:
                if (f.Name or "").lower() == name.lower():
                    return f
                sub = ComBackend._find_folder(f, name)
                if sub:
                    return sub
        except Exception:
            pass
        return None

    def fetch(self, since, unread_only, allowed_ext, seen_ids=frozenset(),
              headers_only=False):
        """Scan the resolved folder newest-first and save wanted attachments."""
        import pywintypes  # noqa: F401  (ensures pywin32 present)

        inbox, where = self._inbox()
        # First run (no `since`): only look back a bounded window, not forever.
        floor = since or (datetime.now(timezone.utc)
                          - timedelta(days=FIRST_SCAN_LOOKBACK_DAYS))

        items = inbox.Items
        try:
            total_in_folder = items.Count
        except Exception:
            total_in_folder = -1
        try:
            items.Sort("[ReceivedTime]", True)   # newest first
        except Exception:
            pass

        checked = older = read_skip = no_attach = already = 0
        results: list[EmailMessage] = []
        self.skipped_ids = []
        for idx, item in enumerate(items):
            if idx >= 1000:                       # hard cap on how far we scan
                break
            try:
                if getattr(item, "Class", 43) != 43:  # 43 = olMail
                    continue
                checked += 1
                received = item.ReceivedTime
                # Outlook's ReceivedTime is local wall-clock time; a naive
                # datetime is interpreted as local by astimezone().
                received_dt = datetime(
                    received.year, received.month, received.day,
                    received.hour, received.minute, received.second,
                ).astimezone(timezone.utc)
                if received_dt <= floor:
                    older += 1
                    if older > 15:               # sorted desc - we're past the window
                        break
                    continue

                unread = bool(getattr(item, "UnRead", False))
                if unread_only and not unread:
                    read_skip += 1
                    continue

                # Skip already-handled mail BEFORE saving any attachment bytes.
                entry_id = str(item.EntryID)
                if entry_id in seen_ids:
                    already += 1
                    continue

                saved: list[Path] = []
                used: set[str] = set()
                matched = 0
                # One sub-folder per message keeps the original file name clean
                # (the parser uses it as a hint) while staying collision-free.
                bucket = cache_bucket(entry_id)
                for att in item.Attachments:
                    fname = _safe(att.FileName)
                    if allowed_ext and _ext_of(fname) not in allowed_ext:
                        continue
                    matched += 1
                    if headers_only:
                        continue
                    bucket.mkdir(parents=True, exist_ok=True)
                    dest = bucket / unique_name(fname, used)
                    att.SaveAsFile(str(dest))
                    saved.append(dest)

                if not matched:
                    no_attach += 1
                    self.skipped_ids.append(entry_id)
                    continue

                results.append(EmailMessage(
                    message_id=entry_id,
                    subject=str(item.Subject or ""),
                    sender=str(getattr(item, "SenderEmailAddress", "") or ""),
                    received_at=received_dt,
                    body=str(item.Body or "")[:20000],
                    attachments=saved,
                    is_unread=unread,
                ))
            except Exception as exc:  # keep scanning other mail
                log.exception("COM item read failed: %s", exc)

        in_window = checked - older
        self.last_scan = (
            f"{where}. Folder holds {total_in_folder} item(s); "
            f"{in_window} within the last "
            f"{FIRST_SCAN_LOOKBACK_DAYS} days (or since the last check); "
            f"{read_skip} skipped as already-read "
            f"(unread-only is {'ON' if unread_only else 'off'}); "
            f"{already} already processed; "
            f"{no_attach} had no usable attachment; "
            f"{len(results)} queued for processing."
        )
        if total_in_folder == 0:
            self.last_scan += (" That folder is EMPTY - it is probably not the "
                               "mailbox your test email arrived in.")
        elif in_window == 0 and total_in_folder > 0:
            self.last_scan += (" Nothing recent enough - check you are pointed "
                               "at the right mailbox/folder.")
        return results


# --------------------------------------------------------------------------
# Microsoft Graph backend
# --------------------------------------------------------------------------
class GraphBackend(OutlookBackend):
    """Cloud mailbox access via Graph, authenticated by device-code sign-in.

    This is the backend to use with the NEW Outlook for Windows and with
    outlook.com personal accounts - neither supports COM automation, and
    Microsoft disabled IMAP/app-password (Basic) auth for personal mailboxes
    on 2024-09-16, so OAuth2 is the only remaining option.
    """

    GRAPH = "https://graph.microsoft.com/v1.0"

    def __init__(self, settings, account: str = "", folder: str = "Inbox") -> None:
        """Target one mailbox/folder; the token comes from the cached sign-in."""
        self._settings = settings
        self._account = (account or "").strip()
        self._folder = (folder or "").strip() or "Inbox"
        self._session = None
        self.last_scan = ""
        self.skipped_ids = []

    def _token(self) -> str:
        """Access token from the cached device-code sign-in."""
        from integrations.graph_auth import access_token

        return access_token(self._settings)

    def _http(self):
        """Lazily created session so all Graph calls reuse one TLS connection."""
        if self._session is None:
            import requests

            self._session = requests.Session()
        return self._session

    @staticmethod
    def _parse_dt(value: str) -> datetime:
        """Graph timestamps are ISO-8601 with a 'Z' suffix Python won't take."""
        return datetime.fromisoformat((value or "").replace("Z", "+00:00"))

    def _folder_ref(self, http, base: str, headers: dict) -> str:
        """Path segment for the configured folder: a well-known name or an id.

        Graph only accepts well-known names (inbox, archive, ...) or folder ids
        in the path, so any other display name is looked up among the top-level
        folders and the Inbox's direct sub-folders. Unmatched names are passed
        through unchanged, which lets a pasted folder id keep working.
        """
        name = self._folder
        key = name.replace(" ", "").lower()
        if key in _GRAPH_WELL_KNOWN:
            return key
        for url in (f"{base}/mailFolders", f"{base}/mailFolders/inbox/childFolders"):
            resp = http.get(url, headers=headers, timeout=30,
                            params={"$top": "200", "$select": "id,displayName"})
            if not resp.ok:
                continue
            for folder in resp.json().get("value", []):
                if (folder.get("displayName") or "").strip().lower() == name.lower():
                    return folder["id"]
        return name

    def fetch(self, since, unread_only, allowed_ext, seen_ids=frozenset(),
              headers_only=False):
        """List the folder, then download attachments for unseen messages only."""
        http = self._http()
        # Ask for plain-text bodies: the default is HTML, whose markup and CSS
        # eat the AI prompt's 6000-character body budget.
        headers = {"Authorization": f"Bearer {self._token()}",
                   "Prefer": 'outlook.body-content-type="text"'}
        # /me is the signed-in mailbox. A different address only works with an
        # org tenant plus admin consent, so only use it when explicitly set.
        base = f"{self.GRAPH}/users/{self._account}" if self._account else f"{self.GRAPH}/me"
        select = ("id,subject,from,receivedDateTime,isRead,"
                  "hasAttachments,body")
        url = f"{base}/mailFolders/{self._folder_ref(http, base, headers)}/messages"

        # First run (no `since`): only look back a bounded window, not forever.
        floor = (since or (datetime.now(timezone.utc)
                           - timedelta(days=FIRST_SCAN_LOOKBACK_DAYS))).astimezone(timezone.utc)

        # Graph rejects some filter+sort combinations outright with
        # "InefficientFilter: The restriction or sort order is too complex for
        # this operation". Try progressively simpler queries and do the
        # remaining narrowing locally; correctness never depends on the server
        # honouring the filter. Attachments are NOT expanded here: that put
        # every attachment's bytes in the list response, so mail already
        # handled was downloaded again on every poll.
        base_params: dict = {"$top": "100", "$select": select}
        f = [f"receivedDateTime ge {floor.strftime('%Y-%m-%dT%H:%M:%SZ')}"]
        if unread_only:
            f.append("isRead eq false")
        attempts: list[dict] = [{**base_params, "$filter": " and ".join(f)}]
        if unread_only:
            attempts.append({**base_params, "$filter": "isRead eq false"})
        # Last resort: newest N, filtered entirely on this side.
        attempts.append({**base_params, "$orderby": "receivedDateTime desc"})

        value = None
        used: dict = {}
        last_err = ""
        for params in attempts:
            resp = http.get(url, headers=headers, params=params, timeout=30)
            if resp.status_code == 404:
                raise RuntimeError(
                    f"Graph could not find folder '{self._folder}'. Use Inbox, "
                    "or the exact name of a top-level folder or of a folder "
                    "directly inside the Inbox.")
            if resp.ok:
                body = resp.json()
                value, used = body.get("value", []), params
                # Walk @odata.nextLink so a long outage does not silently
                # truncate the back-check at the first 100 messages.
                pages = 1
                next_link = body.get("@odata.nextLink")
                while next_link and pages < MAX_GRAPH_PAGES:
                    # Newest-first listing: once a page reaches mail older
                    # than the window there is nothing further to find.
                    if "$orderby" in params and value and \
                            self._parse_dt(value[-1]["receivedDateTime"]) < floor:
                        next_link = None
                        break
                    nxt = http.get(next_link, headers=headers, timeout=30)
                    if not nxt.ok:
                        log.warning("Graph pagination stopped at page %d (%s).",
                                    pages, nxt.status_code)
                        break
                    body = nxt.json()
                    value.extend(body.get("value", []))
                    next_link = body.get("@odata.nextLink")
                    pages += 1
                if next_link:
                    log.warning("Graph result truncated at %d pages.", MAX_GRAPH_PAGES)
                break
            last_err = f"{resp.status_code}: {resp.text[:200]}"
            log.warning("Graph query rejected (%s); trying a simpler one.",
                        last_err)
        if value is None:
            raise RuntimeError(f"Graph error {last_err}")

        # Apply locally whatever the server was not asked to enforce. The date
        # window is always re-checked: it is cheap and never trusts the server.
        value = [m for m in value if m.get("hasAttachments")]
        if unread_only:
            value = [m for m in value if not m.get("isRead", True)]
        # Parse each timestamp once and carry it alongside the message.
        dated = [(m, self._parse_dt(m["receivedDateTime"])) for m in value]
        dated = [(m, dt) for m, dt in dated if dt >= floor]

        results: list[EmailMessage] = []
        self.skipped_ids = []
        already = 0
        for msg, received_dt in dated:
            # Skip already-handled mail BEFORE fetching any attachment bytes.
            if msg["id"] in seen_ids:
                already += 1
                continue
            saved: list[Path] = []
            if not headers_only:
                saved = self._download_attachments(base, msg["id"], headers, allowed_ext)
                if not saved:
                    self.skipped_ids.append(msg["id"])
                    continue
            results.append(EmailMessage(
                message_id=msg["id"],
                subject=msg.get("subject", ""),
                sender=(msg.get("from", {}).get("emailAddress", {}) or {}).get("address", ""),
                received_at=received_dt,
                body=(msg.get("body", {}) or {}).get("content", "")[:20000],
                attachments=saved,
                is_unread=not msg.get("isRead", True),
            ))
        who = self._account or "the signed-in mailbox"
        self.last_scan = (
            f"Graph: folder '{self._folder}' of {who}. Server query "
            f"[{used.get('$filter') or used.get('$orderby', 'newest 100')}]; "
            f"{len(dated)} message(s) with an attachment in range; "
            f"{already} already processed; "
            f"{len(results)} queued for processing."
        )
        if not dated:
            self.last_scan += (" Nothing matched - only emails WITH "
                               "attachments are processed, and only those "
                               "newer than the last check.")
        return results

    def _save_expanded(self, msg_id: str, attachments: list, allowed_ext: set[str]) -> list[Path]:
        """Write attachments whose bytes arrived inline (base64 ``contentBytes``)."""
        import base64

        out: list[Path] = []
        used: set[str] = set()
        bucket = cache_bucket(msg_id)
        for att in attachments or []:
            if att.get("@odata.type") != "#microsoft.graph.fileAttachment":
                continue
            fname = _safe(att.get("name", "attachment"))
            if allowed_ext and _ext_of(fname) not in allowed_ext:
                continue
            raw = att.get("contentBytes")
            if not raw:
                continue
            data = base64.b64decode(raw)
            bucket.mkdir(parents=True, exist_ok=True)
            dest = bucket / unique_name(fname, used)
            # Identical bytes already cached: keep the path, skip the write.
            if not (dest.exists() and dest.stat().st_size == len(data)):
                dest.write_bytes(data)
            out.append(dest)
        return out

    def _download_attachments(self, base, msg_id, headers, allowed_ext):
        """Save one message's wanted attachments; [] when it has none.

        The attachments are listed by name first and only the wanted ones are
        downloaded, as raw bytes streamed to disk. If the server refuses the
        name-only listing, fall back to one call that returns everything.
        """
        http = self._http()
        url = f"{base}/messages/{msg_id}/attachments"
        listing = http.get(url, headers=headers, timeout=30,
                           params={"$select": "id,name,size"})
        if not listing.ok:
            resp = http.get(url, headers=headers, timeout=30)
            resp.raise_for_status()
            return self._save_expanded(msg_id, resp.json().get("value", []), allowed_ext)

        out: list[Path] = []
        used: set[str] = set()
        bucket = cache_bucket(msg_id)
        for att in listing.json().get("value", []):
            # Only real files: an attached email or a cloud link has no bytes.
            if att.get("@odata.type", "#microsoft.graph.fileAttachment") \
                    != "#microsoft.graph.fileAttachment":
                continue
            fname = _safe(att.get("name") or "attachment")
            if allowed_ext and _ext_of(fname) not in allowed_ext:
                continue
            resp = http.get(f"{url}/{att['id']}/$value", headers=headers,
                            timeout=60, stream=True)
            resp.raise_for_status()
            bucket.mkdir(parents=True, exist_ok=True)
            dest = bucket / unique_name(fname, used)
            with dest.open("wb") as fh:
                for chunk in resp.iter_content(1 << 16):
                    fh.write(chunk)
            out.append(dest)
        return out


def build_backend(settings) -> OutlookBackend:
    """Backend for the legacy single-mailbox settings.

    Kept for the Settings "Test mailbox" button and for installs that have not
    added any mailbox rows yet; the watcher uses :func:`build_account_backends`.
    """
    account = settings.get("outlook.account", "")
    folder = settings.get("outlook.folder", "Inbox")
    backend = settings.get("outlook.backend", "com")

    if backend == "graph":
        return GraphBackend(settings, account=account, folder=folder)
    if backend == "imap":
        from integrations.email_imap import ImapBackend

        return ImapBackend(
            host=settings.get("imap.host"),
            port=settings.get_int("imap.port", 993),
            username=settings.get("imap.username") or account,
            password=settings.get("imap.password"),
            folder=settings.get("imap.folder") or folder or "INBOX",
        )
    return ComBackend(account=account, folder=folder)


def build_backend_for(settings, db, row) -> OutlookBackend:
    """Backend for one ``mail_accounts`` row.

    Each mailbox carries its own credentials - a Graph token cache or an IMAP
    password - because those cannot be shared between accounts the way a single
    COM profile can.
    """
    backend = (row["backend"] or "com").strip()
    address = (row["address"] or "").strip()
    folder = (row["folder"] or "").strip()

    if backend == "graph":
        from integrations.graph_auth import AccountTokenStore

        # /me is the mailbox that signed in, so the address is not sent as a
        # user id - it is only a label unless a tenant granted wider access.
        return GraphBackend(AccountTokenStore(settings, db, row["id"]),
                            account="", folder=folder or "Inbox")
    if backend == "imap":
        from integrations.email_imap import ImapBackend

        try:
            port = int(row["imap_port"] or 993)
        except (TypeError, ValueError):
            port = 993
        return ImapBackend(
            host=row["imap_host"],
            port=port,
            username=(row["imap_username"] or address),
            password=settings.decrypt_value(row["imap_password"]),
            folder=folder or "INBOX",
        )
    return ComBackend(account=address, folder=folder or "Inbox")


def account_backends(settings, db):
    """(row, backend) for every enabled mailbox.

    Falls back to the single-mailbox settings when no rows exist, so upgrading
    an existing install keeps working with nothing to configure.
    """
    rows = db.list_mail_accounts(enabled_only=True)
    if not rows:
        return [(None, build_backend(settings))]
    return [(r, build_backend_for(settings, db, r)) for r in rows]
