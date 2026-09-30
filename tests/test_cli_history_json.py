from __future__ import annotations

import json
import time

import pytest

from idm.cli import main as cli_main

NOW = time.time()

ROWS = [
    {"path": "C:/v/A.mkv", "ok": True, "dest": "C:/v/A.en.srt", "language": "en",
     "provider": "subtitlecat", "size": 135_480, "cues": 1746, "ts": NOW - 86400},
    {"path": "C:/v/B.mp4", "ok": True, "dest": "C:/v/B.en.srt", "language": "en",
     "provider": "opensubtitles", "size": 51_200, "cues": 800, "ts": NOW - 7200},
    {"path": "C:/v/C.mkv", "ok": False, "message": "all providers failed", "ts": NOW},
]


@pytest.fixture
def hist(tmp_path, monkeypatch):
    p = tmp_path / "subs_history.json"
    p.write_text(json.dumps(ROWS), encoding="utf-8")
    monkeypatch.setattr("idm.gui.SUBS_HISTORY_FILE", p)
    return p


# ------------------------------------------------------------------- stdout
def test_json_stdout_is_lossless_array(hist, capsys):
    assert cli_main(["history", "--json"]) == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert isinstance(data, list) and len(data) == 2
    a = next(r for r in data if r["provider"] == "subtitlecat")
    assert a["path"] == "C:/v/A.mkv" and a["dest"] == "C:/v/A.en.srt"
    assert a["size"] == 135_480 and a["cues"] == 1746   # numeric, not humanized
    assert isinstance(a["ts"], (int, float))            # timestamps preserved


def test_json_excludes_failures(hist, capsys):
    assert cli_main(["history", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert all(r.get("ok") for r in data)
    assert not any(r.get("path") == "C:/v/C.mkv" for r in data)


def test_f_json_equals_json_flag(hist, capsys):
    a = json.loads(_run(hist, ["history", "--json"]))
    b = json.loads(_run(hist, ["history", "-f", "json"]))
    assert a == b


def _run(hist, argv):
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert cli_main(argv) == 0
    return buf.getvalue()


def test_json_respects_filters(hist, capsys):
    assert cli_main(["history", "--json", "--provider", "SUBTITLECAT"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data) == 1 and data[0]["provider"] == "subtitlecat"


def test_json_output_parses_after_rich_import(hist, capsys):
    # json must be plain stdout (no rich decorations) even though the module
    # also prints rich tables in other modes
    out = _run(hist, ["history", "--json"])
    assert out.lstrip().startswith("[")


# ------------------------------------------------------------------- --out
def test_json_to_file(hist, tmp_path, capsys):
    out = tmp_path / "h.json"
    assert cli_main(["history", "--json", "--out", str(out), "--quiet"]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert len(data) == 2
    log = capsys.readouterr().out
    assert "exported 2 row(s)" in log and "(json)" in log


def test_json_wins_over_extension_sniffing(hist, tmp_path, capsys):
    out = tmp_path / "h.md"                      # .md would normally pick markdown
    assert cli_main(["history", "--json", "--out", str(out), "--quiet"]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))[0]["provider"]


def test_out_json_extension_sniffs_without_flags(hist, tmp_path, capsys):
    out = tmp_path / "h.json"                    # --fmt omitted, .json extension
    assert cli_main(["history", "--out", str(out), "--quiet"]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert len(data) == 2 and data[0]["size"] == 135_480
    assert "(json)" in capsys.readouterr().out


# -------------------------------------------------------------- edge cases
def test_empty_history_json_is_empty_array(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("idm.gui.SUBS_HISTORY_FILE", tmp_path / "none.json")
    assert cli_main(["history", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_bad_date_still_fails_cleanly(hist, capsys):
    assert cli_main(["history", "--json", "--since", "junk"]) == 1
    assert "invalid --since" in capsys.readouterr().out
