"""'idm ignore': manage the config-driven verify ignore list — filenames
the magic-byte scan should never warn about (the escape hatch for files
you inspected and decided are fine, e.g. HLS playlists deliberately kept
under a media name). Entries live in ~/.idm/config.json under
'verify_ignore'; the same helpers back the GUI's ignore dialogs."""
from __future__ import annotations

import json

import pytest

import idm.cli as C
import idm.config as CF
from idm.cli import main as cli_main
from idm.gui_common import move_warned_to_quarantine, scan_for_quarantine

PLAYLIST = b"#EXTM3U\n#EXT-X-TARGETDURATION:10\n" + b"\x00" * 13
MP4_HEAD = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 24
PNG_HEAD = b"\x89PNG\r\n\x1a\n" + b"\x00" * 25


@pytest.fixture(autouse=True)
def _quiet_console(monkeypatch):
    monkeypatch.setattr(C, "console", C.Console(force_terminal=False, width=250))


@pytest.fixture(autouse=True)
def _user_cfg(monkeypatch, tmp_path):
    # inside tmp_path on purpose, but in a subfolder: the verify scans below
    # point at tmp_path (or tmp_path/dl) and never descend into subdirs —
    # a config.json sitting in the scanned folder would itself be scanned
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    monkeypatch.setattr(CF, "USER_CONFIG_PATH", cfg_dir / "config.json")


def _run(argv, capsys):
    code = cli_main(argv)
    out = capsys.readouterr().out
    return code, out


# ----------------------------------------------------------------- list/add
def test_list_starts_empty_and_explains(tmp_path, capsys):
    code, out = _run(["ignore"], capsys)
    assert code == 0
    assert "ignore list is empty" in out and "ignore add" in out


def test_add_then_verify_is_silent(tmp_path, capsys):
    (tmp_path / "kept.webm").write_bytes(PLAYLIST)     # playlist in video clothing
    code, out = _run(["ignore", "add", "kept.webm"], capsys)
    assert code == 0 and "ignored" in out and "kept.webm" in out
    code, out = _run(["-o", str(tmp_path), "verify", "--json"], capsys)
    payload = json.loads(out)
    assert code == 0, "ignored files no longer fail the scan"
    assert payload["ignored"] == 1 and payload["warned"] == 0
    row = payload["files"][0]
    assert row["file"] == "kept.webm" and row["kind"] == "ignored"
    assert row["note"] == ""
    assert (tmp_path / "kept.webm").exists(), "ignore never touches the file"
    code, out = _run(["ignore"], capsys)
    assert "kept.webm" in out


def test_add_is_deduplicated(tmp_path, capsys):
    _run(["ignore", "add", "a.webm"], capsys)
    _run(["ignore", "add", "a.webm"], capsys)
    code, out = _run(["ignore"], capsys)
    assert code == 0
    assert out.count("a.webm") == 1, out


def test_patterns_match_by_extension_and_prefix(tmp_path, capsys):
    (tmp_path / "one.webm").write_bytes(PLAYLIST)
    (tmp_path / "two.webm").write_bytes(PLAYLIST)
    (tmp_path / "130425,_360p.mp4").write_bytes(PLAYLIST)
    (tmp_path / "96528,_360p.mp4").write_bytes(PLAYLIST)
    (tmp_path / "real.mp4").write_bytes(MP4_HEAD)
    _run(["ignore", "add", "*.webm", "130425*"], capsys)
    code, out = _run(["-o", str(tmp_path), "verify", "--json"], capsys)
    payload = json.loads(out)
    assert code == 1, "96528 is still warned, so the run still fails"
    kinds = {f["file"]: f["kind"] for f in payload["files"]}
    assert kinds == {"one.webm": "ignored", "two.webm": "ignored",
                     "130425,_360p.mp4": "ignored", "96528,_360p.mp4": "warned",
                     "real.mp4": "ok"}
    assert payload["ignored"] == 3 and payload["warned"] == 1


def test_extension_pattern_is_case_insensitive(tmp_path, capsys):
    (tmp_path / "MOVIE.WEBM").write_bytes(PLAYLIST)
    _run(["ignore", "add", "*.webm"], capsys)
    code, out = _run(["-o", str(tmp_path), "verify", "--json"], capsys)
    payload = json.loads(out)
    assert code == 0 and payload["ignored"] == 1


def test_ignored_files_are_never_swept(tmp_path, capsys):
    (tmp_path / "kept.webm").write_bytes(PLAYLIST)
    (tmp_path / "gone.webm").write_bytes(PLAYLIST)
    _run(["ignore", "add", "kept.webm"], capsys)
    code, out = _run(["-o", str(tmp_path), "verify", "--json",
                      "--delete-warned"], capsys)
    payload = json.loads(out)
    assert code == 0
    kinds = {f["file"]: f["kind"] for f in payload["files"]}
    assert kinds == {"kept.webm": "ignored", "gone.webm": "moved"}
    assert (tmp_path / "kept.webm").exists(), "ignored file stays in place"
    assert (tmp_path / "quarantine" / "gone.webm").exists()


# ------------------------------------------------------------------- remove
def test_remove(tmp_path, capsys):
    _run(["ignore", "add", "a.webm", "b.webm"], capsys)
    code, out = _run(["ignore", "remove", "a.webm"], capsys)
    assert code == 0 and "removed" in out and "a.webm" in out
    code, out = _run(["ignore"], capsys)
    assert "a.webm" not in out and "b.webm" in out


def test_remove_unknown_fails(tmp_path, capsys):
    code, out = _run(["ignore", "remove", "nope.webm"], capsys)
    assert code == 1
    assert "not on the ignore list" in out
    code, out = _run(["ignore"], capsys)
    assert "nope.webm" not in out


def test_remove_usage_and_add_usage(tmp_path, capsys):
    code, out = _run(["ignore", "remove"], capsys)
    assert code == 1 and "usage" in out
    code, out = _run(["ignore", "add"], capsys)
    assert code == 1 and "usage" in out


# --------------------------------------------------------------------- json
def test_json_list_empty(tmp_path, capsys):
    code, out = _run(["ignore", "--json"], capsys)
    assert code == 0
    payload = json.loads(out)
    assert payload == {"action": "list", "entries": [], "count": 0,
                       "config": str(CF.USER_CONFIG_PATH)}


def test_json_list_and_query(tmp_path, capsys):
    _run(["ignore", "add", "a.webm", "*.m3u"], capsys)
    code, out = _run(["ignore", "--json"], capsys)
    assert code == 0
    payload = json.loads(out)
    assert payload["action"] == "list"
    assert payload["entries"] == ["a.webm", "*.m3u"]
    assert payload["count"] == 2
    assert payload["config"].endswith("config.json")
    # --query extracts one field; -r strips the quotes
    code, out = _run(["ignore", "--query", ".count"], capsys)
    assert code == 0 and out == "2\n"
    code, out = _run(["ignore", "--query", ".entries[0]", "-r"], capsys)
    assert code == 0 and out == "a.webm\n"


def test_json_add_reports_new_and_duplicate(tmp_path, capsys):
    _run(["ignore", "add", "kept.webm"], capsys)
    code, out = _run(["ignore", "add", "kept.webm", "new.webm", "--json"], capsys)
    assert code == 0
    payload = json.loads(out)
    assert payload["action"] == "add"
    assert payload["added"] == ["new.webm"]
    assert payload["duplicates"] == ["kept.webm"]
    assert payload["entries"] == ["kept.webm", "new.webm"]
    assert payload["count"] == 2
    assert payload["config"].endswith("config.json")
    assert "added" not in json.loads(_run(["ignore", "--json"], capsys)[1])


def test_json_remove_reports_removed_and_unknown(tmp_path, capsys):
    _run(["ignore", "add", "a.webm", "b.webm"], capsys)
    code, out = _run(["ignore", "remove", "a.webm", "nope.webm", "--json"], capsys)
    assert code == 0, "mixed remove still succeeds when something was removed"
    payload = json.loads(out)
    assert payload["action"] == "remove"
    assert payload["removed"] == ["a.webm"]
    assert payload["unknown"] == ["nope.webm"]
    assert payload["entries"] == ["b.webm"]
    code, out = _run(["ignore", "remove", "ghost.webm", "--json"], capsys)
    assert code == 1, "remove exits 1 when nothing was removed, as in text mode"
    payload = json.loads(out)
    assert payload["removed"] == [] and payload["unknown"] == ["ghost.webm"]
    assert payload["entries"] == ["b.webm"]
    code, out = _run(["ignore", "remove", "b.webm", "--query", ".removed | length"],
                     capsys)
    assert code == 0 and out == "1\n"


def test_json_add_usage_error_has_no_json(tmp_path, capsys):
    code, out = _run(["ignore", "add", "--json"], capsys)
    assert code == 1 and "usage" in out
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)


def test_json_bad_query_fails_cleanly(tmp_path, capsys):
    _run(["ignore", "add", "a.webm"], capsys)
    code, out = _run(["ignore", "list", "--query", ".entries["], capsys)
    assert code == 1 and "invalid --query" in out
    code, out = _run(["ignore", "--json", "--query", ".count"], capsys)
    assert code == 0 and out == "1\n", "--query implies --json"


# ---------------------------------------------------------------- add hints
def test_add_warns_when_entry_matches_nothing(tmp_path, capsys):
    (tmp_path / "real.mp4").write_bytes(MP4_HEAD)      # on disk but not flagged
    code, out = _run(["-o", str(tmp_path), "ignore", "add", "kept.webm"], capsys)
    assert code == 0, "the typo warning never changes the exit code"
    assert "ignored" in out
    assert "no file in" in out and "kept.webm" in out and "typo" in out
    assert "ignore remove" in out, "the warning names its own undo"


def test_add_is_silent_when_the_entry_matches(tmp_path, capsys):
    (tmp_path / "kept.webm").write_bytes(PLAYLIST)
    code, out = _run(["-o", str(tmp_path), "ignore", "add", "kept.webm"], capsys)
    assert code == 0
    assert "no file in" not in out and "hint" not in out


def test_add_hint_suggests_extension_pattern_for_shared_ext(tmp_path, capsys):
    for name in ("96528,_360p.mp4", "87111,_360p.mp4"):
        (tmp_path / name).write_bytes(PLAYLIST)         # both verify-warned
    code, out = _run(["-o", str(tmp_path), "ignore", "add", "96528,_360p.mp4"],
                     capsys)
    assert code == 0
    assert "hint" in out and "*.mp4" in out
    assert "87111,_360p.mp4" in out, "the hint lists what the pattern would cover"


def test_add_hints_absent_in_json_mode(tmp_path, capsys):
    code, out = _run(["-o", str(tmp_path), "ignore", "add", "ghost.webm",
                      "--json"], capsys)
    assert code == 0
    assert json.loads(out)["added"] == ["ghost.webm"]
    assert "no file in" not in out, "machine mode stays machine-readable"


def test_ignore_add_hints_helper(tmp_path):
    from idm.gui_common import ignore_add_hints

    (tmp_path / "one.webm").write_bytes(PLAYLIST)      # warned
    (tmp_path / "two.webm").write_bytes(PLAYLIST)      # warned
    (tmp_path / "real.mp4").write_bytes(MP4_HEAD)      # ok: never hinted
    matched, hints = ignore_add_hints(tmp_path, ["one.webm"])
    assert matched == ["one.webm"]
    assert hints == [("*.webm", ["one.webm", "two.webm"])]
    # the suggestion is silent when it IS the entry (or already added)
    matched, hints = ignore_add_hints(tmp_path, ["*.webm"])
    assert matched == ["*.webm"] and hints == []
    # a matching entry is never called a typo, and clean files get no hint
    matched, hints = ignore_add_hints(tmp_path, ["real.mp4"])
    assert matched == ["real.mp4"] and hints == []
    # missing folder: no advice, no crash
    assert ignore_add_hints(tmp_path / "nope", ["a.webm"]) == ([], [])


# ------------------------------------------------------------- config shape
def test_config_get_shows_the_effective_list(tmp_path, capsys):
    """The list lives in the normal config layering, so 'config get'
    reflects what the scan reads (idm doctor only reports layer CONFLICTS,
    and a single-layer key is not one)."""
    _run(["ignore", "add", "a.webm"], capsys)
    code, out = _run(["config", "get", "verify_ignore"], capsys)
    assert code == 0 and "a.webm" in out


def test_engine_helpers_agree_with_the_scan(tmp_path):
    """scan_for_quarantine / move_warned_to_quarantine honor the ignore
    list their caller passes — the exact call the CLI and GUI make."""
    from idm.config import normalize_verify_ignore

    (tmp_path / "kept.webm").write_bytes(PLAYLIST)
    (tmp_path / "gone.webm").write_bytes(PLAYLIST)
    ignore = normalize_verify_ignore(["kept.webm"])
    scan = scan_for_quarantine(tmp_path, ignore=ignore)
    assert (scan["warned"], scan["ignored"]) == (1, 1)
    payload, moves = move_warned_to_quarantine(tmp_path, scan, ignore=ignore)
    assert payload["moved"] == 1 and payload["ignored"] == 1
    assert [name for name, _ in moves] == ["gone.webm"]
    # a fresh scan of the post-sweep folder keeps the ignored row
    fresh = scan_for_quarantine(tmp_path, ignore=ignore)
    assert fresh["files"][0]["kind"] == "ignored"
    assert normalize_verify_ignore(None) == []
