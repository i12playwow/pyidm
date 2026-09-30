"""'idm resume --json/--query': scriptable summaries for resumed downloads.

Real downloads against the same local HTTP server harness as
tests/test_integration.py, driven in-process through the CLI.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from idm.cli import main as cli_main

PAYLOAD = bytes(range(256)) * 1024  # 256 KiB deterministic body


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence test output
        pass

    def do_GET(self):
        if self.path == "/bad.bin":
            self.send_response(500)
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self.send_response(200)
            self.send_header("Content-Length", str(len(PAYLOAD)))
            self.end_headers()
            self.wfile.write(PAYLOAD)


@pytest.fixture()
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()


@pytest.fixture(autouse=True)
def _quiet_console(monkeypatch):
    # no explicit file: rich resolves sys.stdout at print time, so capsys
    # sees both rich output and the raw sys.stdout JSON
    import idm.cli as C
    monkeypatch.setattr(C, "console", C.Console(force_terminal=False, width=250))


def _store(tmp_path, records: dict) -> None:
    (tmp_path / "idm.state.json").write_text(
        json.dumps({"version": 1, "downloads": records}), encoding="utf-8")


def _plant_partials(tmp_path, records: dict) -> None:
    """Unfinished records get their .part file on disk — the state a real
    interrupted download leaves. Records without any file are ghosts:
    resume skips them and reports them as skipped."""
    for rec in records.values():
        if rec.get("status") != "done" and rec.get("filename"):
            (tmp_path / (rec["filename"] + ".part")).write_bytes(b"part")


def _url(server, path: str) -> str:
    return f"http://127.0.0.1:{server.server_address[1]}{path}"


def test_resume_json_success(server, tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    records = {_url(server, "/a.bin"): {
        "status": "error", "filename": "a.bin", "size": len(PAYLOAD),
        "updated": 1_700_000_000}}
    _store(tmp_path, records)
    _plant_partials(tmp_path, records)
    code = cli_main(["-o", str(tmp_path), "resume", "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 1 and payload["ok"] == 1
    assert payload["all_ok"] is True
    assert payload["by_status"] == {"done": 1}
    assert payload["skipped_count"] == 0 and payload["skipped"] == []
    assert payload["downloads"][0]["filename"] == "a.bin"
    assert payload["downloads"][0]["total_bytes"] == len(PAYLOAD)
    # the finished record is removed from the state file, like text mode
    after = json.loads((tmp_path / "idm.state.json").read_text(encoding="utf-8"))
    assert after["downloads"] == {}


def test_resume_json_skips_done_records(server, tmp_path, capsys, monkeypatch):
    # a 'done' record in the store is not retried and not reported
    monkeypatch.chdir(tmp_path)
    _store(tmp_path, {_url(server, "/done.bin"): {
        "status": "done", "filename": "done.bin", "updated": 1_700_000_000}})
    code = cli_main(["-o", str(tmp_path), "resume", "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"count": 0, "ok": 0, "all_ok": True, "total_bytes": 0,
                       "by_status": {}, "skipped_count": 0, "skipped": [],
                       "downloads": []}


def test_resume_json_empty_store(server, tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code = cli_main(["-o", str(tmp_path), "resume", "--json"])
    assert code == 0                                   # mirrors text mode
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 0 and payload["all_ok"] is True


def test_resume_json_exit_1_on_failure(server, tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    records = {
        _url(server, "/ok.bin"): {"status": "error", "filename": "ok.bin",
                                  "updated": 1_700_000_000},
        _url(server, "/bad.bin"): {"status": "downloading", "filename": "bad.bin",
                                   "updated": 1_700_000_100}}
    _store(tmp_path, records)
    _plant_partials(tmp_path, records)
    code = cli_main(["-o", str(tmp_path), "resume", "--json"])
    assert code == 1                                   # mirrors text mode
    payload = json.loads(capsys.readouterr().out)
    assert payload["all_ok"] is False and payload["ok"] == 1
    assert payload["by_status"] == {"done": 1, "error": 1}


def test_resume_query_raw(server, tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    records = {_url(server, "/a.bin"): {
        "status": "error", "filename": "a.bin", "updated": 1_700_000_000}}
    _store(tmp_path, records)
    _plant_partials(tmp_path, records)
    code = cli_main(["-o", str(tmp_path), "resume",
                     "--query", ".downloads[].filename", "-r"])
    assert code == 0
    assert capsys.readouterr().out == "a.bin\n"        # jq -r stream, no noise


def test_resume_query_bad_query(server, tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    records = {_url(server, "/a.bin"): {
        "status": "error", "filename": "a.bin", "updated": 1_700_000_000}}
    _store(tmp_path, records)
    _plant_partials(tmp_path, records)
    code = cli_main(["-o", str(tmp_path), "resume", "--query", "bogus"])
    assert code == 1
    assert "invalid --query" in capsys.readouterr().out


def test_text_mode_exit_codes_unchanged(server, tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cli_main(["-o", str(tmp_path), "resume"]) == 0      # empty store
    assert "{" not in capsys.readouterr().out
    records = {_url(server, "/ok.bin"): {
        "status": "error", "filename": "ok.bin", "updated": 1_700_000_000}}
    _store(tmp_path, records)
    _plant_partials(tmp_path, records)
    assert cli_main(["-o", str(tmp_path), "resume"]) == 0      # all succeed
    records = {_url(server, "/bad.bin"): {
        "status": "error", "filename": "bad.bin", "updated": 1_700_000_000}}
    _store(tmp_path, records)
    _plant_partials(tmp_path, records)
    assert cli_main(["-o", str(tmp_path), "resume"]) == 1      # failure
    assert "{" not in capsys.readouterr().out          # no JSON in text mode


# ----------------------------------------------------- ghosts (file missing)
def test_resume_skips_ghost_and_hints_prune(server, tmp_path, capsys,
                                            monkeypatch):
    """An unfinished record whose file (and partial) is gone is never
    retried: text mode reports it and points at 'idm prune-state'."""
    monkeypatch.chdir(tmp_path)
    _store(tmp_path, {
        _url(server, "/ghost.bin"): {"status": "error",
                                     "filename": "ghost.bin",
                                     "updated": 1_700_000_000},
        _url(server, "/ok.bin"): {"status": "error", "filename": "ok.bin",
                                  "updated": 1_700_000_100}})
    _plant_partials(tmp_path, {"k": {"status": "error", "filename": "ok.bin"}})
    code = cli_main(["-o", str(tmp_path), "resume"])
    assert code == 0, "the retryable download succeeded"
    out = capsys.readouterr().out
    assert "[skip] ghost.bin" in out and "file no longer exists" in out
    assert "idm prune-state" in out
    after = json.loads((tmp_path / "idm.state.json").read_text(encoding="utf-8"))
    assert set(after["downloads"]) == {_url(server, "/ghost.bin")}, \
        "the ghost record is kept (prune owns deletion)"


def test_resume_all_ghosts_reports_and_exits_zero(server, tmp_path, capsys,
                                                  monkeypatch):
    monkeypatch.chdir(tmp_path)
    _store(tmp_path, {_url(server, "/ghost.bin"): {
        "status": "downloading", "filename": "ghost.bin",
        "updated": 1_700_000_000}})
    code = cli_main(["-o", str(tmp_path), "resume"])
    assert code == 0, "nothing retried, nothing failed"
    out = capsys.readouterr().out
    assert "1 record(s) skipped" in out and "idm prune-state" in out
    assert "resuming" not in out


def test_resume_json_reports_skipped_ghosts(server, tmp_path, capsys,
                                            monkeypatch):
    monkeypatch.chdir(tmp_path)
    _store(tmp_path, {
        _url(server, "/ghost.bin"): {"status": "error",
                                     "filename": "ghost.bin",
                                     "updated": 1_700_000_000},
        _url(server, "/ok.bin"): {"status": "error", "filename": "ok.bin",
                                  "updated": 1_700_000_100}})
    _plant_partials(tmp_path, {"k": {"status": "error", "filename": "ok.bin"}})
    code = cli_main(["-o", str(tmp_path), "resume", "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["skipped_count"] == 1
    assert payload["skipped"] == [{"url": _url(server, "/ghost.bin"),
                                   "filename": "ghost.bin"}]
    ghost_row = payload["downloads"][0]
    assert ghost_row["status"] == "skipped-ghost"
    assert "idm prune-state" in ghost_row["message"]
    assert ghost_row["total_bytes"] == 0 and ghost_row["dest"] == ""
    assert payload["count"] == 2 and payload["ok"] == 1
    assert payload["by_status"] == {"done": 1}
    assert payload["all_ok"] is True, "ghosts are not failures"


def test_resume_json_all_ghosts_payload(server, tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _store(tmp_path, {_url(server, "/ghost.bin"): {
        "status": "error", "filename": "ghost.bin", "updated": 1_700_000_000}})
    code = cli_main(["-o", str(tmp_path), "resume", "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 1 and payload["ok"] == 0
    assert payload["skipped_count"] == 1
    assert payload["downloads"][0]["status"] == "skipped-ghost"
    assert payload["all_ok"] is True


def test_resume_partial_file_is_never_a_ghost(server, tmp_path, capsys,
                                              monkeypatch):
    """A '.part' on disk means the download can resume — it must be
    retried, not skipped."""
    monkeypatch.chdir(tmp_path)
    records = {_url(server, "/a.bin"): {
        "status": "error", "filename": "a.bin", "updated": 1_700_000_000}}
    _store(tmp_path, records)
    _plant_partials(tmp_path, records)
    code = cli_main(["-o", str(tmp_path), "resume", "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["skipped_count"] == 0 and payload["by_status"] == {"done": 1}


# ------------------------------------------------------------------ parser
def test_parser_resume_json_flags():
    import idm.cli as C
    p = C.build_parser()
    assert p.parse_args(["resume"]).json is False
    assert p.parse_args(["resume"]).query is None
    assert p.parse_args(["resume"]).raw is False
    assert p.parse_args(["resume", "--json"]).json is True
    assert p.parse_args(["resume", "--query", ".ok"]).query == ".ok"
    assert p.parse_args(["resume", "-r"]).raw is True
