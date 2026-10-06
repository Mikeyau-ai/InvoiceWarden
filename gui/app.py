"""Main application window: header, tabs, event pump, watcher wiring."""
from __future__ import annotations

import json
import queue
from datetime import datetime, timezone
from pathlib import Path

import customtkinter as ctk

from core import updater
from core.database import Database
from core.router import Router
from core.settings_store import Settings
from core.watcher import Watcher
from gui import theme
from gui.activity_page import ActivityPage
from gui.customers_tab import CustomersTab
from gui.dialogs import CatchUpDialog, NewCustomerDialog
from gui.settings_tab import SettingsTab
from gui.about_dialog import AboutWindow
from gui.theme import C, FONT_WORDMARK, accent_button
from gui.update_dialog import UpdateDialog
from integrations.registry import label_for
from version import APP_VERSION

#: Cycling glyph shown on "Go back further…" while a catch-up sweep is running, so
#: there's something visibly moving instead of a frozen link.
_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_SPINNER_INTERVAL_MS = 120


class App(ctk.CTk):
    """Top-level window. Owns the DB, settings, watcher and all tabs."""

    def __init__(self, db: Database, settings: Settings, box, autostart: bool = False) -> None:
        """Build the window, tabs and watcher, then start the event pump."""
        super().__init__()
        self.db = db
        self.settings = settings
        self._box = box

        # Thread-safe channels from the watcher into the GUI.
        self._events: queue.Queue[dict] = queue.Queue()
        self._new_customers: queue.Queue[tuple[str, int]] = queue.Queue()

        self.title(f"InvoiceM8  v{APP_VERSION}")
        self.geometry("1080x720")
        self.minsize(920, 600)
        self.configure(fg_color=C["bg"])
        theme.dark_titlebar(self)
        theme.apply_icon(self)

        self._running = False
        self._closing = False
        self._pump_job: str | None = None
        self._catchup_job: str | None = None
        self._catchup_phase = 0
        self._settings_win: ctk.CTkToplevel | None = None
        self._about_win: ctk.CTkToplevel | None = None

        self._build_header()
        self._build_pages()

        # No on_new_customer callback: unknown suppliers are added
        # automatically by the router and flagged NEW in the Customers tab,
        # rather than interrupting with a modal per invoice.
        self.watcher = Watcher(
            db, settings,
            emit=self.emit_event,
            on_status=lambda running: None if self._closing
            else self.after(0, self._set_status, running),
        )

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._pump_job = self.after(400, self._pump)

        # Auto-update check (no-ops when running from source or when disabled).
        self._update_shown = False
        updater.start_check()

        if autostart or settings.get_bool("watcher.autostart"):
            self.after(800, self.watcher.start)

    # -- header --------------------------------------------------
    def _build_header(self) -> None:
        """Top bar: wordmark on the left, the section buttons on the right."""
        bar = ctk.CTkFrame(self, fg_color=C["panel"], corner_radius=0, height=54)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        wordmark = ctk.CTkLabel(bar, text="INVOICEM8", font=FONT_WORDMARK,
                                text_color=C["teal"], cursor="hand2")
        wordmark.pack(side="left", padx=(16, 4))
        wordmark.bind("<Button-1>", lambda _e: self.open_about())

        # Section buttons, packed right-to-left: Settings opens its own window for now.
        accent_button(ctk, bar, "Settings", self._open_settings,
                      colour=C["btn_off"], width=96).pack(side="right", padx=(4, 12))
        self._nav: dict[str, ctk.CTkButton] = {}
        for key, label in (("suppliers", "Suppliers"), ("activity", "Activity")):
            btn = accent_button(ctk, bar, label, lambda k=key: self.show_page(k),
                                colour=C["btn_off"], width=96)
            btn.pack(side="right", padx=4)
            self._nav[key] = btn

    # -- pages --------------------------------------------------
    def _build_pages(self) -> None:
        """Activity (the main screen) and Suppliers; one shown at a time."""
        self._body = ctk.CTkFrame(self, fg_color=C["bg"])
        self._body.pack(fill="both", expand=True)
        self.activity = ActivityPage(self._body, self)
        sup = ctk.CTkFrame(self._body, fg_color=C["bg"])
        inner = ctk.CTkFrame(sup, fg_color=C["panel"])
        inner.pack(fill="both", expand=True, padx=14, pady=14)
        self.customers_tab = CustomersTab(inner, self)
        self._pages = {"activity": self.activity.frame, "suppliers": sup}
        self.settings_tab: SettingsTab | None = None  # created on first open
        self.show_page("activity")

    def show_page(self, key: str) -> None:
        """Show one section and highlight its button."""
        for name, frame in self._pages.items():
            frame.pack_forget()
        self._pages[key].pack(fill="both", expand=True)
        for name, btn in self._nav.items():
            on = name == key
            btn.configure(fg_color=C["teal_btn"] if on else C["btn_off"],
                          hover_color=theme.shade(C["teal_btn"] if on else C["btn_off"], 1.2))
        if key == "suppliers":
            self.customers_tab.refresh()

    # -- settings window --------------------------------------
    def _open_settings(self) -> None:
        """Settings lives in its own window, opened from the header button."""
        if self._settings_win is not None and self._settings_win.winfo_exists():
            self._settings_win.lift()
            self._settings_win.focus()
            return
        win = ctk.CTkToplevel(self)
        win.title("InvoiceM8 - Settings")
        # Height is capped to the screen so the pinned action footer is always
        # on-screen, even on a 768px-tall laptop display.
        h = min(820, max(560, self.winfo_screenheight() - 120))
        win.geometry(f"860x{h}")
        win.minsize(700, 480)
        win.configure(fg_color=C["bg"])
        theme.dark_titlebar(win)
        self._settings_win = win
        self.settings_tab = SettingsTab(win, self)
        win.after(200, win.lift)

    def open_about(self) -> None:
        """About / changelog window (single instance)."""
        if self._about_win is not None and self._about_win.winfo_exists():
            self._about_win.lift()
            return
        self._about_win = AboutWindow(self)

    # -- event pump (runs on the Tk main thread) ----------------
    def emit_event(self, **event) -> None:
        """Thread-safe: called by watcher/router to push a log line."""
        if self._closing:
            return
        event.setdefault("ts", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        # Persist non-error activity; errors are written by the router itself.
        if event.get("level") != "ERROR":
            self.db.add_activity(**{k: event.get(k, "") for k in
                                    ("ts", "level", "customer_name", "invoice_ref",
                                     "platform", "action", "filename", "message")})
        self._events.put(event)

    def _pump(self) -> None:
        """Drain queues and update the UI. Rescheduled every 400 ms."""
        if self._closing:
            return
        try:
            while True:
                self.activity.on_event(self._events.get_nowait())
        except queue.Empty:
            pass
        try:
            while True:
                name, pid = self._new_customers.get_nowait()
                self._handle_new_customer(name, pid)
        except queue.Empty:
            pass

        if not self._update_shown:
            info = updater.pending_update()
            if info is not None:
                self._update_shown = True
                UpdateDialog(self, info)

        self._pump_job = self.after(400, self._pump)

    # -- updates ----------------------------------------------
    def check_updates_now(self, on_result) -> None:
        """Manual check from Settings. Calls on_result(text) on the Tk thread."""
        import threading

        def work() -> None:
            """Worker thread: run the check, then hand back to Tk."""
            try:
                info = updater.check_now()
                error = None
            except updater.UpdateCheckError as exc:
                info, error = None, str(exc)

            def show() -> None:
                """Tk thread: report the outcome."""
                if error is not None:
                    on_result(f"Update check failed: {error}. You can still get "
                              f"the latest version from the GitHub Releases page.")
                elif info is None:
                    on_result(f"You're up to date (v{APP_VERSION})."
                              if updater.is_frozen()
                              else "Update check only runs in the built .exe.")
                else:
                    self._update_shown = True
                    UpdateDialog(self, info)
                    on_result(f"Version {info.version} available.")
            self.after(0, show)

        threading.Thread(target=work, daemon=True).start()

    # -- new customer modal + replay ---------------------------
    def _handle_new_customer(self, name: str, pending_id: int) -> None:
        """Show the modal; on accept, add the customer and route its queue."""
        dlg = NewCustomerDialog(
            self, name,
            label_for(self.settings.get("service.provider", "servicem8")),
            label_for(self.settings.get("accounting.provider", "none")),
        )
        self.wait_window(dlg)
        if not dlg.result:
            self.db.set_pending_status(pending_id, "skipped")
            self.emit_event(level="WARN", customer_name=name, action="skipped",
                            message="User skipped the new-customer prompt.")
            return

        self.customers_tab.add_from_dialog(dlg.result)
        self.emit_event(level="INFO", customer_name=dlg.result["name"], action="added",
                        message="Customer added via prompt; routing queued invoices.")
        self._replay_pending_for(dlg.result["name"], name)

    def _replay_pending_for(self, customer_name: str, extracted_name: str) -> None:
        """Route every pending invoice whose extracted name now resolves."""
        router = Router(self.db, self.settings, emit=self.emit_event)
        # Heal rows written before add_pending() defaulted the status, which
        # were invisible here and so never routed.
        healed = self.db.repair_pending_status()
        if healed:
            self.emit_event(level="INFO", action="repair",
                            message=f"Recovered {healed} queued invoice(s) that "
                                    f"were stuck awaiting review.")
        from core.parser_ai import ParseResult
        for row in self.db.list_pending("pending_new_customer"):
            if row["extracted_name"].strip().lower() not in (
                extracted_name.strip().lower(), customer_name.strip().lower()
            ):
                continue
            data = json.loads(row["raw_json"] or "{}")
            parsed = ParseResult(
                customer_name=customer_name,
                job_number=data.get("job_number", row["job_number"]),
                invoice_ref=data.get("invoice_ref", row["invoice_ref"]),
                amount_total=data.get("amount_total", ""),
                invoice_date=data.get("invoice_date", ""),
            )
            path = Path(row["file_path"])
            if path.exists():
                router.route(parsed, [path], row["email_subject"], row["email_from"])
            self.db.set_pending_status(row["id"], "resolved")
        self.refresh_logs()

    # -- watcher controls (called by the Activity page) ---------
    def set_watching(self, on: bool) -> None:
        """The On/Off switch: start or stop the watcher."""
        if on and not self.watcher.running:
            self.watcher.start()
        elif not on and self.watcher.running:
            self.watcher.stop()

    def scan_now(self) -> None:
        """Check now: turn on if off, then check the mailboxes straight away."""
        if not self.watcher.running:
            self.watcher.start()
        self.watcher.scan_now()

    def catch_up(self) -> None:
        """Go back further: ask how far back (and which job numbers), then sweep once.

        Independent of the watcher's on/off state - the sweep runs on its own
        thread. The job range is not saved; it guards this one run.
        """
        dlg = CatchUpDialog(self)
        self.wait_window(dlg)
        if not dlg.result:
            return

        def done() -> None:
            self._catchup_spin_stop()
            self.refresh_logs()

        self._catchup_phase = 0
        self._catchup_spin_tick()
        self.watcher.catch_up(dlg.result["days_back"], dlg.result["job_floor"],
                              dlg.result["job_ceiling"],
                              on_done=lambda: self.after(0, done))

    def _catchup_spin_tick(self) -> None:
        """Advance the "Going back…" spinner while a sweep runs."""
        if self._closing:
            return
        glyph = _SPINNER_FRAMES[self._catchup_phase % len(_SPINNER_FRAMES)]
        self._catchup_phase += 1
        self.activity.set_sweeping(True, glyph)
        self._catchup_job = self.after(_SPINNER_INTERVAL_MS, self._catchup_spin_tick)

    def _catchup_spin_stop(self) -> None:
        """Stop the spinner and restore the link."""
        if self._catchup_job is not None:
            self.after_cancel(self._catchup_job)
            self._catchup_job = None
        if self._closing:
            return
        self.activity.set_sweeping(False)

    def _set_status(self, running: bool) -> None:
        """Reflect the watcher's state on the Activity page's status card."""
        if self._closing:
            return
        self._running = running
        self.activity.set_running(running)

    def refresh_after_settings(self) -> None:
        """Called by the Settings tab after a save."""
        self.customers_tab.refresh()
        self.activity.refresh()

    def refresh_logs(self) -> None:
        """Repaint the Activity page (tiles, problems, recent list and full log)."""
        self.activity.refresh()
        self.activity.full_log.refresh()

    def _on_close(self) -> None:
        """Tear down in order: stop scheduled callbacks, stop the watcher
        thread, close the DB, then destroy the window. Idempotent and
        exception-safe so a close always completes."""
        if self._closing:
            return
        self._closing = True

        for job in (self._pump_job, self._catchup_job):
            if job is not None:
                try:
                    self.after_cancel(job)
                except Exception:
                    pass
        self._pump_job = self._catchup_job = None

        try:
            self.watcher.stop()          # signals + joins the daemon thread
        except Exception:
            pass
        try:
            self.db.close()              # after the watcher stopped touching it
        except Exception:
            pass

        try:
            self.quit()                  # exit mainloop
        finally:
            self.destroy()               # drop the Tk widgets
