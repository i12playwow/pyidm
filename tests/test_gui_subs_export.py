from __future__ import annotations

import tkinter as tk

import pytest

gui = pytest.importorskip("idm.gui")
from idm import gui as G

ROWS = [
    ("Movie A.mkv", "en", "subtitlecat", "135.5 KB", "1746"),
    ("Movie C.mp4", "en", "opensubtitles", "51.2 KB", "800"),
]


# ------------------------------------------------------------ pure renderers
def test_csv_header_and_rows():
    out = G.history_to_csv(ROWS)
    lines = out.split("\r\n")
    assert lines[0] == "video,lang,provider,size,cues"
    assert lines[1] == "Movie A.mkv,en,subtitlecat,135.5 KB,1746"
    assert lines[2] == "Movie C.mp4,en,opensubtitles,51.2 KB,800"
    assert out.endswith("\r\n")  # RFC-4180 final terminator


def test_csv_escapes_commas_quotes_and_newlines():
    rows = [('Say, "hello"\nworld', "en", "prov", "1 KB", "5")]
    out = G.history_to_csv(rows)
    assert '"Say, ""hello""\nworld"' in out  # quoted, doubled quotes


def test_csv_blanks_emdash_placeholders():
    rows = [("v.mkv", "vi", "?", "—", "—")]
    out = G.history_to_csv(rows)
    assert "v.mkv,vi,?,,\r\n" in out


def test_markdown_header_separator_and_rows():
    out = G.history_to_markdown(ROWS)
    lines = out.split("\n")
    assert lines[0] == "| video | lang | provider | size | cues |"
    assert lines[1] == "|---|---|---|---|---|"
    assert lines[2] == "| Movie A.mkv | en | subtitlecat | 135.5 KB | 1746 |"
    assert len(lines) == 4


def test_markdown_escapes_pipes():
    out = G.history_to_markdown([("a|b.mkv", "en", "p", "1 KB", "5")])
    assert "| a\\|b.mkv |" in out


def test_renderers_on_empty_rows():
    assert G.history_to_csv([]).startswith("video,lang,provider,size,cues")
    assert G.history_to_markdown([]) == "| video | lang | provider | size | cues |\n|---|---|---|---|---|"


# ------------------------------------------------ real-App flow (headless)
def _make_app_with_retry(retries: int = 3):
    import time
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


def test_export_buttons_exist(app):
    texts = []

    def walk(w):
        for c in w.winfo_children():
            try:
                texts.append(c.cget("text"))
            except tk.TclError:  # widgets without a text option
                pass
            walk(c)

    walk(app)
    assert "Copy CSV" in texts and "Copy MD" in texts and "Clear" in texts


def test_copy_csv_puts_table_on_clipboard(app, monkeypatch):
    monkeypatch.setattr(G, "load_subs_history", lambda path=None: [
        {"path": "C:/v/Movie A.mkv", "ok": True, "dest": "C:/v/Movie A.en.srt",
         "language": "en", "provider": "subtitlecat", "size": 135480, "cues": 1746},
    ])
    app._copy_subs_history("csv")
    got = app.clipboard_get()
    assert got == ("video,lang,provider,size,cues\r\n"
                   "Movie A.mkv,en,subtitlecat,135.5 KB,1746\r\n")


def test_copy_md_puts_table_on_clipboard(app, monkeypatch):
    monkeypatch.setattr(G, "load_subs_history", lambda path=None: [
        {"path": "C:/v/Movie C.mp4", "ok": True, "dest": "C:/v/Movie C.en.srt",
         "language": "en", "provider": "opensubtitles", "size": 51200, "cues": 800},
    ])
    app._copy_subs_history("markdown")
    got = app.clipboard_get()
    assert got.startswith("| video | lang | provider | size | cues |")
    assert "| Movie C.mp4 | en | opensubtitles | 51.2 KB | 800 |" in got


def test_copy_empty_history_still_logs(app):
    app._copy_subs_history("csv")
    assert app.clipboard_get().startswith("video,lang,provider,size,cues")
