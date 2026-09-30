from __future__ import annotations

import json

import pytest

from idm import config as C
from idm.cli import main as cli_main


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Redirect both config files into a temp dir and scrub env overrides."""
    user_path = tmp_path / "user-config.json"
    monkeypatch.setattr(C, "USER_CONFIG_PATH", user_path)
    monkeypatch.chdir(tmp_path)
    for var in ("IDM_OUT", "IDM_WORKERS", "OPENSUBTITLES_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    return user_path


def write(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


def test_clean_config_has_no_findings(isolated):
    assert C.diagnose_config() == []


def test_env_overrides_file_value_warns(isolated, monkeypatch):
    write(isolated, {"workers": 8})
    monkeypatch.setenv("IDM_WORKERS", "16")
    findings = C.diagnose_config()
    assert len(findings) == 1
    f = findings[0]
    assert f["kind"] == "override" and f["severity"] == "warn"
    assert f["key"] == "workers"
    assert f["loser_value"] == 8 and f["winner_value"] == 16
    assert f["loser"] == "user (~/.idm/config.json)"
    assert f["winner"] == "environment"


def test_env_shadow_same_value_is_info(isolated, monkeypatch):
    write(isolated, {"workers": 16})
    monkeypatch.setenv("IDM_WORKERS", "16")
    findings = C.diagnose_config()
    assert len(findings) == 1
    assert findings[0]["severity"] == "info"
    assert findings[0]["loser_value"] == findings[0]["winner_value"] == 16


def test_local_file_overrides_user_file(isolated):
    write(isolated, {"out_dir": "user-dir"})
    write(isolated.parent / "idm.json", {"out_dir": "local-dir"})
    findings = C.diagnose_config()
    assert len(findings) == 1
    f = findings[0]
    assert f["severity"] == "warn"           # different values -> real trap
    assert f["loser"].startswith("user") and f["winner"].startswith("local")


def test_file_overriding_default_is_not_reported(isolated):
    # Setting a key once (over the default) is normal usage, not a silent trap.
    write(isolated.parent / "idm.json", {"workers": 8, "retries": 5})
    assert C.diagnose_config() == []


def test_unknown_key_is_flagged_once(isolated):
    write(isolated.parent / "idm.json", {"worker": 8, "typo_key": "x"})
    findings = C.diagnose_config()
    assert len(findings) == 2                # both unknown keys, no overrides
    assert {f["kind"] for f in findings} == {"unknown"}
    assert {f["key"] for f in findings} == {"worker", "typo_key"}
    assert all(f["severity"] == "warn" for f in findings)


def test_secrets_are_masked_in_cli_output(isolated, monkeypatch, capsys):
    write(isolated, {"opensubtitles_api_key": "SUPERSECRETVALUE123"})
    monkeypatch.setenv("OPENSUBTITLES_API_KEY", "ENVSECRETVALUE456")
    rc = cli_main(["doctor"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "SUPERSECRETVALUE123" not in out and "ENVSECRETVALUE456" not in out
    assert "workers" not in out or "overrides" in out  # sanity: table rendered


def test_cli_exit_codes_and_clean_message(isolated, capsys):
    assert cli_main(["doctor"]) == 0
    assert "no config conflicts" in capsys.readouterr().out

    write(isolated, {"workers": 8})
    import os
    os.environ["IDM_WORKERS"] = "16"
    try:
        assert cli_main(["doctor"]) == 1
        out = capsys.readouterr().out
        assert "WARN" in out and "environment" in out
    finally:
        os.environ.pop("IDM_WORKERS", None)
