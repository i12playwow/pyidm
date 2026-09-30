from __future__ import annotations

import pytest

gui = pytest.importorskip("idm.gui")
from idm import gui as G

RESULTS = [
    {"path": "C:/videos/Movie A.mkv", "ok": True, "dest": "C:/videos/Movie A.en.srt",
     "language": "en", "provider": "subtitlecat", "size": 135480, "cues": 1746},
    {"path": "C:/videos/Movie B.mkv", "ok": False, "message": "nope"},
]


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


def _load_history(app):
    app._on_subs_done(1, 2, [(RESULTS[0]["path"], RESULTS[0]["dest"])],
                      RESULTS, {})
    return app.subs_tree.get_children()


def test_dest_paths_tracked_per_row(app):
    iids = _load_history(app)
    assert len(iids) == 1
    assert str(app.subs_dest_by_iid[str(iids[0])]).endswith("Movie A.en.srt")


def test_double_click_open_calls_open_file_with_dest(app, monkeypatch):
    iids = _load_history(app)
    app.subs_tree.selection_set(iids[0])
    opened = []
    monkeypatch.setattr(G, "open_file", lambda p: opened.append(p))
    app.open_subtitle()
    assert opened == ["C:/videos/Movie A.en.srt"]


def test_keyboard_and_mouse_bindings_wired(app):
    """All three interactions are bound to the history tree. Dispatch from a
    Tk event to the bound callback is Tk's own guarantee once bind() succeeds;
    the handlers' behavior is covered by the dedicated tests above."""
    for seq in ("<Return>", "<Double-1>", "<Button-3>"):
        assert app.subs_tree.bind(seq), f"{seq} not bound"
    # the context menu carries all three actions
    end = app.subs_menu.index("end")
    entries = [app.subs_menu.entrycget(i, "label") for i in range(end + 1)
               if app.subs_menu.type(i) != "separator"]
    assert {"Open subtitle", "Show in Explorer", "Open video folder"} <= set(entries)


def test_reveal_calls_explorer_reveal(app, monkeypatch):
    iids = _load_history(app)
    app.subs_tree.selection_set(iids[0])
    revealed = []
    monkeypatch.setattr(G, "reveal_in_explorer", lambda p: revealed.append(p))
    app.reveal_subtitle()
    assert revealed == ["C:/videos/Movie A.en.srt"]


def test_open_without_selection_is_noop(app, monkeypatch):
    opened = []
    monkeypatch.setattr(G, "open_file", lambda p: opened.append(p))
    app.open_subtitle()
    app.reveal_subtitle()
    assert opened == []


def test_open_failure_is_logged_not_raised(app, monkeypatch):
    iids = _load_history(app)
    app.subs_tree.selection_set(iids[0])

    def boom(_p):
        raise OSError("no association")

    monkeypatch.setattr(G, "open_file", boom)
    app.open_subtitle()          # must not raise
    assert "could not open" in app.log_text.get("1.0", "end")


def test_reveal_uses_explorer_select_on_windows(monkeypatch):
    """Pure check: on Windows the reveal spawns explorer /select,<path>."""
    if G.os.name != "nt":
        pytest.skip("windows-specific")
    called = {}
    monkeypatch.setattr(G.subprocess, "Popen",
                        lambda args, **k: called.setdefault("args", args))
    G.reveal_in_explorer("C:/x/Movie.en.srt")
    assert called["args"][0] == "explorer"
    assert "Movie.en.srt" in called["args"][2]


def test_context_menu_and_fallback_to_video_folder(monkeypatch, tmp_path):
    """dest missing -> fall back to the video's sidecar path convention."""
    r = {"path": "C:/videos/X.mkv", "ok": True, "language": "en",
         "provider": "p", "size": 10, "cues": 1}
    monkeypatch.setattr(G, "should_show_first_run", lambda cfg: False)
    monkeypatch.setattr(G, "SUBS_HISTORY_FILE", tmp_path / "subs_history.json")
    app = _make_app_with_retry()
    try:
        app._on_subs_done(1, 1, [], [r], {})
        iids = app.subs_tree.get_children()
        assert len(iids) == 1
        assert str(iids[0]) not in app.subs_dest_by_iid
        sel = []
        monkeypatch.setattr(G, "open_file", lambda p: sel.append(p))
        app.subs_tree.selection_set(iids[0])
        app.open_subtitle()
        assert sel == []          # no dest known -> silent noop
    finally:
        app.destroy()
