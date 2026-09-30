"""PyIDM GUI — Export/report dialogs and their write paths, as an App mixin.

Split out of gui.py (one-shot refactor); this module is part of the idm.gui
package and is not a stable API.
"""
from __future__ import annotations

import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import TYPE_CHECKING

from idm import jq
from idm.utils import write_text_newlines

if TYPE_CHECKING:  # method bodies type against the assembled app
    from idm.gui import _AppBase as _MixinBase
else:
    _MixinBase = object


class ExportDialogsMixin(_MixinBase):
    """Mixin for idm.gui.App — see gui.py for assembly."""

    def run_report_preset(self, kind: str, name: str) -> None:
        """Tools ▸ 'Run <kind> preset: <name>': the in-process twin of
        'idm stats --preset NAME' / 'idm providers --preset NAME'. Reads the
        same preset store, writes the same report file (pinned out, with
        {date}/{kind} expansion), applies the pinned query the same way, and
        logs the exported path where --export prints it. providers checks
        are network work and run off the UI thread, like the providers tab."""
        p = _g.load_presets(kind).get(name)
        if p is None:
            self._log(f"unknown report preset: {name} — see 'idm presets list'",
                      "error")
            return
        query = p.get("query") if isinstance(p.get("query"), str) else ""
        out = p.get("out") if isinstance(p.get("out"), str) else ""
        viewer = p.get("viewer") if isinstance(p.get("viewer"), str) else ""
        if kind == "providers":
            if self._prov_running:
                self._log("provider check already running — try again in a "
                          "moment", "warn")
                return
            self._prov_running = True
            self._log(f"running providers preset '{name}' (health checks)…")

            def worker():
                try:
                    results = _g.run_checks(dict(self.cfg), deep=False)
                    self.events.put(("report_done", kind, name, query, out,
                                     viewer, results, None))
                except Exception as e:  # surface the failure in the log
                    self.events.put(("report_done", kind, name, query, out,
                                     viewer, None, e))

            threading.Thread(target=worker, daemon=True).start()
            return
        # stats is cheap local-file reading — do it inline so the log line
        # lands in the same tick
        try:
            payload = _g.stats_payload(_g.load_subs_history(),
                                    _g.read_state_records())
            out_dir = Path(self.out_var.get().strip() or "downloads")
            payload["downloads"]["path"] = str(out_dir / "idm.state.json")
            payload["subtitles"]["path"] = str(_g.SUBS_HISTORY_FILE)
        except Exception as e:
            self._log(f"stats preset '{name}' failed: {e}", "error")
            return
        self._finish_report_export(kind, name, query, out, viewer, payload,
                                   None)
    def _finish_report_export(self, kind: str, name: str, query: str,
                              out: str, viewer: str, payload, error) -> None:
        """Common tail of run_report_preset (both sync and threaded paths):
        apply the preset's query exactly like the CLI's --query, write the
        pinned out file (with {date}/{kind} expansion), then log where the
        report went and open it in the preset's viewer."""
        self._prov_running = False
        if error is not None:
            self._log(f"{kind} preset '{name}' failed: {error}", "error")
            return
        try:
            result = _g.apply_export_query(query, payload) if query else payload
        except jq.JqError as e:
            self._log(f"report query invalid: {e} — nothing exported", "warn")
            return
        if not out:
            self._log(f"preset '{name}' has no out — pin one with "
                      "'idm presets add stats NAME out=report.json'", "warn")
            return
        path = Path(_g.expand_preset_out(out, kind))
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            write_text_newlines(path, _g.export_json_text(result))
        except OSError as e:
            self._log(f"could not write report: {e}", "warn")
            return
        scope = ", ".join(s for s in (
            f"query {query}" if query else "",
            f"preset {name}" if name else "") if s)
        self._log(f"exported {kind} report -> {path}"
                  f" ({scope})" if scope else f"exported {kind} report -> {path}")
        if viewer:
            self._open_export(str(path), viewer)
    def _on_report_done(self, kind: str, name: str, query: str, out: str,
                        viewer: str, results, error) -> None:
        """Threaded providers-preset run came back: build the same health
        payload 'idm providers --export' would and finish the export."""
        payload = None
        if error is None and results is not None:
            payload = _g.health_payload(results, "quick")
        self._finish_report_export(kind, name, query, out, viewer, payload,
                                   error)
    def clear_export_prefs(self) -> None:
        """Help ▸ 'Clear remembered settings…': forget the per-dialog export
        settings (formats, filters, last-used queries) stored in
        export_prefs.json. Confirm first; a missing file still counts as
        cleared; a delete failure warns without raising."""
        if not messagebox.askyesno(
                "PyIDM",
                "Forget all remembered export settings?\n\n"
                "(formats, filters, and last-used queries in\n"
                f"{_g.EXPORT_PREFS_FILE})"):
            return
        try:
            _g.EXPORT_PREFS_FILE.unlink(missing_ok=True)
        except OSError as e:
            self._log(f"could not reset export settings: {e}", "warn")
            return
        self._log("remembered export settings cleared "
                  "(dialogs start from defaults)")
    def run_provider_check(self) -> None:
        """Run the provider health checks off the UI thread (never blocks Tk)."""
        if self._prov_running:
            return
        self._prov_running = True
        deep = bool(self.prov_deep_var.get())
        self.prov_btn.config(state="disabled")
        self.prov_status_var.set("checking…")
        self._log(f"checking subtitle providers ({'deep' if deep else 'quick'})…")

        def worker():
            try:
                results = _g.run_checks(dict(self.cfg), deep=deep)
                self.events.put(("providers_done", results))
            except Exception as e:  # surface unexpected failures in the tab
                self.events.put(("providers_done", e))

        threading.Thread(target=worker, daemon=True).start()
    def _on_providers_done(self, results) -> None:
        self._prov_running = False
        self.prov_btn.config(state="normal")
        self.prov_tree.delete(*self.prov_tree.get_children())
        if isinstance(results, Exception):
            self.prov_status_var.set(f"check failed: {results}")
            self._log(f"provider check failed: {results}", "error")
            return
        for provider, status, detail, endpoint in _g.provider_rows(results):
            self.prov_tree.insert("",
                                  "end",
                                  values=(provider, status, detail, endpoint),
                                  tags=(status.lower(),))
        tagmap = {"ok": "#1a7f37", "warn": "#b26a00", "down": "#c62828"}
        for tag, color in tagmap.items():
            self.prov_tree.tag_configure(tag, foreground=color)
        self.prov_hints.config(state="normal")
        self.prov_hints.delete("1.0", "end")
        hints = _g.provider_hints(results)
        if hints:
            self.prov_hints.insert("end", "\n".join(hints) + "\n")
        else:
            self.prov_hints.insert("end", "No hints — all reachable providers look healthy.\n")
        counts = {s: sum(1 for r in results if r.status == s) for s in ("ok", "warn", "down")}
        self.prov_status_var.set(
            f"{counts['ok']} OK · {counts['warn']} warn · {counts['down']} down")
        if counts["down"]:
            self._log(f"providers: {counts['down']} down — the chain skips them "
                      "automatically", "warn")
        else:
            self._log(f"providers checked: {counts['ok']} OK, {counts['warn']} warn")
    def _maybe_first_run_wizard(self) -> None:
        if _g.should_show_first_run(self.cfg):
            self._api_key_wizard(first_run=True)
    def _api_key_wizard(self, first_run: bool = False) -> None:
        win = tk.Toplevel(self)
        win.title("PyIDM setup — OpenSubtitles API key")
        win.resizable(False, False)
        win.transient(self)
        win.grab_set()
        frm = ttk.Frame(win, padding=16)
        frm.pack(fill="both", expand=True)
        if first_run:
            ttk.Label(frm, text="Welcome to PyIDM!",
                      font=("TkDefaultFont", 13, "bold")).pack(anchor="w")
        ttk.Label(frm, text=(
            "Subtitles already work with no key (keyless providers).\n"
            "A free OpenSubtitles key adds exact hash matching — it finds\n"
            "subtitles for ANY file, not just popular movies."
        ), justify="left").pack(anchor="w", pady=(6, 2))
        link = ttk.Label(frm, text="Get a free key: opensubtitles.com → Profile → API Keys",
                         foreground="#1565c0", cursor="hand2")
        link.pack(anchor="w")
        link.bind("<Button-1>", lambda e: webbrowser.open(_g.OS_API_KEYS_URL))
        ttk.Label(frm, text="API key (leave empty to skip):").pack(anchor="w", pady=(10, 2))
        key_var = tk.StringVar()
        entry = ttk.Entry(frm, textvariable=key_var, width=46)
        entry.pack(fill="x")
        status_var = tk.StringVar(value="")
        ttk.Label(frm, textvariable=status_var).pack(anchor="w")
        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(10, 0))

        def _finish_skip() -> None:
            _g.set_config_value("wizard_done", "true")
            self.cfg["wizard_done"] = True
            self._log("Setup skipped — keyless subtitle providers stay active. "
                      "Set a key anytime via the 'API key…' button.")
            win.destroy()

        def _save() -> None:
            key = key_var.get().strip()
            if not key:
                _finish_skip()
                return
            status_var.set("Verifying key…")
            win.update_idletasks()
            ok, msg = _g.verify_opensubtitles_key(key)
            if ok:
                self._save_api_key(key)
                win.destroy()
                return
            if messagebox.askyesno("PyIDM",
                                   f"The key was rejected ({msg}).\nSave it anyway?",
                                   parent=win):
                self._save_api_key(key)
                win.destroy()
            else:
                status_var.set("")

        ttk.Button(btns, text="Skip for now", command=_finish_skip).pack(side="right")
        ttk.Button(btns, text="Save key", command=_save).pack(side="right", padx=6)
        entry.focus_set()
        win.protocol("WM_DELETE_WINDOW", _finish_skip)
    def _save_api_key(self, key: str) -> None:
        _g.set_config_value("opensubtitles_api_key", key)
        _g.set_user_env("OPENSUBTITLES_API_KEY", key)  # Windows user env for the exes
        self.cfg["opensubtitles_api_key"] = key
        self.cfg["wizard_done"] = True
        masked = key[:4] + "*" * 8 + "..."
        self._log(f"OpenSubtitles key saved ({masked}) — config + Windows user environment.")
    def _save_subs_history_file(self) -> None:
        """Save the (optionally filtered) history to a .csv, .md, or .json
        file, optionally through a jq-style --query (the same evaluator the
        CLI uses). Filter dialog first — all-empty is accepted silently so
        the plain full export stays one extra keypress; format follows the
        chosen extension. Cancel at either stage is a silent no-op; write
        failures log a warning. When 'Open after export' is checked, the
        written file opens with the Viewer entry's app (default app when
        empty) — the GUI twin of the CLI's --viewer/--quiet flags."""
        got = self._open_filter_dialog()
        if got[0] is None:
            return  # filter dialog cancelled
        # tolerate 3-tuples from older filter-dialog shims (query optional)
        while len(got) < 4:
            got = (*got, "")
        prov, since, until, query = got
        path = filedialog.asksaveasfilename(
            title="Export subtitle history",
            defaultextension=".csv",
            initialfile=f"pyidm-subtitle-history-{time.strftime('%Y%m%d')}.csv",
            filetypes=self.EXPORT_FILETYPES)
        if not path:
            return
        fmt = ("markdown" if path.lower().endswith((".md", ".markdown"))
               else "json" if path.lower().endswith(".json") else "csv")
        open_after = bool(self.saveas_open_var.get())
        viewer = self.saveas_viewer_var.get().strip()
        try:
            if query:
                data = _g.filter_history(_g.load_subs_history(), prov, since, until)
                result = _g.apply_export_query(query, _g.history_json_entries(data))
                Path(path).write_text(_g.export_json_text(result),
                                      encoding="utf-8")
                n = (len(result) if isinstance(result, list)
                     else 1 if result is not None else 0)
            else:
                n = _g.write_history_export(path, fmt, provider=prov,
                                         since=since, until=until)
        except jq.JqError as e:
            self._log(f"export query invalid: {e} — nothing written", "warn")
            return
        except OSError as e:
            self._log(f"subtitle history export failed: {e}", "warn")
            return
        active = [s for s in (f"provider={prov}" if prov else "",
                              f"since {since}" if since else "",
                              f"until {until}" if until else "",
                              f"query {query}" if query else "") if s]
        scope = ", ".join(active)
        self._log(f"subtitle history exported to {path} "
                  f"({n} row(s), {fmt}{', ' + scope if scope else ''})")
        # same one-line preview the CLI prints after --out exports: read back
        # what was actually written (a read failure can't taint the export)
        try:
            preview = _g.export_preview(Path(path).read_text(encoding="utf-8"))
        except OSError:
            preview = "(unreadable)"
        self._log(f"preview: {preview}")
        if open_after:
            self._open_export(path, viewer)
    def _open_export(self, path, viewer: str = "") -> None:
        """Open a freshly written export with the chosen viewer app (or the
        default app when viewer is empty); failures warn instead of raising,
        so a bad Viewer entry can't taint a successful export."""
        how = f" in {viewer}" if viewer else " in the default app"
        opened = _g.open_file_with(path, viewer) if viewer else _g.open_file_safe(path)
        if opened:
            self._log(f"opened {path}{how}")
        else:
            tail = f" '{viewer}'" if viewer else ""
            self._log(f"could not open {path} with{tail or ' any app'} — "
                      "the export itself succeeded", "warn")
    def _open_filter_dialog(self):
        """Modal provider/date-range filter dialog for the history export.
        Returns (provider, since, until, query) on OK; on cancel/window
        close/invalid input it returns the legacy (None, '', '') shape that
        callers pad with an empty query (bad dates are surfaced as a log
        line rather than a modal messagebox). Remembers the last OK'd
        filters and query across GUI restarts."""
        dlg, _prov_cb, _since_e, _until_e, _query_e, _ok, _cancel = \
            self._build_filter_dialog()
        dlg.wait_window()
        result = self._filter_dialog_result
        return result[0] if result else (None, "", "")
    def _build_filter_dialog(self):
        """Build the history filter dialog (the non-modal core of
        _open_filter_dialog, split out so tests can drive it without
        blocking on wait_window). Provider/since/until/query prefill from
        the last OK'd values; OK() remembers all four. Returns
        (dlg, prov_cb, since_e, until_e, query_e, ok, cancel); the OK'd
        (provider, since, until, query) lands in self._filter_dialog_result."""
        dlg = tk.Toplevel(self)
        dlg.title("Filter history export")
        dlg.transient(self)
        dlg.grab_set()
        dlg.resizable(False, False)
        prefs = _g.load_export_prefs().get("history", {})
        providers = _g.history_providers(_g.load_subs_history())
        ttk.Label(dlg, text="Provider:").grid(row=0, column=0, sticky="e",
                                              padx=6, pady=4)
        prov_cb = ttk.Combobox(dlg, values=["(all)"] + providers,
                               state="readonly", width=18)
        last_prov = str(prefs.get("provider") or "")
        prov_cb.set(last_prov if last_prov in providers else "(all)")
        prov_cb.grid(row=0, column=1, padx=6, pady=4)
        ttk.Label(dlg, text="Since (YYYY-MM-DD):").grid(row=1, column=0,
                                                        sticky="e", padx=6, pady=4)
        since_e = ttk.Entry(dlg, width=20)
        if isinstance(prefs.get("since"), str):
            since_e.insert(0, prefs["since"])
        since_e.grid(row=1, column=1, padx=6, pady=4)
        ttk.Label(dlg, text="Until (YYYY-MM-DD):").grid(row=2, column=0,
                                                        sticky="e", padx=6, pady=4)
        until_e = ttk.Entry(dlg, width=20)
        if isinstance(prefs.get("until"), str):
            until_e.insert(0, prefs["until"])
        until_e.grid(row=2, column=1, padx=6, pady=4)
        ttk.Label(dlg, text="Query (jq-style, optional):").grid(row=3, column=0,
                                                                 sticky="e", padx=6, pady=4)
        query_e = ttk.Entry(dlg, width=24)
        if isinstance(prefs.get("query"), str):
            query_e.insert(0, prefs["query"])
        query_e.grid(row=3, column=1, padx=6, pady=4)
        match_lbl = ttk.Label(dlg, text="", foreground="#555")
        match_lbl.grid(row=4, column=0, columnspan=4, pady=(2, 0))

        def _filtered():
            return _g.filter_history(_g.load_subs_history(), prov_cb.get(),
                                  since_e.get(), until_e.get())

        def refresh_count(*_):
            try:
                _g.parse_export_filters(prov_cb.get(), since_e.get(), until_e.get())
                q = query_e.get().strip()
                if q:
                    _g.apply_export_query(q, _g.history_json_entries(_filtered()))
            except (ValueError, jq.JqError) as e:
                match_lbl.config(text=str(e), foreground="#b00020")
                return
            n = _g.export_match_count(prov_cb.get(), since_e.get(), until_e.get())
            # built up as separate statements: multi-line expressions inside
            # f-strings are Python 3.12+ syntax and break the import on 3.9-3.11
            text = f"{n} matching row(s)"
            q = query_e.get().strip()
            if q:
                nq = len(_g.apply_export_query(q, _g.history_json_entries(_filtered())))
                text += f" · query returns {nq}"
            match_lbl.config(foreground="#555", text=text)

        for w in (prov_cb, since_e, until_e, query_e):
            w.bind("<KeyRelease>", refresh_count)
        prov_cb.bind("<<ComboboxSelected>>", refresh_count)
        refresh_count()

        # --- presets row: pick a named provider/date/query combo, or save
        # the current fields as one (shared with 'idm presets' in the CLI).
        # One clickable chip per preset sits under the row for 1-click apply.
        def _apply_preset(_=None):
            p = _g.load_presets("history").get(preset_cb.get().strip())
            if p:
                prov_cb.set(p["provider"] if p.get("provider") in providers
                            else "(all)")
                since_e.delete(0, "end")
                if isinstance(p.get("since"), str):
                    since_e.insert(0, p["since"])
                until_e.delete(0, "end")
                if isinstance(p.get("until"), str):
                    until_e.insert(0, p["until"])
                query_e.delete(0, "end")
                if isinstance(p.get("query"), str):
                    query_e.insert(0, p["query"])
                if isinstance(p.get("viewer"), str):
                    self.saveas_viewer_var.set(p["viewer"])   # pinned viewer
                refresh_count()
            return "break"      # Return in the preset field applies only

        def _save_preset():
            name = preset_cb.get().strip()
            if _g.save_preset("history", name, provider=prov_cb.get(),
                           since=since_e.get().strip(),
                           until=until_e.get().strip(),
                           query=query_e.get().strip(),
                           viewer=self.saveas_viewer_var.get().strip()):
                preset_cb.configure(values=sorted(_g.load_presets("history")))
                self._log(f"export preset '{name}' saved (history)")

        def _delete_preset():
            name = preset_cb.get().strip()
            if _g.delete_preset("history", name):
                preset_cb.configure(values=sorted(_g.load_presets("history")))
                preset_cb.set("")
                self._log(f"export preset '{name}' deleted (history)")

        def _delete_preset_named(name):
            """Middle-click a preset chip: remove that preset — the Delete
            button's exact path (store, dropdown, log), with the name taken
            from the chip instead of the field."""
            if _g.delete_preset("history", name):
                preset_cb.configure(values=sorted(_g.load_presets("history")))
                if preset_cb.get().strip() == name:
                    preset_cb.set("")
                self._log(f"export preset '{name}' deleted (history)")

        preset_cb, _refresh_preset_chips = _g._build_preset_row(
            dlg, "history", 5, _apply_preset, _save_preset, _delete_preset,
            prefs, _delete_preset_named)

        # the last-used preset is preselected and applied on open, so a
        # preset saved from the CLI ('idm presets add') is one click away
        last_preset = prefs.get("preset")
        if isinstance(last_preset, str) and last_preset in preset_cb["values"]:
            preset_cb.set(last_preset)              # default to last-used
            _apply_preset()                         # ...and apply it up front

        result: list = []
        self._filter_dialog_result = result

        def _ok(_=None):
            try:
                filters = _g.parse_export_filters(prov_cb.get(),
                                               since_e.get(), until_e.get())
            except ValueError as e:
                self._log(f"export filter invalid: {e}", "warn")
                return
            q = query_e.get().strip()
            if q:
                try:
                    _g.apply_export_query(q, _g.history_json_entries(
                        _g.filter_history(_g.load_subs_history(), *filters)))
                except jq.JqError as e:
                    self._log(f"export query invalid: {e}", "warn")
                    return
            _g.remember_export_prefs("history", provider=prov_cb.get(),
                                  since=since_e.get().strip(),
                                  until=until_e.get().strip(), query=q,
                                  preset=preset_cb.get().strip())
            result.append((*filters, q))
            dlg.destroy()

        def _cancel(_=None):
            dlg.destroy()

        btns = ttk.Frame(dlg)
        # one row BELOW the preset chips (row 7, chips grid at 6): the two
        # rows used to share a grid row and the chips painted under OK/
        # Cancel
        btns.grid(row=7, column=0, columnspan=4, pady=(6, 8))
        ttk.Button(btns, text="OK", command=_ok).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancel", command=_cancel).pack(side="left", padx=4)
        dlg.bind("<Return>", _ok)
        dlg.bind("<Escape>", _cancel)
        dlg.protocol("WM_DELETE_WINDOW", _cancel)
        return dlg, prov_cb, since_e, until_e, query_e, _ok, _cancel
    def _open_downloads_export_dialog(self):
        """Modal format+query dialog for the download-state export.
        Returns (fmt, query) on OK, None on cancel/close. Remembers the last
        OK'd format and query across GUI restarts."""
        dlg, _fmt_cb, _query_e, _ok, _cancel, _refresh = \
            self._build_downloads_export_dialog()
        dlg.wait_window()
        result = self._downloads_export_dialog_result
        return result[0] if result else None
    def _build_downloads_export_dialog(self):
        """Build the format+query dialog (the non-modal core of
        _open_downloads_export_dialog, split out so tests can drive it
        without blocking on wait_window). Format and query prefill from the
        last OK'd values; OK() remembers both. Returns
        (dlg, fmt_cb, query_e, ok, cancel, refresh); the OK'd
        (fmt, query) lands in self._downloads_export_dialog_result."""
        dlg = tk.Toplevel(self)
        dlg.title("Export download state")
        dlg.transient(self)
        dlg.grab_set()
        dlg.resizable(False, False)
        prefs = _g.load_export_prefs().get("downloads", {})
        ttk.Label(dlg, text="Format:").grid(row=0, column=0, sticky="e",
                                            padx=6, pady=4)
        fmt_cb = ttk.Combobox(dlg, values=["csv", "markdown", "json"],
                              state="readonly", width=12)
        fmt_cb.set(prefs["fmt"] if prefs.get("fmt") in ("csv", "markdown",
                                                        "json") else "csv")
        fmt_cb.grid(row=0, column=1, padx=6, pady=4)
        ttk.Label(dlg, text="Query (jq-style, optional):").grid(row=1, column=0,
                                                                 sticky="e", padx=6, pady=4)
        query_e = ttk.Entry(dlg, width=26)
        if isinstance(prefs.get("query"), str):
            query_e.insert(0, prefs["query"])
        query_e.grid(row=1, column=1, padx=6, pady=4)
        note_lbl = ttk.Label(dlg, text="", foreground="#555")
        note_lbl.grid(row=2, column=0, columnspan=2, pady=(2, 0))

        def refresh(*_):
            q = query_e.get().strip()
            try:
                if q:
                    out_dir = Path(self.out_var.get().strip() or "downloads")
                    _g.apply_export_query(q, _g.download_json_records(
                        _g.read_state_records(out_dir / "idm.state.json")))
            except jq.JqError as e:
                note_lbl.config(text=str(e), foreground="#b00020")
                return
            note_lbl.config(foreground="#555", text="" if not q else "query ok")

        query_e.bind("<KeyRelease>", refresh)
        refresh()                      # validate any remembered query up front

        # --- presets row: pick a named filter+query combo, or save the
        # current fields as one (shared with 'idm presets' in the CLI).
        # One clickable chip per preset sits under the row for 1-click apply.
        def _apply_preset(_=None):
            p = _g.load_presets("downloads").get(preset_cb.get().strip())
            if p:
                if p.get("fmt") in ("csv", "markdown", "json"):
                    fmt_cb.set(p["fmt"])
                query_e.delete(0, "end")
                if isinstance(p.get("query"), str):
                    query_e.insert(0, p["query"])
                if isinstance(p.get("viewer"), str):
                    self.saveas_viewer_var.set(p["viewer"])   # pinned viewer
                refresh()
            return "break"      # Return in the preset field applies only

        def _save_preset():
            name = preset_cb.get().strip()
            if _g.save_preset("downloads", name, fmt=fmt_cb.get(),
                           query=query_e.get().strip(),
                           viewer=self.saveas_viewer_var.get().strip()):
                preset_cb.configure(values=sorted(_g.load_presets("downloads")))
                self._log(f"export preset '{name}' saved (downloads)")

        def _delete_preset():
            name = preset_cb.get().strip()
            if _g.delete_preset("downloads", name):
                preset_cb.configure(values=sorted(_g.load_presets("downloads")))
                preset_cb.set("")
                self._log(f"export preset '{name}' deleted (downloads)")

        def _delete_preset_named(name):
            """Middle-click a preset chip: remove that preset — the Delete
            button's exact path (store, dropdown, log), with the name taken
            from the chip instead of the field."""
            if _g.delete_preset("downloads", name):
                preset_cb.configure(values=sorted(_g.load_presets("downloads")))
                if preset_cb.get().strip() == name:
                    preset_cb.set("")
                self._log(f"export preset '{name}' deleted (downloads)")

        preset_cb, _refresh_preset_chips = _g._build_preset_row(
            dlg, "downloads", 3, _apply_preset, _save_preset, _delete_preset,
            prefs, _delete_preset_named)

        # the last-used preset is preselected and applied on open, so a
        # preset saved from the CLI ('idm presets add') is one click away
        last_preset = prefs.get("preset")
        if isinstance(last_preset, str) and last_preset in preset_cb["values"]:
            preset_cb.set(last_preset)              # default to last-used
            _apply_preset()                         # ...and apply it up front

        result: list = []
        self._downloads_export_dialog_result = result

        def _ok(_=None):
            fmt, q = fmt_cb.get(), query_e.get().strip()
            _g.remember_export_prefs("downloads", fmt=fmt, query=q,
                                  preset=preset_cb.get().strip())
            result.append((fmt, q))
            dlg.destroy()

        def _cancel(_=None):
            dlg.destroy()

        btns = ttk.Frame(dlg)
        # one row BELOW the preset chips (row 5, chips grid at 4): the two
        # rows used to share a grid row and the chips painted under OK/
        # Cancel
        btns.grid(row=5, column=0, columnspan=4, pady=(6, 8))
        ttk.Button(btns, text="OK", command=_ok).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancel", command=_cancel).pack(side="left", padx=4)
        dlg.bind("<Return>", _ok)
        dlg.bind("<Escape>", _cancel)
        dlg.protocol("WM_DELETE_WINDOW", _cancel)
        return dlg, fmt_cb, query_e, _ok, _cancel, refresh
    def _write_downloads_export(self, path, fmt: str, query: str) -> None:
        """The export itself (dialog-independent so tests can drive it):
        query -> JSON result; else csv/markdown/json per fmt."""
        out_dir = Path(self.out_var.get().strip() or "downloads")
        records = _g.read_state_records(out_dir / "idm.state.json")
        try:
            if query:
                data = _g.download_json_records(records)
                result_q = _g.apply_export_query(query, data)
                Path(path).write_text(_g.export_json_text(result_q),
                                      encoding="utf-8")
                n = (len(result_q) if isinstance(result_q, list)
                     else 1 if result_q is not None else 0)
                fmt = "json"
            elif fmt == "csv":
                text = _g.download_to_csv(_g.download_rows(records))
                write_text_newlines(path, text)
                n = len(records)
            elif fmt == "markdown":
                text = _g.download_to_markdown(_g.download_rows(records))
                write_text_newlines(path, text)
                n = len(records)
            else:
                Path(path).write_text(_g.export_json_text(
                    _g.download_json_records(records)), encoding="utf-8")
                n = len(records)
        except jq.JqError as e:
            self._log(f"export query invalid: {e} — nothing written", "warn")
            return
        except OSError as e:
            self._log(f"download-state export failed: {e}", "warn")
            return
        self._log(f"download state exported to {path} "
                  f"({n} row(s), {fmt}{', query ' + query if query else ''})")
        try:
            preview = _g.export_preview(Path(path).read_text(encoding="utf-8"))
        except OSError:
            preview = "(unreadable)"
        self._log(f"preview: {preview}")
        if bool(self.saveas_open_var.get()):
            self._open_export(path, self.saveas_viewer_var.get().strip())


from idm import gui as _g  # noqa: E402  (late import: gui imports this module)
