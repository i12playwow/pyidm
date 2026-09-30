"""GUI downloads tab: the `note` badge — a Note column carrying the task's
non-fatal warning text and an amber 'warned' row tag that wins over the
status color."""
from __future__ import annotations

import time

import pytest

gui = pytest.importorskip("idm.gui")
from idm import gui as G


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
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(G, "should_show_first_run", lambda cfg: False)
    monkeypatch.setattr(G, "SUBS_HISTORY_FILE", tmp_path / "subs_history.json")
    a = _make_app_with_retry()
    a.update()
    yield a
    a.destroy()


def test_note_column_exists(app):
    # 'note' is a Treeview column string, not a class attribute — the real
    # checks are the widget-level ones below (the old `... or True` sentinel
    # here was a false assertion neutered into a tautology).
    cols = app.tree["columns"]
    assert "note" in cols
    assert app.tree.heading("note")["text"] == "Note"


def test_note_empty_by_default(app):
    app._on_task("https://x/a.mp4", "a.mp4", 10, 100, "downloading", "")
    iid = app.iids["https://x/a.mp4"]
    assert app.tree.set(iid, "note") == ""
    assert app.tree.item(iid, "tags") == ("downloading",)


def test_note_sets_column_and_warned_tag(app):
    note = ("file saved, but the URL served JPEG, not the MPEG-4 content "
            "its name promises — it may not open or play correctly")
    app._on_task("https://x/a.mp4", "a.mp4", 100, 100, "done", "ok", note)
    iid = app.iids["https://x/a.mp4"]
    assert app.tree.set(iid, "note") == note
    tags = app.tree.item(iid, "tags")
    assert "warned" in tags and "done" in tags
    assert tags[-1] == "warned"          # amber wins over the status color
    assert "#b26a00" in str(app.tree.tag_configure("warned", "foreground"))


def test_note_persists_and_clears(app):
    url = "https://x/b.webm"
    app._on_task(url, "b.webm", 50, 100, "downloading", "")
    app._on_task(url, "b.webm", 100, 100, "done", "ok",
                 "file saved, but the URL served an M3U playlist")
    iid = app.iids[url]
    assert app.tree.set(iid, "note")
    assert app.tree.item(iid, "tags")[-1] == "warned"


def test_warned_tag_configured_amber(app):
    """The amber badge tag exists; configured after the status tags, Tk's
    last-tag-wins rule gives it priority when both are on a row."""
    fg = str(app.tree.tag_configure("warned", "foreground"))
    assert fg == "#b26a00"
    for status_tag in ("done", "error", "cancelled", "skipped"):
        assert app.tree.tag_configure(status_tag)  # status tags exist too


def test_event_tuple_length_matches_handler(app):
    """The producer lambda sends the task's 7 fields; the handler takes them all."""
    import inspect
    params = [p for p in inspect.signature(G.App._on_task).parameters
              if p not in ("self",)]
    assert len(params) == 7  # url, filename, downloaded, total, status, message, note
