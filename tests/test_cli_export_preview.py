from __future__ import annotations

import json
import time

import pytest

from idm import gui as G
from idm.cli import main as cli_main

NOW = time.time()

ROWS = [
    {"path": "C:/v/A.mkv", "ok": True, "dest": "C:/v/A.en.srt", "language": "en",
     "provider": "subtitlecat", "size": 135_480, "cues": 1746, "ts": NOW},
    {"path": "C:/v/B.mp4", "ok": True, "dest": "C:/v/B.en.srt", "language": "en",
     "provider": "opensubtitles", "size": 51_200, "cues": 800, "ts": NOW},
    {"path": "C:/v/C.mkv", "ok": True, "dest": "C:/v/C.en.srt", "language": "en",
     "provider": "subtitlecat", "size": 10, "cues": 2, "ts": NOW},
    {"path": "C:/v/D.mkv", "ok": True, "dest": "C:/v/D.en.srt", "language": "en",
     "provider": "subtitlecat", "size": 10, "cues": 2, "ts": NOW},
]

RECORDS = {
    "https://x/a.zip": {"status": "error", "filename": "a.zip",
                        "size": 1024, "updated": NOW},
    "https://x/b.bin": {"status": "downloading", "filename": "b.bin",
                        "size": 10, "updated": NOW - 60},
    "https://x/c.iso": {"status": "expired", "filename": "c.iso",
                        "size": 10, "updated": NOW - 120},
}


# ------------------------------------------------------------------ helpers
def test_preview_csv_first_rows():
    text = G.history_to_csv(G.subtitle_rows(ROWS))
    prev = G.export_preview(text)
    lines = text.splitlines()
    assert prev == " ".join(lines[:3])            # first 3 rows, verbatim
    assert "A.mkv" in prev and "B.mp4" in prev
    assert "C.mkv" not in prev                    # only the first 3 rows


def test_preview_markdown_skips_separator():
    text = G.history_to_markdown(G.subtitle_rows(ROWS))
    prev = G.export_preview(text)
    lines = text.splitlines()                     # header, |---|, data...
    assert prev == " ".join([lines[0], lines[2], lines[3]])
    assert "---" not in prev                      # separator never shown


def test_preview_truncates_long_lines():
    text = "\n".join(",".join(["x" * 100] * 2) for _ in range(2))
    prev = G.export_preview(text, max_rows=2)
    assert prev.count("…") == 2 and len(prev) < 130


def test_preview_caps_total_length():
    text = "\n".join(f"row-{i}," + "y" * 60 for i in range(6))
    prev = G.export_preview(text)
    cap = max(160, 3 * 55)  # default rows: cap grows with --rows (v1.11.12)
    assert prev.endswith("…") and len(prev) <= cap


def test_preview_empty_export():
    assert G.export_preview("") == "(empty)"
    assert G.export_preview("video,lang\n") == "video,lang"  # header is a row
    assert G.export_preview("\n  \n") == "(empty)"


def test_preview_json_summarizes_entries():
    j = G.history_to_json(ROWS)
    prev = G.export_preview_json(j)
    assert prev == "subtitlecat:A.mkv opensubtitles:B.mp4 subtitlecat:C.mkv"


def test_preview_json_empty_and_bad():
    assert G.export_preview_json("[]") == "(empty)"
    assert G.export_preview_json("") == "(empty)"
    assert G.export_preview_json("{not json") == "(unparseable)"


# ------------------------------------------------------------------ history
@pytest.fixture
def hist(tmp_path, monkeypatch):
    p = tmp_path / "subs_history.json"
    p.write_text(json.dumps(ROWS), encoding="utf-8")
    monkeypatch.setattr(G, "SUBS_HISTORY_FILE", p)
    return p


@pytest.fixture(autouse=True)
def _no_shell(monkeypatch):
    monkeypatch.setattr(G, "open_file_safe", lambda p: True)
    monkeypatch.setattr(G, "open_file_with", lambda p, v: True)


def _flat(s: str) -> str:
    """Collapse rich's terminal line-wrapping for substring checks."""
    return " ".join(s.split())


def test_history_csv_out_prints_preview(hist, tmp_path, capsys):
    out = tmp_path / "h.csv"
    assert cli_main(["history", "--out", str(out)]) == 0
    log = _flat(capsys.readouterr().out)
    assert "preview: " in log
    first_rows = " ".join(out.read_text(encoding="utf-8").splitlines()[:3])
    assert _flat(first_rows) in log               # preview mirrors the file


def test_history_json_out_prints_json_preview(hist, tmp_path, capsys):
    out = tmp_path / "h.json"
    assert cli_main(["history", "--out", str(out)]) == 0
    assert "subtitlecat:A.mkv" in capsys.readouterr().out


def test_stdout_modes_have_no_preview(hist, capsys):
    for argv in (["history"], ["history", "-f", "csv"],
                 ["history", "-f", "markdown"], ["history", "--json"]):
        assert cli_main(argv) == 0
        assert "preview:" not in capsys.readouterr().out


# ---------------------------------------------------------------- downloads
@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sp = tmp_path / "downloads" / "idm.state.json"
    sp.parent.mkdir()
    sp.write_text(json.dumps({"version": 1, "downloads": RECORDS}), encoding="utf-8")
    return sp


def test_downloads_out_prints_preview(state, tmp_path, capsys):
    out = tmp_path / "d.csv"
    assert cli_main(["downloads", "--out", str(out)]) == 0
    log = _flat(capsys.readouterr().out)
    assert "preview: " in log
    assert "a.zip,error,1.0 KB" in log


def test_downloads_preview_before_open(state, tmp_path, monkeypatch):
    from idm import cli as C
    order = []
    real_print = C.console.print

    def spy_print(*a, **kw):
        order.append(" ".join(str(x) for x in a))
        return real_print(*a, **kw)

    monkeypatch.setattr(C.console, "print", spy_print)
    monkeypatch.setattr(G, "open_file_safe", lambda p: order.append("OPEN") or True)
    assert cli_main(["downloads", "--out", str(tmp_path / "d.csv")]) == 0
    idx_preview = next(i for i, o in enumerate(order) if "preview:" in o)
    idx_open = order.index("OPEN")
    assert idx_open > idx_preview                 # preview precedes the open
    assert "opened" in order[-1]                  # open confirmation comes last


def test_downloads_empty_state_preview(state, tmp_path, capsys):
    (tmp_path / "downloads" / "idm.state.json").write_text(
        json.dumps({"version": 1, "downloads": {}}), encoding="utf-8")
    assert cli_main(["downloads", "--out", str(tmp_path / "e.csv")]) == 0
    assert "preview: url,filename,status,size,updated" in _flat(capsys.readouterr().out)
