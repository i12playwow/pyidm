from __future__ import annotations

import json
import time

import pytest

gui = pytest.importorskip("idm.gui")
from idm import gui as G


def ok_row(dest, ts=None, **kw):
    r = {"path": "C:/v/A.mkv", "ok": True, "dest": str(dest),
         "language": "en", "provider": "subtitlecat", "size": 100_000, "cues": 500}
    if ts is not None:
        r["ts"] = ts
    r.update(kw)
    return r


# ------------------------------------------------------------ pure helper
def test_missing_helper_detects_absent_dest(tmp_path):
    gone = tmp_path / "gone.en.srt"                    # never created
    here = tmp_path / "here.en.srt"
    here.write_text("srt", encoding="utf-8")
    rows = [ok_row(gone), ok_row(here)]
    assert G.missing_subtitle_indices(rows) == {0}


def test_missing_helper_ignores_rows_without_dest():
    assert G.missing_subtitle_indices([{"ok": True, "path": "C:/v/A.mkv"}]) == set()
    assert G.missing_subtitle_indices([{"ok": False, "dest": "C:/nope.srt"}]) == set()
    assert G.missing_subtitle_indices(None) == set()
    assert G.missing_subtitle_indices([]) == set()


def test_missing_helper_does_not_mutate(tmp_path):
    here = tmp_path / "x.srt"
    here.write_text("srt", encoding="utf-8")
    rows = [ok_row(here)]
    G.missing_subtitle_indices(rows)
    assert rows[0]["dest"] == str(here)


# --------------------------------------------------------- real-App render
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


def _tags_by_video(app):
    return {app.subs_tree.item(i, "values")[0]: set(app.subs_tree.item(i, "tags"))
            for i in app.subs_tree.get_children()}


def test_missing_rows_grey_tagged_in_app(app, tmp_path):
    gone = tmp_path / "Gone.en.srt"                    # never created
    here = tmp_path / "Here.en.srt"
    here.write_text("srt", encoding="utf-8")
    app._on_subs_done(2, 2, [], [ok_row(gone), ok_row(here)], {})
    iids = app.subs_tree.get_children()
    names = [app.subs_tree.item(i, "values")[0] for i in iids]
    assert names == ["A.mkv", "A.mkv"]                 # same video, two rows
    tagged = [set(app.subs_tree.item(i, "tags")) for i in iids]
    assert {"missing"} in tagged and set() in tagged


def test_missing_beats_weak_on_color_clash(app, tmp_path):
    gone_weak = tmp_path / "WeakGone.en.srt"           # weak metrics + missing file
    app._on_subs_done(1, 1, [],
                      [ok_row(gone_weak, size=1000, cues=5)], {})
    iid = app.subs_tree.get_children()[0]
    tags = app.subs_tree.item(iid, "tags")
    assert set(tags) == {"missing", "weak"}
    # Tk gives the LAST tag precedence for overlapping options —
    # "missing" must be last or the row renders amber instead of grey.
    assert tags[-1] == "missing"


def test_missing_grey_style_configured(app):
    cfg = app.subs_tree.tag_configure("missing")
    assert cfg["background"] == "#ececec" and cfg["foreground"] == "#777"


def test_no_false_missing_on_legacy_rows_without_dest(app):
    app._on_subs_done(1, 1, [], [{"ok": True, "path": "C:/v/A.mkv",
                                  "language": "en", "provider": "subtitlecat"}], {})
    iid = app.subs_tree.get_children()[0]
    assert not app.subs_tree.item(iid, "tags")


def test_missing_check_uses_dest_from_pending_finished(app, tmp_path):
    """_on_subs_done result without dest gets it via finished pairs."""
    here = tmp_path / "Fin.en.srt"
    here.write_text("srt", encoding="utf-8")
    row = ok_row(here)
    row_no_dest = dict(row)
    del row_no_dest["dest"]
    app._on_subs_done(1, 1, [(row["path"], str(here))], [row_no_dest], {})
    iid = app.subs_tree.get_children()[0]
    assert not app.subs_tree.item(iid, "tags")          # resolved dest exists
    assert app.subs_dest_by_iid[iid] == str(here)


def test_persisted_history_reflects_disk_on_restore(app, tmp_path):
    gone = tmp_path / "OldGone.en.srt"
    here = tmp_path / "OldHere.en.srt"
    here.write_text("srt", encoding="utf-8")
    (tmp_path / "subs_history.json").write_text(json.dumps([
        ok_row(gone, ts=time.time()), ok_row(here, ts=time.time()),
    ]), encoding="utf-8")
    app._restore_subs_history()
    tagged = [set(app.subs_tree.item(i, "tags"))
              for i in app.subs_tree.get_children()]
    assert {"missing"} in tagged and set() in tagged
