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

RECORDS = {"https://x/a.zip": {"status": "error", "filename": "a.zip",
                               "size": 1024, "updated": 1758000000}}


@pytest.fixture
def hist(tmp_path, monkeypatch):
    p = tmp_path / "subs_history.json"
    p.write_text(json.dumps(ROWS), encoding="utf-8")
    monkeypatch.setattr(G, "SUBS_HISTORY_FILE", p)
    return p


@pytest.fixture
def opens(monkeypatch):
    """Track open_file_safe calls without touching the real shell."""
    calls = []
    monkeypatch.setattr(G, "open_file_safe", lambda p: calls.append(str(p)) or True)
    return calls


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sp = tmp_path / "downloads" / "idm.state.json"
    sp.parent.mkdir()
    sp.write_text(json.dumps({"version": 1, "downloads": RECORDS}), encoding="utf-8")
    return sp


# ------------------------------------------------------------------ history
def test_out_opens_export(hist, tmp_path, opens, capsys):
    out = tmp_path / "h.csv"
    assert cli_main(["history", "--out", str(out)]) == 0
    assert opens == [str(out)]
    log = capsys.readouterr().out
    assert "opened" in log and "--quiet" in log


def test_quiet_suppresses_open(hist, tmp_path, opens, capsys):
    out = tmp_path / "h.csv"
    assert cli_main(["history", "--out", str(out), "--quiet"]) == 0
    assert opens == []
    assert "opened" not in capsys.readouterr().out


def test_open_failure_warns_but_export_succeeds(hist, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(G, "open_file_safe", lambda p: False)
    out = tmp_path / "h.csv"
    assert cli_main(["history", "--out", str(out)]) == 0          # exit 0
    log = capsys.readouterr().out
    assert "could not open" in log and "succeeded" in log


def test_stdout_modes_never_open(hist, opens, capsys):
    for argv in (["history"], ["history", "-f", "csv"],
                 ["history", "-f", "markdown"], ["history", "--json"]):
        assert cli_main(argv) == 0
        capsys.readouterr()
    assert opens == []


# ---------------------------------------------------------------- downloads
def test_downloads_out_opens_export(state, tmp_path, opens, capsys):
    out = tmp_path / "d.csv"
    assert cli_main(["downloads", "--out", str(out)]) == 0
    assert opens == [str(out)]


def test_downloads_quiet_suppresses_open(state, tmp_path, opens):
    assert cli_main(["downloads", "--out", str(tmp_path / "d.csv"), "--quiet"]) == 0
    assert opens == []


# ------------------------------------------------------------ the helper
def test_open_file_safe_reports_success(monkeypatch):
    seen = []
    monkeypatch.setattr(G, "open_file", lambda p: seen.append(p))
    assert G.open_file_safe("x.csv") is True
    assert seen == ["x.csv"]


def test_open_file_safe_reports_failure(monkeypatch):
    def boom(p):
        raise OSError("no association")
    monkeypatch.setattr(G, "open_file", boom)
    assert G.open_file_safe("x.csv") is False


# ------------------------------------------------------------------ parser
def test_quiet_defaults_false_and_parses():
    p = build_parser()
    assert p.parse_args(["history"]).quiet is False
    assert p.parse_args(["history", "--quiet"]).quiet is True
    assert p.parse_args(["downloads"]).quiet is False
    assert p.parse_args(["downloads", "--quiet"]).quiet is True
