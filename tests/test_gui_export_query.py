"""GUI export dialogs with --query and JSON output — the interactive twins
of the CLI's --json/--query flags. Both dialogs must use the same evaluator
and writers as the CLI, so GUI and scripted exports can never drift."""
from __future__ import annotations

import json
import time

import pytest

gui = pytest.importorskip("idm.gui")
from idm import gui as G
from idm.jq import JqError

ROWS = [
    {"path": "C:/v/A.mkv", "ok": True, "dest": "C:/v/A.en.srt",
     "language": "en", "provider": "subtitlecat", "size": 135_480, "cues": 1746},
    {"path": "C:/v/B.mp4", "ok": True, "dest": "C:/v/B.en.srt",
     "language": "en", "provider": "opensubtitles", "size": 51_200, "cues": 800},
]

STATE = {
    "https://x/a.zip": {"status": "error", "filename": "a.zip", "size": 1024,
                        "updated": 1_700_000_000, "message": "HTTP 403"},
    "https://x/b.bin": {"status": "downloading", "filename": "b.bin",
                        "size": 2048, "updated": 1_700_000_100},
}


# --------------------------------------------------------- pure helpers
def test_apply_export_query_is_the_cli_evaluator(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "downloads").mkdir()
    (tmp_path / "downloads" / "idm.state.json").write_text(
        json.dumps({"version": 1, "downloads": STATE}), encoding="utf-8")
    data = G.download_json_records(G.read_state_records())
    assert G.apply_export_query("[].filename", data) == ["a.zip", "b.bin"]
    assert G.apply_export_query('[.[] | select(.status == "error")] | length',
                                data) == 1
    with pytest.raises(JqError):
        G.apply_export_query("bogus", data)


def test_export_json_text_matches_cli_format():
    text = G.export_json_text([{"a": 1}])
    assert text == json.dumps([{"a": 1}], indent=2, ensure_ascii=True) + "\n"


def test_write_history_export_json(tmp_path, monkeypatch):
    p = tmp_path / "h.json"
    p.write_text(json.dumps(ROWS), encoding="utf-8")
    monkeypatch.setattr(G, "SUBS_HISTORY_FILE", p)
    out = tmp_path / "exp.json"
    n = G.write_history_export(out, "json")
    assert n == 2
    data = json.loads(out.read_text(encoding="utf-8"))
    assert [d["provider"] for d in data] == ["subtitlecat", "opensubtitles"]


# ------------------------------------------------------------- dialog flow
def _make_app_with_retry(retries: int = 3):
    last = None
    for _ in range(retries):
        try:
            return G.App()
        except gui.tk.TclError as e:  # pragma: no cover - environmental flake
            last = e
            time.sleep(1.0)
    raise last


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setattr(G, "should_show_first_run", lambda cfg: False)
    monkeypatch.setattr(G, "SUBS_HISTORY_FILE", tmp_path / "subs_history.json")
    monkeypatch.setattr(G, "EXPORT_PREFS_FILE", tmp_path / "export_prefs.json")
    a = _make_app_with_retry()
    a.update()
    yield a
    a.destroy()


@pytest.fixture(autouse=True)
def _no_shell_open(monkeypatch):
    opened = []
    monkeypatch.setattr(G, "open_file_safe",
                        lambda p: opened.append(str(p)) or True)
    monkeypatch.setattr(G, "open_file_with",
                        lambda p, v: opened.append((str(p), v)) or True)
    return opened


def _seed_history(tmp_path):
    (tmp_path / "subs_history.json").write_text(json.dumps(ROWS),
                                                encoding="utf-8")


def test_history_save_as_query_writes_json(app, tmp_path, monkeypatch):
    _seed_history(tmp_path)
    out = tmp_path / "providers.json"
    monkeypatch.setattr(G.filedialog, "asksaveasfilename", lambda **kw: str(out))
    monkeypatch.setattr(G.App, "_open_filter_dialog",
                        lambda self: ("", "", "", "[].provider"))
    app._save_subs_history_file()
    text = out.read_text(encoding="utf-8")
    assert json.loads(text) == ["subtitlecat", "opensubtitles"]
    log = app.log_text.get("1.0", "end")
    assert "query [].provider" in log and "preview:" in log


def test_history_save_as_query_selects(app, tmp_path, monkeypatch):
    _seed_history(tmp_path)
    out = tmp_path / "names.json"
    monkeypatch.setattr(G.filedialog, "asksaveasfilename", lambda **kw: str(out))
    monkeypatch.setattr(G.App, "_open_filter_dialog",
                        lambda self: ("", "", "",
                                      '.[] | select(.size > 100000) | .provider'))
    app._save_subs_history_file()
    assert json.loads(out.read_text(encoding="utf-8")) == ["subtitlecat"]


def test_history_save_as_bad_query_aborts_cleanly(app, tmp_path, monkeypatch):
    _seed_history(tmp_path)
    out = tmp_path / "never.json"
    monkeypatch.setattr(G.filedialog, "asksaveasfilename", lambda **kw: str(out))
    monkeypatch.setattr(G.App, "_open_filter_dialog",
                        lambda self: ("", "", "", "bogus"))
    app._save_subs_history_file()
    assert not out.exists()                      # nothing written
    assert "export query invalid" in app.log_text.get("1.0", "end")


def test_history_save_as_json_extension_picks_json(app, tmp_path, monkeypatch):
    _seed_history(tmp_path)
    out = tmp_path / "hist.JSON"                 # case-insensitive extension
    monkeypatch.setattr(G.filedialog, "asksaveasfilename", lambda **kw: str(out))
    monkeypatch.setattr(G.App, "_open_filter_dialog",
                        lambda self: ("", "", "", ""))
    app._save_subs_history_file()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert [d["path"] for d in data] == [r["path"] for r in ROWS]


# ------------------------------------------------ downloads-tab Save As
@pytest.fixture
def dl_store(tmp_path, monkeypatch):
    # the GUI reads the state file from its Save-to directory (out_var)
    (tmp_path / "downloads" / "idm.state.json").parent.mkdir(exist_ok=True)
    (tmp_path / "downloads" / "idm.state.json").write_text(
        json.dumps({"version": 1, "downloads": STATE}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)


def test_downloads_save_as_query(app, tmp_path, monkeypatch, dl_store):
    out = tmp_path / "failed.json"
    monkeypatch.setattr(G.filedialog, "asksaveasfilename", lambda **kw: str(out))
    monkeypatch.setattr(G.App, "_open_downloads_export_dialog",
                        lambda self: ("json",
                                      '.[] | select(.status == "error") | .filename'))
    app._save_downloads_file()
    assert json.loads(out.read_text(encoding="utf-8")) == ["a.zip"]
    log = app.log_text.get("1.0", "end")
    assert "download state exported" in log and "query" in log


def test_downloads_save_as_markdown_no_query(app, tmp_path, monkeypatch, dl_store):
    out = tmp_path / "state.md"
    monkeypatch.setattr(G.filedialog, "asksaveasfilename", lambda **kw: str(out))
    monkeypatch.setattr(G.App, "_open_downloads_export_dialog",
                        lambda self: ("markdown", ""))
    app._save_downloads_file()
    text = out.read_text(encoding="utf-8")
    assert "|" in text and "a.zip" in text


def test_downloads_save_as_plain_json(app, tmp_path, monkeypatch, dl_store):
    out = tmp_path / "state.json"
    monkeypatch.setattr(G.filedialog, "asksaveasfilename", lambda **kw: str(out))
    monkeypatch.setattr(G.App, "_open_downloads_export_dialog",
                        lambda self: ("json", ""))
    app._save_downloads_file()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert {d["url"] for d in data} == set(STATE)


def test_downloads_save_as_bad_query_writes_nothing(app, tmp_path, monkeypatch,
                                                    dl_store):
    out = tmp_path / "never.json"
    monkeypatch.setattr(G.filedialog, "asksaveasfilename", lambda **kw: str(out))
    monkeypatch.setattr(G.App, "_open_downloads_export_dialog",
                        lambda self: ("json", "bogus"))
    app._save_downloads_file()
    assert not out.exists()
    assert "export query invalid" in app.log_text.get("1.0", "end")


def test_downloads_save_as_cancel_is_silent(app, tmp_path, monkeypatch, dl_store):
    monkeypatch.setattr(G.App, "_open_downloads_export_dialog",
                        lambda self: None)
    before = app.log_text.get("1.0", "end")
    app._save_downloads_file()
    assert app.log_text.get("1.0", "end") == before


def test_downloads_save_as_button_exists(app):
    # the downloads tab wires a Save As… button to the new dialog/export flow
    assert hasattr(G.App, "_save_downloads_file")
    assert hasattr(G.App, "_open_downloads_export_dialog")
    assert hasattr(G.App, "_write_downloads_export")


# --------------------------------------------------- query results window
def _find_widgets(win, cls):
    """All descendants of `win` (inclusive) that are instances of `cls` —
    the results window nests its Treeview/buttons in inner frames."""
    found = []
    stack = [win]
    while stack:
        w = stack.pop()
        if isinstance(w, cls):
            found.append(w)
        stack.extend(w.winfo_children())
    return found


def test_query_result_rows_shapes():
    # list of dicts -> union of keys in first-seen order, JSON-ish cells
    cols, rows = G._query_result_rows([{"b": 1, "a": None}, {"a": True}])
    assert cols == ["b", "a"]
    assert rows == [["1", "null"], ["null", "true"]]
    # list of scalars and bare scalar -> single value column
    assert G._query_result_rows(["x", 2, 2.5]) == (["value"], [["x"], ["2"], ["2.5"]])
    assert G._query_result_rows(7) == (["value"], [["7"]])
    # dict -> one row with its keys as columns
    assert G._query_result_rows({"ok": False, "n": 0}) == (
        ["ok", "n"], [["false", "0"]])
    # nested values render as compact JSON, like the CLI payload
    cols, rows = G._query_result_rows([{"u": "https://x/a", "m": {"code": 403}}])
    assert rows == [["https://x/a", '{"code": 403}']]


def test_query_dialog_ok_accepts_valid_query(app, tmp_path, dl_store):
    # Run with a valid query closes the dialog and records the query string
    _seed_history(tmp_path)
    dlg, entry, ok, _cancel, _refresh = app._build_query_dialog("history")
    entry.set("[].provider")
    ok()
    assert not dlg.winfo_exists()
    assert app._query_dialog_result == ["[].provider"]


def test_run_query_ok_shows_results(app, dl_store, monkeypatch):
    # the Run Query… flow: whatever the dialog yields is shown in the window
    monkeypatch.setattr(G.App, "_open_query_dialog",
                        lambda self, s: "[].filename")
    shown = []
    monkeypatch.setattr(G.App, "_show_query_results",
                        lambda self, s, q: shown.append((s, q)) or "win")
    assert app.run_query("history") == "win"
    assert shown == [("history", "[].filename")]


def test_run_query_dialog_cancel_returns_none(app, dl_store, monkeypatch):
    # cancelled dialog -> no results window
    monkeypatch.setattr(G.App, "_open_query_dialog", lambda self, s: None)
    opened = []
    monkeypatch.setattr(G.App, "_show_query_results",
                        lambda self, s, q: opened.append(q))
    assert app.run_query("downloads") is None
    assert opened == []


def test_run_query_dialog_bad_query_refuses_to_close(app, dl_store):
    dlg, entry, ok, _cancel, _refresh = app._build_query_dialog("downloads")
    entry.set("bogus")
    ok()                               # Run — must refuse, dialog stays open
    dlg.update()
    assert "query invalid" in app.log_text.get("1.0", "end")
    assert bool(dlg.winfo_exists())
    dlg.destroy()


def test_query_dialog_live_validation_shows_count(app, tmp_path, dl_store):
    _seed_history(tmp_path)
    dlg, entry, ok, _cancel, refresh = app._build_query_dialog("history")
    entry.set("[].provider")
    refresh()                          # the live-validation hook
    dlg.update()
    labels = _find_widgets(dlg, gui.ttk.Label)
    assert any("returns 2 result(s)" in w.cget("text") for w in labels)
    dlg.destroy()


def test_show_query_results_history_window(app, tmp_path, dl_store):
    _seed_history(tmp_path)
    win = app._show_query_results("history", "[].provider")
    tree = _find_widgets(win, gui.ttk.Treeview)[0]
    assert [tree.set(i, "value") for i in tree.get_children()] == (
        ["subtitlecat", "opensubtitles"])
    # log line + one Toplevel per call
    assert "query [].provider" in app.log_text.get("1.0", "end")
    wins = [w for w in app.winfo_children() if isinstance(w, gui.tk.Toplevel)]
    assert len(wins) == 1
    win.destroy()


def test_show_query_results_downloads_dict_rows_and_sort(app, dl_store):
    win = app._show_query_results("downloads", "[.[]]")   # both state rows
    tree = _find_widgets(win, gui.ttk.Treeview)[0]
    cols = list(tree["columns"])
    assert "url" in cols and "filename" in cols
    assert {tree.set(i, "filename") for i in tree.get_children()} == {"a.zip", "b.bin"}
    # click-sort: numeric column (size) sorts 1024 before 2048
    kids = tree.get_children()
    col = cols.index("size")
    rows = [list(tree.set(i).values()) for i in kids]
    app._sort_results_tree(tree, list(kids), rows, col)
    tree.update()
    order = [tree.set(i, "filename") for i in tree.get_children()]
    assert order == ["a.zip", "b.bin"]
    win.destroy()


def test_show_query_results_sort_reverses_and_marks_heading(app, dl_store):
    win = app._show_query_results("downloads", "[].filename")
    tree = _find_widgets(win, gui.ttk.Treeview)[0]
    rows = [list(tree.set(i).values()) for i in tree.get_children()]
    iids = list(tree.get_children())
    app._sort_results_tree(tree, iids, rows, 0)            # a.zip, b.bin
    assert [tree.set(i, "value") for i in tree.get_children()] == ["a.zip", "b.bin"]
    app._sort_results_tree(tree, iids, rows, 0, reverse=True)  # b.bin, a.zip
    assert [tree.set(i, "value") for i in tree.get_children()] == ["b.bin", "a.zip"]
    win.destroy()


def test_show_query_results_bad_query_no_window(app, dl_store):
    assert app._show_query_results("downloads", "bogus") is None
    assert "no results window" in app.log_text.get("1.0", "end")


def test_copy_query_result_json_and_tsv(app, tmp_path, dl_store):
    _seed_history(tmp_path)
    result = G.apply_export_query("[].provider",
                                  G.history_json_entries(G.load_subs_history()))
    app._copy_query_result(result, "JSON")
    assert app.clipboard_get() == json.dumps(
        ["subtitlecat", "opensubtitles"], indent=2, ensure_ascii=True) + "\n"
    app._copy_query_result(result, "TSV")
    assert app.clipboard_get() == "value\nsubtitlecat\nopensubtitles\n"
    # TSV flattens tabs/newlines inside cells so one row stays one line
    app._copy_query_result([{"a": "x\ty\nz"}], "TSV")
    assert app.clipboard_get() == "a\nx y z\n"
    app._copy_query_result(result)          # default fmt is JSON
    assert app.clipboard_get().startswith("[")


def test_query_result_window_copy_buttons_use_clipboard(app, dl_store):
    win = app._show_query_results("downloads", "[].url")
    btns = {w.cget("text"): w for w in _find_widgets(win, gui.ttk.Button)}
    btns["Copy JSON"].invoke()
    j = app.clipboard_get()
    assert json.loads(j) == list(STATE)
    btns["Copy TSV"].invoke()
    lines = app.clipboard_get().splitlines()
    assert lines[0] == "value" and set(lines[1:]) == set(STATE)
    win.destroy()


def test_run_query_buttons_exist(app):
    assert hasattr(G.App, "run_query")
    assert hasattr(G.App, "_show_query_results")
    assert hasattr(G.App, "_open_query_dialog")
    assert hasattr(G.App, "_copy_query_result")


# ------------------------------------- remembered export prefs per dialog
def test_remember_export_prefs_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "EXPORT_PREFS_FILE", tmp_path / "export_prefs.json")
    assert G.load_export_prefs() == {}          # missing file -> defaults
    G.remember_export_prefs("downloads", fmt="json", query="[].url")
    G.remember_export_prefs("downloads", query="[].filename")  # merges
    G.remember_export_prefs("history", provider="opensubtitles")
    assert G.load_export_prefs() == {
        "downloads": {"fmt": "json", "query": "[].filename"},
        "history": {"provider": "opensubtitles"},
    }
    # garbage file -> clean defaults instead of a crash
    G.EXPORT_PREFS_FILE.write_text("not json", encoding="utf-8")
    assert G.load_export_prefs() == {}
    # non-dict slot values are tolerated instead of raising on merge
    G.EXPORT_PREFS_FILE.write_text('{"downloads": "junk"}', encoding="utf-8")
    G.remember_export_prefs("downloads", fmt="csv")
    assert G.load_export_prefs()["downloads"] == {"fmt": "csv"}


def test_downloads_dialog_prefills_and_remembers(app, tmp_path, dl_store,
                                                 monkeypatch):
    # 1st OK with format + query
    dlg, fmt_cb, query_e, ok, _cancel, _r = app._build_downloads_export_dialog()
    assert fmt_cb.get() == "csv" and query_e.get() == ""   # fresh defaults
    fmt_cb.set("markdown")
    query_e.insert(0, "[].filename")
    ok()
    # 2nd dialog ("restart"): both remembered, query still live-validated
    dlg2, fmt_cb2, query_e2, ok2, _cancel2, refresh2 = \
        app._build_downloads_export_dialog()
    assert fmt_cb2.get() == "markdown"
    assert query_e2.get() == "[].filename"
    refresh2()
    labels = _find_widgets(dlg2, gui.ttk.Label)
    assert any("query ok" in w.cget("text") for w in labels)
    ok2()
    assert app._downloads_export_dialog_result[-1] == ("markdown", "[].filename")


def test_downloads_dialog_invalid_pref_is_ignored(app, tmp_path, dl_store,
                                                  monkeypatch):
    G.remember_export_prefs("downloads", fmt="xlsx", query=42)
    dlg, fmt_cb, query_e, ok, _cancel, _r = app._build_downloads_export_dialog()
    assert fmt_cb.get() == "csv"                # bad fmt -> default
    assert query_e.get() == ""                  # non-string query -> empty
    dlg.destroy()


def test_filter_dialog_prefills_and_remembers(app, tmp_path):
    _seed_history(tmp_path)
    dlg, prov_cb, since_e, until_e, query_e, ok, _cancel = \
        app._build_filter_dialog()
    assert prov_cb.get() == "(all)"
    prov_cb.set("subtitlecat")
    since_e.insert(0, "2026-01-01")
    query_e.insert(0, "[].provider")
    ok()
    dlg2, prov_cb2, since_e2, until_e2, query_e2, ok2, _cancel2 = \
        app._build_filter_dialog()
    assert prov_cb2.get() == "subtitlecat"
    assert since_e2.get() == "2026-01-01"
    assert query_e2.get() == "[].provider"
    ok2()
    assert app._filter_dialog_result[-1] == ("subtitlecat", "2026-01-01", "",
                                             "[].provider")


def test_filter_dialog_unknown_pref_provider_falls_back(app, tmp_path):
    G.remember_export_prefs("history", provider="ghostprovider")
    dlg, prov_cb, _since_e, _until_e, _query_e, _ok, _cancel = \
        app._build_filter_dialog()
    assert prov_cb.get() == "(all)"             # provider no longer exists
    dlg.destroy()


def test_run_query_dialog_prefills_and_remembers(app, tmp_path, dl_store):
    dlg, entry, ok, _cancel, _refresh = app._build_query_dialog("downloads")
    assert entry.get() == ""                    # fresh: no remembered query
    entry.set("[].filename")
    ok()
    dlg2, entry2, ok2, _cancel2, refresh2 = app._build_query_dialog("downloads")
    assert entry2.get() == "[].filename"        # remembered for this source
    refresh2()
    dlg3, _entry3, _ok3, _cancel3, _refresh3 = app._build_query_dialog("history")
    assert _entry3.get() == ""                  # other source unaffected
    dlg.destroy()
    dlg2.destroy()
    dlg3.destroy()


def test_prefs_survive_recreated_app(app, tmp_path, monkeypatch):
    """The actual persistence contract: prefs outlive the Tk app because
    they live in EXPORT_PREFS_FILE, not on the app object."""
    G.remember_export_prefs("downloads", fmt="json", query="[].url")
    b = _make_app_with_retry()
    b.update()
    try:
        dlg, fmt_cb, query_e, _ok, _cancel, _r = \
            b._build_downloads_export_dialog()
        assert fmt_cb.get() == "json"
        assert query_e.get() == "[].url"
        dlg.destroy()
    finally:
        b.destroy()


# --------------------------------------- Help ▸ Clear remembered settings
def test_clear_export_prefs_confirm_deletes_file(app, tmp_path, monkeypatch):
    G.remember_export_prefs("downloads", fmt="json", query="[].url")
    assert G.load_export_prefs()                     # something is stored
    monkeypatch.setattr(G.messagebox, "askyesno", lambda *a, **k: True)
    app.clear_export_prefs()
    assert not G.EXPORT_PREFS_FILE.exists()
    assert G.load_export_prefs() == {}               # dialogs start fresh
    assert "remembered export settings cleared" in app.log_text.get("1.0", "end")


def test_clear_export_prefs_cancel_keeps_file(app, monkeypatch):
    G.remember_export_prefs("downloads", fmt="json")
    monkeypatch.setattr(G.messagebox, "askyesno", lambda *a, **k: False)
    before = app.log_text.get("1.0", "end")
    app.clear_export_prefs()
    assert app.log_text.get("1.0", "end") == before   # silent no-op
    assert G.load_export_prefs().get("downloads") == {"fmt": "json"}


def test_clear_export_prefs_without_file_is_fine(app, monkeypatch):
    monkeypatch.setattr(G.messagebox, "askyesno", lambda *a, **k: True)
    app.clear_export_prefs()                          # nothing stored yet
    assert "remembered export settings cleared" in app.log_text.get("1.0", "end")


def test_clear_export_prefs_unlink_failure_warns(app, tmp_path, monkeypatch):
    # a directory can't be unlinked -> OSError path warns instead of raising
    d = tmp_path / "prefsdir"
    d.mkdir()
    monkeypatch.setattr(G, "EXPORT_PREFS_FILE", d)
    monkeypatch.setattr(G.messagebox, "askyesno", lambda *a, **k: True)
    app.clear_export_prefs()
    assert "could not reset export settings" in app.log_text.get("1.0", "end")


def test_help_menu_offers_clear_remembered_settings(app):
    def menu_labels(m):
        out = []
        for i in range(m.index("end") + 1):
            try:
                out.append(m.entrycget(i, "label"))
            except gui.tk.TclError:      # separators have no label
                out.append(None)
        return out

    # the About entry lives in the menubar's Help cascade; other Menus
    # (context menus) are app children too, so search them all
    help_menus = []
    for top in (w for w in app.winfo_children() if isinstance(w, gui.tk.Menu)):
        for i in range(top.index("end") + 1):
            try:
                sub = app.nametowidget(top.entrycget(i, "menu"))
            except gui.tk.TclError:  # not a cascade entry
                continue
            if "About PyIDM…" in menu_labels(sub):
                help_menus.append(sub)
    assert help_menus, "Help submenu not found"
    assert "Clear remembered settings…" in menu_labels(help_menus[0])


def test_dialogs_start_fresh_after_clear(app, monkeypatch):
    G.remember_export_prefs("downloads", fmt="json", query="[].url")
    monkeypatch.setattr(G.messagebox, "askyesno", lambda *a, **k: True)
    app.clear_export_prefs()
    dlg, fmt_cb, query_e, _ok, _cancel, _r = app._build_downloads_export_dialog()
    assert fmt_cb.get() == "csv" and query_e.get() == ""   # defaults again
    dlg.destroy()


# --------------------------------------- recent queries per Run Query… source
def test_remember_recent_query_dedupes_and_caps(monkeypatch, tmp_path):
    monkeypatch.setattr(G, "EXPORT_PREFS_FILE", tmp_path / "export_prefs.json")
    for q in ("a", "b", "c", "a", "d", "e", "f", "g", "h", "i", "j"):
        G.remember_recent_query("downloads", q)
    rec = G._recent_queries_for("downloads")
    assert rec == ["j", "i", "h", "g", "f", "e", "d", "a"]  # newest first,
    # deduplicated ('a' moved to the front on its second run), capped at 8,
    # and the single-query prefill follows the newest run
    assert G.load_export_prefs()["query:downloads"]["query"] == "j"
    assert G._recent_queries_for("history") == []           # per-source


def test_recent_queries_tolerate_mangled_file(monkeypatch, tmp_path):
    monkeypatch.setattr(G, "EXPORT_PREFS_FILE", tmp_path / "export_prefs.json")
    G.EXPORT_PREFS_FILE.write_text(
        '{"query:downloads": {"recent": ["ok", 7, null, ["x"], "fine"]}}',
        encoding="utf-8")
    assert G._recent_queries_for("downloads") == ["ok", "fine"]
    G.EXPORT_PREFS_FILE.write_text('{"query:downloads": "junk"}',
                                   encoding="utf-8")
    assert G._recent_queries_for("downloads") == []


def test_forget_recent_query_drops_one_entry(monkeypatch, tmp_path):
    monkeypatch.setattr(G, "EXPORT_PREFS_FILE", tmp_path / "export_prefs.json")
    G.remember_recent_query("downloads", "[].filename")
    G.remember_recent_query("downloads", "[].url")
    assert G.forget_recent_query("downloads", "[].filename") is True
    assert G._recent_queries_for("downloads") == ["[].url"]
    # the prefill follows the list's new newest entry
    assert G.load_export_prefs()["query:downloads"]["query"] == "[].url"
    # removing the last one empties both list and prefill
    assert G.forget_recent_query("downloads", "[].url") is True
    assert G._recent_queries_for("downloads") == []
    assert G.load_export_prefs()["query:downloads"]["query"] == ""
    # unknown queries (or another source's) report False and change nothing
    assert G.forget_recent_query("downloads", "ghost") is False
    assert G.forget_recent_query("history", "[].url") is False


def test_forget_recent_query_keeps_live_prefill(monkeypatch, tmp_path):
    # when the slot's prefill points at a query that is still remembered,
    # forgetting an older entry must not move the prefill
    monkeypatch.setattr(G, "EXPORT_PREFS_FILE", tmp_path / "export_prefs.json")
    G.remember_recent_query("downloads", "[].url")
    G.remember_recent_query("downloads", "[].filename")   # prefill (newest)
    G.forget_recent_query("downloads", "[].url")          # drop the older one
    slot = G.load_export_prefs()["query:downloads"]
    assert slot["recent"] == ["[].filename"]
    assert slot["query"] == "[].filename"


def test_query_dialog_dropdown_offers_recent(app, tmp_path, dl_store,
                                             monkeypatch):
    G.remember_recent_query("downloads", "[].filename")
    G.remember_recent_query("downloads", "[].url")
    dlg, cb, ok, _cancel, _refresh = app._build_query_dialog("downloads")
    assert list(cb["values"]) == ["[].url", "[].filename"]  # newest first
    assert cb.get() == "[].url"                    # prefilled with the newest
    # picking an older entry from the dropdown fills and live-validates it
    cb.set("[].filename")
    cb.event_generate("<<ComboboxSelected>>")
    dlg.update()
    labels = _find_widgets(dlg, gui.ttk.Label)
    assert any("returns 2 result(s)" in w.cget("text") for w in labels)
    ok()
    dlg2, cb2, _ok2, _cancel2, _refresh2 = app._build_query_dialog("downloads")
    assert list(cb2["values"]) == ["[].filename", "[].url"]  # re-ranked
    assert cb2.get() == "[].filename"
    dlg.destroy()
    dlg2.destroy()


def test_query_dialog_empty_history_stays_plain_entry(app, dl_store):
    dlg, cb, _ok, _cancel, _refresh = app._build_query_dialog("downloads")
    assert list(cb["values"]) == []
    assert cb.get() == ""
    dlg.destroy()


def test_invalid_run_not_recorded_as_recent(app, tmp_path, dl_store):
    # Run refuses bad queries — they must never land in the recent list
    dlg, cb, ok, _cancel, _refresh = app._build_query_dialog("downloads")
    cb.set("bogus")
    ok()                                        # refused, dialog stays open
    assert G._recent_queries_for("downloads") == []
    dlg.destroy()
    G.remember_recent_query("downloads", "[].url")   # valid run records fine
    assert G._recent_queries_for("downloads") == ["[].url"]


# ------------------------------------- recent-query chips (one-click reuse)
def _query_chip_buttons(dlg):
    """The recent-query chip buttons of a Run Query… dialog (identified by
    their jq-ish text, unlike the fixed Run/Cancel buttons), in pack order
    (= newest first; the generic widget walk does not preserve order)."""
    chips = [b for b in _find_widgets(dlg, gui.ttk.Button)
             if str(b.cget("text")) not in ("Run", "Cancel")]
    if not chips:
        return {}
    parent = chips[0].master                 # the chip strip frame
    return {w.cget("text"): w for w in parent.winfo_children()}  # pack order


def test_query_chips_render_newest_first_and_fill(app, dl_store):
    G.remember_recent_query("downloads", "[].url")
    G.remember_recent_query("downloads", "[].filename")
    dlg, cb, ok, _cancel, refresh = app._build_query_dialog("downloads")
    chips = _query_chip_buttons(dlg)
    assert list(chips) == ["[].filename", "[].url"]     # newest first
    # clear the field, then one click refills and live-validates
    cb.set("")
    refresh()
    list(chips.values())[0].invoke()
    assert cb.get() == "[].filename"
    labels = _find_widgets(dlg, gui.ttk.Label)
    assert any("returns 2 result(s)" in w.cget("text") for w in labels)
    dlg.destroy()


def test_query_chip_click_never_runs(app, dl_store):
    G.remember_recent_query("downloads", "[].filename")
    dlg, _cb, _ok, _cancel, _refresh = app._build_query_dialog("downloads")
    chip = list(_query_chip_buttons(dlg).values())[0]
    chip.invoke()
    dlg.update()
    assert bool(dlg.winfo_exists())                    # still open
    assert app._query_dialog_result == []              # nothing run/recorded
    dlg.destroy()


def test_query_chips_truncate_long_queries(app, dl_store):
    long_q = ".downloads[] | select(.status == \"error\") | .url, .filename"
    G.remember_recent_query("downloads", long_q)
    dlg, _cb, _ok, _cancel, _refresh = app._build_query_dialog("downloads")
    chips = _query_chip_buttons(dlg)
    assert list(chips) == [long_q[:25] + "…"]          # truncated label…
    list(chips.values())[0].invoke()                   # …full query applied
    assert _find_widgets(dlg, gui.ttk.Combobox)[0].get() == long_q
    dlg.destroy()


def test_query_chips_absent_when_no_history(app, dl_store):
    dlg, _cb, _ok, _cancel, _refresh = app._build_query_dialog("downloads")
    assert _query_chip_buttons(dlg) == {}
    dlg.destroy()


def test_query_chips_are_per_source(app, tmp_path, dl_store):
    G.remember_recent_query("downloads", "[].url")
    dlg, cb, _ok, _cancel, _refresh = app._build_query_dialog("history")
    assert _query_chip_buttons(dlg) == {}              # other source isolated
    assert cb.get() == ""
    dlg.destroy()


def test_query_chip_middle_click_forgets_one(app, dl_store):
    G.remember_recent_query("downloads", "[].url")
    G.remember_recent_query("downloads", "[].filename")
    dlg, cb, _ok, _cancel, _refresh = app._build_query_dialog("downloads")
    chips = _query_chip_buttons(dlg)
    assert list(chips) == ["[].filename", "[].url"]
    # both removal bindings exist (Button-2 is Windows' middle click) and the
    # _remove handle points at the same closure, like the tooltip handle
    chip = chips["[].url"]
    assert chip.bind("<Button-2>") and chip.bind("<Button-3>")
    chip._remove()                                     # the middle-click path
    assert G._recent_queries_for("downloads") == ["[].filename"]
    # the chips strip and the dropdown both re-render without the entry
    assert list(_query_chip_buttons(dlg)) == ["[].filename"]
    assert list(cb["values"]) == ["[].filename"]
    # the removal never runs the query or closes the dialog
    assert app._query_dialog_result == []
    assert bool(dlg.winfo_exists())
    dlg.destroy()


def test_query_chip_middle_click_last_entry_clears_field(app, dl_store):
    G.remember_recent_query("downloads", "[].url")
    dlg, cb, _ok, _cancel, _refresh = app._build_query_dialog("downloads")
    assert cb.get() == "[].url"                        # prefilled prefill
    list(_query_chip_buttons(dlg).values())[0]._remove()
    assert _query_chip_buttons(dlg) == {}              # chips strip empties
    assert list(cb["values"]) == [] and cb.get() == ""  # dropdown + field too
    assert G._recent_queries_for("downloads") == []    # and the store
    dlg.destroy()


def test_query_chip_middle_click_keeps_other_prefill(app, dl_store):
    # the field was pointing at 'filename'; forgetting 'url' must not move it
    G.remember_recent_query("downloads", "[].url")
    G.remember_recent_query("downloads", "[].filename")
    dlg, cb, _ok, _cancel, _refresh = app._build_query_dialog("downloads")
    cb.set("[].filename")
    chips = _query_chip_buttons(dlg)
    chips["[].url"]._remove()
    assert cb.get() == "[].filename"
    dlg.destroy()


# -------------------------------- main-table column widths (downloads tab)
def test_main_tables_save_colw_on_close(app, dl_store, monkeypatch):
    # drag-simulate: set widths directly, then close the app through its
    # WM_DELETE_WINDOW hook (the route the window-manager [X] takes)
    app.tree.column("url", width=444)
    app.tree.column("speed", width=77)
    app.subs_tree.column("video", width=333)
    app.subs_tree.column("cues", width=88)
    app.update()
    shown_url = app.tree.column("url", "width")   # stretch may adjust
    shown_speed = app.tree.column("speed", "width")
    shown_video = app.subs_tree.column("video", "width")
    shown_cues = app.subs_tree.column("cues", "width")
    assert str(app.protocol("WM_DELETE_WINDOW")).endswith("_on_close")
    # Patch destroy only for this call. A bare monkeypatch.setattr would be
    # undone AFTER the fixture teardown — which calls this same no-op — so
    # the whole Tk interpreter would leak and, once anything grabs on it
    # (Tk counts every interpreter as its own application), poison every
    # later grab_set() in the run.
    with monkeypatch.context() as m:
        m.setattr(app, "destroy", lambda: None)  # keep the app alive for asserts
        app._on_close()                          # the [X] route
    dl = G.load_export_prefs()["table:downloads"]["colw"]
    hist = G.load_export_prefs()["table:history"]["colw"]
    assert dl["url"] == shown_url and dl["speed"] == shown_speed
    assert hist["video"] == shown_video and hist["cues"] == shown_cues
    assert set(dl) == {"url", "file", "status", "progress", "size", "speed"}
    assert set(hist) == {"video", "lang", "provider", "size", "cues"}


def test_main_tables_restore_colw_on_rebuild(monkeypatch, tmp_path,
                                             dl_store):
    # a fresh App (as after a GUI restart) reopens the saved widths
    G.remember_export_prefs(
        "table:downloads", colw={"url": 555, "speed": 66, "file": 99999})
    G.remember_export_prefs("table:history", colw={"video": 444, "lang": 0})
    app2 = _make_app_with_retry()
    app2.update()
    try:
        assert app2.tree.column("url", "width") == 555
        assert app2.tree.column("speed", "width") == 66
        # missing/out-of-range entries keep the build defaults
        assert app2.tree.column("status", "width") == 95
        assert app2.tree.column("file", "width") == 190     # 99999 clamped out
        assert app2.subs_tree.column("video", "width") == 444
        assert app2.subs_tree.column("lang", "width") == 44  # 0 out of range
    finally:
        app2.destroy()


def test_main_table_colw_survives_mangled_prefs(monkeypatch, tmp_path,
                                                dl_store):
    # a broken file must not stop the app: defaults everywhere
    G.EXPORT_PREFS_FILE.write_text('{"table:downloads": "junk"}',
                                   encoding="utf-8")
    app2 = _make_app_with_retry()
    try:
        # build defaults (queried pre-layout: stretch redistribution would
        # change the shown widths, which this test deliberately avoids)
        assert app2.tree.column("url", "width") == 340
        assert app2.subs_tree.column("video", "width") == 260
        # saving still works afterwards and repairs the slot
        app2.tree.column("url", width=404)
        assert app2._save_table_layouts() is None           # best-effort hook
        assert G.load_export_prefs()["table:downloads"]["colw"]["url"] == 404
    finally:
        app2.destroy()


def test_save_table_colw_on_destroyed_tree_returns_false(app):
    dlg = gui.tk.Toplevel(app)
    try:
        tree = gui.ttk.Treeview(dlg, columns=("a", "b"), show="headings")
        tree.column("a", width=123)
        tree.column("b", width=50)
        assert G.save_table_colw(tree, ("a", "b"), "table:x") is True
        assert G.load_export_prefs()["table:x"]["colw"] == {"a": 123, "b": 50}
    finally:
        dlg.destroy()
    # after teardown the query raises TclError -> False, nothing written
    assert G.save_table_colw(tree, ("a", "b"), "table:y") is False
    assert "table:y" not in G.load_export_prefs()


# ---------------------------------- results-window layout per source
def test_results_window_saves_layout_on_close(app, dl_store):
    win = app._show_query_results("downloads", "[].url")
    tree = _find_widgets(win, gui.ttk.Treeview)[0]
    win.geometry("700x450")
    tree.column("value", width=250)
    win.update()
    shown_w = win.winfo_width()                  # post-layout values
    shown_col = tree.column("value", "width")    # (stretch redistributes)
    btns = {w.cget("text"): w for w in _find_widgets(win, gui.ttk.Button)}
    btns["Close"].invoke()                       # saved through the hook
    slot = G.load_export_prefs()["results:downloads"]
    assert (slot["w"], slot["h"]) == (shown_w, 450)
    assert slot["colw"] == {"value": shown_col}


def test_results_window_wm_close_saves_layout(app, dl_store):
    # the window-manager [X] goes through the same WM_DELETE_WINDOW hook
    win = app._show_query_results("downloads", "[].url")
    tree = _find_widgets(win, gui.ttk.Treeview)[0]
    tree.column("value", width=180)
    win.update()
    shown_col = tree.column("value", "width")
    win.tk.call(win.protocol("WM_DELETE_WINDOW"))   # invoke the [X] handler
    slot = G.load_export_prefs()["results:downloads"]
    assert slot["colw"] == {"value": shown_col}
    assert not win.winfo_exists()


def test_results_window_restores_saved_layout(app, dl_store):
    G.remember_export_prefs("results:downloads", w=820, h=460,
                            colw={"value": 260})
    win = app._show_query_results("downloads", "[].url")
    tree = _find_widgets(win, gui.ttk.Treeview)[0]
    assert tree.column("value", "width") == 260  # stored width applied...
    win.update()                                 # ...before layout stretch
    assert win.winfo_width() == 820 and win.winfo_height() == 460
    win.destroy()


def test_results_window_ignores_bad_saved_layout(app, dl_store):
    G.remember_export_prefs("results:downloads", w=9999, h=50,
                            colw={"value": "wide"})
    win = app._show_query_results("downloads", "[].url")
    tree = _find_widgets(win, gui.ttk.Treeview)[0]
    assert tree.column("value", "width") == 105  # default for 5-char column
    win.update()
    assert win.winfo_width() != 9999              # out-of-range size ignored
    win.destroy()


def test_results_layout_is_per_source(app, tmp_path, dl_store):
    _seed_history(tmp_path)
    G.remember_export_prefs("results:downloads", colw={"value": 250})
    win = app._show_query_results("history", "[].provider")
    tree = _find_widgets(win, gui.ttk.Treeview)[0]
    assert tree.column("value", "width") == 105  # downloads width not leaked
    win.destroy()


# ------------------------------------------- export presets in the dialogs
def _editable_combo(dlg):
    """The one editable combobox in a dialog (the preset picker) — the
    format/provider comboboxes are readonly."""
    cbs = [c for c in _find_widgets(dlg, gui.ttk.Combobox)
           if c.cget("state") != "readonly"]
    return cbs[0]


def test_downloads_dialog_preset_row(app, dl_store):
    dlg, fmt_cb, query_e, _ok, _cancel, _r = app._build_downloads_export_dialog()
    preset_cb = _editable_combo(dlg)
    btns = {b.cget("text"): b for b in _find_widgets(dlg, gui.ttk.Button)}
    # save the current fields as a preset
    fmt_cb.set("json")
    query_e.delete(0, "end")
    query_e.insert(0, "[].filename")
    preset_cb.set("byfile")
    btns["Save"].invoke()
    assert G.load_presets("downloads")["byfile"] == {
        "fmt": "json", "query": "[].filename", "viewer": ""}
    assert "export preset 'byfile' saved" in app.log_text.get("1.0", "end")
    # change fields, then Apply restores the preset's
    fmt_cb.set("csv")
    query_e.delete(0, "end")
    query_e.insert(0, "length")
    preset_cb.set("byfile")
    btns["Apply"].invoke()
    assert fmt_cb.get() == "json" and query_e.get() == "[].filename"
    # Delete removes it and refreshes the dropdown
    btns["Delete"].invoke()
    assert "byfile" not in G.load_presets("downloads")
    assert list(preset_cb["values"]) == []
    assert "export preset 'byfile' deleted" in app.log_text.get("1.0", "end")
    # a blank name saves nothing
    preset_cb.set("")
    btns["Save"].invoke()
    assert G.load_presets("downloads") == {}
    dlg.destroy()


def test_filter_dialog_preset_row(app, tmp_path):
    # rows need ts: the preset's since=2026-01-01 filter drops ts-less rows
    (tmp_path / "subs_history.json").write_text(json.dumps(
        [dict(r, ts=1_800_000_000) for r in ROWS]), encoding="utf-8")
    dlg, prov_cb, since_e, until_e, query_e, _ok, _cancel = \
        app._build_filter_dialog()
    preset_cb = _editable_combo(dlg)
    btns = {b.cget("text"): b for b in _find_widgets(dlg, gui.ttk.Button)}
    prov_cb.set("subtitlecat")
    since_e.delete(0, "end")
    since_e.insert(0, "2026-01-01")
    query_e.delete(0, "end")
    query_e.insert(0, "[].provider")
    preset_cb.set("cat2026")
    btns["Save"].invoke()
    assert G.load_presets("history")["cat2026"] == {
        "provider": "subtitlecat", "since": "2026-01-01", "until": "",
        "query": "[].provider", "viewer": ""}
    # reset the fields, then Apply restores all four
    prov_cb.set("(all)")
    since_e.delete(0, "end")
    query_e.delete(0, "end")
    preset_cb.set("cat2026")
    btns["Apply"].invoke()
    assert prov_cb.get() == "subtitlecat"
    assert since_e.get() == "2026-01-01"
    assert query_e.get() == "[].provider"
    labels = _find_widgets(dlg, gui.ttk.Label)
    assert any("1 matching row(s)" in w.cget("text") for w in labels)
    btns["Delete"].invoke()
    assert "cat2026" not in G.load_presets("history")
    dlg.destroy()


def test_dialog_preset_dropdown_lists_saved_presets(app, tmp_path, dl_store):
    G.save_preset("downloads", "zeta", fmt="csv")
    G.save_preset("downloads", "alpha", fmt="json")
    dlg, _fmt_cb, _query_e, _ok, _cancel, _r = app._build_downloads_export_dialog()
    assert list(_editable_combo(dlg)["values"]) == ["alpha", "zeta"]
    dlg.destroy()
    _seed_history(tmp_path)
    dlg2, _prov_cb, _since_e, _until_e, _query_e, _ok2, _cancel2 = \
        app._build_filter_dialog()
    assert list(_editable_combo(dlg2)["values"]) == []   # per-dialog-kind
    dlg2.destroy()


def test_downloads_dialog_last_preset_preselected_and_applied(app, dl_store):
    # a preset saved from the CLI becomes the dialog's one-click default
    G.save_preset("downloads", "byfile", fmt="json", query="[].filename")
    G.remember_export_prefs("downloads", fmt="csv", preset="byfile")
    dlg, fmt_cb, query_e, _ok, _cancel, _r = app._build_downloads_export_dialog()
    preset_cb = _editable_combo(dlg)
    assert preset_cb.get() == "byfile"            # preselected...
    assert fmt_cb.get() == "json"                 # ...and already applied
    assert query_e.get() == "[].filename"
    labels = _find_widgets(dlg, gui.ttk.Label)
    assert any("query ok" in w.cget("text") for w in labels)
    # OK records the preset as the dialog's new default
    _ok()
    assert G.load_export_prefs()["downloads"]["preset"] == "byfile"


def test_filter_dialog_last_preset_preselected_and_applied(app, tmp_path):
    (tmp_path / "subs_history.json").write_text(json.dumps(
        [dict(r, ts=1_800_000_000) for r in ROWS]), encoding="utf-8")
    G.save_preset("history", "cat2026", provider="subtitlecat",
                  since="2026-01-01", until="", query="[].provider")
    G.remember_export_prefs("history", preset="cat2026")
    dlg, prov_cb, since_e, until_e, query_e, _ok, _cancel = \
        app._build_filter_dialog()
    preset_cb = _editable_combo(dlg)
    assert preset_cb.get() == "cat2026"
    assert prov_cb.get() == "subtitlecat"         # applied on open
    assert since_e.get() == "2026-01-01"
    assert query_e.get() == "[].provider"
    labels = _find_widgets(dlg, gui.ttk.Label)
    # the preset's provider filter keeps only the subtitlecat row
    assert any("1 matching row(s)" in w.cget("text") for w in labels)
    dlg.destroy()


# ------------------------------------- preset example chips (one-click apply)
def _chip_buttons(dlg):
    """Chip buttons sit in the frame that also holds the Apply/Save/Delete
    row's chips container; identify them by their ' · ' label separator."""
    return {b.cget("text"): b for b in _find_widgets(dlg, gui.ttk.Button)
            if " · " in str(b.cget("text"))}


def test_preset_chips_render_and_apply(app, dl_store):
    G.save_preset("downloads", "errors", fmt="json",
                  query='[.[] | select(.status == "error")] | length')
    G.save_preset("downloads", "names", fmt="csv", query="[].filename")
    dlg, fmt_cb, query_e, _ok, _cancel, _r = app._build_downloads_export_dialog()
    chips = _chip_buttons(dlg)
    assert len(chips) == 2
    # chip labels are recognizable: name + a hint of the settings
    assert any(c.startswith("errors · ") for c in chips)
    assert any(c.startswith("names · ") for c in chips)
    # one click applies: chip invocation fills the dialog fields
    chips["names · fmt · query"].invoke() if "names · fmt · query" in chips \
        else [b for t, b in chips.items() if t.startswith("names")][0].invoke()
    assert fmt_cb.get() == "csv" and query_e.get() == "[].filename"
    # the preset field shows which preset the chip applied
    assert _editable_combo(dlg).get() == "names"
    dlg.destroy()


def test_preset_chips_track_save_and_delete(app, dl_store):
    dlg, fmt_cb, query_e, _ok, _cancel, _r = app._build_downloads_export_dialog()
    preset_cb = _editable_combo(dlg)
    btns = {b.cget("text"): b for b in _find_widgets(dlg, gui.ttk.Button)}
    # no presets yet: the chips row says so instead of offering chips
    assert any("no presets saved" in w.cget("text")
               for w in _find_widgets(dlg, gui.ttk.Label))
    fmt_cb.set("json")
    query_e.delete(0, "end")
    query_e.insert(0, "[].url")
    preset_cb.set("urls")
    btns["Save"].invoke()
    chips = _chip_buttons(dlg)                 # a chip appeared
    assert len(chips) == 1 and list(chips)[0].startswith("urls · ")
    # delete removes the chip and restores the empty hint
    preset_cb.set("urls")
    btns["Delete"].invoke()
    assert _chip_buttons(dlg) == {}
    assert any("no presets saved" in w.cget("text")
               for w in _find_widgets(dlg, gui.ttk.Label))
    dlg.destroy()


def test_preset_chip_click_never_oks_dialog(app, dl_store):
    G.save_preset("downloads", "byfile", fmt="json", query="[].filename")
    dlg, _fmt_cb, _query_e, _ok, _cancel, _r = \
        app._build_downloads_export_dialog()
    chip = list(_chip_buttons(dlg).values())[0]
    chip.invoke()
    dlg.update()
    assert bool(dlg.winfo_exists())            # still open
    assert app._downloads_export_dialog_result == []   # nothing recorded
    dlg.destroy()


def test_preset_chip_middle_click_deletes_it(app, dl_store):
    G.save_preset("downloads", "errors", fmt="json",
                  query='[.[] | select(.status == "error")] | length')
    G.save_preset("downloads", "names", fmt="csv", query="[].filename")
    dlg, fmt_cb, query_e, _ok, _cancel, _r = app._build_downloads_export_dialog()
    chips = _chip_buttons(dlg)
    assert len(chips) == 2
    # the chip exposes its removal closure like its tooltip handle, and both
    # middle-click bindings exist (Button-2 is Windows' middle click)
    chip = [b for t, b in chips.items() if t.startswith("names")][0]
    assert chip.bind("<Button-2>") and chip.bind("<Button-3>")
    errors_preset = G.load_presets("downloads")["errors"]
    chip._remove()                             # the middle-click path
    # the store lost exactly that preset; the other chip is still there
    assert G.load_presets("downloads") == {"errors": errors_preset}
    assert list(_chip_buttons(dlg)) == [t for t in chips
                                        if not t.startswith("names")]
    # the removal is logged like the Delete button's
    assert "export preset 'names' deleted (downloads)" in \
        app.log_text.get("1.0", "end")
    # it never OKs the dialog
    assert app._downloads_export_dialog_result == []
    assert bool(dlg.winfo_exists())
    dlg.destroy()


def test_preset_chip_middle_click_clears_selected_field(app, dl_store):
    # middle-clicking the chip of the currently selected preset also clears
    # the preset field — a removed preset must not stay preselected
    G.save_preset("downloads", "byfile", fmt="json", query="[].filename")
    dlg, _fmt_cb, _query_e, _ok, _cancel, _r = \
        app._build_downloads_export_dialog()
    preset_cb = _editable_combo(dlg)
    preset_cb.set("byfile")
    assert "byfile" in preset_cb["values"]
    [b for b in _find_widgets(dlg, gui.ttk.Button)
     if str(b.cget("text")).startswith("byfile")][0]._remove()
    assert G.load_presets("downloads") == {}
    assert _chip_buttons(dlg) == {}            # back to the empty hint
    assert preset_cb.get() == ""               # field cleared...
    assert "byfile" not in preset_cb["values"]  # ...and out of the dropdown
    assert any("no presets saved" in w.cget("text")
               for w in _find_widgets(dlg, gui.ttk.Label))
    dlg.destroy()


def test_filter_dialog_chip_middle_click_deletes_history_preset(
        app, tmp_path):
    _seed_history(tmp_path)
    G.save_preset("history", "cat", provider="subtitlecat",
                  since="2026-01-01", until="", query="[].provider")
    dlg, *_ = app._build_filter_dialog()
    chip = list(_chip_buttons(dlg).values())[0]
    chip._remove()
    assert G.load_presets("history") == {}
    assert _chip_buttons(dlg) == {}
    assert "export preset 'cat' deleted (history)" in \
        app.log_text.get("1.0", "end")
    dlg.destroy()


def test_filter_dialog_chip_applies_provider_and_query(app, tmp_path):
    (tmp_path / "subs_history.json").write_text(json.dumps(
        [dict(r, ts=1_800_000_000) for r in ROWS]), encoding="utf-8")
    G.save_preset("history", "cat", provider="subtitlecat",
                  since="2026-01-01", until="", query="[].provider")
    dlg, prov_cb, since_e, until_e, query_e, _ok, _cancel = \
        app._build_filter_dialog()
    chip = list(_chip_buttons(dlg).values())[0]
    chip.invoke()
    assert prov_cb.get() == "subtitlecat"
    assert since_e.get() == "2026-01-01"
    assert query_e.get() == "[].provider"
    dlg.destroy()


def _run_shortcut(dlg, seq):
    """Fire the apply-last-preset shortcut like a real keypress: the Tk
    binding script exists (asserted), and the Python closure it wraps is
    exposed on the dialog as apply_last_preset for deterministic testing."""
    assert dlg.bind(seq), f"no binding for {seq}"
    return dlg.apply_last_preset()


def test_shortcut_applies_last_preset(app, dl_store):
    G.save_preset("downloads", "byfile", fmt="json", query="[].filename")
    G.remember_export_prefs("downloads", preset="byfile", fmt="csv")
    dlg, fmt_cb, query_e, _ok, _cancel, _r = app._build_downloads_export_dialog()
    assert dlg.bind("<Control-p>") and dlg.bind("<F5>")   # bindings exist
    # a different dialog state: fields not matching the preset yet
    fmt_cb.set("markdown")
    assert _run_shortcut(dlg, "<Control-p>") == "break"
    assert fmt_cb.get() == "json" and query_e.get() == "[].filename"
    assert _editable_combo(dlg).get() == "byfile"
    assert app._downloads_export_dialog_result == []   # no OK happened
    assert bool(dlg.winfo_exists())
    assert _run_shortcut(dlg, "<F5>") == "break"       # alias shortcut
    assert fmt_cb.get() == "json"
    dlg.destroy()


def test_shortcut_is_per_dialog_kind(app, tmp_path, dl_store):
    # downloads' shortcut must not apply history's last preset
    _seed_history(tmp_path)
    G.save_preset("history", "cat", provider="subtitlecat",
                  since="2026-01-01", until="", query="[].provider")
    G.remember_export_prefs("history", preset="cat")
    G.remember_export_prefs("downloads", fmt="csv", preset="")
    dlg, fmt_cb, query_e, _ok, _cancel, _r = app._build_downloads_export_dialog()
    before = (fmt_cb.get(), query_e.get())
    _run_shortcut(dlg, "<Control-p>")
    assert (fmt_cb.get(), query_e.get()) == before   # nothing applied
    assert _editable_combo(dlg).get() == ""
    dlg.destroy()


def test_shortcut_without_last_preset_is_noop(app, dl_store):
    G.save_preset("downloads", "byfile", fmt="json", query="[].filename")
    # no remembered preset (fresh dialog) -> Ctrl+P does nothing
    dlg, fmt_cb, query_e, _ok, _cancel, _r = app._build_downloads_export_dialog()
    before = (fmt_cb.get(), query_e.get())
    _run_shortcut(dlg, "<Control-p>")
    assert (fmt_cb.get(), query_e.get()) == before
    assert _editable_combo(dlg).get() == ""
    dlg.destroy()


def test_shortcut_ignores_stale_last_preset(app, dl_store):
    # the remembered preset was deleted after the last OK
    G.remember_export_prefs("downloads", preset="ghost", fmt="csv")
    dlg, fmt_cb, query_e, _ok, _cancel, _r = app._build_downloads_export_dialog()
    _run_shortcut(dlg, "<Control-p>")
    assert fmt_cb.get() == "csv" and query_e.get() == ""
    assert _editable_combo(dlg).get() == ""
    dlg.destroy()


def test_shortcut_hint_shown_when_last_preset_exists(app, dl_store):
    G.save_preset("downloads", "byfile", fmt="json", query="[].filename")
    G.remember_export_prefs("downloads", preset="byfile")
    dlg, *_ = app._build_downloads_export_dialog()
    assert any("Ctrl+P/F5 applies the last preset" in w.cget("text")
               for w in _find_widgets(dlg, gui.ttk.Label))
    dlg.destroy()
    # without a remembered preset there is no hint
    G.remember_export_prefs("downloads", preset="")
    dlg2, *_ = app._build_downloads_export_dialog()
    assert not any("Ctrl+P/F5" in w.cget("text")
                   for w in _find_widgets(dlg2, gui.ttk.Label))
    dlg2.destroy()


# ------------------------------------------------- chip tooltips
def test_preset_tip_text_lists_every_setting():
    tip = G._preset_tip_text(
        "errors", {"fmt": "json", "query": "[].url", "out": "", "viewer": ""})
    assert tip.startswith("preset 'errors' applies:")
    assert "fmt = json" in tip and "query = [].url" in tip
    # empty settings are not listed at all
    assert "out" not in tip and "viewer" not in tip
    assert "(no settings)" in G._preset_tip_text("minimal", {"fmt": ""})
    # pinned out/viewer show unexpanded, exactly as stored
    tip2 = G._preset_tip_text(
        "p", {"fmt": "csv", "out": "reports/{date}.csv", "viewer": "code"})
    assert "out = reports/{date}.csv" in tip2 and "viewer = code" in tip2


def test_preset_chip_tooltip_holds_full_query(app, dl_store):
    full_q = '[.[] | select(.status == "error")] | length'
    G.save_preset("downloads", "errors", fmt="json", query=full_q)
    dlg, _fmt_cb, _query_e, _ok, _cancel, _r = app._build_downloads_export_dialog()
    chip = [b for b in _find_widgets(dlg, gui.ttk.Button)
            if str(b.cget("text")).startswith("errors")][0]
    tip = chip._tooltip._text            # the tooltip _Tooltip was attached
    assert full_q in tip                 # the chip label abbreviates; tip doesn't
    assert "fmt = json" in tip
    dlg.destroy()


def test_recent_query_chip_tooltip_holds_full_query(app, dl_store):
    long_q = '.downloads[] | select(.status == "error") | .url, .filename'
    G.remember_recent_query("downloads", long_q)
    dlg, _cb, _ok, _cancel, _refresh = app._build_query_dialog("downloads")
    chip = list(_query_chip_buttons(dlg).values())[0]
    assert long_q[:25] in chip.cget("text")          # label truncated…
    tip = chip._tooltip._text
    assert long_q in tip                              # …tooltip holds it all
    assert tip.startswith("apply this query:")
    dlg.destroy()


def test_tooltip_show_and_hide_lifecycle(app, dl_store):
    G.save_preset("downloads", "errors", fmt="json", query="[].url")
    dlg, *_ = app._build_downloads_export_dialog()
    chip = [b for b in _find_widgets(dlg, gui.ttk.Button)
            if str(b.cget("text")).startswith("errors")][0]
    tt = chip._tooltip
    tt._show()                                       # what after(500ms) runs
    assert tt._tip is not None and bool(tt._tip.winfo_exists())
    tt._hide()
    assert tt._tip is None
    # show -> leave event hides (the <Leave> binding path)
    tt._show()
    tt._hide()
    assert tt._tip is None
    dlg.destroy()


def test_preset_apply_syncs_pinned_viewer(app, dl_store):
    # a preset's pinned viewer lands in the shared Viewer entry
    G.save_preset("downloads", "rep", fmt="json", query="[].filename",
                  viewer="code")
    dlg, fmt_cb, query_e, _ok, _cancel, _r = app._build_downloads_export_dialog()
    preset_cb = _editable_combo(dlg)
    preset_cb.set("rep")
    btns = {b.cget("text"): b for b in _find_widgets(dlg, gui.ttk.Button)}
    btns["Apply"].invoke()
    assert app.saveas_viewer_var.get() == "code"
    dlg.destroy()


def test_stale_last_preset_is_ignored(app, dl_store):
    # the remembered preset was deleted (or renamed): no preselect, no apply
    G.remember_export_prefs("downloads", preset="ghost", query="length")
    dlg, fmt_cb, query_e, _ok, _cancel, _r = app._build_downloads_export_dialog()
    assert _editable_combo(dlg).get() == ""
    assert fmt_cb.get() == "csv"                  # plain remembered values
    assert query_e.get() == "length"
    dlg.destroy()


def test_preset_apply_button_does_not_ok(app, dl_store):
    # Apply runs the same closure the preset field's Return binding does:
    # it fills the fields but never OKs (records/destroys) the dialog
    G.save_preset("downloads", "byfile", fmt="json", query="[].filename")
    dlg, fmt_cb, query_e, _ok, _cancel, _r = app._build_downloads_export_dialog()
    preset_cb = _editable_combo(dlg)
    preset_cb.set("byfile")          # user picks/types the preset name
    btns = {b.cget("text"): b for b in _find_widgets(dlg, gui.ttk.Button)}
    btns["Apply"].invoke()
    dlg.update()
    assert fmt_cb.get() == "json" and query_e.get() == "[].filename"
    assert bool(dlg.winfo_exists())               # still open
    assert app._downloads_export_dialog_result == []   # nothing recorded
    dlg.destroy()


# --------------------------------------------------- Tools ▸ report presets
def _menu_labels(m):
    out = []
    for i in range(m.index("end") + 1):
        try:
            out.append(m.entrycget(i, "label"))
        except gui.tk.TclError:      # separators have no label
            out.append(None)
    return out


def test_tools_menu_lists_report_presets(app):
    # one entry per stats/providers preset; other kinds never appear
    G.save_preset("stats", "nightly", query=".downloads", out="stats.json")
    G.save_preset("providers", "weekly", out="providers.json")
    G.save_preset("downloads", "rep", fmt="json")
    app._build_menu()
    labels = _menu_labels(app._tools_report_menu)
    assert "Run stats preset: nightly" in labels
    assert "Run providers preset: weekly" in labels
    assert not any("downloads" in (l or "") for l in labels)
    assert "Rebuild this menu after adding presets" in labels


def test_tools_menu_empty_store_shows_hint(app):
    assert any("No report presets saved" in (l or "")
               for l in _menu_labels(app._tools_report_menu))


def test_run_report_preset_stats_writes_pinned_out(app, tmp_path, dl_store):
    out = tmp_path / "reports" / "stats.json"     # missing parents are created
    G.save_preset("stats", "nightly", query=".downloads.pending", out=str(out))
    app.run_report_preset("stats", "nightly")
    assert json.loads(out.read_text(encoding="utf-8")) == 2
    log = app.log_text.get("1.0", "end")
    assert f"exported stats report -> {out}" in log
    assert "query .downloads.pending" in log and "preset nightly" in log


def test_run_report_preset_stats_full_payload_and_date_expansion(
        app, tmp_path, dl_store):
    G.save_preset("stats", "daily", out=str(tmp_path / "{kind}-{date}.json"))
    app.run_report_preset("stats", "daily")
    stamp = time.strftime("%Y-%m-%d")
    data = json.loads((tmp_path / f"stats-{stamp}.json").read_text(
        encoding="utf-8"))
    # the same payload 'idm stats --json' builds (both stores summarized)
    assert data["downloads"]["by_status"] == {"error": 1, "downloading": 1}
    assert data["downloads"]["pending"] == 2
    assert data["subtitles"]["entries"] == 0
    assert "(preset daily)" in app.log_text.get("1.0", "end")


def test_run_report_preset_unknown_name_logs_error(app):
    app.run_report_preset("stats", "ghost")
    assert "unknown report preset: ghost" in app.log_text.get("1.0", "end")


def test_run_report_preset_bad_query_exports_nothing(app, tmp_path, dl_store):
    out = tmp_path / "never.json"
    G.save_preset("stats", "broken", query="bogus", out=str(out))
    app.run_report_preset("stats", "broken")
    assert not out.exists()
    assert "report query invalid" in app.log_text.get("1.0", "end")


def test_run_report_preset_without_out_hints_pin(app, dl_store):
    G.save_preset("stats", "bare")
    app.run_report_preset("stats", "bare")
    log = app.log_text.get("1.0", "end")
    assert "has no out" in log and "idm presets add stats" in log


def test_run_report_preset_opens_viewer(app, tmp_path, dl_store,
                                        _no_shell_open):
    out = tmp_path / "v.json"
    G.save_preset("stats", "viewed", out=str(out), viewer="code")
    app.run_report_preset("stats", "viewed")
    assert _no_shell_open == [(str(out), "code")]


def _wait_for_log(app, needle, seconds=5.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if needle in app.log_text.get("1.0", "end"):
            return True
        app.update()
        time.sleep(0.02)
    return False


def test_run_report_preset_providers_threaded_to_log(app, tmp_path,
                                                      monkeypatch):
    # providers checks are network work: they run on a worker thread and
    # land in the log via the event loop, like the providers tab
    from idm.health import ProviderHealth
    monkeypatch.setattr(G, "run_checks", lambda *a, **k: [
        ProviderHealth("subtitlecat", "SubtitleCat", "ok", "reachable")])
    out = tmp_path / "providers.json"
    G.save_preset("providers", "weekly", query=".providers[].name",
                  out=str(out))
    app.run_report_preset("providers", "weekly")
    assert _wait_for_log(app, "exported providers report")
    assert json.loads(out.read_text(encoding="utf-8")) == ["subtitlecat"]
    assert not app._prov_running   # guard released when the run finishes


def test_run_report_preset_providers_failure_is_logged(app, tmp_path,
                                                       monkeypatch):
    def boom(*a, **k):
        raise OSError("network unreachable")
    monkeypatch.setattr(G, "run_checks", boom)
    out = tmp_path / "providers.json"
    G.save_preset("providers", "weekly", out=str(out))
    app.run_report_preset("providers", "weekly")
    assert _wait_for_log(app, "network unreachable")
    assert not out.exists()                    # nothing written on failure
    assert not app._prov_running


def test_report_done_event_dispatches(app):
    # the event loop routes 'report_done' tuples to the finisher (both the
    # success and failure shapes)
    app.events.put(("report_done", "stats", "n", "", "", "", None,
                    RuntimeError("kaboom")))
    assert _wait_for_log(app, "stats preset 'n' failed: kaboom")


# ------------------------------------------ preset chips vs OK/Cancel rows
def test_preset_chips_do_not_overlap_dialog_buttons(app, dl_store):
    # regression: the chip row and the OK/Cancel row used to share one grid
    # row, so the right-aligned chips painted under the buttons
    G.save_preset("downloads", "failed", fmt="json",
                  query='[.[] | select(.status == "error")] | length')
    dlg, *_ = app._build_downloads_export_dialog()
    dlg.update()
    chips = [b for b in _find_widgets(dlg, gui.ttk.Button)
             if "·" in str(b.cget("text"))]
    btns = [b for b in _find_widgets(dlg, gui.ttk.Button)
            if str(b.cget("text")) in ("OK", "Cancel")]
    assert chips and btns
    top_btn = min(b.winfo_rooty() for b in btns)
    assert all(c.winfo_rooty() + c.winfo_height() <= top_btn
               for c in chips), "a chip row overlaps the OK/Cancel row"
    dlg.destroy()


def test_history_preset_chips_do_not_overlap_dialog_buttons(app, dl_store,
                                                            tmp_path):
    _seed_history(tmp_path)
    G.save_preset("history", "cat2026", provider="subtitlecat",
                  since="2026-01-01")
    dlg, *_ = app._build_filter_dialog()
    dlg.update()
    chips = [b for b in _find_widgets(dlg, gui.ttk.Button)
             if "·" in str(b.cget("text"))]
    btns = [b for b in _find_widgets(dlg, gui.ttk.Button)
            if str(b.cget("text")) in ("OK", "Cancel")]
    assert chips and btns
    top_btn = min(b.winfo_rooty() for b in btns)
    assert all(c.winfo_rooty() + c.winfo_height() <= top_btn
               for c in chips), "a chip row overlaps the OK/Cancel row"
    dlg.destroy()


# ------------------------------------------------- browser collector (Tools)
def test_tools_menu_collector_start_stop_cycle(app):
    # Tools ▸ Start/Stop browser collector: flips the menu entry, binds the
    # port, and stops cleanly (the app-close path calls the same stop)
    assert "Start browser collector" in _menu_labels(app._tools_menu)
    app._start_collector()
    assert app._collector is not None
    assert app._collector_port > 0
    assert any((l or "").startswith("Stop browser collector")
               for l in _menu_labels(app._tools_menu))
    app._stop_collector()
    assert app._collector is None
    assert "Start browser collector" in _menu_labels(app._tools_menu)


def test_collector_post_lands_in_gui_log(app, monkeypatch, tmp_path):
    # the real loop: start the collector from the Tools menu, POST like the
    # extension does, drive the event loop, see the capture in the log
    import urllib.request

    import idm.collect as C
    monkeypatch.setattr(C, "COLLECT_QUEUE_FILE", tmp_path / "q.json")
    app._start_collector()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{app._collector_port}/collect", method="POST",
            data=json.dumps({"urls": ["https://x/bridge.zip"],
                             "page_url": "https://page/"}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as r:
            data = json.loads(r.read())
        assert data["added"] == 1
        deadline = time.time() + 5
        while time.time() < deadline:
            app.update()
            if "queued 1 link(s)" in app.log_text.get("1.0", "end"):
                break
            time.sleep(0.02)
        assert "queued 1 link(s)" in app.log_text.get("1.0", "end")
        assert C.queue_pending(tmp_path / "q.json")[0]["url"] == \
            "https://x/bridge.zip"
    finally:
        app._stop_collector()


def test_show_and_download_collector_queue(app, monkeypatch, tmp_path):
    import idm.collect as C
    monkeypatch.setattr(C, "COLLECT_QUEUE_FILE", tmp_path / "q.json")
    C.queue_add(["https://x/one.zip", ("https://x/two.zip", "renamed.zip")],
                page_url="https://page/", path=tmp_path / "q.json")
    app._show_collector_queue()                 # logs every pending entry
    log = app.log_text.get("1.0", "end")
    assert "pending: https://x/one.zip (from https://page/)" in log
    assert "2 pending" in log
    # Download queue now: hands the drained jobs to the engine on a thread
    started = {}
    monkeypatch.setattr(C, "start_downloads",
                        lambda jobs, out_dir, cfg, log=None:
                        started.update(jobs=jobs, out_dir=out_dir) or {"ok": 2})
    app._download_collector_queue()
    deadline = time.time() + 5
    while time.time() < deadline and "jobs" not in started:
        app.update()
        time.sleep(0.02)
    assert started["jobs"] == [("https://x/one.zip", None),
                               ("https://x/two.zip", "renamed.zip")]
    assert C.load_queue(tmp_path / "q.json")["pending"] == []
    assert "collector queue: starting 2 download(s)" in \
        app.log_text.get("1.0", "end")


def test_download_queue_now_empty_logs_hint(app, monkeypatch, tmp_path):
    import idm.collect as C
    monkeypatch.setattr(C, "COLLECT_QUEUE_FILE", tmp_path / "q.json")
    app._download_collector_queue()
    assert "collector queue is empty" in app.log_text.get("1.0", "end")