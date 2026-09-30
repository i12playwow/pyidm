from __future__ import annotations

import json
import time

import pytest

gui = pytest.importorskip("idm.gui")
from idm import gui as G

NOW = time.time()
DAY = 86400

ROWS = [
    {"path": "C:/v/A.mkv", "ok": True, "dest": "C:/v/A.en.srt", "language": "en",
     "provider": "subtitlecat", "size": 135_480, "cues": 1746, "ts": NOW - 100},
    {"path": "C:/v/B.mp4", "ok": True, "dest": "C:/v/B.en.srt", "language": "en",
     "provider": "opensubtitles", "size": 51_200, "cues": 800, "ts": NOW - 3 * DAY},
    {"path": "C:/v/C.mp4", "ok": True, "dest": "C:/v/C.en.srt", "language": "de",
     "provider": "subtitlecat", "size": 900, "cues": 10, "ts": NOW - 40 * DAY},
]


@pytest.fixture
def hist_file(tmp_path, monkeypatch):
    p = tmp_path / "h.json"
    p.write_text(json.dumps(ROWS), encoding="utf-8")
    monkeypatch.setattr(G, "SUBS_HISTORY_FILE", p)
    return p


# ------------------------------------------------------------ pure helpers
def test_parse_date_input_variants():
    assert G.parse_date_input("") is None
    assert G.parse_date_input("   ") is None
    start = G.parse_date_input("2026-09-01")
    end = G.parse_date_input("2026-09-01", end=True)
    assert start < end
    with_t = G.parse_date_input("2026-09-01 12:30")
    assert start < with_t < end
    with pytest.raises(ValueError):
        G.parse_date_input("not-a-date")


def test_filter_history_provider_and_dates(hist_file):
    assert len(G.filter_history(json.loads(hist_file.read_text()))) == 3
    assert [r["path"] for r in G.filter_history(
        json.loads(hist_file.read_text()), provider="SubtitleCat")] == [
        "C:/v/A.mkv", "C:/v/C.mp4"]                 # case-insensitive
    # last 7 days: A and B only (window derived from NOW so the test is
    # wall-clock independent — it must not break at a date rollover)
    from datetime import datetime as _dt
    since_d = _dt.fromtimestamp(NOW - 3 * DAY).strftime("%Y-%m-%d")
    until_d = _dt.fromtimestamp(NOW - 100).strftime("%Y-%m-%d")
    got = G.filter_history(json.loads(hist_file.read_text()),
                           since=since_d, until=until_d)
    assert [r["path"] for r in got] == ["C:/v/A.mkv", "C:/v/B.mp4"]
    # entries without ts are dropped when a date bound is active
    rows = [dict(ROWS[0], ts=None)] + [ROWS[1]]
    assert len(G.filter_history(rows, since="2020-01-01")) == 1


def test_filter_history_inclusive_bounds(hist_file):
    rows = [{"path": "x", "ok": True, "provider": "p", "ts": 1_700_000_000}]
    iso = time.strftime("%Y-%m-%d", time.localtime(1_700_000_000))
    assert len(G.filter_history(rows, since=iso)) == 1     # boundary included
    assert len(G.filter_history(rows, until=iso)) == 1


def test_parse_export_filters_rejects_inverted_range():
    with pytest.raises(ValueError):
        G.parse_export_filters("", "2026-09-10", "2026-09-01")
    assert G.parse_export_filters(" cat ", " ", "") == ("cat", "", "")


def test_history_providers_sorted(hist_file):
    assert G.history_providers(json.loads(hist_file.read_text())) == [
        "opensubtitles", "subtitlecat"]


# --------------------------------------------------------- filtered export
def test_write_history_export_provider_filter(hist_file, tmp_path):
    out = tmp_path / "cat.csv"
    n = G.write_history_export(out, "csv", provider="subtitlecat")
    assert n == 2
    text = out.read_text(encoding="utf-8")
    assert "A.mkv" in text and "C.mp4" in text and "B.mp4" not in text


def test_write_history_export_provider_no_match(hist_file, tmp_path):
    out = tmp_path / "none.csv"
    n = G.write_history_export(out, "csv", provider="yifysubtitles")
    assert n == 0
    assert out.exists()                                     # header only


def test_write_history_export_date_window(hist_file, tmp_path):
    from datetime import datetime as _dt
    since_d = _dt.fromtimestamp(NOW - 3 * DAY).strftime("%Y-%m-%d")
    until_d = _dt.fromtimestamp(NOW - 100).strftime("%Y-%m-%d")
    out = tmp_path / "week.csv"
    n = G.write_history_export(out, "markdown", since=since_d, until=until_d)
    assert n == 2
    text = out.read_text(encoding="utf-8")
    assert "A.mkv" in text and "B.mp4" in text and "C.mp4" not in text


# ------------------------------------------------------------ dialog flow
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
def app(monkeypatch, tmp_path, hist_file):
    monkeypatch.setattr(G, "should_show_first_run", lambda cfg: False)
    a = _make_app_with_retry()
    a.update()
    yield a
    a.destroy()


@pytest.fixture(autouse=True)
def _no_shell_open(monkeypatch):
    """Save As may auto-open the export; tests must never touch the shell."""
    opened = []
    monkeypatch.setattr(G, "open_file_safe", lambda p: opened.append(str(p)) or True)
    monkeypatch.setattr(G, "open_file_with", lambda p, v: opened.append((str(p), v)) or True)
    return opened


def test_filter_dialog_ok_returns_values(app, monkeypatch, tmp_path):
    """Drive the real filter dialog headlessly: _build_filter_dialog was
    split out of _open_filter_dialog precisely so tests can run it without
    wait_window. Build it, set the fields through the returned widgets, fire
    the returned ok() closure, and pin the (provider, since, until, query)
    contract. The cancel path is pinned by
    test_filter_dialog_cancel_returns_none."""
    monkeypatch.setattr(G, "EXPORT_PREFS_FILE", tmp_path / "prefs.json")
    dlg, prov_cb, since_e, until_e, query_e, ok, _cancel = \
        app._build_filter_dialog()
    prov_cb.set("subtitlecat")          # a provider present in the history
    since_e.insert(0, "2020-01-01")
    ok()
    assert app._filter_dialog_result == [
        ("subtitlecat", "2020-01-01", "", "")]


def test_filter_dialog_cancel_returns_none(app, monkeypatch):
    # The only honest headless way: patch the dialog construction so
    # wait_window returns immediately with no OK having fired.
    monkeypatch.setattr(G.tk, "Toplevel", lambda *a, **k: type("D", (), {
        "title": lambda s, *a: None, "transient": lambda s, *a: None,
        "grab_set": lambda s: None, "resizable": lambda s, *a: None,
        "destroy": lambda s: None, "protocol": lambda s, *a: None,
        "bind": lambda s, *a: None, "wait_window": lambda s: None,
        "grid": lambda s, *a, **k: None})())
    monkeypatch.setattr(G.ttk, "Label", lambda *a, **k: type("L", (), {
        "grid": lambda s, *a, **k: None, "config": lambda s, **k: None,
        "pack": lambda s, *a, **k: None})())
    monkeypatch.setattr(G.ttk, "Combobox", lambda *a, **k: type("C", (), {
        "grid": lambda s, *a, **k: None, "current": lambda s, i: None,
        "get": lambda s: "(all)", "set": lambda s, v: None,
        "bind": lambda s, *a, **k: None})())
    monkeypatch.setattr(G.ttk, "Entry", lambda *a, **k: type("E", (), {
        "grid": lambda s, *a, **k: None, "get": lambda s: "",
        "bind": lambda s, *a, **k: None})())
    monkeypatch.setattr(G.ttk, "Button", lambda *a, **k: type("B", (), {
        "pack": lambda s, *a, **k: None})())
    monkeypatch.setattr(G.ttk, "Frame", lambda *a, **k: type("F", (), {
        "grid": lambda s, *a, **k: None,
        "winfo_children": lambda s: [],
        "pack": lambda s, *a, **k: None})())
    assert app._open_filter_dialog() == (None, "", "")


def test_save_as_logs_filter_scope(app, monkeypatch, tmp_path):
    out = tmp_path / "scoped.csv"
    monkeypatch.setattr(G.filedialog, "asksaveasfilename", lambda **kw: str(out))
    monkeypatch.setattr(G.App, "_open_filter_dialog",
                        lambda self: ("subtitlecat", "2026-09-16", ""))
    app._save_subs_history_file()
    log = app.log_text.get("1.0", "end")
    assert "exported to" in log
    assert "provider=subtitlecat" in log and "since 2026-09-16" in log
    text = out.read_text(encoding="utf-8")
    # subtitlecat matches A and C, but C (40d old) falls outside 'since'
    assert "A.mkv" in text and "C.mp4" not in text and "B.mp4" not in text
