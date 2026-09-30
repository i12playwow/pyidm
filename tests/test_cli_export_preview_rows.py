from __future__ import annotations

import json
import time

import pytest

from idm import gui as G
from idm.cli import main as cli_main

NOW = time.time()

ROWS = [
    {"path": f"C:/v/{n}.mkv", "ok": True, "dest": f"C:/v/{n}.en.srt", "language": "en",
     "provider": "subtitlecat", "size": 1000 + i, "cues": 100 + i, "ts": NOW}
    for i, n in enumerate(["A", "B", "C", "D", "E"])
]

RECORDS = {
    f"https://x/{n}.zip": {"status": "error", "filename": f"{n}.zip",
                           "size": 1024, "updated": NOW - i}
    for i, n in enumerate(["a", "b", "c", "d"])
}


# ------------------------------------------------------------------ helpers
def test_preview_rows_zero_suppresses():
    csv = G.history_to_csv(G.subtitle_rows(ROWS))
    assert G.export_preview(csv, max_rows=0) == "(suppressed)"
    assert G.export_preview_json("[]", max_rows=0) == "(suppressed)"


def test_preview_rows_negative_raises():
    with pytest.raises(ValueError):
        G.export_preview("a,b\n", max_rows=-1)
    with pytest.raises(ValueError):
        G.export_preview_json("[]", max_rows=-1)


def test_preview_rows_override_shows_more():
    csv = G.history_to_csv(G.subtitle_rows(ROWS))
    prev6 = G.export_preview(csv, max_rows=6)   # header + all 5 data rows
    assert all(f"{n}.mkv" in prev6 for n in "ABCDE")
    prev5 = G.export_preview(csv, max_rows=5)   # header + 4 data rows
    assert "D.mkv" in prev5 and "E.mkv" not in prev5
    assert G.export_preview(csv) == G.export_preview(csv, max_rows=3)  # default


def test_preview_json_rows_override():
    j = G.history_to_json(ROWS)
    prev2 = G.export_preview_json(j, max_rows=2)
    assert "subtitlecat:A.mkv" in prev2 and "subtitlecat:B.mkv" in prev2
    assert "C.mkv" not in prev2


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
    return " ".join(s.split())


def test_history_rows_6_in_preview(hist, tmp_path, capsys):
    out = tmp_path / "h.csv"
    assert cli_main(["history", "--out", str(out), "--rows", "6"]) == 0
    log = _flat(capsys.readouterr().out)
    assert all(f"{n}.mkv" in log for n in "ABCDE")   # header + 5 data rows


def test_history_rows_1_header_only(hist, tmp_path, capsys):
    out = tmp_path / "h.csv"
    assert cli_main(["history", "--out", str(out), "--rows", "1"]) == 0
    log = _flat(capsys.readouterr().out)
    assert "video,lang,provider,size,cues" in log
    assert "A.mkv" not in log


def test_history_json_rows_1_single_entry(hist, tmp_path, capsys):
    out = tmp_path / "h.json"
    assert cli_main(["history", "--json", "--out", str(out), "--rows", "1"]) == 0
    log = _flat(capsys.readouterr().out)
    assert "subtitlecat:A.mkv" in log and "B.mkv" not in log


def test_history_rows_0_suppresses_but_writes(hist, tmp_path, capsys):
    out = tmp_path / "h.csv"
    assert cli_main(["history", "--out", str(out), "--rows", "0"]) == 0
    log = _flat(capsys.readouterr().out)
    assert "preview: (suppressed)" in log
    assert out.exists() and "A.mkv" in out.read_text(encoding="utf-8")


def test_history_rows_negative_clean_error(hist, tmp_path, capsys):
    out = tmp_path / "h.csv"
    assert cli_main(["history", "--out", str(out), "--rows", "-1"]) == 1
    log = _flat(capsys.readouterr().out + capsys.readouterr().err)
    assert "invalid --rows: -1" in log
    assert not out.exists()  # nothing written before the check


# ---------------------------------------------------------------- downloads
@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sp = tmp_path / "downloads" / "idm.state.json"
    sp.parent.mkdir()
    sp.write_text(json.dumps({"version": 1, "downloads": RECORDS}), encoding="utf-8")
    return sp


def test_downloads_rows_1_header_only(state, tmp_path, capsys):
    out = tmp_path / "d.csv"
    assert cli_main(["downloads", "--out", str(out), "--rows", "1"]) == 0
    log = _flat(capsys.readouterr().out)
    assert "url,filename,status,size,updated" in log
    assert "a.zip" not in log


def test_downloads_rows_3_two_data_rows(state, tmp_path, capsys):
    out = tmp_path / "d.csv"
    assert cli_main(["downloads", "--out", str(out), "--rows", "3"]) == 0
    log = _flat(capsys.readouterr().out)
    assert "a.zip,error" in log and "b.zip,error" in log and "c.zip" not in log


def test_downloads_rows_negative_clean_error(state, tmp_path, capsys):
    out = tmp_path / "d.csv"
    assert cli_main(["downloads", "--out", str(out), "--rows", "-3"]) == 1
    log = _flat(capsys.readouterr().out + capsys.readouterr().err)
    assert "invalid --rows: -3" in log
    assert not out.exists()


# ------------------------------------------------------------------ parser
def test_parser_rows_default_is_3():
    import idm.cli as C
    for argv in (["history"], ["downloads"]):
        args = C.build_parser().parse_args(argv)
        assert args.rows == 3
