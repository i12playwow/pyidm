"""Pending sanitize renames surfaced in the downloads tab: rows whose file
on disk has a mangled Content-Disposition name get a violet 'rename' badge
and a Note preview of the clean name (at startup, after restore, and after
a bulk sanitize apply), plus a right-click 'Fix this file name…' action —
the per-file twin of 'idm sanitize-names --apply' sharing the same engine
(headless parity test at the bottom)."""
from __future__ import annotations

import time

import pytest

gui = pytest.importorskip("idm.gui")
from idm import gui as G

PLAYLIST = b"#EXTM3U\n#EXT-X-TARGETDURATION:10\n" + b"\x00" * 13
MANGLED = "130425,_360p.mp4,.mp4,_720p.mp4,"
CLEAN = "130425,_360p.mp4"


def _make_app_with_retry(retries: int = 3):
    last = None
    for _ in range(retries):
        try:
            return G.App()
        except gui.tk.TclError as e:  # pragma: no cover - environmental flake
            last = e
            time.sleep(1.0)
    raise last


class _MsgBoxStub:
    """Records messagebox calls so tests never block on a real dialog."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def showinfo(self, title, message, **kw):
        self.calls.append(("info", message))

    def showwarning(self, title, message, **kw):
        self.calls.append(("warning", message))

    def showerror(self, title, message, **kw):
        self.calls.append(("error", message))

    def askyesno(self, title, message, **kw):
        self.calls.append(("yesno", message))
        return False


def _pump(app, stub, want: int = 1, seconds: float = 5.0) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline and len(stub.calls) < want:
        app.update()
        time.sleep(0.05)
    for _ in range(3):
        app.update()
        app._poll()
        time.sleep(0.02)


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(G, "should_show_first_run", lambda cfg: False)
    monkeypatch.setattr(G, "SUBS_HISTORY_FILE", tmp_path / "subs_history.json")
    a = _make_app_with_retry()
    a.update()
    yield a
    a.destroy()


def _row(app, out, name, url, body=PLAYLIST):
    (out / name).write_bytes(body)
    app._on_task(url, name, 10, 10, "done", "")
    app.tree.selection_set(app.iids[url])
    app.tree.focus(app.iids[url])


# ------------------------------------------------------------- the badge
def test_mangled_row_gets_rename_note_and_violet_tag(app, tmp_path):
    out = tmp_path / "dl"
    out.mkdir()
    app.out_var.set(str(out))
    _row(app, out, MANGLED, "https://x/a")
    app._refresh_rename_notes()
    iid = app.iids["https://x/a"]
    assert app.tree.set(iid, "note") == f"mangled name — clean name is: {CLEAN}"
    assert app.tree.item(iid, "tags") == ("rename",)
    assert "#7c3aed" in str(app.tree.tag_configure("rename", "foreground"))
    assert (out / MANGLED).exists(), "the preview renames nothing"


def test_clean_and_warned_rows_are_untouched(app, tmp_path, monkeypatch):
    out = tmp_path / "dl"
    out.mkdir()
    app.out_var.set(str(out))
    _row(app, out, CLEAN, "https://x/a")
    app._on_task("https://x/b", "real.mp4", 10, 10, "done", "",
                 "file saved, but the URL served PNG")
    app._refresh_rename_notes()
    clean_iid = app.iids["https://x/a"]
    warn_iid = app.iids["https://x/b"]
    assert app.tree.set(clean_iid, "note") == ""
    assert app.tree.item(clean_iid, "tags") == ("done",)
    assert "mangled name" not in app.tree.set(warn_iid, "note")
    assert app.tree.item(warn_iid, "tags")[-1] == "warned", \
        "the amber content warning must never be masked"


def test_unknown_mangled_file_gets_synthetic_row(app, tmp_path):
    out = tmp_path / "dl"
    out.mkdir()
    (out / MANGLED).write_bytes(PLAYLIST)          # no tree row for it
    app.out_var.set(str(out))
    app._refresh_rename_notes()
    rename_iids = [i for u, i in app.iids.items() if u.startswith("rename::")]
    assert len(rename_iids) == 1
    iid = rename_iids[0]
    assert app.tree.set(iid, "url") == "(from an older run)"
    assert app.tree.set(iid, "file") == MANGLED
    assert CLEAN in app.tree.set(iid, "note")


def test_row_file_updates_when_note_refreshes_after_rename(app, tmp_path):
    """A row whose on-disk name changes (CLI or bulk apply) clears its badge
    on the next refresh — file column now clean, note empty, tag gone."""
    out = tmp_path / "dl"
    out.mkdir()
    app.out_var.set(str(out))
    _row(app, out, MANGLED, "https://x/a")
    app._refresh_rename_notes()
    iid = app.iids["https://x/a"]
    assert app.tree.item(iid, "tags") == ("rename",)
    # the file gets renamed out-of-band (CLI, or the bulk Tools action)
    (out / MANGLED).rename(out / CLEAN)
    app.tree.set(iid, "file", CLEAN)
    app._refresh_rename_notes()
    assert app.tree.set(iid, "note") == ""
    assert app.tree.item(iid, "tags") == ("done",)


# ------------------------------------------------------ right-click fix
def test_fix_this_file_name_renames_and_clears_badge(app, tmp_path):
    out = tmp_path / "dl"
    out.mkdir()
    _row(app, out, MANGLED, "https://x/a")
    app.out_var.set(str(out))
    app._refresh_rename_notes()
    iid = app.iids["https://x/a"]
    stub = _MsgBoxStub()
    import unittest.mock as mock
    with mock.patch.object(G, "messagebox", stub):
        app.fix_one_name_from_gui()
        _pump(app, stub)

    assert (out / CLEAN).read_bytes() == PLAYLIST
    assert not (out / MANGLED).exists()
    assert f"[rename] {MANGLED} -> {CLEAN}" in app.log_text.get("1.0", "end")
    assert stub.calls and stub.calls[0][0] == "info"


def test_fix_on_clean_file_is_a_no_op_popup(app, tmp_path):
    out = tmp_path / "dl"
    out.mkdir()
    _row(app, out, CLEAN, "https://x/a")
    app.out_var.set(str(out))
    stub = _MsgBoxStub()
    import unittest.mock as mock
    with mock.patch.object(G, "messagebox", stub):
        app.fix_one_name_from_gui()
        _pump(app, stub)

    assert (out / CLEAN).read_bytes() == PLAYLIST, "clean file untouched"
    assert any(kind == "info" and "already has a clean name" in msg
               for kind, msg in stub.calls)


def test_fix_failure_reports_error_and_keeps_file(app, tmp_path):
    out = tmp_path / "dl"
    out.mkdir()
    _row(app, out, MANGLED, "https://x/a")
    app.out_var.set(str(out))
    stub = _MsgBoxStub()
    import unittest.mock as mock

    real_rename = type(tmp_path).rename

    def locked_rename(self, target):
        raise OSError("locked")

    monkeypatch = tmp_path  # noqa: F841
    with mock.patch.object(type(out), "rename", locked_rename), \
            mock.patch.object(G, "messagebox", stub):
        app.fix_one_name_from_gui()
        _pump(app, stub)

    assert (out / MANGLED).read_bytes() == PLAYLIST, "failed rename keeps the file"
    assert any(kind == "error" and "locked" in msg for kind, msg in stub.calls)
    log = app.log_text.get("1.0", "end")
    assert "[error]" in log and "locked" in log


def test_menu_has_fix_this_file_name(app):
    last = app.tree_menu.index("end")
    labels = [app.tree_menu.entrycget(i, "label") for i in range(last + 1)]
    assert any("Fix this file name" in l for l in labels)


# ------------------------------------------------- headless engine parity
def test_fix_one_name_matches_engine_semantics(tmp_path):
    from idm.gui_common import fix_one_name

    (tmp_path / MANGLED).write_bytes(PLAYLIST)
    assert fix_one_name(tmp_path, MANGLED) == (True, MANGLED, CLEAN, "")
    assert (tmp_path / CLEAN).read_bytes() == PLAYLIST
    # clean name: no-op with an explanatory (empty-target) result
    assert fix_one_name(tmp_path, CLEAN) == (False, CLEAN, "", "")
    # missing file: no-op with an error
    assert fix_one_name(tmp_path, "ghost.webm")[3] == "file is gone"
    # collision: never overwrites, takes the ' (2)' suffix
    (tmp_path / MANGLED).write_bytes(PLAYLIST)
    renamed, _f, to, _e = fix_one_name(tmp_path, MANGLED)
    assert renamed and to == "130425,_360p (2).mp4"
    assert (tmp_path / CLEAN).read_bytes() == PLAYLIST
