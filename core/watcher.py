"""Background inbox watcher.

A single daemon thread that:
  * on start, does a back-check for everything since the last processed email
    (or unread mail) so overnight invoices are not missed;
  * then polls Outlook every ``watcher.poll_minutes`` minutes;
  * parses each new invoice email and hands it to the :class:`Router`.

All UI communication is through thread-safe callbacks (``emit`` for log lines,
``on_new_customer`` for the modal, ``on_status`` for the run indicator).
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from dateutil import parser as dtparse

from config import CACHE_RETENTION_DAYS
from core.database import Database
from core.housekeeping import prune_attachment_cache
from core.parser_ai import InvoiceParser
from core.router import Router
from integrations.email_outlook import account_backends

log = logging.getLogger(__name__)


class Watcher:
    """Owns the polling thread and its lifecycle."""

    def __init__(self, db: Database, settings, on_new_customer=None,
                 emit=None, on_status=None) -> None:
        """Build the router/parser and prepare the (not yet started) thread."""
        self._db = db
        self._settings = settings
        self._emit = emit or (lambda **_: None)
        self._on_status = on_status or (lambda running: None)
        self._router = Router(db, settings, on_new_customer=on_new_customer, emit=self._emit)
        self._parser = InvoiceParser(settings)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        # A catch-up sweep runs on its own thread; this serialises it against
        # the polling thread so two fetches never hit the same mailbox at once
        # (unsafe for the Outlook COM backend in particular). Its own cancel
        # flag lets a stop abort it without being tangled up in ``_stop``,
        # which also means "the watcher is not running".
        self._scan_lock = threading.Lock()
        self._catch_up_stop = threading.Event()
        #: When the last mailbox scan started (UTC), for the "checked 2 min ago" status.
        self.last_poll: datetime | None = None

    # -- lifecycle ---------------------------------------------------
    @property
    def running(self) -> bool:
        """True while the polling thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """Launch the polling thread. Idempotent."""
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="InvoiceWarden-Watcher", daemon=True)
        self._thread.start()
        self._on_status(True)
        self._emit(level="INFO", action="watcher", message="Watcher started.")

    def stop(self) -> None:
        """Signal the thread and wait briefly for it to exit. Idempotent.

        The thread is a daemon, so if it is stuck in a network call it is left
        to finish on its own and the process can still exit cleanly - the DB
        guards against a late write from it.
        """
        already_stopping = self._stop.is_set()
        self._stop.set()
        self._catch_up_stop.set()   # abort an in-flight catch-up sweep too
        self._wake.set()
        if self._thread and self._thread.is_alive() and not already_stopping:
            self._thread.join(timeout=5)
        self._on_status(False)
        self._emit(level="INFO", action="watcher", message="Watcher stopped.")

    def scan_now(self) -> None:
        """Force an immediate poll (used by the 'Scan now' button)."""
        self._wake.set()

    def catch_up(self, days_back: int, job_floor: int, job_ceiling: int = 0,
                 on_done=None) -> None:
        """Run one deliberate sweep of old mail, off-thread, with a job filter.

        This is the "clear a backlog of old unread invoices" action. Unlike a
        routine poll it looks back an arbitrary number of days and, when
        ``job_floor``/``job_ceiling`` are set, only files an invoice whose
        Service-system job number is within [floor, ceiling] (an unreadable
        job number is skipped too). The bounds are not persisted - they guard
        this run only. Runs whether or not the polling thread is started;
        ``on_done`` fires on completion.
        """
        since = datetime.now(timezone.utc) - timedelta(days=max(1, days_back))
        self._catch_up_stop.clear()

        if job_ceiling:
            scope = f"only jobs {job_floor or 1}-{job_ceiling}"
        elif job_floor:
            scope = f"skipping jobs below {job_floor}"
        else:
            scope = "all jobs"

        def work() -> None:
            """Thread body: one locked poll with the supplied job filter."""
            try:
                self._emit(level="INFO", action="catch_up",
                           message=f"Catch-up scan: mail from the last "
                                   f"{days_back} day(s), {scope}.")
                with self._scan_lock:
                    self._poll(since=since, unread_only=False,
                               job_floor=job_floor, job_ceiling=job_ceiling,
                               cancel=self._catch_up_stop)
                self._emit(level="INFO", action="catch_up",
                           message="Catch-up scan finished.")
            except Exception as exc:
                log.exception("Catch-up scan failed")
                self._emit(level="ERROR", action="catch_up", message=str(exc))
            finally:
                if on_done:
                    on_done()

        threading.Thread(target=work, name="InvoiceWarden-CatchUp", daemon=True).start()

    # -- main loop -------------------------------------------------
    def _run(self) -> None:
        """Thread body: startup back-check, then poll until stopped."""
        try:
            self._housekeeping()
        except Exception:
            log.exception("Housekeeping failed")   # never block the first scan

        try:
            self._back_check()
        except Exception as exc:
            log.exception("Back-check failed")
            self._emit(level="ERROR", action="back_check", message=str(exc))

        while not self._stop.is_set():
            interval = max(1, self._settings.get_int("watcher.poll_minutes", 5)) * 60
            self._wake.wait(timeout=interval)
            self._wake.clear()
            if self._stop.is_set():
                break
            try:
                with self._scan_lock:   # never overlap a catch-up sweep
                    self._poll(since=self._resume_point(),
                               unread_only=self._settings.get_bool("watcher.unread_only"))
            except Exception as exc:
                log.exception("Poll failed")
                self._emit(level="ERROR", action="poll", message=str(exc))

    def _resume_point(self) -> datetime | None:
        """Timestamp to fetch mail from - the newest email we have handled."""
        last = self._db.last_seen_email_time()
        if not last:
            return None
        try:
            return dtparse.isoparse(last).astimezone(timezone.utc)
        except (ValueError, TypeError):
            return None

    def _back_check(self) -> None:
        """Startup sweep: unread + anything since we last ran."""
        self._emit(level="INFO", action="back_check",
                   message="Startup back-check for missed invoices...")
        with self._scan_lock:   # never overlap a catch-up sweep
            self._poll(since=self._resume_point(), unread_only=False, back_check=True)

    def _housekeeping(self) -> None:
        """Bounded-growth maintenance: prune the attachment cache and the log.

        Runs once when the thread starts. Both stores otherwise grow forever -
        the cache gains a folder per processed email, the activity log a row
        per emitted event.
        """
        removed = prune_attachment_cache(
            keep_days=self._settings.get_int("watcher.cache_days", CACHE_RETENTION_DAYS),
            protected=self._db.unresolved_error_paths() | self._db.pending_paths(),
        )
        trimmed = self._db.trim_activity_log()
        if removed or trimmed:
            self._emit(level="INFO", action="cleanup",
                       message=f"Housekeeping: removed {removed} cached attachment "
                               f"folder(s), trimmed {trimmed} old log row(s).")

    def _poll(self, since, unread_only: bool, back_check: bool = False,
              job_floor: int = 0, job_ceiling: int = 0,
              cancel: threading.Event | None = None) -> None:
        """One scan -> parse -> route cycle across every enabled mailbox.

        ``job_floor``/``job_ceiling`` are forwarded to the router (0 = off;
        only a catch-up sweep sets them). ``cancel`` is the flag to abort on -
        the shared ``_stop`` for a routine poll, the sweep's own flag for a
        catch-up.
        """
        cancel = cancel or self._stop
        self.last_poll = datetime.now(timezone.utc)
        # Prefilter on the mailbox side so attachments no customer wants are
        # never transferred; per-customer filtering still happens in the router.
        allowed_ext = self._db.all_file_types()
        # Already-handled mail is skipped before its attachments are fetched.
        seen_ids = self._db.recent_processed_ids()

        messages = []
        for row, backend in account_backends(self._settings, self._db):
            label = (row["address"] if row and row["address"]
                     else "the configured mailbox")
            if cancel.is_set():
                return
            try:
                found = backend.fetch(since=since, unread_only=unread_only,
                                      allowed_ext=allowed_ext, seen_ids=seen_ids)
            except Exception as exc:
                # One broken mailbox must not stop the others being scanned.
                log.exception("Mailbox %s failed", label)
                self._emit(level="ERROR", action="poll",
                           message=f"Mailbox '{label}' failed: {exc}")
                continue
            if not found:
                detail = getattr(backend, "last_scan", "") or "No new emails."
                self._emit(level="INFO", action="poll",
                           message=f"[{label}] Nothing to process. {detail}")
            messages.extend(found)

        if not messages:
            return

        for msg in sorted(messages, key=lambda m: m.received_at):
            if cancel.is_set():
                return
            if self._db.is_email_processed(msg.message_id):
                continue
            self._emit(level="INFO", customer_name="", platform="-", action="found",
                       message=f"Processing '{msg.subject}' from {msg.sender}")
            try:
                parsed = self._parser.parse(msg.subject, msg.body, msg.attachments,
                                            sender=msg.sender)
                self._emit(level="INFO", customer_name=parsed.customer_name,
                           invoice_ref=parsed.invoice_ref, platform="-", action="parsed",
                           message=f"job={parsed.job_number or '-'} "
                                   f"ref={parsed.invoice_ref or '-'} "
                                   f"conf={parsed.confidence:.2f} src={parsed.source}")
                result = self._router.route(parsed, msg.attachments, msg.subject,
                                            msg.sender, job_floor=job_floor,
                                            job_ceiling=job_ceiling)
            except Exception as exc:
                log.exception("Message processing failed")
                self._db.add_error(stage="parse", filename=msg.subject,
                                   error=str(exc))
                self._emit(level="ERROR", action="parse", message=str(exc))
                result = "error"

            self._db.mark_email_processed(
                msg.message_id,
                received_at=msg.received_at.isoformat(),
                subject=msg.subject,
                result=result,
            )
