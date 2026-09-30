from __future__ import annotations

import json

import pytest

from idm import config as C


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Redirect both config files into a temp dir and scrub env overrides."""
    user_path = tmp_path / "user-config.json"
    monkeypatch.setattr(C, "USER_CONFIG_PATH", user_path)
    monkeypatch.setattr(C, "LOCAL_CONFIG_NAME", "idm.json")
    monkeypatch.chdir(tmp_path)
    for var in ("IDM_OUT", "IDM_WORKERS", "OPENSUBTITLES_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    return user_path


def write(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


def test_defaults_only(isolated):
    cfg, sources = C.effective_config_with_sources()
    assert cfg["workers"] == 4 and sources["workers"] == "default"
    assert cfg["segments"] == 8 and sources["segments"] == "default"
    assert sources["opensubtitles_api_key"] == "default"


def test_user_config_layer(isolated):
    write(isolated, {"workers": 9, "opensubtitles_api_key": "key123"})
    cfg, sources = C.effective_config_with_sources()
    assert cfg["workers"] == 9
    assert sources["workers"] == "user (~/.idm/config.json)"
    assert cfg["opensubtitles_api_key"] == "key123"
    assert sources["opensubtitles_api_key"] == "user (~/.idm/config.json)"
    assert sources["segments"] == "default"      # untouched key stays default


def test_local_config_wins_over_user(isolated):
    write(isolated, {"workers": 9})
    write(isolated.parent / "idm.json", {"workers": 12})
    cfg, sources = C.effective_config_with_sources()
    assert cfg["workers"] == 12
    assert sources["workers"] == "local (idm.json)"


def test_extra_file_layer(isolated):
    write(isolated.parent / "extra.json", {"workers": 7})
    cfg, sources = C.effective_config_with_sources("extra.json")
    assert cfg["workers"] == 7
    assert sources["workers"] == "extra (extra.json)"


def test_env_beats_every_file(isolated):
    write(isolated, {"workers": 9})
    write(isolated.parent / "idm.json", {"workers": 12})
    import os
    os.environ["IDM_WORKERS"] = "15"
    try:
        cfg, sources = C.effective_config_with_sources()
        assert cfg["workers"] == 15
        assert sources["workers"] == "environment"
    finally:
        os.environ.pop("IDM_WORKERS")


def test_env_opensubtitles_key_tracked(isolated):
    import os
    os.environ["OPENSUBTITLES_API_KEY"] = "envkey"
    try:
        cfg, sources = C.effective_config_with_sources()
        assert cfg["opensubtitles_api_key"] == "envkey"
        assert sources["opensubtitles_api_key"] == "environment"
    finally:
        os.environ.pop("OPENSUBTITLES_API_KEY")


def test_nested_dict_merge_from_user(isolated):
    write(isolated, {"headers": {"User-Agent": "Custom/1.0"}})
    cfg, sources = C.effective_config_with_sources()
    assert cfg["headers"]["User-Agent"] == "Custom/1.0"       # overridden
    assert cfg["headers"]["Accept"] == "*/*"                   # default survives
    assert sources["headers"] == "user (~/.idm/config.json)"


def test_get_config_unchanged_by_refactor(isolated):
    """get_config must behave exactly as before (sources are additive)."""
    write(isolated, {"workers": 9})
    import os
    os.environ["IDM_WORKERS"] = "11"
    try:
        assert C.get_config()["workers"] == 11
        assert C.effective_config_with_sources()[0]["workers"] == 11
    finally:
        os.environ.pop("IDM_WORKERS")


def test_gui_about_wiring():
    """The GUI About dialog uses the source-tracking helper and mask."""
    gui = pytest.importorskip("idm.gui")
    assert hasattr(gui, "effective_config_with_sources")
    assert hasattr(gui, "mask_secret")
    assert hasattr(gui.App, "show_about")
