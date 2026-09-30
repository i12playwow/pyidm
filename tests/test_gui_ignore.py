"""GUI verify ignore-list workflow: right-click ▸ Ignore this file in
verify… / Manage verify ignore list… — the GUI twins of 'idm ignore
add/remove', writing the same 'verify_ignore' config key the CLI and the
verify scan read."""
from __future__ import annotations

import time

import pytest

gui = pytest.importorskip("idm.gui")
from idm import gui as G
import idm.config as CF

PLAYLIST = b"#EXTM3U\n#EXT-X-TARGETDURATION:10\n" + b"\x00" * 13
MP4_HEAD = b"\x00\x00\x00\x18ftypmp44" + b"\x00" * 24


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
    """Records messagebox calls; askyesno answers per-flag (default False
    so offers to change config state are declined unless opted in)."""

    def __init__(self, yes_to_ignore: bool = False):
        self.calls: list[tuple[str, str]] = []
        self.yes_to_ignore = yes_to_ignore

    def showinfo(self, title, message, **kw):
        self.calls.append(("info", message))

    def showwarning(self, title, message, **kw):
        self.calls.append(("warning", message))

    def showerror(self, title, message, **kw):
        self.calls.append(("error", message))

    def askyesno(self, title, message, **kw):
        self.calls.append(("yesno", message))
        return "ignore list" in message and self.yes_to_ignore


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
    # inside tmp_path but not inside any scanned downloads folder, and the
    # folder pre-created: set_config_value() writes without mkdir-ing parents
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    monkeypatch.setattr(CF, "USER_CONFIG_PATH", cfg_dir / "config.json")
    monkeypatch.setattr(G, "should_show_first_run", lambda cfg: False)
    monkeypatch.setattr(G, "SUBS_HISTORY_FILE", tmp_path / "subs_history.json")
    # hermetic dialogs: a bug must fail the test, never open a real prompt
    monkeypatch.setattr(G, "_ask_string",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("_ask_string leaked in a test")))
    a = _make_app_with_retry()
    a.update()
    yield a
    a.destroy()


def _row(app, out, name, url):
    (out / name).write_bytes(PLAYLIST)
    app._on_task(url, name, 10, 10, "done", "")
    app.tree.selection_set(app.iids[url])
    app.tree.focus(app.iids[url])


# --------------------------------------------------------------- the menu
def test_tree_menu_has_ignore_actions(app):
    last = app.tree_menu.index("end")
    labels = [app.tree_menu.entrycget(i, "label") for i in range(last + 1)]
    assert any("Ignore this file" in l for l in labels)
    assert any("Manage verify ignore list" in l for l in labels)


# ------------------------------------------------- ignore this file…
def test_ignore_file_adds_focused_row_name(app, tmp_path):
    import unittest.mock as mock

    out = tmp_path / "dl"
    out.mkdir()
    _row(app, out, "130425,_360p.mp4", "https://x/130425,_360p.mp4")
    with mock.patch.object(G, "_ask_string",
                           return_value="130425,_360p.mp4"), \
            mock.patch.object(G, "messagebox", _MsgBoxStub()) as stub:
        app.ignore_file_from_gui()
    from idm.config import verify_ignore_list
    assert verify_ignore_list(app.cfg) == ["130425,_360p.mp4"]
    log = app.log_text.get("1.0", "end")
    assert "[ignore] 130425,_360p.mp4 added" in log
    assert stub.calls and stub.calls[0][0] == "info"


def test_ignore_file_suggestion_comes_from_the_row(app, tmp_path):
    import unittest.mock as mock

    out = tmp_path / "dl"
    out.mkdir()
    _row(app, out, "kept.webm", "https://x/kept.webm")
    got: dict = {}

    def fake_ask(title, prompt, initial=""):
        got["initial"] = initial
        return None                      # cancel — nothing persisted

    with mock.patch.object(G, "_ask_string", fake_ask), \
            mock.patch.object(G, "messagebox", _MsgBoxStub()):
        app.ignore_file_from_gui()
    assert got["initial"] == "kept.webm"
    from idm.config import verify_ignore_list
    assert verify_ignore_list(app.cfg) == [], "cancel adds nothing"


def test_ignore_file_cancelled_or_empty_persists_nothing(app, tmp_path):
    import unittest.mock as mock

    out = tmp_path / "dl"
    out.mkdir()
    _row(app, out, "kept.webm", "https://x/kept.webm")
    with mock.patch.object(G, "_ask_string", return_value=None), \
            mock.patch.object(G, "messagebox", _MsgBoxStub()):
        app.ignore_file_from_gui()
    with mock.patch.object(G, "_ask_string", return_value="   "), \
            mock.patch.object(G, "messagebox", _MsgBoxStub()):
        app.ignore_file_from_gui()
    from idm.config import verify_ignore_list
    assert verify_ignore_list(app.cfg) == []


# ------------------------------------------------- manage the list…
def test_manage_removes_selected_entries(app, tmp_path):
    import unittest.mock as mock

    from idm.config import verify_ignore_add, verify_ignore_list
    verify_ignore_add(app.cfg, ["a.webm", "b.webm"])
    with mock.patch.object(G, "_ask_choose_items",
                           return_value=["a.webm"]), \
            mock.patch.object(G, "messagebox", _MsgBoxStub()):
        app.manage_verify_ignore_from_gui()
    assert verify_ignore_list(app.cfg) == ["b.webm"]
    log = app.log_text.get("1.0", "end")
    assert "[ignore] a.webm removed" in log


def test_manage_cancelled_removes_nothing(app, tmp_path):
    import unittest.mock as mock

    from idm.config import verify_ignore_add, verify_ignore_list
    verify_ignore_add(app.cfg, ["a.webm"])
    with mock.patch.object(G, "_ask_choose_items", return_value=None), \
            mock.patch.object(G, "messagebox", _MsgBoxStub()):
        app.manage_verify_ignore_from_gui()
    assert verify_ignore_list(app.cfg) == ["a.webm"]


def test_manage_ok_with_nothing_selected_removes_nothing(app, tmp_path):
    import unittest.mock as mock

    from idm.config import verify_ignore_add, verify_ignore_list
    verify_ignore_add(app.cfg, ["a.webm"])
    with mock.patch.object(G, "_ask_choose_items", return_value=[]), \
            mock.patch.object(G, "messagebox", _MsgBoxStub()):
        app.manage_verify_ignore_from_gui()
    assert verify_ignore_list(app.cfg) == ["a.webm"], \
        "OK with nothing selected removes nothing"


def test_manage_empty_list_offers_to_add(app, tmp_path):
    import unittest.mock as mock

    calls: list[str] = []
    with mock.patch.object(type(app), "ignore_file_from_gui",
                           lambda self: calls.append("add")):
        app.manage_verify_ignore_from_gui()
    assert calls == ["add"], "empty list falls through to the add dialog"


# ------------------------------------- the verify flow honors the list
def test_verify_scan_reports_ignored_and_never_sweeps_them(app, tmp_path):
    out = tmp_path / "dl"
    out.mkdir()
    (out / "kept.webm").write_bytes(PLAYLIST)
    (out / "gone.webm").write_bytes(PLAYLIST)
    (out / "real.mp4").write_bytes(MP4_HEAD)
    app.out_var.set(str(out))
    app._on_task("https://x/kept.webm", "kept.webm", 10, 10, "done", "")
    app._on_task("https://x/gone.webm", "gone.webm", 10, 10, "done", "")
    from idm.config import verify_ignore_add
    verify_ignore_add(app.cfg, ["kept.webm"])
    stub = _MsgBoxStub()
    import unittest.mock as mock
    with mock.patch.object(G, "messagebox", stub):
        app.verify_quarantine_from_gui()
        _pump(app, stub)

    kinds = {}
    for iid in app.iids.values():
        kinds[app.tree.set(iid, "file")] = app.tree.set(iid, "status")
    assert kinds["kept.webm"] == "done", "ignored file never swept or warned"
    assert kinds["gone.webm"] == "moved"
    assert (out / "kept.webm").exists()
    assert (out / "quarantine" / "gone.webm").exists()
    log = app.log_text.get("1.0", "end")
    assert "1 on the ignore list" in log


def test_sweep_offer_can_add_ignore_entries(app, tmp_path):
    out = tmp_path / "dl"
    out.mkdir()
    (out / "kept.webm").write_bytes(PLAYLIST)
    app.out_var.set(str(out))
    stub = _MsgBoxStub(yes_to_ignore=True)
    import unittest.mock as mock
    with mock.patch.object(G, "messagebox", stub):
        app.verify_quarantine_from_gui()
        _pump(app, stub, want=2)

    from idm.config import verify_ignore_list
    assert verify_ignore_list(app.cfg) == ["kept.webm"], \
        "accepting the post-sweep offer persists the ignore entry"
    log = app.log_text.get("1.0", "end")
    assert "[ignore] kept.webm added" in log


def test_sweep_offer_declined_persists_nothing(app, tmp_path):
    out = tmp_path / "dl"
    out.mkdir()
    (out / "kept.webm").write_bytes(PLAYLIST)
    app.out_var.set(str(out))
    stub = _MsgBoxStub(yes_to_ignore=False)
    import unittest.mock as mock
    with mock.patch.object(G, "messagebox", stub):
        app.verify_quarantine_from_gui()
        _pump(app, stub, want=1)

    from idm.config import verify_ignore_list
    assert verify_ignore_list(app.cfg) == []
    assert any(kind == "yesno" and "ignore list" in msg
               for kind, msg in stub.calls), "the offer was made"
