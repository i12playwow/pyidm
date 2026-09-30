from __future__ import annotations

import json
from unittest import mock

import pytest

from idm.cli import main as cli_main
from idm.health import ProviderHealth

FAKE_OK = [
    ProviderHealth("subtitlecat", "SubtitleCat", "ok", "reachable",
                   endpoint="https://subtitlecat.com", hints=[]),
    ProviderHealth("opensubtitles", "OpenSubtitles", "warn", "no API key",
                   hints=["set opensubtitles_api_key"]),
]
FAKE_DOWN = FAKE_OK + [
    ProviderHealth("podnapisi", "Podnapisi", "down", "DNS blocked",
                   hints=["ISP-level block detected"]),
]


@pytest.fixture(autouse=True)
def _quiet_console(monkeypatch):
    # no explicit file: rich resolves sys.stdout at print time, so capsys
    # sees both the rich output and cmd_providers' raw sys.stdout JSON
    import idm.cli as C
    monkeypatch.setattr(C, "console",
                        C.Console(force_terminal=False, width=250))


def _run(monkeypatch, argv, results, capsys):
    with mock.patch("idm.health.run_checks", return_value=results) as rc:
        code = cli_main(argv)
        out = capsys.readouterr().out
    return code, out, rc


def test_parser_json_default_false():
    import idm.cli as C
    assert C.build_parser().parse_args(["providers"]).json is False
    assert C.build_parser().parse_args(["providers", "--json"]).json is True


def test_json_payload_lossless(monkeypatch, capsys):
    code, out, _ = _run(monkeypatch, ["providers", "--json"], FAKE_OK, capsys)
    assert code == 0
    data = json.loads(out)
    assert data["mode"] == "quick"
    assert data["all_ok"] is True
    assert [p["name"] for p in data["providers"]] == ["subtitlecat", "opensubtitles"]
    p0 = data["providers"][0]
    assert p0 == {"name": "subtitlecat", "label": "SubtitleCat", "status": "ok",
                  "detail": "reachable", "endpoint": "https://subtitlecat.com",
                  "hints": []}
    # raw status values, not the display marks
    assert data["providers"][1]["status"] == "warn"


def test_json_exit_1_when_down(monkeypatch, capsys):
    code, out, _ = _run(monkeypatch, ["providers", "--json"], FAKE_DOWN, capsys)
    data = json.loads(out)
    assert data["all_ok"] is False
    assert code == 1


def test_text_mode_exit_codes_unchanged(monkeypatch, capsys):
    assert _run(monkeypatch, ["providers"], FAKE_OK, capsys)[0] == 0
    assert _run(monkeypatch, ["providers"], FAKE_DOWN, capsys)[0] == 1


def test_text_mode_has_no_json(monkeypatch, capsys):
    _, out, _ = _run(monkeypatch, ["providers"], FAKE_OK, capsys)
    assert '"providers"' not in out and "OK" in out
    assert "hint: set opensubtitles_api_key" in out


def test_deep_flag_reaches_run_checks(monkeypatch, capsys):
    _, _, rc = _run(monkeypatch, ["providers", "--json", "--deep"], FAKE_OK, capsys)
    rc.assert_called_once()
    kwargs = rc.call_args.kwargs
    assert kwargs.get("deep") is True
    _, _, rc2 = _run(monkeypatch, ["providers", "--json"], FAKE_OK, capsys)
    assert rc2.call_args.kwargs.get("deep") is False
