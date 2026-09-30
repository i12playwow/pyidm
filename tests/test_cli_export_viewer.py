from __future__ import annotations

import json

import pytest

from idm import gui as G
from idm.cli import build_parser
from idm.cli import main as cli_main

ROWS = [
    {"path": "C:/v/A.mkv", "ok": True, "dest": "C:/v/A.en.srt", "language": "en",
     "provider": "subtitlecat", "size": 135_480, "cues": 1746},
]


@pytest.fixture
def hist(tmp_path, monkeypatch):
    p = tmp_path / "subs_history.json"
    p.write_text(json.dumps(ROWS), encoding="utf-8")
    monkeypatch.setattr(G, "SUBS_HISTORY_FILE", p)
    return p


@pytest.fixture
def viewers(monkeypatch):
    """Stub both open helpers; return (with_calls, safe_calls)."""
    with_calls, safe_calls = [], []
    monkeypatch.setattr(G, "open_file_with",
                        lambda p, v: with_calls.append((str(p), v)) or True)
    monkeypatch.setattr(G, "open_file_safe",
                        lambda p: safe_calls.append(str(p)) or True)
    return with_calls, safe_calls


# ------------------------------------------------------------- the helper
def test_helper_resolves_name_on_path(monkeypatch, tmp_path):
    import shutil
    monkeypatch.setattr(shutil, "which",
                        lambda name: "C:/fake/code.exe" if name == "code" else None)
    spawned = []
    monkeypatch.setattr(G.subprocess, "Popen", lambda *a, **k: spawned.append(a))
    f = tmp_path / "f.csv"
    f.write_text("x", encoding="utf-8")
    assert G.open_file_with(f, "code") is True
    assert spawned and spawned[0][0] == ["C:/fake/code.exe", str(f)]


def test_helper_resolves_existing_file_path(monkeypatch, tmp_path):
    import shutil
    monkeypatch.setattr(shutil, "which", lambda name: None)  # nothing on PATH
    exe = tmp_path / "myviewer.exe"
    exe.write_bytes(b"")                       # a real file, not on PATH
    spawned = []
    monkeypatch.setattr(G.subprocess, "Popen", lambda *a, **k: spawned.append(a))
    f = tmp_path / "f.csv"
    f.write_text("x", encoding="utf-8")
    assert G.open_file_with(f, str(exe)) is True
    assert spawned[0][0] == [str(exe), str(f)]


def test_helper_unknown_viewer_returns_false(monkeypatch, tmp_path):
    import shutil
    monkeypatch.setattr(shutil, "which", lambda name: None)
    spawned = []
    monkeypatch.setattr(G.subprocess, "Popen", lambda *a, **k: spawned.append(a))
    assert G.open_file_with(tmp_path / "f.csv", "nope-xyz") is False
    assert spawned == []


def test_helper_spawn_failure_returns_false(monkeypatch, tmp_path):
    import shutil
    monkeypatch.setattr(shutil, "which", lambda name: "C:/fake/app.exe")
    def boom(*a, **k):
        raise OSError("access denied")
    monkeypatch.setattr(G.subprocess, "Popen", boom)
    assert G.open_file_with(tmp_path / "f.csv", "app") is False


def test_helper_empty_viewer_returns_false(tmp_path):
    assert G.open_file_with(tmp_path / "f.csv", "   ") is False


# ---------------------------------------------------------------- history
def test_viewer_dispatches_to_open_file_with(hist, tmp_path, viewers, capsys):
    out = tmp_path / "h.md"
    assert cli_main(["history", "--out", str(out), "--viewer", "notepad"]) == 0
    with_calls, safe_calls = viewers
    assert with_calls == [(str(out), "notepad")] and safe_calls == []
    assert "in notepad" in capsys.readouterr().out


def test_no_viewer_uses_default_app(hist, tmp_path, viewers):
    out = tmp_path / "h.md"
    assert cli_main(["history", "--out", str(out)]) == 0
    with_calls, safe_calls = viewers
    assert with_calls == [] and safe_calls == [str(out)]


def test_quiet_beats_viewer(hist, tmp_path, viewers):
    out = tmp_path / "h.md"
    assert cli_main(["history", "--out", str(out), "--viewer", "notepad",
                     "--quiet"]) == 0
    with_calls, safe_calls = viewers
    assert with_calls == [] and safe_calls == []


def test_viewer_without_out_is_ignored(hist, viewers, capsys):
    assert cli_main(["history", "-f", "csv", "--viewer", "notepad"]) == 0
    with_calls, safe_calls = viewers
    assert with_calls == [] and safe_calls == []


def test_unknown_viewer_warns_but_export_succeeds(hist, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(G, "open_file_with", lambda p, v: False)
    out = tmp_path / "h.md"
    assert cli_main(["history", "--out", str(out), "--viewer", "ghost"]) == 0  # exit 0
    log = capsys.readouterr().out
    assert "could not open" in log and "ghost" in log and "succeeded" in log


# -------------------------------------------------------------- downloads
def test_downloads_viewer_dispatch(tmp_path, monkeypatch, viewers):
    monkeypatch.chdir(tmp_path)
    sp = tmp_path / "downloads" / "idm.state.json"
    sp.parent.mkdir()
    sp.write_text(json.dumps(
        {"version": 1,
         "downloads": {"https://x/a": {"status": "error", "updated": 1758000000}}}),
        encoding="utf-8")
    out = tmp_path / "d.csv"
    assert cli_main(["downloads", "--out", str(out), "--viewer", "code"]) == 0
    with_calls, safe_calls = viewers
    assert with_calls == [(str(out), "code")] and safe_calls == []


# ----------------------------------------------------------------- parser
def test_viewer_defaults_and_parses():
    p = build_parser()
    assert p.parse_args(["history"]).viewer is None
    assert p.parse_args(["history", "--viewer", "notepad"]).viewer == "notepad"
    assert p.parse_args(["downloads", "--viewer", "code"]).viewer == "code"
