from __future__ import annotations

import json
import time

import pytest

gui = pytest.importorskip("idm.gui")
from idm import gui as G


def row(tmp_path, name="A.mkv", srt="A.en.srt", exists=True, **kw):
    dest = tmp_path / srt
    if exists:
        dest.write_text("1\n00:00:01,000 --> 00:00:02,000\nhi\n", encoding="utf-8")
    return {"path": str(tmp_path / name), "ok": True, "dest": str(dest),
            "language": "en", "provider": "subtitlecat",
            "size": 135_480, "cues": 1746, "ts": time.time(), **kw}


# ------------------------------------------------------------ pure helpers
def test_find_relocated_file_exact_match(tmp_path):
    (tmp_path / "A.en.srt").write_text("x", encoding="utf-8")
    assert G.find_relocated_file("C:/old/A.en.srt", tmp_path) == \
        str(tmp_path / "A.en.srt")


def test_find_relocated_file_unique_stem(tmp_path):
    (tmp_path / "A.en.ass").write_text("x", encoding="utf-8")
    assert G.find_relocated_file("C:/old/A.en.srt", tmp_path) == \
        str(tmp_path / "A.en.ass")


def test_find_relocated_file_ambiguous_stem_is_none(tmp_path):
    (tmp_path / "A.en.srt").write_text("x", encoding="utf-8")
    (tmp_path / "A.en.vtt").write_text("x", encoding="utf-8")
    dest = "C:/old/B.en.srt"                      # exact name absent -> stem path
    assert G.find_relocated_file(dest, tmp_path) is None


def test_find_relocated_file_no_match_or_bad_folder(tmp_path):
    assert G.find_relocated_file("C:/old/A.en.srt", tmp_path) is None
    assert G.find_relocated_file("C:/old/A.en.srt", tmp_path / "nope") is None


def test_update_history_dest_persists(tmp_path, monkeypatch):
    p = tmp_path / "h.json"
    rows = [row(tmp_path, exists=False),
            row(tmp_path, name="B.mkv", srt="B.en.srt", exists=False)]
    p.write_text(json.dumps(rows), encoding="utf-8")
    monkeypatch.setattr(G, "SUBS_HISTORY_FILE", p)
    n = G.update_history_dest(rows[0]["dest"], "C:/new/A.en.srt")
    assert n == 1
    saved = json.loads(p.read_text(encoding="utf-8"))
    assert saved[0]["dest"] == "C:/new/A.en.srt"
    assert saved[1]["dest"] == rows[1]["dest"]        # untouched


def test_update_history_dest_no_match_or_write_failure(tmp_path, monkeypatch):
    p = tmp_path / "h.json"
    rows = [row(tmp_path, exists=False)]
    p.write_text(json.dumps(rows), encoding="utf-8")
    monkeypatch.setattr(G, "SUBS_HISTORY_FILE", p)
    assert G.update_history_dest("C:/other.srt", "C:/new.srt") == 0
    monkeypatch.setattr(G.Path, "write_text",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("ro")))
    assert G.update_history_dest(rows[0]["dest"], "C:/new.srt") == 0


# --------------------------------------------------------------- app flow
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


def _load(app, rows):
    G.SUBS_HISTORY_FILE.write_text(json.dumps(rows), encoding="utf-8")
    app._restore_subs_history()
    return list(app.subs_tree.get_children())


def test_relocate_via_folder_match(app, tmp_path, monkeypatch):
    rows = [row(tmp_path, exists=False)]
    iids = _load(app, rows)
    app.subs_tree.selection_set(iids[0])
    moved = tmp_path / "moved"
    moved.mkdir()
    (moved / "A.en.srt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(G.filedialog, "askdirectory", lambda **kw: str(moved))
    app._relocate_subtitle()
    assert json.loads(G.SUBS_HISTORY_FILE.read_text(encoding="utf-8"))[0]["dest"] \
        == str(moved / "A.en.srt")
    # row un-greys: no missing tag on the re-rendered row (fresh iids —
    # the table was rebuilt)
    new_iids = list(app.subs_tree.get_children())
    assert "missing" not in app.subs_tree.item(new_iids[0], "tags")
    assert app.subs_dest_by_iid[new_iids[0]] == str(moved / "A.en.srt")
    assert "relocated" in app.log_text.get("1.0", "end")


def test_relocate_folder_without_match_falls_back_to_file(app, tmp_path, monkeypatch):
    rows = [row(tmp_path, exists=False)]
    iids = _load(app, rows)
    app.subs_tree.selection_set(iids[0])
    empty = tmp_path / "empty"
    empty.mkdir()
    picked = tmp_path / "elsewhere.srt"
    picked.write_text("x", encoding="utf-8")
    monkeypatch.setattr(G.filedialog, "askdirectory", lambda **kw: str(empty))
    monkeypatch.setattr(G.filedialog, "askopenfilename", lambda **kw: str(picked))
    app._relocate_subtitle()
    saved = json.loads(G.SUBS_HISTORY_FILE.read_text(encoding="utf-8"))
    assert saved[0]["dest"] == str(picked)
    assert "relocated" in app.log_text.get("1.0", "end")


def test_relocate_folder_no_match_then_file_cancel(app, tmp_path, monkeypatch):
    rows = [row(tmp_path, exists=False)]
    iids = _load(app, rows)
    app.subs_tree.selection_set(iids[0])
    empty = tmp_path / "empty2"
    empty.mkdir()
    monkeypatch.setattr(G.filedialog, "askdirectory", lambda **kw: str(empty))
    monkeypatch.setattr(G.filedialog, "askopenfilename", lambda **kw: "")
    before = G.SUBS_HISTORY_FILE.read_text(encoding="utf-8")
    app._relocate_subtitle()
    assert G.SUBS_HISTORY_FILE.read_text(encoding="utf-8") == before
    assert "relocated" not in app.log_text.get("1.0", "end")
    assert "missing" in app.subs_tree.item(iids[0], "tags")   # still grey


def test_relocate_guard_when_file_exists(app, tmp_path, monkeypatch):
    rows = [row(tmp_path)]                        # dest exists
    iids = _load(app, rows)
    app.subs_tree.selection_set(iids[0])
    opened = []
    monkeypatch.setattr(G.filedialog, "askdirectory",
                        lambda **kw: opened.append(1) or "")
    app._relocate_subtitle()
    assert not opened                             # no dialog offered
    assert "already exists" in app.log_text.get("1.0", "end")


def test_relocate_no_selection_is_noop(app, monkeypatch):
    opened = []
    monkeypatch.setattr(G.filedialog, "askdirectory",
                        lambda **kw: opened.append(1) or "")
    app._relocate_subtitle()
    assert not opened


def test_relocate_updates_dest_map_for_render(app, tmp_path, monkeypatch):
    rows = [row(tmp_path, exists=False)]
    iids = _load(app, rows)
    app._pending_finished = [("V.mkv", rows[0]["dest"])]
    app.subs_tree.selection_set(iids[0])
    moved = tmp_path / "m2"
    moved.mkdir()
    (moved / "A.en.srt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(G.filedialog, "askdirectory", lambda **kw: str(moved))
    app._relocate_subtitle()
    new_iids = list(app.subs_tree.get_children())
    assert app.subs_dest_by_iid[new_iids[0]] == str(moved / "A.en.srt")
    assert app._pending_finished == []   # restore resets session fallback


def test_menu_has_relocate(app):
    labels = []
    for i in range(app.subs_menu.index("end") + 1):
        if app.subs_menu.type(i) != "separator":
            labels.append(app.subs_menu.entrycget(i, "label"))
    assert "Relocate..." in labels
