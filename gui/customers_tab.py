"""Suppliers page - the local list of suppliers and where each one's invoices go.

Left: the list (new, unreviewed suppliers first). Right: the supplier's details. Up front
are only the name, the other names it goes by, and a switch for each system that's
actually set up. The IDs, file types and notes sit under "More options".
"""
from __future__ import annotations

from tkinter import messagebox

import customtkinter as ctk

from config import SUPPORTED_FILE_TYPES
from gui.theme import C, FONT_HEAD, FONT_UI, accent_button
from integrations.registry import ACCOUNTING_ENABLED, label_for


class CustomersTab:
    """Builds and manages the Customer Management tab."""

    def __init__(self, parent, app) -> None:
        """Build the customer list column and the edit form beside it."""
        self._app = app
        self._db = app.db
        self._current_id: int | None = None
        self._type_vars: dict[str, ctk.CTkCheckBox] = {}

        root = ctk.CTkFrame(parent, fg_color=C["bg"])
        root.pack(fill="both", expand=True)

        # -- list column --
        # Wide enough that most supplier names fit on one line; the pathological
        # long ones wrap onto a second line rather than being clipped.
        left = ctk.CTkFrame(root, fg_color=C["panel"], width=300)
        left.pack(side="left", fill="y", padx=(0, 8), pady=0)
        left.pack_propagate(False)
        head = ctk.CTkFrame(left, fg_color=C["panel"])
        head.pack(fill="x", padx=10, pady=8)
        ctk.CTkLabel(head, text="Suppliers", font=FONT_HEAD,
                     text_color=C["text"]).pack(side="left")
        accent_button(ctk, head, "?", self._guide, colour=C["btn_off"],
                      width=28, height=24).pack(side="right")
        accent_button(ctk, left, "+ New supplier", self._new,
                      colour=C["teal_btn"]).pack(fill="x", padx=10, pady=(0, 6))
        # Suppliers are added automatically, so the list needs ordering and a
        # way to see just the ones nobody has looked at yet.
        from core.database import Database as _Db

        sort_row = ctk.CTkFrame(left, fg_color=C["panel"])
        sort_row.pack(fill="x", padx=10, pady=(0, 4))
        ctk.CTkLabel(sort_row, text="Sort", font=FONT_UI,
                     text_color=C["dim"]).pack(side="left", padx=(0, 6))
        self._sort = ctk.CTkOptionMenu(sort_row, width=170,
                                       values=list(_Db.CUSTOMER_SORTS),
                                       command=lambda _v: self.refresh())
        self._sort.set("New / unreviewed first")
        self._sort.pack(side="left")

        self._new_only = ctk.CTkSwitch(left, text="Show new only",
                                       command=self.refresh)
        self._new_only.pack(anchor="w", padx=10, pady=(0, 6))

        self._count = ctk.CTkLabel(left, text="", font=FONT_UI,
                                   text_color=C["dim"], anchor="w")
        self._count.pack(anchor="w", padx=10, pady=(0, 4))

        self._list = ctk.CTkScrollableFrame(left, fg_color=C["panel"])
        self._list.pack(fill="both", expand=True, padx=6, pady=6)

        # -- edit column --
        right = ctk.CTkScrollableFrame(root, fg_color=C["bg"])
        right.pack(side="left", fill="both", expand=True)
        self._form = right
        self._build_form()
        self.refresh()

    def _guide(self) -> None:
        """Explain how suppliers get added and what NEW means."""
        from gui.help_content import SETUP_GUIDES
        from gui.help_dialog import GuideWindow

        GuideWindow(self._app, [("customers", SETUP_GUIDES["customers"])])

    # -- list -----------------------------------------------------
    def refresh(self) -> None:
        """Reload the customer list from the DB."""
        for w in self._list.winfo_children():
            w.destroy()
        # Keep the "send to" switches in sync with the systems set up in Settings.
        svc_label, acct_label = self._show_targets()

        rows = self._db.list_customers_sorted(self._sort.get(),
                                              new_only=bool(self._new_only.get()))
        unreviewed = self._db.count_unreviewed_customers()
        self._count.configure(
            text=(f"{len(rows)} shown  ·  {unreviewed} new" if unreviewed
                  else f"{len(rows)} shown"),
            text_color=C["amber"] if unreviewed else C["dim"])

        for row in rows:
            self._add_row(row, svc_label, acct_label)

    def _add_row(self, row, svc_label: str, acct_label: str) -> None:
        """One clickable supplier entry: full-width name, dim routing line below.

        Built from a frame + two labels rather than a single CTkButton so the
        name can have the whole column width and wrap when it must, while the
        routing summary and NEW marker sit on a quieter second line.
        """
        tags = []
        if row["servicem8_enabled"]:
            tags.append(svc_label)
        if row["accounting_enabled"]:
            tags.append(acct_label)
        is_new = not row["reviewed"]
        routing = " / ".join(tags) or "not routed"
        sub_text = f"NEW  ·  {routing}" if is_new else routing

        card = ctk.CTkFrame(self._list, fg_color=C["row"], corner_radius=4)
        card.pack(fill="x", pady=2)
        name = ctk.CTkLabel(card, text=row["name"], anchor="w", justify="left",
                            wraplength=250, font=FONT_UI,
                            text_color=C["amber"] if is_new else C["text"])
        name.pack(fill="x", padx=8, pady=(5, 0))
        sub = ctk.CTkLabel(card, text=sub_text, anchor="w", justify="left",
                           font=("Segoe UI", 11),
                           text_color=C["amber"] if is_new else C["dim"])
        sub.pack(fill="x", padx=8, pady=(0, 5))

        # The frame and both labels behave as one button (click + hover).
        for w in (card, name, sub):
            w.bind("<Button-1>", lambda _e, cid=row["id"]: self._load(cid))
            w.bind("<Enter>", lambda _e: card.configure(fg_color=C["select"]))
            w.bind("<Leave>", lambda _e: card.configure(fg_color=C["row"]))

    # -- form ---------------------------------------------------
    def _build_form(self) -> None:
        """Lay out the supplier's details: the essentials, then More options."""
        f = self._form
        ctk.CTkLabel(f, text="Supplier details", font=FONT_HEAD,
                     text_color=C["text"]).pack(anchor="w", padx=6, pady=(8, 0))
        self._hint = ctk.CTkLabel(f, text="", font=FONT_UI, text_color=C["dim"], anchor="w",
                                  justify="left", wraplength=560)
        self._hint.pack(fill="x", padx=6, pady=(0, 8))

        self._name = self._entry(f, "Name", "As it appears on their invoices")
        self._aliases = self._entry(f, "Also known as",
                                    "Other names on their invoices, separated by commas")

        targets = ctk.CTkFrame(f, fg_color=C["panel"])
        targets.pack(fill="x", padx=6, pady=10)
        ctk.CTkLabel(targets, text="Send their invoices to", font=FONT_UI,
                     text_color=C["text"]).pack(anchor="w", padx=12, pady=(8, 2))
        self._sm8 = ctk.CTkSwitch(targets, text="")
        self._acct = ctk.CTkSwitch(targets, text="")
        self._no_targets = ctk.CTkLabel(
            targets, text="Nothing is set up yet. Connect ServiceM8 or your accounting "
                          "system in Settings first.",
            font=FONT_UI, text_color=C["amber"], anchor="w")
        self._targets_box = targets
        self._show_targets()

        # Everything below is optional, so it starts folded away.
        self._more_btn = ctk.CTkButton(f, text="More options  ▸", width=10, height=26,
                                       fg_color="transparent", hover_color=C["row"],
                                       text_color=C["teal"], font=FONT_UI, anchor="w",
                                       command=self._toggle_more)
        self._more_btn.pack(anchor="w", padx=2, pady=(2, 2))
        more = ctk.CTkFrame(f, fg_color=C["bg"])
        self._more = more
        self._sm8_uuid = self._entry(more, "ServiceM8 client ID",
                                     "Only needed if the name doesn't match in ServiceM8")
        self._acct_id = self._entry(more, "Accounting contact ID",
                                    "Only needed if the name doesn't match")
        self._notes = self._entry(more, "Notes", "Anything worth remembering")
        types = ctk.CTkFrame(more, fg_color=C["panel"])
        types.pack(fill="x", padx=6, pady=6)
        ctk.CTkLabel(types, text="File types to file (most suppliers send PDF only)",
                     font=FONT_UI, text_color=C["text"]).pack(anchor="w", padx=12, pady=(8, 2))
        row = ctk.CTkFrame(types, fg_color=C["panel"])
        row.pack(anchor="w", padx=12, pady=(0, 8))
        for ext in SUPPORTED_FILE_TYPES:
            cb = ctk.CTkCheckBox(row, text=ext.upper(), width=60)
            cb.pack(side="left", padx=4)
            self._type_vars[ext] = cb

        bar = ctk.CTkFrame(f, fg_color=C["bg"])
        bar.pack(fill="x", padx=6, pady=14)
        self._bar = bar
        accent_button(ctk, bar, "Save", self._save, colour=C["teal_btn"]).pack(side="left")
        accent_button(ctk, bar, "Delete", self._delete, colour=C["btn_off"]).pack(side="left", padx=8)
        self._status = ctk.CTkLabel(f, text="", font=FONT_UI, text_color=C["dim"])
        self._status.pack(anchor="w", padx=6)
        self._new()

    def _show_targets(self) -> tuple[str, str]:
        """Show a switch only for each system that's set up; returns their labels."""
        svc = self._app.settings.get("service.provider", "servicem8")
        acct = self._app.settings.get("accounting.provider", "none")
        svc_label, acct_label = label_for(svc), label_for(acct)
        for w in (self._sm8, self._acct, self._no_targets):
            w.pack_forget()
        if svc and svc != "none":
            self._sm8.configure(text=svc_label)
            self._sm8.pack(anchor="w", padx=12, pady=4)
        if not ACCOUNTING_ENABLED:
            acct = "none"
        if acct and acct != "none":
            self._acct.configure(text=acct_label)
            self._acct.pack(anchor="w", padx=12, pady=4)
        if (not svc or svc == "none") and (not acct or acct == "none"):
            self._no_targets.pack(anchor="w", padx=12, pady=4)
        self._targets_box.pack_configure(pady=(10, 10))
        return svc_label, acct_label

    def _toggle_more(self, show: bool | None = None) -> None:
        """Fold or unfold More options."""
        show = not self._more.winfo_ismapped() if show is None else show
        if show:
            self._more.pack(fill="x", before=self._bar)
            self._more_btn.configure(text="More options  ▾")
        else:
            self._more.pack_forget()
            self._more_btn.configure(text="More options  ▸")

    def _entry(self, parent, label: str, placeholder: str = "") -> ctk.CTkEntry:
        """One labelled text field in the details form."""
        wrap = ctk.CTkFrame(parent, fg_color=C["bg"])
        wrap.pack(fill="x", padx=6, pady=3)
        ctk.CTkLabel(wrap, text=label, font=FONT_UI, text_color=C["text"],
                     width=170, anchor="w").pack(side="left")
        e = ctk.CTkEntry(wrap, width=360, placeholder_text=placeholder)
        e.pack(side="left", fill="x", expand=True)
        return e

    # -- load / new / save / delete ------------------------------
    def _new(self) -> None:
        """Clear the form to create a customer from scratch."""
        self._current_id = None
        for e in (self._name, self._aliases, self._sm8_uuid, self._acct_id, self._notes):
            e.delete(0, "end")
        self._sm8.deselect(); self._acct.deselect()
        for ext, cb in self._type_vars.items():
            cb.select() if ext == "pdf" else cb.deselect()
        self._toggle_more(False)
        self._hint.configure(text="Pick a supplier on the left, or fill this in to add a new "
                                  "one. Suppliers are also added for you when their first "
                                  "invoice arrives.", text_color=C["dim"])
        self._status.configure(text="")

    def _load(self, cid: int) -> None:
        """Populate the form from one stored customer profile."""
        row = self._db.get_customer(cid)
        if not row:
            return
        self._current_id = cid
        import json
        self._name.delete(0, "end"); self._name.insert(0, row["name"])
        self._aliases.delete(0, "end")
        self._aliases.insert(0, ", ".join(json.loads(row["aliases"] or "[]")))
        self._sm8_uuid.delete(0, "end"); self._sm8_uuid.insert(0, row["servicem8_client_uuid"])
        self._acct_id.delete(0, "end"); self._acct_id.insert(0, row["accounting_contact_id"])
        self._notes.delete(0, "end"); self._notes.insert(0, row["notes"])
        (self._sm8.select if row["servicem8_enabled"] else self._sm8.deselect)()
        (self._acct.select if row["accounting_enabled"] else self._acct.deselect)()
        enabled = set(row["file_types"].split(","))
        for ext, cb in self._type_vars.items():
            cb.select() if ext in enabled else cb.deselect()
        # Open More options when something in it is filled in, so nothing is hidden.
        self._toggle_more(bool(row["servicem8_client_uuid"] or row["accounting_contact_id"]
                               or row["notes"] or enabled != {"pdf"}))
        if not row["reviewed"]:
            self._hint.configure(text="Added automatically when their first invoice arrived. "
                                      "Check the name and where their invoices go, then Save.",
                                 text_color=C["amber"])
        else:
            self._hint.configure(text="", text_color=C["dim"])
        self._status.configure(text="")

    def _collect(self) -> dict:
        """Read the form back into an upsert-ready dict."""
        return {
            "id": self._current_id,
            "name": self._name.get().strip(),
            "aliases": [a.strip() for a in self._aliases.get().split(",") if a.strip()],
            "servicem8_enabled": bool(self._sm8.get()),
            "myob_enabled": False,  # retained column; routing uses the two toggles above
            "accounting_enabled": bool(self._acct.get()),
            "file_types": [ext for ext, cb in self._type_vars.items() if cb.get()] or ["pdf"],
            "servicem8_client_uuid": self._sm8_uuid.get().strip(),
            "accounting_contact_id": self._acct_id.get().strip(),
            "notes": self._notes.get().strip(),
        }

    def _save(self) -> None:
        """Validate and persist the form, then refresh the list."""
        data = self._collect()
        if not data["name"]:
            self._status.configure(text="Enter the supplier's name.", text_color=C["red"])
            return
        try:
            # Saving IS the review: opening an auto-added supplier, checking
            # its toggles and saving clears the NEW badge.
            data["reviewed"] = True
            self._current_id = self._db.upsert_customer(data)
            self.refresh()
            self._hint.configure(text="", text_color=C["dim"])
            self._status.configure(text="Saved.", text_color=C["teal"])
        except Exception as exc:
            self._status.configure(text=f"Save failed: {exc}", text_color=C["red"])

    def _delete(self) -> None:
        """Remove the loaded customer and reset the form."""
        if self._current_id is None:
            return
        name = self._name.get().strip() or "this supplier"
        if not messagebox.askyesno("Delete supplier",
                                   f"Delete {name}? Their past activity stays in the log.",
                                   icon="warning", parent=self._form.winfo_toplevel()):
            return
        self._db.delete_customer(self._current_id)
        self._new()
        self.refresh()
        self._status.configure(text="Supplier deleted.", text_color=C["amber"])

    # -- used by the new-customer modal --------------------------
    def add_from_dialog(self, data: dict) -> int:
        """Persist a customer created via the watcher's new-customer prompt."""
        cid = self._db.upsert_customer(data)
        self.refresh()
        return cid
