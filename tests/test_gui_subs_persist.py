from __future__ import annotations

import pytest

gui = pytest.importorskip("idm.gui")
from idm import gui as G

RESULTS = [
    {"path": "C:/videos/Movie A.mkv", "ok": True, "dest": "C:/videos/Movie A.en.srt",
     "language": "en", "provider": "subtitlecat", "size": 135480, "cues": 1746},
    {"path": "C:/videos/Movie B.mkv", "ok": False, "message": "nope"},
]


# ------------------------------------------------------------ storage layer
def test_save_then_load_round_trip(tmp_path):
    f = tmp_path / "h.json"
    G.save_subs_history(RESULTS, path=f)
    loaded = G.load_subs_history(path=f)
    assert len(loaded) == 1                     # only the successful row
    assert loaded[0]["dest"].endswith("Movie A.en.srt")
    assert loaded[0]["provider"] == "subtitlecat"


def test_save_appends_and_caps(tmp_path):
    f = tmp_path / "h.json"
    G.save_subs_history(RESULTS, path=f, cap=3)
    G.save_subs_history(RESULTS, path=f, cap=3)
    G.save_subs_history(RESULTS, path=f, cap=3)
    loaded = G.load_subs_history(path=f)
    assert len(loaded) == 3                     # capped, oldest dropped
    assert all(r["provider"] == "subtitlecat" for r in loaded)


def test_load_missing_and_corrupt_files(tmp_path):
    assert G.load_subs_history(path=tmp_path / "absent.json") == []
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert G.load_subs_history(path=bad) == []
    # a JSON file that isn't a list is ignored, not fatal
    (tmp_path / "dict.json").write_text('{"a": 1}', encoding="utf-8")
    assert G.load_subs_history(path=tmp_path / "dict.json") == []


# ------------------------------------------------- App integration (headless)
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
    monkeypatch.setattr(G, "SUBS_HISTORY_FILE", tmp_path / "h.json")
    a = _make_app_with_retry()
    a.update()
    yield a, tmp_path / "h.json"
    a.destroy()


def test_history_survives_restart(app):
    a, histfile = app
    a._on_subs_done(1, 2, [(RESULTS[0]["path"], RESULTS[0]["dest"])], RESULTS, {})
    assert histfile.exists()                    # written synchronously

    # a *new* App instance sees the persisted rows on startup
    import unittest.mock as um
    with um.patch.object(G, "should_show_first_run", lambda cfg: False):
        b = _make_app_with_retry()
    try:
        rows = b.subs_tree.get_children()
        assert len(rows) == 1
        vals = b.subs_tree.item(rows[0])["values"]
        assert str(vals[2]) == "subtitlecat"
        assert str(b.subs_dest_by_iid[str(rows[0])]).endswith("Movie A.en.srt")
    finally:
        b.destroy()


def test_clear_history_removes_rows_and_file(app, monkeypatch):
    a, histfile = app
    a._on_subs_done(1, 2, [(RESULTS[0]["path"], RESULTS[0]["dest"])], RESULTS, {})
    assert a.subs_tree.get_children() and histfile.exists()

    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *a_, **k: True)
    a.clear_subs_history()
    assert a.subs_tree.get_children() == ()
    assert not histfile.exists()
    assert a.subs_dest_by_iid == {}


def test_clear_history_declined_keeps_everything(app, monkeypatch):
    a, histfile = app
    a._on_subs_done(1, 2, [(RESULTS[0]["path"], RESULTS[0]["dest"])], RESULTS, {})
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *a_, **k: False)
    a.clear_subs_history()
    assert a.subs_tree.get_children()           # rows kept
    assert histfile.exists()                    # file kept


def test_clear_on_empty_history_is_silent_noop(app, monkeypatch):
    a, histfile = app
    called = []
    monkeypatch.setattr(gui.messagebox, "askyesno",
                        lambda *a_, **k: called.append(1) or True)
    a.clear_subs_history()
    assert called == []                         # never prompted
