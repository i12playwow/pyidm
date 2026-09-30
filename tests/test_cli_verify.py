"""'idm verify': retroactive magic-byte scan of the downloads folder.

The same kind-mismatch check a finished download gets (_warn_on_kind_mismatch),
applied to files already on disk — driven in-process through the CLI, with a
tiny hand-built directory instead of the HTTP harness (no network needed).
"""
from __future__ import annotations

import json

import pytest

import idm.cli as C
from idm.cli import main as cli_main
from idm.core import scan_file_for_name

MP4_HEAD = b"\x00\x00\x00\x18ftypmp44" + b"\x00" * 24          # real MPEG-4
PNG_HEAD = b"\x89PNG\r\n\x1a\n" + b"\x00" * 25                 # a JPEG/png-class image
PLAYLIST = b"#EXTM3U\n#EXT-X-TARGETDURATION:10\n" + b"\x00" * 13


def _mkfile(d, name: str, head: bytes) -> None:
    (d / name).write_bytes(head)


@pytest.fixture(autouse=True)
def _quiet_console(monkeypatch):
    monkeypatch.setattr(C, "console", C.Console(force_terminal=False, width=250))


def _run(argv, capsys):
    code = cli_main(argv)
    out = capsys.readouterr().out
    return code, out


# ----------------------------------------------------------------- text mode
def test_clean_and_mismatched(tmp_path, capsys):
    _mkfile(tmp_path, "real.mp4", MP4_HEAD)
    _mkfile(tmp_path, "fake.mp4", PNG_HEAD)
    code, out = _run(["-o", str(tmp_path), "verify"], capsys)  # global -o
    assert code == 1
    assert "[warn] fake.mp4:" in out
    assert "file saved, but the URL served PNG" in out
    assert "real.mp4" not in out.split("[warn]")[0]  # clean file never warned
    assert "2 file(s) scanned" in out
    assert "1 clean" in out and "1 warned" in out


def test_json_shape(tmp_path, capsys):
    _mkfile(tmp_path, "fake.mp4", PNG_HEAD)
    code, out = _run(["verify", "-o", str(tmp_path), "--json"], capsys)  # -o after
    payload = json.loads(out)
    assert code == 1
    assert set(payload) == {"dir", "scanned", "ok", "warned", "unreadable",
                            "skipped", "ignored", "moved", "quarantine", "files"}
    assert payload["scanned"] == 1 and payload["warned"] == 1
    assert payload["ok"] == 0 and payload["skipped"] == 0
    assert payload["moved"] == 0 and payload["quarantine"].endswith("quarantine")
    entry = payload["files"][0]
    assert entry["file"] == "fake.mp4" and entry["kind"] == "warned"
    assert entry["size"] == len(PNG_HEAD)
    assert entry["note"].startswith("file saved, but the URL served PNG")


def test_m3u_saved_as_video(tmp_path, capsys):
    _mkfile(tmp_path, "junk.webm", PLAYLIST)
    code, out = _run(["verify", "-o", str(tmp_path)], capsys)
    assert code == 1
    assert "[warn] junk.webm:" in out
    assert "M3U playlist" in out and "Matroska/WebM" in out


def test_no_opinion_extension_skipped(tmp_path, capsys):
    _mkfile(tmp_path, "data.bin", PNG_HEAD)
    code, out = _run(["verify", "-o", str(tmp_path)], capsys)
    assert code == 0
    assert "1 file(s) scanned" in out and "no opinion" in out
    assert "[warn]" not in out


def test_text_extension_clean(tmp_path, capsys):
    _mkfile(tmp_path, "notes.txt", "hello world\n".encode())
    code, out = _run(["verify", "-o", str(tmp_path)], capsys)
    assert code == 0
    assert "1 clean" in out and "warned" in out


def test_empty_and_missing_dir(tmp_path, capsys):
    (tmp_path / "empty").mkdir()
    code, out = _run(["verify", "-o", str(tmp_path / "empty")], capsys)
    assert code == 0 and "nothing to verify" in out
    code, out = _run(["verify", "-o", str(tmp_path / "missing")], capsys)
    assert code == 0 and "no downloads folder" in out


def test_state_file_and_subdirs_ignored(tmp_path, capsys):
    (tmp_path / "idm.state.json").write_text('{"version": 1, "downloads": {}}',
                                             encoding="utf-8")
    (tmp_path / "sub").mkdir()
    _mkfile(tmp_path / "sub", "fake.mp4", PNG_HEAD)   # must not be scanned
    _mkfile(tmp_path, "real.mp4", MP4_HEAD)
    code, out = _run(["verify", "-o", str(tmp_path), "--json"], capsys)
    payload = json.loads(out)
    assert code == 0
    assert [f["file"] for f in payload["files"]] == ["real.mp4"]
    assert payload["scanned"] == 1 and payload["ok"] == 1


# ------------------------------------------------------------------ --query
def test_query_implies_json(tmp_path, capsys):
    _mkfile(tmp_path, "real.mp4", MP4_HEAD)
    _mkfile(tmp_path, "fake.mp4", PNG_HEAD)
    code, out = _run(["verify", "-o", str(tmp_path), "--query", ".warned", "-r"],
                     capsys)
    assert code == 1 and out.strip() == "1"
    code, out = _run(["verify", "-o", str(tmp_path),
                      "--query", '[.files[] | select(.kind == "warned")] | .file',
                      "-r"], capsys)
    assert code == 1 and out.strip() == "fake.mp4"


# ------------------------------------------------------------ --delete-warned
def test_delete_warned_moves_files(tmp_path, capsys):
    _mkfile(tmp_path, "real.mp4", MP4_HEAD)
    _mkfile(tmp_path, "fake.mp4", PNG_HEAD)
    code, out = _run(["verify", "-o", str(tmp_path), "--json", "--delete-warned"],
                     capsys)
    payload = json.loads(out)
    assert code == 0, "nothing left warned after the sweep"
    assert payload["moved"] == 1 and payload["warned"] == 0
    entry = next(f for f in payload["files"] if f["file"] == "fake.mp4")
    assert entry["kind"] == "moved"
    assert entry["quarantine"].endswith("quarantine\\fake.mp4") or \
           entry["quarantine"].endswith("quarantine/fake.mp4")
    assert not (tmp_path / "fake.mp4").exists()
    assert (tmp_path / "quarantine" / "fake.mp4").read_bytes() == PNG_HEAD
    assert (tmp_path / "real.mp4").exists()          # clean file untouched
    assert not (tmp_path / "real.mp4").is_symlink()


def test_delete_warned_text_output(tmp_path, capsys):
    _mkfile(tmp_path, "fake.mp4", PNG_HEAD)
    code, out = _run(["verify", "-o", str(tmp_path), "--delete-warned"], capsys)
    assert code == 0
    assert "fake.mp4 ->" in out and "quarantine" in out
    assert "1 quarantined" in out
    assert "0 warned" in out


def test_delete_warned_collision_gets_suffix(tmp_path, capsys):
    _mkfile(tmp_path, "fake.mp4", PNG_HEAD)
    qdir = tmp_path / "quarantine"
    qdir.mkdir()
    (qdir / "fake.mp4").write_bytes(b"older copy")
    code, out = _run(["verify", "-o", str(tmp_path), "--json", "--delete-warned"],
                     capsys)
    payload = json.loads(out)
    assert code == 0
    entry = payload["files"][0]
    assert entry["kind"] == "moved"
    assert entry["file"].endswith("fake.mp4") and "(2)" in entry["quarantine"]
    assert (qdir / "fake.mp4").read_bytes() == b"older copy"   # original kept
    assert (qdir / "fake (2).mp4").read_bytes() == PNG_HEAD    # new arrival


def test_delete_warned_clean_run_creates_no_quarantine(tmp_path, capsys):
    _mkfile(tmp_path, "real.mp4", MP4_HEAD)
    code, out = _run(["verify", "-o", str(tmp_path), "--delete-warned"], capsys)
    assert code == 0
    assert not (tmp_path / "quarantine").exists(), "no folder for nothing"
    assert "1 clean" in out


def test_default_still_leaves_files_in_place(tmp_path, capsys):
    _mkfile(tmp_path, "fake.mp4", PNG_HEAD)
    code, out = _run(["verify", "-o", str(tmp_path)], capsys)
    assert code == 1
    assert (tmp_path / "fake.mp4").exists(), "warn-only by default"
    assert not (tmp_path / "quarantine").exists()


# ------------------------------------------------------- core helper contract
def test_scan_file_for_name_contract(tmp_path):
    fake = tmp_path / "fake.mp4"
    _mkfile(tmp_path, "fake.mp4", PNG_HEAD)
    assert scan_file_for_name(fake).startswith("file saved, but")
    real = tmp_path / "real.mp4"
    _mkfile(tmp_path, "real.mp4", MP4_HEAD)
    assert scan_file_for_name(real) == ""
    assert scan_file_for_name(tmp_path / "no.bin") == ""   # no opinion on .bin
    assert scan_file_for_name(tmp_path / "ghost.mp4") == ""  # missing file
