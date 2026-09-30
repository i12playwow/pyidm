"""'idm --query': a jq-style filter over every --json output.

Real downloads for the get/batch surfaces (same local-HTTP pattern as
tests/test_integration.py), mocked stores for the reporting surfaces.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from idm import gui as G
from idm.cli import main as cli_main
from idm.jq import JqError, apply_query, format_output, query_or_none

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


# ------------------------------------------------------------- evaluator
DOC = {
    "count": 2, "ok": 1, "all_ok": False, "total_bytes": 100,
    "by_status": {"done": 1, "error": 1},
    "downloads": [
        {"url": "u1", "filename": "a.zip", "status": "done", "total_bytes": 100},
        {"url": "u2", "filename": "b.bin", "status": "error", "total_bytes": 0,
         "message": "HTTP 500"},
    ],
}


def test_query_paths_and_indexing():
    assert apply_query(".count", DOC) == 2
    assert apply_query(".downloads[0].filename", DOC) == "a.zip"
    assert apply_query(".downloads[-1].status", DOC) == "error"
    assert apply_query(".downloads[].filename", DOC) == ["a.zip", "b.bin"]
    assert apply_query(".", DOC) == DOC


def test_query_pipes_and_builtins():
    assert apply_query(".downloads | length", DOC) == 2
    assert apply_query(".downloads[0] | keys", DOC) == \
        ["filename", "status", "total_bytes", "url"]
    assert apply_query(".downloads[].total_bytes | add", DOC) == 100
    assert apply_query(".downloads | first | .status", DOC) == "done"
    assert apply_query(".downloads | last | .status", DOC) == "error"
    assert apply_query(".downloads[].total_bytes | max", DOC) == 100


def test_query_compare_logic_arith():
    assert apply_query('.downloads[1].message == "HTTP 500"', DOC) is True
    assert apply_query(".ok == .count", DOC) is False
    assert apply_query(".ok == 1 and .count == 2", DOC) is True
    assert apply_query('.downloads[1].status != "done"', DOC) is True
    assert apply_query(".count + 1", DOC) == 3
    assert apply_query(".missing", DOC) is None
    assert apply_query(".downloads[].nope", DOC) == []
    assert apply_query(".nothing | length", DOC) == 0


def test_query_errors():
    for bad in (".downloads[0].bogus[", ".count +", "bogus",
                ".downloads[0].status >"):
        with pytest.raises(JqError):
            apply_query(bad, DOC)
    assert query_or_none("bogus", DOC) is None      # error -> None


def test_query_select_stream_filtering():
    # select() after '.[]' filters element-wise, like jq's streams
    assert apply_query('.downloads[] | select(.status == "error") | .filename',
                       DOC) == ["b.bin"]
    assert apply_query('.downloads[] | select(.total_bytes > 0) | .filename',
                       DOC) == ["a.zip"]
    assert apply_query(".downloads[] | select(.message != null) | length", DOC) == 1
    assert apply_query('.downloads[] | select(.status == "done" and .total_bytes >= 100) | .url',
                       DOC) == ["u1"]
    assert apply_query('.downloads[] | select(.status == "cancelled") | .url',
                       DOC) == []            # no matches -> empty


def test_query_select_plain_value_semantics():
    # without a stream, select tests the value as-is (jq-exact):
    # falsy -> None (jq's empty), truthy -> the value itself
    assert apply_query("select(.all_ok)", DOC) is None
    assert apply_query(".ok | select(.)", DOC) == 1
    # a plain list is tested whole, not element-wise
    assert apply_query(".downloads | select(length == 2) | length", DOC) == 2
    assert apply_query(".downloads | select(length == 5)", DOC) is None
    # == never raises; mismatched </> does (via apply_query's clean JqError)
    assert apply_query(".downloads | select(. == 5)", DOC) is None
    with pytest.raises(JqError):
        apply_query(".downloads | select(. > 5)", DOC)


def test_query_select_output_stays_jsonable():
    out = apply_query('.downloads[] | select(.status == "error")', DOC)
    assert isinstance(out, list) and json.dumps(out)
    assert [d["url"] for d in out] == ["u2"]


def test_query_map():
    assert apply_query(".downloads | map(.filename)", DOC) == ["a.zip", "b.bin"]
    assert apply_query(".downloads | map(.total_bytes) | add", DOC) == 100
    assert apply_query(".downloads | map(keys) | first", DOC) == \
        ["filename", "status", "total_bytes", "url"]
    # stream form transforms each element (documented divergence from jq):
    # two dicts -> two transformed values
    assert apply_query(".downloads[] | map(.) | length", DOC) == 2
    with pytest.raises(JqError):
        apply_query(".count | map(.)", DOC)          # map: not a list


def test_query_has_and_startswith():
    assert apply_query('.downloads[1] | has("message")', DOC) is True
    assert apply_query('.downloads[0] | has("message")', DOC) is False
    assert apply_query('.downloads | has(0)', DOC) is True
    assert apply_query('.downloads | has(9)', DOC) is False
    assert apply_query('.downloads[0].filename | startswith("a")', DOC) is True
    assert apply_query('.downloads[0].filename | startswith("b")', DOC) is False
    # composed with select: the URL-prefix filter idiom
    assert apply_query('.downloads[] | select(.url | startswith("u")) | .filename',
                       DOC) == ["a.zip", "b.bin"]
    with pytest.raises(JqError):
        apply_query(".count | startswith(\"x\")", DOC)   # not a string
    with pytest.raises(JqError):
        apply_query(".count | has(\"k\")", DOC)          # not a dict/list


def test_query_endswith_and_contains():
    assert apply_query('.downloads[0].filename | endswith(".zip")', DOC) is True
    assert apply_query('.downloads[0].filename | endswith(".bin")', DOC) is False
    assert apply_query('.downloads[0].url | contains("1")', DOC) is True
    assert apply_query('.downloads[0].url | contains("9")', DOC) is False
    # endswith is the file-extension filter idiom; contains the substring one
    assert apply_query('.downloads[] | select(.filename | endswith(".bin")) | .url',
                       DOC) == ["u2"]
    assert apply_query('.downloads[] | select(.filename | contains(".")) | .url',
                       DOC) == ["u1", "u2"]
    # empty needle is contained in anything, like Python's 'in'
    assert apply_query('.downloads[0].filename | contains("")', DOC) is True
    # strings-only, same surface as startswith
    with pytest.raises(JqError):
        apply_query(".count | endswith(\"x\")", DOC)   # not a string
    with pytest.raises(JqError):
        apply_query(".count | contains(\"x\")", DOC)   # not a string
    with pytest.raises(JqError):
        apply_query('.downloads[0].filename | contains(4)', DOC)  # not a string arg
    with pytest.raises(JqError):
        apply_query("endswith(\"x\")", DOC)


def test_query_optional_iteration():
    # '.[]?' swallows the iterate error, like jq's error-suppression suffix
    assert apply_query(".count | .[]?", DOC) == []
    assert apply_query(".missing | .[]?", DOC) == []
    assert apply_query(".downloads[]? | .filename", DOC) == ["a.zip", "b.bin"]
    # plain '.[]' on a non-iterable still raises
    with pytest.raises(JqError):
        apply_query(".count | .[]", DOC)


def test_format_output_raw():
    assert format_output("a.zip", raw=True) == "a.zip"
    assert format_output(True, raw=True) == "true"
    assert format_output(None, raw=True) == "null"
    assert format_output(False, raw=True) == "false"
    assert format_output(3, raw=True) == "3"
    assert format_output(["a", "b"], raw=True) == "a\nb"   # one per line
    assert format_output([{"x": 1}], raw=True) == '{"x":1}'  # nested -> compact JSON
    assert format_output("a.zip", raw=False) == '"a.zip"'
    assert format_output(["a", "b"], raw=False) == \
        json.dumps(["a", "b"], indent=2)


# ------------------------------------------------------------------ get
def test_get_query(server, tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code = cli_main(["get", _url(server, "/one.bin"), _url(server, "/two.bin"),
                     "-o", str(tmp_path), "--query", ".downloads[].filename"])
    assert code == 0
    assert json.loads(capsys.readouterr().out) == ["one.bin", "two.bin"]


def test_get_query_raw(server, tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code = cli_main(["get", _url(server, "/one.bin"),
                     "-o", str(tmp_path), "--query", ".downloads[0].status", "-r"])
    assert code == 0
    assert capsys.readouterr().out == "done\n"       # bare, no quotes


def test_get_query_raw_streams_list_lines(server, tmp_path, capsys, monkeypatch):
    # jq -r streams: '[].field' with -r prints one value per line
    monkeypatch.chdir(tmp_path)
    cli_main(["get", _url(server, "/one.bin"),
              "-o", str(tmp_path), "--query", ".downloads[].status", "-r"])
    assert capsys.readouterr().out == "done\n"


def test_get_query_preserves_exit_code(server, tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code = cli_main(["get", _url(server, "/ok.bin"), _url(server, "/bad.bin"),
                     "-o", str(tmp_path), "--query", ".ok", "-r"])
    assert code == 1                                 # mirrors plain --json
    assert capsys.readouterr().out == "1\n"


def test_get_query_bad_query_is_error(server, tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code = cli_main(["get", _url(server, "/one.bin"),
                     "-o", str(tmp_path), "--query", "bogus"])
    assert code == 1
    assert "invalid --query" in capsys.readouterr().out


def test_get_query_implies_json_quiet(server, tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cli_main(["get", _url(server, "/one.bin"),
              "-o", str(tmp_path), "--query", ".downloads[0].filename", "-r"])
    assert capsys.readouterr().out == "one.bin\n"    # no progress/log noise


# ---------------------------------------------------------------- batch
def test_batch_query(server, tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "urls.txt"
    src.write_text(f"{_url(server, '/a.bin')}\n"
                   f"{_url(server, '/b.bin')} -> renamed.bin\n", encoding="utf-8")
    code = cli_main(["batch", str(src), "-o", str(tmp_path),
                     "--query", ".downloads[].filename"])
    assert code == 0
    assert json.loads(capsys.readouterr().out) == ["a.bin", "renamed.bin"]


# ------------------------------------------------------- reporting surfaces
HIST = [
    {"path": "C:/v/A.mkv", "ok": True, "dest": "C:/v/A.en.srt", "language": "en",
     "provider": "subtitlecat", "size": 1000, "ts": 1_700_000_000},
    {"path": "C:/v/B.mp4", "ok": True, "dest": "C:/v/B.en.srt", "language": "en",
     "provider": "opensubtitles", "size": 2000, "ts": 1_700_000_100},
    {"path": "C:/v/C.mkv", "ok": False, "message": "no subs found",
     "ts": 1_700_000_200},
]
RECORDS = {
    "https://x/a.zip": {"status": "error", "filename": "a.zip",
                        "size": 1024, "updated": 1_700_000_000},
    "https://x/b.bin": {"status": "downloading", "filename": "b.bin",
                        "size": 2048, "updated": 1_700_000_100},
}


@pytest.fixture
def stores(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "idm.state.json").write_text(
        json.dumps({"version": 1, "downloads": RECORDS}), encoding="utf-8")
    hp = tmp_path / "subs.json"
    hp.write_text(json.dumps(HIST), encoding="utf-8")
    monkeypatch.setattr(G, "SUBS_HISTORY_FILE", hp)
    return tmp_path


def test_history_query(stores, capsys, monkeypatch):
    monkeypatch.setattr(G, "open_file_safe", lambda p: True)
    monkeypatch.setattr(G, "open_file_with", lambda p, v: True)
    assert cli_main(["history", "--query", "length"]) == 0
    assert json.loads(capsys.readouterr().out) == 2  # OK entries only


def test_history_query_raw(stores, capsys):
    assert cli_main(["history", "--query", ".[].provider", "-r"]) == 0
    assert capsys.readouterr().out == "subtitlecat\nopensubtitles\n"


def test_downloads_query(stores, capsys):
    assert cli_main(["-o", str(stores), "downloads", "--query", "[].status", "-r"]) == 0
    assert sorted(capsys.readouterr().out.split()) == ["downloading", "error"]


def test_stats_query(stores, capsys):
    assert cli_main(["-o", str(stores), "stats",
                     "--query", ".downloads.by_status"]) == 0
    out = capsys.readouterr().out
    assert json.loads(out) == {"downloading": 1, "error": 1}


def test_stats_query_raw(stores, capsys):
    assert cli_main(["-o", str(stores), "stats",
                     "--query", ".downloads.total_bytes", "-r"]) == 0
    assert capsys.readouterr().out == "3072\n"


def test_downloads_query_select(server, stores, capsys, tmp_path):
    # end-to-end: filter errored records straight from the state file
    assert cli_main(["-o", str(stores), "downloads",
                     "--query", '[] | select(.status == "error") | .filename',
                     "-r"]) == 0
    assert capsys.readouterr().out == "a.zip\n"


def test_downloads_query_map_has(stores, capsys):
    assert cli_main(["-o", str(stores), "downloads",
                     "--query", "map(.filename)", "-r"]) == 0
    assert capsys.readouterr().out == "a.zip\nb.bin\n"
    assert cli_main(["-o", str(stores), "downloads",
                     "--query", '.[0] | has("message")', "-r"]) == 0
    assert capsys.readouterr().out == "false\n"


def test_history_query_startswith_optional_iter(stores, capsys):
    assert cli_main(["history",
                     "--query", '.[].dest | select(startswith("C:"))',
                     "-r"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("C:/") and out.count("\n") == 2
    # '.[]?' tolerates the query running against an empty entry list
    assert cli_main(["history", "--provider", "nomatch",
                     "--query", ".[]? | .language", "-r"]) == 0
    assert capsys.readouterr().out == ""


def test_stats_watch_rejects_query(stores, capsys):
    assert cli_main(["-o", str(stores), "stats",
                     "--watch", "1", "--query", ".ok"]) == 1


# ------------------------------------------------------------- providers
def test_providers_query(monkeypatch, capsys):
    import idm.health as H
    from idm.health import ProviderHealth
    fake = [ProviderHealth("subtitlecat", "SubtitleCat", "ok", "reachable",
                           endpoint="https://subtitlecat.com", hints=[])]
    monkeypatch.setattr(H, "run_checks", lambda *a, **k: fake)
    assert cli_main(["providers", "--query", ".providers[].name", "-r"]) == 0
    assert capsys.readouterr().out == "subtitlecat\n"
    monkeypatch.setattr(H, "run_checks", lambda *a, **k: [])
    assert cli_main(["providers", "--query", ".all_ok", "-r"]) == 0
    assert capsys.readouterr().out == "true\n"       # exit stays 0 when all ok


# ------------------------------------------------------------ plain --json
def test_downloads_json_flag(stores, capsys):
    assert cli_main(["-o", str(stores), "downloads", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert isinstance(data, list) and len(data) == 2
    assert {d["url"] for d in data} == set(RECORDS)
    assert {d["filename"] for d in data} == {"a.zip", "b.bin"}


def test_history_json_same_data_as_query_identity(stores, capsys):
    assert cli_main(["history", "--json"]) == 0
    plain = json.loads(capsys.readouterr().out)
    assert cli_main(["history", "--query", "."]) == 0
    queried = json.loads(capsys.readouterr().out)
    assert plain == queried                          # --query never drifts


# ---------------------------------------------------------------- parser
def test_parser_query_flags():
    import idm.cli as C
    p = C.build_parser()
    assert p.parse_args(["get", "u"]).query is None
    assert p.parse_args(["get", "u", "--query", ".ok"]).query == ".ok"
    assert p.parse_args(["get", "u"]).raw is False
    assert p.parse_args(["get", "u", "-r"]).raw is True
    assert p.parse_args(["batch", "--query", "x", "-r"]).raw is True
    assert p.parse_args(["stats", "--query", "x"]).query == "x"
    assert p.parse_args(["stats"]).raw is False
    assert p.parse_args(["history", "--query", "x"]).query == "x"
    assert p.parse_args(["downloads", "--query", "x"]).query == "x"
    assert p.parse_args(["providers", "--query", "x"]).query == "x"
    assert p.parse_args(["downloads", "--json"]).json is True
    assert p.parse_args(["history", "--json"]).json is True
