from __future__ import annotations

import json
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
    # chdir FIRST: the header reads ./downloads/idm.state.json at construction,
    # and the project's real store must never leak into these tests
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(G, "should_show_first_run", lambda cfg: False)
    monkeypatch.setattr(G, "SUBS_HISTORY_FILE", tmp_path / "subs_history.json")
    a = _make_app_with_retry()
    a.update()
    yield a
    a.destroy()


@pytest.fixture(autouse=True)
def _no_shell_open(monkeypatch):
    monkeypatch.setattr(G, "open_file_safe", lambda p: True)
    monkeypatch.setattr(G, "open_file_with", lambda p, v: True)


def test_read_state_records_missing_file_is_empty(tmp_path):
    assert G.read_state_records(tmp_path / "nope" / "idm.state.json") == {}


def test_read_state_records_never_creates_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    G.read_state_records()  # default path: ./downloads/idm.state.json
    assert not (tmp_path / "downloads").exists()  # a peek leaves no artifacts


def test_read_state_records_malformed_is_empty(tmp_path):
    p = tmp_path / "idm.state.json"
    p.write_text("{not json", encoding="utf-8")
    assert G.read_state_records(p) == {}
    p.write_text(json.dumps({"version": 1, "downloads": "not-a-dict"}),
                 encoding="utf-8")
    assert G.read_state_records(p) == {}


def test_read_state_records_parses_store(tmp_path):
    p = tmp_path / "idm.state.json"
    p.write_text(json.dumps({"version": 1, "downloads": {
        "https://x/a.zip": {"status": "error", "filename": "a.zip",
                            "size": 1024, "updated": time.time()}}}),
        encoding="utf-8")
    recs = G.read_state_records(p)
    assert list(recs) == ["https://x/a.zip"]
    assert recs["https://x/a.zip"]["status"] == "error"


def test_header_starts_zero_pending_when_no_state(app, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app.update()
    assert app.dl_status_var.get() == "0 pending"


def test_header_reflects_state_file(app, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sp = tmp_path / "downloads" / "idm.state.json"
    sp.parent.mkdir()
    sp.write_text(json.dumps({"version": 1, "downloads": {
        "https://x/a.zip": {"status": "error", "filename": "a.zip",
                            "size": 1024, "updated": time.time()},
        "https://x/b.bin": {"status": "downloading", "filename": "b.bin",
                            "size": 10, "updated": time.time()}}}),
        encoding="utf-8")
    app._refresh_dl_status()   # manual pass == what the 2s timer runs
    app.update()
    s = app.dl_status_var.get()
    assert s.startswith("2 pending")
    assert "downloading 1" in s and "error 1" in s
    assert "oldest from" in s


def test_refresh_reschedules_itself(app, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    scheduled = []
    monkeypatch.setattr(app, "after",
                        lambda ms, fn=None: scheduled.append((ms, fn)))
    app._refresh_dl_status()
    assert scheduled and scheduled[0][0] == 2000  # the 2s timer continues
