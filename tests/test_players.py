from __future__ import annotations

from pathlib import Path

import pytest

from idm.players import (
    build_command,
    detect_player,
    launch_with_subtitle,
)


@pytest.fixture
def media(tmp_path):
    video = tmp_path / "Movie.mkv"
    video.write_bytes(b"video")
    sub = tmp_path / "Movie.en.srt"
    sub.write_text("1\n00:00:01,000 --> 00:00:02,000\nhello\n", encoding="utf-8")
    return video, sub


# ------------------------------------------------------------------- detection
def test_detect_from_config_path(media, tmp_path):
    fake = tmp_path / "myplayer.exe"
    fake.write_text("")
    kind, exe = detect_player({"player": str(fake)}, which=lambda n: None)
    assert (kind, exe) == ("custom", str(fake))


def test_detect_config_missing_falls_back_to_default():
    kind, exe = detect_player({"player": r"C:\definitely\not\here.exe"}, which=lambda n: None)
    assert kind == "default" and exe is None


def test_detect_via_which_order():
    calls = []
    which = lambda n: calls.append(n) or f"C:/bin/{n}.exe"
    kind, exe = detect_player({}, which=which)
    assert calls[0] == "vlc"
    assert (kind, exe) == ("vlc", "C:/bin/vlc.exe")


def test_detect_custom_wins_over_which():
    kind, _ = detect_player({"player": "mpc-hc64"}, which=lambda n: f"C:/bin/{n}.exe")
    assert kind == "mpc"


def test_detect_nothing(media):
    kind, exe = detect_player({}, which=lambda n: None, exists=lambda p: False)
    assert (kind, exe) == ("default", None)


# ---------------------------------------------------------------- command line
def test_vlc_command():
    cmd = build_command("vlc", "vlc.exe", Path("Movie.mkv"), Path("Movie.en.srt"))
    assert cmd == ["vlc.exe", "--sub-file", "Movie.en.srt", "Movie.mkv"]


def test_mpc_command():
    cmd = build_command("mpc", "mpc-hc64.exe", Path("Movie.mkv"), Path("Movie.en.srt"))
    assert cmd == ["mpc-hc64.exe", "Movie.mkv", "/sub", "Movie.en.srt"]


def test_generic_kind_returns_none():
    assert build_command("custom", "x.exe", Path("v.mkv"), Path("v.srt")) is None


# ------------------------------------------------------------------- launching
def test_launch_vlc_popen(media, monkeypatch):
    calls = []
    monkeypatch.setattr("idm.players.detect_player",
                        lambda cfg=None, **k: ("vlc", "vlc.exe"))
    monkeypatch.setattr("idm.players.subprocess.Popen",
                        lambda cmd, **kw: calls.append(cmd))
    info = launch_with_subtitle(media[0], media[1], {})
    assert calls and calls[0][:2] == ["vlc.exe", "--sub-file"]
    assert info["player"] == "VLC" and info["copied"] is None


def test_launch_mpc_popen(media, monkeypatch, tmp_path):
    exe = tmp_path / "mpc-hc64.exe"
    exe.write_text("")
    calls = []
    monkeypatch.setattr("idm.players.subprocess.Popen", lambda cmd, **kw: calls.append(cmd))
    info = launch_with_subtitle(media[0], media[1], {"player": str(exe)})
    assert calls and calls[0][0] == str(exe)
    assert calls[0][1:] == [str(media[0]), "/sub", str(media[1])]
    assert info["player"] == "MPC"


def test_launch_default_copies_sidecar(media, monkeypatch):
    """No player: video opens via default app, srt copied to same stem."""
    monkeypatch.setattr("idm.players.detect_player", lambda cfg=None, **k: ("default", None))
    monkeypatch.setattr("idm.players.os.startfile", lambda p: None, raising=False)
    video, sub = media
    info = launch_with_subtitle(video, sub, {})
    assert info["player"] == "default app"
    sidecar = video.with_suffix(".srt")
    assert sidecar.exists() and sidecar.read_text(encoding="utf-8").startswith("1\n")
    assert Path(info["copied"]) == sidecar


def test_launch_custom_player_copies_sidecar(media, monkeypatch, tmp_path):
    exe = tmp_path / "myplayer.exe"
    exe.write_text("")
    calls = []
    monkeypatch.setattr("idm.players.subprocess.Popen", lambda cmd, **kw: calls.append(cmd))
    video, sub = media
    info = launch_with_subtitle(video, sub, {"player": str(exe)})
    assert calls and calls[0] == [str(exe), str(video)]
    assert Path(info["copied"]) == video.with_suffix(".srt")


def test_sidecar_not_duplicated(media, monkeypatch):
    """Existing same-stem srt is left alone (no overwrite of user files)."""
    monkeypatch.setattr("idm.players.detect_player", lambda cfg=None, **k: ("default", None))
    monkeypatch.setattr("idm.players.os.startfile", lambda p: None, raising=False)
    video, sub = media
    existing = video.with_suffix(".srt")
    existing.write_text("KEEP", encoding="utf-8")
    info = launch_with_subtitle(video, sub, {})
    assert info["copied"] is None
    assert existing.read_text(encoding="utf-8") == "KEEP"
