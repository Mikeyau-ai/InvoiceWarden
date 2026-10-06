"""First-run setup wizard: email, ServiceM8, AI, then switch on.

Shown automatically on a fresh install (nothing set up yet) and from Settings > Run setup
again. Every value is saved with the same keys Settings uses, and every Test button runs the
same check as its Settings counterpart, so the two can't drift apart. Each step can be left
and finished later in Settings.
"""
from __future__ import annotations

import threading
import webbrowser

import customtkinter as ctk

from config import APP_NAME
from core import startup as win_startup
from core.parser_ai import AI_PROVIDERS, test_ai_provider
from gui.help_content import SETUP_GUIDES
from gui.help_dialog import GuideWindow
from gui.theme import C, FONT_UI, accent_button, apply_icon, dark_titlebar
from integrations.email_outlook import build_backend
from integrations.registry import build_provider

_FONT_TITLE = ("Segoe UI Semibold", 18)
_FONT_SMALL = ("Segoe UI", 12)

#: Where "Request access" sends people (the support form, pre-filled for this request).
STUDIO_AI_REQUEST_URL = ("https://sixthdaystudios.com/support?app=invoicewarden&topic=help"
                         "&subject=ai-access")

#: How the AI is provided: (choice, title, explanation). See _page_ai.
_AI_CHOICES = [
    ("free", "Free Google Gemini key (good to start)",
     "Free for normal use and takes about 5 minutes. On Google's free tier, Google may use "
     "what's sent (your invoices) to improve its products, and people at Google may read it."),
    ("paid", "Paid Google Gemini key",
     "The same key with billing switched on in Google: usually well under A$1 a month. "
     "Google doesn't use your data."),
    ("other", "Another AI service",
     "OpenAI (ChatGPT), Anthropic (Claude), or your own AI server."),
    ("studio", "Sixth Day Studios AI (request access)",
     "We provide the AI, so you don't need a key. Your invoices pass through our server on "
     "their way to the AI (we don't keep them). Not open yet: request access and we'll be "
     "in touch."),
]

#: How to read mail: (stored key, title, one-line explanation).
_MAIL_CHOICES = [
    ("graph", "Microsoft 365 or Outlook.com",
     "Work or personal Microsoft email. You sign in once; no passwords are stored."),
    ("com", "The Outlook app on this PC (classic Outlook)",
     "Reads the Outlook you're already signed into. Not the new Outlook for Windows."),
    ("imap", "Other email (Gmail, Fastmail, iCloud...)",
     "Uses an app password from your email provider."),
]


def needs_setup(settings) -> bool:
    """True on a fresh install: setup never finished and no job system key saved."""
    return not settings.get_bool("setup.done") and not settings.get("servicem8.api_key", "")


class SetupWizard(ctk.CTkToplevel):
    """Five short pages: welcome, email, ServiceM8, AI, done."""

    STEPS = ("welcome", "email", "service", "ai", "done")

    def __init__(self, app) -> None:
        """Build the window frame (title, page area, Back/Next) and show page one."""
        super().__init__(app)
        self._app = app
        self._s = app.settings
        self._step = 0
        self.title(f"Set up {APP_NAME}")
        self.geometry("660x700")
        self.minsize(600, 600)
        self.configure(fg_color=C["bg"])
        self.transient(app)
        dark_titlebar(self)
        apply_icon(self)

        self._progress = ctk.CTkLabel(self, text="", font=_FONT_SMALL, text_color=C["dim"], anchor="w")
        self._progress.pack(fill="x", padx=28, pady=(18, 0))
        self._page = ctk.CTkFrame(self, fg_color=C["bg"])
        self._page.pack(fill="both", expand=True, padx=24, pady=(4, 0))
        nav = ctk.CTkFrame(self, fg_color=C["panel"], corner_radius=0)
        nav.pack(fill="x", side="bottom")
        self._back = accent_button(ctk, nav, "Back", self._go_back, colour=C["btn_off"], width=100)
        self._back.pack(side="left", padx=16, pady=12)
        self._next = accent_button(ctk, nav, "Next", self._go_next, colour=C["teal_btn"], width=120)
        self._next.pack(side="right", padx=16, pady=12)
        self._later = ctk.CTkButton(nav, text="Finish later", width=10, fg_color="transparent",
                                    hover_color=C["row"], text_color=C["dim"], font=_FONT_SMALL,
                                    command=self.destroy)
        self._later.pack(side="right")
        self._show()
        self.after(200, self.lift)

    # -- page plumbing -------------------------------------------------
    def _clear(self) -> None:
        """Empty the page area."""
        for w in self._page.winfo_children():
            w.destroy()

    def _title(self, text: str, blurb: str = "") -> None:
        """Page heading plus an optional explanation line."""
        ctk.CTkLabel(self._page, text=text, font=_FONT_TITLE, text_color=C["text"],
                     anchor="w").pack(fill="x", pady=(4, 2))
        if blurb:
            ctk.CTkLabel(self._page, text=blurb, font=FONT_UI, text_color=C["dim"], anchor="w",
                         justify="left", wraplength=570).pack(fill="x", pady=(0, 10))

    def _field(self, parent, label: str, key: str, secret: bool = False,
               placeholder: str = "") -> ctk.CTkEntry:
        """A labelled entry pre-filled from settings; saved by _save_fields()."""
        ctk.CTkLabel(parent, text=label, font=FONT_UI, text_color=C["text"],
                     anchor="w").pack(fill="x", pady=(6, 2))
        entry = ctk.CTkEntry(parent, show="•" if secret else "", placeholder_text=placeholder)
        entry.insert(0, self._s.get(key, ""))
        entry.pack(fill="x")
        self._fields[key] = entry
        return entry

    def _status_line(self) -> ctk.CTkLabel:
        """The result line under a Test button."""
        self._result = ctk.CTkLabel(self._page, text="", font=FONT_UI, text_color=C["dim"],
                                    anchor="w", justify="left", wraplength=570)
        self._result.pack(fill="x", pady=(6, 0))
        return self._result

    def _save_fields(self) -> None:
        """Write every field on the current page to settings."""
        for key, entry in self._fields.items():
            self._s.set(key, entry.get().strip())

    def _run_test(self, busy: str, work) -> None:
        """Save, run a check off the UI thread, then show the result (teal = OK)."""
        self._save_fields()
        self._result.configure(text=busy, text_color=C["dim"])

        def worker() -> None:
            """Worker thread: run the check and hand the outcome back to Tk."""
            try:
                ok, text = work()
            except Exception as exc:
                ok, text = False, str(exc)

            def show() -> None:
                """Tk thread: paint the result if the page is still open."""
                if self.winfo_exists() and self._result.winfo_exists():
                    self._result.configure(text=("✓  " if ok else "") + text,
                                           text_color=C["teal"] if ok else C["red"])
            self._app.after(0, show)

        threading.Thread(target=worker, daemon=True).start()

    def _guide(self, key: str) -> None:
        """Open one setup guide, if it exists."""
        if key in SETUP_GUIDES:
            GuideWindow(self._app, [(key, SETUP_GUIDES[key])])

    def _show(self) -> None:
        """Render the current step and update the navigation buttons."""
        self._clear()
        self._fields: dict[str, ctk.CTkEntry] = {}
        name = self.STEPS[self._step]
        if 0 < self._step < len(self.STEPS) - 1:
            self._progress.configure(text=f"Step {self._step} of {len(self.STEPS) - 2}")
        else:
            self._progress.configure(text="")
        getattr(self, f"_page_{name}")()
        self._back.configure(state="normal" if self._step else "disabled")
        last = self._step == len(self.STEPS) - 1
        self._next.configure(text="Finish" if last else ("Get started" if self._step == 0 else "Next"))
        self._later.pack_forget() if last else self._later.pack(side="right")

    def _go_back(self) -> None:
        """Previous page (keeps what was typed)."""
        self._save_fields()
        self._step = max(0, self._step - 1)
        self._show()

    def _go_next(self) -> None:
        """Save this page, then the next one (or finish)."""
        self._save_fields()
        if self._step == len(self.STEPS) - 1:
            self._finish()
            return
        self._step += 1
        self._show()

    # -- pages -----------------------------------------------------------
    def _page_welcome(self) -> None:
        """What the app does and what setup involves."""
        self._title(f"Welcome to {APP_NAME}",
                    "It watches your inbox for supplier invoices, reads each one, and files it "
                    "against the right job in ServiceM8. You'll be set up in three short steps:")
        for line in ("1.  Your email: where suppliers send invoices",
                     "2.  ServiceM8: where they get filed",
                     "3.  The AI that reads each invoice"):
            ctk.CTkLabel(self._page, text=line, font=FONT_UI, text_color=C["text"],
                         anchor="w").pack(fill="x", padx=8, pady=3)
        ctk.CTkLabel(self._page, text="Allow about 10 minutes. Anything you skip can be finished "
                                      "later in Settings.",
                     font=FONT_UI, text_color=C["dim"], anchor="w", justify="left",
                     wraplength=570).pack(fill="x", pady=(14, 0))

    def _page_email(self) -> None:
        """Pick how to read mail, then the few details that choice needs."""
        self._title("Your email", "Which inbox do your suppliers send invoices to?")
        self._mail = ctk.StringVar(value=self._s.get("outlook.backend", "graph") or "graph")
        for key, title, blurb in _MAIL_CHOICES:
            box = ctk.CTkFrame(self._page, fg_color=C["panel"], corner_radius=6)
            box.pack(fill="x", pady=3)
            ctk.CTkRadioButton(box, text=title, value=key, variable=self._mail, font=FONT_UI,
                               fg_color=C["teal_btn"], command=self._email_details
                               ).pack(anchor="w", padx=12, pady=(8, 0))
            ctk.CTkLabel(box, text=blurb, font=_FONT_SMALL, text_color=C["dim"], anchor="w"
                         ).pack(fill="x", padx=40, pady=(0, 8))
        self._details = ctk.CTkFrame(self._page, fg_color=C["bg"])
        self._details.pack(fill="x", pady=(6, 0))
        self._email_details()

    def _email_details(self) -> None:
        """The fields for the chosen way of reading mail, plus Test."""
        self._save_fields()
        for w in self._details.winfo_children():
            w.destroy()
        self._fields = {}
        backend = self._mail.get()
        self._s.set("outlook.backend", backend)
        d = self._details
        if backend == "graph":
            from integrations.graph_auth import signed_in_account

            who = signed_in_account(self._s)
            row = ctk.CTkFrame(d, fg_color=C["bg"])
            row.pack(fill="x", pady=(6, 0))
            accent_button(ctk, row, "Sign in to Microsoft", self._graph_sign_in,
                          colour=C["teal_btn"]).pack(side="left")
            ctk.CTkLabel(row, text=f"Signed in as {who}" if who else "Not signed in yet",
                         font=FONT_UI, text_color=C["teal"] if who else C["dim"]
                         ).pack(side="left", padx=12)
        elif backend == "com":
            self._field(d, "Mailbox name, as Outlook shows it", "outlook.account",
                        placeholder="accounts@yourbusiness.com.au")
        else:
            from integrations.email_imap import IMAP_PRESETS

            row = ctk.CTkFrame(d, fg_color=C["bg"])
            row.pack(fill="x", pady=(6, 0))
            ctk.CTkLabel(row, text="Provider", font=FONT_UI, text_color=C["text"]).pack(side="left")
            preset = ctk.CTkOptionMenu(row, values=list(IMAP_PRESETS),
                                       command=lambda name: self._apply_preset(name))
            preset.set(self._s.get("imap.preset", "Gmail") or "Gmail")
            preset.pack(side="left", padx=10)
            self._apply_preset(preset.get(), quiet=True)
            self._field(d, "Email address", "imap.username", placeholder="you@gmail.com")
            self._field(d, "App password (not your normal password)", "imap.password", secret=True)
        buttons = ctk.CTkFrame(d, fg_color=C["bg"])
        buttons.pack(fill="x", pady=(10, 0))
        accent_button(ctk, buttons, "Test", self._test_email, colour=C["btn_off"],
                      width=90).pack(side="left")
        accent_button(ctk, buttons, "How do I do this?",
                      lambda: self._guide({"graph": "outlook_graph", "imap": "outlook_imap"}
                                          .get(backend, "outlook_com")),
                      colour=C["btn_off"]).pack(side="left", padx=8)
        self._result = ctk.CTkLabel(d, text="", font=FONT_UI, text_color=C["dim"], anchor="w",
                                    justify="left", wraplength=570)
        self._result.pack(fill="x", pady=(6, 0))

    def _apply_preset(self, name: str, quiet: bool = False) -> None:
        """Fill the IMAP server and port from a provider preset."""
        from integrations.email_imap import IMAP_PRESETS

        host, port = IMAP_PRESETS.get(name, ("", 993))
        self._s.set("imap.preset", name)
        if host:
            self._s.set("imap.host", host)
            self._s.set("imap.port", str(port))

    def _graph_sign_in(self) -> None:
        """Open the Microsoft sign-in window; refresh this page when it's done."""
        from gui.graph_signin_dialog import GraphSignInDialog

        GraphSignInDialog(self._app, self._s,
                          on_done=lambda *_: self.winfo_exists() and self._email_details())

    def _test_email(self) -> None:
        """Look for matching mail without downloading anything (same as Settings)."""
        unread = self._s.get_bool("watcher.unread_only")

        def work():
            """Worker: headers-only scan of the inbox."""
            backend = build_backend(self._s)
            msgs = backend.fetch(since=None, unread_only=unread, allowed_ext=set(),
                                 headers_only=True)
            detail = getattr(backend, "last_scan", "") or f"{len(msgs)} message(s) found."
            return True, f"Connected. {detail}"

        self._run_test("Checking your inbox...", work)

    def _page_service(self) -> None:
        """ServiceM8 API key, with the guide and a Test."""
        self._s.set("service.provider", "servicem8")
        self._title("ServiceM8", "Invoices are attached to the matching job in ServiceM8. "
                                 "It needs an API key from your ServiceM8 account.")
        self._field(self._page, "ServiceM8 API key", "servicem8.api_key", secret=True)
        row = ctk.CTkFrame(self._page, fg_color=C["bg"])
        row.pack(fill="x", pady=(10, 0))
        accent_button(ctk, row, "Test", self._test_service, colour=C["btn_off"],
                      width=90).pack(side="left")
        accent_button(ctk, row, "How do I get this key?", lambda: self._guide("servicem8"),
                      colour=C["btn_off"]).pack(side="left", padx=8)
        self._status_line()

    def _test_service(self) -> None:
        """Check the ServiceM8 key against ServiceM8."""
        def work():
            """Worker: the provider's own connection check."""
            res = build_provider("servicem8", self._s).test_connection()
            return res.ok, res.detail

        self._run_test("Checking with ServiceM8...", work)

    def _page_ai(self) -> None:
        """How the AI is provided (free key, paid key, another AI, or ours), then its details."""
        self._title("The AI that reads invoices",
                    "It finds the supplier, invoice number and job number on each invoice. "
                    "Choose how it's provided:")
        provider = self._s.get("ai.provider", "gemini") or "gemini"
        plan = self._s.get("ai.gemini_plan", "")
        start = "other" if provider != "gemini" else ("paid" if plan == "paid" else "free")
        self._ai_choice = ctk.StringVar(value=getattr(self, "_ai_last", start))
        for key, title, blurb in _AI_CHOICES:
            box = ctk.CTkFrame(self._page, fg_color=C["panel"], corner_radius=6)
            box.pack(fill="x", pady=2)
            ctk.CTkRadioButton(box, text=title, value=key, variable=self._ai_choice, font=FONT_UI,
                               fg_color=C["teal_btn"], command=self._ai_details
                               ).pack(anchor="w", padx=12, pady=(6, 0))
            ctk.CTkLabel(box, text=blurb, font=_FONT_SMALL, text_color=C["dim"], anchor="w",
                         justify="left", wraplength=510).pack(fill="x", padx=40, pady=(0, 6))
        self._details = ctk.CTkFrame(self._page, fg_color=C["bg"])
        self._details.pack(fill="x", pady=(4, 0))
        ctk.CTkLabel(self._page, text="No AI at all? It still works by looking for labels like "
                                      "\"Job No\", but misses job numbers that aren't clearly marked.",
                     font=_FONT_SMALL, text_color=C["dim"], anchor="w", justify="left",
                     wraplength=580).pack(fill="x", side="bottom", pady=(6, 0))
        self._ai_details()

    def _ai_details(self) -> None:
        """The fields for the chosen way of providing the AI."""
        self._save_fields()
        for w in self._details.winfo_children():
            w.destroy()
        self._fields = {}
        choice = self._ai_last = self._ai_choice.get()
        d = self._details
        if choice == "studio":
            ctk.CTkLabel(d, text="Request access and we'll email you when it's ready. Until "
                                 "then you can start with a free Gemini key and switch later.",
                         font=FONT_UI, text_color=C["text"], anchor="w", justify="left",
                         wraplength=580).pack(fill="x", pady=(4, 6))
            accent_button(ctk, d, "Request access", lambda: webbrowser.open(STUDIO_AI_REQUEST_URL),
                          colour=C["teal_btn"]).pack(anchor="w")
            self._result = ctk.CTkLabel(d, text="")
            return
        if choice in ("free", "paid"):
            self._s.set("ai.provider", "gemini")
            self._s.set("ai.gemini_plan", choice)
            provider = "gemini"
        else:
            provider = self._s.get("ai.provider", "openai")
            if provider == "gemini":
                provider = "openai"
            others = {m["label"]: k for k, m in AI_PROVIDERS.items() if k != "gemini"}
            row = ctk.CTkFrame(d, fg_color=C["bg"])
            row.pack(fill="x", pady=(4, 0))
            ctk.CTkLabel(row, text="AI service", font=FONT_UI, text_color=C["text"]).pack(side="left")
            menu = ctk.CTkOptionMenu(row, values=list(others),
                                     command=lambda label: (self._save_fields(),
                                                            self._s.set("ai.provider", others[label]),
                                                            self._ai_details()))
            menu.set(AI_PROVIDERS[provider]["label"])
            menu.pack(side="left", padx=10)
            self._s.set("ai.provider", provider)
        meta = AI_PROVIDERS[provider]
        if meta.get("needs_base_url"):
            self._field(d, "Server address (ends in /v1)", "ai.compat_base_url")
        self._field(d, "API key" if meta.get("needs_key", True) else
                    "API key (optional for a local server)", meta["key_setting"], secret=True)
        buttons = ctk.CTkFrame(d, fg_color=C["bg"])
        buttons.pack(fill="x", pady=(8, 0))
        accent_button(ctk, buttons, "Test", self._test_ai, colour=C["btn_off"],
                      width=90).pack(side="left")
        guide = {"free": "How do I get a free key?", "paid": "How do I set up a paid key?"}
        accent_button(ctk, buttons, guide.get(choice, "How do I get a key?"),
                      lambda: self._guide(provider), colour=C["btn_off"]).pack(side="left", padx=8)
        self._result = ctk.CTkLabel(d, text="", font=FONT_UI, text_color=C["dim"], anchor="w",
                                    justify="left", wraplength=570)
        self._result.pack(fill="x", pady=(4, 0))

    def _test_ai(self) -> None:
        """Send a made-up invoice to the AI and check it reads it back."""
        self._run_test("Sending a test invoice to the AI...",
                       lambda: test_ai_provider(self._s))

    def _page_done(self) -> None:
        """Last switches, then Finish."""
        self._title("All set", "Here's how you'd like it to run. You can change these any time "
                               "in Settings.")
        self._startup = ctk.CTkSwitch(self._page, text="Start with Windows", font=FONT_UI)
        self._startup.pack(anchor="w", pady=6)
        (self._startup.select if win_startup.is_enabled() else self._startup.deselect)()
        self._auto = ctk.CTkSwitch(self._page, text="Turn on automatically when the app opens",
                                   font=FONT_UI)
        self._auto.pack(anchor="w", pady=6)
        (self._auto.select if self._s.get_bool("watcher.autostart") else self._auto.deselect)()
        self._now = ctk.CTkSwitch(self._page, text="Turn on now and start checking email",
                                  font=FONT_UI)
        self._now.pack(anchor="w", pady=6)
        self._now.select()
        ctk.CTkLabel(self._page, text="New suppliers are added for you as their invoices arrive. "
                                      "Anything that needs you shows in amber on the main screen.",
                     font=FONT_UI, text_color=C["dim"], anchor="w", justify="left",
                     wraplength=570).pack(fill="x", pady=(14, 0))

    def _finish(self) -> None:
        """Save the switches, mark setup done, optionally switch on, close."""
        self._s.set("watcher.autostart", "1" if self._auto.get() else "0")
        try:
            win_startup.set_enabled(bool(self._startup.get()))
        except Exception:
            pass  # not fatal: the Settings switch reports the error if it happens again
        self._s.set("setup.done", "1")
        turn_on = bool(self._now.get())
        self._app.refresh_after_settings()
        self.destroy()
        if turn_on:
            self._app.set_watching(True)
