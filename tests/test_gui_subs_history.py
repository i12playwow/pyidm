from __future__ import annotations

import pytest

gui = pytest.importorskip("idm.gui")
from idm import gui as G

RESULTS = [
    {"path": "C:/videos/Movie A.mkv", "ok": True, "dest": "C:/videos/Movie A.en.srt",
     "language": "en", "provider": "subtitlecat", "size": 135480, "cues": 1746},
    {"path": "C:/videos/Movie B.mkv", "ok": False, "message": "all providers failed"},
    {"path": "C:/videos/Movie C.mp4", "ok": True, "dest": "C:/videos/Movie C.en.srt",
     "language": "en", "provider": "opensubtitles", "size": 51200, "cues": 800},
]


# ------------------------------------------------------------ pure helpers
def test_subtitle_rows_skip_failures_and_format():
    rows = G.subtitle_rows(RESULTS)
    assert rows == [
        ("Movie A.mkv", "en", "subtitlecat", "135.5 KB", "1746"),
        ("Movie C.mp4", "en", "opensubtitles", "51.2 KB", "800"),
    ]


def test_subtitle_rows_tolerate_legacy_results():
    # results from an older run without provider/size/cues metadata
    rows = G.subtitle_rows([{"path": "x/v.mkv", "ok": True, "dest": "x/v.en.srt",
                             "language": "vi"}])
    assert rows == [("v.mkv", "vi", "?", "—", "—")]


def test_subtitle_hints_lines():
    hints = G.subtitle_hints(RESULTS)
    assert hints == [
        "Movie A.mkv [en] via subtitlecat — 135.5 KB, 1746 cues",
        "Movie C.mp4 [en] via opensubtitles — 51.2 KB, 800 cues",
    ]


def test_subtitle_rows_empty():
    assert G.subtitle_rows([]) == []
    assert G.subtitle_rows(None) == []


# ------------------------------------------- real-App render path (headless)
def _make_app_with_retry(retries: int = 3):
    """Transient 'couldn't read ttk/*.tcl' errors can occur when many Tk roots
    are created in one suite run (Windows file locks / AV scans). Retry."""
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
    # keep the persisted history OUT of the real user file
    monkeypatch.setattr(G, "SUBS_HISTORY_FILE", tmp_path / "subs_history.json")
    a = _make_app_with_retry()
    a.update()
    yield a
    a.destroy()


def test_history_table_exists(app):
    headings = {c: app.subs_tree.heading(c)["text"] for c in app.subs_tree["columns"]}
    assert headings == {"video": "Video / title", "lang": "Lang",
                        "provider": "Provider", "size": "Size", "cues": "Cues"}


def test_on_subs_done_fills_history(app):
    app._on_subs_done(2, 3,
                      [(RESULTS[0]["path"], RESULTS[0]["dest"])],
                      RESULTS, {})
    rows = [app.subs_tree.item(i)["values"] for i in app.subs_tree.get_children()]
    assert len(rows) == 2                       # the failed file is not listed
    assert str(rows[0][2]) == "subtitlecat" and str(rows[0][4]) == "1746"
    assert str(rows[1][2]) == "opensubtitles" and str(rows[1][3]) == "51.2 KB"


def test_on_subs_done_no_autoplay(app, monkeypatch):
    launched = []
    monkeypatch.setattr(G, "launch_with_subtitle",
                        lambda *a, **k: launched.append(a) or {"player": "x"})
    app.play_var.set(False)
    app._on_subs_done(1, 1, [(RESULTS[0]["path"], RESULTS[0]["dest"])],
                      RESULTS[:1], {})
    assert launched == []


def test_on_subs_done_autoplay_uses_finished_pairs(app, monkeypatch):
    launched = []
    monkeypatch.setattr(G, "launch_with_subtitle",
                        lambda *a, **k: launched.append(a) or {"player": "x"})
    app.play_var.set(True)
    # launches are staggered via after(1500, cb); drive the scheduled
    # callbacks instead of waiting out the timer
    scheduled = []
    monkeypatch.setattr(app, "after",
                        lambda ms, cb=None: scheduled.append(cb))
    app._on_subs_done(1, 1, [(RESULTS[0]["path"], RESULTS[0]["dest"])],
                      RESULTS[:1], {})
    assert scheduled and scheduled[0] is not None
    scheduled[0]()
    assert launched and launched[0][0] == RESULTS[0]["path"]
