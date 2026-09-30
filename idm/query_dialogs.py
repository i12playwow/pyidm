"""PyIDM GUI — Run Query… dialogs and the sortable results window, as an App mixin.

Split out of gui.py (one-shot refactor); this module is part of the idm.gui
package and is not a stable API.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING, Any

from idm import jq

if TYPE_CHECKING:  # method bodies type against the assembled app
    from idm.gui import _AppBase as _MixinBase
else:
    _MixinBase = object


class QueryDialogsMixin(_MixinBase):
    """Mixin for idm.gui.App — see gui.py for assembly."""

    def run_query(self, source: str):
        """'Run Query…' flow: modal query entry, then the sortable results
        window. Cancel (or a query that fails validation) opens nothing."""
        query = self._open_query_dialog(source)
        if query is None:
            return None
        return self._show_query_results(source, query)
    def _open_query_dialog(self, source: str):
        """Modal query entry for the results window. Live-validates against
        the source's data (error in red, else the result count); Run
        re-validates and refuses to close on error — the same contract as the
        export filter dialog. Returns the query string, or None on cancel /
        window close / invalid query. Remembers the last run query per
        source across GUI restarts (prefilled next time)."""
        dlg, _query_e, _ok, _cancel, _refresh = self._build_query_dialog(source)
        dlg.wait_window()
        result = self._query_dialog_result
        return result[0] if result else None
    def _build_query_dialog(self, source: str):
        """Build the query-entry dialog for the results window (the non-modal
        core of _open_query_dialog, split out so tests can drive validation
        without        blocking on wait_window — same pattern as _write_downloads_export).
        The query field is a combobox prefilled with the last query run
        against this source; the dropdown lists the most recent queries
        (deduplicated, _g.RECENT_QUERY_LIMIT) and picking one live-validates
        it. A successful Run records the query. Returns
        (dlg, query_cb, ok, cancel, refresh); ok() refuses to close
        on an invalid/empty query, refresh() re-runs the live validation
        label, and the entered query lands in self._query_dialog_result."""
        dlg = tk.Toplevel(self)
        dlg.title(f"Run query — {source}")
        dlg.transient(self)
        dlg.grab_set()
        dlg.resizable(False, False)
        recent = _g._recent_queries_for(source)
        ttk.Label(dlg, text="Query (jq-style):").grid(row=0, column=0,
                                                      sticky="e", padx=6, pady=4)
        query_cb = ttk.Combobox(dlg, values=recent, width=44)
        if recent:
            query_cb.current(0)
        query_cb.grid(row=0, column=1, padx=6, pady=4)
        note_lbl = ttk.Label(dlg, text="", foreground="#555")
        note_lbl.grid(row=1, column=0, columnspan=2, pady=(2, 0))
        chip_row = ttk.Frame(dlg)
        chip_row.grid(row=2, column=0, columnspan=2, padx=6, sticky="w")

        def refresh_chips():
            """One clickable chip per recent query (newest first): clicking
            fills the field and live-validates it, it never runs the query.
            Middle-clicking a chip removes that query from the recents
            (quick curation) and re-renders the chips and dropdown."""
            for child in chip_row.winfo_children():
                child.destroy()
            recent = _g._recent_queries_for(source)
            if not recent:
                return
            for q in recent:
                chip = ttk.Button(
                    chip_row, text=q if len(q) <= 26 else q[:25] + "…",
                    command=lambda qq=q: (query_cb.set(qq), refresh()))
                _g._attach_tooltip(chip, _g._query_tip_text(q))
                _g._bind_middle_click(chip, lambda qq=q: _forget_recent(qq))
                chip.pack(side="left", padx=(0, 4))

        def _forget_recent(q: str):
            """Drop one recent query (chip middle-click) from the store and
            re-render the chips and the dropdown. If the removed entry was
            the field's prefill, the field follows the list (newest remaining
            entry, or empty)."""
            if not _g.forget_recent_query(source, q):
                return
            values = [v for v in query_cb["values"] if v != q]
            query_cb.configure(values=values)
            if query_cb.get() == q:
                query_cb.set(values[0] if values else "")
            refresh()
            refresh_chips()

        refresh_chips()
        result: list = []
        self._query_dialog_result = result

        def refresh(*_):
            q = query_cb.get().strip()
            if not q:
                note_lbl.config(foreground="#555", text="")
                return
            try:
                res = _g.apply_export_query(q, self._query_source_data(source))
                n = len(res) if isinstance(res, list) else 1
                note_lbl.config(foreground="#555",
                                text=f"query ok — returns {n} result(s)")
            except jq.JqError as e:
                note_lbl.config(text=str(e), foreground="#b00020")

        query_cb.bind("<KeyRelease>", refresh)
        query_cb.bind("<<ComboboxSelected>>", refresh)

        def _ok(_=None):
            q = query_cb.get().strip()
            if not q:
                return
            try:
                _g.apply_export_query(q, self._query_source_data(source))
            except jq.JqError as e:
                self._log(f"query invalid: {e}", "warn")
                return
            _g.remember_recent_query(source, q)
            result.append(q)
            dlg.destroy()

        def _cancel(_=None):
            dlg.destroy()

        btns = ttk.Frame(dlg)
        btns.grid(row=3, column=0, columnspan=2, pady=(6, 8))
        ttk.Button(btns, text="Run", command=_ok).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancel", command=_cancel).pack(side="left", padx=4)
        dlg.bind("<Return>", _ok)
        dlg.bind("<Escape>", _cancel)
        dlg.protocol("WM_DELETE_WINDOW", _cancel)
        query_cb.focus_set()
        return dlg, query_cb, _ok, _cancel, refresh
    def _show_query_results(self, source: str, query: str):
        """Evaluate `query` against the source's data and open a sortable
        results window: click a column heading to sort by it, click again to
        reverse; 'Copy JSON' puts the exact CLI-style payload on the
        clipboard, 'Copy TSV' a header+rows grid for spreadsheets. The
        window's size and column widths are remembered per source and
        restored next time. Bad queries open nothing and log a warning.
        Returns the Toplevel, or None."""
        try:
            result = _g.apply_export_query(query, self._query_source_data(source))
        except jq.JqError as e:
            self._log(f"query invalid: {e} — no results window", "warn")
            return None
        cols, rows = _g._query_result_rows(result)
        _raw_layout = _g.load_export_prefs().get(f"results:{source}")
        layout: dict[str, Any] = _raw_layout if isinstance(_raw_layout, dict) else {}
        win = tk.Toplevel(self)
        win.title(f"Query results — {source} · {query[:60]}")
        win.transient(self)
        w, h = layout.get("w"), layout.get("h")
        if (isinstance(w, int) and 200 <= w <= 4000
                and isinstance(h, int) and 150 <= h <= 3000):
            win.geometry(f"{w}x{h}")          # remembered size per source
        frm = ttk.Frame(win, padding=8)
        frm.pack(fill="both", expand=True)
        info = ttk.Frame(frm)
        info.pack(fill="x")
        ttk.Label(info, text=f"query: {query}", foreground="#333").pack(side="left")
        ttk.Label(info, text=f"{len(rows)} row(s) × {len(cols)} column(s)",
                  foreground="#555").pack(side="right")

        tf = ttk.Frame(frm)
        tf.pack(fill="both", expand=True, pady=(6, 0))
        tree = ttk.Treeview(tf, columns=cols or ["(empty)"],
                            show="headings", height=14)
        raw_colw = layout.get("colw")
        saved_widths = raw_colw if isinstance(raw_colw, dict) else {}
        for name in (cols or ["(empty)"]):
            saved = saved_widths.get(name)
            default_w = max(90, min(300, 60 + 9 * len(name)))
            tree.column(name, width=(saved if isinstance(saved, int)
                                     and 40 <= saved <= 2000 else default_w),
                        anchor="w", stretch=True)
        iids = [tree.insert("", "end", values=tuple(r)) for r in rows]
        sort_state = {"col": None, "reverse": False}

        def sort_by(col_name):
            idx = cols.index(col_name)
            reverse = (sort_state["col"] == idx and not sort_state["reverse"])
            sort_state.update(col=idx, reverse=reverse)
            self._sort_results_tree(tree, iids, rows, idx, reverse=reverse)
            for c in (cols or ["(empty)"]):
                mark = (" ▼" if reverse else " ▲") if c == col_name else ""
                tree.heading(c, text=c + mark)

        for name in (cols or ["(empty)"]):
            def _sort_heading(n: str = name) -> None:
                sort_by(n)
            tree.heading(name, text=name, command=_sort_heading)
        ysb = ttk.Scrollbar(tf, orient="vertical", command=tree.yview)
        xsb = ttk.Scrollbar(tf, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        ysb.grid(row=0, column=1, sticky="ns")
        xsb.grid(row=1, column=0, sticky="ew")
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(0, weight=1)

        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(6, 0))
        ttk.Button(btns, text="Copy JSON",
                   command=lambda: self._copy_query_result(result, "JSON")).pack(side="left")
        ttk.Button(btns, text="Copy TSV",
                   command=lambda: self._copy_query_result(result, "TSV")).pack(
            side="left", padx=4)

        def _save_layout() -> None:
            # remember this source's window size and column widths (best-
            # effort) so the next window for the same source opens the same
            try:
                _g.remember_export_prefs(
                    f"results:{source}",
                    w=win.winfo_width(), h=win.winfo_height(),
                    colw={name: int(tree.column(name, "width"))
                          for name in (cols or ["(empty)"])})
            except tk.TclError:
                pass

        def _close() -> None:
            _save_layout()
            win.destroy()

        # the Close button and the window-manager [X] both go through the
        # WM_DELETE_WINDOW hook, so the layout is saved either way
        win.protocol("WM_DELETE_WINDOW", _close)
        ttk.Button(btns, text="Close", command=_close).pack(side="right")
        self._log(f"query {query} — {len(rows)} row(s), {len(cols)} column(s) ({source})")
        return win
    def _sort_results_tree(self, tree, iids, rows, col: int,
                           reverse: bool = False) -> None:
        """Reorder the query-results tree by one column: cells that parse as
        numbers sort numerically, everything else lexicographically."""
        def key(i: int):
            cells = rows[i]
            v = cells[col] if col < len(cells) else ""
            try:
                return (0, float(v), "")
            except (TypeError, ValueError):
                return (1, 0.0, str(v))
        for i in sorted(range(len(rows)), key=key, reverse=reverse):
            tree.move(iids[i], "", "end")
    def _copy_query_result(self, result, fmt: str = "JSON") -> None:
        """Copy a query result to the clipboard: pretty JSON by default (the
        exact bytes a .json export would hold) or a TSV grid (header + rows,
        tabs/newlines in cells flattened) for pasting into spreadsheets."""
        if fmt.upper() == "TSV":
            cols, rows_t = _g._query_result_rows(result)
            if not cols:
                text = ""
            else:
                def flat(s: str) -> str:
                    return s.replace("\t", " ").replace("\n", " ").replace("\r", " ")
                text = "\n".join(
                    ["\t".join(flat(c) for c in cols)]
                    + ["\t".join(flat(c) for c in r) for r in rows_t]) + "\n"
        else:
            text = _g.export_json_text(result)
        self.clipboard_clear()
        self.clipboard_append(text)
        self._log(f"query result copied to clipboard as {fmt.upper()}")


from idm import gui as _g  # noqa: E402  (late import: gui imports this module)
