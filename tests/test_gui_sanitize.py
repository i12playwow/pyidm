"""Tools ▸ 'Sanitize downloads names (dry run)': the GUI twin of
'idm sanitize-names' — scans the Save-to folder for names a mangled
Content-Disposition produced, logs each pending rename, and offers to
apply it (a second background pass renames in place, never overwriting).
Shares the exact same gui_common engine as the CLI (headless parity test
at the bottom)."""
from __future__ import annotations

import time

import pytest

gui = pytest.importorskip("idm.gui")
from idm import gui as G

PLAYLIST = b"#EXTM3U\n#EXT-X-TARGETDURATION:10\n" + b"\x00" * 13
MP4_HEAD = b"\x00\x00\x00\x18ftypmp44" + b"\x00" * 24
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
    """Records messagebox calls; askyesno returns the scripted answer."""

    def __init__(self, answer: bool = True):
        self.answer = bool(answer)
        self.calls: list[tuple[str, str]] = []

    def showinfo(self, title, message, **kw):
        self.calls.append(("info", message))

    def showwarning(self, title, message, **kw):
        self.calls.append(("warning", message))

    def showerror(self, title, message, **kw):
        self.calls.append(("error", message))

    def askyesno(self, title, message, **kw):
        self.calls.append(("yesno", message))
        return self.answer


def _pump(app, stub, maxcount: int = 1, seconds: float = 5.0) -> None:
    """Pump the Tk event loop until the expected popups have arrived (the
    scan and the apply run in background threads and report via events),
    then drain whatever is still queued."""
    deadline = time.time() + seconds
    while time.time() < deadline and len(stub.calls) < maxcount:
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


# ------------------------------------------------------------------- the menu
def test_tools_menu_offers_sanitize_dry_run(app):
    menubar = app.nametowidget(app.cget("menu"))
    tools = None
    for i in range(menubar.index("end") + 1):
        if menubar.type(i) == "cascade" and \
                menubar.entrycget(i, "label") == "Tools":
            tools = app.nametowidget(menubar.entrycget(i, "menu"))
    assert tools is not None, "no Tools cascade in the menubar"
    labels = [tools.entrycget(i, "label")
              for i in range(tools.index("end") + 1)
              if tools.type(i) == "command"]
    # NB: no "downloads" substring — test_tools_menu_lists_report_presets
    # asserts the Tools menu never mentions the non-report preset kinds
    assert any("Sanitize" in l and "dry run" in l for l in labels), labels


# ------------------------------------------------------------------ dry run
def test_dry_run_offers_to_apply_and_renames_on_yes(app, tmp_path, monkeypatch):
    out = tmp_path / "dl"
    out.mkdir()
    (out / MANGLED).write_bytes(PLAYLIST)
    (out / "real.mp4").write_bytes(MP4_HEAD)
    app.out_var.set(str(out))
    stub = _MsgBoxStub(answer=True)
    monkeypatch.setattr(G, "messagebox", stub)

    app.sanitize_names_from_gui()
    _pump(app, stub, maxcount=2)
    app._poll()

    assert [kind for kind, _ in stub.calls] == ["yesno", "info"]
    assert "2" in stub.calls[0][1] and "1" in stub.calls[0][1], \
        "the offer should say how many files are mangled"
    assert (out / CLEAN).read_bytes() == PLAYLIST, "rename applied"
    assert not (out / MANGLED).exists()
    assert (out / "real.mp4").exists(), "clean file untouched"
    log = app.log_text.get("1.0", "end")
    assert f"[rename] {MANGLED} -> {CLEAN}" in log
    assert "1 file(s) renamed" in stub.calls[-1][1]


def test_dry_run_declined_leaves_files_alone(app, tmp_path, monkeypatch):
    out = tmp_path / "dl"
    out.mkdir()
    (out / MANGLED).write_bytes(PLAYLIST)
    app.out_var.set(str(out))
    stub = _MsgBoxStub(answer=False)
    monkeypatch.setattr(G, "messagebox", stub)

    app.sanitize_names_from_gui()
    _pump(app, stub, maxcount=1)
    app._poll()

    assert [kind for kind, _ in stub.calls] == ["yesno"]
    assert (out / MANGLED).read_bytes() == PLAYLIST, "decline renames nothing"
    assert not (out / CLEAN).exists()
    log = app.log_text.get("1.0", "end")
    assert f"[rename] {MANGLED} -> {CLEAN}" in log, \
        "the dry run still logs the pending renames"


def test_dry_run_clean_folder_shows_info_only(app, tmp_path, monkeypatch):
    out = tmp_path / "dl"
    out.mkdir()
    (out / "real.mp4").write_bytes(MP4_HEAD)
    app.out_var.set(str(out))
    stub = _MsgBoxStub(answer=True)
    monkeypatch.setattr(G, "messagebox", stub)

    app.sanitize_names_from_gui()
    _pump(app, stub, maxcount=1)
    app._poll()

    assert [kind for kind, _ in stub.calls] == ["info"]
    assert "clean names" in stub.calls[0][1]
    log = app.log_text.get("1.0", "end")
    assert "clean names" in log


def test_dry_run_missing_folder_is_just_info(app, tmp_path, monkeypatch):
    out = tmp_path / "dl"          # never created
    app.out_var.set(str(out))
    stub = _MsgBoxStub(answer=True)
    monkeypatch.setattr(G, "messagebox", stub)

    app.sanitize_names_from_gui()
    _pump(app, stub, maxcount=1)
    app._poll()

    assert [kind for kind, _ in stub.calls] == ["info"], \
        "a missing Save-to folder is not an error (0 files scanned)"


# ------------------------------------------------------------------- apply
def test_apply_failure_reports_error_and_keeps_file(app, tmp_path, monkeypatch):
    out = tmp_path / "dl"
    out.mkdir()
    (out / MANGLED).write_bytes(PLAYLIST)
    app.out_var.set(str(out))
    stub = _MsgBoxStub(answer=True)
    monkeypatch.setattr(G, "messagebox", stub)

    real_rename = type(tmp_path).rename

    def locked_rename(self, target):
        raise OSError("locked")

    monkeypatch.setattr(type(tmp_path), "rename", locked_rename)
    try:
        app.sanitize_names_from_gui()
        _pump(app, stub, maxcount=2)
        app._poll()
    finally:
        monkeypatch.setattr(type(tmp_path), "rename", real_rename)

    assert [kind for kind, _ in stub.calls] == ["yesno", "error"]
    assert (out / MANGLED).read_bytes() == PLAYLIST, "failed rename kept the file"
    log = app.log_text.get("1.0", "end")
    assert "[error]" in log and "locked" in log


# ------------------------------------------------- headless engine parity
def test_gui_common_sanitize_helpers_match_cli_payloads(tmp_path):
    """The Tools menu and 'idm sanitize-names' share one engine; payloads
    and on-disk effects must agree (same keys the CLI's --json reports)."""
    from idm.gui_common import apply_name_fixes, scan_unclean_names

    (tmp_path / MANGLED).write_bytes(PLAYLIST)
    (tmp_path / "real.mp4").write_bytes(MP4_HEAD)
    (tmp_path / "idm.state.json").write_text("{}", encoding="utf-8")

    scan = scan_unclean_names(tmp_path)
    assert (scan["scanned"], scan["unclean"]) == (2, 1), \
        "idm.state.json is neither scanned nor flagged"
    assert (scan["renamed"], scan["failed"]) == (0, 0)
    assert scan["files"] == [{"file": MANGLED, "to": CLEAN,
                              "action": "would-rename"}]
    assert (tmp_path / MANGLED).exists(), "scan renames nothing"

    payload, renames = apply_name_fixes(tmp_path, scan=scan)
    assert renames == [(MANGLED, CLEAN)]
    assert payload["renamed"] == 1 and payload["failed"] == 0
    assert payload["files"][0]["action"] == "renamed"
    assert {"file", "to", "action"} == set(payload["files"][0]), \
        "same keys as the CLI's --json payload"
    assert (tmp_path / CLEAN).read_bytes() == PLAYLIST
    assert not (tmp_path / MANGLED).exists()

    # a second, fresh scan finds nothing; applying a collision-safe name
    # never overwrites
    (tmp_path / MANGLED).write_bytes(PLAYLIST)
    (tmp_path / CLEAN).write_bytes(b"do-not-clobber")
    payload2, renames2 = apply_name_fixes(tmp_path)
    assert renames2 == [(MANGLED, "130425,_360p (2).mp4")]
    assert (tmp_path / CLEAN).read_bytes() == b"do-not-clobber"
    assert (tmp_path / "130425,_360p (2).mp4").read_bytes() == PLAYLIST
