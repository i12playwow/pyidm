"""Player integration: open a video with its subtitle in the user's media player.

After PyIDM saves ``Name.en.srt`` next to a video, launch_with_subtitle() starts
the video with the subtitle attached:

- VLC:      vlc --sub-file Name.en.srt Video.mkv
- MPC-HC / MPC-BE: mpc-hc64.exe Video.mkv /sub Name.en.srt
- anything else configured via ``player``: started with the video, and the
  subtitle is additionally copied to ``Name.srt`` (same stem) so the player's
  sidecar auto-load picks it up.
- no player at all: the video opens with the OS default app, subtitle copied
  to the same-stem sidecar.

Detection order: config ``player`` override -> PATH (vlc, mpc-hc64, mpc-hc,
mpc-be64, mpc-be) -> known Windows install locations -> default app.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def _known_candidates() -> list[tuple[str, str]]:
    """(kind, path) for common Windows player install locations."""
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    vlc = [rf"{pf}\VideoLAN\VLC\vlc.exe", rf"{pf86}\VideoLAN\VLC\vlc.exe"]
    mpc = [
        rf"{pf}\MPC-HC\mpc-hc64.exe", rf"{pf86}\MPC-HC\mpc-hc.exe",
        rf"{pf86}\K-Lite Codec Pack\MPC-HC64\mpc-hc64.exe",
        rf"{pf86}\K-Lite Codec Pack\MPC-HC\mpc-hc.exe",
        rf"{pf}\MPC-BE\mpc-be64.exe", rf"{pf86}\MPC-BE\mpc-be.exe",
        rf"{pf86}\K-Lite Codec Pack\MPC-BE64\mpc-be64.exe",
    ]
    out = [("vlc", p) for p in vlc] + [("mpc", p) for p in mpc]
    return out


def _kind_of(exe: str) -> str:
    name = Path(exe).name.lower()
    if "vlc" in name:
        return "vlc"
    if "mpc" in name:
        return "mpc"
    return "custom"


def detect_player(cfg: dict | None = None, which=shutil.which,
                  exists=lambda p: Path(p).exists()) -> tuple[str, str | None]:
    """Return (kind, executable) — ('default', None) when nothing is found."""
    cfg = cfg or {}
    custom = str(cfg.get("player") or "").strip()
    if custom:
        found = which(custom) or (custom if exists(custom) else None)
        if found:
            return _kind_of(found), found
        return "default", None  # configured player missing — fall back
    for name in ("vlc", "mpc-hc64", "mpc-hc", "mpc-be64", "mpc-be"):
        p = which(name)
        if p:
            return _kind_of(p), p
    for kind, p in _known_candidates():
        if exists(p):
            return kind, p
    return "default", None


def build_command(kind: str, exe: str, video: Path, subtitle: Path) -> list[str] | None:
    """Player-specific command line; None means 'generic' handling."""
    if kind == "vlc":
        return [exe, "--sub-file", str(subtitle), str(video)]
    if kind == "mpc":
        return [exe, str(video), "/sub", str(subtitle)]
    return None


def _sidecar_copy(video: Path, subtitle: Path) -> Path | None:
    """Copy the subtitle to <video-stem>.srt (best-effort sidecar for default apps)."""
    sidecar = video.with_suffix(".srt")
    if sidecar == subtitle:
        return None
    try:
        if not sidecar.exists():
            shutil.copyfile(subtitle, sidecar)
            return sidecar
    except OSError:
        pass
    return None


def _open_default(video: Path) -> None:
    if sys.platform == "win32" and hasattr(os, "startfile"):
        os.startfile(str(video))
    else:
        opener = shutil.which("xdg-open") or shutil.which("open")
        if opener:
            subprocess.Popen([opener, str(video)])


def launch_with_subtitle(video, subtitle, cfg: dict | None = None) -> dict:
    """Open the video in the best available player with the subtitle attached.

    Returns {"player": <label>, "copied": <sidecar path | None>}.
    """
    video, subtitle = Path(video), Path(subtitle)
    kind, exe = detect_player(cfg)

    if kind == "default":
        copied = _sidecar_copy(video, subtitle)
        _open_default(video)
        return {"player": "default app", "copied": copied}

    if kind == "custom":
        # Unknown player: start it with the video, sidecar copy for auto-load.
        if exe is None:                  # unreachable: kind='custom' implies exe
            return {"player": "default app", "copied": None}
        copied = _sidecar_copy(video, subtitle)
        subprocess.Popen([exe, str(video)], creationflags=CREATE_NO_WINDOW)
        return {"player": Path(exe).name, "copied": copied}

    assert exe is not None           # vlc/mpc kinds always carry an executable
    cmd = build_command(kind, exe, video, subtitle)
    assert cmd is not None           # ...and those kinds always build a command
    subprocess.Popen(cmd, creationflags=CREATE_NO_WINDOW)
    return {"player": "VLC" if kind == "vlc" else "MPC", "copied": None}
