from __future__ import annotations

import json

import pytest

from idm import config as C

gui = pytest.importorskip("idm.gui")
from idm import gui as G


# ------------------------------------------------------------- coercion
def test_digit_api_key_stays_string(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "USER_CONFIG_PATH", tmp_path / "c.json")
    C.set_config_value("opensubtitles_api_key", "1234567890123456")
    stored = json.loads((tmp_path / "c.json").read_text())
    assert stored["opensubtitles_api_key"] == "1234567890123456"  # never int
    assert C.set_config_value("opensubtitles_api_key", "1234567890123456")


def test_numeric_keys_still_coerce(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "USER_CONFIG_PATH", tmp_path / "c.json")
    C.set_config_value("workers", "12")
    stored = json.loads((tmp_path / "c.json").read_text())
    assert stored["workers"] == 12                      # real number
    C.set_config_value("min_segmented_size", "1048576")
    stored = json.loads((tmp_path / "c.json").read_text())
    assert stored["min_segmented_size"] == 1048576


def test_player_stays_string_even_if_it_looks_numeric(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "USER_CONFIG_PATH", tmp_path / "c.json")
    C.set_config_value("player", "123")
    stored = json.loads((tmp_path / "c.json").read_text())
    assert stored["player"] == "123"


def test_remove_config_value(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "USER_CONFIG_PATH", tmp_path / "c.json")
    C.set_config_value("workers", "12")
    assert C.remove_config_value("workers") is True
    assert C.load_user_config() == {}
    assert C.remove_config_value("workers") is False    # already gone


# ------------------------------------------------------- warning matrix
def _sources(**overrides):
    base = dict.fromkeys(("out_dir", "workers", "opensubtitles_api_key", "player"), "default")
    base.update(overrides)
    return base


def test_warn_env_overrides_edit():
    monkey = pytest.MonkeyPatch()
    monkey.setenv("IDM_WORKERS", "7")
    w = G.edit_warnings("workers", {}, _sources())
    monkey.undo()
    assert any("IDM_WORKERS" in x and "override" in x for x in w)


def test_env_unset_tip_not_warning():
    monkey = pytest.MonkeyPatch()
    monkey.delenv("IDM_WORKERS", raising=False)
    w = G.edit_warnings("workers", {}, _sources())
    monkey.undo()
    assert any("Tip:" in x for x in w) and not any("override" in x for x in w)


def test_warn_idm_json_shadowing():
    w = G.edit_warnings("player", {}, _sources(player="local (idm.json)"))
    assert any("idm.json" in x and "NOT take effect" in x for x in w)


def test_warn_currently_from_environment():
    w = G.edit_warnings("opensubtitles_api_key", {},
                        _sources(opensubtitles_api_key="environment"))
    assert any("stays overridden" in x for x in w)


def test_api_key_mirror_note():
    w = G.edit_warnings("opensubtitles_api_key", {}, _sources())
    assert any("OPENSUBTITLES_API_KEY" in x and "mirrored" in x for x in w)


def test_no_warnings_for_plain_key():
    assert G.edit_warnings("player", {}, _sources()) == []


# ------------------------------------------- real-App persistence wiring
@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setattr(C, "USER_CONFIG_PATH", tmp_path / "user-config.json")
    monkeypatch.chdir(tmp_path)                          # local idm.json isolated
    monkeypatch.setattr(G, "should_show_first_run", lambda cfg: False)
    a = _make_app_with_retry()
    a.update()
    yield a, tmp_path
    a.destroy()


def _make_app_with_retry(retries: int = 3):
    """Creating many Tk roots in one suite run can hit transient 'couldn't read
    ttk/*.tcl' errors on Windows (file locks / AV scans). Retry a few times."""
    import time

    last = None
    for _ in range(retries):
        try:
            return G.App()
        except gui.tk.TclError as e:  # pragma: no cover - environmental flake
            last = e
            time.sleep(1.0)
    raise last


def _find_all(widget, cls):
    """All descendants of `widget` that are instances of `cls` (any depth)."""
    found = []
    for child in widget.winfo_children():
        if isinstance(child, cls):
            found.append(child)
        found.extend(_find_all(child, cls))
    return found


def _open_editor_on_key(app, key):
    a, _ = app
    a.show_about()
    tops = [w for w in a.winfo_children() if isinstance(w, gui.tk.Toplevel)]
    win = tops[-1]
    trees = [t for t in _find_all(win, gui.tk.ttk.Treeview) if "key" in t["columns"]]
    assert trees, "About treeview not found"
    tree = trees[0]
    for iid in tree.get_children():
        if tree.set(iid, "key") == key:
            return a, win, tree, iid, C.USER_CONFIG_PATH
    raise AssertionError(f"key {key} not in About table")


def _open_editor(a, win, tree, iid):
    a._edit_config_value(tree, iid)
    editors = [w for w in a.winfo_children() if isinstance(w, gui.tk.Toplevel) and w is not win]
    edit_win = editors[-1]
    entry = _find_all(edit_win, gui.tk.ttk.Entry)[0]
    return edit_win, entry


def _click_button(edit_win, label):
    # Read every button's text BEFORE invoking: the handler may destroy the
    # window, which would make later cget() calls raise TclError.
    buttons = [(b, str(b["text"])) for b in _find_all(edit_win, gui.tk.ttk.Button)]
    match = [b for b, text in buttons if text == label]
    assert match, f"button {label!r} not found among {[t for _, t in buttons]}"
    match[0].invoke()


def test_save_and_reset_via_editor(app):
    a, win, tree, iid, cfg_path = _open_editor_on_key(app, "workers")
    edit_win, entry = _open_editor(a, win, tree, iid)
    entry.delete(0, "end")
    entry.insert(0, "11")
    _click_button(edit_win, "Save")
    a.update()
    stored = json.loads(cfg_path.read_text())
    assert stored["workers"] == 11
    # effective table refreshed: value 11 now visible in the About table
    rows = {tree.set(i, "key"): tree.set(i, "value") for i in tree.get_children()}
    assert rows["workers"] == "11"

    # refresh_table() re-created all rows -> re-locate the workers row
    iid = next(i for i in tree.get_children()
               if tree.set(i, "key") == "workers")
    # reset
    edit_win, entry = _open_editor(a, win, tree, iid)
    _click_button(edit_win, "Reset to default (remove key)")
    a.update()
    stored = json.loads(cfg_path.read_text())
    assert "workers" not in stored
    rows = {tree.set(i, "key"): tree.set(i, "value") for i in tree.get_children()}
    assert rows["workers"] == "4"                        # default again


def test_persist_edit_wires_env_mirror(app, monkeypatch):
    a, win, tree, iid, cfg_path = _open_editor_on_key(app, "opensubtitles_api_key")
    seen = {}
    monkeypatch.setattr(G, "set_user_env",
                        lambda name, value: seen.__setitem__(name, value))
    edit_win, entry = _open_editor(a, win, tree, iid)
    entry.delete(0, "end")
    entry.insert(0, "abcd1234efgh5678")
    _click_button(edit_win, "Save")
    a.update()
    assert seen == {"OPENSUBTITLES_API_KEY": "abcd1234efgh5678"}


# --------------------------------------- verify_ignore in the About view
def test_verify_ignore_row_shows_json_and_layer(app):
    """The About table lists verify_ignore with its JSON-array value (and
    where the layer that set it) once entries exist."""
    from idm.config import verify_ignore_add
    a, _ = app
    verify_ignore_add({}, ["130425,_360p.mp4", "*.m3u"])
    a, win, tree, iid, _ = _open_editor_on_key(app, "verify_ignore")
    assert tree.set(iid, "value") == '["130425,_360p.mp4", "*.m3u"]'
    assert tree.set(iid, "source") == "user (~/.idm/config.json)"


def test_edit_verify_ignore_single_name_saves_list(app):
    """A bare filename in the editor round-trips to a one-entry JSON list
    the scan reads — even a comma-heavy mangled name stays whole."""
    a, win, tree, iid, cfg_path = _open_editor_on_key(app, "verify_ignore")
    edit_win, entry = _open_editor(a, win, tree, iid)
    entry.delete(0, "end")
    entry.insert(0, "130425,_360p.mp4,.mp4,_720p.mp4,")
    _click_button(edit_win, "Save")
    a.update()
    stored = json.loads(cfg_path.read_text())
    assert stored["verify_ignore"] == ["130425,_360p.mp4,.mp4,_720p.mp4,"]
    from idm.config import normalize_verify_ignore, verify_ignore_list
    assert verify_ignore_list({"verify_ignore": stored["verify_ignore"]}) == \
        ["130425,_360p.mp4,.mp4,_720p.mp4,"]
    assert normalize_verify_ignore(stored["verify_ignore"]) == \
        ["130425,_360p.mp4,.mp4,_720p.mp4,"]
    rows = {tree.set(i, "key"): tree.set(i, "value") for i in tree.get_children()}
    assert rows["verify_ignore"] == '["130425,_360p.mp4,.mp4,_720p.mp4,"]'


def test_edit_verify_ignore_python_list_and_multi_token(app):
    """Python-style bracketed lists and space-separated tokens are accepted
    and re-encoded as JSON (what the effective-value column shows)."""
    a, win, tree, iid, cfg_path = _open_editor_on_key(app, "verify_ignore")
    edit_win, entry = _open_editor(a, win, tree, iid)
    entry.delete(0, "end")
    entry.insert(0, "['130425,_360p.mp4', '*.m3u']")
    _click_button(edit_win, "Save")
    a.update()
    assert json.loads(cfg_path.read_text())["verify_ignore"] == \
        ["130425,_360p.mp4", "*.m3u"]

    iid = next(i for i in tree.get_children()
               if tree.set(i, "key") == "verify_ignore")
    edit_win, entry = _open_editor(a, win, tree, iid)
    entry.delete(0, "end")
    entry.insert(0, "a.webm b.webm")
    _click_button(edit_win, "Save")
    a.update()
    assert json.loads(cfg_path.read_text())["verify_ignore"] == \
        ["a.webm", "b.webm"]


def test_edit_verify_ignore_reset_to_default(app):
    a, win, tree, iid, cfg_path = _open_editor_on_key(app, "verify_ignore")
    from idm.config import verify_ignore_add
    verify_ignore_add({}, ["kept.webm"])
    edit_win, entry = _open_editor(a, win, tree, iid)
    _click_button(edit_win, "Reset to default (remove key)")
    a.update()
    assert "verify_ignore" not in json.loads(cfg_path.read_text())
    rows = {tree.set(i, "key"): tree.set(i, "value") for i in tree.get_children()}
    assert rows["verify_ignore"] == "[]", "falls back to the default"


def test_edit_verify_ignore_shows_format_hint(app):
    """The editor explains the accepted formats before you type."""
    a, win, tree, iid, _ = _open_editor_on_key(app, "verify_ignore")
    edit_win, _entry = _open_editor(a, win, tree, iid)
    texts = [str(l["text"]) for l in _find_all(edit_win, gui.tk.ttk.Label)]
    assert any("JSON array" in t and "verify_ignore" not in t for t in texts) or \
        any("JSON array" in t for t in texts)


def test_edit_warnings_cover_verify_ignore():
    w = G.edit_warnings("verify_ignore", {}, {})
    assert any("JSON array" in x for x in w), w


def test_about_value_column_truncates_long_lists(app):
    """A long list renders truncated (like every other key) — the JSON copy
    button always carries the full value."""
    from idm.config import verify_ignore_add
    a, _ = app
    verify_ignore_add({}, [f"very_long_name_{i}_padding_padding_padding.webm"
                           for i in range(4)])
    a, win, tree, iid, _ = _open_editor_on_key(app, "verify_ignore")
    assert tree.set(iid, "value").endswith("...")


# ------------------------------- structured list editor (add/remove rows)
def test_list_editor_widget_on_verify_ignore(app):
    """A list-typed key opens the structured editor: seeded rows, an add
    box, and a remove button — alongside the raw JSON entry."""
    from idm.config import verify_ignore_add
    a, _ = app
    verify_ignore_add({}, ["kept.webm", "*.m3u"])
    a, win, tree, iid, _ = _open_editor_on_key(app, "verify_ignore")
    edit_win, entry = _open_editor(a, win, tree, iid)
    boxes = _find_all(edit_win, gui.tk.Listbox)
    assert boxes, "list-typed key must open the structured row editor"
    box = boxes[0]
    assert list(box.get(0, "end")) == ["kept.webm", "*.m3u"], "seeded from the real value"
    labels = [str(l["text"]) for l in _find_all(edit_win, gui.tk.ttk.Label)]
    assert any("entry rows" in t for t in labels)
    texts = [str(b["text"]) for b in _find_all(edit_win, gui.tk.ttk.Button)]
    assert "Add entry" in texts and "Remove selected" in texts


def test_plain_scalar_keys_keep_the_raw_editor(app):
    """Scalar keys (and lists of objects) never grow the row editor."""
    a, win, tree, iid, _ = _open_editor_on_key(app, "workers")
    edit_win, _entry = _open_editor(a, win, tree, iid)
    assert not _find_all(edit_win, gui.tk.Listbox)
    a2, win2, tree2, iid2, _ = _open_editor_on_key(app, "domain_headers")
    edit_win2, _e2 = _open_editor(a2, win2, tree2, iid2)
    assert not _find_all(edit_win2, gui.tk.Listbox), "object lists stay raw"


def test_list_editor_add_and_remove_rows_save(app):
    """Rows added and removed through the widget land in ~/.idm/config.json
    via Save — verbatim, commas and all."""
    a, win, tree, iid, cfg_path = _open_editor_on_key(app, "verify_ignore")
    edit_win, entry = _open_editor(a, win, tree, iid)
    box = _find_all(edit_win, gui.tk.Listbox)[0]
    add_entry = _find_all(edit_win, gui.tk.ttk.Entry)[-1]

    add_entry.insert(0, "130425,_360p.mp4,.mp4,_720p.mp4,")
    _click_button(edit_win, "Add entry")
    add_entry.insert(0, "*.m3u")
    _click_button(edit_win, "Add entry")
    assert list(box.get(0, "end")) == ["130425,_360p.mp4,.mp4,_720p.mp4,", "*.m3u"]
    assert add_entry.get() == "", "the add box clears after each add"

    box.selection_set(0)
    _click_button(edit_win, "Remove selected")
    assert list(box.get(0, "end")) == ["*.m3u"]

    _click_button(edit_win, "Save")
    a.update()
    assert json.loads(cfg_path.read_text())["verify_ignore"] == ["*.m3u"]


def test_list_editor_multi_select_remove(app):
    """Extended selectmode: several rows go in one click."""
    from idm.config import verify_ignore_add
    a, win, tree, iid, cfg_path = _open_editor_on_key(app, "verify_ignore")
    verify_ignore_add({}, ["a.webm", "b.webm", "c.webm"])
    edit_win, _entry = _open_editor(a, win, tree, iid)
    box = _find_all(edit_win, gui.tk.Listbox)[0]
    box.selection_set(0)
    box.selection_set(2)
    _click_button(edit_win, "Remove selected")
    assert list(box.get(0, "end")) == ["b.webm"]
    _click_button(edit_win, "Save")
    a.update()
    assert json.loads(cfg_path.read_text())["verify_ignore"] == ["b.webm"]


def test_list_editor_remove_with_no_selection_is_safe(app):
    a, win, tree, iid, cfg_path = _open_editor_on_key(app, "verify_ignore")
    edit_win, _entry = _open_editor(a, win, tree, iid)
    _click_button(edit_win, "Remove selected")       # nothing selected
    import os
    assert not os.path.exists(cfg_path), "nothing persisted — no config file at all"


def test_list_editor_empty_add_and_bare_json_token(app):
    """An empty add box is a no-op; a bare token typed into the JSON entry
    still shows as a row (the same rule _edit_input applies on Save)."""
    a, win, tree, iid, _ = _open_editor_on_key(app, "verify_ignore")
    edit_win, entry = _open_editor(a, win, tree, iid)
    box = _find_all(edit_win, gui.tk.Listbox)[0]
    _click_button(edit_win, "Add entry")             # empty -> nothing
    assert box.get(0, "end") == ()
    entry.delete(0, "end")
    entry.insert(0, "kept.webm")
    a.update()
    assert box.get(0, "end") == ("kept.webm",), "bare token shows as one row"


def test_list_editor_cancel_discards_changes(app):
    from idm.config import verify_ignore_add
    a, win, tree, iid, cfg_path = _open_editor_on_key(app, "verify_ignore")
    verify_ignore_add({}, ["kept.webm"])
    edit_win, _entry = _open_editor(a, win, tree, iid)
    box = _find_all(edit_win, gui.tk.Listbox)[0]
    add_entry = _find_all(edit_win, gui.tk.ttk.Entry)[-1]
    add_entry.insert(0, "extra.webm")
    _click_button(edit_win, "Add entry")
    assert list(box.get(0, "end")) == ["kept.webm", "extra.webm"]
    _click_button(edit_win, "Cancel")
    a.update()
    assert json.loads(cfg_path.read_text())["verify_ignore"] == ["kept.webm"]
