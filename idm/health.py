"""Provider health checks: DNS, HTTP reachability, key validity, deep probe.

Used by ``idm providers [--deep]``. Every probe returns a ProviderHealth with
one of three statuses: ok / warn / down. Exit code of the CLI command is 0 when
no provider is fully *down*, 1 otherwise — usable in scripts.
"""
from __future__ import annotations

import os
import re
import socket
from dataclasses import dataclass, field

import requests

from .config import get_user_env
from .subtitles import (
    _SC_BASE,
    _SC_HEADERS,
    _YYS_BASE,
    _YYS_UA,
    OS_BASE,
    _provider_names,
)

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) PyIDM/1.4"}


@dataclass
class ProviderHealth:
    name: str            # provider key in the chain config
    label: str
    status: str          # "ok" | "warn" | "down"
    detail: str
    endpoint: str = ""
    hints: list[str] = field(default_factory=list)


def _dns_ok(host: str) -> tuple[bool, str]:
    """Resolve host. Returns (ok, detail). A poisoned/NXDOMAIN answer means
    ISP-level blocking (e.g. Podnapisi returning 36.37.242.94 here)."""
    try:
        ip = socket.gethostbyname(host)
        return True, f"DNS ok ({ip})"
    except OSError as e:
        return False, f"DNS failed ({e})"


def check_dns() -> list[ProviderHealth]:
    """Infrastructure-level check: can this machine even reach the provider hosts?"""
    hosts = [
        ("opensubtitles", "OpenSubtitles", "api.opensubtitles.com"),
        ("yifysubtitles", "YIFYSubtitles", "yifysubtitles.ch"),
        ("subtitlecat", "SubtitleCat", "www.subtitlecat.com"),
    ]
    out = []
    for key, label, host in hosts:
        ok, detail = _dns_ok(host)
        out.append(ProviderHealth(
            name=key, label=label, status="ok" if ok else "down",
            detail=detail, endpoint=host,
            hints=[] if ok else [("host unreachable from this network — the chain "
                                  "will skip it automatically")],
        ))
    return out


def _get(url: str, headers: dict | None = None, timeout: float = 12.0):
    return requests.get(url, headers=headers or UA, timeout=timeout)


def check_opensubtitles() -> ProviderHealth:
    key = os.environ.get("OPENSUBTITLES_API_KEY", "") or ""
    if not key:
        key = get_user_env("OPENSUBTITLES_API_KEY") or ""
    if not key:
        return ProviderHealth(
            name="opensubtitles", label="OpenSubtitles", status="warn",
            detail="no API key configured (keyless providers still cover you)",
            endpoint=OS_BASE,
            hints=["get a free key at opensubtitles.com → Profile → API Keys, then:",
                   "  idm config set opensubtitles_api_key YOUR_KEY"],
        )
    try:
        r = _get(f"{OS_BASE}/subtitles", headers={"Api-Key": key,
                                                  "User-Agent": "TemporaryUserAgent",
                                                  "Accept": "application/json"},
                 timeout=12.0)
    except requests.RequestException as e:
        return ProviderHealth(
            name="opensubtitles", label="OpenSubtitles", status="down",
            detail=f"request failed: {type(e).__name__}", endpoint=OS_BASE,
            hints=["the chain will use the keyless providers instead"],
        )
    if r.status_code == 200:
        return ProviderHealth(
            name="opensubtitles", label="OpenSubtitles", status="ok",
            detail="API key accepted", endpoint=OS_BASE,
        )
    if r.status_code == 401:
        return ProviderHealth(
            name="opensubtitles", label="OpenSubtitles", status="down",
            detail="API key rejected (HTTP 401)", endpoint=OS_BASE,
            hints=["check the key: idm config getenv OPENSUBTITLES_API_KEY"],
        )
    if r.status_code == 429:
        return ProviderHealth(
            name="opensubtitles", label="OpenSubtitles", status="warn",
            detail="reachable but rate limited (HTTP 429) — free tier limits",
            endpoint=OS_BASE,
        )
    return ProviderHealth(
        name="opensubtitles", label="OpenSubtitles", status="warn",
        detail=f"unexpected HTTP {r.status_code}", endpoint=OS_BASE,
    )


def check_yifysubtitles(deep: bool = False) -> ProviderHealth:
    ok, detail = _dns_ok(_YYS_BASE.replace("https://", ""))
    if not ok:
        return ProviderHealth(name="yifysubtitles", label="YIFYSubtitles",
                              status="down", detail=detail, endpoint=_YYS_BASE)
    try:
        r = _get(f"{_YYS_BASE}/movie-imdb/tt1375666", timeout=12.0)  # Inception
    except requests.RequestException as e:
        return ProviderHealth(name="yifysubtitles", label="YIFYSubtitles",
                              status="down", detail=f"HTTP failed: {type(e).__name__}",
                              endpoint=_YYS_BASE)
    if r.status_code != 200:
        return ProviderHealth(name="yifysubtitles", label="YIFYSubtitles",
                              status="warn", detail=f"unexpected HTTP {r.status_code}",
                              endpoint=_YYS_BASE)
    detail = "movie pages reachable"
    if deep:
        d = _get(f"{_YYS_BASE}/subtitles/inception-2010-english-yify-244676",
                 timeout=12.0)
        m = re.search(r'href="(/subtitle/[^"#?]+\.zip)"', d.text) if d.status_code == 200 else None
        if not m:
            return ProviderHealth(name="yifysubtitles", label="YIFYSubtitles",
                                  status="warn", detail="reachable but no download link "
                                  "found on test page", endpoint=_YYS_BASE)
        z = _get(f"{_YYS_BASE}{m.group(1)}",
                 headers={**_YYS_UA, "Referer": f"{_YYS_BASE}/subtitles/inception-2010-english-yify-244676"},
                 timeout=20.0)
        if z.status_code == 200 and z.content[:2] == b"PK":
            detail = "end-to-end OK (fetched a real subtitle zip)"
        else:
            return ProviderHealth(name="yifysubtitles", label="YIFYSubtitles",
                                  status="warn",
                                  detail=f"zip fetch failed (HTTP {z.status_code})",
                                  endpoint=_YYS_BASE,
                                  hints=[("their CDN needs a Referer header — "
                                          "PyIDM sends it automatically")])
    return ProviderHealth(name="yifysubtitles", label="YIFYSubtitles", status="ok",
                          detail=detail, endpoint=_YYS_BASE)


def check_subtitlecat(deep: bool = False) -> ProviderHealth:
    ok, detail = _dns_ok(_SC_BASE.replace("https://", "").rstrip("/"))
    if not ok:
        return ProviderHealth(name="subtitlecat", label="SubtitleCat",
                              status="down", detail=detail, endpoint=_SC_BASE)
    try:
        r = _get(_SC_BASE, timeout=12.0)
    except requests.RequestException as e:
        return ProviderHealth(name="subtitlecat", label="SubtitleCat",
                              status="down", detail=f"HTTP failed: {type(e).__name__}",
                              endpoint=_SC_BASE)
    if r.status_code != 200:
        return ProviderHealth(name="subtitlecat", label="SubtitleCat",
                              status="warn", detail=f"unexpected HTTP {r.status_code}",
                              endpoint=_SC_BASE)
    detail = "site reachable (native search is intermittently empty; chain absorbs it)"
    if deep:
        r2 = _get("https://www.subtitlecat.com/subs/751/Sintel.2010.720p.BluRay.x264.AAC-%5BYTS.MX%5D.html",
                  headers=_SC_HEADERS, timeout=15.0)
        srt = re.findall(r'href="(/subs/[^"]+\.srt)"', r2.text) if r2.status_code == 200 else []
        if srt:
            s = _get("https://www.subtitlecat.com" + srt[0], headers=_SC_HEADERS, timeout=30.0)
            if s.status_code == 200 and b"-->" in s.content:
                detail = "end-to-end OK (fetched a real .srt)"
            else:
                return ProviderHealth(name="subtitlecat", label="SubtitleCat",
                                      status="warn", detail=f".srt fetch failed (HTTP {s.status_code})",
                                      endpoint=_SC_BASE)
        else:
            return ProviderHealth(name="subtitlecat", label="SubtitleCat",
                                  status="warn", detail="reachable but no .srt links on "
                                  "the test page", endpoint=_SC_BASE)
    return ProviderHealth(name="subtitlecat", label="SubtitleCat", status="ok",
                          detail=detail, endpoint=_SC_BASE)


def check_provider(name: str, deep: bool = False) -> ProviderHealth:
    if name == "opensubtitles":
        return check_opensubtitles()
    if name == "yifysubtitles":
        return check_yifysubtitles(deep)
    if name == "subtitlecat":
        return check_subtitlecat(deep)
    return ProviderHealth(name=name, label=name, status="warn",
                          detail="unknown provider (check subtitle_providers config)")


def run_checks(cfg: dict, deep: bool = False) -> list[ProviderHealth]:
    """DNS layer for everything reachable, then per-provider checks in chain order."""
    results = []
    seen = set()
    for name in _provider_names(cfg):
        if name in seen:
            continue
        seen.add(name)
        results.append(check_provider(name, deep))
    return results


def health_payload(results: list[ProviderHealth], mode: str) -> dict:
    """ProviderHealth results -> a lossless JSON-able dict (raw 'ok'/'warn'/'down'
    statuses, hints preserved). `mode` is 'quick' or 'deep'; all_ok is False
    when any provider is down. Same data the text table renders."""
    return {
        "mode": mode,
        "all_ok": not any(r.status == "down" for r in results),
        "providers": [
            {"name": r.name, "label": r.label, "status": r.status,
             "detail": r.detail, "endpoint": r.endpoint, "hints": list(r.hints)}
            for r in results
        ],
    }
