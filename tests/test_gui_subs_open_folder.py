from __future__ import annotations

import pytest

gui = pytest.importorskip("idm.gui")
from idm import gui as G

ROWS = [
    {"path": "C:/videos/Movie A.mkv", "ok": True, "dest": "C:/videos/Movie A.en.srt",
     "language": "en", "provider": "subtitlecat", "size": 135480, "cues": 1746},
    {"path": "C:/downloads/Legacy.mov", "ok": True, "dest": "C:/videos/Legacy.en.srt",
     "language": "en", "provider": "p", "size": 900, "cues": 9},  # video folder != dest folder
]


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


def _select(app, index=0):
    iid = app.subs_tree.get_children()[index]
    app.subs_tree.selection_set(iid)
    return iid


def test_menu_contains_open_video_folder(app):
    end = app.subs_menu.index("end")
    labels = [app.subs_menu.entrycget(i, "label") for i in range(end + 1)
              if app.subs_menu.type(i) != "separator"]
    assert "Open video folder" in labels


def test_open_video_folder_uses_video_parent(app, monkeypatch):
    app._on_subs_done(2, 2, [(r["path"], r["dest"]) for r in ROWS], ROWS, {})
    _select(app, 0)
    opened = []
    monkeypatch.setattr(G, "open_folder", lambda p: opened.append(p))
    app.open_video_folder()
    assert opened == [str(G.Path("C:/videos"))]   # the video's folder, not the .srt


def test_open_video_folder_opens_distinct_folders(app, monkeypatch):
    app._on_subs_done(2, 2, [(r["path"], r["dest"]) for r in ROWS], ROWS, {})
    opened = []
    monkeypatch.setattr(G, "open_folder", lambda p: opened.append(p))
    _select(app, 0)
    app.open_video_folder()
    _select(app, 1)
    app.open_video_folder()
    assert opened == [str(G.Path("C:/videos")), str(G.Path("C:/downloads"))]


def test_falls_back_to_dest_parent_when_video_path_missing(app, monkeypatch, tmp_path):
    row = {"path": "", "ok": True, "dest": str(tmp_path / "Movie.en.srt"),
           "language": "en", "provider": "p", "size": 10, "cues": 1}
    app._on_subs_done(1, 1, [], [row], {})
    _select(app)
    opened = []
    monkeypatch.setattr(G, "open_folder", lambda p: opened.append(p))
    app.open_video_folder()
    assert opened == [str(tmp_path)]


def test_no_selection_is_silent_noop(app, monkeypatch):
    opened = []
    monkeypatch.setattr(G, "open_folder", lambda p: opened.append(p))
    app.open_video_folder()
    assert opened == []


def test_open_failure_is_logged_not_raised(app, monkeypatch):
    app._on_subs_done(1, 1, [(ROWS[0]["path"], ROWS[0]["dest"])], ROWS[:1], {})
    _select(app)

    def boom(_p):
        raise OSError("gone")

    monkeypatch.setattr(G, "open_folder", boom)
    app.open_video_folder()                    # must not raise
    assert "could not open folder" in app.log_text.get("1.0", "end")


def test_maps_rebuilt_on_re_sort(app):
    app._on_subs_done(2, 2, [(r["path"], r["dest"]) for r in ROWS], ROWS, {})
    app.subs_sort_var.set("Smallest")
    # after any re-render, every row keeps both its dest and its video path
    assert set(app.subs_dest_by_iid) == set(app.subs_video_by_iid)
    for vid in app.subs_video_by_iid.values():
        assert vid.endswith((".mkv", ".mov"))
