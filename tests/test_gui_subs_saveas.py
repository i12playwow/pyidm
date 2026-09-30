from __future__ import annotations

import time

import pytest

gui = pytest.importorskip("idm.gui")
from idm import gui as G

ROWS = [
    {"path": "C:/v/A.mkv", "ok": True, "dest": "C:/v/A.en.srt",
     "language": "en", "provider": "subtitlecat", "size": 135_480, "cues": 1746},
    {"path": "C:/v/B.mp4", "ok": True, "dest": "C:/v/B.en.srt",
     "language": "en", "provider": "opensubtitles", "size": 51_200, "cues": 800},
]


# ------------------------------------------------------------ pure helper
def test_write_history_export_csv(tmp_path, monkeypatch):
    p = tmp_path / "h.json"
    p.write_text(__import__("json").dumps(ROWS), encoding="utf-8")
    monkeypatch.setattr(G, "SUBS_HISTORY_FILE", p)
    out = tmp_path / "exp.csv"
    n = G.write_history_export(out, "csv")
    assert n == 2
    raw = out.read_bytes()                          # no newline translation
    assert b"\r\n" in raw                           # RFC-4180 CRLF, byte-exact
    text = raw.decode("utf-8")
    lines = text.splitlines()
    assert lines[0].startswith("video")             # established lowercase header
    assert any("A.mkv" in ln for ln in lines[1:])


def test_write_history_export_markdown(tmp_path, monkeypatch):
    p = tmp_path / "h.json"
    p.write_text(__import__("json").dumps(ROWS), encoding="utf-8")
    monkeypatch.setattr(G, "SUBS_HISTORY_FILE", p)
    out = tmp_path / "exp.md"
    n = G.write_history_export(out, "markdown")
    assert n == 2
    text = out.read_text(encoding="utf-8")
    assert text.lstrip().startswith("#") or "|" in text


def test_write_history_export_empty_history(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "SUBS_HISTORY_FILE", tmp_path / "missing.json")
    out = tmp_path / "empty.csv"
    n = G.write_history_export(out, "csv")
    assert n == 0
    assert out.read_bytes().count(b"\r\n") >= 1     # header only


# --------------------------------------------------------- dialog flow
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


def test_save_as_dialog_writes_csv(app, tmp_path, monkeypatch):
    import json
    (tmp_path / "subs_history.json").write_text(json.dumps(ROWS), encoding="utf-8")
    out = tmp_path / "saved.csv"
    monkeypatch.setattr(G.filedialog, "asksaveasfilename", lambda **kw: str(out))
    monkeypatch.setattr(G.App, "_open_filter_dialog",
                        lambda self: ("", "", ""))          # all-empty = full export
    app._save_subs_history_file()
    assert out.exists()
    assert "A.mkv" in out.read_text(encoding="utf-8")
    assert "exported to" in app.log_text.get("1.0", "end")


def test_save_as_md_extension_picks_markdown(app, tmp_path, monkeypatch):
    import json
    (tmp_path / "subs_history.json").write_text(json.dumps(ROWS), encoding="utf-8")
    out = tmp_path / "saved.MD"                     # case-insensitive
    monkeypatch.setattr(G.filedialog, "asksaveasfilename", lambda **kw: str(out))
    monkeypatch.setattr(G.App, "_open_filter_dialog",
                        lambda self: ("", "", ""))
    app._save_subs_history_file()
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert "|" in text and "\r\n" not in text       # markdown, not csv


def test_save_as_cancel_is_silent_noop(app, tmp_path, monkeypatch):
    monkeypatch.setattr(G.filedialog, "asksaveasfilename", lambda **kw: "")
    monkeypatch.setattr(G.App, "_open_filter_dialog",
                        lambda self: ("", "", ""))
    before = app.log_text.get("1.0", "end")
    app._save_subs_history_file()
    assert app.log_text.get("1.0", "end") == before  # nothing logged on cancel


def test_save_as_filter_dialog_cancel_aborts(app, tmp_path, monkeypatch):
    import json
    (tmp_path / "subs_history.json").write_text(json.dumps(ROWS), encoding="utf-8")
    monkeypatch.setattr(G.App, "_open_filter_dialog",
                        lambda self: (None, "", ""))        # filter cancelled
    opened = []
    monkeypatch.setattr(G.filedialog, "asksaveasfilename",
                        lambda **kw: opened.append(1) or "")
    before = app.log_text.get("1.0", "end")
    app._save_subs_history_file()
    assert not opened                                   # save dialog never shown
    assert app.log_text.get("1.0", "end") == before


def test_save_as_write_error_logs_warning(app, tmp_path, monkeypatch):
    import json
    (tmp_path / "subs_history.json").write_text(json.dumps(ROWS), encoding="utf-8")
    out = tmp_path / "blocked.csv"
    monkeypatch.setattr(G.filedialog, "asksaveasfilename", lambda **kw: str(out))
    monkeypatch.setattr(G.App, "_open_filter_dialog",
                        lambda self: ("", "", ""))
    calls = []

    def boom(*a, **kw):
        calls.append(1)
        raise OSError("disk full")

    monkeypatch.setattr(G, "write_history_export", boom)
    app._save_subs_history_file()
    assert calls and "export failed" in app.log_text.get("1.0", "end")
