from __future__ import annotations

import json
import time

import pytest

from idm import gui as G
from idm.cli import build_parser
from idm.cli import main as cli_main

NOW = time.time()

ROWS = [
    {"path": "C:/v/A.mkv", "ok": True, "dest": "C:/v/A.en.srt", "language": "en",
     "provider": "subtitlecat", "size": 135_480, "cues": 1746, "ts": 1758600000},
    {"path": "C:/v/B.mp4", "ok": True, "dest": "C:/v/B.en.srt", "language": "en",
     "provider": "opensubtitles", "size": 51_200, "cues": 800, "ts": 1756000000},
]


@pytest.fixture
def hist(tmp_path, monkeypatch):
    p = tmp_path / "subs_history.json"
    p.write_text(json.dumps(ROWS), encoding="utf-8")
    monkeypatch.setattr(G, "SUBS_HISTORY_FILE", p)
    return p


# ------------------------------------------------------------------ formats
def test_table_lists_rows(hist, capsys):
    assert cli_main(["history"]) == 0
    out = capsys.readouterr().out
    assert "A.mkv" in out  # rich table wraps; keep assertions loose but real
    assert "subtitlecat" in out and "opensubtitles" in out
    assert "2 entries" in out


def test_csv_to_stdout(hist, capsys):
    assert cli_main(["history", "-f", "csv"]) == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[0] == "video,lang,provider,size,cues"
    assert "A.mkv,en,subtitlecat,135.5 KB,1746" in lines


def test_markdown_to_stdout(hist, capsys):
    assert cli_main(["history", "-f", "markdown"]) == 0
    out = capsys.readouterr().out
    assert out.lstrip().startswith("|")
    assert any("A.mkv" in ln and "|" in ln for ln in out.splitlines())


def test_out_csv_writes_file(hist, tmp_path, capsys):
    out = tmp_path / "h.csv"
    assert cli_main(["history", "--out", str(out), "--quiet"]) == 0
    raw = out.read_bytes()
    assert b"\r\n" in raw                              # byte-exact RFC-4180
    assert "exported 2 row(s)" in capsys.readouterr().out


def test_out_md_extension_picks_markdown(hist, tmp_path, capsys):
    out = tmp_path / "h.md"
    assert cli_main(["history", "--out", str(out), "--quiet"]) == 0
    assert out.read_text(encoding="utf-8").lstrip().startswith("|")
    assert "markdown" in capsys.readouterr().out


def test_explicit_fmt_beats_extension(hist, tmp_path, capsys):
    out = tmp_path / "h.csv"
    assert cli_main(["history", "-f", "markdown", "--out", str(out), "--quiet"]) == 0
    assert out.read_text(encoding="utf-8").lstrip().startswith("|")


# ------------------------------------------------------------------ filters
def test_provider_filter_case_insensitive(hist, capsys):
    assert cli_main(["history", "--provider", "SUBTITLECAT", "-f", "csv"]) == 0
    out = capsys.readouterr().out
    assert "A.mkv" in out and "B.mp4" not in out


def test_date_window(hist, capsys):
    # A is ~2025-09, B is ~2025-08 — window keeps only B
    assert cli_main(["history", "--since", "2025-01-01", "--until",
                     "2025-09-01", "-f", "csv"]) == 0
    out = capsys.readouterr().out
    assert "B.mp4" in out and "A.mkv" not in out


def test_filtered_footer_note(hist, capsys):
    assert cli_main(["history", "--provider", "subtitlecat"]) == 0
    assert "1 entry (filtered)" in capsys.readouterr().out


# ------------------------------------------------------------ edge cases
def test_empty_history_is_clean(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(G, "SUBS_HISTORY_FILE", tmp_path / "none.json")
    assert cli_main(["history"]) == 0
    out = capsys.readouterr().out
    assert "0 entries" in out


def test_bad_since_date_fails_cleanly(hist, capsys):
    assert cli_main(["history", "--since", "not-a-date"]) == 1
    out = capsys.readouterr().out
    assert "invalid --since" in out and "YYYY-MM-DD" in out


def test_subparsers_route_to_expected_handlers():
    p = build_parser()
    assert p.parse_args(["history"]).func.__name__ == "cmd_subs_history"
    assert p.parse_args(["subs", "f.mkv", "--no-play"]).func.__name__ == "cmd_subs"
