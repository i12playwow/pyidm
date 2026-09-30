"""PyIDM benchmark: measure speedup from multi-connection segmented downloads.

Usage:
  python bench/benchmark.py                       # local simulated host (2 MiB/s per connection)
  python bench/benchmark.py --url https://...     # benchmark a real URL
  python bench/benchmark.py --segments 1,4,8,16 --runs 3

Local mode serves a 100 MiB file over loopback and throttles EVERY connection
to --per-conn-mib MiB/s — the behavior of file-hosting sites that cap each
connection (the case where segmented downloading genuinely helps). On a
non-throttling CDN your home bandwidth is the bottleneck and extra connections
won't help; use --url against a real host to see that.
"""
from __future__ import annotations

import argparse
import shutil
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CHUNK = 64 * 1024


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def make_handler(path: Path, per_conn_rate: float):
    total = path.stat().st_size

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):  # silence request logging
            pass

        def do_HEAD(self):
            self.send_response(200)
            self.send_header("Content-Length", str(total))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()

        def _send(self, start: int, end: int):
            length = end - start + 1
            self.send_response(206 if start > 0 or end < total - 1 else 200)
            if start > 0 or end < total - 1:
                self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            with open(path, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    n = min(CHUNK, remaining)
                    t0 = time.perf_counter()
                    data = f.read(n)
                    if not data:
                        break
                    self.wfile.write(data)
                    self.wfile.flush()
                    remaining -= len(data)
                    # per-connection token-bucket-ish throttle
                    target = len(data) / per_conn_rate
                    elapsed = time.perf_counter() - t0
                    if elapsed < target:
                        time.sleep(target - elapsed)

        def do_GET(self):
            rng = self.headers.get("Range", "")
            if rng.startswith("bytes="):
                first = rng[6:].split(",")[0]
                start_s, _, end_s = first.partition("-")
                start = int(start_s) if start_s else 0
                end = int(end_s) if end_s else total - 1
                end = min(end, total - 1)
                if start >= total:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{total}")
                    self.end_headers()
                    return
                self._send(start, end)
            else:
                self._send(0, total - 1)

    return H


def run_matrix(url: str, segments: list[int], runs: int, outdir: Path, size: int) -> dict[int, list[float]]:
    runs_by_seg: dict[int, list[float]] = {}
    for seg in segments:
        for i in range(runs):
            t0 = time.perf_counter()
            p = subprocess.run(
                [sys.executable, "-m", "idm.cli", "get", url,
                 "--segments", str(seg), "--overwrite", "-o", str(outdir)],
                capture_output=True, text=True,
            )
            dt = time.perf_counter() - t0
            got = outdir / "file.bin"
            ok = p.returncode == 0 and got.exists() and got.stat().st_size == size
            if not ok:
                print(p.stdout[-400:], p.stderr[-400:])
                sys.exit(f"run failed: seg={seg} run={i + 1}")
            mibps = size / 1024 / 1024 / dt
            print(f"  seg={seg:>2} run{i + 1}: {dt:6.1f}s  ({mibps:5.1f} MiB/s)")
            runs_by_seg.setdefault(seg, []).append(dt)
            got.unlink()
    return runs_by_seg


def report(runs_by_seg: dict[int, list[float]], size: int) -> None:
    mib = size / 1024 / 1024
    print("\n=== results (average of runs) ===")
    base = sum(runs_by_seg[min(runs_by_seg)]) / len(runs_by_seg[min(runs_by_seg)])
    for seg in sorted(runs_by_seg):
        avg = sum(runs_by_seg[seg]) / len(runs_by_seg[seg])
        print(f"  {seg:>2} connections: {avg:6.1f}s  {mib / avg:6.1f} MiB/s  speedup x{base / avg:.2f}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", help="benchmark this URL instead of the local throttled server")
    ap.add_argument("--size-mib", type=int, default=100, help="local test file size (default 100)")
    ap.add_argument("--per-conn-mib", type=float, default=2.0, help="per-connection cap in MiB/s (local mode)")
    ap.add_argument("--segments", default="1,8,16", help="comma list, e.g. 1,8,16")
    ap.add_argument("--runs", type=int, default=2, help="runs per segment count")
    ap.add_argument("--outdir", default="bench_out", help="temp output dir")
    args = ap.parse_args()
    segments = [int(x) for x in args.segments.split(",")]

    outdir = Path(args.outdir)
    outdir.mkdir(exist_ok=True)
    size = args.size_mib * 1024 * 1024

    server = None
    try:
        if args.url:
            url, label = args.url, f"real host: {args.url}"
            head = subprocess.run([sys.executable, "-c",
                                   (f"import requests;r=requests.head({args.url!r},timeout=15,allow_redirects=True);"
                                    "print(r.status_code, r.headers.get('Content-Length'), r.headers.get('Accept-Ranges'))")],
                                  capture_output=True, text=True)
            print(f"probe: {head.stdout.strip() or head.stderr.strip()}")
        else:
            payload = outdir / "payload.bin"
            if not payload.exists() or payload.stat().st_size != size:
                print(f"generating {args.size_mib} MiB test file ...")
                pattern = bytes(range(256)) * 4096  # 1 MiB
                with open(payload, "wb") as f:
                    for _ in range(args.size_mib):
                        f.write(pattern)
            port = free_port()
            server = ThreadingHTTPServer(("127.0.0.1", port),
                                         make_handler(payload, args.per_conn_mib * 1024 * 1024))
            threading.Thread(target=server.serve_forever, daemon=True).start()
            url = f"http://127.0.0.1:{port}/file.bin"
            label = (f"local host, {args.per_conn_mib} MiB/s cap PER CONNECTION "
                     f"(simulates per-connection-throttling file hosts)")
        print(f"benchmarking {args.size_mib} MiB from {label}")
        print(f"matrix: segments={segments} runs={args.runs}\n")
        runs_by_seg = run_matrix(url, segments, args.runs, outdir, size)
        report(runs_by_seg, size)
    finally:
        if server:
            server.shutdown()
        shutil.rmtree(outdir, ignore_errors=True)


if __name__ == "__main__":
    main()
