"""Integration tests: resume, expiring-link refresh, cancel, flaky connections,
and multi-connection segmented downloads — against a local HTTP server."""
from __future__ import annotations

import json
import re
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from idm.core import Downloader
from idm.state import State

PAYLOAD = bytes(range(256)) * 4096  # 1 MiB deterministic body
JPEG_BYTES = b"\xff\xd8\xff\xe0JFIF\x00" + bytes(range(256)) * 4
M3U_BYTES = b"#EXTM3U\n#EXT-X-TARGETDURATION:10\n#EXTINF:9.0,\nseg1.ts\n"
MP4_BYTES = b"\x00\x00\x00\x18ftypmp42" + bytes(range(256)) * 4
HTML_PAGE = (
    b"<!DOCTYPE html>\n<html><head><title>Hotlink protection</title></head>"
    b"<body><h1>Access denied</h1><p>Please visit the site first.</p></body></html>\n"
)


class Handler(BaseHTTPRequestHandler):
    def _port(self) -> int:
        return self.server.server_address[1]

    def log_message(self, *a):  # silence test output
        pass

    def _serve_ranged(self) -> None:
        """Serve PAYLOAD with full Range support."""
        rng = self.headers.get("Range")
        if rng:
            self.server.ranged_hits += 1
            m = re.match(r"bytes=(\d+)-(\d*)", rng)
            start = int(m.group(1)) if m else 0
            end = int(m.group(2)) if (m and m.group(2)) else len(PAYLOAD) - 1
            end = min(end, len(PAYLOAD) - 1)
            data = PAYLOAD[start: end + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(PAYLOAD)}")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("ETag", '"v1"')
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_response(200)
            self.send_header("Content-Length", str(len(PAYLOAD)))
            self.send_header("ETag", '"v1"')
            self.end_headers()
            self.wfile.write(PAYLOAD)

    def do_GET(self):
        p = urllib.parse.urlparse(self.path).path
        if p == "/file.bin":
            self._serve_ranged()
        elif p == "/expiring.bin":
            if self.headers.get("Authorization") != "Bearer good":
                body = b'{"error": "link expired"}'
                self.send_response(403)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(PAYLOAD)))
            self.end_headers()
            self.wfile.write(PAYLOAD)
        elif p == "/expiring-ranged.bin":
            if self.headers.get("Authorization") != "Bearer good":
                body = b'{"error": "link expired"}'
                self.send_response(403)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self._serve_ranged()
        elif p == "/api/refresh":
            self.server.refresh_hits += 1
            body = json.dumps({"data": {"url": f"http://127.0.0.1:{self._port()}/file.bin?fresh=1"}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif p == "/flaky.bin":
            # drop the connection mid-transfer on the first attempt
            if not getattr(self.server, "flaky_done", False):
                self.server.flaky_done = True
                self.send_response(200)
                self.send_header("Content-Length", str(len(PAYLOAD)))
                self.end_headers()
                self.wfile.write(PAYLOAD[: 300 * 1024])
                self.wfile.flush()
                self.connection.close()
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(PAYLOAD)))
            self.end_headers()
            self.wfile.write(PAYLOAD)
        elif p == "/poster.jpg":
            # hotlink protection: a media URL answered with a web page
            body = HTML_PAGE
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif p == "/toggle-guard.jpg":
            # Ranged requests (the probe) always get real bytes; the first
            # Range-less GET (a fresh single-stream transfer) gets the
            # hotlink-protection page, later ones the real payload.
            if self.headers.get("Range"):
                self._serve_ranged()
                return
            with self.server.toggle_lock:
                self.server.html_hits += 1
                first = self.server.html_hits == 1
            if first:
                body = HTML_PAGE
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(200)
                self.send_header("Content-Length", str(len(PAYLOAD)))
                self.end_headers()
                self.wfile.write(PAYLOAD)
        elif p == "/guarded.bin":
            # hotlink protection done right: media bytes only when the
            # expected Referer (and optional Cookie) are present
            if (self.headers.get("Referer") == "https://media.example/page"
                    and self.headers.get("Cookie") == "sid=42"):
                self._serve_ranged()
            else:
                body = HTML_PAGE
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        elif p == "/echo-headers":
            body = json.dumps({
                "referer": self.headers.get("Referer"),
                "cookie": self.headers.get("Cookie"),
                "x-probe": self.headers.get("X-Probe"),
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif p == "/mislabeled.mp4":
            # a video filename served as JPEG bytes
            body = JPEG_BYTES
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif p == "/playlist":
            # HLS manifest bytes (what those leftover #EXTM3U files were)
            body = M3U_BYTES
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif p == "/cd-mangled":
            # a server sending several comma-glued filename variants —
            # the header that used to litter the downloads folder
            body = MP4_BYTES
            self.send_response(200)
            self.send_header(
                "Content-Disposition",
                'attachment; filename="130425,_360p.mp4,.mp4,_720p.mp4,"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif p == "/big.bin":
            # slow trickle so a cancel can land mid-transfer; ignores Range
            self.send_response(200)
            self.send_header("Content-Length", str(len(PAYLOAD) * 100))
            self.end_headers()
            for _ in range(100):
                if self.server.stop_flag.is_set():
                    return
                self.wfile.write(PAYLOAD)
                self.wfile.flush()
                self.server.stop_flag.wait(0.05)
        else:
            self.send_response(404)
            self.end_headers()


@pytest.fixture()
def server(tmp_path):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    srv.stop_flag = threading.Event()
    srv.ranged_hits = 0
    srv.refresh_hits = 0
    srv.html_hits = 0
    srv.toggle_lock = threading.Lock()
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.stop_flag.set()
    srv.shutdown()


def make_dl(tmp_path, cfg_extra=None, **dl_kwargs):
    cfg = {"out_dir": str(tmp_path / "out"), "workers": 2, "retries": 3, "timeout": 10,
           "link_providers": [{
               "match": r"^http://127\.0\.0\.1:\d+/expiring",
               "refresh_url": "http://127.0.0.1:{port}/api/refresh",
               "url_field": "data.url",
           }]}
    cfg["link_providers"][0]["refresh_url"] = cfg["link_providers"][0]["refresh_url"].format(
        port=dl_kwargs.pop("port"))
    if cfg_extra:
        cfg.update(cfg_extra)
    return Downloader(cfg, state=State(tmp_path / "state.json"), **dl_kwargs)


SEG = {"segments": 4, "min_segmented_size": 1024}


def test_simple_download(server, tmp_path):
    port = server.server_address[1]
    dl = make_dl(tmp_path, port=port)
    t = dl.download_one(f"http://127.0.0.1:{port}/file.bin")
    assert t.status == "done", t.message
    assert (tmp_path / "out" / "file.bin").read_bytes() == PAYLOAD


def test_resume_from_partial(server, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    part = out / "file.bin.part"
    part.write_bytes(PAYLOAD[: 400 * 1024])

    port = server.server_address[1]
    dl = make_dl(tmp_path, port=port)
    t = dl.download_one(f"http://127.0.0.1:{port}/file.bin")
    assert t.status == "done", t.message
    assert (out / "file.bin").read_bytes() == PAYLOAD
    assert not part.exists()


def test_expired_link_refreshed_via_provider(server, tmp_path):
    port = server.server_address[1]
    dl = make_dl(tmp_path, port=port)
    t = dl.download_one(f"http://127.0.0.1:{port}/expiring.bin", name_hint="expiring.bin")
    assert t.status == "done", t.message
    assert t.refreshes == 1
    assert server.refresh_hits == 1
    assert (tmp_path / "out" / "expiring.bin").read_bytes() == PAYLOAD


def test_expired_without_provider_fails_cleanly(server, tmp_path):
    port = server.server_address[1]
    cfg = {"out_dir": str(tmp_path / "out"), "workers": 1, "retries": 2, "timeout": 10,
           "link_providers": []}
    dl = Downloader(cfg, state=State(tmp_path / "state.json"))
    t = dl.download_one(f"http://127.0.0.1:{port}/expiring.bin")
    assert t.status == "error"
    assert "expired" in t.message.lower()


def test_flaky_connection_resumes(server, tmp_path):
    port = server.server_address[1]
    dl = make_dl(tmp_path, port=port)
    t = dl.download_one(f"http://127.0.0.1:{port}/flaky.bin")
    assert t.status == "done", t.message
    assert (tmp_path / "out" / "flaky.bin").read_bytes() == PAYLOAD


def test_batch_and_dedupe(server, tmp_path):
    port = server.server_address[1]
    dl = make_dl(tmp_path, port=port, cfg_extra={"workers": 4})
    u = f"http://127.0.0.1:{port}/file.bin"
    results = dl.download_batch([(u, "one.bin"), (u, "one.bin")])
    statuses = [t.status for t in results]
    assert statuses[0] == "done"
    # second copy either dedupes to a new name (if it raced the first)
    # or is skipped because the first already finished
    if statuses[1] == "done":
        assert (tmp_path / "out" / "one (1).bin").exists()
    else:
        assert statuses[1] == "skipped"
        assert (tmp_path / "out" / "one.bin").exists()


def test_cancel_mid_transfer(server, tmp_path):
    port = server.server_address[1]
    ev = threading.Event()
    dl = make_dl(tmp_path, port=port, cancel_event=ev)
    th = threading.Thread(
        target=lambda: dl.download_one(f"http://127.0.0.1:{port}/big.bin"))
    th.start()
    ev.set()  # cancel before/while it starts streaming
    th.join(timeout=30)
    assert not th.is_alive()
    leftovers = list((tmp_path / "out").glob("*.part"))
    assert all(f.stat().st_size < len(PAYLOAD) * 100 for f in leftovers)


def test_state_records_failures_for_resume(server, tmp_path):
    port = server.server_address[1]
    cfg = {"out_dir": str(tmp_path / "out"), "workers": 1, "retries": 1, "timeout": 10,
           "link_providers": []}
    dl = Downloader(cfg, state=State(tmp_path / "state.json"))
    dl.download_one(f"http://127.0.0.1:{port}/missing.bin")
    rec = State(tmp_path / "state.json").get(f"http://127.0.0.1:{port}/missing.bin")
    assert rec.get("status") == "error"


# ----------------------------------------------------------------- segmented


def test_segmented_multi_connection(server, tmp_path):
    port = server.server_address[1]
    dl = make_dl(tmp_path, port=port, cfg_extra=SEG)
    t = dl.download_one(f"http://127.0.0.1:{port}/file.bin")
    assert t.status == "done", t.message
    assert t.total == len(PAYLOAD)
    assert t.segments == 4
    assert (tmp_path / "out" / "file.bin").read_bytes() == PAYLOAD
    # probe + at least 3 of the 4 segments must have used Range
    assert server.ranged_hits >= 4
    leftovers = list((tmp_path / "out").glob("*.part*"))
    assert not leftovers, "segment parts must be merged and cleaned up"


def test_segmented_expiry_refreshes_once_for_all_segments(server, tmp_path):
    port = server.server_address[1]
    dl = make_dl(tmp_path, port=port, cfg_extra=SEG)
    t = dl.download_one(f"http://127.0.0.1:{port}/expiring-ranged.bin",
                        name_hint="expiring-ranged.bin")
    assert t.status == "done", t.message
    # probe + every segment hit the 403, but exactly one provider refresh happened
    assert server.refresh_hits == 1
    assert t.refreshes == 1
    assert (tmp_path / "out" / "expiring-ranged.bin").read_bytes() == PAYLOAD


def test_segmented_resume_from_partial_segments(server, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    seg_len = (len(PAYLOAD) + 3) // 4
    (out / "file.bin.part0").write_bytes(PAYLOAD[:seg_len])
    (out / "file.bin.part2").write_bytes(PAYLOAD[2 * seg_len: 3 * seg_len])

    port = server.server_address[1]
    dl = make_dl(tmp_path, port=port, cfg_extra=SEG)
    t = dl.download_one(f"http://127.0.0.1:{port}/file.bin")
    assert t.status == "done", t.message
    assert (out / "file.bin").read_bytes() == PAYLOAD
    assert not list(out.glob("*.part*"))
    assert server.ranged_hits >= 2  # segments 1 and 3 fetched ranged


def test_small_file_stays_single_stream(server, tmp_path):
    # 1 MiB payload with the default 4 MiB threshold -> no segmentation
    port = server.server_address[1]
    dl = make_dl(tmp_path, port=port)  # default min_segmented_size
    t = dl.download_one(f"http://127.0.0.1:{port}/file.bin")
    assert t.status == "done", t.message
    assert t.segments == 0
    assert (tmp_path / "out" / "file.bin").read_bytes() == PAYLOAD


# ---------------------------------------------------------------- html_guard


def test_html_guard_rejects_page_for_media_url(server, tmp_path):
    """HTTP 200 + an HTML page for a media URL must fail, not save .html"""
    port = server.server_address[1]
    dl = make_dl(tmp_path, port=port)
    t = dl.download_one(f"http://127.0.0.1:{port}/poster.jpg")
    assert t.status == "error"
    assert "web page" in t.message
    out = tmp_path / "out"
    assert not list(out.glob("*")), "no file or part may be left behind"
    rec = State(tmp_path / "state.json").get(f"http://127.0.0.1:{port}/poster.jpg")
    assert rec.get("status") == "error"


def test_html_guard_rejects_page_with_segment_config(server, tmp_path):
    # segmented settings still end in the single-stream guard (probe got 200)
    port = server.server_address[1]
    dl = make_dl(tmp_path, port=port, cfg_extra=SEG)
    t = dl.download_one(f"http://127.0.0.1:{port}/poster.jpg")
    assert t.status == "error"
    assert "web page" in t.message
    assert not list((tmp_path / "out").glob("*"))


def test_html_guard_recovers_once_link_is_fixed(server, tmp_path):
    """First attempt gets the page (error), retry after the link works."""
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}/toggle-guard.jpg"
    dl = make_dl(tmp_path, port=port)
    t = dl.download_one(url)
    assert t.status == "error" and "web page" in t.message
    assert server.html_hits == 1  # guard must not retry-loop on HTML
    t2 = dl.download_one(url)
    assert t2.status == "done", t2.message
    assert (tmp_path / "out" / "toggle-guard.jpg").read_bytes() == PAYLOAD


def test_html_guard_discards_poisoned_part_and_recovers(server, tmp_path):
    """A leftover .part holding a saved web page (e.g. from an older run
    without the guard) is discarded, not appended to, when the server
    honors Range."""
    out = tmp_path / "out"
    out.mkdir()
    (out / "file.bin.part").write_bytes(HTML_PAGE)
    logs: list[str] = []
    port = server.server_address[1]
    dl = make_dl(tmp_path, port=port,
                 log_cb=lambda msg, level="info": logs.append(str(msg)))
    t = dl.download_one(f"http://127.0.0.1:{port}/file.bin")
    assert t.status == "done", t.message
    assert (out / "file.bin").read_bytes() == PAYLOAD
    assert not list(out.glob("*.part*"))
    assert any("discarding the leftover part" in m for m in logs), logs


def test_html_guard_discards_poisoned_segment_part(server, tmp_path):
    """Segmented resume: one poisoned .partN is restarted; real parts kept."""
    out = tmp_path / "out"
    out.mkdir()
    seg_len = (len(PAYLOAD) + 3) // 4
    (out / "file.bin.part0").write_bytes(PAYLOAD[:seg_len])       # real
    (out / "file.bin.part1").write_bytes(HTML_PAGE)               # poisoned
    port = server.server_address[1]
    dl = make_dl(tmp_path, port=port, cfg_extra=SEG)
    t = dl.download_one(f"http://127.0.0.1:{port}/file.bin")
    assert t.status == "done", t.message
    assert (out / "file.bin").read_bytes() == PAYLOAD
    assert not list(out.glob("*.part*"))


def test_html_guard_can_be_disabled(server, tmp_path):
    """html_guard: False restores the old save-anything behavior."""
    port = server.server_address[1]
    dl = make_dl(tmp_path, port=port, cfg_extra={"html_guard": False})
    t = dl.download_one(f"http://127.0.0.1:{port}/poster.jpg",
                        name_hint="poster.jpg")
    assert t.status == "done", t.message
    assert (tmp_path / "out" / "poster.jpg").read_bytes() == HTML_PAGE


# ------------------------------------------------------------ domain_headers


def test_domain_headers_unlock_hotlink_protected_download(server, tmp_path):
    """A Referer/Cookie-gated URL succeeds once domain_headers supply them."""
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}/guarded.bin"
    # without the headers: html_guard correctly refuses the page
    t = make_dl(tmp_path, port=port).download_one(url)
    assert t.status == "error" and "web page" in t.message
    # with them: the real media bytes come through
    dl = make_dl(tmp_path, port=port, cfg_extra={"domain_headers": [{
        "match": "/guarded",
        "headers": {"Referer": "https://media.example/page",
                    "Cookie": "sid=42"},
    }]})
    t2 = dl.download_one(url)
    assert t2.status == "done", t2.message
    assert (tmp_path / "out" / "guarded.bin").read_bytes() == PAYLOAD


def test_domain_headers_reach_probe_segments_and_resume(server, tmp_path):
    """Extra headers ride along on every request: probe, each segment, and
    the single-stream resume retry (via the echo endpoint's reply)."""
    port = server.server_address[1]
    dh = {"domain_headers": [{"match": "/echo-headers",
                              "headers": {"Referer": "r", "Cookie": "c",
                                          "X-Probe": "1"}}]}
    dl = make_dl(tmp_path, port=port, cfg_extra={**SEG, **dh})
    t = dl.download_one(f"http://127.0.0.1:{port}/echo-headers",
                        name_hint="echo.json")
    assert t.status == "done", t.message
    seen = json.loads((tmp_path / "out" / "echo.json").read_bytes())
    # the body came from a probe/segment request (Range present but echoed
    # headers are what matters here)
    assert seen == {"referer": "r", "cookie": "c", "x-probe": "1"}

    # single-stream resume: poison the etag path by pre-writing a partial
    # part, forcing a fresh non-206 request that must still carry the headers
    out = tmp_path / "out2"
    out.mkdir()
    dl2 = make_dl(tmp_path, port=port, cfg_extra={**dh, "out_dir": str(out)})
    (out / "echo.json.part").write_bytes(b"{\n")
    t2 = dl2.download_one(f"http://127.0.0.1:{port}/echo-headers",
                          name_hint="echo.json")
    assert t2.status == "done", t2.message
    seen2 = json.loads((out / "echo.json").read_bytes())
    assert seen2["x-probe"] == "1"


# ---------------------------------------------------------- kind validation


def test_kind_mismatch_warns_on_mislabeled_media(server, tmp_path):
    """A .mp4 filename served as JPEG bytes: download succeeds but a warn
    names the real content kind."""
    logs: list[str] = []
    port = server.server_address[1]
    dl = make_dl(tmp_path, port=port,
                 log_cb=lambda msg, level="info": logs.append(str(msg)))
    t = dl.download_one(f"http://127.0.0.1:{port}/mislabeled.mp4")
    assert t.status == "done", t.message
    assert (tmp_path / "out" / "mislabeled.mp4").read_bytes() == JPEG_BYTES
    warns = [m for m in logs if "[warn]" in m]
    assert len(warns) == 1, logs
    assert "JPEG" in warns[0] and "MPEG-4" in warns[0]
    assert "file saved, but" in warns[0]
    assert t.note and "JPEG" in t.note, t.note


def test_playlist_refused_like_html(server, tmp_path):
    """An HLS manifest served as the media itself now errors like an HTML
    page does — a segment list can never play as the requested file."""
    logs: list[str] = []
    port = server.server_address[1]
    dl = make_dl(tmp_path, port=port,
                 log_cb=lambda msg, level="info": logs.append(str(msg)))
    t = dl.download_one(f"http://127.0.0.1:{port}/playlist",
                        name_hint="3007.webm")
    assert t.status == "error", t.message
    assert "HLS/M3U playlist" in t.message
    assert "refused to save" in t.message
    assert not list((tmp_path / "out").glob("*.part*")), "no part files left"
    assert not list((tmp_path / "out").glob("3007.webm"))


def test_mangled_cd_name_is_cleaned(server, tmp_path):
    """A multi-variant Content-Disposition filename is cleaned at pick
    time: the file lands under one clean extension instead of the old
    '130425,_360p.mp4,.mp4,_720p.mp4,' mess."""
    port = server.server_address[1]
    dl = make_dl(tmp_path, port=port)
    t = dl.download_one(f"http://127.0.0.1:{port}/cd-mangled")
    assert t.status == "done", t.message
    assert t.filename == "130425,_360p.mp4", t.filename
    assert t.dest is not None and t.dest.exists()
    assert t.dest.name == "130425,_360p.mp4"
    assert t.note == "", t.note          # real MP4 bytes under a .mp4 name
    # nothing else was written (in particular not the full mangled name)
    names = {p.name for p in (tmp_path / "out").iterdir()}
    assert names == {"130425,_360p.mp4"}


def test_playlist_guard_opt_out_restores_warn(server, tmp_path):
    """`"playlist_guard": false` restores the old warn-only behavior: the
    playlist saves under its (sloppy) name and the download stands."""
    logs: list[str] = []
    port = server.server_address[1]
    dl = make_dl(tmp_path, port=port, cfg_extra={"playlist_guard": False},
                 log_cb=lambda msg, level="info": logs.append(str(msg)))
    t = dl.download_one(f"http://127.0.0.1:{port}/playlist",
                        name_hint="3007.webm")
    assert t.status == "done", t.message
    warns = [m for m in logs if "[warn]" in m]
    assert len(warns) == 1, logs
    assert "M3U" in warns[0] and "Matroska/WebM" in warns[0]


def test_no_warning_when_kind_matches_or_is_unknown(server, tmp_path):
    """Matching kinds (json/text), compatible families, unknown bytes and
    unopinionated extensions all stay silent."""
    logs: list[str] = []
    port = server.server_address[1]
    dl = make_dl(tmp_path, port=port,
                 log_cb=lambda msg, level="info": logs.append(str(msg)))
    # .json name over text/JSON body: kind matches
    t = dl.download_one(f"http://127.0.0.1:{port}/echo-headers",
                        name_hint="echo.json")
    assert t.status == "done"
    # .bin name over arbitrary bytes: engine has no opinion
    t2 = dl.download_one(f"http://127.0.0.1:{port}/file.bin")
    assert t2.status == "done"
    assert not [m for m in logs if "[warn]" in m], logs
