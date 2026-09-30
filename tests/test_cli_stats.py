from __future__ import annotations

import json
import time

import pytest

from idm import gui as G
from idm.cli import main as cli_main

NOW = time.time()

HIST = [
    {"path": "C:/v/A.mkv", "ok": True, "dest": "C:/v/A.en.srt", "language": "en",
     "provider": "subtitlecat", "size": 135_480, "cues": 1746, "ts": NOW - 100},
    {"path": "C:/v/B.mp4", "ok": True, "dest": "C:/v/B.en.srt", "language": "en",
     "provider": "opensubtitles", "size": 51_200, "cues": 800, "ts": NOW - 200},
    {"path": "C:/v/C.mkv", "ok": False, "message": "no subs found", "ts": NOW - 50},
]

RECORDS = {
    "https://x/a.zip": {"status": "error", "filename": "a.zip",
                        "size": 1024, "updated": NOW - 300},
    "https://x/b.bin": {"status": "downloading", "filename": "b.bin",
                        "size": 2048, "updated": NOW - 10},
    "https://x/c.iso": {"status": "error", "filename": "c.iso",
                        "size": 10, "updated": NOW - 60},
}


# ------------------------------------------------------------------ helpers
def test_downloads_status_line():
    s = G.downloads_status(RECORDS)
    assert s.startswith("3 pending") and "3.1 KB" in s   # human_size is SI
    assert "downloading 1" in s and "error 2" in s
    assert "oldest from" in s
    assert G.downloads_status({}) == "0 pending"


def test_downloads_total_bytes_skips_non_numeric():
    recs = {"u1": {"size": 100, "status": "x"},
            "u2": {"size": None, "status": "x"},
            "u3": {"status": "x"}}
    assert G.downloads_total_bytes(recs) == 100


def test_stats_payload_lossless_numbers():
    p = G.stats_payload(HIST, RECORDS)
    d, s = p["downloads"], p["subtitles"]
    assert d["pending"] == 3 and d["total_bytes"] == 1024 + 2048 + 10
    assert d["by_status"] == {"downloading": 1, "error": 2}
    assert d["oldest"]["url"] == "https://x/a.zip"
    assert s["entries"] == 3 and s["ok"] == 2 and s["failed"] == 1
    assert s["total_bytes"] == 135_480 + 51_200
    assert s["by_provider"] == {"opensubtitles": 1, "subtitlecat": 1}
    assert s["oldest"]["video"] == "B.mp4"          # oldest OK entry
    assert p["subtitles"]["path"] is None            # caller fills paths


def test_stats_payload_empty_stores():
    p = G.stats_payload([], {})
    assert p["downloads"] == {"path": None, "pending": 0, "total_bytes": 0,
                              "by_status": {}, "oldest": None}
    assert p["subtitles"]["entries"] == 0 and p["subtitles"]["oldest"] is None


# --------------------------------------------------------------------- CLI
@pytest.fixture
def stores(tmp_path, monkeypatch):
    """-o style layout: <tmp>/idm.state.json + a monkeypatched history file."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "idm.state.json").write_text(
        json.dumps({"version": 1, "downloads": RECORDS}), encoding="utf-8")
    hp = tmp_path / "subs.json"
    hp.write_text(json.dumps(HIST), encoding="utf-8")
    monkeypatch.setattr(G, "SUBS_HISTORY_FILE", hp)
    return tmp_path


@pytest.fixture(autouse=True)
def _no_shell(monkeypatch):
    monkeypatch.setattr(G, "open_file_safe", lambda p: True)
    monkeypatch.setattr(G, "open_file_with", lambda p, v: True)


def _flat(s: str) -> str:
    return " ".join(s.split())


def test_stats_text_output(stores, tmp_path, capsys):
    assert cli_main(["-o", str(stores), "stats"]) == 0
    log = _flat(capsys.readouterr().out)
    assert "Downloads" in log and "3 pending" in log and "3.1 KB" in log
    assert "error 2" in log and "downloading 1" in log
    assert "idm resume -o" in log
    assert "Subtitles" in log and "2 entries" in log
    assert "subtitlecat 1" in log and "opensubtitles 1" in log
    assert "idm history" in log


def test_stats_empty_stores(stores, capsys):
    (stores / "idm.state.json").unlink()
    (stores / "subs.json").write_text("[]", encoding="utf-8")
    assert cli_main(["-o", str(stores), "stats"]) == 0
    log = _flat(capsys.readouterr().out)
    assert "0 pending" in log and "0 entries" in log


def test_stats_json_lossless(stores, tmp_path, capsys):
    assert cli_main(["-o", str(stores), "stats", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["downloads"]["pending"] == 3
    assert payload["downloads"]["total_bytes"] == 3082
    assert payload["downloads"]["path"].endswith("idm.state.json")
    assert payload["subtitles"]["ok"] == 2
    assert payload["subtitles"]["failed"] == 1
    assert payload["subtitles"]["path"].endswith("subs.json")
    assert isinstance(payload["subtitles"]["total_bytes"], int)  # real numbers


def test_stats_json_no_status_markup(stores, capsys):
    out = capsys.readouterr()
    assert cli_main(["-o", str(stores), "stats", "--json"]) == 0
    out = capsys.readouterr()
    assert "[" not in out.out.split("{")[0]          # no rich markup before JSON


def test_stats_missing_files_are_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(G, "SUBS_HISTORY_FILE", tmp_path / "missing.json")
    assert cli_main(["-o", str(tmp_path), "stats"]) == 0
    log = _flat(capsys.readouterr().out)
    assert "0 pending" in log and "0 entries" in log


def test_stats_read_only(stores, tmp_path, capsys):
    sp = stores / "idm.state.json"
    before = sp.read_bytes()
    assert cli_main(["-o", str(stores), "stats"]) == 0
    assert cli_main(["-o", str(stores), "stats", "--json"]) == 0
    assert sp.read_bytes() == before                 # never mutates the store


# ------------------------------------------------------------------ parser
def test_parser_stats_json_default_false():
    import idm.cli as C
    assert C.build_parser().parse_args(["stats"]).json is False
    assert C.build_parser().parse_args(["stats", "--json"]).json is True
