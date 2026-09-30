from __future__ import annotations

import pytest

gui = pytest.importorskip("idm.gui")
from idm import gui as G


# ------------------------------------------------------- first-run detection
def test_fresh_setup_shows_wizard(monkeypatch):
    monkeypatch.delenv("OPENSUBTITLES_API_KEY", raising=False)
    monkeypatch.setattr(G, "get_user_env", lambda name: None)
    assert G.should_show_first_run({}) is True


def test_wizard_done_flag_suppresses(monkeypatch):
    monkeypatch.delenv("OPENSUBTITLES_API_KEY", raising=False)
    monkeypatch.setattr(G, "get_user_env", lambda name: None)
    assert G.should_show_first_run({"wizard_done": True}) is False


def test_key_in_config_suppresses(monkeypatch):
    monkeypatch.delenv("OPENSUBTITLES_API_KEY", raising=False)
    monkeypatch.setattr(G, "get_user_env", lambda name: None)
    assert G.should_show_first_run({"opensubtitles_api_key": "k"}) is False


def test_key_in_process_env_suppresses(monkeypatch):
    monkeypatch.setenv("OPENSUBTITLES_API_KEY", "k")
    monkeypatch.setattr(G, "get_user_env", lambda name: None)
    assert G.should_show_first_run({}) is False


def test_key_in_windows_user_env_suppresses(monkeypatch):
    monkeypatch.delenv("OPENSUBTITLES_API_KEY", raising=False)
    monkeypatch.setattr(G, "get_user_env", lambda name: "stored-key")
    assert G.should_show_first_run({}) is False


# ------------------------------------------------------------ key verification
def test_verify_key_accepts_200(monkeypatch):
    class R:
        status_code = 200

    monkeypatch.setattr("requests.get", lambda *a, **k: R())
    ok, msg = G.verify_opensubtitles_key("goodkey")
    assert ok and "accepted" in msg


def test_verify_key_rate_limited_still_valid(monkeypatch):
    class R:
        status_code = 429

    monkeypatch.setattr("requests.get", lambda *a, **k: R())
    ok, msg = G.verify_opensubtitles_key("goodkey")
    assert ok and "rate" in msg.lower()


def test_verify_key_rejected_401(monkeypatch):
    class R:
        status_code = 401

    monkeypatch.setattr("requests.get", lambda *a, **k: R())
    ok, msg = G.verify_opensubtitles_key("badkey")
    assert not ok and "401" in msg


def test_verify_key_network_error_fails_closed(monkeypatch):
    import requests as rq

    def boom(*a, **k):
        raise rq.ConnectionError("no internet")

    monkeypatch.setattr("requests.get", boom)
    ok, msg = G.verify_opensubtitles_key("anykey")
    assert not ok and "network error" in msg


def test_verify_key_empty():
    ok, msg = G.verify_opensubtitles_key("")
    assert not ok


# ------------------------------------------------------------- save wiring
def _make_app(monkeypatch):
    """Instantiate the real App with Tk widgets stubbed (headless-safe)."""
    class FakeWidget:
        def __init__(self, *a, **k):
            pass

        def __getattr__(self, name):
            return lambda *a, **k: None

    class App(G.App):
        def __init__(self):
            self.cfg = {}
            self.events = __import__("queue").Queue()
            self._log_lines = []

        def _log(self, msg, level="info"):
            self._log_lines.append(msg)

    # NOTE: no monkeypatching here — tests patch G.set_config_value/
    # G.set_user_env themselves BEFORE calling _make_app.
    return App()


def test_save_api_key_persists_to_config_and_env(monkeypatch):
    saved_cfg, saved_env = {}, {}

    monkeypatch.setattr(G, "set_config_value",
                        lambda k, v: saved_cfg.__setitem__(k, v))
    monkeypatch.setattr(G, "set_user_env",
                        lambda name, value: saved_env.__setitem__(name, value))
    app = _make_app(monkeypatch)
    app._save_api_key("abcd1234efgh5678")
    assert saved_cfg == {"opensubtitles_api_key": "abcd1234efgh5678"}
    assert saved_env == {"OPENSUBTITLES_API_KEY": "abcd1234efgh5678"}
    assert app.cfg["wizard_done"] is True
    assert any("saved" in line for line in app._log_lines)
