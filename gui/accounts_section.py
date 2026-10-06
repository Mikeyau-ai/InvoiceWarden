"""Email accounts section of the Settings window.

One row per monitored mailbox, up to :data:`config.MAX_MAIL_ACCOUNTS`. Each
account keeps its own credentials because they cannot be shared: a Graph
mailbox needs its own sign-in token, and an IMAP mailbox its own app password.
Only the COM backend could share a profile, and even there the mailbox name
differs per account.
"""
from __future__ import annotations

import customtkinter as ctk

from config import MAX_MAIL_ACCOUNTS
from gui.theme import C, FONT_HEAD, FONT_UI, accent_button

BACKENDS = ["graph", "imap", "com"]

#: Per-backend hint shown under a row, so the right fields are obvious.
_HINTS = {
    "graph": ("Microsoft 365 / Outlook.com. Click Sign in - each mailbox needs "
              "its own sign-in. No Azure setup required."),
    "imap": ("Gmail, Fastmail, Yahoo, iCloud, business hosts. Needs an APP "
             "PASSWORD. Does not work with outlook.com."),
    "com": ("Classic Outlook desktop on this PC. Put the mailbox name exactly "
            "as Outlook shows it."),
}


class AccountsSection:
    """Builds and manages the mailbox list inside the Settings window."""

    def __init__(self, parent, app, status) -> None:
        """Render the section into ``parent``; ``status`` reports messages."""
        self._app = app
        self._db = app.db
        self._settings = app.settings
        self._status = status
        self._rows: list[dict] = []

        self.frame = ctk.CTkFrame(parent, fg_color="transparent")
        self.frame.pack(fill="x")
        self.refresh()

    def _guide(self) -> None:
        """Open the multi-mailbox setup guide."""
        from gui.help_content import SETUP_GUIDES
        from gui.help_dialog import GuideWindow

        GuideWindow(self._app, [("email_accounts", SETUP_GUIDES["email_accounts"]),
                                ("outlook_graph", SETUP_GUIDES["outlook_graph"]),
                                ("outlook_imap", SETUP_GUIDES["outlook_imap"])])

    # -- rendering ----------------------------------------------
    def refresh(self) -> None:
        """Rebuild the whole section from the database."""
        for w in self.frame.winfo_children():
            w.destroy()
        self._rows = []

        accounts = self._db.list_mail_accounts()
        head = ctk.CTkFrame(self.frame, fg_color="transparent")
        head.pack(fill="x", padx=6, pady=(10, 2))
        ctk.CTkLabel(head, text=(f"Mailboxes  ({len(accounts)} of {MAX_MAIL_ACCOUNTS})"
                                 if accounts else "Mailboxes"),
                     font=FONT_HEAD, text_color=C["teal"]).pack(side="left")
        add = accent_button(ctk, head, "+ Add a mailbox", self._add,
                            colour=C["btn_off"])
        add.pack(side="right")
        accent_button(ctk, head, "Setup guide", self._guide,
                      colour=C["btn_off"], width=100).pack(side="right", padx=8)
        if len(accounts) >= MAX_MAIL_ACCOUNTS:
            add.configure(state="disabled",
                          text=f"Maximum {MAX_MAIL_ACCOUNTS} reached")

        if not accounts:
            ctk.CTkLabel(self.frame,
                         text="Using your mailbox below. Add mailboxes here only if "
                              "invoices arrive in more than one inbox.",
                         font=FONT_UI, text_color=C["dim"], anchor="w",
                         justify="left", wraplength=760).pack(anchor="w", padx=6)
            return

        for row in accounts:
            self._build_row(row)

    def _build_row(self, row) -> None:
        """One mailbox card: address, backend, per-backend fields, actions."""
        card = ctk.CTkFrame(self.frame, fg_color=C["row"])
        card.pack(fill="x", padx=6, pady=4)

        top = ctk.CTkFrame(card, fg_color=C["row"])
        top.pack(fill="x", padx=10, pady=(8, 2))

        enabled = ctk.CTkSwitch(top, text="", width=44)
        (enabled.select if row["enabled"] else enabled.deselect)()
        enabled.pack(side="left")

        address = ctk.CTkEntry(top, width=260, placeholder_text="name@company.com")
        address.insert(0, row["address"] or "")
        address.pack(side="left", padx=(4, 8))

        backend = ctk.CTkOptionMenu(top, values=BACKENDS, width=90,
                                    command=lambda _v, r=row: self._save_row_then_refresh(r))
        backend.set(row["backend"] or "graph")
        backend.pack(side="left")

        folder = ctk.CTkEntry(top, width=130, placeholder_text="Inbox")
        folder.insert(0, row["folder"] or "")
        folder.pack(side="left", padx=8)

        accent_button(ctk, top, "Remove", lambda r=row: self._remove(r),
                      colour=C["btn_off"], width=80).pack(side="right")

        entry = {"id": row["id"], "enabled": enabled, "address": address,
                 "backend": backend, "folder": folder}

        # IMAP needs server + credentials on their own line.
        if (row["backend"] or "") == "imap":
            imap = ctk.CTkFrame(card, fg_color=C["row"])
            imap.pack(fill="x", padx=10, pady=(0, 4))
            entry["imap_host"] = self._mini(imap, "Server", row["imap_host"], 190)
            entry["imap_port"] = self._mini(imap, "Port", row["imap_port"] or "993", 60)
            entry["imap_username"] = self._mini(imap, "Username", row["imap_username"], 190)
            entry["imap_password"] = self._mini(
                imap, "App password",
                self._settings.decrypt_value(row["imap_password"]), 170, secret=True)

        actions = ctk.CTkFrame(card, fg_color=C["row"])
        actions.pack(fill="x", padx=10, pady=(0, 8))
        accent_button(ctk, actions, "Save", lambda r=row: self._save_row_then_refresh(r),
                      colour=C["teal_btn"], width=80).pack(side="left")
        accent_button(ctk, actions, "Test", lambda r=row: self._test(r),
                      colour=C["btn_off"], width=80).pack(side="left", padx=8)
        if (row["backend"] or "") == "graph":
            accent_button(ctk, actions, "Sign in", lambda r=row: self._sign_in(r),
                          colour=C["purple"], width=90).pack(side="left")
            who = self._signed_in_as(row)
            ctk.CTkLabel(actions,
                         text=f"signed in as {who}" if who else "not signed in",
                         font=FONT_UI,
                         text_color=C["teal"] if who else C["amber"]
                         ).pack(side="left", padx=10)

        ctk.CTkLabel(card, text=_HINTS.get(row["backend"] or "", ""), font=FONT_UI,
                     text_color=C["dim"], anchor="w", justify="left",
                     wraplength=740).pack(anchor="w", padx=10, pady=(0, 8))
        self._rows.append(entry)

    def _mini(self, parent, label: str, value: str, width: int,
              secret: bool = False) -> ctk.CTkEntry:
        """Small labelled entry used for the IMAP fields."""
        ctk.CTkLabel(parent, text=label, font=FONT_UI,
                     text_color=C["text"]).pack(side="left", padx=(0, 4))
        e = ctk.CTkEntry(parent, width=width, show="•" if secret else "")
        e.insert(0, value or "")
        e.pack(side="left", padx=(0, 10))
        return e

    # -- actions ------------------------------------------------
    def _widgets_for(self, row) -> dict | None:
        """The live widgets belonging to one account row."""
        return next((r for r in self._rows if r["id"] == row["id"]), None)

    def save_all(self) -> None:
        """Persist every visible row - called by the Settings Save button."""
        for w in self._rows:
            self._persist(w)

    def _persist(self, w: dict) -> None:
        """Write one row's widget values back to the database."""
        fields = {
            "enabled": 1 if w["enabled"].get() else 0,
            "address": w["address"].get().strip(),
            "backend": w["backend"].get(),
            "folder": w["folder"].get().strip(),
        }
        if "imap_host" in w:
            fields.update(
                imap_host=w["imap_host"].get().strip(),
                imap_port=w["imap_port"].get().strip() or "993",
                imap_username=w["imap_username"].get().strip(),
                imap_password=self._settings.encrypt_value(w["imap_password"].get()),
            )
        self._db.update_mail_account(w["id"], **fields)

    def _save_row_then_refresh(self, row) -> None:
        """Save one row and re-render (the backend choice changes its fields)."""
        w = self._widgets_for(row)
        if w:
            self._persist(w)
        self.refresh()
        self._status.configure(text="Mailbox saved.", text_color=C["teal"])

    def _add(self) -> None:
        """Append a new empty mailbox, up to the cap."""
        if len(self._db.list_mail_accounts()) >= MAX_MAIL_ACCOUNTS:
            self._status.configure(
                text=f"Maximum of {MAX_MAIL_ACCOUNTS} mailboxes reached.",
                text_color=C["amber"])
            return
        self.save_all()          # don't lose edits in the other rows
        self._db.add_mail_account(backend="graph", folder="Inbox")
        self.refresh()
        self._status.configure(
            text="Mailbox added - enter the address, then Sign in (Graph) or "
                 "fill the IMAP fields.", text_color=C["dim"])

    def _remove(self, row) -> None:
        """Delete a mailbox and its stored credentials."""
        self._db.delete_mail_account(row["id"])
        self.refresh()
        self._status.configure(text="Mailbox removed.", text_color=C["amber"])

    def _signed_in_as(self, row) -> str:
        """Account name cached for this Graph mailbox, or ''."""
        try:
            from integrations.graph_auth import AccountTokenStore, signed_in_account

            return signed_in_account(AccountTokenStore(self._settings, self._db,
                                                       row["id"]))
        except Exception:
            return ""

    def _sign_in(self, row) -> None:
        """Run the device-code sign-in for this mailbox only."""
        from gui.graph_signin_dialog import GraphSignInDialog
        from integrations.graph_auth import AccountTokenStore

        self.save_all()
        GraphSignInDialog(self._app,
                          AccountTokenStore(self._settings, self._db, row["id"]),
                          on_done=self.refresh)

    def _test(self, row) -> None:
        """Headers-only probe of one mailbox, off the UI thread."""
        import threading

        from integrations.email_outlook import build_backend_for

        self.save_all()
        fresh = next((r for r in self._db.list_mail_accounts()
                      if r["id"] == row["id"]), None)
        if fresh is None:
            return
        label = fresh["address"] or "mailbox"
        self._status.configure(text=f"Testing {label}...", text_color=C["dim"])

        def work() -> None:
            try:
                backend = build_backend_for(self._settings, self._db, fresh)
                msgs = backend.fetch(since=None, unread_only=False,
                                     allowed_ext=set(), headers_only=True)
                detail = getattr(backend, "last_scan", "") or f"{len(msgs)} message(s)."
                text, colour = f"[{label}] {detail}", (C["teal"] if msgs else C["amber"])
            except Exception as exc:
                text, colour = f"[{label}] {exc}", C["red"]
            try:
                self._app.after(0, lambda: self._status.configure(text=text,
                                                                  text_color=colour))
            except Exception:
                pass

        threading.Thread(target=work, daemon=True, name="mailbox-test").start()
