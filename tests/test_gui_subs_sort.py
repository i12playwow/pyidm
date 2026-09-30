from __future__ import annotations

import json
from pathlib import Path

import pytest

gui = pytest.importorskip("idm.gui")
from idm import gui as G

# (video, cues, size) crafted to exercise sorting and weak flags
ROWS = [
    {"path": "C:/v/Big.mkv", "ok": True, "dest": "C:/v/Big.en.srt", "language": "en",
     "provider": "subtitlecat", "size": 135_480, "cues": 1746},
    {"path": "C:/v/Tiny.mp4", "ok": True, "dest": "C:/v/Tiny.en.srt", "language": "en",
     "provider": "yifysubtitles", "size": 4_200, "cues": 12},          # weak (both)
    {"path": "C:/v/Mid.avi", "ok": True, "dest": "C:/v/Mid.en.srt", "language": "en",
     "provider": "opensubtitles", "size": 30_000, "cues": 300},
    {"path": "C:/v/Legacy.mov", "ok": True, "dest": "C:/v/Legacy.en.srt",
     "language": "en", "provider": "subtitlecat"},                      # no size/cues
    {"path": "C:/v/Failed.mkv", "ok": False, "message": "all providers failed"},
]
OK_VIDEOS = [r["path"].split("/")[-1] for r in ROWS if r["ok"]]


def realize_rows(rows, tmp_path):
    """Copy rows, replacing dest paths with real files in tmp_path so the
    missing-on-disk check passes (the honesty feature must not bleed into
    sorting assertions)."""
    out = []
    for r in rows:
        r2 = dict(r)
        if r2.get("dest"):
            p = tmp_path / Path(r2["dest"]).name
            p.write_text("1\n00:00:01,000 --> 00:00:02,000\nhi\n", encoding="utf-8")
            r2["dest"] = str(p)
        out.append(r2)
    return out


# ------------------------------------------------------------ pure sorting
def test_newest_keeps_chronological_order_and_drops_failures():
    assert [r["path"].split("/")[-1] for r in G.sort_subs_results(ROWS, "newest")] == OK_VIDEOS


def test_cues_sorting_asc_and_desc():
    asc = G.sort_subs_results(ROWS, "cues_asc")
    cues = [r.get("cues") for r in asc]
    assert cues == [12, 300, 1746, None]             # missing value last
    desc = G.sort_subs_results(ROWS, "cues_desc")
    assert [r.get("cues") for r in desc] == [1746, 300, 12, None]


def test_size_sorting_and_missing_last_both_directions():
    assert [r.get("size") for r in G.sort_subs_results(ROWS, "size_asc")] == [4200, 30000, 135480, None]
    assert [r.get("size") for r in G.sort_subs_results(ROWS, "size_desc")] == [135480, 30000, 4200, None]


def test_ties_break_oldest_first():
    tied = [{"path": f"C:/v/a{i}.mkv", "ok": True, "dest": f"C:/v/a{i}.srt",
             "size": 100, "cues": 10} for i in range(4)]
    out = G.sort_subs_results(tied, "size_desc")
    assert [r["path"] for r in out] == [t["path"] for t in tied]


def test_bool_is_not_a_numeric_field():
    rows = [{"path": "x", "ok": True, "dest": "x.srt", "cues": True},   # bool: not a number
            {"path": "y", "ok": True, "dest": "y.srt", "cues": 3}]
    out = G.sort_subs_results(rows, "cues_asc")
    assert [r["path"] for r in out] == ["y", "x"]      # bool row treated as missing -> last


def test_sort_key_from_label():
    assert G.sort_key_from_label("Fewest cues") == "cues_asc"
    assert G.sort_key_from_label("Largest") == "size_desc"
    assert G.sort_key_from_label("Newest") == "newest"
    assert G.sort_key_from_label("nonsense") == "newest"


# --------------------------------------------------------- weak-row logic
def test_is_weak_subtitle_thresholds():
    assert G.is_weak_subtitle({"cues": 12, "size": 4200})            # weak on both
    assert G.is_weak_subtitle({"cues": 12, "size": 90_000})          # weak cues only
    assert G.is_weak_subtitle({"cues": 900, "size": 4_200})          # weak size only
    assert not G.is_weak_subtitle({"cues": 50, "size": 20_480})      # exactly at thresholds
    assert not G.is_weak_subtitle({"cues": 51, "size": 20_490})
    assert not G.is_weak_subtitle({})                                # unknown quality
    assert not G.is_weak_subtitle({"cues": None, "size": None})
    assert not G.is_weak_subtitle({"cues": "1746"})                  # non-numeric ignored


def test_weak_subtitle_indices_map_to_ok_rows():
    weak = G.weak_subtitle_indices(G.sort_subs_results(ROWS, "newest"))
    # OK order: Big(0), Tiny(1), Mid(2), Legacy(3) -> only Tiny is weak
    assert weak == {1}


# --------------------------------------------------- real-App flow (headless)
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


def _tree_rows(app):
    return [app.subs_tree.item(i)["values"] for i in app.subs_tree.get_children()]


def test_sort_combobox_and_tag_exist(app):
    assert app.subs_sort_var.get() == "Newest"
    assert app.subs_sort_var.trace_info()             # re-sort trace is registered
    assert app.subs_tree.tag_configure("weak")["background"] == "#fff3d6"


def test_on_subs_done_renders_sorted_with_weak_tag_and_dests(app, tmp_path):
    rows = realize_rows(ROWS, tmp_path)
    finished = [(r["path"], r["dest"]) for r in rows if r["ok"]]
    app._on_subs_done(4, 5, finished, rows, {})
    iids = app.subs_tree.get_children()
    rows = _tree_rows(app)
    assert len(rows) == 4                              # failure dropped
    # default 'Newest' -> chronological order
    assert [str(r[0]) for r in rows] == OK_VIDEOS
    tags = {str(r[0]): set(app.subs_tree.item(i)["tags"]) for r, i in zip(rows, iids)}
    assert tags["Tiny.mp4"] == {"weak"}
    assert all(not t for v, t in tags.items() if v != "Tiny.mp4")
    # dest map must survive the reorder
    assert G.Path(str(app.subs_dest_by_iid[iids[0]])).name == "Big.en.srt"
    assert G.Path(str(app.subs_dest_by_iid[iids[1]])).name == "Tiny.en.srt"


def test_combobox_change_resorts_table(app):
    app._on_subs_done(4, 5, [(r["path"], r["dest"]) for r in ROWS if r["ok"]], ROWS, {})
    app.subs_sort_var.set("Most cues")                 # trace must re-render
    rows = _tree_rows(app)
    assert [str(r[0]) for r in rows] == ["Big.mkv", "Mid.avi", "Tiny.mp4", "Legacy.mov"]
    assert str(rows[3][4]) == "—"                      # missing cues still listed, last


def test_restore_history_sorted_with_weak_tags(app, tmp_path):
    G.SUBS_HISTORY_FILE.write_text(json.dumps(realize_rows(ROWS, tmp_path)),
                                   encoding="utf-8")
    app._restore_subs_history()
    app.subs_sort_var.set("Smallest")
    rows = _tree_rows(app)
    assert [str(r[0]) for r in rows] == ["Tiny.mp4", "Mid.avi", "Big.mkv", "Legacy.mov"]
    tags = [set(app.subs_tree.item(i)["tags"]) for i in app.subs_tree.get_children()]
    assert tags[0] == {"weak"}
