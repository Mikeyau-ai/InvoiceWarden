"""Settings tab.

Layout:
  * Deployment - three dropdowns: Service system, Accounting system, AI Provider.
  * Credential sections are rendered DYNAMICALLY - only the fields for the
    currently-selected service system, accounting system, Outlook backend and
    AI provider are shown. Changing a dropdown re-renders in place.

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
    ACCOUNTING_PROVIDERS,
    SERVICE_PROVIDERS,
    build_provider,
)

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


class SettingsTab:
    """Builds and manages the Settings tab widgets."""

    def __init__(self, parent, app) -> None:
        """Build the scrolling form plus its pinned action footer."""
        self._app = app
        self._settings = app.settings
        self._fields: dict[str, ctk.CTkEntry] = {}

        # Root splits into a fixed footer (always-visible action bar + status)
        # and the scrolling body above it, so the buttons can never be pushed
        # off-screen by a long form or a multi-line status message.
        self._root = ctk.CTkFrame(parent, fg_color=C["bg"])
        self._root.pack(fill="both", expand=True)
        self._footer = ctk.CTkFrame(self._root, fg_color=C["panel"], corner_radius=0)
        self._footer.pack(side="bottom", fill="x")
        self.frame = ctk.CTkScrollableFrame(self._root, fg_color=C["bg"])
        self.frame.pack(side="top", fill="both", expand=True)

        self._build_deployment()
        # Dynamic containers - repopulated by _render().
        self._svc_box = ctk.CTkFrame(self.frame, fg_color=C["bg"])
        self._svc_box.pack(fill="x")
        self._acct_box = ctk.CTkFrame(self.frame, fg_color=C["bg"])
        self._acct_box.pack(fill="x")
        self._accounts_box = ctk.CTkFrame(self.frame, fg_color=C["bg"])
        self._accounts_box.pack(fill="x")
        self._outlook_box = ctk.CTkFrame(self.frame, fg_color=C["bg"])
        self._outlook_box.pack(fill="x")
        self._ai_box = ctk.CTkFrame(self.frame, fg_color=C["bg"])
        self._ai_box.pack(fill="x")

        self._build_watcher()
        self._build_updates()
        self._build_actions()

        from gui.accounts_section import AccountsSection

        self.accounts = AccountsSection(self._accounts_box, app, self._status)
        self.load()

    # -- static: deployment selectors ---------------------------
    def _build_deployment(self) -> None:
        """The three provider dropdowns that drive every dynamic section."""
        self._header(self.frame, "Deployment")
        self._service = self._dropdown(
            self.frame, "Service system",
            [c.label for c in SERVICE_PROVIDERS.values()], self._render)
        self._accounting = self._dropdown(
            self.frame, "Accounting system",
            [c.label for c in ACCOUNTING_PROVIDERS.values()], self._render)
        self._ai_provider = self._dropdown(
            self.frame, "AI Provider",
            [m["label"] for m in AI_PROVIDERS.values()], self._render)

    def _build_watcher(self) -> None:
        """Poll interval, cache retention and the watcher toggles."""
        self._header(self.frame, "Watcher")
        wrap = ctk.CTkFrame(self.frame, fg_color=C["bg"])
        wrap.pack(fill="x", padx=6, pady=3)
        ctk.CTkLabel(wrap, text="Poll interval (minutes)", font=FONT_UI,
                     text_color=C["text"], width=250, anchor="w").pack(side="left")
        self._poll = ctk.CTkEntry(wrap, width=80)
        self._poll.pack(side="left")

        cache_row = ctk.CTkFrame(self.frame, fg_color=C["bg"])
        cache_row.pack(fill="x", padx=6, pady=3)
        ctk.CTkLabel(cache_row, text="Keep cached attachments (days)", font=FONT_UI,
                     text_color=C["text"], width=250, anchor="w").pack(side="left")
        self._cache_days = ctk.CTkEntry(cache_row, width=80)
        self._cache_days.pack(side="left")
        self._note(self.frame,
                   "Downloaded attachments are kept this long so a failed upload "
                   "can still be retried, then deleted. 0 disables the cleanup.")

        conf_row = ctk.CTkFrame(self.frame, fg_color=C["bg"])
        conf_row.pack(fill="x", padx=6, pady=3)
        ctk.CTkLabel(conf_row, text="Min confidence to add a supplier", font=FONT_UI,
                     text_color=C["text"], width=250, anchor="w").pack(side="left")
        self._min_conf = ctk.CTkEntry(conf_row, width=80)
        self._min_conf.pack(side="left")
        self._note(self.frame,
                   "0 to 1. Below this, a supplier the app has never seen is NOT "
                   "created automatically - the invoice is held for you instead. "
                   "Invoices for suppliers already on file are unaffected.")

        self._unread_only = ctk.CTkSwitch(
            self.frame,
            text="Only process UNREAD emails  (off = every invoice since the last check)")
        self._unread_only.pack(anchor="w", padx=6, pady=4)
        self._autostart = ctk.CTkSwitch(self.frame, text="Start watcher automatically on app launch")
        self._autostart.pack(anchor="w", padx=6, pady=4)
        self._run_startup = ctk.CTkSwitch(self.frame, text="Run InvoiceWarden on Windows startup")
        self._run_startup.pack(anchor="w", padx=6, pady=4)

    def _build_updates(self) -> None:
        """Version line, auto-update toggle and the manual check button."""
        from core import updater
        from version import APP_VERSION

        self._header(self.frame, "Updates")
        ctk.CTkLabel(self.frame, text=f"Current version: {APP_VERSION}"
                     + ("" if updater.is_frozen() else "  (running from source)"),
                     font=FONT_UI, text_color=C["dim"]).pack(anchor="w", padx=6)
        self._auto_update = ctk.CTkSwitch(
            self.frame, text="Automatically check for updates on launch",
            command=lambda: updater.set_enabled(bool(self._auto_update.get())))
        self._auto_update.pack(anchor="w", padx=6, pady=4)
        row = ctk.CTkFrame(self.frame, fg_color=C["bg"])
        row.pack(fill="x", padx=6, pady=2)
        accent_button(ctk, row, "Check for updates now",
                      lambda: self._app.check_updates_now(
                          lambda t: self._status.configure(text=t, text_color=C["dim"])),
                      colour=C["blue"]).pack(side="left")
        accent_button(ctk, row, "About / Changelog", self._app.open_about,
                      colour=C["btn_off"]).pack(side="left", padx=8)

    def _build_actions(self) -> None:
        """Fixed footer: action buttons plus a scrollable status/diagnostic box."""
        bar = ctk.CTkFrame(self._footer, fg_color=C["panel"])
        bar.pack(fill="x", padx=6, pady=(8, 4))
        accent_button(ctk, bar, "Save settings", self._save, colour=C["green"]).pack(side="left")
        accent_button(ctk, bar, "Test service", self._test_service, colour=C["blue"]).pack(side="left", padx=8)
        accent_button(ctk, bar, "Test accounting", self._test_accounting, colour=C["blue"]).pack(side="left")
        accent_button(ctk, bar, "Test mailbox", self._test_outlook, colour=C["blue"]).pack(side="left", padx=8)
        accent_button(ctk, bar, "Test AI", self._test_ai, colour=C["blue"]).pack(side="left")
        accent_button(ctk, bar, "Authorise OAuth", self._oauth, colour=C["purple"]).pack(side="left", padx=(8, 0))
        accent_button(ctk, bar, "Setup guide (all)", self._open_full_guide, colour=C["btn_off"]).pack(side="left", padx=8)

        # A textbox rather than a label: diagnostics can be several lines, and
        # this wraps, scrolls and can be selected/copied.
        self._status_box = ctk.CTkTextbox(self._footer, height=72, wrap="word",
                                          font=FONT_UI, fg_color=C["row"],
                                          text_color=C["dim"])
        self._status_box.pack(fill="x", padx=6, pady=(0, 8))
        self._status_box.configure(state="disabled")
        self._status = _StatusProxy(self._status_box)

    # -- widget helpers ----------------------------------------
    def _header(self, parent, text: str, guide_key: str | None = None) -> None:
        """Section heading, optionally with a 'Setup guide' button on the right."""
        row = ctk.CTkFrame(parent, fg_color=C["bg"])
        row.pack(fill="x", padx=6, pady=(16, 4))
        ctk.CTkLabel(row, text=text, font=FONT_HEAD, text_color=C["blue"]
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
        wrap = ctk.CTkFrame(parent, fg_color=C["bg"])
        wrap.pack(fill="x", padx=6, pady=3)
        ctk.CTkLabel(wrap, text=label, font=FONT_UI, text_color=C["text"],
                     width=250, anchor="w").pack(side="left")
        menu = ctk.CTkOptionMenu(wrap, values=values, command=lambda *_: on_change())
        menu.pack(side="left")
        return menu

    def _row(self, parent, key: str, label: str, secret: bool) -> None:
        """One labelled credential entry, pre-filled from settings."""
        wrap = ctk.CTkFrame(parent, fg_color=C["bg"])
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

        row = ctk.CTkFrame(self._outlook_box, fg_color=C["bg"])
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
        row = ctk.CTkFrame(self._outlook_box, fg_color=C["bg"])
        row.pack(fill="x", padx=6, pady=(8, 2))
        ctk.CTkLabel(row, text="Microsoft sign-in", font=FONT_UI,
                     text_color=C["text"], width=250, anchor="w").pack(side="left")
        accent_button(ctk, row, "Sign in to Microsoft", self._graph_sign_in,
                      colour=C["purple"]).pack(side="left")
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

    def _on_backend_change(self) -> None:
        """Remember the Outlook backend choice, then re-render."""
        self._ob_value = self._outlook_backend.get()
        self._render()

    def _render(self, *_a) -> None:
        """Rebuild every dynamic credential section from the current dropdowns."""
        # Every credential entry is recreated from settings on each render.
        self._fields = {}
        for box in (self._svc_box, self._acct_box, self._outlook_box, self._ai_box):
            for w in box.winfo_children():
                w.destroy()

        # --- service system ---
        svc_key = self._provider_key(self._service, SERVICE_PROVIDERS)
        svc_cls = SERVICE_PROVIDERS[svc_key]
        self._header(self._svc_box, f"Service system - {svc_cls.label}", guide_key=svc_key)
        if not svc_cls.setting_fields:
            self._note(self._svc_box, "No credentials required for this option.")
        for key, lbl, secret in svc_cls.setting_fields:
            self._row(self._svc_box, key, lbl, secret)
        if not svc_cls.implemented and svc_cls.setting_fields:
            self._note(self._svc_box, "Preview integration - fields are saved, "
                                      "but uploads are not wired yet.")

        # --- accounting system ---
        acct_key = self._provider_key(self._accounting, ACCOUNTING_PROVIDERS)
        acct_cls = ACCOUNTING_PROVIDERS[acct_key]
        self._header(self._acct_box, f"Accounting system - {acct_cls.label}", guide_key=acct_key)
        if not acct_cls.setting_fields:
            self._note(self._acct_box, "No credentials required for this option.")
        for key, lbl, secret in acct_cls.setting_fields:
            self._row(self._acct_box, key, lbl, secret)
        if not acct_cls.implemented and acct_cls.setting_fields:
            self._note(self._acct_box, "Preview integration - fields are saved, "
                                       "but uploads are not wired yet.")

        # --- Outlook ---
        backend = getattr(self, "_ob_value", None) or self._settings.get("outlook.backend", "com")
        self._header(self._outlook_box, "Email",
                     guide_key={"graph": "outlook_graph",
                                "imap": "outlook_imap"}.get(backend, "outlook_com"))
        self._outlook_backend = self._dropdown(
            self._outlook_box, "Backend", ["com", "graph", "imap"],
            self._on_backend_change)
        self._outlook_backend.set(backend)
        self._ob_value = backend
        for key, lbl, secret in OUTLOOK_COMMON:
            self._row(self._outlook_box, key, lbl, secret)
        backend = self._outlook_backend.get()
        if backend == "com":
            self._note(self._outlook_box,
                       "COM reads the CLASSIC Outlook desktop client you are already "
                       "signed into - no credentials needed. It does NOT work with "
                       "the new Outlook for Windows (no COM support): use 'graph' "
                       "for that, and for outlook.com accounts.")
        for key, lbl, secret in OUTLOOK_BY_BACKEND.get(backend, []):
            self._row(self._outlook_box, key, lbl, secret)
        if backend == "graph":
            self._build_graph_signin()
        elif backend == "imap":
            self._build_imap_preset()
            self._note(self._outlook_box,
                       "IMAP works with Gmail, Fastmail, Yahoo, iCloud and most "
                       "business mail hosts using an APP PASSWORD (not your normal "
                       "password). It does NOT work with outlook.com - Microsoft "
                       "disabled app passwords for personal accounts in Sept 2024; "
                       "use 'graph' for those, or auto-forward that mail to a "
                       "provider listed above. Click 'Setup guide' for the steps.")

        # --- AI ---
        akey = self._ai_key()
        meta = AI_PROVIDERS[akey]
        self._header(self._ai_box, f"AI Provider - {meta['label']}", guide_key=akey)
        self._row(self._ai_box, "ai.model",
                  f"Model name (blank = {meta['default_model'] or 'server default'})", False)
        if meta["needs_base_url"]:
            self._row(self._ai_box, "ai.compat_base_url", "API base URL (ends in /v1)", False)
        klabel = "API Key" if meta["needs_key"] else "API Key (optional for local servers)"
        self._row(self._ai_box, meta["key_setting"], klabel, True)

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
        if not broken:
            return
        pretty = ", ".join(sorted(broken))
        self._status.configure(
            text=("STORED CREDENTIALS COULD NOT BE READ. The local encryption "
                  "key changed, so these saved values are unrecoverable and are "
                  "being treated as EMPTY: " + pretty + ". Re-enter each one "
                  "above and click Save settings. Until you do, anything using "
                  "them will fail with confusing errors."),
            text_color=C["red"])

    def _save(self) -> None:
        """Write every visible field back to the settings store."""
        for key, entry in self._fields.items():
            self._settings.set(key, entry.get())
        self._settings.set("service.provider",
                           self._provider_key(self._service, SERVICE_PROVIDERS))
        self._settings.set("accounting.provider",
                           self._provider_key(self._accounting, ACCOUNTING_PROVIDERS))
        self._settings.set("ai.provider", self._ai_key())
        self._settings.set("outlook.backend", self._outlook_backend.get())
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
        self._status.configure(text="Settings saved.", text_color=C["green"])

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
            return res.detail, C["green"] if res.ok else C["red"]

        self._run_test("Testing the service system...", work)

    def _test_accounting(self) -> None:
        """Check the selected Accounting system's credentials against its API."""
        self._save()
        key = self._settings.get("accounting.provider")

        def work():
            """Worker: call the provider's own connection check."""
            res = build_provider(key, self._settings).test_connection()
            return res.detail, C["green"] if res.ok else C["red"]

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
            return f"Mailbox OK - {detail}", C["green"] if msgs else C["yellow"]

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
            return detail, C["green"] if ok else C["red"]

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
