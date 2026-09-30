"""The browser-extension collector: queue store, HTTP bridge, engine handoff.

The extension itself (browser-extension/) is plain JS loaded unpacked; its
contract with these tests is the HTTP surface: /ping, /collect, /download,
/queue, /queue/clear — JSON in/out, 400 on garbage, dedupe on re-POSTs.
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import idm.collect as C


@pytest.fixture()
def qfile(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "COLLECT_QUEUE_FILE", tmp_path / "collect_queue.json")
    return tmp_path / "collect_queue.json"


@pytest.fixture()
def server(qfile):
    log = []
    srv = C.start_collector(0, {"out_dir": str(qfile.parent / "downloads")},
                            queue_path=qfile, log=lambda m, lvl="info": log.append(m))
    yield srv, log
    srv.shutdown()
    srv.server_close()


def url(srv, path):
    return f"http://127.0.0.1:{srv.server_address[1]}{path}"


def get(srv, path):
    with urllib.request.urlopen(url(srv, path)) as r:
        return json.loads(r.read())


def post(srv, path, body=None, raw=None):
    data = raw if raw is not None else json.dumps(body).encode()
    req = urllib.request.Request(url(srv, path), method="POST", data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


# ------------------------------------------------------------------ queue
def test_queue_add_dedupes_and_stamps(qfile):
    n = C.queue_add(["https://x/a.zip", "https://x/b.zip", "https://x/a.zip"],
                    page_url="https://page/", path=qfile)
    assert n == 2                                # duplicate a.zip skipped
    assert C.queue_add(["https://x/a.zip"], path=qfile) == 0
    q = C.load_queue(qfile)
    assert [e["url"] for e in q["pending"]] == ["https://x/a.zip",
                                                "https://x/b.zip"]
    assert q["pending"][0]["page_url"] == "https://page/"
    assert isinstance(q["pending"][0]["ts"], float)


def test_queue_tolerates_mangled_file(qfile):
    qfile.parent.mkdir(exist_ok=True)
    qfile.write_text("{broken", encoding="utf-8")
    assert C.load_queue(qfile) == {"pending": [], "history": []}
    qfile.write_text('{"pending": "junk", "history": [1, {"url": "u"}]}',
                     encoding="utf-8")
    assert C.load_queue(qfile)["history"] == [{"url": "u"}]


def test_queue_take_drains_and_archives(qfile):
    C.queue_add(["https://x/a.zip", "https://x/b.zip"], path=qfile)
    jobs = C.queue_take(qfile)
    assert jobs == [("https://x/a.zip", None), ("https://x/b.zip", None)]
    assert C.load_queue(qfile)["pending"] == []
    assert len(C.load_queue(qfile)["history"]) == 2
    assert C.queue_take(qfile) == []             # second take: nothing left
    # (url, name) pairs name that one download, like a 'url -> name' line
    C.queue_add([("https://x/c.zip", "renamed.bin"), "https://x/d.zip"],
                path=qfile)
    assert C.queue_take(qfile) == [("https://x/c.zip", "renamed.bin"),
                                   ("https://x/d.zip", None)]


def test_queue_payload_counts(qfile):
    C.queue_add(["https://x/a.zip"], path=qfile)
    p = C.queue_payload(C.load_queue(qfile))
    assert p["pending_count"] == 1 and len(p["pending"]) == 1
    assert p["history"] == []


# ----------------------------------------------------------------- server
def test_ping_reports_version_and_out_dir(server):
    srv, _ = server
    data = get(srv, "/ping")
    assert data["ok"] is True and data["version"]
    assert data["out_dir"].endswith("downloads")


def test_collect_and_queue_roundtrip(server, qfile):
    srv, log = server
    data = post(srv, "/collect", {"urls": ["https://x/a.zip"],
                                  "page_url": "https://page/"})
    assert data == {"ok": True, "added": 1, "pending": 1}
    assert any("queued 1 link(s)" in m for m in log)
    q = get(srv, "/queue")
    assert q["pending_count"] == 1
    assert q["pending"][0]["page_url"] == "https://page/"
    # plain-text batch lists work too (parse_job_lines semantics)
    req = urllib.request.Request(url(srv, "/collect"), method="POST",
                                 data=b"https://x/plain.zip -> renamed.zip\n"
                                      b"# comment\n",
                                 headers={"Content-Type": "text/plain"})
    with urllib.request.urlopen(req) as r:
        data = json.loads(r.read())
    assert data["added"] == 1
    jobs = C.queue_take(qfile)
    assert ("https://x/plain.zip", "renamed.zip") in jobs


def test_download_endpoint_starts_the_engine(server, qfile):
    srv, log = server

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = b"x" * 4096
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    fsrv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=fsrv.serve_forever, daemon=True).start()
    try:
        data = post(srv, "/download",
                    {"urls": [f"http://127.0.0.1:{fsrv.server_address[1]}/f.bin"]})
        assert data["ok"] is True and data["started"] == 1
        assert data["summary"]["ok"] == 1 and data["summary"]["all_ok"] is True
        dest = qfile.parent / "downloads" / "f.bin"
        assert dest.read_bytes() == b"x" * 4096
        assert any("download(s) finished" in m for m in log)
        assert C.load_queue(qfile)["history"]     # archived after the run
    finally:
        fsrv.shutdown()
        fsrv.server_close()


def test_bad_body_is_400_not_crash(server):
    srv, _ = server
    req = urllib.request.Request(url(srv, "/collect"), method="POST",
                                 data=b"{oops", headers={"Content-Type": "application/json"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req)
    assert e.value.code == 400 and "bad JSON" in e.value.read().decode()


def test_no_urls_is_400(server):
    srv, _ = server
    with pytest.raises(urllib.error.HTTPError) as e:
        post(srv, "/collect", {})
    assert e.value.code == 400


def test_unknown_path_is_404(server):
    srv, _ = server
    with pytest.raises(urllib.error.HTTPError) as e:
        get(srv, "/nope")
    assert e.value.code == 404


def test_clear_endpoint_empties_pending(server, qfile):
    srv, _ = server
    C.queue_add(["https://x/a.zip", "https://x/b.zip"], path=qfile)
    assert post(srv, "/queue/clear") == {"ok": True, "cleared": 2}
    assert C.load_queue(qfile)["pending"] == []
    assert post(srv, "/queue/clear")["cleared"] == 0


def test_binds_loopback_only(server):
    srv, _ = server
    assert srv.server_address[0] == "127.0.0.1"


# ------------------------------------------------- concurrency + binding
def test_concurrent_queue_mutations_do_not_lose_captures(qfile):
    # regression: queue_take (a /download drain) racing queue_add (an
    # arriving /collect) used to overwrite the added capture wholesale
    C.queue_add(["https://x/keep.zip"], path=qfile)
    barrier = threading.Barrier(2)

    def take():
        barrier.wait()
        C.queue_take(qfile)

    def collect():
        barrier.wait()
        C.queue_add(["https://x/new.zip"], path=qfile)

    t1 = threading.Thread(target=take)
    t2 = threading.Thread(target=collect)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    q = C.load_queue(qfile)
    urls = [e["url"] for e in q["pending"]] + [e["url"] for e in q["history"]]
    assert "https://x/keep.zip" in urls      # the drain still archived it
    assert "https://x/new.zip" in urls       # the capture was not lost


def test_second_collector_on_same_port_is_refused(qfile):
    # Windows SO_REUSEADDR would let a second bind silently steal requests;
    # the collector deliberately disables it so a double-start raises (the
    # GUI turns that into its 'another PyIDM may be running' warning)
    srv = C.start_collector(0, {}, queue_path=qfile)
    try:
        port = srv.server_address[1]
        with pytest.raises(OSError):
            C.CollectorServer(port, {})
    finally:
        srv.shutdown()
        srv.server_close()
