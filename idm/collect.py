"""PyIDM collector — the browser-extension bridge.

A tiny localhost HTTP server the bundled browser extension posts captured
links to (popup "Add page links", background "Send link to PyIDM…" context
menu, page-media scan from the popup). Everything lands in one queue file
(``~/.idm/collect_queue.json``), which the GUI can poll and show in its log,
and which the collector itself can hand straight to the download engine.

Endpoints
---------
    GET  /ping        -> {"ok": true, "version": "...", "out_dir": "..."}
    POST /collect     -> queue the URLs in the body
    POST /download    -> queue AND start downloading immediately (out_dir
                         from the server's config unless overridden per
                         request)
    GET  /queue       -> the current queue (queue_payload shape)
    POST /queue/clear -> empty the queue

POST bodies are JSON (or a plain-text batch list): {"urls": [...],
"url": "...", "page_url": "...", "out_dir": "..."} — anything
``parse_job_lines`` understands works too. Malformed JSON is rejected with
400, not crashed on: the extension is in-process-untrusted input, same as
any other command surface. The server binds 127.0.0.1 only — nothing off
this machine can reach it.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from . import __version__
from .utils import parse_job_lines

COLLECT_QUEUE_FILE = Path.home() / ".idm" / "collect_queue.json"
DEFAULT_PORT = 27492          # unassigned, memorable; options page can change it
MAX_BODY = 1 << 20            # 1 MiB is far more than any capture needs

# Every queue mutation is a read-modify-write of one JSON file, and the
# server is threaded (a /download drain can race an arriving /collect):
# serialize the whole load->change->save window or concurrent captures are
# lost outright (verified: a take-vs-add race dropped the new capture).
_QUEUE_LOCK = threading.Lock()


# ----------------------------------------------------------------- queue
def load_queue(path=None) -> dict:
    """The persisted capture queue: {\"pending\": [...], \"history\": [...]},
    oldest first, each entry {\"url\", \"name\", \"page_url\", \"ts\"}. A
    missing or mangled file reads as empty (best-effort, like the other
    stores)."""
    p = Path(path) if path else COLLECT_QUEUE_FILE
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"pending": [], "history": []}
    if not isinstance(data, dict):
        return {"pending": [], "history": []}
    out: dict[str, list] = {"pending": [], "history": []}
    for key in out:
        slot = data.get(key)
        if isinstance(slot, list):
            out[key] = [e for e in slot if isinstance(e, dict)]
    return out


def save_queue(q: dict, path=None) -> None:
    p = Path(path) if path else COLLECT_QUEUE_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(q, indent=2, ensure_ascii=True) + "\n",
                 encoding="utf-8")


def queue_add(items, page_url: str = "", path=None) -> int:
    """Append captures to the pending queue, skipping duplicates of anything
    already pending (re-clicking a context menu shouldn't double-book a
    link). Returns the number actually added. `items` holds plain URL
    strings or (url, name|None) pairs — a pair names that one download, the
    way a 'url -> name' batch line does. Each entry is stamped and keeps
    its source page so 'where did this come from?' stays answerable."""
    with _QUEUE_LOCK:
        q = load_queue(path)
        have = {e.get("url") for e in q["pending"]}
        added = 0
        for item in items:
            url, name = (item if isinstance(item, tuple) else (item, None))
            url = str(url or "").strip()
            if not url or url in have:
                continue
            have.add(url)
            q["pending"].append({
                "url": url, "name": name or "", "page_url": page_url or "",
                "ts": time.time(),
            })
            added += 1
        if added:
            save_queue(q, path)
    return added


def queue_pending(path=None) -> list[dict]:
    return load_queue(path)["pending"]


def queue_clear(path=None) -> int:
    """Empty the pending list (history untouched). Returns how many were
    dropped."""
    with _QUEUE_LOCK:
        q = load_queue(path)
        n = len(q["pending"])
        q["pending"] = []
        save_queue(q, path)
    return n


def queue_take(path=None) -> list[tuple[str, str | None]]:
    """Atomically drain the pending queue into a batch job list
    [(url, name|None), ...] for the engine (and move the entries to
    history). Used by both the GUI's 'download queue now' and the
    collector's direct mode."""
    with _QUEUE_LOCK:
        q = load_queue(path)
        pending = q["pending"]
        if not pending:
            return []
        q["pending"] = []
        q["history"] = (q["history"] + pending)[-200:]   # same 200-cap as subs history
        save_queue(q, path)
    return [(e.get("url"), e.get("name") or None) for e in pending
            if e.get("url")]


def queue_payload(q: dict) -> dict:
    """Queue -> lossless JSON-able dict for /queue (real ints, same
    convention as every other payload)."""
    return {
        "pending": list(q["pending"]),
        "history": list(q["history"]),
        "pending_count": len(q["pending"]),
    }


def start_downloads(jobs, out_dir, cfg, log=None) -> dict:
    """Hand (url, name) jobs straight to the engine — the /download path and
    the GUI's 'download queue now'. Never raises: a failed batch is logged
    and reported, not crashed on. Returns the batch summary dict."""
    from .core import Downloader
    from .gui import batch_payload
    from .state import State as _State

    cfg = dict(cfg)
    cfg["out_dir"] = str(out_dir)
    state = _State(Path(out_dir) / "idm.state.json")
    dl = Downloader(cfg, out_dir=Path(out_dir), state=state)
    try:
        results = dl.download_batch(jobs)
    except Exception as e:                       # engine blow-up -> summary
        if log:
            log(f"collector batch failed: {e}", "error")
        return {"ok": 0, "total": len(jobs), "error": str(e)}
    payload = batch_payload(results)
    ok = sum(1 for t in results if t.status in ("done", "skipped"))
    if log:
        log(f"collector: {ok}/{len(results)} download(s) finished -> {out_dir}")
    payload["ok"] = ok
    return payload


# ----------------------------------------------------------------- server
class _Handler(BaseHTTPRequestHandler):
    server_version = f"PyIDM/{__version__}"
    server: CollectorServer       # narrowed: the handler only ever runs on ours

    def log_message(self, fmt, *args):          # silence per-request noise
        pass

    # -- helpers ------------------------------------------------------
    def _json(self, payload, code: int = 200) -> None:
        body = (json.dumps(payload, indent=2, ensure_ascii=True) + "\n").encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_items(self) -> tuple[list[tuple[str, str | None]], dict]:
        """POST body -> (items, extra) where each item is (url, name|None).
        Accepts JSON ({urls: [...] | url, page_url, name, out_dir}) or a
        plain-text batch list ('url -> name' renames honored). Raises
        ValueError on oversize/unparseable bodies so the caller can 400 it."""
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ValueError("body too large")
        raw = self.rfile.read(length) if length else b""
        text = raw.decode("utf-8", errors="replace")
        ctype = (self.headers.get("Content-Type") or "").lower()
        extra: dict = {}
        items: list[tuple[str, str | None]] = []
        if "json" in ctype or text.lstrip().startswith(("[", "{")):
            try:
                data = json.loads(text) if text.strip() else {}
            except ValueError as e:
                raise ValueError(f"bad JSON: {e}") from None
            if not isinstance(data, dict):
                raise ValueError("JSON body must be an object")
            urls = data.get("urls") or ([data["url"]] if data.get("url") else [])
            if not isinstance(urls, list):
                raise ValueError("'urls' must be a list")
            name = data.get("name") if isinstance(data.get("name"), str) else None
            # a 'name' names a single-URL send; for batches the URLs name
            # themselves (the engine derives filenames per URL)
            if len(urls) == 1 and name:
                items = [(str(urls[0]).strip(), name)]
            else:
                items = [(str(u).strip(), None) for u in urls]
            extra = {k: data[k] for k in ("page_url", "out_dir")
                     if isinstance(data.get(k), str)}
        else:
            items = parse_job_lines(text)
        items = [(u, n) for u, n in items if str(u or "").strip()]
        if not items:
            raise ValueError("no URLs in body")
        return items, extra

    # -- routes -------------------------------------------------------
    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path == "/ping":
            self._json({"ok": True, "version": __version__,
                        "out_dir": self.server.cfg.get("out_dir") or "downloads"})
        elif path == "/queue":
            self._json(queue_payload(load_queue(self.server.queue_path)))
        else:
            self._json({"ok": False, "error": "not found"}, 404)

    def do_POST(self) -> None:
        # Ordering contract: log BEFORE responding, so a client that has
        # seen the ack knows the log entry exists (the roundtrip test
        # asserts on it right after post() — logging after _json() raced
        # the client and flaked CI once, on PR #30's py3.9 leg).
        path = self.path.split("?")[0]
        if path == "/queue/clear":          # no body needed
            n = queue_clear(self.server.queue_path)
            if self.server.log:
                self.server.log(f"collector: queue cleared ({n} pending)")
            self._json({"ok": True, "cleared": n})
            return
        try:
            items, extra = self._read_items()
        except ValueError as e:
            self._json({"ok": False, "error": str(e)}, 400)
            return
        page = extra.get("page_url", "")
        if path == "/collect":
            n = queue_add(items, page_url=page,
                          path=self.server.queue_path)
            if self.server.log:
                self.server.log(f"collector: queued {n} link(s) "
                                f"from browser ({len(items)} sent)")
            self._json({"ok": True, "added": n,
                        "pending": len(queue_pending(self.server.queue_path))})
        elif path == "/download":
            n = queue_add(items, page_url=page,
                          path=self.server.queue_path)
            out_dir = extra.get("out_dir") or self.server.cfg.get("out_dir") \
                or "downloads"
            jobs = queue_take(self.server.queue_path)
            if self.server.log:
                self.server.log(f"collector: starting {len(jobs)} download(s) "
                                f"-> {out_dir}")
            summary = start_downloads(
                jobs, out_dir, self.server.cfg, log=self.server.log)
            self._json({"ok": True, "added": n, "started": len(jobs),
                        "summary": summary})
        else:
            self._json({"ok": False, "error": "not found"}, 404)


class CollectorServer(ThreadingHTTPServer):
    daemon_threads = True
    # SO_REUSEADDR on Windows is SO_REUSEADDR+SO_REUSEPORT-in-one: a second
    # collector on the same port would silently double-bind and steal
    # requests. The default (False) makes the second bind raise — which is
    # exactly what the GUI's 'another PyIDM may be running' warning assumes.
    allow_reuse_address = False

    def __init__(self, port: int, cfg: dict, queue_path=None, log=None) -> None:
        self.cfg = dict(cfg)
        self.queue_path: Path | None = queue_path
        self.log: Callable[..., None] | None = log
        super().__init__(("127.0.0.1", port), _Handler)


def start_collector(port: int = DEFAULT_PORT, cfg: dict | None = None,
                    queue_path=None, log=None) -> CollectorServer:
    """Create + start the collector server on a thread (daemon, like every
    other background worker). Returns the server; callers keep it and call
    .shutdown()/.server_close() to stop it."""
    srv = CollectorServer(port, cfg or {}, queue_path=queue_path, log=log)
    threading.Thread(target=srv.serve_forever, daemon=True,
                     name="pyidm-collector").start()
    return srv


def run_cli(args) -> int:
    """`idm collect`: run the collector in the foreground until Ctrl+C."""
    import time

    from .config import get_config

    cfg = get_config(getattr(args, "config", None))
    port = int(args.port or DEFAULT_PORT)
    print(f"PyIDM collector {__version__} listening on http://127.0.0.1:{port} "
          f"(queue: {COLLECT_QUEUE_FILE})")
    print("Load the browser extension from browser-extension/ "
          "(chrome://extensions -> Load unpacked) and click 'Add page links'.")
    srv = start_collector(port, cfg, log=lambda m, lvl="info": print(m))
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\nstopping…")
    finally:
        srv.shutdown()
        srv.server_close()
    return 0
