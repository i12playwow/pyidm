from __future__ import annotations

import json
import time

import pytest

from idm.cli import build_parser
from idm.cli import main as cli_main

NOW = time.time()

RECORDS = {
    "https://x/a.zip": {"status": "error", "message": "connection reset",
                        "filename": "a.zip", "size": 1_048_576,
                        "updated": NOW - 2 * 86400},
    "https://x/b.bin": {"status": "downloading", "filename": "b.bin",
                        "size": 3_145_728, "updated": NOW - 3600},
    "https://x/c.iso": {"status": "expired", "filename": "c.iso",
                        "size": None, "updated": None},  # no timestamp
}


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sp = tmp_path / "downloads" / "idm.state.json"   # default out_dir is ./downloads
    sp.parent.mkdir()
    sp.write_text(json.dumps({"version": 1, "downloads": RECORDS}), encoding="utf-8")
    return sp


# ------------------------------------------------------------------ formats
def test_table_lists_rows(state, capsys):
    assert cli_main(["downloads"]) == 0
    out = capsys.readouterr().out
    assert "a.zip" in out and "b.bin" in out
    assert "error" in out and "downloading" in out
    assert "3 unfinished downloads" in out
    assert "idm resume" in out  # footer points the way back


def test_table_rows_newest_first(state, capsys):
    assert cli_main(["downloads"]) == 0
    out = capsys.readouterr().out
    assert out.index("b.bin") < out.index("a.zip") < out.index("c.iso")


def test_csv_to_stdout(state, capsys):
    assert cli_main(["downloads", "-f", "csv"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "url,filename,status,size,updated"
    assert any(ln.startswith("https://x/a.zip,a.zip,error,1.0 MB,") for ln in lines)
    assert any(ln.startswith("https://x/c.iso,c.iso,expired,,") for ln in lines)


def test_markdown_to_stdout(state, capsys):
    assert cli_main(["downloads", "-f", "markdown"]) == 0
    out = capsys.readouterr().out
    assert out.lstrip().startswith("|")
    assert "| url | filename | status | size | updated |" in out


def test_out_csv_writes_file(state, tmp_path, capsys):
    out = tmp_path / "state.csv"
    assert cli_main(["downloads", "--out", str(out), "--quiet"]) == 0
    raw = out.read_bytes()
    assert raw.splitlines()[0] == b"url,filename,status,size,updated"
    assert b"\r\n" in raw                              # byte-exact RFC-4180
    assert "exported 3 row(s)" in capsys.readouterr().out


def test_out_md_extension_picks_markdown(state, tmp_path, capsys):
    out = tmp_path / "state.md"
    assert cli_main(["downloads", "--export", str(out), "--quiet"]) == 0
    assert out.read_text(encoding="utf-8").lstrip().startswith("|")
    assert "markdown" in capsys.readouterr().out


def test_explicit_fmt_beats_extension(state, tmp_path):
    out = tmp_path / "state.csv"
    assert cli_main(["downloads", "-f", "markdown", "--out", str(out), "--quiet"]) == 0
    assert out.read_text(encoding="utf-8").lstrip().startswith("|")


# ------------------------------------------------------------------ filters
def test_status_filter_case_insensitive(state, capsys):
    assert cli_main(["downloads", "--status", "ERROR", "-f", "csv"]) == 0
    out = capsys.readouterr().out
    assert "a.zip" in out and "b.bin" not in out and "c.iso" not in out


def test_date_window_excludes_undated(state, capsys):
    # a is 2 days old, b is 1 hour old, c is undated — only b is in the window
    since = time.strftime("%Y-%m-%d", time.localtime(NOW - 86400))
    assert cli_main(["downloads", "--since", since, "-f", "csv"]) == 0
    out = capsys.readouterr().out
    assert "b.bin" in out and "a.zip" not in out and "c.iso" not in out


def test_filtered_footer_note(state, capsys):
    assert cli_main(["downloads", "--status", "error"]) == 0
    assert "1 unfinished download (filtered)" in capsys.readouterr().out


# --------------------------------------------------------------- edge cases
def test_empty_state_is_clean(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli_main(["downloads"]) == 0
    out = capsys.readouterr().out
    assert "0 unfinished downloads" in out


def test_bad_since_date_fails_cleanly(state, capsys):
    assert cli_main(["downloads", "--since", "not-a-date"]) == 1
    out = capsys.readouterr().out
    assert "invalid --since" in out and "YYYY-MM-DD" in out


def test_listing_is_read_only(state):
    # the listing must never touch the state file (resume semantics are separate)
    before = state.read_bytes()
    for argv in (["downloads"], ["downloads", "-f", "csv"],
                 ["downloads", "-f", "markdown"],
                 ["downloads", "--status", "error", "--out", "x.csv", "--quiet"]):
        assert cli_main(argv) in (0, 1)
    assert state.read_bytes() == before


def test_subparsers_route_to_expected_handlers():
    p = build_parser()
    assert p.parse_args(["downloads"]).func.__name__ == "cmd_downloads"
    assert p.parse_args(["downloads", "--out", "x.csv"]).export == "x.csv"
    assert p.parse_args(["get", "-o", "d", "http://x"]).out == "d"  # global -o intact
