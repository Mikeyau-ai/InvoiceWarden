"""Activity Log tab - searchable history of what moved where and when.

Rows are read from ``activity_log`` in SQLite. The watcher also streams live
lines here through the app's event queue.

Two view levels:
  * the default *simple* view shows only outcomes - uploads, skips, errors and
    supplier changes - with a plain local date/time;
  * ticking **Advanced** also shows the routine step-by-step chatter (email
    seen, parsed, polled, non-invoice attachments) and the raw UTC timestamp
    each row was stored with.
"""
from __future__ import annotations

from datetime import datetime
from tkinter import messagebox

import customtkinter as ctk

from gui.theme import C, FONT_DATA, FONT_HEAD, accent_button

#: INFO-level actions that are routine progress chatter, not outcomes. Hidden
#: in the simple view, shown when "Advanced" is ticked. A denylist, so anything
#: at WARN/ERROR and every other INFO action (uploaded, new customer, ...) stays
#: visible by default - a new action type shows rather than silently vanishing.
_NOISE_ACTIONS = {"found", "parsed", "poll", "cleanup", "skipped"}

#: Actions that represent a successful filing - coloured green.
_SUCCESS_ACTIONS = {"uploaded", "credit linked"}

#: Delay after the last keystroke before the live filter re-queries, in ms.
#: Long enough to coalesce a burst of typing, short enough to feel immediate.
_FILTER_DEBOUNCE_MS = 150


def is_noise_line(level: str, action: str) -> bool:
    """True for a routine-progress line that the simple view should hide."""
    return level == "INFO" and action in _NOISE_ACTIONS


def line_tag(level: str, action: str) -> str:
    """Text-colour tag for one log line: SUCCESS (green) or the level name."""
    if level == "INFO" and action in _SUCCESS_ACTIONS:
        return "SUCCESS"
    return level


def format_ts(ts: str, advanced: bool) -> str:
    """Render a stored UTC timestamp for the log column.

    Advanced view keeps the raw ISO-8601 UTC string, useful for cross
    referencing against other logs. The simple view converts it to local
    time and drops the seconds/timezone detail non-technical readers don't
    need.
    """
    if not ts:
        return ts
    if advanced:
        return f"{ts:25}"
    try:
        local = datetime.fromisoformat(ts).astimezone()
    except ValueError:
        return ts
    return f"{local.strftime('%b %d  %I:%M %p'):17}"


def format_line(ts, level, platform, customer, ref, action, filename, message) -> str:
    """One monospaced, column-aligned log row (trailing newline included)."""
    return (f"{ts}  {level:5}  {(platform or '-'):>12}  "
            f"{(customer or '-'):20.20}  {(ref or '-'):12.12}  "
            f"{action:10}  {filename:24.24}  {message}\n")


class LogsTab:
    """Scrolling, filterable activity view backed by the DB."""

    def __init__(self, parent, app) -> None:
        """Build the search bar and the scrolling log textbox."""
        self._app = app
        self._db = app.db
        self._filter_job: str | None = None

        root = ctk.CTkFrame(parent, fg_color=C["bg"])
        root.pack(fill="both", expand=True)

        bar = ctk.CTkFrame(root, fg_color=C["bg"])
        bar.pack(fill="x", pady=(0, 6))
        ctk.CTkLabel(bar, text="Activity Log", font=FONT_HEAD,
                     text_color=C["blue"]).pack(side="left", padx=6)
        self._search = ctk.CTkEntry(bar, width=280,
                                    placeholder_text="Search supplier / ref / platform / text")
        self._search.pack(side="left", padx=6)
        self._search.bind("<Return>", lambda _e: self.refresh())
        self._search.bind("<Escape>", lambda _e: self._clear_filter())
        # Live filter: re-query as the user types (debounced), and drop back to
        # the full log automatically when the box is emptied - no button needed.
        self._search.bind("<KeyRelease>", self._schedule_filter)
        accent_button(ctk, bar, "Clear filter", self._clear_filter,
                      colour=C["btn_off"]).pack(side="left", padx=6)
        accent_button(ctk, bar, "Clear log", self._clear_log,
                      colour=C["btn_off"]).pack(side="left")
        # Unticked = simple view (outcomes only); ticked = every progress line.
        self._advanced = ctk.CTkCheckBox(bar, text="Advanced", command=self.refresh)
        self._advanced.pack(side="right", padx=6)

        self._box = ctk.CTkTextbox(root, font=FONT_DATA, wrap="none",
                                   fg_color=C["row"], text_color=C["text"])
        self._box.pack(fill="both", expand=True)
        self._box.configure(state="disabled")
        self._configure_tags()
        self.refresh()

    def _configure_tags(self) -> None:
        """Colour lines by outcome/level (matches RamBo's issue colouring)."""
        for name, colour in (("INFO", C["text"]), ("WARN", C["yellow"]),
                             ("ERROR", C["red"]), ("SUCCESS", C["green"])):
            self._box.tag_config(name, foreground=colour)

    def _advanced_on(self) -> bool:
        """True when the Advanced checkbox is ticked (show every line)."""
        return bool(self._advanced.get())

    # -- filter -----------------------------------------------------
    def _schedule_filter(self, _event=None) -> None:
        """Debounce live-filter re-queries so fast typing doesn't thrash."""
        if self._filter_job is not None:
            self._box.after_cancel(self._filter_job)
        self._filter_job = self._box.after(_FILTER_DEBOUNCE_MS, self._run_filter)

    def _run_filter(self) -> None:
        """Fire the debounced filter refresh (guarded against teardown)."""
        self._filter_job = None
        if self._box.winfo_exists():
            self.refresh()

    def _clear_filter(self) -> None:
        """Reset the search box and show all recent activity again."""
        self._search.delete(0, "end")
        self.refresh()

    def _clear_log(self) -> None:
        """Permanently delete the stored activity history (with confirmation)."""
        if not messagebox.askyesno(
            "Clear activity log",
            "Delete all activity log entries from the database?\n"
            "This cannot be undone. (The error log is not affected.)",
            icon="warning", parent=self._box.winfo_toplevel(),
        ):
            return
        removed = self._db.clear_activity_log()
        self._box.configure(state="normal")
        self._box.delete("1.0", "end")
        self._box.configure(state="disabled")
        self._app.emit_event(level="INFO", action="log",
                             message=f"Activity log cleared ({removed} entries removed).")

    def refresh(self, *_a) -> None:
        """Re-query the DB and repaint, honouring the filter and view level."""
        term = self._search.get().strip()
        advanced = self._advanced_on()
        rows = self._db.search_activity(term)
        self._box.configure(state="normal")
        self._box.delete("1.0", "end")
        for r in reversed(rows):  # oldest first
            if not advanced and is_noise_line(r["level"], r["action"]):
                continue
            line = format_line(format_ts(r["ts"], advanced), r["level"], r["platform"],
                               r["customer_name"], r["invoice_ref"],
                               r["action"], r["filename"], r["message"])
            self._box.insert("end", line, line_tag(r["level"], r["action"]))
        self._box.see("end")
        self._box.configure(state="disabled")

    def append_live(self, event: dict) -> None:
        """Append one streamed watcher event without a full DB re-read."""
        term = self._search.get().strip().lower()
        text = " ".join(str(v) for v in event.values()).lower()
        if term and term not in text:
            return
        level = event.get("level", "INFO")
        action = event.get("action", "")
        advanced = self._advanced_on()
        if not advanced and is_noise_line(level, action):
            return
        line = format_line(format_ts(event.get("ts", ""), advanced), level,
                           event.get("platform", "-"), event.get("customer_name"),
                           event.get("invoice_ref"), action,
                           event.get("filename", ""), event.get("message", ""))
        self._box.configure(state="normal")
        self._box.insert("end", line, line_tag(level, action))
        self._box.see("end")
        self._box.configure(state="disabled")
