from __future__ import annotations

import pytest

from idm import health as H
from idm.cli import main as cli_main
from idm.health import ProviderHealth, check_provider, run_checks


def test_unknown_provider_warns():
    r = check_provider("nosuchprovider")
    assert r.status == "warn" and "unknown" in r.detail.lower()


def test_no_key_is_warn_not_down():
    monkey = pytest.MonkeyPatch()
    monkey.delenv("OPENSUBTITLES_API_KEY", raising=False)
    monkey.setattr(H, "get_user_env", lambda name: None)
    r = H.check_opensubtitles()
    monkey.undo()
    assert r.status == "warn" and "no api key" in r.detail.lower()


def test_bad_key_is_down_with_hint():
    calls = []

    class Resp:
        status_code = 401

    def fake_get(url, headers=None, timeout=12.0):
        calls.append(url)
        return Resp()

    monkey = pytest.MonkeyPatch()
    monkey.setattr(H, "_get", fake_get)
    monkey.setattr(H, "get_user_env", lambda name: "badkey123")
    r = H.check_opensubtitles()
    monkey.undo()
    assert r.status == "down" and r.hints


def test_dns_fail_is_down():
    monkey = pytest.MonkeyPatch()
    monkey.setattr(H, "_dns_ok", lambda host: (False, "DNS failed (blocked)"))
    r = H.check_yifysubtitles(deep=False)
    monkey.undo()
    assert r.status == "down" and "DNS" in r.detail


def test_deep_ysy_end_to_end():
    class Z:
        status_code = 200
        content = b"PK\x03\x04subtitledata"
        text = '<a href="/subtitle/inception-2010-english-yify-244676.zip">dl</a>'

    def fake_get(url, headers=None, timeout=12.0):
        return Z()

    monkey = pytest.MonkeyPatch()
    monkey.setattr(H, "_get", fake_get)
    monkey.setattr(H, "_dns_ok", lambda host: (True, "DNS ok (1.2.3.4)"))
    r = H.check_yifysubtitles(deep=True)
    monkey.undo()
    assert r.status == "ok" and "end-to-end" in r.detail


def test_run_checks_follows_chain_order(monkeypatch):
    seen = []

    def fake_check(name, deep=False):
        seen.append(name)
        return ProviderHealth(name=name, label=name, status="ok", detail="x")

    monkeypatch.setattr(H, "check_provider", fake_check)
    results = run_checks({"subtitle_providers": "opensubtitles,yifysubtitles,subtitlecat"})
    assert seen == ["opensubtitles", "yifysubtitles", "subtitlecat"]
    assert len(results) == 3


def test_cli_providers_exit_codes(monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(tmp_path)  # keep config reads local
    monkeypatch.setattr("idm.health.run_checks", lambda cfg, deep=False: [
        ProviderHealth("opensubtitles", "OpenSubtitles", "ok", "fine"),
        ProviderHealth("yifysubtitles", "YIFYSubtitles", "down", "DNS failed"),
    ])
    rc = cli_main(["providers"])
    out = capsys.readouterr().out
    assert rc == 1                      # one provider down -> exit 1
    assert "DOWN" in out and "OK" in out

    monkeypatch.setattr("idm.health.run_checks", lambda cfg, deep=False: [
        ProviderHealth("opensubtitles", "OpenSubtitles", "warn", "no key"),
    ])
    rc = cli_main(["providers"])
    capsys.readouterr()
    assert rc == 0                      # warn is not down


def test_cli_providers_deep_flag(monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(tmp_path)
    got = {}

    def fake(cfg, deep=False):
        got["deep"] = deep
        return [ProviderHealth("subtitlecat", "SubtitleCat", "ok", "fine")]

    monkeypatch.setattr("idm.health.run_checks", fake)
    assert cli_main(["providers", "--deep"]) == 0
    assert got["deep"] is True
