"""GUI downloads-tab right-click menu: Verify files (magic-byte scan +
quarantine) and Restore quarantined files — the GUI twins of
'idm verify --delete-warned' and 'idm restore', sharing the exact same
gui_common engine (verified by the headless parity test at the bottom)."""
from __future__ import annotations

import time

import pytest

gui = pytest.importorskip("idm.gui")
from idm import gui as G

MP4_HEAD = b"\x00\x00\x00\x18ftypmp44" + b"\x00" * 24          # real MPEG-4
PNG_HEAD = b"\x89PNG\r\n\x1a\n" + b"\x00" * 25                 # mislabeled body
PLAYLIST = b"#EXTM3U\n#EXT-X-TARGETDURATION:10\n" + b"\x00" * 13


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
    """Records messagebox calls (so tests never block on a real dialog);
    askyesno answers False by default so offers to change config state are
    declined unless a test opts in."""

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


def _pump(app, stub, seconds: float = 5.0) -> None:
    """Pump the Tk event loop until the worker thread's result popup has
    arrived (the actions run in background threads and report via events)."""
    deadline = time.time() + seconds
    while time.time() < deadline and not stub.calls:
        app.update()
        time.sleep(0.05)
    app.update()


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(G, "should_show_first_run", lambda cfg: False)
    monkeypatch.setattr(G, "SUBS_HISTORY_FILE", tmp_path / "subs_history.json")
    a = _make_app_with_retry()
    a.update()
    yield a
    a.destroy()


# ------------------------------------------------------------------- the menu
def test_tree_menu_has_quarantine_actions(app):
    last = app.tree_menu.index("end")
    labels = [app.tree_menu.entrycget(i, "label") for i in range(last + 1)]
    assert any("Verify" in l for l in labels)
    assert any("Restore" in l for l in labels)


# ------------------------------------------------------------ verify (sweep)
def test_verify_action_quarantines_and_reports(app, tmp_path, monkeypatch):
    out = tmp_path / "dl"
    out.mkdir()
    (out / "fake.mp4").write_bytes(PNG_HEAD)
    (out / "real.mp4").write_bytes(MP4_HEAD)
    app.out_var.set(str(out))
    stub = _MsgBoxStub()
    monkeypatch.setattr(G, "messagebox", stub)

    app.verify_quarantine_from_gui()
    _pump(app, stub)
    app._poll()                      # drain any remaining events

    assert stub.calls, "result popup never arrived"
    assert (out / "quarantine" / "fake.mp4").read_bytes() == PNG_HEAD
    assert (out / "real.mp4").exists(), "clean file untouched"
    log = app.log_text.get("1.0", "end")
    assert "[quarantine] fake.mp4 ->" in log
    assert any(kind == "warning" and "1 flagged" in msg for kind, msg in stub.calls)


def test_verify_action_clean_shows_info(app, tmp_path, monkeypatch):
    out = tmp_path / "dl"
    out.mkdir()
    (out / "real.mp4").write_bytes(MP4_HEAD)
    app.out_var.set(str(out))
    stub = _MsgBoxStub()
    monkeypatch.setattr(G, "messagebox", stub)

    app.verify_quarantine_from_gui()
    _pump(app, stub)
    app._poll()

    assert not (out / "quarantine").exists(), "no folder for a clean sweep"
    assert any(kind == "info" and "nothing needed quarantining" in msg
               for kind, msg in stub.calls)


# ------------------------------------------------------- moved rows in the tree
def test_sweep_marks_moved_rows_and_adds_synthetic_ones(app, tmp_path, monkeypatch):
    out = tmp_path / "dl"
    out.mkdir()
    (out / "fake.mp4").write_bytes(PNG_HEAD)
    (out / "ghost.webm").write_bytes(PLAYLIST)   # no URL row for this one
    app.out_var.set(str(out))
    app._on_task("https://x/fake.mp4", "fake.mp4", 10, 10, "done", "")
    iid = app.iids["https://x/fake.mp4"]
    stub = _MsgBoxStub()
    monkeypatch.setattr(G, "messagebox", stub)

    app.verify_quarantine_from_gui()
    _pump(app, stub)
    app._poll()

    assert app.tree.set(iid, "status") == "moved"
    assert app.tree.item(iid, "tags") == ("moved",)
    assert "#6b7280" in str(app.tree.tag_configure("moved", "foreground"))
    synth = [i for i in app.quar_iids.values()
             if app.tree.set(i, "url") == "(from an older run)"]
    assert len(synth) == 1
    assert app.tree.set(synth[0], "file") == "ghost.webm"
    assert app.tree.set(synth[0], "status") == "moved"


def test_restore_preselects_the_clicked_file(app, tmp_path, monkeypatch):
    out = tmp_path / "dl"
    out.mkdir()
    (out / "fake.mp4").write_bytes(PNG_HEAD)
    (out / "other.webm").write_bytes(PLAYLIST)
    app.out_var.set(str(out))
    app._on_task("https://x/fake.mp4", "fake.mp4", 10, 10, "done", "")
    app.verify_quarantine_from_gui()
    stub = _MsgBoxStub()
    monkeypatch.setattr(G, "messagebox", stub)
    _pump(app, stub)
    app._poll()

    app.tree.selection_set(app.iids["https://x/fake.mp4"])
    app.tree.focus(app.iids["https://x/fake.mp4"])
    got: dict = {}

    def fake_picker(title, prompt, items, preselect=None):
        got["items"] = list(items)
        got["preselect"] = preselect
        return ["fake.mp4"]

    monkeypatch.setattr(G, "_ask_choose_items", fake_picker)
    app.restore_quarantine_from_gui()
    app._poll()
    assert got["preselect"] == ["fake.mp4"], got
    assert set(got["items"]) == {"fake.mp4", "other.webm"}


def test_restore_reverts_moved_rows(app, tmp_path, monkeypatch):
    out = tmp_path / "dl"
    out.mkdir()
    (out / "fake.mp4").write_bytes(PNG_HEAD)
    app.out_var.set(str(out))
    app._on_task("https://x/fake.mp4", "fake.mp4", 10, 10, "done", "")
    iid = app.iids["https://x/fake.mp4"]
    stub = _MsgBoxStub()
    monkeypatch.setattr(G, "messagebox", stub)
    app.verify_quarantine_from_gui()
    _pump(app, stub)
    app._poll()
    assert app.tree.set(iid, "status") == "moved"

    monkeypatch.setattr(G, "_ask_choose_items", lambda *a, **k: ["fake.mp4"])
    app.restore_quarantine_from_gui()
    _pump(app, stub)
    app._poll()

    assert app.tree.set(iid, "status") == "done"
    assert app.tree.item(iid, "tags") == ("done",)
    assert app.tree.set(iid, "note") == ""
    assert app.quar_iids == {}


def test_restore_removes_synthetic_rows(app, tmp_path, monkeypatch):
    out = tmp_path / "dl"
    out.mkdir()
    (out / "ghost.webm").write_bytes(PLAYLIST)
    app.out_var.set(str(out))
    stub = _MsgBoxStub()
    monkeypatch.setattr(G, "messagebox", stub)
    app.verify_quarantine_from_gui()
    _pump(app, stub)
    app._poll()
    synth = list(app.quar_iids.values())
    assert len(synth) == 1

    monkeypatch.setattr(G, "_ask_choose_items", lambda *a, **k: ["ghost.webm"])
    app.restore_quarantine_from_gui()
    _pump(app, stub)
    app._poll()

    assert app.quar_iids == {}
    assert not app.tree.exists(synth[0]), "synthetic row removed after restore"


# -------------------------------------------------------------------- restore
def test_restore_action_brings_files_back(app, tmp_path, monkeypatch):
    out = tmp_path / "dl"
    out.mkdir()
    q = out / "quarantine"
    q.mkdir()
    (q / "fake.mp4").write_bytes(PLAYLIST)
    app.out_var.set(str(out))
    monkeypatch.setattr(G, "_ask_choose_items", lambda *a, **k: ["fake.mp4"])
    stub = _MsgBoxStub()
    monkeypatch.setattr(G, "messagebox", stub)

    app.restore_quarantine_from_gui()
    _pump(app, stub)
    app._poll()

    assert (out / "fake.mp4").read_bytes() == PLAYLIST
    assert not q.exists(), "emptied quarantine is tidied away"
    log = app.log_text.get("1.0", "end")
    assert "[restore] fake.mp4 ->" in log
    assert any(kind == "info" and "1 file(s) restored" in msg
               for kind, msg in stub.calls)


def test_restore_action_cancelled_leaves_everything(app, tmp_path, monkeypatch):
    out = tmp_path / "dl"
    out.mkdir()
    q = out / "quarantine"
    q.mkdir()
    (q / "fake.mp4").write_bytes(PLAYLIST)
    app.out_var.set(str(out))
    monkeypatch.setattr(G, "_ask_choose_items", lambda *a, **k: None)  # cancel
    stub = _MsgBoxStub()
    monkeypatch.setattr(G, "messagebox", stub)

    app.restore_quarantine_from_gui()
    assert stub.calls == []          # no popup on cancel
    assert (q / "fake.mp4").exists(), "nothing moved"
    assert not (out / "fake.mp4").exists()


def test_restore_action_without_quarantine(app, tmp_path, monkeypatch):
    out = tmp_path / "dl"
    out.mkdir()
    app.out_var.set(str(out))
    stub = _MsgBoxStub()
    monkeypatch.setattr(G, "messagebox", stub)

    app.restore_quarantine_from_gui()
    assert any(kind == "info" and "Nothing to restore" in msg
               for kind, msg in stub.calls)


# ------------------------------------------------- headless engine parity
def test_gui_common_helpers_match_cli_payloads(tmp_path):
    """The GUI buttons and the CLI commands share one engine; the payloads
    and on-disk effects must agree."""
    from idm.gui_common import (
        move_warned_to_quarantine,
        restore_from_quarantine,
        scan_for_quarantine,
    )

    (tmp_path / "fake.mp4").write_bytes(PNG_HEAD)
    (tmp_path / "real.mp4").write_bytes(MP4_HEAD)
    scan = scan_for_quarantine(tmp_path)
    assert (scan["scanned"], scan["warned"], scan["ok"]) == (2, 1, 1)
    payload, moves = move_warned_to_quarantine(tmp_path, scan)
    assert payload["moved"] == 1 and payload["warned"] == 0 and len(moves) == 1
    assert (tmp_path / "quarantine" / "fake.mp4").exists()
    back = restore_from_quarantine(tmp_path)
    assert back["restored"] == 1 and (tmp_path / "fake.mp4").exists()
    assert not (tmp_path / "quarantine").exists()
