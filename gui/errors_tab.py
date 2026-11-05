"""Error Log tab - failed parses / uploads / missing job numbers with retry.

Each unresolved row from ``error_log`` is shown with a Retry button (re-runs
the stored payload through the router) and a Dismiss button (marks resolved).
"""
from __future__ import annotations

import threading

import customtkinter as ctk

from tkinter import messagebox

from core.router import Router
from gui.theme import C, FONT_DATA, FONT_HEAD, FONT_UI, accent_button


class ErrorsTab:
    """Manual remediation view for the error log."""

    def __init__(self, parent, app) -> None:
        """Build the toolbar and the scrolling list of error cards."""
        self._app = app
        self._db = app.db
        self._router = Router(app.db, app.settings, emit=app.emit_event)

        root = ctk.CTkFrame(parent, fg_color=C["bg"])
        root.pack(fill="both", expand=True)

        bar = ctk.CTkFrame(root, fg_color=C["bg"])
        bar.pack(fill="x", pady=(0, 6))
        ctk.CTkLabel(bar, text="Error Log", font=FONT_HEAD,
                     text_color=C["dim"]).pack(side="left", padx=6)
        accent_button(ctk, bar, "Refresh", self.refresh, colour=C["btn_off"]).pack(side="left")
        accent_button(ctk, bar, "Clear all", self._clear_all,
                      colour=C["btn_off"]).pack(side="left", padx=6)
        self._show_resolved = ctk.CTkSwitch(bar, text="Show resolved", command=self.refresh)
        self._show_resolved.pack(side="left", padx=10)

        self._list = ctk.CTkScrollableFrame(root, fg_color=C["panel"])
        self._list.pack(fill="both", expand=True)
        self.refresh()

    def refresh(self) -> None:
        """Rebuild the card list from the error log."""
        for w in self._list.winfo_children():
            w.destroy()
        rows = self._db.list_errors(include_resolved=bool(self._show_resolved.get()))
        if not rows:
            ctk.CTkLabel(self._list, text="No errors.", font=FONT_UI,
                         text_color=C["dim"]).pack(anchor="w", padx=10, pady=10)
            return
        for r in rows:
            card = ctk.CTkFrame(self._list, fg_color=C["row"])
            card.pack(fill="x", padx=6, pady=4)
            head = (f"#{r['id']}  {r['ts']}  [{r['stage']}]  "
                    f"{r['customer_name'] or '-'} / {r['invoice_ref'] or '-'}  "
                    f"retries={r['retry_count']}")
            ctk.CTkLabel(card, text=head, font=FONT_DATA,
                         text_color=C["text"] if not r["resolved"] else C["dim"],
                         anchor="w").pack(fill="x", padx=10, pady=(6, 0))
            ctk.CTkLabel(card, text=r["error"], font=FONT_UI, text_color=C["text"],
                         anchor="w", wraplength=760, justify="left").pack(fill="x", padx=10)
            if r["filename"]:
                ctk.CTkLabel(card, text=f"file: {r['filename']}", font=FONT_UI,
                             text_color=C["dim"], anchor="w").pack(fill="x", padx=10)
            if not r["resolved"]:
                actions = ctk.CTkFrame(card, fg_color=C["row"])
                actions.pack(anchor="e", padx=10, pady=6)
                accent_button(ctk, actions, "Retry", lambda rr=r: self._retry(rr),
                              colour=C["green"], width=80).pack(side="left", padx=4)
                accent_button(ctk, actions, "Dismiss", lambda rr=r: self._dismiss(rr),
                              colour=C["btn_off"], width=90).pack(side="left")

    def _retry(self, row) -> None:
        """Re-run one failed upload from its stored payload, off the Tk thread.

        The upload makes network calls that can take minutes, which froze the
        whole window when run inline. The worker only records its outcome; the
        Tk thread picks it up by polling (``after`` is not called from threads).
        """
        if getattr(self, "_retrying", False):
            return                      # one retry at a time
        self._retrying = True
        self._db.bump_error_retry(row["id"])
        outcome: dict = {}

        def work() -> None:
            """Worker thread: run the retry and note success or the error."""
            try:
                outcome["ok"] = bool(row["payload"]) and                     self._router.retry_from_payload(row["payload"])
            except Exception as exc:
                outcome["ok"] = False
                outcome["error"] = str(exc)

        def poll() -> None:
            """Tk thread: finish up once the worker has reported."""
            if "ok" not in outcome:
                self._list.after(300, poll)
                return
            self._retrying = False
            if outcome.get("error"):
                self._app.emit_event(level="ERROR", action="retry",
                                     message=outcome["error"])
            if outcome["ok"]:
                self._db.mark_error_resolved(row["id"])
            self.refresh()
            self._app.refresh_logs()

        threading.Thread(target=work, daemon=True, name="InvoiceWarden-Retry").start()
        self._list.after(300, poll)

    def _clear_all(self) -> None:
        """Dismiss every open warning/error after a confirmation."""
        if not self._db.list_errors():
            return
        if not messagebox.askyesno(
            "Clear all", "Dismiss every open item without retrying?\n"
            "Held invoices will not be filed.", icon="warning",
            parent=self._list.winfo_toplevel(),
        ):
            return
        self._db.resolve_all_errors()
        self.refresh()

    def _dismiss(self, row) -> None:
        """Mark one error handled without retrying it."""
        self._db.mark_error_resolved(row["id"])
        self.refresh()
