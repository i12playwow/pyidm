from __future__ import annotations

import json
import time

import pytest

gui = pytest.importorskip("idm.gui")
from idm import gui as G

ROWS = [
    {"path": "C:/v/A.mkv", "ok": True, "dest": "C:/v/A.en.srt", "language": "en",
     "provider": "subtitlecat", "size": 135_480, "cues": 1746},
]


def _log(app) -> str:
    return app.log_text.get("1.0", "end")


def _make_app_with_retry(tries: int = 5):
    """App() creation intermittently raises TclError on this machine
    (same workaround as test_gui_subs_export_filters)."""
    last = None
    for _ in range(tries):
        try:
            return G.App()
        except Exception as e:
            last = e
            time.sleep(1.0)
    raise last


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setattr(G, "should_show_first_run", lambda cfg: False)
    monkeypatch.setattr(G, "SUBS_HISTORY_FILE", tmp_path / "subs_history.json")
    (tmp_path / "subs_history.json").write_text(json.dumps(ROWS), encoding="utf-8")
    a = _make_app_with_retry()
    a.update()
    yield a
    a.destroy()


@pytest.fixture(autouse=True)
def opens(monkeypatch):
    """Stub the shell-open helpers; return the list they record into."""
    rec = []
    monkeypatch.setattr(G, "open_file_safe", lambda p: rec.append(("safe", str(p))) or True)
    monkeypatch.setattr(G, "open_file_with", lambda p, v: rec.append(("with", str(p), v)) or True)
    return rec


@pytest.fixture
def save_dialog(monkeypatch, tmp_path):
    """Make the Save As dialog return a .md path in tmp_path."""
    out = tmp_path / "exported.md"
    monkeypatch.setattr(G.filedialog, "asksaveasfilename", lambda **kw: str(out))
    return out


@pytest.fixture
def filters_ok(monkeypatch):
    monkeypatch.setattr(G.App, "_open_filter_dialog", lambda self: ("", "", ""))
    return True


def test_open_after_export_default_app(app, save_dialog, filters_ok, opens):
    app.saveas_open_var.set(True)
    app.saveas_viewer_var.set("")
    app._save_subs_history_file()
    assert save_dialog.exists()
    assert opens == [("safe", str(save_dialog))]
    assert f"opened {save_dialog} in the default app" in _log(app)


def test_viewer_entry_routes_to_open_file_with(app, save_dialog, filters_ok, opens):
    app.saveas_viewer_var.set("notepad")
    app._save_subs_history_file()
    assert opens == [("with", str(save_dialog), "notepad")]
    assert "in notepad" in _log(app)


def test_unchecking_open_box_is_quiet(app, save_dialog, filters_ok, opens):
    app.saveas_open_var.set(False)
    app.saveas_viewer_var.set("notepad")   # viewer set but open disabled
    app._save_subs_history_file()
    assert opens == []
    assert "opened" not in _log(app)


def test_open_failure_warns_export_still_logged(app, save_dialog, filters_ok, monkeypatch):
    monkeypatch.setattr(G, "open_file_with", lambda p, v: False)
    monkeypatch.setattr(G, "open_file_safe", lambda p: False)
    app.saveas_viewer_var.set("ghost")
    app._save_subs_history_file()
    log = _log(app)
    assert "exported to" in log                      # export line intact
    assert "could not open" in log and "ghost" in log and "succeeded" in log


def test_write_failure_skips_open_entirely(app, save_dialog, filters_ok, monkeypatch, opens):
    def boom(*a, **kw):
        raise OSError("disk full")
    monkeypatch.setattr(G, "write_history_export", boom)
    app._save_subs_history_file()
    assert opens == []                               # nothing to open
    assert "export failed" in _log(app)


def test_cancel_at_filter_never_opens(app, opens, monkeypatch):
    monkeypatch.setattr(G.App, "_open_filter_dialog", lambda self: (None, "", ""))
    before = _log(app)
    app._save_subs_history_file()
    assert opens == [] and _log(app) == before


def test_cancel_at_save_dialog_never_opens(app, opens, monkeypatch, filters_ok):
    monkeypatch.setattr(G.filedialog, "asksaveasfilename", lambda **kw: "")
    app._save_subs_history_file()
    assert opens == []


def test_default_open_var_is_checked(app):
    assert app.saveas_open_var.get() is True


def test_viewer_var_defaults_empty(app):
    assert app.saveas_viewer_var.get() == ""
