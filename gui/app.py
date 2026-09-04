"""Main application window: header, tabs, event pump, watcher wiring."""
from __future__ import annotations

import json
import math
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
from gui.customers_tab import CustomersTab
from gui.dialogs import CatchUpDialog, NewCustomerDialog
from gui.errors_tab import ErrorsTab
from gui.logs_tab import LogsTab
from gui.settings_tab import SettingsTab
from gui.about_dialog import AboutWindow
from gui.theme import C, FONT_TAGLINE, FONT_UI, FONT_WORDMARK, accent_button
from gui.update_dialog import UpdateDialog
from integrations.registry import label_for
from version import APP_VERSION

#: Watcher-running border pulse: steps per cycle and ms between them.
_GLOW_STEPS = 20
_GLOW_INTERVAL_MS = 200


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
        self._glow_job: str | None = None
        self._pump_job: str | None = None
        self._glow_phase = 0
        self._settings_win: ctk.CTkToplevel | None = None
        self._about_win: ctk.CTkToplevel | None = None

        self._build_header()
        self._build_tabs()

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
        """Top bar: wordmark, tagline and the Scan/Start/Settings buttons."""
        bar = ctk.CTkFrame(self, fg_color=C["panel"], corner_radius=0, height=54)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        wordmark = ctk.CTkLabel(bar, text="INVOICEM8", font=FONT_WORDMARK,
                                text_color=C["text"], cursor="hand2")
        wordmark.pack(side="left", padx=(16, 4))
        wordmark.bind("<Button-1>", lambda _e: self.open_about())
        self._tagline = ctk.CTkLabel(bar, text="", font=FONT_TAGLINE,
                                     text_color=C["dim"], cursor="hand2")
        self._tagline.pack(side="left", padx=4)
        self._tagline.bind("<Button-1>", lambda _e: self.open_about())
        self._refresh_tagline()

        # Right-aligned controls (packed right-to-left):
        #   Catch up | Scan now | Start/Stop | Settings
        accent_button(ctk, bar, "Settings", self._open_settings,
                      colour=C["btn_off"]).pack(side="right", padx=(4, 12))
        self._start_btn = accent_button(ctk, bar, "Start Watcher", self._toggle_watcher,
                                        colour=C["green"])
        self._start_btn.pack(side="right", padx=4)
        accent_button(ctk, bar, "Scan now", self._scan_now,
                      colour=C["blue"]).pack(side="right", padx=4)
        accent_button(ctk, bar, "Catch up…", self._catch_up,
                      colour=C["btn_off"]).pack(side="right", padx=4)

    # -- tabs ---------------------------------------------------
    def _build_tabs(self) -> None:
        """Create the three main tabs inside the glow border frame."""
        # Outer frame carries the "watcher running" glow border.
        self._glow = ctk.CTkFrame(self, fg_color=C["bg"], corner_radius=8,
                                  border_width=2, border_color=C["border"])
        self._glow.pack(fill="both", expand=True, padx=10, pady=10)

        self.tabs = ctk.CTkTabview(self._glow, fg_color=C["panel"])
        self.tabs.pack(fill="both", expand=True, padx=3, pady=3)
        for name in ("Suppliers", "Activity Log", "Error Log"):
            self.tabs.add(name)
        self.customers_tab = CustomersTab(self.tabs.tab("Suppliers"), self)
        self.logs_tab = LogsTab(self.tabs.tab("Activity Log"), self)
        self.errors_tab = ErrorsTab(self.tabs.tab("Error Log"), self)
        self.settings_tab: SettingsTab | None = None  # created on first open
        self.tabs.set("Activity Log")

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
                self.logs_tab.append_live(self._events.get_nowait())
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

    # -- misc callbacks ---------------------------------------
    def _toggle_watcher(self) -> None:
        """Start/Stop button: flip the watcher state."""
        if self.watcher.running:
            self.watcher.stop()
        else:
            self.watcher.start()

    def _scan_now(self) -> None:
        """Scan-now button: start the watcher if idle, then force a poll."""
        if not self.watcher.running:
            self.watcher.start()
        self.watcher.scan_now()

    def _catch_up(self) -> None:
        """Catch-up button: prompt for a lookback + job range, then sweep once.

        Independent of the watcher's on/off state - the sweep runs on its own
        thread. The job range is not saved; it guards this one run.
        """
        dlg = CatchUpDialog(self)
        self.wait_window(dlg)
        if not dlg.result:
            return
        self.watcher.catch_up(dlg.result["days_back"], dlg.result["job_floor"],
                              dlg.result["job_ceiling"],
                              on_done=lambda: self.after(0, self.refresh_logs))

    def _set_status(self, running: bool) -> None:
        """Reflect watcher state in the toggle button and the glow border."""
        if self._closing:
            return
        self._running = running
        self._start_btn.configure(
            text="Stop Watcher" if running else "Start Watcher",
            fg_color=C["red"] if running else C["green"],
            hover_color=theme.shade(C["red"] if running else C["green"], 0.82),
        )
        if running and self._glow_job is None:
            self._glow_phase = 0
            self._glow_tick()
        elif not running and self._glow_job is not None:
            self.after_cancel(self._glow_job)
            self._glow_job = None
            self._glow.configure(border_color=C["border"])

    def _glow_tick(self) -> None:
        """Pulse the tab-area border green while the watcher runs.

        This runs for as long as the watcher is on - i.e. all day - so it ticks
        at 200 ms rather than 80 ms (the same ~4s cycle, a third of the
        redraws) and does no work at all while the window is minimised.
        """
        if self._closing:
            return
        self._glow_phase = (self._glow_phase + 1) % _GLOW_STEPS
        try:
            visible = self.state() == "normal"
        except Exception:                      # window already going away
            visible = False
        if visible:
            t = (math.sin(self._glow_phase / _GLOW_STEPS * 2 * math.pi) + 1) / 2  # 0..1
            self._glow.configure(border_color=theme.shade(C["green"], 0.55 + 0.95 * t))
        self._glow_job = self.after(_GLOW_INTERVAL_MS, self._glow_tick)

    def _refresh_tagline(self) -> None:
        """Header subtitle showing the selected Service and Accounting systems."""
        svc = label_for(self.settings.get("service.provider", "servicem8"))
        acct = label_for(self.settings.get("accounting.provider", "none"))
        self._tagline.configure(text=f"Outlook  >  {svc}  +  {acct}")

    def refresh_after_settings(self) -> None:
        """Called by the Settings tab after a save."""
        self._refresh_tagline()
        self.customers_tab.refresh()

    def refresh_logs(self) -> None:
        """Repaint the activity log from the DB."""
        self.logs_tab.refresh()

    def _on_close(self) -> None:
        """Tear down in order: stop scheduled callbacks, stop the watcher
        thread, close the DB, then destroy the window. Idempotent and
        exception-safe so a close always completes."""
        if self._closing:
            return
        self._closing = True

        for job in (self._pump_job, self._glow_job):
            if job is not None:
                try:
                    self.after_cancel(job)
                except Exception:
                    pass
        self._pump_job = self._glow_job = None

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
