"""Core download engine.

Supports two transfer modes per file:

- **Segmented (multi-connection)** — the file is probed for size and Range
  support, split into N ranges, and the ranges are downloaded in parallel
  into ``Name.ext.part0..N`` files, then concatenated into the final file.
  Each segment resumes individually; an expired link is refreshed once and
  the fresh URL is shared by all segments.
- **Single-stream** — classic one-connection download with Range resume
  into ``Name.ext.part``. Used when the server doesn't support ranges, the
  file is small, or leftover single-stream parts exist.
"""
from __future__ import annotations

import os
import random
import re
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlparse

import requests

from .links import (
    RETRYABLE_STATUSES,
    LinkExpiredError,
    _normalize_pattern,
    find_provider,
    looks_expired,
    resolve_url,
)
from .state import State
from .utils import human_size, sanitize_filename

MIN_SEG_BYTES = 256 * 1024      # never split below this segment size
EMIT_THRESHOLD = 512 * 1024     # progress callback throttle (bytes)
HTML_GUARD_HEAD_BYTES = 1024    # how much of a stream to sniff for HTML

_HTML_SIGS = (
    b"<!doctype html",
    b"<html",
    b"<head",
    b"<body",
    b"<title",
    b"<script",
    b"<iframe",
    b"<!--",
)
_HTML_GUARD_MSG = (
    "server sent a web page instead of the file (hotlink protection, "
    "expired link, or consent wall) — html_guard refused to save it"
)
_PLAYLIST_GUARD_MSG = (
    "server sent an HLS/M3U playlist, not the media itself (the video "
    "lives in the segment links inside it) — html_guard refused to save it"
)


_M3U_SIGS = (b"#EXTM3U", b"#EXT-X")


def _looks_like_m3u(head: bytes) -> bool:
    """Case-insensitive M3U/HLS-marker search across the sniff window
    (a manifest may open with a BOM, whitespace, or comments first)."""
    if head.startswith(b"\xef\xbb\xbf"):  # UTF-8 BOM
        head = head[3:]
    up = head[:HTML_GUARD_HEAD_BYTES].upper()
    return any(sig in up for sig in _M3U_SIGS)


def _looks_like_html(head: bytes) -> bool:
    """Case-insensitive HTML-marker search across the first KiB (a page
    may open with whitespace, a BOM, or an XML prolog before <html>)."""
    if head.startswith(b"\xef\xbb\xbf"):  # UTF-8 BOM
        head = head[3:]
    low = head[:HTML_GUARD_HEAD_BYTES].lower()
    return any(sig in low for sig in _HTML_SIGS)


# filename extension -> the content kind its magic bytes should show
_EXT_KIND: dict[str, str] = {
    ".mp4": "mp4", ".m4v": "mp4", ".mov": "mp4",
    ".mkv": "matroska", ".webm": "matroska",
    ".avi": "avi", ".wav": "wav",
    ".mp3": "mp3", ".ogg": "ogg", ".opus": "ogg", ".flac": "flac",
    ".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png", ".gif": "gif",
    ".webp": "webp",
    ".pdf": "pdf", ".zip": "zip", ".epub": "zip",
    ".m3u": "m3u", ".m3u8": "m3u",
    ".srt": "text", ".vtt": "text", ".json": "text", ".txt": "text",
    ".md": "text", ".html": "html", ".htm": "html",
}

# expected -> actual kinds that are fine despite the different label
_KIND_COMPAT: dict[str, set[str]] = {
    "m3u": {"text"},      # a playlist IS text
    "html": {"text"},     # a web page IS text
    "text": {"html", "m3u"},  # and vice versa — same family
}

_KIND_LABEL: dict[str, str] = {
    "mp4": "MPEG-4", "matroska": "Matroska/WebM", "avi": "AVI",
    "wav": "WAV", "mp3": "MP3", "ogg": "Ogg", "flac": "FLAC",
    "jpeg": "JPEG", "png": "PNG", "gif": "GIF", "webp": "WebP",
    "pdf": "PDF", "zip": "ZIP", "gzip": "gzip",
    "m3u": "an M3U playlist", "text": "plain text", "html": "HTML",
}


def detect_content_kind(head: bytes) -> str | None:
    """Best-effort content type from magic bytes.

    Returns a short kind name ("mp4", "jpeg", "m3u", "text", …) or
    ``None`` when the bytes are unrecognizable — ``None`` must never
    produce a warning, only confident detections may.
    """
    if head.startswith(b"\xff\xd8"):
        return "jpeg"
    if head.startswith(b"\x89PNG"):
        return "png"
    if head.startswith(b"GIF8"):
        return "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[:4] == b"RIFF" and head[8:12] == b"AVI ":
        return "avi"
    if head[4:8] == b"ftyp":
        return "mp4"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "matroska"  # EBML: covers both .mkv and .webm
    if head.startswith(b"ID3") or (
        len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0
    ):
        return "mp3"
    if head.startswith(b"OggS"):
        return "ogg"
    if head.startswith(b"fLaC"):
        return "flac"
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"PK\x03\x04"):
        return "zip"
    if head.startswith(b"\x1f\x8b"):
        return "gzip"
    stripped = head.lstrip()
    if stripped.startswith(b"\xef\xbb\xbf"):
        stripped = stripped[3:]
    if stripped.upper().startswith(b"#EXTM3U"):
        return "m3u"
    if _looks_like_html(head):
        return "html"
    try:
        txt = head.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if not txt:
        return None
    printable = sum(ch.isprintable() or ch in "\r\n\t" for ch in txt)
    return "text" if printable >= 0.95 * len(txt) else None


def _unmangle_cd_name(name: str) -> str:
    """Clean a mangled Content-Disposition filename down to one variant.

    Some servers send several comma-glued variants of the same name
    ('130425,_360p.mp4,.mp4,_720p.mp4,'), which used to be saved verbatim
    — a name whose extension is ',' hides the real one from the kind
    check and litters the downloads folder. Keep everything up to the
    first known extension that ends the name or is followed by a comma;
    names without a known extension (and honest double extensions like
    'mytrip.webm.notes') pass through untouched."""
    lowered = name.lower()
    for ext in sorted(_EXT_KIND, key=len, reverse=True):
        start = 0
        while True:
            i = lowered.find(ext, start)
            if i == -1:
                break
            end = i + len(ext)
            if end == len(name) or name[end] == ",":
                return name[:end]
            start = i + 1
    return name


def expected_kind_for_name(name: str) -> str | None:
    """The content kind a filename promises (None = no opinion).

    Mangled Content-Disposition names (e.g.
    '130425,_360p.mp4,.mp4,_720p.mp4,') can end in punctuation, which
    hides the real extension; trailing punctuation is ignored before the
    lookup so such names keep their opinion (and their warning)."""
    base = name.rstrip(".,; \t")
    return _EXT_KIND.get(Path(base).suffix.lower())


def scan_file_for_name(path) -> str:
    """The kind-mismatch check a finished download gets, applied to an
    already-saved file ('idm verify'). Reads the first KiB, compares the
    detected kind against what the filename promises, and returns the
    short note ('' when clean or when there is no opinion / nothing
    readable). Silent by design — the caller turns the note into output."""
    expected = expected_kind_for_name(Path(path).name)
    if expected is None:
        return ""
    try:
        with open(path, "rb") as f:
            head = f.read(HTML_GUARD_HEAD_BYTES)
    except OSError:
        return ""
    if not head:
        return ""
    actual = detect_content_kind(head)
    if (actual is None or actual == expected
            or actual in _KIND_COMPAT.get(expected, set())):
        return ""
    return (
        f"file saved, but the URL served {_KIND_LABEL.get(actual, actual)}, "
        f"not the {_KIND_LABEL.get(expected, expected)} content its name "
        "promises — it may not open or play correctly"
    )


def headers_for_url(url: str, domain_headers) -> dict[str, str]:
    """Per-domain extra headers for ``url`` (Referer, Cookie, …).

    Config shape (``domain_headers`` key)::

        "domain_headers": [
            {"match": "supjav\\.com", "headers": {"Referer": "https://supjav.com/"}},
            {"match": "cdn\\.example\\.com", "headers": {"Cookie": "token=x"}}
        ]

    Every rule whose regex matches is applied, **later rules winning on
    conflicting keys** (so a site-specific rule can override a catch-all).
    Rules without ``match`` apply to every URL (true catch-all); empty or
    malformed rules are skipped. Pattern syntax matches link_providers
    (PCRE-style (?<name>…) groups are accepted and normalized).
    """
    out: dict[str, str] = {}
    for item in domain_headers or []:
        if not isinstance(item, dict):
            continue
        raw = item.get("headers")
        if not isinstance(raw, dict) or not raw:
            continue
        pattern = item.get("match")
        if pattern:
            try:
                if not re.search(_normalize_pattern(str(pattern)), url):
                    continue
            except re.error:
                continue
        for k, v in raw.items():
            if isinstance(k, str) and isinstance(v, str):
                out[k] = v
    return out


class CancelledError(RuntimeError):
    pass


class DownloadFailed(RuntimeError):
    """Clean, user-facing failure."""


class _RangeLost(RuntimeError):
    """Server stopped honoring Range requests mid-download."""


@dataclass
class DownloadTask:
    url: str
    name_hint: str | None = None
    filename: str = ""
    dest: Path | None = None
    status: str = "pending"  # pending|downloading|done|error|cancelled|skipped
    message: str = ""
    downloaded: int = 0
    total: int | None = None
    attempts: int = 0
    refreshes: int = 0
    segments: int = 0
    note: str = ""           # non-fatal warning shown to the user (e.g. a
    # kind mismatch: 'file saved, but the URL served JPEG, not the MPEG-4
    # its name promises') — empty on clean tasks


@dataclass
class _SegRange:
    index: int
    start: int
    end: int  # inclusive

    @property
    def length(self) -> int:
        return self.end - self.start + 1


@dataclass
class _RefreshState:
    """Shared, thread-safe expiry-refresh bookkeeping for one download.

    All segments of a file refresh through the same state: the first worker
    that sees an expired link triggers the provider; the others simply pick
    up the new URL (guarded by a generation counter).
    """

    original_url: str
    max_refreshes: int
    url: str = ""
    count: int = 0
    gen: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        if not self.url:
            self.url = self.original_url

    def current(self) -> tuple[str, int]:
        with self._lock:
            return self.url, self.gen

    def refresh(self, seen_gen: int, provider, session, timeout: float) -> bool:
        """Refresh the URL if nobody else did since ``seen_gen``. True if refreshed."""
        with self._lock:
            if self.gen != seen_gen:
                return False  # another segment already refreshed it
            if provider is None:
                raise LinkExpiredError("no refresh provider configured for this URL")
            if self.count >= self.max_refreshes:
                raise LinkExpiredError(f"exceeded max refreshes ({self.max_refreshes})")
            new_url = resolve_url(self.original_url, provider, session, timeout)
            self.url = new_url
            self.count += 1
            self.gen += 1
            return True


class _SegProgress:
    """Thread-safe aggregate progress across segment workers."""

    def __init__(self, outer: Downloader, task: DownloadTask, n: int):
        self._outer = outer
        self._task = task
        self._have = [0] * n
        self._emitted = -1
        self._lock = threading.Lock()

    def update(self, i: int, nbytes: int) -> None:
        with self._lock:
            self._have[i] = nbytes
            s = sum(self._have)
            self._task.downloaded = s
            if s - self._emitted >= EMIT_THRESHOLD or s == self._task.total:
                self._emitted = s
                self._outer._emit(self._task)


def dedupe_path(dest: Path) -> Path:
    if not dest.exists():
        return dest
    stem, suffix = dest.stem, dest.suffix
    i = 1
    while True:
        cand = dest.with_name(f"{stem} ({i}){suffix}")
        if not cand.exists():
            return cand
        i += 1


class Downloader:
    def __init__(
        self,
        config: dict | None = None,
        *,
        out_dir: str | Path | None = None,
        workers: int | None = None,
        segments: int | None = None,
        retries: int | None = None,
        timeout: float | None = None,
        chunk_size: int | None = None,
        overwrite: bool = False,
        progress_cb: Callable[..., object] | None = None,
        log_cb: Callable[..., object] | None = None,
        state: State | None = None,
        cancel_event: threading.Event | None = None,
    ):
        cfg = config or {}
        self.config = cfg
        self.out_dir = Path(out_dir or cfg.get("out_dir") or "downloads")
        self.workers = int(workers or cfg.get("workers", 4))
        self.segments = int(segments if segments is not None else cfg.get("segments", 8))
        self.min_segmented_size = int(cfg.get("min_segmented_size", 4 << 20))
        self.retries = int(retries if retries is not None else cfg.get("retries", 5))
        self.html_guard = bool(cfg.get("html_guard", True))
        # part of html_guard: also refuse HLS/M3U playlists served as the
        # media itself (they can never play as the file their name promises)
        self.playlist_guard = bool(cfg.get("playlist_guard", True))
        self.timeout = float(timeout or cfg.get("timeout", 30))
        self.chunk_size = int(chunk_size or cfg.get("chunk_size", 1 << 20))
        self.max_refreshes = int(cfg.get("max_refreshes", 3))
        self.overwrite = bool(overwrite)
        self._domain_headers = cfg.get("domain_headers") or []
        self.progress_cb = progress_cb
        self.log_cb = log_cb
        self.state = state
        self.cancel_event = cancel_event or threading.Event()

        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": "Mozilla/5.0 PyIDM/1.0", "Accept": "*/*", **(cfg.get("headers") or {})}
        )
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=8, pool_maxsize=max(32, self.segments)
        )
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

        self._lock = threading.Lock()
        self._active: set[str] = set()

    # ------------------------------------------------------------------ misc
    # -------------------------------------------------------------- request
    def _request_headers(self, url: str, base: dict | None = None) -> dict:
        """Per-request headers: the call-site's base headers plus any
        per-domain extras configured for this URL."""
        extra = headers_for_url(url, self._domain_headers)
        return {**(base or {}), **extra} if extra else (base or {})

    def log(self, msg: str, level: str = "info") -> None:
        if self.log_cb:
            try:
                self.log_cb(msg, level)
            except Exception:
                pass

    def _emit(self, task: DownloadTask) -> None:
        if self.progress_cb:
            try:
                self.progress_cb(task)
            except Exception:
                pass

    def _check_cancel(self) -> None:
        if self.cancel_event.is_set():
            raise CancelledError()

    def _claim(self, dest: Path) -> Path:
        with self._lock:
            p = dest
            i = 1
            while str(p).lower() in self._active:
                p = dest.with_name(f"{dest.stem} ({i}){dest.suffix}")
                i += 1
            self._active.add(str(p).lower())
            return p

    def _release(self, dest: Path) -> None:
        with self._lock:
            self._active.discard(str(dest).lower())

    def _save_state(self, task: DownloadTask) -> None:
        if not self.state:
            return
        if task.status in ("done", "skipped"):
            self.state.remove(task.url)
        else:
            self.state.set(
                task.url,
                status=task.status,
                message=task.message,
                filename=task.filename,
                size=task.total,
                attempts=task.attempts,
                refreshes=task.refreshes,
            )

    # -------------------------------------------------------------- filename
    def _pick_filename(self, resp, url: str, hint: str | None) -> str:
        if hint:
            return sanitize_filename(hint)
        cd = resp.headers.get("Content-Disposition", "")
        m = re.search(r"filename\*=UTF-8''([^;]+)", cd, re.IGNORECASE) or re.search(
            r'filename="?([^";]+)"?', cd, re.IGNORECASE
        )
        if m:
            return sanitize_filename(
                _unmangle_cd_name(unquote(m.group(1).strip())))
        name = unquote(Path(urlparse(resp.url).path).name)
        return (sanitize_filename(_unmangle_cd_name(name))
                if name else "download.bin")

    def _backoff(self, attempt: int) -> float:
        return min(30.0, (2.0 ** attempt) * random.uniform(0.7, 1.3))

    @staticmethod
    def _head_looks_like_html(head: bytes) -> bool:
        """Heuristic: does the leading chunk of a stream look like a web
        page? Sites behind hotlink protection answer a direct media URL
        with HTTP 200 + an HTML page instead of an error status, which
        would otherwise be saved verbatim under a media filename."""
        return _looks_like_html(head)

    def _head_looks_like_guarded(self, head: bytes) -> bool:
        """Anything html_guard refuses outright: a web page, or — while
        playlist_guard is on — an HLS/M3U playlist answered as the media
        itself (segment lists can never play as the requested file)."""
        if self._head_looks_like_html(head):
            return True
        return bool(self.playlist_guard and _looks_like_m3u(head))

    def _guard_message(self, head: bytes) -> str:
        """The refusal message matching what _head_looks_like_guarded saw
        (same precedence: a page that embeds #EXTM3U still reads as HTML)."""
        if self._head_looks_like_html(head):
            return _HTML_GUARD_MSG
        if self.playlist_guard and _looks_like_m3u(head):
            return _PLAYLIST_GUARD_MSG
        return _HTML_GUARD_MSG

    def _warn_on_kind_mismatch(self, dest: Path, url: str) -> str:
        """Post-download sanity check: warn when the saved file's magic
        bytes contradict what its filename promises (e.g. an .mp4 that is
        really a JPEG, or an HLS playlist saved as a video). Non-fatal by
        design — the download stands; only the log says something is off.
        Unrecognizable content is never warned about. Returns the short
        note (empty when there is nothing to flag) so callers can surface
        it on the task (GUI row badge, JSON summary)."""
        note = scan_file_for_name(dest)
        if not note:
            return ""
        self.log(
            f"[warn] {dest.name}: {note}",
            "warn",
        )
        return note

    # ------------------------------------------------------------ entrypoint
    def download_one(self, url: str, name_hint: str | None = None) -> DownloadTask:
        task = DownloadTask(url=url, name_hint=name_hint)
        rs = _RefreshState(original_url=url, max_refreshes=self.max_refreshes)
        try:
            self.out_dir.mkdir(parents=True, exist_ok=True)
            provider = find_provider(url, self.config.get("link_providers") or [])
            self._download(task, rs, provider)
        except CancelledError:
            task.status = "cancelled"
            task.message = "cancelled by user"
            self.log(f"[cancelled] {url}", "warn")
        except DownloadFailed as e:
            task.status = "error"
            task.message = str(e)
            self.log(f"[error] {url}: {e}", "error")
        except LinkExpiredError as e:
            task.status = "error"
            task.message = str(e)
            self.log(f"[expired] {url}: {e}", "error")
        except Exception as e:
            task.status = "error"
            task.message = f"{type(e).__name__}: {e}"
            self.log(f"[error] {url}: {task.message}", "error")
        finally:
            if task.dest:
                self._release(task.dest)
            self._save_state(task)
            self._emit(task)
        return task

    # ----------------------------------------------------------------- probe
    def _probe(self, task: DownloadTask, rs: _RefreshState, provider) -> dict:
        """GET Range bytes=0-0 to learn size, Range support, ETag, filename."""
        attempts = 0
        while True:
            self._check_cancel()
            url, gen = rs.current()
            attempts += 1
            task.attempts = attempts
            try:
                resp = self.session.get(
                    url, headers=self._request_headers(url, {"Range": "bytes=0-0"}),
                    stream=True, timeout=self.timeout,
                )
            except requests.RequestException as e:
                if attempts >= self.retries:
                    raise DownloadFailed(
                        f"network error probing after {attempts} attempt(s): {e}"
                    )
                self.log(f"[retry {attempts}/{self.retries}] {url} — {e}", "warn")
                time.sleep(self._backoff(attempts))
                continue

            status = resp.status_code
            if status >= 400:
                try:
                    body = next(resp.iter_content(2048), b"")
                except Exception:
                    body = b""
                headers = resp.headers
                resp.close()
                if looks_expired(status, headers, body):
                    try:
                        if rs.refresh(gen, provider, self.session, self.timeout):
                            task.refreshes = rs.count
                            self.log(
                                f"[refresh {task.refreshes}/{self.max_refreshes}] "
                                f"link expired (HTTP {status}); fresh URL: {rs.url}",
                                "warn",
                            )
                    except LinkExpiredError as e:
                        raise DownloadFailed(
                            f"link expired (HTTP {status}) and refresh failed: {e}"
                        )
                    continue
                if status in RETRYABLE_STATUSES and attempts < self.retries:
                    time.sleep(self._backoff(attempts))
                    continue
                raise DownloadFailed(f"HTTP {status}")

            range_ok = False
            total = None
            if status == 206:
                range_ok = True
                m = re.search(r"/(\d+)\s*$", resp.headers.get("Content-Range", ""))
                if m:
                    total = int(m.group(1))
            else:
                cl = resp.headers.get("Content-Length")
                if cl and cl.isdigit():
                    total = int(cl)
            return {
                "resp": resp,
                "total": total,
                "range_ok": range_ok,
                "etag": resp.headers.get("ETag"),
            }

    # -------------------------------------------------------------- dispatch
    def _download(self, task: DownloadTask, rs: _RefreshState, provider) -> None:
        task.status = "downloading"
        self._emit(task)

        probe = self._probe(task, rs, provider)
        resp = probe["resp"]
        try:
            task.filename = self._pick_filename(resp, rs.url, task.name_hint)
        finally:
            resp.close()

        total = probe["total"]
        task.total = total
        dest = self.out_dir / task.filename
        if dest.exists() and not self.overwrite:
            task.dest = dest
            task.status = "skipped"
            task.message = "already exists"
            self.log(f"[skip] {dest.name} already exists", "warn")
            return
        dest = self._claim(dest)
        task.dest = dest

        part_single = Path(str(dest) + ".part")
        use_segments = bool(
            self.segments > 1
            and probe["range_ok"]
            and total
            and total >= self.min_segmented_size
            and not part_single.exists()
        )
        if use_segments:
            segs = self._plan_segments(total)
            try:
                self._download_segmented(task, rs, provider, dest, segs, probe["etag"])
                return
            except _RangeLost:
                self.log(
                    "[warn] server stopped honoring ranges; "
                    "falling back to a single connection",
                    "warn",
                )
                for s in segs:
                    Path(str(dest) + f".part{s.index}").unlink(missing_ok=True)

        self._download_single(task, rs, provider, dest, part_single, probe["etag"], total)

    def _plan_segments(self, total: int) -> list[_SegRange]:
        n = int(min(self.segments, max(1, total // MIN_SEG_BYTES)))
        seg_len = (total + n - 1) // n
        return [
            _SegRange(i, i * seg_len, min((i + 1) * seg_len, total) - 1)
            for i in range(n)
        ]

    # ------------------------------------------------------------- segmented
    def _download_segmented(
        self,
        task: DownloadTask,
        rs: _RefreshState,
        provider,
        dest: Path,
        segs: list[_SegRange],
        etag: str | None,
    ) -> None:
        task.total = sum(s.length for s in segs)
        task.segments = len(segs)
        prog = _SegProgress(self, task, len(segs))
        for s in segs:
            p = Path(str(dest) + f".part{s.index}")
            if p.exists():
                prog.update(s.index, p.stat().st_size)

        self.log(f"[seg] {dest.name}: {len(segs)} parallel connections, "
                 f"{human_size(task.total)}")
        err = None
        with ThreadPoolExecutor(max_workers=len(segs)) as ex:
            futs = [
                ex.submit(
                    self._seg_worker, task, rs, provider, s,
                    Path(str(dest) + f".part{s.index}"), etag, prog,
                )
                for s in segs
            ]
            for fut in as_completed(futs):
                try:
                    fut.result()
                except CancelledError:
                    err = err or "__cancel__"
                except _RangeLost:
                    err = err or "__rangelost__"
                except DownloadFailed as e:
                    err = err or str(e)
                except Exception as e:  # defensive: never hang on surprises
                    err = err or f"{type(e).__name__}: {e}"

        if err == "__cancel__":
            raise CancelledError()
        if err == "__rangelost__":
            raise _RangeLost()
        if err:
            task.status = "error"
            task.message = err
            return

        merged = Path(str(dest) + ".merge")
        try:
            with open(merged, "wb") as out:
                for s in segs:
                    p = Path(str(dest) + f".part{s.index}")
                    size = p.stat().st_size
                    if size != s.length:
                        task.status = "error"
                        task.message = (
                            f"segment {s.index} incomplete "
                            f"({size}/{s.length} bytes)"
                        )
                        return
                    with open(p, "rb") as f:
                        shutil.copyfileobj(f, out, 1 << 20)
            os.replace(merged, dest)
            task.note = self._warn_on_kind_mismatch(dest, rs.url) or task.note
        finally:
            if merged.exists():
                merged.unlink(missing_ok=True)

        for s in segs:
            Path(str(dest) + f".part{s.index}").unlink(missing_ok=True)
        task.downloaded = task.total
        task.status = "done"
        task.message = "ok"
        self.log(
            f"[done] {dest.name} ({human_size(task.total)}, "
            f"{len(segs)} connections)"
        )
        self._emit(task)

    def _seg_worker(
        self,
        task: DownloadTask,
        rs: _RefreshState,
        provider,
        seg: _SegRange,
        part: Path,
        etag: str | None,
        prog: _SegProgress,
    ) -> None:
        attempts = 0
        while True:
            self._check_cancel()
            have = part.stat().st_size if part.exists() else 0
            if have >= seg.length:
                prog.update(seg.index, seg.length)
                return
            # html_guard (resume variant): a leftover .partN that starts with
            # a web page is from a poisoned run — restart this segment clean.
            if self.html_guard and have > 0:
                try:
                    with open(part, "rb") as f:
                        head = f.read(HTML_GUARD_HEAD_BYTES)
                except OSError:
                    head = b""
                if head and self._head_looks_like_guarded(head):
                    part.unlink(missing_ok=True)
                    have = 0
                    self.log(
                        f"[html_guard] segment {seg.index}: saved part is a "
                        "web page or playlist, not media; restarting the segment",
                        "warn",
                    )
            url, gen = rs.current()
            headers = {"Range": f"bytes={seg.start + have}-{seg.end}"}
            if have > 0 and etag:
                headers["If-Range"] = etag
            attempts += 1
            try:
                resp = self.session.get(
                    url, headers=self._request_headers(url, headers),
                    stream=True, timeout=self.timeout,
                )
            except requests.RequestException as e:
                if attempts >= self.retries:
                    raise DownloadFailed(
                        f"segment {seg.index}: network error after "
                        f"{attempts} attempt(s): {e}"
                    )
                self.log(f"[retry {attempts}/{self.retries}] segment {seg.index}: {e}",
                         "warn")
                time.sleep(self._backoff(attempts))
                continue

            status = resp.status_code
            if status >= 400:
                try:
                    body = next(resp.iter_content(2048), b"")
                except Exception:
                    body = b""
                h = resp.headers
                resp.close()
                if looks_expired(status, h, body):
                    try:
                        if rs.refresh(gen, provider, self.session, self.timeout):
                            task.refreshes = rs.count
                            self.log(
                                f"[refresh {task.refreshes}/{self.max_refreshes}] "
                                f"link expired (HTTP {status}); fresh URL: {rs.url}",
                                "warn",
                            )
                    except LinkExpiredError as e:
                        raise DownloadFailed(
                            f"segment {seg.index}: link expired (HTTP {status}) "
                            f"and refresh failed: {e}"
                        )
                    continue
                if status in RETRYABLE_STATUSES and attempts < self.retries:
                    time.sleep(self._backoff(attempts))
                    continue
                raise DownloadFailed(f"segment {seg.index}: HTTP {status}")

            if status == 200:
                # Expected 206: either content changed under our If-Range or
                # the server ignores ranges for this request.
                resp.close()
                raise _RangeLost()

            m = re.match(
                r"bytes (\d+)-", resp.headers.get("Content-Range", "")
            )
            if m and int(m.group(1)) != seg.start + have:
                resp.close()
                with open(part, "wb"):
                    pass  # restart this segment from its beginning
                time.sleep(0.5)
                continue

            written = have
            try:
                with open(part, "ab" if have else "wb") as f:
                    for chunk in resp.iter_content(self.chunk_size):
                        self._check_cancel()
                        if chunk:
                            # html_guard: only a *fresh* segment stream (nothing
                            # written yet) can be a hotlink-protection page.
                            if (
                                self.html_guard
                                and written == 0
                                and self._head_looks_like_guarded(chunk)
                            ):
                                raise DownloadFailed(
                                    f"segment {seg.index}: "
                                    f"{self._guard_message(chunk)}"
                                )
                            f.write(chunk)
                            written = f.tell()
                            prog.update(seg.index, written)
            except requests.RequestException as e:
                if attempts >= self.retries:
                    raise DownloadFailed(
                        f"segment {seg.index}: connection lost after "
                        f"{attempts} attempt(s): {e}"
                    )
                self.log(
                    f"[retry {attempts}/{self.retries}] segment {seg.index}: "
                    f"connection lost; resuming",
                    "warn",
                )
                time.sleep(self._backoff(attempts))
                continue
            finally:
                resp.close()

            final = part.stat().st_size
            if final > seg.length:
                raise _RangeLost()
            if final < seg.length:
                if attempts >= self.retries:
                    raise DownloadFailed(
                        f"segment {seg.index}: truncated ({final}/{seg.length} bytes)"
                    )
                time.sleep(1.0)
                continue
            prog.update(seg.index, seg.length)
            return

    # ---------------------------------------------------------- single-stream
    def _download_single(
        self,
        task: DownloadTask,
        rs: _RefreshState,
        provider,
        dest: Path,
        part: Path,
        etag: str | None,
        total_hint: int | None,
    ) -> None:
        attempts = 0
        while True:
            self._check_cancel()
            have = part.stat().st_size if part.exists() else 0
            task.downloaded = have
            headers = {}
            resumed = have > 0
            if resumed:
                headers["Range"] = f"bytes={have}-"
                if etag:
                    headers["If-Range"] = etag

            url, gen = rs.current()
            attempts += 1
            task.attempts = attempts
            try:
                resp = self.session.get(
                    url, headers=self._request_headers(url, headers),
                    stream=True, timeout=self.timeout,
                )
            except requests.RequestException as e:
                if attempts >= self.retries:
                    task.status = "error"
                    task.message = f"network error after {attempts} attempt(s): {e}"
                    return
                self.log(
                    f"[retry {attempts}/{self.retries}] {task.url} — {e}; "
                    f"retrying in {self._backoff(attempts):.1f}s",
                    "warn",
                )
                time.sleep(self._backoff(attempts))
                continue

            status = resp.status_code
            if status >= 400:
                try:
                    body = next(resp.iter_content(2048), b"")
                except Exception:
                    body = b""
                h = resp.headers
                resp.close()
                if looks_expired(status, h, body):
                    try:
                        if rs.refresh(gen, provider, self.session, self.timeout):
                            task.refreshes = rs.count
                            self.log(
                                f"[refresh {task.refreshes}/{self.max_refreshes}] "
                                f"link expired (HTTP {status}); fresh URL: {rs.url}",
                                "warn",
                            )
                    except LinkExpiredError as e:
                        task.status = "error"
                        task.message = (
                            f"link expired (HTTP {status}) and refresh failed: {e}"
                        )
                        return
                    continue
                if status in RETRYABLE_STATUSES and attempts < self.retries:
                    time.sleep(self._backoff(attempts))
                    continue
                task.status = "error"
                task.message = f"HTTP {status}"
                return

            if status == 206:
                m = re.match(r"bytes (\d+)-", resp.headers.get("Content-Range", ""))
                if not m or int(m.group(1)) != have:
                    have = 0
                    resumed = False
            elif status == 200 and have > 0:
                have = 0
                resumed = False

            cl = resp.headers.get("Content-Length")
            total = total_hint
            if cl and cl.isdigit():
                total = have + int(cl) if (status == 206 and resumed) else int(cl)
            task.total = total

            if have == 0:
                new_etag = resp.headers.get("ETag")
                if new_etag:
                    etag = new_etag

            # html_guard (resume variant): a leftover .part whose *start* is
            # a web page means a previous run saved hotlink-protection HTML
            # — discard it and start the transfer clean (bounded: this only
            # fires when have > 0, and after the unlink have == 0).
            if self.html_guard and have > 0:
                try:
                    with open(part, "rb") as f:
                        head = f.read(HTML_GUARD_HEAD_BYTES)
                except OSError:
                    head = b""
                if head and self._head_looks_like_guarded(head):
                    part.unlink(missing_ok=True)
                    self.log(
                        f"[html_guard] {task.url}: the saved file is a web "
                        "page or playlist, not media; discarding the leftover "
                        "part and retrying",
                        "warn",
                    )
                    continue

            written = have
            guarded = False
            # html_guard: hold back the first bytes of a fresh stream until
            # we have enough to tell a web page from real media (segmented
            # requests that get HTML fall back to this single-stream path
            # via _RangeLost, so this is the one choke point).
            pending: bytearray | None = bytearray()
            try:
                with open(part, "ab" if resumed else "wb") as f:
                    for chunk in resp.iter_content(self.chunk_size):
                        self._check_cancel()
                        if not chunk:
                            continue
                        if self.html_guard and written == 0 and pending is not None:
                            pending.extend(chunk)
                            if len(pending) < HTML_GUARD_HEAD_BYTES:
                                continue
                            if self._head_looks_like_guarded(bytes(pending)):
                                guarded = True
                                break
                            f.write(bytes(pending))
                            pending = None
                            written = f.tell()
                            task.downloaded = written
                            self._emit(task)
                            continue
                        f.write(chunk)
                        written = f.tell()
                        task.downloaded = written
                        self._emit(task)
                    # stream ended before the guard head filled: decide on
                    # what was held back (a small genuine file, e.g. an icon)
                    if pending:
                        if (self.html_guard and written == 0
                                and self._head_looks_like_guarded(bytes(pending))):
                            guarded = True
                        else:
                            f.write(bytes(pending))
                            written = f.tell()
            except requests.RequestException as e:
                if attempts >= self.retries:
                    task.status = "error"
                    task.message = (
                        f"connection lost mid-transfer after {attempts} attempt(s): {e}"
                    )
                    return
                self.log(
                    f"[retry {attempts}/{self.retries}] connection lost "
                    f"({human_size(written)} kept); resuming",
                    "warn",
                )
                time.sleep(self._backoff(attempts))
                continue
            finally:
                resp.close()

            if guarded:
                part.unlink(missing_ok=True)
                task.status = "error"
                task.message = self._guard_message(bytes(pending))
                self.log(f"[error] {task.url}: {task.message}", "error")
                return

            task.downloaded = written
            if total is not None and written < total:
                if attempts >= self.retries:
                    task.status = "error"
                    task.message = f"truncated: got {written} of {total} bytes"
                    return
                self.log(
                    f"[retry {attempts}/{self.retries}] truncated at "
                    f"{human_size(written)}/{human_size(total)}; resuming…",
                    "warn",
                )
                time.sleep(1.0)
                continue

            os.replace(part, dest)
            task.note = self._warn_on_kind_mismatch(dest, rs.url) or task.note
            task.total = total if total is not None else written
            task.status = "done"
            task.message = "ok"
            self.log(f"[done] {dest.name} ({human_size(written)})")
            self._emit(task)
            return

    # ----------------------------------------------------------------- batch
    def download_batch(self, jobs) -> list[DownloadTask]:
        """jobs: iterable of (url, name_hint|None). Returns tasks in input order."""
        jobs = list(jobs)
        results: list[DownloadTask] = []
        with ThreadPoolExecutor(max_workers=max(1, self.workers)) as ex:
            futs = [ex.submit(self.download_one, u, n) for u, n in jobs]
            for fut in as_completed(futs):
                results.append(fut.result())
        order = {u: i for i, (u, _) in enumerate(jobs)}
        results.sort(key=lambda t: order.get(t.url, 1 << 30))
        return results
