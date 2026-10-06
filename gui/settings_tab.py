"""Settings window.

Layout, as cards:
  * Email - the mailboxes to watch (or the single mailbox), how to read them, Test.
  * Where invoices go - the service system and the accounting system, each with Test.
  * AI - which AI reads the invoices, its key, Test.
  * General - start with Windows, turn on when opened, unread emails only.
  * Advanced (folded) - check interval, attachment cache, new-supplier confidence,
    AI model name, updates.
Credential fields are rendered DYNAMICALLY - only the fields for the currently
selected systems are shown. Changing a dropdown re-renders in place.

Secrets are Fernet-encrypted by :class:`Settings`; hidden providers keep their
stored values (switching back reveals them again).
"""
from __future__ import annotations

import threading

import customtkinter as ctk

from core import startup as win_startup
from gui.help_content import FIELD_HELP, SETUP_GUIDES
from core.parser_ai import AI_PROVIDERS, test_ai_provider
from gui.help_dialog import GuideWindow, HelpPopup
from gui.theme import C, FONT_HEAD, FONT_UI, accent_button
from integrations.email_outlook import build_backend
from integrations.registry import (
    ACCOUNTING_ENABLED,
    ACCOUNTING_PROVIDERS,
    SERVICE_PROVIDERS,
    build_provider,
    selectable_service_providers,
)

#: Plain-English names for the three ways of reading mail (stored as com/graph/imap).
BACKEND_LABELS = {
    "com": "Outlook app on this PC (classic Outlook)",
    "graph": "Microsoft 365 / Outlook.com (sign in)",
    "imap": "Other email: Gmail, Fastmail, iCloud... (IMAP)",
}

_FONT_CARD = ("Segoe UI Semibold", 15)

#: Canonical device-code sign-in page (works for personal and work accounts).
DEVICE_LOGIN_URL = "https://microsoft.com/devicelogin"

# Outlook field groups keyed by backend.
OUTLOOK_COMMON = [
    ("outlook.account", "Mailbox / account to monitor", False),
    ("outlook.folder", "Folder name", False),
]
OUTLOOK_BY_BACKEND = {
    "com": [],  # COM uses the signed-in desktop Outlook - no credentials
    # Device-code sign-in needs only the Client ID; the tenant defaults to
    # "common" which covers personal outlook.com and work/school accounts.
    "graph": [
        ("outlook.graph_client_id", "Application (client) ID", True),
        ("outlook.graph_tenant", "Tenant (blank = common)", False),
    ],
    # IMAP needs a server + app password; the preset dropdown fills host/port.
    "imap": [
        ("imap.host", "IMAP server", False),
        ("imap.port", "Port", False),
        ("imap.username", "Username / email address", False),
        ("imap.password", "App password", True),
        ("imap.folder", "IMAP folder", False),
    ],
}


class _StatusProxy:
    """Gives a CTkTextbox the ``.configure(text=..., text_color=...)`` API the
    rest of this module already calls on the old status label."""

    def __init__(self, box) -> None:
        """Wrap one textbox so it can stand in for a status label."""
        self._box = box

    def configure(self, text: str = "", text_color: str | None = None, **_kw) -> None:
        """Replace the box contents, optionally recolouring it.

        Silently does nothing once the widget is gone - a background test can
        still report back after its Settings window has been closed.
        """
        try:
            if not self._box.winfo_exists():
                return
            self._box.configure(state="normal")
            self._box.delete("1.0", "end")
            self._box.insert("1.0", text)
            if text_color:
                self._box.configure(text_color=text_color)
            self._box.configure(state="disabled")
        except Exception:
            pass


class _Fixed:
    """Stands in for the mail-reading dropdown when it isn't shown (``.get()`` only)."""

    def __init__(self, value: str) -> None:
        """Remember the label the dropdown would have shown."""
        self._value = value

    def get(self) -> str:
        """The remembered label."""
        return self._value

    def set(self, value: str) -> None:
        """Remember a new label (mirrors the dropdown's API)."""
        self._value = value


class SettingsTab:
    """Builds and manages the Settings tab widgets."""

    def __init__(self, parent, app) -> None:
        """Build the scrolling cards plus the pinned Save footer."""
        self._app = app
        self._settings = app.settings
        self._fields: dict[str, ctk.CTkEntry] = {}

        # Root splits into a fixed footer (Save + status) and the scrolling body
        # above it, so Save can never be pushed off-screen.
        self._root = ctk.CTkFrame(parent, fg_color=C["bg"])
        self._root.pack(fill="both", expand=True)
        self._footer = ctk.CTkFrame(self._root, fg_color=C["panel"], corner_radius=0)
        self._footer.pack(side="bottom", fill="x")
        self.frame = ctk.CTkScrollableFrame(self._root, fg_color=C["bg"])
        self.frame.pack(side="top", fill="both", expand=True)

        self._build_email_card()
        self._build_destinations_card()
        self._build_ai_card()
        self._build_general_card()
        self._build_advanced()
        self._build_actions()

        from gui.accounts_section import AccountsSection

        self.accounts = AccountsSection(self._accounts_box, app, self._status)
        self.load()

    # -- cards --------------------------------------------------
    def _card(self, title: str, blurb: str = ""):
        """A rounded card with a title and a one-line explanation; returns its body."""
        card = ctk.CTkFrame(self.frame, fg_color=C["panel"], corner_radius=8)
        card.pack(fill="x", padx=8, pady=(10, 0))
        ctk.CTkLabel(card, text=title, font=_FONT_CARD, text_color=C["text"],
                     anchor="w").pack(fill="x", padx=14, pady=(12, 0))
        if blurb:
            ctk.CTkLabel(card, text=blurb, font=FONT_UI, text_color=C["dim"], anchor="w",
                         justify="left", wraplength=720).pack(fill="x", padx=14)
        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="x", padx=8, pady=(4, 10))
        return body

    def _buttons(self, parent, buttons) -> None:
        """A row of small buttons (Test, Setup guide...) at the bottom of a card."""
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=6, pady=(8, 0))
        for label, cmd in buttons:
            accent_button(ctk, row, label, cmd, colour=C["btn_off"]).pack(side="left", padx=(0, 8))

    def _build_email_card(self) -> None:
        """Mailboxes (list) and the single-mailbox fields with how to read them."""
        body = self._card("Email", "The inbox (or inboxes) your suppliers send invoices to.")
        self._accounts_box = ctk.CTkFrame(body, fg_color="transparent")
        self._accounts_box.pack(fill="x")
        self._outlook_box = ctk.CTkFrame(body, fg_color="transparent")
        self._outlook_box.pack(fill="x")
        self._buttons(body, [("Test mailbox", self._test_outlook)])

    def _build_destinations_card(self) -> None:
        """Service system and accounting system, each with its own fields."""
        body = self._card("Where invoices go",
                          "The job system filed invoices are attached to. Each supplier "
                          "can be switched on or off on the Suppliers page.")
        current = self._settings.get("service.provider", "servicem8")
        self._service = self._dropdown(
            body, "Job system",
            [c.label for c in selectable_service_providers(current).values()], self._render)
        self._svc_box = ctk.CTkFrame(body, fg_color="transparent")
        self._svc_box.pack(fill="x")
        self._acct_box = ctk.CTkFrame(body, fg_color="transparent")
        if ACCOUNTING_ENABLED:
            self._accounting = self._dropdown(
                body, "Accounting system",
                [c.label for c in ACCOUNTING_PROVIDERS.values()], self._render)
            self._acct_box.pack(fill="x")
        else:
            # Not offered for now; keeps the stored choice untouched (see _save).
            self._accounting = _Fixed(ACCOUNTING_PROVIDERS["none"].label)

    def _build_ai_card(self) -> None:
        """Which AI reads the invoices, and its key."""
        body = self._card("AI", "Reads each invoice to find the supplier, invoice number "
                                "and job number.")
        self._ai_provider = self._dropdown(
            body, "AI provider",
            [m["label"] for m in AI_PROVIDERS.values()], self._render)
        self._ai_box = ctk.CTkFrame(body, fg_color="transparent")
        self._ai_box.pack(fill="x")
        self._buttons(body, [("Test AI", self._test_ai)])

    def _build_general_card(self) -> None:
        """The everyday switches."""
        body = self._card("General")
        self._run_startup = ctk.CTkSwitch(body, text="Start with Windows")
        self._run_startup.pack(anchor="w", padx=6, pady=4)
        self._autostart = ctk.CTkSwitch(body, text="Turn on automatically when the app opens")
        self._autostart.pack(anchor="w", padx=6, pady=4)
        self._unread_only = ctk.CTkSwitch(
            body, text="Only unread emails  (off = every email since the last check)")
        self._unread_only.pack(anchor="w", padx=6, pady=4)

    def _build_advanced(self) -> None:
        """Folded section: tuning numbers, AI model name and updates."""
        self._adv_btn = ctk.CTkButton(self.frame, text="Advanced  ▸", width=10, height=28,
                                      fg_color="transparent", hover_color=C["row"],
                                      text_color=C["teal"], font=FONT_UI, anchor="w",
                                      command=self._toggle_advanced)
        self._adv_btn.pack(anchor="w", padx=8, pady=(12, 0))
        self._adv = ctk.CTkFrame(self.frame, fg_color=C["panel"], corner_radius=8)
        adv = ctk.CTkFrame(self._adv, fg_color="transparent")
        adv.pack(fill="x", padx=8, pady=8)

        def number(label: str) -> ctk.CTkEntry:
            """One labelled number box."""
            wrap = ctk.CTkFrame(adv, fg_color="transparent")
            wrap.pack(fill="x", padx=6, pady=3)
            ctk.CTkLabel(wrap, text=label, font=FONT_UI, text_color=C["text"],
                         width=250, anchor="w").pack(side="left")
            entry = ctk.CTkEntry(wrap, width=80)
            entry.pack(side="left")
            return entry

        self._poll = number("Check email every (minutes)")
        self._cache_days = number("Keep downloaded attachments (days)")
        self._note(adv, "Kept so a failed upload can still be retried, then deleted. "
                        "0 keeps them forever.")
        self._min_conf = number("New-supplier confidence (0 to 1)")
        self._note(adv, "Below this, a supplier the app has never seen isn't added "
                        "automatically; the invoice waits in Needs attention instead.")
        self._ai_model_box = ctk.CTkFrame(adv, fg_color="transparent")
        self._ai_model_box.pack(fill="x")
        self._build_updates(adv)

    def _toggle_advanced(self) -> None:
        """Fold or unfold the Advanced section."""
        if self._adv.winfo_ismapped():
            self._adv.pack_forget()
            self._adv_btn.configure(text="Advanced  ▸")
        else:
            self._adv.pack(fill="x", padx=8, pady=(4, 10))
            self._adv_btn.configure(text="Advanced  ▾")

    def _build_updates(self, parent) -> None:
        """Version line, auto-update toggle and the manual check button."""
        from core import updater
        from version import APP_VERSION

        self._header(parent, "Updates")
        ctk.CTkLabel(parent, text=f"Version {APP_VERSION}"
                     + ("" if updater.is_frozen() else "  (running from source)"),
                     font=FONT_UI, text_color=C["dim"]).pack(anchor="w", padx=6)
        self._auto_update = ctk.CTkSwitch(
            parent, text="Check for updates when the app opens",
            command=lambda: updater.set_enabled(bool(self._auto_update.get())))
        self._auto_update.pack(anchor="w", padx=6, pady=4)
        self._buttons(parent, [
            ("Check for updates now", lambda: self._app.check_updates_now(
                lambda t: self._status.configure(text=t, text_color=C["dim"]))),
            ("About / what's new", self._app.open_about)])

    def _build_actions(self) -> None:
        """Fixed footer: Save, the combined setup guide, and a status/diagnostic box."""
        bar = ctk.CTkFrame(self._footer, fg_color=C["panel"])
        bar.pack(fill="x", padx=6, pady=(8, 4))
        accent_button(ctk, bar, "Save", self._save, colour=C["teal_btn"],
                      width=120).pack(side="left")
        accent_button(ctk, bar, "Setup guide", self._open_full_guide,
                      colour=C["btn_off"]).pack(side="left", padx=8)
        accent_button(ctk, bar, "Run setup again", self._app.open_setup,
                      colour=C["btn_off"]).pack(side="left")

        # A textbox rather than a label: diagnostics can be several lines, and
        # this wraps, scrolls and can be selected/copied.
        self._status_box = ctk.CTkTextbox(self._footer, height=56, wrap="word",
                                          font=FONT_UI, fg_color=C["row"],
                                          text_color=C["dim"])
        self._status_box.pack(fill="x", padx=6, pady=(0, 8))
        self._status_box.configure(state="disabled")
        self._status = _StatusProxy(self._status_box)

    # -- widget helpers ----------------------------------------
    def _header(self, parent, text: str, guide_key: str | None = None) -> None:
        """Section heading, optionally with a 'Setup guide' button on the right."""
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=6, pady=(10, 2))
        ctk.CTkLabel(row, text=text, font=FONT_HEAD, text_color=C["teal"]
                     ).pack(side="left")
        if guide_key and guide_key in SETUP_GUIDES:
            ctk.CTkButton(
                row, text="Setup guide", width=100, height=24,
                fg_color=C["btn_off"], hover_color=C["select"], font=FONT_UI,
                command=lambda k=guide_key: GuideWindow(
                    self._app, [(k, SETUP_GUIDES[k])]),
            ).pack(side="right")

    def _help_button(self, parent, key: str, label: str) -> None:
        """The little '?' that pops a HelpPopup for one field."""
        text = FIELD_HELP.get(key)
        if not text:
            return
        ctk.CTkButton(
            parent, text="?", width=24, height=24, corner_radius=12,
            fg_color=C["btn_off"], hover_color=C["blue"], font=FONT_HEAD,
            command=lambda: HelpPopup(self._app, label, text),
        ).pack(side="left", padx=(6, 0))

    def _dropdown(self, parent, label: str, values: list[str], on_change) -> ctk.CTkOptionMenu:
        """Labelled option menu that re-renders the form when changed."""
        wrap = ctk.CTkFrame(parent, fg_color="transparent")
        wrap.pack(fill="x", padx=6, pady=3)
        ctk.CTkLabel(wrap, text=label, font=FONT_UI, text_color=C["text"],
                     width=250, anchor="w").pack(side="left")
        menu = ctk.CTkOptionMenu(wrap, values=values, command=lambda *_: on_change())
        menu.pack(side="left")
        return menu

    def _row(self, parent, key: str, label: str, secret: bool) -> None:
        """One labelled credential entry, pre-filled from settings."""
        wrap = ctk.CTkFrame(parent, fg_color="transparent")
        wrap.pack(fill="x", padx=6, pady=3)
        ctk.CTkLabel(wrap, text=label, font=FONT_UI, text_color=C["text"],
                     width=250, anchor="w").pack(side="left")
        entry = ctk.CTkEntry(wrap, show="•" if secret else "", width=400)
        entry.insert(0, self._settings.get(key, ""))
        entry.pack(side="left", fill="x", expand=True)
        self._help_button(wrap, key, label)
        self._fields[key] = entry

    def _note(self, parent, text: str) -> None:
        """Dim explanatory paragraph under a field or section."""
        ctk.CTkLabel(parent, text=text, font=FONT_UI, text_color=C["dim"],
                     anchor="w", justify="left", wraplength=640).pack(anchor="w", padx=6, pady=(0, 4))

    # -- dynamic render --------------------------------------
    def _provider_key(self, menu: ctk.CTkOptionMenu, table) -> str:
        """Resolve the selected label back to a provider key."""
        label = menu.get()
        for k, cls in table.items():
            if cls.label == label:
                return k
        return next(iter(table))

    def _ai_key(self) -> str:
        """Resolve the AI Provider dropdown label back to its provider key."""
        label = self._ai_provider.get()
        for k, m in AI_PROVIDERS.items():
            if m["label"] == label:
                return k
        return "gemini"

    def _build_imap_preset(self) -> None:
        """Provider preset that fills the host/port fields for the user."""
        from integrations.email_imap import IMAP_PRESETS

        row = ctk.CTkFrame(self._outlook_box, fg_color="transparent")
        row.pack(fill="x", padx=6, pady=(6, 2))
        ctk.CTkLabel(row, text="Provider preset", font=FONT_UI,
                     text_color=C["text"], width=250, anchor="w").pack(side="left")
        menu = ctk.CTkOptionMenu(row, values=list(IMAP_PRESETS))
        menu.set(self._settings.get("imap.preset", "Gmail"))
        menu.pack(side="left")

        def apply_preset() -> None:
            """Fill the host/port fields from the chosen preset."""
            name = menu.get()
            host, port = IMAP_PRESETS.get(name, ("", 993))
            self._settings.set("imap.preset", name)
            if host:
                for key, value in (("imap.host", host), ("imap.port", str(port))):
                    if key in self._fields:
                        self._fields[key].delete(0, "end")
                        self._fields[key].insert(0, value)
            self._status.configure(
                text=f"Preset '{name}' applied - now enter your email address "
                     f"and app password, then Save settings.", text_color=C["dim"])

        accent_button(ctk, row, "Apply preset", apply_preset,
                      colour=C["btn_off"]).pack(side="left", padx=8)

    def _build_graph_signin(self) -> None:
        """Sign-in row for the Graph backend: status + sign in / sign out."""
        from integrations.graph_auth import signed_in_account

        who = signed_in_account(self._settings)
        row = ctk.CTkFrame(self._outlook_box, fg_color="transparent")
        row.pack(fill="x", padx=6, pady=(8, 2))
        ctk.CTkLabel(row, text="Microsoft sign-in", font=FONT_UI,
                     text_color=C["text"], width=250, anchor="w").pack(side="left")
        accent_button(ctk, row, "Sign in to Microsoft", self._graph_sign_in,
                      colour=C["teal_btn"]).pack(side="left")
        if who:
            accent_button(ctk, row, "Sign out", self._graph_sign_out,
                          colour=C["btn_off"]).pack(side="left", padx=8)
        self._note(self._outlook_box,
                   f"Signed in as {who}." if who else
                   "Not signed in. Save the Client ID first, then click "
                   "'Sign in to Microsoft' - you'll get a short code to enter at "
                   "microsoft.com/devicelogin. Needed because Microsoft turned off "
                   "app-password/IMAP access for personal accounts in Sept 2024.")

    def _graph_sign_in(self) -> None:
        """Open the dedicated sign-in window (its own status, copyable)."""
        from gui.graph_signin_dialog import GraphSignInDialog

        self._save()
        GraphSignInDialog(self._app, self._settings, on_done=self._render)

    def _graph_sign_out(self) -> None:
        """Forget the cached Microsoft tokens and re-render."""
        from integrations.graph_auth import sign_out

        sign_out(self._settings)
        self._render()
        self._status.configure(text="Signed out of Microsoft.", text_color=C["dim"])

    def _backend_key(self) -> str:
        """The stored backend key (com/graph/imap) for the dropdown's label."""
        label = self._outlook_backend.get()
        for key, text in BACKEND_LABELS.items():
            if text == label:
                return key
        return label if label in BACKEND_LABELS else "com"

    def _on_backend_change(self) -> None:
        """Remember the mail-reading choice, then re-render."""
        self._ob_value = self._backend_key()
        self._render()

    def _render(self, *_a) -> None:
        """Rebuild every dynamic credential section from the current dropdowns."""
        # Every credential entry is recreated from settings on each render.
        self._fields = {}
        for box in (self._svc_box, self._acct_box, self._outlook_box, self._ai_box,
                    self._ai_model_box):
            for w in box.winfo_children():
                w.destroy()

        # --- service system and accounting system ---
        for box, menu, table, test in (
                (self._svc_box, self._service, SERVICE_PROVIDERS, self._test_service),
                (self._acct_box, self._accounting, ACCOUNTING_PROVIDERS, self._test_accounting)):
            key = self._provider_key(menu, table)
            cls = table[key]
            if key == "none" or (table is ACCOUNTING_PROVIDERS and not ACCOUNTING_ENABLED):
                continue
            for fkey, lbl, secret in cls.setting_fields:
                self._row(box, fkey, lbl, secret)
            if not cls.implemented and cls.setting_fields:
                self._note(box, "Preview integration - fields are saved, "
                                "but uploads are not wired yet.")
            buttons = [(f"Test {cls.label}", test)]
            if getattr(cls, "uses_oauth", False):
                buttons.append((f"Connect {cls.label}", self._oauth))
            if key in SETUP_GUIDES:
                buttons.append(("Setup guide", lambda k=key: GuideWindow(
                    self._app, [(k, SETUP_GUIDES[k])])))
            self._buttons(box, buttons)

        # --- mailbox ---
        backend = getattr(self, "_ob_value", None) or self._settings.get("outlook.backend", "com")
        if self._app.db.list_mail_accounts():
            # Mailboxes are listed above; the single-mailbox fields no longer apply.
            self._ob_value = backend
            self._outlook_backend = _Fixed(BACKEND_LABELS.get(backend, backend))
        else:
            self._header(self._outlook_box, "Your mailbox",
                         guide_key={"graph": "outlook_graph",
                                    "imap": "outlook_imap"}.get(backend, "outlook_com"))
            self._outlook_backend = self._dropdown(
                self._outlook_box, "Read email using", list(BACKEND_LABELS.values()),
                self._on_backend_change)
            self._outlook_backend.set(BACKEND_LABELS.get(backend, BACKEND_LABELS["com"]))
            self._ob_value = backend
            for key, lbl, secret in OUTLOOK_COMMON:
                self._row(self._outlook_box, key, lbl, secret)
            if backend == "com":
                self._note(self._outlook_box,
                           "Reads the classic Outlook desktop app you're already signed "
                           "into, so no password is needed. It doesn't work with the new "
                           "Outlook for Windows: choose Microsoft 365 / Outlook.com for that.")
            for key, lbl, secret in OUTLOOK_BY_BACKEND.get(backend, []):
                self._row(self._outlook_box, key, lbl, secret)
            if backend == "graph":
                self._build_graph_signin()
            elif backend == "imap":
                self._build_imap_preset()
                self._note(self._outlook_box,
                           "Works with Gmail, Fastmail, Yahoo, iCloud and most business "
                           "mail hosts using an app password (not your normal password). "
                           "Not for outlook.com: use Microsoft 365 / Outlook.com instead.")

        # --- AI ---
        akey = self._ai_key()
        meta = AI_PROVIDERS[akey]
        if akey in SETUP_GUIDES:
            self._header(self._ai_box, meta["label"], guide_key=akey)
        self._header(self._ai_model_box, "AI model")
        self._row(self._ai_model_box, "ai.model",
                  f"Model name (blank = {meta['default_model'] or 'server default'})", False)
        if meta["needs_base_url"]:
            self._row(self._ai_box, "ai.compat_base_url", "API base URL (ends in /v1)", False)
        klabel = "API Key" if meta["needs_key"] else "API Key (optional for local servers)"
        self._row(self._ai_box, meta["key_setting"], klabel, True)
        self._gemini_plan = None
        if akey == "gemini":
            # Free keys use a model with a bigger free allowance; the note says what free costs.
            plans = {"Free key (Google may use invoice data)": "free",
                     "Paid key (Google doesn't use your data)": "paid"}
            self._gemini_plan = self._dropdown(self._ai_box, "Key type", list(plans), lambda: None)
            self._gemini_plan_values = plans
            current = self._settings.get("ai.gemini_plan", "") or "paid"
            self._gemini_plan.set(next(k for k, v in plans.items() if v == current))

    # -- load / save ----------------------------------------
    def load(self) -> None:
        """Populate the static selectors + watcher options from settings."""
        svc = self._settings.get("service.provider", "servicem8")
        self._service.set(SERVICE_PROVIDERS.get(svc, SERVICE_PROVIDERS["servicem8"]).label)
        acct = self._settings.get("accounting.provider", "none")
        self._accounting.set(ACCOUNTING_PROVIDERS.get(acct, ACCOUNTING_PROVIDERS["none"]).label)
        ai_prov = self._settings.get("ai.provider", "gemini")
        self._ai_provider.set(AI_PROVIDERS.get(ai_prov, AI_PROVIDERS["gemini"])["label"])
        self._poll.delete(0, "end")
        self._poll.insert(0, self._settings.get("watcher.poll_minutes", "5"))
        self._min_conf.delete(0, "end")
        self._min_conf.insert(0, self._settings.get("customers.min_confidence", "0.4"))
        self._cache_days.delete(0, "end")
        self._cache_days.insert(0, self._settings.get("watcher.cache_days", "30"))
        (self._unread_only.select if self._settings.get_bool("watcher.unread_only") else self._unread_only.deselect)()
        (self._autostart.select if self._settings.get_bool("watcher.autostart") else self._autostart.deselect)()
        (self._run_startup.select if win_startup.is_enabled() else self._run_startup.deselect)()
        from core import updater
        (self._auto_update.select if updater.auto_check_pref() else self._auto_update.deselect)()
        self._render()
        self._warn_unreadable_secrets()

    def _warn_unreadable_secrets(self) -> None:
        """Tell the user plainly when stored credentials can no longer be read.

        The local encryption key living in Windows Credential Manager can be
        lost (new PC, cleared credentials, different user). The ciphertext in
        the database is then unrecoverable, and every affected field silently
        reads back as empty - which looks like a working config but is not.
        """
        try:
            broken = self._settings.unreadable_secrets()
        except Exception:
            return
        # Leftovers from systems that aren't selected don't matter; say nothing about them.
        in_use = sorted(k for k in broken if k in self._fields)
        if not in_use:
            return
        self._status.configure(
            text=("Some saved keys or passwords couldn't be read on this PC, so they "
                  "need entering again: " + ", ".join(in_use) + ". Re-enter them above, "
                  "then Save."),
            text_color=C["amber"])

    def _save(self) -> None:
        """Write every visible field back to the settings store."""
        for key, entry in self._fields.items():
            self._settings.set(key, entry.get())
        self._settings.set("service.provider",
                           self._provider_key(self._service, SERVICE_PROVIDERS))
        if ACCOUNTING_ENABLED:
            self._settings.set("accounting.provider",
                               self._provider_key(self._accounting, ACCOUNTING_PROVIDERS))
        self._settings.set("ai.provider", self._ai_key())
        if getattr(self, "_gemini_plan", None) is not None:
            self._settings.set("ai.gemini_plan",
                               self._gemini_plan_values.get(self._gemini_plan.get(), "paid"))
        self._settings.set("outlook.backend", self._backend_key())
        self._settings.set("watcher.poll_minutes", self._poll.get() or "5")
        self._settings.set("customers.min_confidence", self._min_conf.get() or "0.4")
        self._settings.set("watcher.cache_days", self._cache_days.get() or "30")
        self._settings.set("watcher.unread_only", "1" if self._unread_only.get() else "0")
        self._settings.set("watcher.autostart", "1" if self._autostart.get() else "0")
        try:
            win_startup.set_enabled(bool(self._run_startup.get()))
        except Exception as exc:
            self._status.configure(text=f"Startup toggle failed: {exc}", text_color=C["red"])
            return
        if getattr(self, "accounts", None):
            self.accounts.save_all()
        self._app.refresh_after_settings()
        self._status.configure(text="Saved.", text_color=C["teal"])

    # -- tests / oauth --------------------------------------
    def _run_test(self, busy: str, work) -> None:
        """Run one connection test off the Tk thread and report the result.

        ``work`` is called on a worker and returns ``(text, colour)``. Doing
        this inline used to freeze the whole window for the duration of the
        call - worst with the mailbox test, which talks to a remote server.
        """
        self._status.configure(text=busy, text_color=C["dim"])

        def worker() -> None:
            """Worker thread: run the check, then marshal back to Tk."""
            try:
                text, colour = work()
            except Exception as exc:
                text, colour = f"{exc}", C["red"]
            self._app.after(0, lambda: self._status.configure(text=text,
                                                              text_color=colour))

        threading.Thread(target=worker, daemon=True,
                         name="InvoiceWarden-SettingsTest").start()

    def _test_service(self) -> None:
        """Check the selected Service system's credentials against its API."""
        self._save()
        key = self._settings.get("service.provider")

        def work():
            """Worker: call the provider's own connection check."""
            res = build_provider(key, self._settings).test_connection()
            return res.detail, C["teal"] if res.ok else C["red"]

        self._run_test("Testing the service system...", work)

    def _test_accounting(self) -> None:
        """Check the selected Accounting system's credentials against its API."""
        self._save()
        key = self._settings.get("accounting.provider")

        def work():
            """Worker: call the provider's own connection check."""
            res = build_provider(key, self._settings).test_connection()
            return res.detail, C["teal"] if res.ok else C["red"]

        self._run_test("Testing the accounting system...", work)

    def _test_outlook(self) -> None:
        """Probe the mailbox and report what a real scan would have found.

        Runs headers-only: it identifies matching messages without downloading
        or writing a single attachment, so the test is cheap and leaves nothing
        behind in the cache.
        """
        self._save()
        unread_only = self._unread_only.get() == 1

        def work():
            """Worker: identify matching mail without downloading anything."""
            backend = build_backend(self._settings)
            msgs = backend.fetch(since=None, unread_only=unread_only,
                                 allowed_ext=set(), headers_only=True)
            detail = getattr(backend, "last_scan", "") or f"{len(msgs)} message(s) found."
            return f"Mailbox OK - {detail}", C["teal"] if msgs else C["amber"]

        self._run_test("Testing the mailbox connection...", work)

    def _test_ai(self) -> None:
        """Round-trip a synthetic invoice through the configured AI provider.

        Proves the whole extraction path - key, endpoint, model name and
        JSON-mode support - rather than merely that the host is reachable.
        """
        self._save()

        def work():
            """Worker: send the synthetic invoice and grade the reply."""
            ok, detail = test_ai_provider(self._settings)
            return detail, C["teal"] if ok else C["red"]

        self._run_test("Sending a test invoice to the AI provider...", work)

    def _open_full_guide(self) -> None:
        """Setup guide covering every currently-selected section."""
        keys = [
            self._provider_key(self._service, SERVICE_PROVIDERS),
            self._provider_key(self._accounting, ACCOUNTING_PROVIDERS),
            {"graph": "outlook_graph", "imap": "outlook_imap"}.get(
                getattr(self, "_ob_value", "com"), "outlook_com"),
            self._ai_key(),
        ]
        sections = [(k, SETUP_GUIDES[k]) for k in keys if k in SETUP_GUIDES]
        GuideWindow(self._app, sections)

    def _oauth(self) -> None:
        """Launch the OAuth consent dialog for whichever provider uses it."""
        self._save()
        from gui.oauth_dialog import OAuthDialog
        for key in (self._settings.get("service.provider"),
                    self._settings.get("accounting.provider")):
            prov = build_provider(key, self._settings)
            if prov.uses_oauth:
                OAuthDialog(self._app, key, self._settings, self._status)
                return
        self._status.configure(text="Neither selected provider uses OAuth.", text_color=C["dim"])
