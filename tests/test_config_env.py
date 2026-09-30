from __future__ import annotations

import pytest

from idm import config as C
from idm.cli import main as cli_main

TEST_VAR = "PYIDM_TEST_ENV_KEY"


@pytest.fixture
def clean_test_var():
    yield
    C.clear_user_env(TEST_VAR)          # registry
    import os
    os.environ.pop(TEST_VAR, None)      # process


# ------------------------------------------------------------------ mask helper
def test_mask_secret_hides_middle():
    masked = C.mask_secret("abcd1234efgh5678ijkl")
    assert masked.startswith("abcd") and masked.endswith("...")
    assert "1234efgh" not in masked and "ijkl" not in masked


def test_mask_secret_short_and_empty():
    assert C.mask_secret("abc") == "***"
    assert C.mask_secret("") == "(not set)"
    assert C.mask_secret(None) == "(not set)"


# ------------------------------------------------------------- registry round-trip
def test_user_env_round_trip(clean_test_var):
    assert C.get_user_env(TEST_VAR) in (None, "")  # starts unset
    C.set_user_env(TEST_VAR, "hello-world")
    assert C.get_user_env(TEST_VAR) == "hello-world"       # persisted (HKCU)
    import os
    assert os.environ.get(TEST_VAR) == "hello-world"       # current process too
    C.clear_user_env(TEST_VAR)
    assert C.get_user_env(TEST_VAR) in (None, "")


def test_setenv_getenv_delenv_via_cli(clean_test_var, capsys):
    assert cli_main(["config", "setenv", TEST_VAR, "s3cret-value-1234567890"]) == 0
    out = capsys.readouterr().out
    assert "s3cret" not in out              # never print full values
    assert cli_main(["config", "getenv", TEST_VAR]) == 0
    out = capsys.readouterr().out
    assert "s3cret" not in out and TEST_VAR in out
    assert cli_main(["config", "delenv", TEST_VAR]) == 0
    capsys.readouterr()
    assert C.get_user_env(TEST_VAR) in (None, "")


def test_config_set_mirrors_api_key_to_env(monkeypatch, tmp_path):
    """idm config set opensubtitles_api_key KEY must land in the user env too."""
    seen = {}
    monkeypatch.setitem(C.__dict__, "set_config_value", lambda k, v: None)
    monkeypatch.setattr("idm.cli.set_config_value", lambda k, v: None)
    monkeypatch.setattr("idm.cli.set_user_env",
                        lambda name, value: seen.__setitem__(name, value))
    assert cli_main(["config", "set", "opensubtitles_api_key", "abcd1234efgh5678"]) == 0
    assert seen == {"OPENSUBTITLES_API_KEY": "abcd1234efgh5678"}


def test_get_config_env_override_precedence(monkeypatch):
    """Process env must win over config files (this is how the frozen exe sees the key)."""
    monkeypatch.setenv("IDM_WORKERS", "9")
    monkeypatch.setenv("OPENSUBTITLES_API_KEY", "envkey123456")
    cfg = C.get_config()
    assert cfg["workers"] == 9
    assert cfg["opensubtitles_api_key"] == "envkey123456"
