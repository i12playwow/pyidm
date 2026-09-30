"""Subtitle downloading with a provider chain (OpenSubtitles + keyless fallbacks).

Providers are tried in the order given by ``subtitle_providers`` in the config
(default: ``opensubtitles,subtitlecat``). A provider raises SubtitleError to
signal "nothing available here", which moves the chain to the next one — so
subtitles still work when OpenSubtitles is down, out of quota, or you have no
API key at all.

Free API key for OpenSubtitles (https://www.opensubtitles.com, Profile → API Keys):

    idm config set opensubtitles_api_key YOUR_KEY
    (or set the OPENSUBTITLES_API_KEY environment variable)

SubtitleCat (https://www.subtitlecat.com) is keyless: keyword search over a
release-name catalog with direct .srt downloads in many languages.
"""
from __future__ import annotations

import gzip
import os
import re
import struct
from pathlib import Path
from urllib.parse import unquote, urljoin

import requests

OS_BASE = "https://api.opensubtitles.com/api/v1"
VIDEO_EXTS = {
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".ts", ".m2ts", ".webm",
    ".mpg", ".mpeg", ".flv", ".m4v", ".ogv", ".3gp",
}

SEASON_EP_RE = re.compile(r"[sS](\d{1,2})[eE](\d{1,3})")


class SubtitleError(RuntimeError):
    pass


def is_video(path) -> bool:
    return Path(path).suffix.lower() in VIDEO_EXTS


# --------------------------------------------------------------------- hashing
def _sum64(buf: bytes) -> int:
    n = len(buf) // 8
    return sum(struct.unpack(f"<{n}Q", buf[: n * 8])) if n else 0


def opensubtitles_hash(path) -> str:
    """OpenSubtitles movie hash: size + sum of first and last 64 KiB (LE 64-bit)."""
    path = Path(path)
    chunk = 65536
    size = path.stat().st_size
    acc = size
    with path.open("rb") as f:
        head = f.read(chunk)
        acc += _sum64(head)
        if size > chunk:
            f.seek(-chunk, os.SEEK_END)
            acc += _sum64(f.read(chunk))
    return "%016x" % (acc & 0xFFFFFFFFFFFFFFFF)


# ---------------------------------------------------------------------- search
def _headers(api_key: str, user_agent: str) -> dict:
    if not api_key:
        raise SubtitleError(
            "OpenSubtitles API key missing — run: idm config set opensubtitles_api_key YOUR_KEY"
        )
    return {
        "Api-Key": api_key,
        "User-Agent": user_agent or "TemporaryUserAgent",
        "Accept": "application/json",
    }


def search_subtitles(
    api_key: str,
    *,
    user_agent: str = "",
    moviehash: str | None = None,
    filesize: int | None = None,
    query: str | None = None,
    languages: str | None = None,
    season: int | None = None,
    episode: int | None = None,
) -> list[dict]:
    params: dict[str, str] = {}
    if moviehash:
        params["moviehash"] = moviehash
    if filesize:
        params["moviefilesize"] = str(filesize)
    if query:
        params["query"] = query
    if languages:
        params["languages"] = languages
    if season:
        params["season_number"] = str(season)
    if episode:
        params["episode_number"] = str(episode)
    r = requests.get(
        f"{OS_BASE}/subtitles", headers=_headers(api_key, user_agent),
        params=params, timeout=30,
    )
    if r.status_code == 401:
        raise SubtitleError("OpenSubtitles rejected the API key (HTTP 401)")
    if r.status_code == 429:
        raise SubtitleError("rate limited by OpenSubtitles (HTTP 429) — wait and retry")
    r.raise_for_status()
    return r.json().get("data", [])


def _score(attr: dict) -> tuple:
    return (
        -1 if attr.get("ai_translated") else 0,
        -1 if attr.get("machine_translated") else 0,
        1 if not attr.get("hearing_impaired") else 0,
        float(attr.get("ratings") or 0),
        int(attr.get("download_count") or 0),
    )


def pick_best(entries: list[dict]) -> tuple[dict, dict] | None:
    """Return (attributes, file_entry) for the best candidate, or None."""
    candidates = []
    for entry in entries:
        attrs = entry.get("attributes", {})
        files = [f for f in (attrs.get("files") or []) if f.get("file_id")]
        if files:
            candidates.append((_score(attrs), attrs, files))
    if not candidates:
        return None
    candidates.sort(key=lambda t: t[0], reverse=True)
    _, attrs, files = candidates[0]
    return attrs, files[0]


# -------------------------------------------------------------------- download
def request_download_url(api_key: str, file_id, user_agent: str = "") -> dict:
    """Ask OpenSubtitles for a temporary download link for one file_id.

    The endpoint is POST /download with a JSON body (GET with query params
    returns 404 since the API update). Consumes one unit of the daily quota.
    """
    r = requests.post(
        f"{OS_BASE}/download", headers=_headers(api_key, user_agent),
        json={"file_id": file_id}, timeout=60,
    )
    if r.status_code in (401, 403):
        raise SubtitleError(f"download denied (HTTP {r.status_code}): {r.text[:200]}")
    if r.status_code == 406:
        raise SubtitleError("download quota exceeded (HTTP 406) — free tier is ~5/day")
    r.raise_for_status()
    return r.json()


def _fetch_content(link: str) -> bytes:
    r = requests.get(link, timeout=120)
    r.raise_for_status()
    data = r.content
    if data[:2] == b"\x1f\x8b":  # some CDNs serve gzip without Content-Encoding
        data = gzip.decompress(data)
    return data


def save_subtitle(media_path, file_name: str, data: bytes, lang_tag: str, overwrite=False) -> Path:
    media = Path(media_path)
    ext = Path(file_name or "subtitle.srt").suffix or ".srt"
    dest = media.with_name(f"{media.stem}.{lang_tag}{ext}")
    if dest.exists() and not overwrite:
        raise SubtitleError(f"subtitle already exists: {dest.name}")
    dest.write_bytes(data)
    return dest


# --------------------------------------------------------------- text helpers
def _decode(data: bytes) -> str:
    """Best-effort decode of a subtitle payload (many sites serve non-UTF8)."""
    try:
        data.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        pass
    for enc in ("cp1252", "latin-1"):
        try:
            data.decode(enc)
            return enc
        except UnicodeDecodeError:
            continue
    return "latin-1"


def _looks_like_srt(data: bytes) -> bool:
    """Heuristic check that a payload is a subtitle, not an HTML error page."""
    if b"-->" in data:  # srt/vtt timing lines
        return True
    head = data[:512].lstrip(b"\xef\xbb\xbf").lower()
    return head.startswith((b"[script info]", b"[events]"))  # ass/ssa


_TIME_RE = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[.,](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[.,](\d{1,3})"
)
_ASS_TIME_RE = re.compile(
    r"(?im)^dialogue:\s*\d+,(\d):?(\d{2}):(\d{2})\.(\d{2}),(\d):?(\d{2}):(\d{2})\.(\d{2})"
)


def count_cues(data: bytes) -> int:
    """Count well-formed subtitle cues in a payload.

    A cue is a complete start-->end timing pair with sane ordering
    (end after start). Understands SRT/VTT (00:00:01,000 --> ...) and
    ASS/SSA Dialogue lines. Payloads that sniff as subtitle but yield 0
    cues are rejected downstream.
    """
    cues = 0
    for m in _TIME_RE.finditer(data.decode("latin-1", errors="ignore")):
        h1, m1, s1, ms1, h2, m2, s2, ms2 = (int(g) for g in m.groups())
        start = ((h1 * 60 + m1) * 60 + s1) * 1000 + ms1
        end = ((h2 * 60 + m2) * 60 + s2) * 1000 + ms2
        if end > start:
            cues += 1
    if cues == 0:
        for m in _ASS_TIME_RE.finditer(data.decode("latin-1", errors="ignore")):
            h1, m1, s1, cs1, h2, m2, s2, cs2 = (int(g) for g in m.groups())
            start = ((h1 * 60 + m1) * 60 + s1) * 1000 + cs1 * 10
            end = ((h2 * 60 + m2) * 60 + s2) * 1000 + cs2 * 10
            if end > start:
                cues += 1
    return cues


def _release_query(stem_or_title: str) -> str:
    """Clean a filename/title into a good keyword-search query."""
    s = re.sub(r"[\[\]\(\)\{\}]", " ", stem_or_title)
    s = re.sub(r"(?i)\b(x264|x265|h\.?264|h\.?265|hevc|aac|ac3|ddp?5?\.?1|dts|"
               r"hdr|10bit|8bit|web-?rip|web|webrip|br-?rip|bluray|blu-?ray|"
               r"hdrip|dvdrip|1080p|720p|2160p|480p|yts\.?(mx|lt|am)|rarbg|"
               r"xvid|mp3|aac2\.0|proper|repack|mp4|mkv|avi|mov|wmv)\b", " ", s)
    s = re.sub(r"[._\-]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# ------------------------------------------------------- SubtitleCat provider
_SC_BASE = "https://www.subtitlecat.com/"
_SC_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) PyIDM/1.2"}
_SC_LANGS = {
    "english": "en", "arabic": "ar", "brazilian portuguese": "pt-br",
    "bulgarian": "bg", "croatian": "hr", "czech": "cs", "danish": "da",
    "dutch": "nl", "estonian": "et", "farsi": "fa", "persian": "fa",
    "finnish": "fi", "french": "fr", "german": "de", "greek": "el",
    "hebrew": "he", "hungarian": "hu", "indonesian": "id", "italian": "it",
    "japanese": "ja", "korean": "ko", "latvian": "lv", "lithuanian": "lt",
    "norwegian": "no", "polish": "pl", "portuguese": "pt", "romanian": "ro",
    "russian": "ru", "serbian": "sr", "slovenian": "sl", "spanish": "es",
    "swedish": "sv", "thai": "th", "turkish": "tr", "ukrainian": "uk",
    "vietnamese": "vi", "chinese": "zh",
}


def _sc_parse_search(html: str, base: str = _SC_BASE) -> list[tuple[str, str]]:
    """Search page -> [(release_title, detail_url)].

    Result hrefs may be absolute-path (/subs/…) or relative (subs/…) —
    both resolve against the base URL."""
    pages: list[tuple[str, str]] = []
    for href in re.findall(r'href="(?:/)?(subs/[^"]+\.html)"', html):
        url = urljoin(base, href.replace("&amp;", "&"))
        name = unquote(urlparse_path(url))
        if name not in [t for t, _ in pages]:
            pages.append((name, url))
    return pages


def urlparse_path(url: str) -> str:
    from urllib.parse import urlparse
    return Path(unquote(urlparse(url).path)).name


def _sc_parse_detail(html: str, base: str = _SC_BASE) -> list[tuple[str, str, str]]:
    """Detail page -> [(lang_name, lang_code, srt_url)] in page order."""
    out = []
    seen = set()
    for m in re.finditer(
        r'<span><img[^>]*alt="([a-z]{2})"[^>]*></span>\s*'
        r'<span>([^<]+)</span>\s*(?:<span>\s*)?'
        r'<a[^>]*href="(?:/)?(subs/[^"]+\.srt)"', html
    ):
        code2, lang_name, href = m.group(1), m.group(2).strip(), m.group(3)
        code = _SC_LANGS.get(lang_name.lower(), code2 or "unk")
        url = urljoin(base, href.replace("&amp;", "&"))
        if url not in seen:
            seen.add(url)
            out.append((lang_name, code, url))
    return out


def _sc_fetch_srt(url: str) -> bytes:
    r = requests.get(url, headers=_SC_HEADERS, timeout=60)
    r.raise_for_status()
    data = r.content
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return data


def _sc_pick_largest(rows, wanted, fetch=_sc_fetch_srt):
    """Pick the best .srt from a detail page: largest valid file per language.

    rows: [(lang_name, code, srt_url)]; wanted: language codes in priority
    order. Requested languages are tried first (in that order), other
    languages serve as fallback. Within a language EVERY candidate is
    fetched and the LARGEST payload that passes the srt sniff test wins —
    big files are complete subtitles, small ones are often stubs/pieces.
    Returns (lang_name, code, url, data) or None when nothing valid remains.
    """
    def pri(code):
        return wanted.index(code) if code in wanted else len(wanted)

    codes = sorted({r[1] for r in rows}, key=pri)
    for code in codes:
        best = None
        for lang_name, _c, url in (r for r in rows if r[1] == code):
            try:
                data = fetch(url)
            except requests.RequestException:
                continue
            if not _looks_like_srt(data):
                continue
            cues = count_cues(data)
            if cues == 0:
                continue  # sniffs like a subtitle but has no valid cues
            if best is None or len(data) > len(best[3]):
                best = (lang_name, code, url, data)
        if best:
            return best
    return None


def _download_from_subtitlecat(cfg, langs, moviehash=None, filesize=None,
                               query=None, log=print) -> dict:
    """SubtitleCat provider — keyless keyword search. Raises SubtitleError if empty.

    Detail pages are scanned with progressive query shortening ("Movie Name
    2024" -> "Movie Name" -> "Movie"); within a page the LARGEST valid .srt
    in a requested language is preferred (big files are complete subtitles,
    small ones are often stubs/pieces).
    """
    q = _release_query(query) if query else ""
    if not q:
        raise SubtitleError("no usable title for keyword search")
    wanted = [ln.strip().lower() for ln in (langs or "").split(",") if ln.strip()]
    words = q.split()
    attempts = [" ".join(words[:n]) for n in range(len(words), 0, -1)][:4]
    seen_titles = set()
    for attempt in attempts:
        r = requests.get("https://www.subtitlecat.com/index.php",
                         params={"search": attempt}, headers=_SC_HEADERS, timeout=30)
        r.raise_for_status()
        pages = _sc_parse_search(r.text)
        fresh = [(t, u) for t, u in pages if t not in seen_titles]
        for t, _u in fresh:
            seen_titles.add(t)
        if not fresh:
            log(f"[subs] subtitlecat: no results for {attempt!r} — shortening query")
            continue
        log(f"[subs] subtitlecat: {len(fresh)} result(s) for {attempt!r}")
        for _title, detail_url in fresh[:6]:
            try:
                detail = requests.get(detail_url, headers=_SC_HEADERS, timeout=30)
                detail.raise_for_status()
            except requests.RequestException:
                continue
            rows = _sc_parse_detail(detail.text)
            picked = _sc_pick_largest(rows, wanted)
            if not picked:
                continue
            lang_name, code, srt_url, data = picked
            fname = urlparse_path(srt_url)
            log(f"[subs] subtitlecat: found {code} ({lang_name}) — {fname} "
                f"({len(data)} bytes, {count_cues(data)} cues)")
            return {"data": data, "file_name": fname, "lang": code,
                    "remaining": None, "provider": "subtitlecat"}
    raise SubtitleError(f"no {'/'.join(wanted) or 'en'} subtitles on SubtitleCat")


# ------------------------------------------------------- YIFYSubtitles provider
_YYS_BASE = "https://yifysubtitles.ch"
_YYS_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) PyIDM/1.2"}
# subtitle language code -> slug word used in yifysubtitles detail URLs
_YYS_CODE_TO_SLUG = {
    "en": "english", "ar": "arabic", "bg": "bulgarian", "zh": "chinese",
    "hr": "croatian", "cs": "czech", "da": "danish", "nl": "dutch",
    "et": "estonian", "fa": "farsi", "fi": "finnish", "fr": "french",
    "de": "german", "el": "greek", "he": "hebrew", "hu": "hungarian",
    "id": "indonesian", "it": "italian", "ja": "japanese", "ko": "korean",
    "lv": "latvian", "lt": "lithuanian", "no": "norwegian", "pl": "polish",
    "pt": "portuguese", "pt-br": "brazilian-portuguese", "ro": "romanian",
    "ru": "russian", "sr": "serbian", "sl": "slovenian", "es": "spanish",
    "sv": "swedish", "th": "thai", "tr": "turkish", "uk": "ukrainian",
    "vi": "vietnamese",
}
_YYS_SLUG_TO_CODE = {v: k for k, v in _YYS_CODE_TO_SLUG.items()}


def _imdb_id_from_title(query: str, timeout: float = 20.0) -> str | None:
    """Resolve a movie title to an IMDb id via Wikidata (keyless, property P345)."""
    try:
        params: dict[str, str | int] = {
            "action": "wbsearchentities", "search": query, "language": "en",
            "format": "json", "limit": 5, "type": "item"}
        s = requests.get(
            "https://www.wikidata.org/w/api.php",
            params=params,
            headers=_YYS_UA, timeout=timeout,
        )
        s.raise_for_status()
        for ent in s.json().get("search", []):
            c = requests.get(
                "https://www.wikidata.org/w/api.php",
                params={"action": "wbgetclaims", "entity": ent["id"],
                        "property": "P345", "format": "json"},
                headers=_YYS_UA, timeout=timeout,
            )
            c.raise_for_status()
            claims = c.json().get("claims", {}).get("P345")
            if claims:
                return claims[0]["mainsnak"]["datavalue"]["value"]
    except (requests.RequestException, ValueError, KeyError, IndexError):
        return None
    return None


def _yys_pick_srt(zf) -> tuple[str, bytes] | None:
    """Choose the biggest .srt in a subtitle zip (big = full, not a sample)."""
    members = [n for n in zf.namelist() if n.lower().endswith(".srt")]
    if not members:
        return None
    best = max(members, key=lambda n: zf.getinfo(n).file_size)
    return best, zf.read(best)


def _download_from_yifysubtitles(cfg, langs, moviehash=None, filesize=None,
                                 query=None, log=print) -> dict:
    """Keyless provider: Wikidata title→IMDb id → yifysubtitles.ch movie page
    → per-language detail page → subtitle zip (Referer-required) → .srt."""
    import io
    import zipfile

    if not query:
        raise SubtitleError("no usable title for YIFYSubtitles")
    if SEASON_EP_RE.search(query):
        raise SubtitleError("TV episodes not supported by YIFYSubtitles — trying next provider")
    wanted = [ln.strip().lower() for ln in (langs or "en").split(",") if ln.strip()]

    q = _release_query(query)
    # progressive shortening: "Sintel Trailer 2010" -> "Sintel Trailer" -> "Sintel"
    tt = None
    words = q.split()
    for attempt in [" ".join(words[:n]) for n in range(len(words), 0, -1)][:3]:
        tt = _imdb_id_from_title(attempt)
        if tt:
            q = attempt
            break
    if not tt:
        raise SubtitleError("no IMDb id found for query — trying next provider")
    log(f"[subs] yifysubtitles: {q!r} -> {tt}")
    r = requests.get(f"{_YYS_BASE}/movie-imdb/{tt}", headers=_YYS_UA, timeout=30)
    r.raise_for_status()
    details = list(dict.fromkeys(re.findall(r'href="(/subtitles/[^"#?]+)"', r.text)))
    if not details:
        raise SubtitleError("no subtitles listed for this movie — trying next provider")

    tried = 0
    for path in details:
        low = path.lower()
        code = next((c for slug, c in _YYS_SLUG_TO_CODE.items() if f"-{slug}-" in low), None)
        if code is None or (wanted and code not in wanted):
            continue
        if tried >= 6:
            break
        tried += 1
        detail_url = f"{_YYS_BASE}{path}"
        try:
            d = requests.get(detail_url, headers=_YYS_UA, timeout=30)
            d.raise_for_status()
            m = re.search(r'href="(/subtitle/[^"#?]+\.zip)"', d.text)
            if not m:
                continue
            z = requests.get(f"{_YYS_BASE}{m.group(1)}",
                             headers={**_YYS_UA, "Referer": detail_url}, timeout=60)
            z.raise_for_status()
            zf = zipfile.ZipFile(io.BytesIO(z.content))
            picked = _yys_pick_srt(zf)
        except (requests.RequestException, zipfile.BadZipFile, OSError):
            continue
        if not picked or count_cues(picked[1]) == 0:
            continue  # not a subtitle or zero valid cues — next detail page
        name, data = picked
        log(f"[subs] yifysubtitles: found {code} — {name} "
            f"({len(data)} bytes, {count_cues(data)} cues)")
        return {"data": data, "file_name": name, "lang": code,
                "remaining": None, "provider": "yifysubtitles"}
    raise SubtitleError(f"no {'/'.join(wanted)} subtitles on YIFYSubtitles")


# ----------------------------------------------------------------- provider chain
def _provider_names(cfg: dict) -> list[str]:
    raw = str(cfg.get("subtitle_providers") or "opensubtitles,yifysubtitles,subtitlecat")
    names = [n.strip().lower() for n in raw.split(",") if n.strip()]
    return names or ["opensubtitles", "yifysubtitles", "subtitlecat"]


def _provider_label(name: str) -> str:
    return {"opensubtitles": "OpenSubtitles",
            "yifysubtitles": "YIFYSubtitles",
            "subtitlecat": "SubtitleCat"}.get(name, name)


def _download_from_opensubtitles(cfg, langs, moviehash=None, filesize=None,
                                 query=None, log=print) -> dict:
    """OpenSubtitles provider. Raises SubtitleError to signal "not available here".

    Strategy: a moviehash search first (exact match for the local file), then
    a cleaned-name query search. The API rejects raw filename stems with
    HTTP 400 (e.g. 'Sintel.Trailer.2010.720p.x264'), so queries are always
    cleaned via _release_query() first, and HTTP errors fall through to the
    next provider instead of aborting the chain.
    """
    api_key = cfg.get("opensubtitles_api_key") or os.environ.get("OPENSUBTITLES_API_KEY", "")
    if not api_key:
        raise SubtitleError("no API key — skipping to next provider")
    ua = cfg.get("opensubtitles_user_agent", "TemporaryUserAgent")
    entries: list[dict] = []
    try:
        if moviehash:
            try:
                entries = search_subtitles(api_key, user_agent=ua,
                                           moviehash=moviehash, filesize=filesize,
                                           languages=langs)
            except requests.HTTPError as e:
                code = e.response.status_code if e.response is not None else "?"
                log(f"[subs] opensubtitles: hash search failed (HTTP {code}) "
                    "— falling back to query")
                entries = []
        if not entries and query:
            cleaned = _release_query(query)
            words = cleaned.split()
            # progressive shortening: 'Sintel Trailer 2010' -> 'Sintel Trailer' -> 'Sintel'
            for attempt in [" ".join(words[:n]) for n in range(len(words), 0, -1)][:4]:
                if not attempt:
                    continue
                try:
                    entries = search_subtitles(api_key, user_agent=ua,
                                               query=attempt, languages=langs)
                except requests.HTTPError:
                    continue
                if entries:
                    log(f"[subs] opensubtitles: {len(entries)} result(s) for "
                        f"query {attempt!r}")
                    break
    except requests.HTTPError as http_err:
        code = (http_err.response.status_code
                if http_err.response is not None else "?")
        raise SubtitleError(f"OpenSubtitles HTTP {code} — trying next provider")
    except requests.RequestException as e:
        raise SubtitleError(f"OpenSubtitles unreachable ({type(e).__name__}) "
                            "— trying next provider")
    if not entries:
        raise SubtitleError("no results on OpenSubtitles — trying next provider")
    # rank candidates best-first; a dead file (HTTP 404) must not kill the
    # provider, so every candidate file is tried in order
    candidates = []
    for entry in entries:
        attrs = entry.get("attributes", {})
        files = [f for f in (attrs.get("files") or []) if f.get("file_id")]
        if files:
            candidates.append((_score(attrs), attrs, files))
    if not candidates:
        raise SubtitleError("search results had no downloadable files")
    candidates.sort(key=lambda t: t[0], reverse=True)
    tried = 0
    for _s, attrs, files in candidates:
        for f in files:
            if tried >= 4:
                break
            tried += 1
            try:
                info = request_download_url(api_key, f["file_id"], user_agent=ua)
                data = _fetch_content(info["link"])
            except requests.HTTPError as e:
                code = e.response.status_code if e.response is not None else "?"
                if code in (401, 403, 406):  # auth/quota: no point trying more files
                    raise SubtitleError(f"download denied (HTTP {code}) — "
                                        "trying next provider")
                log(f"[subs] opensubtitles: file {f['file_id']} unavailable "
                    f"(HTTP {code}) — next candidate")
                continue
            except requests.RequestException as e:
                log(f"[subs] opensubtitles: download failed ({type(e).__name__}) "
                    "— next candidate")
                continue
            if count_cues(data) == 0:
                log(f"[subs] opensubtitles: file {f['file_id']} has no valid "
                    "cues — next candidate")
                continue
            lang = attrs.get("language") or (langs.split(",")[0] if langs else "unk")
            return {"data": data,
                    "file_name": info.get("file_name") or f.get("file_name") or "",
                    "lang": lang, "remaining": info.get("remaining"),
                    "provider": "opensubtitles"}
    raise SubtitleError("no downloadable file on OpenSubtitles — trying next provider")


def fetch_from_providers(cfg, langs, *, moviehash=None, filesize=None,
                         query=None, log=print) -> dict:
    """Try each configured provider in order; return the first success.

    Raises SubtitleError listing every provider that failed.
    """
    names = _provider_names(cfg)
    errors = []
    for name in names:
        fn = {"opensubtitles": _download_from_opensubtitles,
              "yifysubtitles": _download_from_yifysubtitles,
              "subtitlecat": _download_from_subtitlecat}.get(name)
        if fn is None:
            log(f"[subs] unknown provider {name!r} — skipping", "warn")
            continue
        label = _provider_label(name)
        try:
            log(f"[subs] trying {label}…")
            result = fn(cfg, langs, moviehash=moviehash, filesize=filesize,
                        query=query, log=log)
            if count_cues(result.get("data", b"")) == 0:
                # a provider may return a payload that sniffs as a subtitle
                # but parses to zero cues — treat it as unavailable
                raise SubtitleError("result had no valid cues")
            return result
        except SubtitleError as e:
            errors.append(f"{label}: {e}")
            log(f"[subs] {label} not available ({e})", "warn")
        except requests.RequestException as e:
            errors.append(f"{label}: {type(e).__name__}")
            log(f"[subs] {label} unreachable ({type(e).__name__})", "warn")
    raise SubtitleError("all providers failed — " + "; ".join(errors) if errors
                        else "all providers failed")


def download_for_video(path, cfg: dict, langs: str, overwrite=False, log=print) -> dict:
    """Find + download the best subtitle for one local video file (provider chain)."""
    path = Path(path)
    size = path.stat().st_size
    moviehash = opensubtitles_hash(path)
    try:
        result = fetch_from_providers(
            cfg, langs, moviehash=moviehash, filesize=size, query=path.stem, log=log,
        )
    except SubtitleError as e:
        return {"path": str(path), "ok": False, "message": str(e)}

    dest = save_subtitle(
        path, result["file_name"] or f"{path.stem}.{result['lang']}.srt",
        result["data"], result["lang"], overwrite=overwrite,
    )
    extra = f" ({result['remaining']} downloads left today)" if result.get("remaining") else ""
    log(f"[subs] {path.name}: saved {dest.name} via {result['provider']} "
        f"({count_cues(result['data'])} cues){extra}")
    return {"path": str(path), "ok": True, "dest": str(dest), "language": result["lang"],
            "provider": result["provider"], "size": len(result["data"]),
            "cues": count_cues(result["data"])}


def download_for_title(query: str, cfg: dict, langs: str, out_dir=None, overwrite=False, log=print) -> dict:
    """Search by title text (no local file) and save to out_dir (default cwd)."""
    try:
        result = fetch_from_providers(cfg, langs, query=query, log=log)
    except SubtitleError as e:
        return {"path": query, "ok": False, "message": str(e)}

    base = Path(out_dir) if out_dir else Path.cwd()
    safe = re.sub(r"[^\w\s.-]", "", query).strip() or "subtitle"
    dest = save_subtitle(base / f"{safe}.mkv", result["file_name"], result["data"],
                         result["lang"], overwrite)
    log(f"[subs] {query!r}: saved {dest.name} via {result['provider']} "
        f"({count_cues(result['data'])} cues)")
    return {"path": query, "ok": True, "dest": str(dest), "language": result["lang"],
            "provider": result["provider"], "size": len(result["data"]),
            "cues": count_cues(result["data"])}


def batch_for_folder(folder, cfg: dict, langs: str, overwrite=False, log=print) -> list[dict]:
    """Fetch subtitles for every video file in a folder (non-recursive)."""
    results: list[dict] = []
    videos = sorted(p for p in Path(folder).iterdir() if p.is_file() and is_video(p))
    if not videos:
        log(f"[subs] no video files found in {folder}", "warn")
        return results
    log(f"[subs] {len(videos)} video file(s) in {folder}")
    for v in videos:
        try:
            results.append(download_for_video(v, cfg, langs, overwrite=overwrite, log=log))
        except SubtitleError as e:
            log(f"[subs] {v.name}: {e}", "error")
            results.append({"path": str(v), "ok": False, "message": str(e)})
        except Exception as e:
            log(f"[subs] {v.name}: {type(e).__name__}: {e}", "error")
            results.append({"path": str(v), "ok": False, "message": str(e)})
    return results
