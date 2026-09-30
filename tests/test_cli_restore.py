"""'idm restore': move files back out of the quarantine folder that
'idm verify --delete-warned' fills — or discard them for good.

Driven in-process through the CLI against a hand-built directory; the
quarantine folder is pre-planted with the exact kind of mangled-name
HLS playlists the real sweep put there.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import idm.cli as C
from idm.cli import main as cli_main

PLAYLIST = b"#EXTM3U\n#EXT-X-TARGETDURATION:10\n" + b"\x00" * 13
MANGLED = "130425,_360p.mp4,.mp4,_720p.mp4,"
MP4_HEAD = b"\x00\x00\x00\x18ftypmp44" + b"\x00" * 24          # real MPEG-4
PNG_HEAD = b"\x89PNG\r\n\x1a\n" + b"\x00" * 25                 # mislabeled body


def _plant_quarantine(d, names) -> None:
    q = d / "quarantine"
    q.mkdir(parents=True, exist_ok=True)
    for i, name in enumerate(names):
        (q / name).write_bytes(PLAYLIST + bytes([i]))


@pytest.fixture(autouse=True)
def _quiet_console(monkeypatch):
    monkeypatch.setattr(C, "console", C.Console(force_terminal=False, width=250))


def _run(argv, capsys):
    code = cli_main(argv)
    out = capsys.readouterr().out
    return code, out


# ------------------------------------------------------------------ restore
def test_restore_all(tmp_path, capsys):
    _plant_quarantine(tmp_path, ["a.mp4", MANGLED])
    code, out = _run(["-o", str(tmp_path), "restore", "--json"], capsys)
    payload = json.loads(out)
    assert code == 0
    assert payload["restored"] == 2 and payload["failed"] == 0
    assert {f["file"] for f in payload["files"]} == {"a.mp4", MANGLED}
    assert (tmp_path / "a.mp4").read_bytes() == PLAYLIST + b"\x00"
    assert (tmp_path / MANGLED).exists()
    assert not (tmp_path / "quarantine").exists(), "emptied folder is tidied away"


def test_restore_named_only(tmp_path, capsys):
    _plant_quarantine(tmp_path, ["a.mp4", "b.mp4"])
    code, out = _run(["restore", "-o", str(tmp_path), "a.mp4", "--json"], capsys)
    payload = json.loads(out)
    assert code == 0
    assert payload["restored"] == 1
    assert (tmp_path / "a.mp4").exists()
    assert (tmp_path / "quarantine" / "b.mp4").exists(), "the other one stays"
    assert (tmp_path / "quarantine").is_dir(), "folder kept while not empty"


def test_restore_collision_gets_suffix(tmp_path, capsys):
    _plant_quarantine(tmp_path, ["clip.mp4"])
    (tmp_path / "clip.mp4").write_bytes(b"the original download")
    code, out = _run(["restore", "-o", str(tmp_path), "--json"], capsys)
    payload = json.loads(out)
    assert code == 0
    assert (tmp_path / "clip.mp4").read_bytes() == b"the original download"
    assert (tmp_path / "clip (2).mp4").read_bytes() == PLAYLIST + b"\x00"
    entry = payload["files"][0]
    assert entry["action"] == "restored" and entry["dest"].endswith("clip (2).mp4")


def test_restore_missing_name_fails(tmp_path, capsys):
    _plant_quarantine(tmp_path, ["a.mp4"])
    code, out = _run(["restore", "-o", str(tmp_path), "ghost.mp4", "--json"], capsys)
    payload = json.loads(out)
    assert code == 1
    assert payload["restored"] == 0 and payload["failed"] == 1
    assert payload["files"][0]["error"] == "not in the quarantine folder"
    assert (tmp_path / "quarantine" / "a.mp4").exists(), "untouched"


def test_restore_without_quarantine_folder(tmp_path, capsys):
    code, out = _run(["restore", "-o", str(tmp_path), "--json"], capsys)
    payload = json.loads(out)
    assert code == 0
    assert payload["restored"] == 0 and payload["files"] == []
    code, out = _run(["restore", "-o", str(tmp_path)], capsys)
    assert code == 0 and "no quarantine folder" in out


def test_restore_empty_quarantine(tmp_path, capsys):
    (tmp_path / "quarantine").mkdir()
    code, out = _run(["restore", "-o", str(tmp_path)], capsys)
    assert code == 0 and "quarantine is empty" in out


def test_restore_failed_move_keeps_folder(tmp_path, capsys, monkeypatch):
    """A replace that fails leaves the file in quarantine — the run fails
    and the folder must not be tidied away over it."""
    _plant_quarantine(tmp_path, ["locked.mp4"])
    real_replace = Path.replace

    def locked_replace(self, target):
        if self.name == "locked.mp4":
            raise OSError("file is locked by another process")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", locked_replace)
    code, out = _run(["restore", "-o", str(tmp_path), "--json"], capsys)
    payload = json.loads(out)
    assert code == 1
    assert payload["restored"] == 0 and payload["failed"] == 1
    assert "locked" in payload["files"][0]["error"]
    assert (tmp_path / "quarantine" / "locked.mp4").exists()
    assert (tmp_path / "quarantine").is_dir()


# ----------------------------------------------------------------- --discard
def test_discard_deletes_for_good(tmp_path, capsys):
    _plant_quarantine(tmp_path, ["a.mp4", MANGLED])
    code, out = _run(["restore", "-o", str(tmp_path), "--discard", "--json"], capsys)
    payload = json.loads(out)
    assert code == 0
    assert payload["discarded"] == 2
    assert not (tmp_path / "quarantine").exists(), "emptied folder tidied away"
    assert not (tmp_path / "a.mp4").exists()


def test_discard_named_only(tmp_path, capsys):
    _plant_quarantine(tmp_path, ["a.mp4", "b.mp4"])
    code, out = _run(["restore", "-o", str(tmp_path), "a.mp4", "--discard", "--json"],
                     capsys)
    payload = json.loads(out)
    assert code == 0
    assert payload["discarded"] == 1 and payload["restored"] == 0
    assert not (tmp_path / "quarantine" / "a.mp4").exists()
    assert (tmp_path / "quarantine" / "b.mp4").exists()


def test_discard_failed_unlink_keeps_folder(tmp_path, capsys, monkeypatch):
    _plant_quarantine(tmp_path, ["locked.mp4"])
    real_unlink = Path.unlink

    def locked_unlink(self, missing_ok=False):
        if self.name == "locked.mp4":
            raise OSError("file is locked by another process")
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", locked_unlink)
    code, out = _run(["restore", "-o", str(tmp_path), "--discard", "--json"], capsys)
    payload = json.loads(out)
    assert code == 1
    assert payload["discarded"] == 0 and payload["failed"] == 1
    assert (tmp_path / "quarantine" / "locked.mp4").exists()
    assert (tmp_path / "quarantine").is_dir(), "folder kept while failed"


# ------------------------------------------------------------------ queries
def test_query_implies_json(tmp_path, capsys):
    _plant_quarantine(tmp_path, ["a.mp4", "b.mp4"])
    code, out = _run(["restore", "-o", str(tmp_path), "--query", ".restored", "-r"],
                     capsys)
    assert code == 0 and out.strip() == "2"


# ---------------------------------------------------------- round trip w/ verify
def test_verify_restore_round_trip(tmp_path, capsys):
    (tmp_path / "fake.mp4").write_bytes(PNG_HEAD)
    (tmp_path / "real.mp4").write_bytes(MP4_HEAD)
    code, out = _run(["verify", "-o", str(tmp_path), "--json", "--delete-warned"],
                     capsys)
    assert code == 0 and not (tmp_path / "fake.mp4").exists()
    code, out = _run(["restore", "-o", str(tmp_path), "--json"], capsys)
    payload = json.loads(out)
    assert code == 0 and payload["restored"] == 1
    assert (tmp_path / "fake.mp4").read_bytes() == PNG_HEAD
    assert not (tmp_path / "quarantine").exists()
