"""'idm prune-state': remove download-state records whose file no longer
exists in the downloads folder — dry-run preview by default, --apply
removes them from idm.state.json (files are never touched, and a record
whose file reappeared between preview and apply keeps its record)."""
from __future__ import annotations

import json

import pytest

import idm.cli as C
from idm.state import State, prune_stale_records, scan_stale_records


@pytest.fixture(autouse=True)
def _quiet_console(monkeypatch):
    monkeypatch.setattr(C, "console", C.Console(force_terminal=False, width=250))


def _run(argv, capsys):
    code = C.main(argv)
    out = capsys.readouterr().out
    return code, out


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """A state dir with one live record (file on disk), one stale (file
    gone), and one filename-less record (never stale)."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "downloads").mkdir()
    (tmp_path / "downloads" / "movie.mp4").write_bytes(b"\x00" * 8)
    st = State(tmp_path / "downloads" / "idm.state.json")
    st.set("https://x/live.mp4", status="done", filename="movie.mp4",
           size=8, updated=1_758_748_800.0)
    st.set("https://x/gone.webm", status="done", filename="gone.webm",
           size=1024, updated=1_758_748_801.0)
    st.set("https://x/ghost", status="downloading", updated=1_758_748_802.0)
    return st


# ------------------------------------------------------------------ engine
def test_scan_finds_only_missing_files(store):
    payload = scan_stale_records(store)
    assert payload["action"] == "scan"
    assert payload["scanned"] == 3 and payload["stale"] == 1 and payload["live"] == 2
    assert payload["records"][0]["url"] == "https://x/gone.webm"
    assert payload["records"][0]["filename"] == "gone.webm"
    assert payload["records"][0]["status"] == "done"
    assert payload["records"][0]["size"] == 1024
    assert isinstance(payload["records"][0]["updated"], float)
    assert payload["records"][0]["removed"] is False
    assert (store.path.parent / "gone.webm").exists() is False
    assert len(store.records()) == 3, "scan is read-only"


def test_prune_removes_stale_keeps_live_and_nameless(store):
    payload, removed = prune_stale_records(store)
    assert removed == 1 and payload["action"] == "apply"
    assert payload["stale"] == 1 and payload["live"] == 2
    kept = store.records()
    assert "https://x/gone.webm" not in kept
    assert "https://x/live.mp4" in kept and "https://x/ghost" in kept
    assert store.path.exists(), "store rewritten atomically"
    assert payload["records"][0]["removed"] is True


def test_fresh_prune_rechecks_so_back_on_disk_is_never_removed(store, tmp_path):
    (store.path.parent / "gone.webm").write_bytes(b"\x00" * 4)   # back on disk
    payload, removed = prune_stale_records(store)
    assert removed == 0
    assert payload["records"] == [], "a fresh scan finds nothing stale"
    assert len(store.records()) == 3, "re-checked at apply: nothing removed"


def test_apply_of_stale_preview_rechecks_each_record(store, tmp_path):
    preview = scan_stale_records(store)
    (store.path.parent / "gone.webm").write_bytes(b"\x00" * 4)   # back on disk
    payload, removed = prune_stale_records(store, scan=preview)
    assert removed == 0 and payload["records"][0]["removed"] is False
    assert len(store.records()) == 3


def test_partial_download_is_never_stale(tmp_path):
    """A record with resumable evidence on disk ('.part' or '.part0') is
    kept: its download can still continue."""
    d = tmp_path / "dl"
    d.mkdir()
    st = State(d / "idm.state.json")
    st.set("https://x/movie.mp4", status="downloading", filename="movie.mp4")
    (d / "movie.mp4.part").write_bytes(b"partial")
    payload = scan_stale_records(st)
    assert payload["stale"] == 0 and payload["scanned"] == 1
    (d / "movie.mp4.part").unlink()
    (d / "movie.mp4.part0").write_bytes(b"seg0")
    payload = scan_stale_records(st)
    assert payload["stale"] == 0, "segmented partials protect the record too"
    payload, removed = prune_stale_records(st)
    assert removed == 0 and len(st.records()) == 1


# ------------------------------------------------------------------- vacuum
def test_vacuum_compacts_and_reports_bytes(tmp_path):
    """--vacuum rewrites the store without indentation: the file shrinks
    even when nothing was stale, and the payload carries byte counts."""
    d = tmp_path / "dl"
    d.mkdir()
    st = State(d / "idm.state.json")
    for i in range(5):
        st.set(f"https://x/f{i}.mp4", status="done", filename=f"f{i}.mp4",
               size=1024 * i)
        (d / f"f{i}.mp4").write_bytes(b"\x00" * 8)
    before = st.path.stat().st_size
    payload, removed = prune_stale_records(st, compact=True)
    assert removed == 0 and payload["compacted"] is True
    assert payload["state_bytes_before"] == before
    assert payload["state_bytes_after"] < before
    assert payload["bytes_reclaimed"] == before - payload["state_bytes_after"]
    assert payload["bytes_reclaimed"] > 0
    assert len(st.records()) == 5, "nothing stale: records survive"
    # the compacted store still loads fine
    assert State(st.path).records() == st.records()


def test_vacuum_also_prunes(tmp_path):
    """One pass: stale record gone, store compacted, counts consistent."""
    d = tmp_path / "dl"
    d.mkdir()
    st = State(d / "idm.state.json")
    st.set("https://x/gone.webm", status="error", filename="gone.webm")
    st.set("https://x/here.mp4", status="done", filename="here.mp4")
    (d / "here.mp4").write_bytes(b"\x00" * 8)
    payload, removed = prune_stale_records(st, compact=True)
    assert removed == 1
    assert payload["compacted"] is True and payload["bytes_reclaimed"] > 0
    assert list(st.records()) == ["https://x/here.mp4"]


def test_vacuum_missing_store_is_a_clean_noop(tmp_path):
    d = tmp_path / "none"
    payload, removed = prune_stale_records(d / "idm.state.json", compact=True)
    assert removed == 0 and payload["compacted"] is True
    assert payload["state_bytes_before"] is None
    assert payload["state_bytes_after"] is None
    assert payload["bytes_reclaimed"] is None
    assert not (d / "idm.state.json").exists(), "still never creates a store"


def test_plain_apply_reports_null_byte_fields(tmp_path):
    """Without --vacuum the byte fields are present but null (and the
    write keeps its indentation)."""
    d = tmp_path / "dl"
    d.mkdir()
    st = State(d / "idm.state.json")
    st.set("https://x/a.mp4", status="done", filename="a.mp4")
    (d / "a.mp4").write_bytes(b"\x00" * 8)
    payload, removed = prune_stale_records(st)
    assert payload["compacted"] is False
    assert payload["state_bytes_before"] is None
    assert payload["state_bytes_after"] is None
    assert payload["bytes_reclaimed"] is None
    text = st.path.read_text(encoding="utf-8")
    assert "\n  " in text, "default write stays indented"


# --------------------------------------------------------------------- CLI
def test_cli_vacuum_text_mode(tmp_path, capsys):
    State(tmp_path / "idm.state.json").set("https://x/gone.webm",
                                           status="done", filename="gone.webm")
    code, out = _run(["-o", str(tmp_path), "prune-state", "--vacuum"], capsys)
    assert code == 0
    assert "removed" in out and "compacted" in out and "reclaimed" in out
    assert State(tmp_path / "idm.state.json").records() == {}


def test_cli_vacuum_json_payload_and_query(tmp_path, capsys):
    State(tmp_path / "idm.state.json").set("https://x/gone.webm",
                                           status="error", filename="gone.webm")
    code, out = _run(["-o", str(tmp_path), "prune-state", "--vacuum", "--json"],
                     capsys)
    assert code == 0
    payload = json.loads(out)
    assert payload["action"] == "apply" and payload["compacted"] is True
    assert payload["stale"] == 1 and payload["bytes_reclaimed"] > 0
    assert payload["state_bytes_after"] < payload["state_bytes_before"]
    code, out = _run(["-o", str(tmp_path), "prune-state", "--vacuum",
                      "--query", ".bytes_reclaimed"], capsys)
    assert code == 0 and int(out) >= 0, "re-vacuuming may reclaim 0 (jitter)"


def test_cli_vacuum_clean_store(tmp_path, capsys):
    """Nothing stale: --vacuum still compacts and reports the reclaim
    (clamped at 0 — the fresh timestamp jitters the size)."""
    st = State(tmp_path / "idm.state.json")
    st.set("https://x/here.mp4", status="done", filename="here.mp4")
    (tmp_path / "here.mp4").write_bytes(b"\x00" * 8)
    before = st.path.stat().st_size
    code, out = _run(["-o", str(tmp_path), "prune-state", "--vacuum", "--json"],
                     capsys)
    assert code == 0
    payload = json.loads(out)
    assert payload["stale"] == 0 and payload["live"] == 1
    assert payload["bytes_reclaimed"] >= 0
    assert payload["bytes_reclaimed"] <= before
    assert payload["state_bytes_after"] <= before + 4, "jitter clamped, not grown"


def test_cli_vacuum_is_idempotent(tmp_path):
    """Vacuuming an already-compact store twice stays compact: the second
    run reclaims nothing (clamped at 0 — the fresh 'updated' timestamp
    jitters the byte count by a character or two, so sizes are compared
    with tolerance, and an indented rewrite would be far bigger)."""
    d = tmp_path / "dl"
    d.mkdir()
    st = State(d / "idm.state.json")
    st.set("https://x/a.mp4", status="done", filename="a.mp4")
    (d / "a.mp4").write_bytes(b"\x00" * 8)
    prune_stale_records(st, compact=True)
    compact_size = st.path.stat().st_size
    payload, _ = prune_stale_records(st, compact=True)
    assert abs(st.path.stat().st_size - compact_size) <= 4, "stays compact"
    # clamped at 0 — never negative — but a shorter fresh 'updated'
    # timestamp may legitimately shrink an already-compact store by a
    # byte or two, so exact zero would be a flaky assertion
    reclaimed = payload["bytes_reclaimed"]
    assert reclaimed is not None and reclaimed >= 0, "clamped, never negative"
    assert reclaimed <= 4, "only timestamp jitter, not a real reclaim"
    # the shape is really compact: a single line, no indentation
    body = st.path.read_text(encoding="utf-8")
    assert body.count("\n") == 0 and "\n  " not in body


def test_cli_vacuum_missing_store_text(tmp_path, capsys):
    code, out = _run(["-o", str(tmp_path), "prune-state", "--vacuum"], capsys)
    assert code == 0
    assert "0 stale record(s) removed" in out
    assert "nothing to compact" in out
    assert not (tmp_path / "idm.state.json").exists()


def test_cli_vacuum_missing_store_json(tmp_path, capsys):
    code, out = _run(["-o", str(tmp_path), "prune-state", "--vacuum", "--json"],
                     capsys)
    assert code == 0
    payload = json.loads(out)
    assert payload["compacted"] is True and payload["bytes_reclaimed"] is None


def test_missing_store_is_empty_and_clean(tmp_path):
    payload = scan_stale_records(tmp_path / "none" / "idm.state.json")
    assert payload == {"action": "scan", "dir": str(tmp_path / "none"),
                       "scanned": 0, "stale": 0, "live": 0, "records": []}


# --------------------------------------------------------------------- CLI
def test_cli_dry_run_is_default(tmp_path, capsys):
    State(tmp_path / "idm.state.json").set("https://x/gone.webm",
                                           status="done", filename="gone.webm")
    code, out = _run(["-o", str(tmp_path), "prune-state"], capsys)
    assert code == 0
    assert "stale" in out and "dry run" in out and "gone.webm" in out
    assert len(State(tmp_path / "idm.state.json").records()) == 1, "preview only"


def test_cli_apply_removes_and_keeps(tmp_path, capsys):
    st = State(tmp_path / "idm.state.json")
    st.set("https://x/gone.webm", status="done", filename="gone.webm")
    st.set("https://x/here.mp4", status="done", filename="here.mp4")
    (tmp_path / "here.mp4").write_bytes(b"\x00" * 8)
    code, out = _run(["-o", str(tmp_path), "prune-state", "--apply"], capsys)
    assert code == 0
    assert "removed" in out and "gone.webm" in out and "1 stale record(s)" in out
    assert list(State(tmp_path / "idm.state.json").records()) == ["https://x/here.mp4"]
    assert (tmp_path / "here.mp4").exists()


def test_cli_json_preview_and_apply(tmp_path, capsys):
    State(tmp_path / "idm.state.json").set("https://x/gone.webm",
                                           status="error", filename="gone.webm",
                                           size=1024)
    code, out = _run(["-o", str(tmp_path), "prune-state", "--json"], capsys)
    assert code == 0
    payload = json.loads(out)
    assert payload["action"] == "scan" and payload["stale"] == 1
    assert payload["records"][0]["removed"] is False

    code, out = _run(["-o", str(tmp_path), "prune-state", "--apply", "--json"], capsys)
    assert code == 0
    payload = json.loads(out)
    assert payload["action"] == "apply" and payload["stale"] == 1
    assert payload["records"][0]["removed"] is True
    assert payload["live"] == 0 and payload["scanned"] == 1

    code, out = _run(["-o", str(tmp_path), "prune-state", "--query", ".stale"], capsys)
    assert code == 0 and out == "0\n", "--query implies --json"


def test_cli_json_clean_state_reports_zero(tmp_path, capsys):
    code, out = _run(["-o", str(tmp_path), "prune-state", "--json"], capsys)
    assert code == 0
    payload = json.loads(out)
    assert payload["scanned"] == 0 and payload["stale"] == 0 and payload["records"] == []
