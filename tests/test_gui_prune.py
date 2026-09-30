"""GUI downloads-tab right-click menu: 'Prune stale records…' — the GUI
twin of 'idm prune-state', sharing the exact idm.state engine
(scan_stale_records / prune_stale_records). The preview arrives as a
'prune_done' event and offers the apply; the result lands as
'prune_apply_done'. Files are never touched; records are re-checked at
apply time, so a file that reappeared keeps its record."""
from __future__ import annotations

import json
import time

import pytest

gui = pytest.importorskip("idm.gui")
from idm import gui as G
from idm.state import State


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
    """Records messagebox calls (tests never block on a real dialog);
    askyesno answers False by default so the apply offer is declined
    unless a test opts in via `answers`."""

    def __init__(self, answers: dict[str, bool] | None = None):
        self.calls: list[tuple[str, str]] = []
        self.answers = dict(answers or {})

    def showinfo(self, title, message, **kw):
        self.calls.append(("info", message))

    def showwarning(self, title, message, **kw):
        self.calls.append(("warning", message))

    def showerror(self, title, message, **kw):
        self.calls.append(("error", message))

    def askyesno(self, title, message, **kw):
        self.calls.append(("yesno", message))
        for needle, answer in self.answers.items():
            if needle in message:
                return answer
        return False


def _pump(app, stub, seconds: float = 5.0) -> None:
    """Pump the Tk event loop until the worker thread's popup has arrived
    (the action runs in a background thread and reports via events)."""
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


def _seed(out, stale: str = "gone.webm", live: str | None = "here.mp4"):
    st = State(out / "idm.state.json")
    st.set(f"https://x/{stale}", status="done", filename=stale, size=1024)
    if live:
        st.set(f"https://x/{live}", status="done", filename=live, size=2048)
        (out / live).write_bytes(b"\x00" * 8)


def test_tree_menu_has_prune_action(app):
    last = app.tree_menu.index("end")
    labels = [app.tree_menu.entrycget(i, "label") for i in range(last + 1)]
    assert any("Prune stale records" in l for l in labels)


def test_prune_preview_offers_apply_and_decline_keeps_state(app, tmp_path,
                                                            monkeypatch):
    out = tmp_path / "dl"
    out.mkdir()
    _seed(out)
    app.out_var.set(str(out))
    stub = _MsgBoxStub()                    # askyesno -> False: decline
    monkeypatch.setattr(G, "messagebox", stub)

    app.prune_stale_from_gui()
    _pump(app, stub)

    kinds = [k for k, _ in stub.calls]
    assert "yesno" in kinds, "the preview must offer the apply"
    message = next(m for k, m in stub.calls if k == "yesno")
    assert "1 stale" in message and "gone.webm" in message
    assert "Files are never touched" in message
    assert len(State(out / "idm.state.json").records()) == 2, "decline = no-op"


def test_prune_accept_applies_and_reports(app, tmp_path, monkeypatch):
    out = tmp_path / "dl"
    out.mkdir()
    _seed(out)
    app.out_var.set(str(out))
    stub = _MsgBoxStub(answers={"Remove these records": True})
    monkeypatch.setattr(G, "messagebox", stub)

    app.prune_stale_from_gui()
    deadline = time.time() + 8
    while time.time() < deadline and not any(
            k == "info" and "removed" in m for k, m in stub.calls):
        app.update()
        time.sleep(0.05)
    app.update()

    records = State(out / "idm.state.json").records()
    assert "https://x/gone.webm" not in records, "stale record pruned"
    assert "https://x/here.mp4" in records, "live record kept"
    assert (out / "here.mp4").exists(), "files are never touched"
    applied = next(m for k, m in stub.calls
                   if k == "info" and "removed" in m)
    assert "1 stale record(s) removed" in applied and "1 record(s) kept" in applied
    logs = app.log_text.get("1.0", "end")
    assert "removed gone.webm" in logs


def test_prune_clean_state_shows_info_not_offer(app, tmp_path, monkeypatch):
    out = tmp_path / "dl"
    out.mkdir()
    _seed(out, stale="gone.webm", live=None)
    (out / "gone.webm").write_bytes(b"\x00" * 4)      # file exists: not stale
    app.out_var.set(str(out))
    stub = _MsgBoxStub()
    monkeypatch.setattr(G, "messagebox", stub)

    app.prune_stale_from_gui()
    _pump(app, stub)

    assert stub.calls and stub.calls[0][0] == "info", "no apply offer when clean"
    assert "Nothing to prune" in stub.calls[0][1]
    assert len(State(out / "idm.state.json").records()) == 1


def test_prune_state_file_missing_is_info(app, tmp_path, monkeypatch):
    out = tmp_path / "dl"
    out.mkdir()
    app.out_var.set(str(out))
    stub = _MsgBoxStub()
    monkeypatch.setattr(G, "messagebox", stub)

    app.prune_stale_from_gui()
    _pump(app, stub)

    assert stub.calls and stub.calls[0][0] == "info"
    assert "0 record(s)" in stub.calls[0][1] and "Nothing to prune" in stub.calls[0][1]
    assert not (out / "idm.state.json").exists(), "a scan never creates a state file"


def test_prune_recheck_keeps_record_whose_file_reappeared(app, tmp_path,
                                                          monkeypatch):
    out = tmp_path / "dl"
    out.mkdir()
    _seed(out)
    app.out_var.set(str(out))
    stub = _MsgBoxStub(answers={"Remove these records": True})
    monkeypatch.setattr(G, "messagebox", stub)
    orig_ask = stub.askyesno

    def _reappear_then_answer(title, message, **kw):
        # the file comes back between the preview and the accepted apply:
        # the apply-time recheck must keep the record
        if "Remove these records" in message:
            (out / "gone.webm").write_bytes(b"\x00" * 4)
        return orig_ask(title, message, **kw)

    stub.askyesno = _reappear_then_answer
    app.prune_stale_from_gui()
    # pump until the APPLY result popup arrives (the preview yesno alone
    # does not mean the background apply has finished)
    deadline = time.time() + 8
    while time.time() < deadline and not any(
            k == "info" and "removed" in m for k, m in stub.calls):
        app.update()
        time.sleep(0.05)
    app.update()

    records = State(out / "idm.state.json").records()
    assert "https://x/gone.webm" in records, "re-checked at apply: record kept"
    assert len(records) == 2
    applied = next(m for k, m in stub.calls if k == "info" and "removed" in m)
    assert applied.startswith("0 stale record(s) removed")


def test_gui_resume_skips_ghosts_and_hints_prune(app, tmp_path, monkeypatch):
    """Tools ▸ Resume unfinished downloads: ghosts are skipped (never
    retried), the log points at prune, and live records still run."""
    from unittest.mock import patch

    out = tmp_path / "dl"
    out.mkdir()
    st = State(out / "idm.state.json")
    st.set("https://x/gone.webm", status="error", filename="gone.webm")
    st.set("https://x/pending.mp4", status="pending", filename="pending.mp4")
    (out / "pending.mp4.part").write_bytes(b"part")   # resumable, not a ghost
    app.out_var.set(str(out))
    stub = _MsgBoxStub()
    monkeypatch.setattr(G, "messagebox", stub)
    app.running = False

    def fake_download_batch(jobs):
        assert [j[1] for j in jobs] == ["pending.mp4"], \
            "only the resumable record is retried"
        return []

    with patch.object(G.Downloader, "download_batch",
                      side_effect=fake_download_batch):
        app.resume_from_gui()
        deadline = time.time() + 8
        while time.time() < deadline and not app.log_text.get("1.0", "end").count(
                "resuming"):
            app.update()
            time.sleep(0.05)
        app.update()

    logs = app.log_text.get("1.0", "end")
    assert "[skip] gone.webm" in logs and "idm prune-state" in logs
    assert "resuming 1 download(s)" in logs and "1 skipped: file missing" in logs


def test_gui_resume_all_ghosts_shows_warning(app, tmp_path, monkeypatch):
    out = tmp_path / "dl"
    out.mkdir()
    State(out / "idm.state.json").set("https://x/gone.webm", status="error",
                                      filename="gone.webm")
    app.out_var.set(str(out))
    stub = _MsgBoxStub()
    monkeypatch.setattr(G, "messagebox", stub)

    app.resume_from_gui()
    app.update()

    assert stub.calls and stub.calls[0][0] == "warning"
    assert "1 record(s) skipped" in stub.calls[0][1]
    assert "idm prune-state" in stub.calls[0][1]
    assert app.running is False, "no batch was started"


def test_prune_json_state_round_trip(tmp_path):
    """The engine the GUI calls returns the documented payload shape."""
    from idm.state import scan_stale_records

    out = tmp_path / "dl"
    out.mkdir()
    _seed(out)
    payload = scan_stale_records(out / "idm.state.json")
    assert payload["action"] == "scan" and payload["stale"] == 1
    json.dumps(payload)
