"""Guard: every JSON example in docs/automation.md must match real output.

Each documented scenario is re-run against a live local HTTP server (or
seeded stores) and the real payload is shape-compared against the docs'
JSON example: same keys, nesting, list lengths, value types. Values may
differ between runs (ports, paths, timestamps) — shapes may not. A field
added/removed/renamed/re-typed in any --json payload fails here until the
docs are updated in the same commit. Also asserts the documented output
format (json.dumps indent=2 + trailing newline) and exit codes.
"""
from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from idm import gui as G
from idm.cli import main as cli_main

DOCS = Path(__file__).resolve().parent.parent / "docs" / "automation.md"
PAYLOAD = bytes(range(256)) * 1024


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence test output
        pass

    def do_GET(self):
        if self.path == "/bad.bin":
            self.send_response(500)
        else:
            self.send_response(200)
        self.send_header("Content-Length", "0" if self.path == "/bad.bin"
                         else str(len(PAYLOAD)))
        self.end_headers()
        if self.path != "/bad.bin":
            self.wfile.write(PAYLOAD)


@pytest.fixture()
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()


@pytest.fixture(autouse=True)
def _quiet_console(monkeypatch):
    import idm.cli as C
    monkeypatch.setattr(C, "console", C.Console(force_terminal=False, width=250))


# ------------------------------------------------------------- doc parsing
def _sections() -> dict[str, str]:
    """Heading -> the section's fenced ```json example."""
    text = DOCS.read_text(encoding="utf-8")
    found: dict[str, str] = {}
    parts = re.split(r"^## ", text, flags=re.MULTILINE)
    for part in parts[1:]:
        heading = part.splitlines()[0].strip().strip("`")
        m = re.search(r"```json\n(.*?)\n```", part, flags=re.DOTALL)
        if m:
            found[heading] = m.group(1)
    return found


def _shape(v):
    """Type-exact, value-free skeleton: keys, nesting, list lengths."""
    if isinstance(v, dict):
        return {"dict": {k: _shape(x) for k, x in sorted(v.items())}}
    if isinstance(v, list):
        return {"list": [_shape(x) for x in v]}
    return type(v).__name__


def _run(argv, capsys):
    code = cli_main(argv)
    out = capsys.readouterr().out
    # documented format: one JSON document, json.dumps(indent=2) + newline
    payload = json.loads(out)                      # parseable, nothing else
    assert out == json.dumps(payload, indent=2) + "\n"
    return code, payload


def _url(server, path):
    return f"http://127.0.0.1:{server.server_address[1]}{path}"


def _seed_state(tmp_path, records):
    (tmp_path / "idm.state.json").write_text(
        json.dumps({"version": 1, "downloads": records}), encoding="utf-8")


# ------------------------------------------------------------ the scenarios
SCENARIOS = {
    "idm get --json": lambda s, t: (
        ["get", _url(s, "/movie.mp4"), _url(s, "/bad.bin"), "-o", str(t), "--json"], 1),
    "idm batch --json": lambda s, t: (
        None, 0),  # built inline (needs the urls.txt file)
    "idm resume --json": lambda s, t: (
        None, 1),  # built inline (needs seeded state)
    "idm downloads --json": lambda s, t: (
        None, 0),  # built inline (needs seeded state)
    "idm history --json": lambda s, t: (
        ["history", "--json"], 0),
    "idm stats --json": lambda s, t: (
        ["-o", str(t), "stats", "--json"], 0),
    "idm providers --json": lambda s, t: (
        ["providers", "--json"], 1),
    "idm presets --json": lambda s, t: (
        None, 0),  # built inline (needs seeded presets)
    "idm verify --json": lambda s, t: (
        None, 0),  # built inline (pre-planted files + --delete-warned)
    "idm restore --json": lambda s, t: (
        None, 0),  # built inline (pre-planted quarantine)
    "idm sanitize-names --json": lambda s, t: (
        None, 0),  # built inline (pre-planted mangled name)
    "idm prune-state --json": lambda s, t: (
        None, 0),  # built inline (seeded state + --apply)
    "idm ignore --json": lambda s, t: (
        None, 0),  # built inline (writes the user config — patched inline)
}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Stores matching the docs' examples."""
    _seed_state(tmp_path, {
        "https://cdn.example.com/big.iso": {
            "status": "error", "filename": "big.iso", "size": 3221225472,
            "updated": 1758748800, "message": "HTTP 403"}})
    (tmp_path / "subs.json").write_text(json.dumps([
        {"path": "C:/Videos/Inception 2010.mkv", "ok": True,
         "dest": "C:/Videos/Inception 2010.en.srt", "language": "en",
         "provider": "opensubtitles", "size": 51200, "cues": 812,
         "ts": 1758748800}]), encoding="utf-8")
    monkeypatch.setattr(G, "SUBS_HISTORY_FILE", tmp_path / "subs.json")
    monkeypatch.setattr(G, "EXPORT_PREFS_FILE", tmp_path / "export_prefs.json")
    return tmp_path


@pytest.mark.parametrize("heading", sorted(SCENARIOS))
def test_doc_example_matches_real_output(heading, server, env, capsys, monkeypatch):
    assert heading in _sections(), "docs section lost its JSON example"
    doc = json.loads(_sections()[heading])

    if heading == "idm batch --json":
        src = env / "urls.txt"
        src.write_text(f"{_url(server, '/album.zip')} -> music.zip\n"
                       f"{_url(server, '/report.pdf')} -> report.pdf\n",
                       encoding="utf-8")
        argv, want_rc = ["batch", str(src), "-o", str(env), "--json"], 0
    elif heading == "idm resume --json":
        _seed_state(env, {
            _url(server, "/album.zip"): {"status": "downloading",
                                         "filename": "album.zip",
                                         "updated": 1758748800},
            _url(server, "/bad.bin"): {"status": "error", "filename": "bad.bin",
                                       "updated": 1758748801}})
        (env / "album.zip.part").write_bytes(b"part")   # resumable evidence:
        (env / "bad.bin.part").write_bytes(b"part")    # never counted stale
        argv, want_rc = ["-o", str(env), "resume", "--json"], 1
    elif heading == "idm downloads --json":
        argv, want_rc = ["-o", str(env), "downloads", "--json"], 0
    elif heading == "idm providers --json":
        import idm.health as H
        from idm.health import ProviderHealth
        fake = [
            ProviderHealth("opensubtitles", "OpenSubtitles", "ok",
                           "API key present",
                           endpoint="https://api.opensubtitles.com", hints=[]),
            ProviderHealth("subtitlecat", "SubtitleCat", "ok", "reachable",
                           endpoint="https://subtitlecat.com", hints=[]),
            ProviderHealth("podnapisi", "Podnapisi", "down", "DNS blocked",
                           endpoint="https://www.podnapisi.net",
                           hints=[("ISP-level block detected — the chain "
                                   "skips it automatically")])]
        monkeypatch.setattr(H, "run_checks", lambda *a, **k: fake)
        argv, want_rc = ["providers", "--json"], 1
    elif heading == "idm verify --json":
        scan = env / "scan"                  # clean dir: env's own stores stay out
        scan.mkdir()
        (scan / "movie.mp4").write_bytes(
            b"\x00\x00\x00\x18ftypmp44" + b"\x00" * 24)   # content matches name
        (scan / "notes.bin").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
        (scan / "poster.webm").write_bytes(                # playlist in video clothing
            b"#EXTM3U\n#EXT-X-TARGETDURATION:10\n" + b"\x00" * 13)
        argv, want_rc = ["verify", "-o", str(scan), "--json", "--delete-warned"], 0
    elif heading == "idm restore --json":
        qdir = env / "quarantine"
        qdir.mkdir()
        (qdir / "poster.webm").write_bytes(
            b"#EXTM3U\n#EXT-X-TARGETDURATION:10\n" + b"\x00" * 13)
        argv, want_rc = ["-o", str(env), "restore", "--json"], 0
    elif heading == "idm sanitize-names --json":
        scan = env / "san"
        scan.mkdir()
        (scan / "real.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 20)
        (scan / "130425,_360p.mp4,.mp4,_720p.mp4,").write_bytes(
            b"#EXTM3U\n" + b"\x00" * 10)          # mangled CD name
        argv, want_rc = ["-o", str(scan), "sanitize-names", "--json", "--apply"], 0
    elif heading == "idm ignore --json":
        import idm.config as CF
        cfg_dir = env / "cfg"                # env fixture stays read-only:
        cfg_dir.mkdir()                      # never touch the real user config
        cfg_path = cfg_dir / "config.json"
        cfg_path.write_text(json.dumps({"verify_ignore": ["130425*"]}),
                            encoding="utf-8")   # the docs example pre-exists
        monkeypatch.setattr(CF, "USER_CONFIG_PATH", cfg_path)
        argv, want_rc = ["ignore", "add", "kept.webm", "*.m3u", "--json"], 0
    elif heading == "idm prune-state --json":
        from idm.state import State
        st = State(env / "idm.state.json")
        st.set("https://cdn.example.com/big.iso", status="error",
               filename="big.iso", size=1024)
        st.set("https://cdn.example.com/movie.mp4", status="done",
               filename="movie.mp4", size=2048)
        (env / "movie.mp4").write_bytes(b"\x00" * 8)   # only this file is live
        # --vacuum: pins the compaction byte fields the docs show
        argv, want_rc = ["-o", str(env), "prune-state", "--vacuum", "--json"], 0
    elif heading == "idm presets --json":
        G.save_preset("history", "cat2026", provider="subtitlecat",
                      since="2026-01-01", until="", query="[].provider")
        G.save_preset("downloads", "errors", fmt="json",
                      query='[.[] | select(.status == "error")] | length')
        G.save_preset("stats", "weekly", query=".downloads.pending",
                      out="reports/{date}-{kind}.json", viewer="code")
        G.save_preset("providers", "nightly",
                      query="[.providers[] | select(.status != \"ok\")]",
                      out="providers-{date}.json")
        argv, want_rc = ["presets", "list", "--json"], 0
    else:
        argv, want_rc = SCENARIOS[heading](server, env)

    code, real = _run(argv, capsys)
    assert code == want_rc, f"exit code drifted for {heading}"
    assert _shape(real) == _shape(doc), (
        f"{heading}: real payload shape drifted from docs/automation.md — "
        "update the doc example in the same commit")


def test_quick_index_lists_every_json_command():
    text = DOCS.read_text(encoding="utf-8")
    for heading in SCENARIOS:
        cmd = heading.split()[1]
        assert f"| `idm {cmd}` |" in text, f"quick index is missing idm {cmd}"
