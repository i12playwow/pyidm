from __future__ import annotations

import time

import pytest

gui = pytest.importorskip("idm.gui")
from idm import gui as G
from idm.health import ProviderHealth

RESULTS = [
    ProviderHealth("opensubtitles", "OpenSubtitles", "warn",
                   "no API key configured", "https://api.opensubtitles.com",
                   ["get a free key at opensubtitles.com"]),
    ProviderHealth("yifysubtitles", "YIFYSubtitles", "ok",
                   "movie pages reachable", "https://yifysubtitles.ch"),
    ProviderHealth("subtitlecat", "SubtitleCat", "down",
                   "DNS failed (blocked)", "https://www.subtitlecat.com",
                   ["host unreachable from this network"]),
]


# ------------------------------------------------------------ pure helpers
def test_provider_rows_formatting():
    rows = G.provider_rows(RESULTS)
    assert rows[0] == ("OpenSubtitles", "WARN", "no API key configured",
                       "https://api.opensubtitles.com")
    assert rows[1][1] == "OK"
    assert rows[2][1] == "DOWN"


def test_provider_hints_labeled():
    hints = G.provider_hints(RESULTS)
    assert hints == [
        "hint (OpenSubtitles): get a free key at opensubtitles.com",
        "hint (SubtitleCat): host unreachable from this network",
    ]
    assert G.provider_hints([RESULTS[1]]) == []


def test_unknown_status_mark_falls_back():
    weird = [ProviderHealth("x", "X", "odd", "d")]
    assert G.provider_rows(weird)[0][1] == "ODD"


# ------------------------------------------- real-App render path (headless)
@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(G, "should_show_first_run", lambda cfg: False)
    a = _make_app_with_retry()
    a.update()
    yield a
    a.destroy()


def _make_app_with_retry(retries: int = 3):
    """Creating many Tk roots in one suite run can hit transient 'couldn't read
    ttk/*.tcl' errors on Windows (file locks / AV scans). Retry a few times."""
    last = None
    for _ in range(retries):
        try:
            return G.App()
        except gui.tk.TclError as e:  # pragma: no cover - environmental flake
            last = e
            time.sleep(1.0)
    raise last


def test_tabs_present(app):
    tabs = [app.notebook.tab(t, "text") for t in app.notebook.tabs()]
    assert tabs == ["Downloads", "Providers"]


def test_on_providers_done_renders_rows_and_hints(app):
    app._on_providers_done(RESULTS)
    rows = [app.prov_tree.set(i) for i in app.prov_tree.get_children()]
    assert [r["provider"] for r in rows] == ["OpenSubtitles", "YIFYSubtitles", "SubtitleCat"]
    assert [r["status"] for r in rows] == ["WARN", "OK", "DOWN"]
    assert "DNS failed (blocked)" in rows[2]["detail"]
    hints = app.prov_hints.get("1.0", "end").strip()
    assert "hint (OpenSubtitles): get a free key" in hints
    assert "hint (SubtitleCat): host unreachable" in hints
    assert app.prov_status_var.get() == "1 OK · 1 warn · 1 down"


def test_on_providers_done_no_hints_message(app):
    ok_only = [ProviderHealth("yifysubtitles", "YIFYSubtitles", "ok", "fine")]
    app._on_providers_done(ok_only)
    assert "No hints" in app.prov_hints.get("1.0", "end")
    assert app.prov_status_var.get() == "1 OK · 0 warn · 0 down"


def test_on_providers_done_exception_path(app):
    app._on_providers_done(ValueError("boom"))
    assert "check failed" in app.prov_status_var.get()
    assert str(app.prov_btn["state"]) == "normal"     # button usable again


def test_run_provider_check_thread_wiring(app, monkeypatch):
    monkeypatch.setattr(G, "run_checks", lambda cfg, deep=False: RESULTS)
    app.prov_deep_var.set(True)
    app.run_provider_check()
    assert str(app.prov_btn["state"]) == "disabled"
    assert app.prov_status_var.get() == "checking…"
    # wait for the worker thread's event, then let the poll loop render it
    deadline = time.time() + 10
    while time.time() < deadline and not app.prov_tree.get_children():
        app.update()
        time.sleep(0.05)
    rows = [app.prov_tree.set(i) for i in app.prov_tree.get_children()]
    assert [r["status"] for r in rows] == ["WARN", "OK", "DOWN"]
    assert str(app.prov_btn["state"]) == "normal"
    assert app._prov_running is False
