"""'idm get' / 'idm batch' --json: scriptable download summaries.

Real downloads against the same local HTTP server harness as
tests/test_integration.py, driven in-process through the CLI.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from idm import gui as G
from idm.cli import main as cli_main
from idm.core import DownloadTask

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


def _url(server, path: str) -> str:
    return f"http://127.0.0.1:{server.server_address[1]}{path}"


# ------------------------------------------------------------------- helper
def test_batch_payload_pure_helper():

    results = [
        DownloadTask(url="http://x/a.zip", filename="a.zip", status="done",
                     downloaded=1024, dest=None),
        DownloadTask(url="http://x/b.bin", filename="", status="error",
                     message="HTTP 500", downloaded=0),
        DownloadTask(url="http://x/c.bin", filename="c.bin", status="skipped",
                     downloaded=2048),
        DownloadTask(url="http://x/d.mp4", filename="d.mp4", status="done",
                     downloaded=4096, note="file saved, but the URL served JPEG"),
    ]
    p = G.batch_payload(results)
    assert p["count"] == 4 and p["ok"] == 3 and p["all_ok"] is False
    assert p["total_bytes"] == 1024 + 2048 + 4096
    assert p["by_status"] == {"done": 2, "error": 1, "skipped": 1}
    d = p["downloads"]
    assert d[0]["status"] == "done" and d[0]["total_bytes"] == 1024
    assert d[0]["message"] == "" and d[0]["note"] == ""   # clean done row
    assert d[1]["filename"] == "" and d[1]["message"] == "HTTP 500"
    assert d[1]["total_bytes"] == 0
    assert d[2]["status"] == "skipped" and d[2]["message"] == ""
    assert d[3]["status"] == "done" and d[3]["note"].startswith("file saved")
    assert json.dumps(p)                              # JSON-able


def test_batch_payload_empty():
    assert G.batch_payload([]) == {"count": 0, "ok": 0, "all_ok": True,
                                   "total_bytes": 0, "by_status": {},
                                   "skipped_count": 0, "skipped": [],
                                   "downloads": []}


# ---------------------------------------------------------------------- CLI
def test_get_json_success(server, tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code = cli_main(["get", _url(server, "/one.bin"), _url(server, "/two.bin"),
                     "-o", str(tmp_path), "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 2 and payload["ok"] == 2
    assert payload["all_ok"] is True
    assert payload["by_status"] == {"done": 2}
    assert payload["total_bytes"] == 2 * len(PAYLOAD)
    assert [d["filename"] for d in payload["downloads"]] == ["one.bin", "two.bin"]
    assert all(d["status"] == "done" for d in payload["downloads"])
    assert all(d["total_bytes"] == len(PAYLOAD) for d in payload["downloads"])
    assert (tmp_path / "one.bin").read_bytes() == PAYLOAD


def test_get_json_stdout_stays_parseable(server, tmp_path, capsys, monkeypatch):
    # --json suppresses progress bars AND log lines: nothing else on stdout
    monkeypatch.chdir(tmp_path)
    cli_main(["get", _url(server, "/one.bin"), "-o", str(tmp_path), "--json"])
    out = capsys.readouterr().out
    assert out.lstrip().startswith("{")          # nothing but JSON on stdout
    json.loads(out)  # the whole stdout is exactly one JSON document


def test_batch_json_success(server, tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "urls.txt"
    src.write_text(f"{_url(server, '/a.bin')}\n"
                   f"{_url(server, '/b.bin')} -> renamed.bin\n", encoding="utf-8")
    code = cli_main(["batch", str(src), "-o", str(tmp_path), "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["all_ok"] is True and payload["count"] == 2
    by_name = {d["filename"]: d for d in payload["downloads"]}
    assert set(by_name) == {"a.bin", "renamed.bin"}
    assert (tmp_path / "renamed.bin").read_bytes() == PAYLOAD


def test_get_json_exit_1_on_failure(server, tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code = cli_main(["get", _url(server, "/ok.bin"), _url(server, "/bad.bin"),
                     "-o", str(tmp_path), "--json"])
    assert code == 1                                   # mirrors text mode
    payload = json.loads(capsys.readouterr().out)
    assert payload["all_ok"] is False and payload["ok"] == 1
    assert payload["count"] == 2
    assert payload["by_status"]["done"] == 1 and payload["by_status"]["error"] == 1
    assert payload["downloads"][0]["total_bytes"] == len(PAYLOAD)  # real numbers
    assert payload["downloads"][1]["message"]          # failure reason present


def test_text_mode_exit_code_unchanged(server, tmp_path, capsys, monkeypatch):
    assert cli_main(["get", _url(server, "/ok.bin"),
                     "-o", str(tmp_path)]) == 0
    assert cli_main(["get", _url(server, "/bad.bin"),
                     "-o", str(tmp_path)]) == 1
    assert "{" not in capsys.readouterr().out          # no JSON in text mode


def test_batch_json_empty_list(tmp_path, capsys, monkeypatch):
    # text mode exits 1 for an empty list (input error); --json mirrors that
    # but still emits the payload shape so scripts can parse the result
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "empty.txt"
    src.write_text("# nothing here\n", encoding="utf-8")
    assert cli_main(["batch", str(src), "-o", str(tmp_path), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 0 and payload["all_ok"] is True


# ------------------------------------------------------------------ parser
def test_parser_get_batch_json_default_false():
    import idm.cli as C
    assert C.build_parser().parse_args(["get", "u"]).json is False
    assert C.build_parser().parse_args(["get", "u", "--json"]).json is True
    assert C.build_parser().parse_args(["batch"]).json is False
    assert C.build_parser().parse_args(["batch", "--json"]).json is True
