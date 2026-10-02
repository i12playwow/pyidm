"""PyIDM GUI — About dialog with the live config table and its editor.

Split out of gui.py (one-shot refactor); this module is part of the idm.gui
package and is not a stable API.
"""
from __future__ import annotations

import ast
import json
import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:  # method bodies type against the assembled app
    from idm.gui import _AppBase as _MixinBase
else:
    _MixinBase = object


class AboutDialogsMixin(_MixinBase):
    """Mixin for idm.gui.App — see gui.py for assembly."""

    def show_about(self) -> None:
        """About dialog: version + the effective merged config with the layer
        each value came from (defaults -> user config -> local config -> env)."""
        win = tk.Toplevel(self)
        win.title("About PyIDM")
        win.geometry("780x520")
        win.transient(self)

        frm = ttk.Frame(win, padding=10)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text=f"PyIDM v{_g.__version__} — batch download manager",
                  font=("TkDefaultFont", 12, "bold")).pack(anchor="w")
        ttk.Label(frm, text=(
            "Effective config = defaults <- ~/.idm/config.json <- ./idm.json <- environment.\n"
            "The Source column shows which layer last set each value. Secrets are masked."
        ), foreground="#555").pack(anchor="w", pady=(2, 8))

        cfg_cols = ("key", "value", "source")
        tree = ttk.Treeview(frm, columns=cfg_cols, show="headings")
        for c, w, anchor in (("key", 210, "w"), ("value", 320, "w"), ("source", 200, "w")):
            tree.heading(c, text=c.title())
            tree.column(c, width=w, anchor=cast(Any, anchor))  # Tk accepts any anchor str
        vsb = ttk.Scrollbar(frm, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="left", fill="y")

        cfg, sources = _g.effective_config_with_sources()
        secret_keys = {"opensubtitles_api_key"}

        def fmt(value) -> str:
            if isinstance(value, str):
                return value
            try:
                import json
                text = json.dumps(value)
            except (TypeError, ValueError):
                text = str(value)
            return text if len(text) <= 60 else text[:57] + "..."

        for key in sorted(cfg):
            value = cfg[key]
            if key in secret_keys and isinstance(value, str) and value:
                shown = _g.mask_secret(value)
            else:
                shown = fmt(value)
            tree.insert("", "end", values=(key, shown, sources.get(key, "")))

        btns = ttk.Frame(frm)
        btns.pack(side="bottom", fill="x", pady=(8, 0))

        def copy_json() -> None:
            import json
            self.clipboard_clear()
            self.clipboard_append(json.dumps(cfg, indent=2, default=str))
            self._log("effective config copied to clipboard as JSON")

        def open_edit(event=None) -> None:
            sel = tree.selection()
            if sel:
                self._edit_config_value(tree, sel[0])

        tree.bind("<Double-1>", open_edit)
        ttk.Button(frm, text="Edit selected value…",
                   command=open_edit).pack(side="left", pady=(6, 0))
        ttk.Button(btns, text="Copy effective config as JSON",
                   command=copy_json).pack(side="right")

    def _edit_config_value(self, tree, iid) -> None:
        values = tree.set(iid)
        key = values["key"]
        shown_value = values["value"]
        win = tk.Toplevel(self)
        win.title(f"Edit config: {key}")
        win.resizable(False, False)
        win.transient(self)
        _g._grab_modal(win)
        frm = ttk.Frame(win, padding=14)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text=f"key: {key}", font=("TkDefaultFont", 11, "bold")).pack(anchor="w")
        ttk.Label(frm, text=f"current effective value: {shown_value}").pack(anchor="w", pady=(2, 6))

        cfg_now, sources = _g.effective_config_with_sources()
        for w in _g.edit_warnings(key, dict(self.cfg), sources):
            ttk.Label(frm, text=f"• {w}", foreground="#b26a00",
                      wraplength=440, justify="left").pack(anchor="w", pady=1)

        # List-typed keys get the structured row editor below; it drives the
        # same StringVar, so the raw JSON entry stays the single source of
        # truth and Save/_edit_input work unchanged. Seeded with the real
        # (untruncated) current list — the table column may have cut it off.
        is_list_key = _string_list_key(key)
        if is_list_key:
            from .config import normalize_verify_ignore
            var = tk.StringVar(
                value=json.dumps(normalize_verify_ignore(cfg_now.get(key))))
        else:
            var = tk.StringVar()

        ttk.Label(frm, text=("New value as JSON — the entry rows below stay in sync:"
                             if is_list_key else
                             "New value (saved to ~/.idm/config.json):")
                  ).pack(anchor="w", pady=(8, 2))
        entry = ttk.Entry(frm, textvariable=var, width=52)
        entry.pack(fill="x")

        if is_list_key:
            ttk.Label(frm, text="Entries — add one below, select and remove:").pack(
                anchor="w", pady=(8, 2))
            lb_frame = ttk.Frame(frm)
            lb_frame.pack(fill="x")
            listbox = tk.Listbox(lb_frame, height=4, selectmode="extended",
                                 exportselection=False)
            vsb = ttk.Scrollbar(lb_frame, orient="vertical", command=listbox.yview)
            listbox.configure(yscrollcommand=vsb.set)
            listbox.pack(side="left", fill="both", expand=True)
            vsb.pack(side="left", fill="y")

            def _current_rows() -> list[str]:
                text = var.get().strip()
                if not text:
                    return []
                try:
                    value = json.loads(text)
                except ValueError:
                    return [text]        # a bare token typed by hand (same rule as _edit_input)
                if isinstance(value, list):
                    return [str(x) for x in value]
                return [str(value)]

            def _render_rows(*_):
                listbox.delete(0, "end")
                for row in _current_rows():
                    listbox.insert("end", row)

            def _add_row():
                text = add_var.get().strip()
                if not text:
                    return
                rows = _current_rows()
                rows.append(text)        # verbatim: a mangled name keeps its commas
                var.set(json.dumps(rows))
                add_var.set("")

            def _remove_rows():
                sel = list(listbox.curselection())
                if not sel:
                    return
                rows = _current_rows()
                for i in sorted(sel, reverse=True):
                    if 0 <= i < len(rows):
                        del rows[i]
                var.set(json.dumps(rows))

            add_frame = ttk.Frame(frm)
            add_frame.pack(fill="x", pady=(6, 0))
            add_var = tk.StringVar()
            add_entry = ttk.Entry(add_frame, textvariable=add_var, width=40)
            add_entry.pack(side="left", fill="x", expand=True)
            ttk.Button(add_frame, text="Add entry",
                       command=_add_row).pack(side="left", padx=(6, 0))
            ttk.Button(frm, text="Remove selected",
                       command=_remove_rows).pack(anchor="w", pady=(6, 0))
            var.trace_add("write", _render_rows)
            _render_rows()

        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(12, 0))

        def refresh_table() -> None:
            cfg2, sources2 = _g.effective_config_with_sources()
            tree.delete(*tree.get_children())
            for k in sorted(cfg2):
                v = cfg2[k]
                masked = _g.mask_secret(v) if k == "opensubtitles_api_key" and isinstance(v, str) and v else None
                import json as _json
                text = v if isinstance(v, str) else _json.dumps(v)
                if len(text) > 60:
                    text = text[:57] + "..."
                tree.insert("", "end", values=(k, masked or text, sources2.get(k, "")))

        def _persist_edit(new_value: str | None) -> None:
            if new_value is None:
                if _g.remove_config_value(key):
                    self._log(f"config: removed {key} from ~/.idm/config.json "
                              "(falls back to defaults / idm.json)")
                else:
                    self._log(f"config: {key} was not set in ~/.idm/config.json")
            else:
                _g.set_config_value(key, new_value)
                if key == "opensubtitles_api_key" and new_value:
                    _g.set_user_env("OPENSUBTITLES_API_KEY", new_value)
                self._log(f"config: saved {key} -> ~/.idm/config.json")
            self.cfg.update(_g.get_config())
            refresh_table()
            win.destroy()

        def on_save() -> None:
            _persist_edit(_edit_input(key, var.get().strip()))

        def on_reset() -> None:
            _persist_edit(None)

        ttk.Button(btns, text="Cancel", command=win.destroy).pack(side="right")
        ttk.Button(btns, text="Reset to default (remove key)",
                   command=on_reset).pack(side="right", padx=6)
        ttk.Button(btns, text="Save", command=on_save).pack(side="right")
        entry.focus_set()


from idm import gui as _g  # noqa: E402  (late import: gui imports this module)

# Keys whose value is a JSON array of strings; each gets the structured
# row editor in the About dialog (add/remove entry rows). A list of
# objects (domain_headers) must never be listed here.
_STRING_LIST_KEYS = {"verify_ignore", "link_providers"}


def _string_list_key(key: str) -> bool:
    """True for config keys whose value is a JSON array of strings — the
    About editor grows the structured add/remove-entry widget for these
    (verify_ignore, link_providers). An explicit registry rather than a
    DEFAULTS-type check: domain_headers is also a list, but of objects,
    which the row editor cannot represent."""
    return key in _STRING_LIST_KEYS


def _edit_input(key: str, text: str) -> str:
    """Normalize one About-editor input for a list-typed key (verify_ignore):
    already-JSON passes through untouched; a Python-style bracketed list or
    space-separated tokens is re-encoded as JSON. A single bare token is kept
    verbatim even when it contains commas — PyIDM's own mangled filenames do
    ('130425,_360p.mp4,.mp4,_720p.mp4,') — so a filename can never be split
    into garbage. Returns text unchanged for every other key."""
    if key != "verify_ignore" or not text:
        return text
    try:
        json.loads(text)
        return text                      # valid JSON (array, string, …): as-is
    except ValueError:
        pass
    if text.startswith("[") and text.endswith("]"):
        try:
            parsed = ast.literal_eval(text)
        except (ValueError, SyntaxError):
            parsed = None
        if isinstance(parsed, (list, tuple)):
            return json.dumps([str(x).strip() for x in parsed if str(x).strip()])
        return text                      # malformed brackets: let config warn
    parts = [p for p in (t.strip(", ") for t in text.split()) if p]
    if len(parts) > 1:
        return json.dumps(parts)         # 'a.webm b.webm' -> ["a.webm", "b.webm"]
    return json.dumps([text])            # one token: a filename, commas and all
