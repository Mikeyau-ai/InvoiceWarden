"""SQLite persistence layer for InvoiceWarden.

One :class:`Database` instance is shared across threads (GUI + watcher), so all
access goes through a single connection guarded by a lock. Row factory returns
dict-like ``sqlite3.Row`` objects.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key       TEXT PRIMARY KEY,
    value     TEXT NOT NULL DEFAULT '',
    encrypted INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS customers (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    name                  TEXT NOT NULL UNIQUE COLLATE NOCASE,
    aliases               TEXT NOT NULL DEFAULT '[]',   -- JSON list of alt names
    servicem8_enabled     INTEGER NOT NULL DEFAULT 0,
    myob_enabled          INTEGER NOT NULL DEFAULT 0,   -- vestigial: routing uses
                                                        -- the two generic toggles
    accounting_enabled    INTEGER NOT NULL DEFAULT 0,   -- generic accounting provider
    file_types            TEXT NOT NULL DEFAULT 'pdf',  -- comma list
    servicem8_client_uuid TEXT NOT NULL DEFAULT '',
    accounting_contact_id TEXT NOT NULL DEFAULT '',
    notes                 TEXT NOT NULL DEFAULT '',
    reviewed              INTEGER NOT NULL DEFAULT 1,  -- 0 = auto-added, unseen
    created_at            TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);

-- Derived lookup index over customers.aliases (which stays the source of truth
-- for the UI). Rebuilt from the JSON on startup and on every customer write, so
-- an alias match is one indexed query instead of a full scan + JSON parse.
CREATE TABLE IF NOT EXISTS customer_aliases (
    customer_id INTEGER NOT NULL,
    alias       TEXT NOT NULL COLLATE NOCASE,
    PRIMARY KEY (customer_id, alias)
);
CREATE INDEX IF NOT EXISTS idx_customer_alias ON customer_aliases(alias COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS activity_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    level         TEXT NOT NULL DEFAULT 'INFO',
    customer_name TEXT NOT NULL DEFAULT '',
    invoice_ref   TEXT NOT NULL DEFAULT '',
    platform      TEXT NOT NULL DEFAULT '',
    action        TEXT NOT NULL DEFAULT '',
    filename      TEXT NOT NULL DEFAULT '',
    message       TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS error_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    stage         TEXT NOT NULL DEFAULT '',
    customer_name TEXT NOT NULL DEFAULT '',
    invoice_ref   TEXT NOT NULL DEFAULT '',
    filename      TEXT NOT NULL DEFAULT '',
    error         TEXT NOT NULL DEFAULT '',
    payload       TEXT NOT NULL DEFAULT '',   -- JSON snapshot for retry
    retry_count   INTEGER NOT NULL DEFAULT 0,
    resolved      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS pending_invoices (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TEXT NOT NULL,
    extracted_name TEXT NOT NULL DEFAULT '',
    job_number     TEXT NOT NULL DEFAULT '',
    invoice_ref    TEXT NOT NULL DEFAULT '',
    email_subject  TEXT NOT NULL DEFAULT '',
    email_from     TEXT NOT NULL DEFAULT '',
    file_path      TEXT NOT NULL DEFAULT '',
    raw_json       TEXT NOT NULL DEFAULT '{}',
    status         TEXT NOT NULL DEFAULT 'pending_new_customer'
);

-- One row per monitored mailbox. Each carries its own credentials: a Graph
-- token cache or an IMAP password, since those are per-account and cannot be
-- shared the way a COM profile can.
CREATE TABLE IF NOT EXISTS mail_accounts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    position      INTEGER NOT NULL DEFAULT 0,
    enabled       INTEGER NOT NULL DEFAULT 1,
    backend       TEXT NOT NULL DEFAULT 'graph',   -- com | graph | imap
    address       TEXT NOT NULL DEFAULT '',
    folder        TEXT NOT NULL DEFAULT '',
    graph_cache   TEXT NOT NULL DEFAULT '',        -- encrypted MSAL cache
    imap_host     TEXT NOT NULL DEFAULT '',
    imap_port     TEXT NOT NULL DEFAULT '993',
    imap_username TEXT NOT NULL DEFAULT '',
    imap_password TEXT NOT NULL DEFAULT '',        -- encrypted
    created_at    TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS processed_emails (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id  TEXT NOT NULL UNIQUE,
    received_at TEXT NOT NULL DEFAULT '',
    processed_at TEXT NOT NULL,
    subject     TEXT NOT NULL DEFAULT '',
    result      TEXT NOT NULL DEFAULT ''
);

-- One row per (document, platform) that was successfully uploaded. Used to
-- stop the same invoice/credit being pushed twice, even from a different
-- email or a manual retry.
CREATE TABLE IF NOT EXISTS processed_documents (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    file_hash     TEXT NOT NULL DEFAULT '',   -- sha256 of the attachment bytes
    customer_name TEXT NOT NULL DEFAULT '',
    invoice_ref   TEXT NOT NULL DEFAULT '',
    doc_type      TEXT NOT NULL DEFAULT 'invoice',   -- invoice | credit
    platform      TEXT NOT NULL DEFAULT '',
    remote_id     TEXT NOT NULL DEFAULT '',
    job_number    TEXT NOT NULL DEFAULT '',   -- job this was filed against
    filename      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_procdoc_hash ON processed_documents(file_hash, platform);
CREATE INDEX IF NOT EXISTS idx_procdoc_ref
    ON processed_documents(customer_name, invoice_ref, doc_type, platform);
"""

#: How many recent message ids :meth:`Database.recent_processed_ids` returns.
#: The set is only a prefilter - :meth:`is_email_processed` remains the
#: authoritative check - so bounding it cannot cause a message to be
#: reprocessed, only to be fetched once more than strictly necessary.
_SEEN_ID_WINDOW = 5000

#: Default cap for :meth:`Database.trim_activity_log`.
ACTIVITY_LOG_KEEP = 20_000


def _utcnow() -> str:
    """ISO-8601 UTC timestamp used for every stored row."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class _ClosedCursor:
    """Stand-in returned by Database._exec after close() so a racing worker
    thread on shutdown gets a harmless no-op instead of an exception."""

    lastrowid = 0
    rowcount = 0

    def fetchall(self):
        """No rows: the connection is gone."""
        return []

    def fetchone(self):
        """No row: the connection is gone."""
        return None


class Database:
    """Serialized access to the InvoiceWarden SQLite file."""

    def __init__(self, path: Path) -> None:
        """Open (creating if needed) the database and apply the schema."""
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._closed = False
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA foreign_keys=ON;")
            self._conn.executescript(SCHEMA)
            self._conn.commit()
        self._migrate()
        self._rebuild_alias_index()

    def _migrate(self) -> None:
        """Add columns introduced after a database was first created.

        SQLite has no "ADD COLUMN IF NOT EXISTS", and CREATE TABLE IF NOT
        EXISTS silently leaves an existing table alone, so new columns have to
        be applied explicitly.
        """
        with self._lock:
            have = {r["name"] for r in
                    self._conn.execute("PRAGMA table_info(customers)")}
            cols = {r["name"] for r in
                    self._conn.execute("PRAGMA table_info(processed_documents)")}
            if "job_number" not in cols:
                self._conn.execute("ALTER TABLE processed_documents ADD COLUMN "
                                   "job_number TEXT NOT NULL DEFAULT ''")
                self._conn.commit()
            if "reviewed" not in have:
                # Existing customers were added deliberately, so mark them seen.
                self._conn.execute("ALTER TABLE customers ADD COLUMN "
                                   "reviewed INTEGER NOT NULL DEFAULT 1")
                self._conn.commit()

    # -- low level helpers -------------------------------------------------
    def _exec(self, sql: str, params: Iterable[Any] = ()):
        """Run one write statement and commit. Returns the cursor."""
        with self._lock:
            if self._closed:                 # a leaked worker thread on shutdown
                return _ClosedCursor()
            cur = self._conn.execute(sql, tuple(params))
            self._conn.commit()
            return cur

    def _query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        """Run one read statement and return all rows."""
        with self._lock:
            if self._closed:
                return []
            return self._conn.execute(sql, tuple(params)).fetchall()

    # -- settings -------------------------------------------------------
    def get_setting(self, key: str, default: str = "") -> tuple[str, bool, bool]:
        """Return ``(raw_value, encrypted_flag, found)`` for a setting key.

        ``found`` distinguishes "stored as an empty string" from "never set",
        which the caller needs in order to decide whether a default applies.
        Returning it here avoids a second existence query.
        """
        rows = self._query("SELECT value, encrypted FROM settings WHERE key=?", (key,))
        if not rows:
            return default, False, False
        return rows[0]["value"], bool(rows[0]["encrypted"]), True

    def set_setting(self, key: str, value: str, encrypted: bool = False) -> None:
        """Upsert a setting."""
        self._exec(
            "INSERT INTO settings(key,value,encrypted) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, encrypted=excluded.encrypted",
            (key, value, int(encrypted)),
        )

    def all_settings(self) -> dict[str, sqlite3.Row]:
        """Every settings row keyed by name (used for bulk diagnostics)."""
        return {r["key"]: r for r in self._query("SELECT * FROM settings")}

    # -- customers ----------------------------------------------------
    #: Orderings offered by the Customers tab.
    CUSTOMER_SORTS = {
        "Name (A-Z)": "name COLLATE NOCASE ASC",
        "Name (Z-A)": "name COLLATE NOCASE DESC",
        "Newest first": "created_at DESC, id DESC",
        "Oldest first": "created_at ASC, id ASC",
        "New / unreviewed first": "reviewed ASC, created_at DESC",
    }

    def list_customers_sorted(self, sort: str = "Name (A-Z)",
                              new_only: bool = False) -> list[sqlite3.Row]:
        """Customers in the requested order, optionally only unreviewed ones."""
        order = self.CUSTOMER_SORTS.get(sort, self.CUSTOMER_SORTS["Name (A-Z)"])
        sql = "SELECT * FROM customers"
        if new_only:
            sql += " WHERE reviewed=0"
        return self._query(f"{sql} ORDER BY {order}")

    def count_unreviewed_customers(self) -> int:
        """How many auto-added customers have not been reviewed yet."""
        rows = self._query("SELECT COUNT(*) AS n FROM customers WHERE reviewed=0")
        return int(rows[0]["n"]) if rows else 0

    def count_activity(self, actions: tuple[str, ...], since_iso: str) -> int:
        """How many activity rows with one of ``actions`` were logged at or after
        ``since_iso`` (a UTC ISO-8601 string, the same format ``ts`` is stored in)."""
        marks = ",".join("?" for _ in actions)
        rows = self._query(
            f"SELECT COUNT(*) AS n FROM activity_log WHERE action IN ({marks}) AND ts >= ?",
            (*actions, since_iso))
        return int(rows[0]["n"]) if rows else 0

    def mark_customer_reviewed(self, customer_id: int, reviewed: bool = True) -> None:
        """Clear (or restore) the 'new' badge on one customer."""
        self._exec("UPDATE customers SET reviewed=? WHERE id=?",
                   (1 if reviewed else 0, customer_id))

    def list_customers(self) -> list[sqlite3.Row]:
        """All customer profiles, ordered by name."""
        return self._query("SELECT * FROM customers ORDER BY name COLLATE NOCASE")

    def get_customer(self, cid: int) -> sqlite3.Row | None:
        """One customer profile by id, or None."""
        rows = self._query("SELECT * FROM customers WHERE id=?", (cid,))
        return rows[0] if rows else None

    def all_file_types(self) -> set[str]:
        """Union of every customer's enabled file types.

        Used as a cheap mailbox-side prefilter so attachments no customer could
        ever want are never downloaded. Empty when no customer is configured,
        which the caller treats as "no filtering".
        """
        out: set[str] = set()
        for row in self._query("SELECT file_types FROM customers"):
            out.update(t.strip().lower() for t in (row["file_types"] or "").split(",")
                       if t.strip())
        return out

    def _rebuild_alias_index(self) -> None:
        """Regenerate ``customer_aliases`` from the JSON on each customer row.

        Done once at startup so the derived table is always consistent with the
        source column, including after an upgrade from a build that predates it
        or a hand-edit of the database.
        """
        rows = self._query("SELECT id, aliases FROM customers")
        with self._lock:
            if self._closed:
                return
            self._conn.execute("DELETE FROM customer_aliases")
            for row in rows:
                self._conn.executemany(
                    "INSERT OR IGNORE INTO customer_aliases(customer_id, alias) VALUES(?,?)",
                    [(row["id"], a) for a in self._decode_aliases(row["aliases"])],
                )
            self._conn.commit()

    @staticmethod
    def _decode_aliases(raw: str) -> list[str]:
        """Parse the stored alias JSON into a list of non-blank strings."""
        try:
            values = json.loads(raw or "[]")
        except (ValueError, TypeError):
            return []
        return [str(a).strip() for a in values if str(a).strip()]

    def _sync_aliases(self, cid: int, aliases: list[str]) -> None:
        """Replace the alias index rows for one customer."""
        self._exec("DELETE FROM customer_aliases WHERE customer_id=?", (cid,))
        with self._lock:
            if self._closed:
                return
            self._conn.executemany(
                "INSERT OR IGNORE INTO customer_aliases(customer_id, alias) VALUES(?,?)",
                [(cid, a.strip()) for a in aliases if a.strip()],
            )
            self._conn.commit()

    def find_customer_by_name(self, name: str) -> sqlite3.Row | None:
        """Match on canonical name first, then the alias index (case-insensitive)."""
        name_norm = (name or "").strip()
        if not name_norm:
            return None
        rows = self._query("SELECT * FROM customers WHERE name=? COLLATE NOCASE",
                           (name_norm,))
        if rows:
            return rows[0]
        rows = self._query(
            "SELECT c.* FROM customers c "
            "JOIN customer_aliases a ON a.customer_id = c.id "
            "WHERE a.alias = ? COLLATE NOCASE LIMIT 1",
            (name_norm,),
        )
        return rows[0] if rows else None

    def upsert_customer(self, data: dict[str, Any]) -> int:
        """Insert or update a customer profile. ``data`` may contain ``id``."""
        now = _utcnow()
        aliases = [str(a).strip() for a in data.get("aliases", []) if str(a).strip()]
        fields = dict(
            name=data["name"].strip(),
            aliases=json.dumps(aliases),
            servicem8_enabled=int(bool(data.get("servicem8_enabled"))),
            accounting_enabled=int(bool(data.get("accounting_enabled"))),
            file_types=",".join(data.get("file_types", ["pdf"])) or "pdf",
            servicem8_client_uuid=data.get("servicem8_client_uuid", ""),
            accounting_contact_id=data.get("accounting_contact_id", ""),
            notes=data.get("notes", ""),
            updated_at=now,
        )
        # Only set on insert (or when explicitly passed) so editing a customer
        # in the UI never silently re-flags it as new.
        if "reviewed" in data:
            fields["reviewed"] = int(bool(data["reviewed"]))
        cid = data.get("id")
        if cid:
            cols = ", ".join(f"{k}=?" for k in fields)
            self._exec(f"UPDATE customers SET {cols} WHERE id=?", (*fields.values(), cid))
        else:
            fields["created_at"] = now
            cols = ", ".join(fields)
            ph = ", ".join("?" for _ in fields)
            cur = self._exec(f"INSERT INTO customers({cols}) VALUES({ph})",
                             tuple(fields.values()))
            cid = int(cur.lastrowid)
        self._sync_aliases(cid, aliases)
        return cid

    def delete_customer(self, cid: int) -> None:
        """Remove a customer profile and its alias index rows."""
        self._exec("DELETE FROM customer_aliases WHERE customer_id=?", (cid,))
        self._exec("DELETE FROM customers WHERE id=?", (cid,))

    # -- logs --------------------------------------------------------
    def add_activity(self, **kw: Any) -> None:
        """Append one row to the activity log."""
        kw.setdefault("ts", _utcnow())
        keys = ("ts", "level", "customer_name", "invoice_ref", "platform",
                "action", "filename", "message")
        vals = [kw.get(k, "") for k in keys]
        self._exec(
            f"INSERT INTO activity_log({','.join(keys)}) VALUES({','.join('?' for _ in keys)})",
            vals,
        )

    def search_activity(self, term: str = "", limit: int = 500) -> list[sqlite3.Row]:
        """Newest activity rows, optionally filtered by a free-text term.

        The blank-term case is the default view and is served by a plain
        descending scan of the primary key rather than six ``LIKE '%%'``
        predicates over the whole table.
        """
        if not term:
            return self._query(
                "SELECT * FROM activity_log ORDER BY id DESC LIMIT ?", (limit,))
        like = f"%{term}%"
        return self._query(
            "SELECT * FROM activity_log WHERE "
            "customer_name LIKE ? OR invoice_ref LIKE ? OR platform LIKE ? "
            "OR action LIKE ? OR filename LIKE ? OR message LIKE ? "
            "ORDER BY id DESC LIMIT ?",
            (like, like, like, like, like, like, limit),
        )

    def clear_activity_log(self) -> int:
        """Delete all activity_log rows. Returns how many were removed."""
        return int(self._exec("DELETE FROM activity_log").rowcount or 0)

    def trim_activity_log(self, keep: int = ACTIVITY_LOG_KEEP) -> int:
        """Drop all but the newest ``keep`` activity rows. Returns how many went.

        The tab only ever displays the most recent few hundred, but the table
        gains a row per emitted event, so without this it grows forever.
        """
        rows = self._query(
            "SELECT id FROM activity_log ORDER BY id DESC LIMIT 1 OFFSET ?", (keep,))
        if not rows:
            return 0
        return int(self._exec("DELETE FROM activity_log WHERE id <= ?",
                              (rows[0]["id"],)).rowcount or 0)

    def add_error(self, **kw: Any) -> int:
        """Append one row to the error log. Returns its id."""
        kw.setdefault("ts", _utcnow())
        keys = ("ts", "stage", "customer_name", "invoice_ref", "filename",
                "error", "payload")
        vals = [kw.get(k, "") for k in keys]
        cur = self._exec(
            f"INSERT INTO error_log({','.join(keys)}) VALUES({','.join('?' for _ in keys)})",
            vals,
        )
        return int(cur.lastrowid)

    def list_errors(self, include_resolved: bool = False) -> list[sqlite3.Row]:
        """Newest error rows, unresolved only unless asked otherwise."""
        sql = "SELECT * FROM error_log"
        if not include_resolved:
            sql += " WHERE resolved=0"
        return self._query(sql + " ORDER BY id DESC LIMIT 500")

    def unresolved_error_paths(self) -> set[str]:
        """File paths referenced by unresolved errors, so retry stays possible.

        The attachment-cache sweep must not delete a file a queued retry still
        needs.
        """
        out: set[str] = set()
        for row in self._query("SELECT payload FROM error_log WHERE resolved=0"):
            try:
                path = json.loads(row["payload"] or "{}").get("file_path", "")
            except (ValueError, TypeError):
                continue
            if path:
                out.add(str(path))
        return out

    def mark_error_resolved(self, eid: int) -> None:
        """Flag one error row as dealt with."""
        self._exec("UPDATE error_log SET resolved=1 WHERE id=?", (eid,))

    def bump_error_retry(self, eid: int) -> None:
        """Increment the retry counter on one error row."""
        self._exec("UPDATE error_log SET retry_count=retry_count+1 WHERE id=?", (eid,))

    # -- pending invoices (new-customer queue) ----------------------
    def add_pending(self, **kw: Any) -> int:
        """Queue one invoice awaiting a new-customer decision. Returns its id.

        ``status`` is defaulted here, not left to the column default: every
        column is named in the INSERT, so an omitted status was being written
        as '' and never matched the ``pending_new_customer`` queue it was
        meant to join.
        """
        kw.setdefault("ts", _utcnow())
        kw.setdefault("status", "pending_new_customer")
        keys = ("ts", "extracted_name", "job_number", "invoice_ref",
                "email_subject", "email_from", "file_path", "raw_json", "status")
        vals = [kw.get(k, "") for k in keys]
        cur = self._exec(
            f"INSERT INTO pending_invoices({','.join(keys)}) VALUES({','.join('?' for _ in keys)})",
            vals,
        )
        return int(cur.lastrowid)

    def repair_pending_status(self) -> int:
        """Heal rows written before add_pending() defaulted the status.

        Those were inserted with an empty status and so were invisible to
        list_pending(), leaving the invoice queued forever. Returns how many
        were repaired.
        """
        rows = self._query("SELECT id FROM pending_invoices WHERE status=''")
        if rows:
            self._exec("UPDATE pending_invoices SET status='pending_new_customer' "
                       "WHERE status=''")
        return len(rows)

    def list_pending(self, status: str = "pending_new_customer") -> list[sqlite3.Row]:
        """Queued invoices in one status, newest first."""
        return self._query(
            "SELECT * FROM pending_invoices WHERE status=? ORDER BY id DESC", (status,)
        )

    def pending_paths(self) -> set[str]:
        """Cached file paths still referenced by an unresolved pending invoice."""
        return {row["file_path"] for row in self._query(
            "SELECT file_path FROM pending_invoices WHERE status='pending_new_customer'"
        ) if row["file_path"]}

    def set_pending_status(self, pid: int, status: str) -> None:
        """Move one queued invoice to a new status."""
        self._exec("UPDATE pending_invoices SET status=? WHERE id=?", (status, pid))

    # -- processed email dedupe -----------------------------------
    # -- mail accounts -------------------------------------------
    def list_mail_accounts(self, enabled_only: bool = False) -> list[sqlite3.Row]:
        """Monitored mailboxes in display order."""
        sql = "SELECT * FROM mail_accounts"
        if enabled_only:
            sql += " WHERE enabled=1"
        return self._query(sql + " ORDER BY position, id")

    def add_mail_account(self, **kw: Any) -> int:
        """Append a mailbox row and return its id."""
        rows = self._query("SELECT COALESCE(MAX(position), -1) AS p FROM mail_accounts")
        kw.setdefault("position", (rows[0]["p"] if rows else -1) + 1)
        kw.setdefault("created_at", _utcnow())
        keys = ("position", "enabled", "backend", "address", "folder",
                "graph_cache", "imap_host", "imap_port", "imap_username",
                "imap_password", "created_at")
        vals = [kw.get(k, 1 if k == "enabled" else "") for k in keys]
        cur = self._exec(
            f"INSERT INTO mail_accounts({','.join(keys)}) "
            f"VALUES({','.join('?' for _ in keys)})", vals)
        return int(cur.lastrowid)

    def update_mail_account(self, account_id: int, **fields: Any) -> None:
        """Patch named columns on one mailbox row."""
        allowed = {"position", "enabled", "backend", "address", "folder",
                   "graph_cache", "imap_host", "imap_port", "imap_username",
                   "imap_password"}
        fields = {k: v for k, v in fields.items() if k in allowed}
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        self._exec(f"UPDATE mail_accounts SET {cols} WHERE id=?",
                   (*fields.values(), account_id))

    def delete_mail_account(self, account_id: int) -> None:
        """Remove one mailbox and its stored credentials."""
        self._exec("DELETE FROM mail_accounts WHERE id=?", (account_id,))

    def is_email_processed(self, message_id: str) -> bool:
        """True when this message id has already been handled."""
        return bool(
            self._query("SELECT 1 FROM processed_emails WHERE message_id=?", (message_id,))
        )

    def recent_processed_ids(self, limit: int = _SEEN_ID_WINDOW) -> set[str]:
        """Message ids handled recently, as a mailbox-side prefilter.

        Passed into the mail backends so an email that is still sitting in the
        inbox is skipped *before* its attachments are downloaded again. Bounded
        because it is only an optimisation: :meth:`is_email_processed` is still
        consulted per message, so a miss costs one redundant fetch, never a
        duplicate upload.
        """
        return {r["message_id"] for r in self._query(
            "SELECT message_id FROM processed_emails ORDER BY id DESC LIMIT ?", (limit,))}

    def mark_email_processed(self, message_id: str, received_at: str,
                             subject: str, result: str) -> None:
        """Record that one email has been handled."""
        self._exec(
            "INSERT OR IGNORE INTO processed_emails(message_id,received_at,processed_at,subject,result) "
            "VALUES(?,?,?,?,?)",
            (message_id, received_at, _utcnow(), subject, result),
        )

    # -- per-document dedupe -------------------------------------
    def document_already_sent(self, file_hash: str, customer_name: str,
                              invoice_ref: str, doc_type: str,
                              platform: str) -> sqlite3.Row | None:
        """Return the prior upload row if this document already went to ``platform``.

        A match is either the exact same file bytes, or the same
        customer + reference + document type (when a reference was parsed).
        """
        if file_hash:
            rows = self._query(
                "SELECT * FROM processed_documents WHERE file_hash=? AND platform=? LIMIT 1",
                (file_hash, platform),
            )
            if rows:
                return rows[0]
        if invoice_ref:
            rows = self._query(
                "SELECT * FROM processed_documents WHERE customer_name=? AND invoice_ref=? "
                "AND doc_type=? AND platform=? LIMIT 1",
                (customer_name, invoice_ref, doc_type, platform),
            )
            if rows:
                return rows[0]
        return None

    def record_document_sent(self, *, file_hash: str, customer_name: str,
                             invoice_ref: str, doc_type: str, platform: str,
                             remote_id: str, filename: str,
                             job_number: str = "") -> None:
        """Remember a successful upload so it is never repeated."""
        self._exec(
            "INSERT INTO processed_documents"
            "(ts,file_hash,customer_name,invoice_ref,doc_type,platform,remote_id,"
            "job_number,filename) VALUES(?,?,?,?,?,?,?,?,?)",
            (_utcnow(), file_hash, customer_name, invoice_ref, doc_type,
             platform, remote_id, job_number, filename),
        )

    def find_job_for_credit(self, customer_name: str, refs: list[str],
                            platform: str) -> tuple[str, str]:
        """Job number to file a credit against, and how it was decided.

        Credit notes usually quote the invoice they are crediting rather than a
        job number, so prefer an exact match on a previously uploaded invoice
        reference; otherwise fall back to that customer's most recent job.
        Returns ("", "") when nothing is known.
        """
        wanted = [r.strip() for r in refs if r and r.strip()]
        for ref in wanted:
            rows = self._query(
                "SELECT job_number FROM processed_documents "
                "WHERE customer_name=? COLLATE NOCASE AND invoice_ref=? COLLATE NOCASE "
                "AND doc_type='invoice' AND platform=? AND job_number<>'' "
                "ORDER BY id DESC LIMIT 1",
                (customer_name, ref, platform))
            if rows:
                return rows[0]["job_number"], f"matched invoice {ref}"

        rows = self._query(
            "SELECT job_number FROM processed_documents "
            "WHERE customer_name=? COLLATE NOCASE AND doc_type='invoice' "
            "AND platform=? AND job_number<>'' ORDER BY id DESC LIMIT 1",
            (customer_name, platform))
        if rows:
            return rows[0]["job_number"], "most recent invoice for this customer"
        return "", ""

    def last_seen_email_time(self) -> str | None:
        """Received timestamp of the newest handled email, or None."""
        rows = self._query("SELECT MAX(received_at) AS m FROM processed_emails")
        return rows[0]["m"] if rows and rows[0]["m"] else None

    def close(self) -> None:
        """Close the connection. Idempotent; safe while other threads race."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._conn.close()
            except Exception:
                pass
