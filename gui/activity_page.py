"""Activity page - the main screen: status, today at a glance, what needs attention,
and recent activity in plain English.

Layout, top to bottom:
  * status card: one On/Off switch, what it's doing ("Checked 2 min ago"), Check now;
  * three tiles: invoices filed today, this week, and how many things need attention;
  * "Needs attention": unresolved problems from the error log, each with what to do;
  * "Recent activity": outcomes only, one readable sentence each. The full technical log
    (search, every step, raw timestamps) is one switch away and is the old Activity Log.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import customtkinter as ctk

from core.router import Router
from gui.logs_tab import LogsTab
from gui.theme import C, FONT_HEAD, FONT_UI, FONT_UI_BOLD, accent_button, shade

#: Activity rows that count as "filed" in the tiles.
FILED_ACTIONS = ("uploaded", "credit linked")

#: How many recent rows the plain list reads (it shows outcomes only, so fewer appear).
_RECENT_ROWS = 300

#: How often the "Checked 2 min ago" line is refreshed, in ms.
_STATUS_TICK_MS = 15_000

_FONT_TITLE = ("Segoe UI Semibold", 15)
_FONT_TILE_NUM = ("Segoe UI Semibold", 22)
_FONT_SMALL = ("Segoe UI", 12)


def _ago(when: datetime | None, now: datetime) -> str:
    """'just now', '3 min ago', '2 h ago' for a past UTC time."""
    if when is None:
        return "not yet"
    mins = int((now - when).total_seconds() // 60)
    if mins < 1:
        return "just now"
    if mins < 60:
        return f"{mins} min ago"
    return f"{mins // 60} h ago"


def status_text(running: bool, mailboxes: int, last_poll: datetime | None,
                poll_minutes: int, now: datetime) -> tuple[str, str]:
    """(title, subtitle) for the status card."""
    if not running:
        return ("Off", "Turn it on to start filing invoices from your email.")
    boxes = f"{mailboxes} mailbox{'es' if mailboxes != 1 else ''}"
    title = f"On: watching {boxes}"
    if last_poll is None:
        return (title, "Starting up. The first check runs in a moment.")
    nxt = last_poll + timedelta(minutes=max(1, poll_minutes))
    left = int((nxt - now).total_seconds() // 60)
    tail = "next check any moment" if left < 1 else f"next check in {left} min"
    return (title, f"Checked {_ago(last_poll, now)} · {tail}")


def _local_time(ts: str) -> str:
    """Stored UTC ISO time -> local 'Tue 10:42' (today shows just the time)."""
    try:
        local = datetime.fromisoformat(ts).astimezone()
    except (TypeError, ValueError):
        return ""
    if local.date() == datetime.now().astimezone().date():
        return local.strftime("%I:%M %p").lstrip("0")
    return local.strftime("%a %d %b  %I:%M %p").replace("  0", "  ")


def plain_line(row) -> tuple[str, str] | None:
    """One activity row as (sentence, colour tag), or None if it isn't worth showing.

    Only outcomes are shown here: invoices filed or skipped, suppliers added, and
    things that were held back. Routine steps (polls, parsing, housekeeping) stay in
    the full log.
    """
    action = row["action"]
    who = row["customer_name"] or "Unknown supplier"
    ref = f" · invoice {row['invoice_ref']}" if row["invoice_ref"] else ""
    where = row["platform"] if row["platform"] and row["platform"] != "-" else ""
    if action in FILED_ACTIONS:
        detail = row["message"].split("] ", 1)[-1] if row["message"] else ""
        what = "credit note linked" if action == "credit linked" else "filed"
        text = f"✓  {who}{ref}: {what}" + (f" to {where}" if where else "")
        if detail and detail.lower() not in ("ok", "uploaded"):
            text += f" ({detail})"
        return text, "ok"
    if action == "duplicate":
        return f"•  {who}{ref}: already filed" + (f" in {where}" if where else "") + ", skipped", "dim"
    if action in ("new customer", "added"):
        return f"+  New supplier added: {who}. Check their details in Suppliers.", "info"
    if action in ("held", "queued"):
        return f"!  {who}{ref}: held back, see Needs attention", "warn"
    if action == "no_route":
        return f"!  {who}{ref}: not filed, this supplier has nowhere to send invoices", "warn"
    if action == "catch_up":
        return f"•  {row['message']}", "dim"
    return None


def problem_text(row) -> tuple[str, str]:
    """(headline, what to do) for one unresolved error-log row."""
    who = row["customer_name"] or "Unknown supplier"
    ref = f" · invoice {row['invoice_ref']}" if row["invoice_ref"] else ""
    err = (row["error"] or "").strip()
    stage = row["stage"]
    if stage == "supplier":
        return (f"New supplier not on your list: {who}{ref}",
                "Add them in Suppliers (or check the name), then press Retry.")
    if stage == "parse":
        return (f"Couldn't read an invoice in: {row['filename'] or 'an email'}",
                err or "The attachment couldn't be read. Check it and file it by hand.")
    platform = ""
    if err.startswith("[") and "] " in err:
        platform, err = err[1:].split("] ", 1)
    to = f" to {platform}" if platform else ""
    err = err or "The upload failed"
    if err[-1] not in ".!?":
        err += "."
    return (f"Couldn't file {who}{ref}{to}", f"{err} Press Retry once it's fixed.")


class ActivityPage:
    """The main screen. Owns the status card, tiles, problems list and recent list."""

    def __init__(self, parent, app) -> None:
        """Build every section; data is filled in by refresh()."""
        self._app = app
        self._db = app.db
        self._router = Router(app.db, app.settings, emit=app.emit_event)
        self._running = False
        self._busy_sweep = False
        self._refresh_job: str | None = None

        self.frame = ctk.CTkFrame(parent, fg_color=C["bg"])
        self._build_status()
        self._build_tiles()
        self._build_attention()
        self._build_recent()
        self.refresh()
        self._tick()

    # -- building ----------------------------------------------------
    def _build_status(self) -> None:
        """Status card: switch, title + subtitle, Check now."""
        card = ctk.CTkFrame(self.frame, fg_color=C["panel"], corner_radius=8)
        card.pack(fill="x", padx=14, pady=(14, 8))
        self._switch = ctk.CTkSwitch(card, text="", width=56, switch_width=52, switch_height=26,
                                     command=self._on_switch)
        self._switch.pack(side="left", padx=(16, 6), pady=14)
        text = ctk.CTkFrame(card, fg_color=C["panel"])
        text.pack(side="left", fill="x", expand=True, pady=10)
        self._title = ctk.CTkLabel(text, text="", font=_FONT_TITLE, text_color=C["text"], anchor="w")
        self._title.pack(fill="x")
        self._subtitle = ctk.CTkLabel(text, text="", font=FONT_UI, text_color=C["dim"], anchor="w")
        self._subtitle.pack(fill="x")
        self._check_btn = accent_button(ctk, card, "Check now", self._app.scan_now,
                                        colour=C["btn_off"], width=110)
        self._check_btn.pack(side="right", padx=16)

    def _build_tiles(self) -> None:
        """Three number tiles: filed today, this week, needs attention."""
        row = ctk.CTkFrame(self.frame, fg_color=C["bg"])
        row.pack(fill="x", padx=14, pady=(0, 8))
        self._tiles = {}
        for i, (key, label) in enumerate((("today", "Filed today"), ("week", "Filed this week"),
                                          ("attention", "Needs attention"))):
            row.grid_columnconfigure(i, weight=1, uniform="tile")
            tile = ctk.CTkFrame(row, fg_color=C["panel"], corner_radius=8)
            tile.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 4, 0 if i == 2 else 4))
            cap = ctk.CTkLabel(tile, text=label, font=_FONT_SMALL, text_color=C["dim"], anchor="w")
            cap.pack(fill="x", padx=14, pady=(8, 0))
            num = ctk.CTkLabel(tile, text="0", font=_FONT_TILE_NUM, text_color=C["text"], anchor="w")
            num.pack(fill="x", padx=14, pady=(0, 8))
            self._tiles[key] = (tile, cap, num)

    def _build_attention(self) -> None:
        """'Needs attention' section; hidden while there's nothing in it."""
        self._attn = ctk.CTkFrame(self.frame, fg_color=C["bg"])
        ctk.CTkLabel(self._attn, text="Needs attention", font=FONT_HEAD,
                     text_color=C["amber"], anchor="w").pack(fill="x", padx=2, pady=(0, 4))
        self._attn_list = ctk.CTkScrollableFrame(self._attn, fg_color=C["bg"], height=150)
        self._attn_list.pack(fill="x")

    def _build_recent(self) -> None:
        """'Recent activity' header (with Go back further / Full log) and the two views."""
        self._recent = ctk.CTkFrame(self.frame, fg_color=C["bg"])
        self._recent.pack(fill="both", expand=True, padx=14, pady=(4, 14))
        head = ctk.CTkFrame(self._recent, fg_color=C["bg"])
        head.pack(fill="x", pady=(0, 4))
        ctk.CTkLabel(head, text="Recent activity", font=FONT_HEAD, text_color=C["text"],
                     anchor="w").pack(side="left", padx=2)
        self._full = ctk.CTkSwitch(head, text="Full log", command=self._toggle_full, font=_FONT_SMALL)
        self._full.pack(side="right", padx=(10, 2))
        self._sweep_btn = ctk.CTkButton(head, text="Go back further…", width=10, height=24,
                                        fg_color="transparent", hover_color=C["row"],
                                        text_color=C["teal"], font=_FONT_SMALL,
                                        command=self._app.catch_up)
        self._sweep_btn.pack(side="right")

        self._plain = ctk.CTkTextbox(self._recent, font=FONT_UI, wrap="word",
                                     fg_color=C["panel"], text_color=C["text"])
        self._plain.pack(fill="both", expand=True)
        for tag, colour in (("ok", C["teal"]), ("warn", C["amber"]), ("info", C["text"]),
                            ("dim", C["dim"]), ("time", C["dimmer"])):
            self._plain.tag_config(tag, foreground=colour)
        self._plain.configure(state="disabled")
        # The old detailed log, built once and shown instead of the plain list on demand.
        self._full_holder = ctk.CTkFrame(self._recent, fg_color=C["bg"])
        self.full_log = LogsTab(self._full_holder, self._app)

    # -- actions -------------------------------------------------------
    def _on_switch(self) -> None:
        """The On/Off switch starts or stops the watcher."""
        self._app.set_watching(bool(self._switch.get()))

    def _toggle_full(self) -> None:
        """Swap between the plain recent list and the full technical log."""
        if self._full.get():
            self._plain.pack_forget()
            self._full_holder.pack(fill="both", expand=True)
            self.full_log.refresh()
        else:
            self._full_holder.pack_forget()
            self._plain.pack(fill="both", expand=True)

    def _retry(self, row) -> None:
        """Re-run one failed filing from its stored snapshot."""
        self._db.bump_error_retry(row["id"])
        ok = False
        try:
            if row["payload"]:
                ok = self._router.retry_from_payload(row["payload"])
        except Exception as exc:
            self._app.emit_event(level="ERROR", action="retry", message=str(exc))
        if ok:
            self._db.mark_error_resolved(row["id"])
        self.refresh()

    def _dismiss(self, row) -> None:
        """Mark one problem as handled without retrying it."""
        self._db.mark_error_resolved(row["id"])
        self.refresh()

    # -- state from the app ----------------------------------------------
    def set_running(self, running: bool) -> None:
        """Reflect the watcher's on/off state."""
        self._running = running
        if bool(self._switch.get()) != running:
            self._switch.select() if running else self._switch.deselect()
        self._update_status()

    def set_sweeping(self, busy: bool, glyph: str = "") -> None:
        """Show a 'Going back…' spinner on the link while a catch-up sweep runs."""
        self._busy_sweep = busy
        self._sweep_btn.configure(text=f"Going back… {glyph}" if busy else "Go back further…",
                                  state="disabled" if busy else "normal")

    def on_event(self, event: dict) -> None:
        """A live watcher event: feed the full log, and refresh the summary soon."""
        self.full_log.append_live(event)
        if self._refresh_job is None:
            self._refresh_job = self.frame.after(800, self._deferred_refresh)

    def _deferred_refresh(self) -> None:
        """Coalesce a burst of events into one repaint."""
        self._refresh_job = None
        if self.frame.winfo_exists():
            self.refresh()

    # -- painting -------------------------------------------------------
    def _tick(self) -> None:
        """Keep 'Checked 2 min ago' current."""
        if not self.frame.winfo_exists():
            return
        self._update_status()
        self.frame.after(_STATUS_TICK_MS, self._tick)

    def _update_status(self) -> None:
        """Repaint the status card from the watcher's state."""
        app = self._app
        mailboxes = len(app.db.list_mail_accounts(enabled_only=True)) or 1
        title, sub = status_text(self._running, mailboxes,
                                 getattr(app.watcher, "last_poll", None) if hasattr(app, "watcher") else None,
                                 app.settings.get_int("watcher.poll_minutes", 5),
                                 datetime.now(timezone.utc))
        self._title.configure(text=title, text_color=C["text"] if self._running else C["dim"])
        self._subtitle.configure(text=sub)

    def refresh(self) -> None:
        """Re-read the tiles, the problems and the recent list from the database."""
        now_local = datetime.now().astimezone()
        midnight = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = midnight - timedelta(days=midnight.weekday())
        iso = lambda d: d.astimezone(timezone.utc).isoformat(timespec="seconds")
        today = self._db.count_activity(FILED_ACTIONS, iso(midnight))
        week = self._db.count_activity(FILED_ACTIONS, iso(week_start))
        problems = self._db.list_errors()
        new_suppliers = self._db.count_unreviewed_customers()
        attention = len(problems) + (1 if new_suppliers else 0)

        self._tiles["today"][2].configure(text=str(today))
        self._tiles["week"][2].configure(text=str(week))
        tile, cap, num = self._tiles["attention"]
        hot = attention > 0
        tile.configure(fg_color=C["amber_bg"] if hot else C["panel"])
        cap.configure(text_color=C["amber"] if hot else C["dim"])
        num.configure(text=str(attention), text_color=C["amber"] if hot else C["text"])

        self._paint_attention(problems, new_suppliers)
        self._paint_recent()

    def _paint_attention(self, problems, new_suppliers: int) -> None:
        """Rebuild the problem cards (shown only when there's something to do)."""
        for w in self._attn_list.winfo_children():
            w.destroy()
        if not problems and not new_suppliers:
            self._attn.pack_forget()
            return
        self._attn.pack(fill="x", padx=14, pady=(0, 6), before=self._recent)
        if new_suppliers:
            self._card(f"{new_suppliers} new supplier{'s' if new_suppliers != 1 else ''} "
                       f"added automatically",
                       "Check their names and where their invoices go.",
                       [("Review", lambda: self._app.show_page("suppliers"))])
        for row in problems[:50]:
            head, todo = problem_text(row)
            buttons = []
            if row["stage"] == "supplier":
                buttons.append(("Suppliers", lambda: self._app.show_page("suppliers")))
            if row["payload"]:
                buttons.append(("Retry", lambda r=row: self._retry(r)))
            buttons.append(("Dismiss", lambda r=row: self._dismiss(r)))
            self._card(head, todo, buttons, when=_local_time(row["ts"]))
        rows = min(len(problems) + (1 if new_suppliers else 0), 3)
        self._attn_list.configure(height=rows * 72)

    def _card(self, head: str, todo: str, buttons, when: str = "") -> None:
        """One amber problem row: headline, what to do, buttons on the right."""
        card = ctk.CTkFrame(self._attn_list, fg_color=C["amber_bg"], corner_radius=6)
        card.pack(fill="x", pady=(0, 4), padx=(0, 4))
        btns = ctk.CTkFrame(card, fg_color=C["amber_bg"])
        btns.pack(side="right", padx=8)
        for label, cmd in buttons:
            accent_button(ctk, btns, label, cmd, colour=C["btn_off"], width=84,
                          height=26).pack(side="left", padx=2)
        text = ctk.CTkFrame(card, fg_color=C["amber_bg"])
        text.pack(side="left", fill="x", expand=True, padx=12, pady=6)
        ctk.CTkLabel(text, text=head + (f"   {when}" if when else ""), font=FONT_UI_BOLD,
                     text_color=C["amber"], anchor="w", justify="left",
                     wraplength=620).pack(fill="x")
        ctk.CTkLabel(text, text=todo, font=_FONT_SMALL, text_color=shade(C["amber"], 0.85),
                     anchor="w", justify="left", wraplength=620).pack(fill="x")

    def _paint_recent(self) -> None:
        """Newest outcomes first, one sentence each."""
        box = self._plain
        box.configure(state="normal")
        box.delete("1.0", "end")
        shown = 0
        for row in self._db.search_activity("", limit=_RECENT_ROWS):
            line = plain_line(row)
            if line is None:
                continue
            text, tag = line
            box.insert("end", f"{_local_time(row['ts']):>18}   ", "time")
            box.insert("end", text + "\n", tag)
            shown += 1
        if not shown:
            box.insert("end", "\n   Nothing filed yet. When an invoice arrives in your email, "
                              "it'll show up here.\n", "dim")
        box.configure(state="disabled")
