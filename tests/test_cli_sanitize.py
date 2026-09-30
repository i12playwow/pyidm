"""'idm sanitize-names': list (dry run) or fix (--apply) downloads files
whose names a mangled Content-Disposition produced — the same
_unmangle_cd_name rule _pick_filename applies to new downloads, applied
to files already on disk. Driven in-process through the CLI."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import idm.cli as C
from idm.cli import main as cli_main

PLAYLIST = b"#EXTM3U\n#EXT-X-TARGETDURATION:10\n" + b"\x00" * 13
MANGLED = "130425,_360p.mp4,.mp4,_720p.mp4,"
CLEAN = "130425,_360p.mp4"


def _mkfile(d, name: str, body: bytes) -> None:
    (d / name).write_bytes(body)


@pytest.fixture(autouse=True)
def _quiet_console(monkeypatch):
    monkeypatch.setattr(C, "console", C.Console(force_terminal=False, width=250))


def _run(argv, capsys):
    code = cli_main(argv)
    out = capsys.readouterr().out
    return code, out


# ----------------------------------------------------------------- dry run
def test_dry_run_lists_without_touching(tmp_path, capsys):
    _mkfile(tmp_path, MANGLED, PLAYLIST)
    _mkfile(tmp_path, "real.mp4", b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 24)
    code, out = _run(["sanitize-names", "-o", str(tmp_path), "--json"], capsys)
    payload = json.loads(out)
    assert code == 1, "dry run flags what it would change"
    assert payload["scanned"] == 2 and payload["unclean"] == 1
    assert payload["renamed"] == 0 and payload["failed"] == 0
    row = payload["files"][0]
    assert row == {"file": MANGLED, "to": CLEAN, "action": "would-rename"}
    assert (tmp_path / MANGLED).exists(), "dry run renames nothing"
    assert not (tmp_path / CLEAN).exists()


def test_dry_run_text_output(tmp_path, capsys):
    _mkfile(tmp_path, MANGLED, PLAYLIST)
    code, out = _run(["sanitize-names", "-o", str(tmp_path)], capsys)
    assert code == 1
    assert f"{MANGLED} -> {CLEAN}" in out
    assert "dry run" in out and "--apply" in out


def test_all_clean(tmp_path, capsys):
    _mkfile(tmp_path, "movie.mp4", b"\x00\x00\x00\x18ftypmp42")
    code, out = _run(["sanitize-names", "-o", str(tmp_path), "--json"], capsys)
    payload = json.loads(out)
    assert code == 0
    assert payload["scanned"] == 1 and payload["unclean"] == 0
    assert payload["files"] == []
    code, out = _run(["sanitize-names", "-o", str(tmp_path)], capsys)
    assert code == 0 and "clean names" in out


def test_missing_and_empty_dirs(tmp_path, capsys):
    code, out = _run(["sanitize-names", "-o", str(tmp_path / "nope"), "--json"], capsys)
    payload = json.loads(out)
    assert code == 0
    assert payload["scanned"] == 0 and payload["files"] == []
    (tmp_path / "empty").mkdir()
    code, out = _run(["sanitize-names", "-o", str(tmp_path / "empty")], capsys)
    assert code == 0 and "clean names" in out


# -------------------------------------------------------------------- apply
def test_apply_renames(tmp_path, capsys):
    _mkfile(tmp_path, MANGLED, PLAYLIST)
    code, out = _run(["sanitize-names", "-o", str(tmp_path), "--apply", "--json"],
                     capsys)
    payload = json.loads(out)
    assert code == 0
    row = payload["files"][0]
    assert row["action"] == "renamed" and row["to"] == CLEAN
    assert (tmp_path / CLEAN).read_bytes() == PLAYLIST
    assert not (tmp_path / MANGLED).exists()


def test_apply_text_output_and_idempotent(tmp_path, capsys):
    _mkfile(tmp_path, MANGLED, PLAYLIST)
    code, out = _run(["sanitize-names", "-o", str(tmp_path), "--apply"], capsys)
    assert code == 0
    assert f"[rename] {MANGLED} -> {CLEAN}" in out
    assert "1 file(s) renamed, 0 failed" in out
    code, out = _run(["sanitize-names", "-o", str(tmp_path)], capsys)
    assert code == 0 and "clean names" in out, "second run is a no-op"


def test_apply_never_overwrites(tmp_path, capsys):
    _mkfile(tmp_path, MANGLED, PLAYLIST)
    _mkfile(tmp_path, CLEAN, b"the original file")
    code, out = _run(["sanitize-names", "-o", str(tmp_path), "--apply", "--json"],
                     capsys)
    payload = json.loads(out)
    assert code == 0
    row = payload["files"][0]
    assert row["action"] == "renamed"
    assert row["to"] == "130425,_360p (2).mp4", row
    assert (tmp_path / CLEAN).read_bytes() == b"the original file"
    assert (tmp_path / "130425,_360p (2).mp4").read_bytes() == PLAYLIST


def test_apply_failed_rename_counts(tmp_path, capsys, monkeypatch):
    _mkfile(tmp_path, MANGLED, PLAYLIST)
    real_rename = Path.rename  # noqa: F841 (documents the original being replaced)

    def locked_rename(self, target):
        raise OSError("file is locked by another process")

    monkeypatch.setattr(Path, "rename", locked_rename)
    code, out = _run(["sanitize-names", "-o", str(tmp_path), "--apply", "--json"],
                     capsys)
    payload = json.loads(out)
    assert code == 1
    row = payload["files"][0]
    assert row["action"] == "failed" and "locked" in row["error"]
    assert (tmp_path / MANGLED).exists(), "file stays put on failure"


def test_state_file_and_subdirs_ignored(tmp_path, capsys):
    (tmp_path / "idm.state.json").write_text('{"version": 1, "downloads": {}}',
                                             encoding="utf-8")
    sub = tmp_path / "quarantine"
    sub.mkdir()
    _mkfile(sub, MANGLED, PLAYLIST)          # not descended into
    code, out = _run(["sanitize-names", "-o", str(tmp_path), "--json"], capsys)
    payload = json.loads(out)
    assert code == 0
    assert payload["scanned"] == 0 and payload["files"] == []


def test_apply_json_dry_run_exits_1_after_a_successful_apply(tmp_path, capsys):
    """'--apply --json': unclean stays > 0 after a fully successful apply
    (they are the same rows, now renamed); the JSON exit code keys on
    failed/renamed, not on unclean."""
    _mkfile(tmp_path, MANGLED, PLAYLIST)
    code, out = _run(["sanitize-names", "-o", str(tmp_path), "--apply", "--json"],
                     capsys)
    payload = json.loads(out)
    assert code == 0
    assert payload["unclean"] == 1 and payload["renamed"] == 1
    assert payload["failed"] == 0
    assert payload["files"][0]["action"] == "renamed"


def test_json_payload_shape_is_stable(tmp_path, capsys):
    """The dry-run and apply payloads expose exactly the keys the docs and
    the GUI rely on (scan_unclean_names / apply_name_fixes share them)."""
    _mkfile(tmp_path, MANGLED, PLAYLIST)
    code, out = _run(["sanitize-names", "-o", str(tmp_path), "--json"], capsys)
    payload = json.loads(out)
    assert set(payload) == {"dir", "scanned", "unclean", "renamed",
                            "failed", "files"}
    assert set(payload["files"][0]) == {"file", "to", "action"}
    code, out = _run(["sanitize-names", "-o", str(tmp_path),
                      "--apply", "--json"], capsys)
    payload = json.loads(out)
    assert set(payload) == {"dir", "scanned", "unclean", "renamed",
                            "failed", "files"}
    assert {"file", "to", "action"} <= set(payload["files"][0])


# ------------------------------------------------------------------ queries
def test_query_implies_json(tmp_path, capsys):
    _mkfile(tmp_path, MANGLED, PLAYLIST)
    code, out = _run(["sanitize-names", "-o", str(tmp_path),
                      "--query", ".unclean", "-r"], capsys)
    assert code == 1 and out.strip() == "1"
