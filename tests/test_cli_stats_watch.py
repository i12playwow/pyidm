from __future__ import annotations

import json

import pytest

from idm import gui as G
from idm.cli import main as cli_main


def _make_app(tmp_path, monkeypatch, hist="[]"):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "subs.json").write_text(hist, encoding="utf-8")
    monkeypatch.setattr(G, "SUBS_HISTORY_FILE", tmp_path / "subs.json")
    return tmp_path


@pytest.fixture(autouse=True)
def _no_shell(monkeypatch):
    monkeypatch.setattr(G, "open_file_safe", lambda p: True)
    monkeypatch.setattr(G, "open_file_with", lambda p, v: True)


def _flat(s: str) -> str:
    return " ".join(s.split())


# ------------------------------------------------------------------ parser
def test_parser_watch_default_none():
    import idm.cli as C
    assert C.build_parser().parse_args(["stats"]).watch is None
    assert C.build_parser().parse_args(["stats", "--watch", "3"]).watch == 3


# -------------------------------------------------------------- validation
def test_watch_zero_clean_error(tmp_path, monkeypatch, capsys):
    d = _make_app(tmp_path, monkeypatch)
    assert cli_main(["-o", str(d), "stats", "--watch", "0"]) == 1
    assert "invalid --watch: 0" in _flat(capsys.readouterr().out)


def test_watch_negative_clean_error(tmp_path, monkeypatch, capsys):
    d = _make_app(tmp_path, monkeypatch)
    assert cli_main(["-o", str(d), "stats", "--watch", "-1"]) == 1
    assert "invalid --watch: -1" in _flat(capsys.readouterr().out)


def test_watch_with_json_rejected(tmp_path, monkeypatch, capsys):
    d = _make_app(tmp_path, monkeypatch)
    assert cli_main(["-o", str(d), "stats", "--watch", "5", "--json"]) == 1
    assert "--json" in _flat(capsys.readouterr().out)


# ----------------------------------------------------------------- the loop
def test_watch_reloads_stores_each_tick(tmp_path, monkeypatch):
    """The state file gains a record *between* ticks (written by the stubbed
    sleep, standing in for a concurrent download process); the second render
    must show it — proving every tick reads fresh data."""
    import io

    import idm.cli as C

    d = _make_app(tmp_path, monkeypatch)
    state = d / "idm.state.json"
    state.write_text(json.dumps({"version": 1, "downloads": {}}), encoding="utf-8")

    calls = {"n": 0}

    def fake_sleep(seconds):
        calls["n"] += 1
        if calls["n"] == 1:
            # a concurrent process records a failed download meanwhile
            state.write_text(json.dumps(
                {"version": 1,
                 "downloads": {"https://x/n.zip": {"status": "error",
                                                   "filename": "n.zip",
                                                   "size": 10,
                                                   "updated": 1}}}),
                encoding="utf-8")
            return                          # tick 2 renders the new record
        raise KeyboardInterrupt             # stop after the second render

    monkeypatch.setattr("time.sleep", fake_sleep)  # cmd_stats aliases this module

    buf = io.StringIO()
    monkeypatch.setattr(C, "console", C.Console(file=buf, force_terminal=False,
                                                width=250))
    rc = cli_main(["-o", str(d), "stats", "--watch", "1"])
    out = _flat(buf.getvalue())
    assert rc == 130                          # main()'s Ctrl+C convention
    assert calls["n"] == 2                    # slept between the two ticks
    assert "watching every 1s" in out
    assert "0 pending" in out                 # first tick: empty state
    assert "1 pending" in out                 # second tick: reloaded
    assert out.index("1 pending") > out.index("0 pending")


def test_watch_first_render_does_not_sleep(tmp_path, monkeypatch):
    """A huge interval still renders immediately — the first sleep only
    happens after tick 1 is fully on screen."""
    import io

    import idm.cli as C

    d = _make_app(tmp_path, monkeypatch)
    slept = []

    def fake_sleep(seconds):
        slept.append(seconds)
        raise KeyboardInterrupt

    monkeypatch.setattr("time.sleep", fake_sleep)

    buf = io.StringIO()
    monkeypatch.setattr(C, "console", C.Console(file=buf, force_terminal=False,
                                                width=250))
    rc = cli_main(["-o", str(d), "stats", "--watch", "9999"])
    assert rc == 130
    assert slept == [9999]                    # slept exactly once, after tick 1
    out = _flat(buf.getvalue())
    assert "Downloads" in out and "Subtitles" in out  # tick 1 fully rendered
