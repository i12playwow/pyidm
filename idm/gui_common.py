"""PyIDM GUI — pure helpers (formatters, filters, sorting, queries).

Data-in/data-out only: no Tk, no store paths, no config reads.
Split out of gui.py (one-shot refactor); not a stable API.
"""
from __future__ import annotations

import time
from pathlib import Path

from .utils import human_size

STATUS_MARKS = {"ok": "OK", "warn": "WARN", "down": "DOWN"}
_HISTORY_HEADER = ("video", "lang", "provider", "size", "cues")
_DOWNLOAD_HEADER = ("url", "filename", "status", "size", "updated")
WEAK_CUES = 50
WEAK_SIZE = 20 * 1024
SUBS_SORT_LABELS = {
    "Newest": "newest",
    "Fewest cues": "cues_asc",
    "Most cues": "cues_desc",
    "Smallest": "size_asc",
    "Largest": "size_desc",
}



def provider_rows(results) -> list[tuple[str, str, str, str]]:
    """ProviderHealth list -> (provider, STATUS, detail, endpoint) rows for the tab."""
    return [(r.label, STATUS_MARKS.get(r.status, r.status.upper()), r.detail, r.endpoint)
            for r in results]
def provider_hints(results) -> list[str]:
    """All actionable hints, labeled with the provider they belong to."""
    return [f"hint ({r.label}): {h}" for r in results for h in r.hints]
def subtitle_row(r: dict) -> tuple[str, str, str, str, str]:
    """One subtitle result -> (video, lang, provider, size, cues) display tuple."""
    size = r.get("size")
    size_s = human_size(size) if isinstance(size, int) else (str(size) if size else "—")
    cues = r.get("cues")
    return (
        Path(r.get("path", "")).name,
        str(r.get("language") or "?"),
        str(r.get("provider") or "?"),
        size_s,
        str(cues) if cues is not None else "—",
    )
def subtitle_rows(results: list[dict]) -> list[tuple[str, str, str, str, str]]:
    """Subtitle download results -> display rows for the history tree.
    Size uses human units; failures are skipped (they already surface in the log)."""
    return [subtitle_row(r) for r in results or [] if r.get("ok")]
def _table_to_csv(rows, header) -> str:
    """Rows of display strings -> CSV text. RFC-4180 quoting (fields with
    commas/quotes are quoted, embedded quotes doubled); CRLF endings so Excel
    opens it cleanly. Em-dash placeholders become empty cells."""
    import csv as _csv
    import io as _io
    buf = _io.StringIO()
    w = _csv.writer(buf, lineterminator="\r\n")
    w.writerow(header)
    for row in rows:
        w.writerow(["" if v == "—" else v for v in row])
    return buf.getvalue()
def _table_to_markdown(rows, header) -> str:
    """Rows of display strings -> a GitHub-flavored markdown table (pastes
    into READMEs, issues, and chat apps as a real table). Pipes escaped."""
    def esc(v: str) -> str:
        return v.replace("|", "\\|")
    lines = ["| " + " | ".join(header) + " |",
             "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(esc(v) for v in row) + " |" for row in rows]
    return "\n".join(lines)
def history_to_csv(rows: list[tuple[str, str, str, str, str]]) -> str:
    """History rows -> CSV text (same RFC-4180 rules as _table_to_csv)."""
    return _table_to_csv(rows, _HISTORY_HEADER)
def history_to_markdown(rows: list[tuple[str, str, str, str, str]]) -> str:
    """History rows -> a GitHub-flavored markdown table."""
    return _table_to_markdown(rows, _HISTORY_HEADER)
def history_to_json(history) -> str:
    """History entries -> JSON for scripting (the filtered OK entries as-is,
    every field preserved: path, dest, language, provider, size, cues, ts).
    ASCII-escaped output so any console codepage can take it, and piping is
    byte-stable across machines."""
    import json as _json
    ok = [r for r in (history or []) if r.get("ok")]
    return _json.dumps(ok, indent=2)
def history_json_entries(history) -> list[dict]:
    """The OK history entries as objects for scripting — the same data
    history_to_json writes, so 'idm history --json' and --query can filter
    on exactly what the file export holds."""
    return [r for r in (history or []) if r.get("ok")]
def export_preview(text: str, max_rows: int = 3) -> str:
    """A written export's text -> a single preview line: the file's first
    max_rows rows (the markdown |---| separator doesn't count as a row),
    each truncated, joined with spaces; the whole line capped at ~160 chars.
    max_rows: 1+ rows to include; 0 yields '(suppressed)'; negative raises
    ValueError (the CLI turns it into a clean error for --rows)."""
    if max_rows < 0:
        raise ValueError(f"rows must be >= 0, got {max_rows}")
    if max_rows == 0:
        return "(suppressed)"
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return "(empty)"
    if lines[0].lstrip().startswith("|") and len(lines) > 1:
        lines = [lines[0]] + lines[2:]   # drop the |---| separator row
    parts = [ln if len(ln) <= 60 else ln[:59] + "…" for ln in lines[:max_rows]]
    out = " ".join(parts)
    # the cap grows with explicitly requested rows so larger previews aren't
    # truncated back to ~3 rows' worth of content
    cap = max(160, max_rows * 55)
    if len(out) > cap:
        out = out[:cap - 1] + "…"
    return out or "(empty)"
def export_preview_json(json_text: str, max_rows: int = 3) -> str:
    """    A written JSON export's text -> a single preview line (first `max_rows`
    entries, summarized as provider:path). Empty arrays yield '(empty)'.
    Same contract as export_preview: 0 -> '(suppressed)', negative -> ValueError."""
    import json as _json
    if max_rows < 0:
        raise ValueError(f"rows must be >= 0, got {max_rows}")
    if max_rows == 0:
        return "(suppressed)"
    if not (json_text or "").strip():
        return "(empty)"
    try:
        data = _json.loads(json_text)
    except ValueError:
        return "(unparseable)"
    if not data:
        return "(empty)"
    parts = [f"{r.get('provider', '?')}:{Path(str(r.get('path', '?'))).name}"
             for r in data[:max_rows] if isinstance(r, dict)]
    out = " ".join(parts)
    cap = max(160, max_rows * 40)
    return out if len(out) <= cap else out[:cap - 1] + "…"
def downloads_total_bytes(records) -> int:
    """Sum of numeric `size` fields across download-state records (bools are
    not numbers; missing sizes contribute 0)."""
    return sum(r["size"] for r in (records or {}).values()
               if isinstance(r.get("size"), (int, float))
               and not isinstance(r.get("size"), bool))
def download_status_counts(records) -> dict[str, int]:
    """{status: count} over download-state records, alphabetically sorted so
    the text output is deterministic."""
    counts: dict[str, int] = {}
    for r in (records or {}).values():
        s = str(r.get("status") or "unknown")
        counts[s] = counts.get(s, 0) + 1
    return dict(sorted(counts.items()))
def downloads_status(records) -> str:
    """One-line summary for download-state records: count, bytes, per-status
    breakdown, oldest entry's age (from `updated` stamps). Segments with no
    data are omitted; empty stores yield '0 pending' (the state file only
    holds unfinished downloads)."""
    records = records or {}
    if not records:
        return "0 pending"
    parts = [f"{len(records)} pending"]
    if downloads_total_bytes(records):
        parts.append(human_size(downloads_total_bytes(records)))
    parts.extend(f"{s} {n}" for s, n in download_status_counts(records).items())
    stamps = [r["updated"] for r in records.values()
              if isinstance(r.get("updated"), (int, float))]
    if stamps:
        parts.append(f"oldest from {format_age(time.time() - min(stamps))} ago")
    return " — ".join(parts)
def stats_payload(history, records) -> dict:
    """Both stores -> a lossless JSON-able stats dict (all numbers real).
 Down/Subs sections use the same pure helpers as the text views, so the
 JSON summary can never drift from what the text reports. Records include
 the full path so callers can say which state file was summarized."""
    history = history or []
    records = records or {}
    counts = download_status_counts(records)
    ok = [r for r in history if r.get("ok")]
    stamps = [r["updated"] for r in records.values()
              if isinstance(r.get("updated"), (int, float))]
    oldest_rec = (min(records.items(), key=lambda kv: kv[1].get("updated") or 0)
                  if stamps else None)
    ok_stamps = [r["ts"] for r in ok if isinstance(r.get("ts"), (int, float))]
    oldest_h = (min(ok, key=lambda r: r["ts"])
                if ok_stamps else None)
    return {
        "downloads": {
            "path": None,  # set by the caller (cmd_stats) to the state path
            "pending": len(records),
            "total_bytes": downloads_total_bytes(records),
            "by_status": counts,
            "oldest": ({
                "url": oldest_rec[0],
                "filename": oldest_rec[1].get("filename"),
                "age_seconds": int(time.time() - oldest_rec[1]["updated"]),
            } if oldest_rec else None),
        },
        "subtitles": {
            "path": None,  # set by the caller (cmd_stats) to the history path
            "entries": len(history),
            "ok": len(ok),
            "failed": len(history) - len(ok),
            "total_bytes": history_total_bytes(history),
            "by_provider": dict(provider_counts(history)),
            "weak": len([r for r in ok if is_weak_subtitle(r)]),
            "oldest": ({
                "video": Path(oldest_h.get("path", "")).name,
                "age_seconds": int(time.time() - oldest_h["ts"]),
            } if oldest_h else None),
        },
    }
def batch_payload(results, skipped=None) -> dict:
    """DownloadTask results (from 'idm get' / 'idm batch') -> a lossless,
    JSON-able summary dict. Reads the exact fields the text summary renders
    (status, filename, human size, note), so the JSON can never drift from
    what the table shows; byte sizes stay real numbers for scripts.
    `skipped` lists [(url, filename)] ghost records 'idm resume' declined
    to retry (file missing on disk) — reported, never attempted."""
    results = list(results or [])
    downloads = [{
        "url": t.url,
        "filename": t.filename or "",
        "status": t.status,
        "total_bytes": t.downloaded,
        "message": (t.message if t.status not in ("done", "skipped") else ""),
        # non-fatal warnings ride `note` (e.g. a kind mismatch on a done row)
        "note": getattr(t, "note", "") or "",
        "dest": str(t.dest) if t.dest else "",
    } for t in results]
    counts: dict[str, int] = {}
    for t in results:
        counts[t.status] = counts.get(t.status, 0) + 1
    skipped = [(str(u), str(f or "")) for u, f in (skipped or [])]
    downloads = ([{
        "url": u, "filename": f, "status": "skipped-ghost", "total_bytes": 0,
        "message": "file no longer exists — record kept; clean it up with "
                   "'idm prune-state'", "note": "", "dest": "",
    } for u, f in skipped] + downloads)
    return {
        "count": len(downloads),
        "ok": sum(1 for t in results if t.status in ("done", "skipped")),
        "all_ok": all(t.status in ("done", "skipped") for t in results),
        "total_bytes": sum(t.downloaded for t in results),
        "by_status": counts,
        "skipped_count": len(skipped),
        "skipped": [{"url": u, "filename": f} for u, f in skipped],
        "downloads": downloads,
    }
def download_json_records(records) -> list[dict]:
    """Download-state records ({url: rec}) -> a JSON-able list with the url
    plus every state field preserved, for 'idm downloads --json/--query'."""
    return [dict(rec, url=str(url)) for url, rec in (records or {}).items()]
def download_row(url: str, rec: dict) -> tuple[str, str, str, str, str]:
    """One download-state record -> (url, filename, status, size, updated).
    Size uses human units; `updated` renders as local 'YYYY-MM-DD HH:MM';
    missing fields become empty cells."""
    size = rec.get("size")
    size_s = (human_size(size)
              if isinstance(size, (int, float)) and not isinstance(size, bool) else "")
    ts = rec.get("updated")
    ts_s = ""
    if isinstance(ts, (int, float)) and not isinstance(ts, bool):
        from datetime import datetime as _dt
        ts_s = _dt.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    return (str(url), str(rec.get("filename") or ""),
            str(rec.get("status") or "?"), size_s, ts_s)
def download_rows(records) -> list[tuple[str, str, str, str, str]]:
    """Download-state records ({url: rec}) -> display rows, newest-updated
    first; records without a timestamp sort last."""
    items = sorted((records or {}).items(),
                   key=lambda kv: _num_field(kv[1], "updated") or 0.0, reverse=True)
    return [download_row(url, rec) for url, rec in items]
def download_to_csv(rows: list[tuple[str, str, str, str, str]]) -> str:
    """Download-state rows -> CSV text (same rules as the history export)."""
    return _table_to_csv(rows, _DOWNLOAD_HEADER)
def download_to_markdown(rows: list[tuple[str, str, str, str, str]]) -> str:
    """Download-state rows -> a GitHub-flavored markdown table."""
    return _table_to_markdown(rows, _DOWNLOAD_HEADER)
def filter_downloads(records, status: str = "", since="", until="") -> dict:
    """Keep download-state records whose status matches exactly
    (case-insensitive) and whose `updated` timestamp lies in [since, until].
    Empty bounds mean no restriction; records without `updated` are dropped
    only when a date bound is active (mirrors filter_history)."""
    st = (status or "").strip().lower()
    t0 = parse_date_input(since)
    t1 = parse_date_input(until, end=True)
    out = {}
    for url, rec in (records or {}).items():
        if st and str(rec.get("status") or "").lower() != st:
            continue
        ts = rec.get("updated")
        if (t0 is not None or t1 is not None) and not isinstance(ts, (int, float)):
            continue
        if t0 is not None and ts < t0:
            continue
        if t1 is not None and ts > t1:
            continue
        out[url] = rec
    return out
def sort_key_from_label(label: str) -> str:
    """Combobox label -> sort key ('newest' for anything unknown)."""
    return SUBS_SORT_LABELS.get(label, "newest")
def _num_field(r: dict, field: str):
    v = r.get(field)
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None
def sort_subs_results(results, key: str = "newest") -> list[dict]:
    """OK subtitle results sorted for the history table (stable).
    key: 'newest' keeps the given chronological order; 'cues_asc'/'cues_desc'
    and 'size_asc'/'size_desc' sort by the numeric field. Rows without the
    field go last regardless of direction. Failures are never listed."""
    rows = [r for r in (results or []) if r.get("ok")]
    field = {"cues_asc": "cues", "cues_desc": "cues",
             "size_asc": "size", "size_desc": "size"}.get(key)
    if field is None:
        return rows
    reverse = key.endswith("desc")
    present = [(v, r) for r in rows if (v := _num_field(r, field)) is not None]
    missing = [r for r in rows if _num_field(r, field) is None]
    present.sort(key=lambda t: t[0], reverse=reverse)  # stable: ties keep insertion order
    return [r for _, r in present] + missing
def is_weak_subtitle(r: dict) -> bool:
    """True when numeric size/cues exist AND fall below the review thresholds.
    Unknown quality (no numeric fields) is never flagged."""
    cues = _num_field(r, "cues")
    size = _num_field(r, "size")
    if cues is None and size is None:
        return False
    if cues is not None and cues < WEAK_CUES:
        return True
    return size is not None and size < WEAK_SIZE
def weak_subtitle_indices(results) -> set[int]:
    """Positions (into the OK-row list, i.e. subtitle_rows order) that are weak."""
    return {i for i, r in enumerate(r for r in (results or []) if r.get("ok"))
            if is_weak_subtitle(r)}
def missing_subtitle_indices(results) -> set[int]:
    """Positions (into the OK-row list) whose .srt no longer exists on disk.
    Only rows with a recorded dest can be checked; the rest stay unflagged
    (unknown, not missing)."""
    out = set()
    for i, r in enumerate(r for r in (results or []) if r.get("ok")):
        dest = r.get("dest")
        if dest and not Path(dest).is_file():
            out.add(i)
    return out
def find_relocated_file(dest, folder) -> str | None:
    """Guess where a missing .srt went inside folder: an exact-name match
    wins; otherwise a unique filename-stem match (e.g. 'A.en.srt' found as
    'A.en.srt' or renamed 'A.en.ass' family — same stem). Returns None when
    folder is missing, unreadable, or the stem match is ambiguous."""
    d, f = Path(dest), Path(folder)
    if not f.is_dir():
        return None
    exact = f / d.name
    if exact.is_file():
        return str(exact)
    try:
        matches = sorted(p for p in f.iterdir()
                         if p.is_file() and p.stem == d.stem)
    except OSError:
        return None
    return str(matches[0]) if len(matches) == 1 else None
def format_age(seconds: float) -> str:
    """Seconds -> coarse human age: 'just now', '5m', '3h', '12d'."""
    s = int(max(0, seconds))
    if s < 60:
        return "just now"
    m = s // 60
    if m < 60:
        return f"{m}m"
    h = s // 3600
    if h < 48:
        return f"{h}h"
    return f"{s // 86400}d"
def history_total_bytes(history) -> int:
    """Sum of numeric file sizes across OK rows (bools are not numbers)."""
    return sum(r["size"] for r in (history or []) if r.get("ok")
               and isinstance(r.get("size"), (int, float))
               and not isinstance(r.get("size"), bool))
def provider_counts(history) -> list[tuple[str, int]]:
    """(provider, count) across OK rows with a provider, most-used first,
    ties alphabetical — deterministic for the status line."""
    counts: dict[str, int] = {}
    for r in (history or []):
        if not r.get("ok"):
            continue
        p = r.get("provider")
        if isinstance(p, str) and p:
            counts[p] = counts.get(p, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
def parse_date_input(s: str, end: bool = False) -> float | None:
    """Parse 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM' (local time) to a unix ts.
    Bare dates bound whole days: start-of-day, or end-of-day when end=True.
    Returns None for empty/whitespace (meaning: no bound). Raises ValueError
    on unparseable text so callers can surface it."""
    s = (s or "").strip()
    if not s:
        return None
    from datetime import datetime as _dt
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            d = _dt.strptime(s, fmt)
        except ValueError:
            continue
        if end and fmt == "%Y-%m-%d":
            d = d.replace(hour=23, minute=59, second=59)
        return d.timestamp()
    raise ValueError(f"unrecognized date {s!r} (use YYYY-MM-DD or YYYY-MM-DD HH:MM)")
def filter_history(history, provider: str = "", since="", until="") -> list[dict]:
    """Keep OK entries matching an exact provider (case-insensitive) and
    stamped within [since, until]. Empty provider/since/until means no bound;
    rows without a timestamp are dropped only when a date bound is active."""
    p = (provider or "").strip().lower()
    t0 = parse_date_input(since)
    t1 = parse_date_input(until, end=True)
    out = []
    for r in (history or []):
        if not r.get("ok"):
            continue
        if p and str(r.get("provider") or "").lower() != p:
            continue
        ts = r.get("ts")
        if (t0 is not None or t1 is not None) and not isinstance(ts, (int, float)):
            continue
        if t0 is not None and ts < t0:
            continue
        if t1 is not None and ts > t1:
            continue
        out.append(r)
    return out
def history_providers(history) -> list[str]:
    """Provider names for the filter dropdown (alphabetical, deduplicated)."""
    return sorted({p for p, _ in provider_counts(history)})
def parse_export_filters(provider: str, since: str, until: str) -> tuple[str, str, str]:
    """Normalize raw filter-dialog input; raises ValueError with a friendly
    message on a bad date. Used by both the GUI dialog and its tests."""
    if (since or "").strip() and (until or "").strip():
        s, u = parse_date_input(since), parse_date_input(until, end=True)
        if s is not None and u is not None and s > u:
            raise ValueError("'since' is after 'until'")
    return (provider or "").strip(), (since or "").strip(), (until or "").strip()
# ------------------------------------------------------- verify ignore-list
def name_is_ignored(name: str, ignore=None) -> bool:
    """True when the verify scan should not warn about ``name``: an ignore
    entry ending in '.*' matches by extension ('*.m3u' — case-insensitive),
    one ending in '*' matches by prefix, anything else is an exact
    (case-insensitive) match. Missing/empty ignore lists ignore nothing."""
    for pattern in (ignore or []):
        entry = str(pattern).strip()
        if not entry:
            continue
        low = name.lower()
        if entry.startswith("*.") and len(entry) > 2:
            if low.endswith(entry[1:].lower()):        # '*.webm' -> '.webm'
                return True
        elif entry.endswith("*"):
            if low.startswith(entry[:-1].lower()):     # '130425*' -> '130425'
                return True
        elif entry.lower() == low:
            return True
    return False


def ignore_add_hints(out_dir, entries, ignore=None):
    """Feedback for 'idm ignore add': (matched, hints). matched holds the
    entries that match at least one file in out_dir right now — the rest
    match nothing on disk and silence nothing, which is usually a typo
    (the CLI warns so it can be removed again; --json stays clean).
    hints pairs a '*.ext' suggestion with the flagged files of that
    extension it would cover: when several verify-flagged files share an
    extension, one extension pattern beats adding their names one by
    one. A hint only fires when it covers something the entries do not
    already name (else it is noise repeating what was just added).
    Read-only — one scan of out_dir, nothing written."""
    entries = list(dict.fromkeys(str(e).strip() for e in (entries or [])
                                 if str(e).strip()))
    if not entries:
        return [], []
    try:
        scan = scan_for_quarantine(out_dir, ignore=ignore)
    except OSError:
        return [], []
    matched = [e for e in entries
               if any(name_is_ignored(f["file"], [e]) for f in scan["files"])]
    flagged = [f["file"] for f in scan["files"] if f["kind"] == "warned"]
    by_ext: dict[str, list[str]] = {}
    for name in flagged:
        by_ext.setdefault(Path(name).suffix.lower(), []).append(name)
    added = {e.lower() for e in entries}
    hints, seen = [], set()
    for entry in entries:
        ext = Path(entry).suffix.lower()
        pattern = f"*{ext}"
        if not ext or pattern.lower() in added or pattern in seen:
            continue
        others = by_ext.get(ext, [])
        added_names = {e.lower() for e in entries if not e.startswith("*")}
        if any(n.lower() not in added_names for n in others):
            seen.add(pattern)
            hints.append((pattern, others))
    return matched, hints


# --------------------------------------------------------------- quarantine
def scan_for_quarantine(out_dir, ignore=None):
    """The files 'idm verify --delete-warned' would move, in the same
    JSON shape the CLI reports ("scanned"/"ok"/"warned"/... counts plus
    per-file "file"/"size"/"kind"/"note" rows). Shared by 'idm verify'
    and the GUI's right-click actions so the verdicts can never differ."""
    from .core import expected_kind_for_name, scan_file_for_name

    files: list[dict] = []
    if Path(out_dir).is_dir():
        for path in sorted(Path(out_dir).iterdir()):
            if not path.is_file() or path.name == "idm.state.json":
                continue
            try:
                size = path.stat().st_size
                with open(path, "rb") as probe:
                    probe.read(1)
                readable = True
            except OSError:
                size, readable = 0, False
            if not readable:
                kind, note = "unreadable", ""
            elif name_is_ignored(path.name, ignore):
                # on the config-driven ignore list: inspected and decided
                # fine — never warned, never swept by --delete-warned
                kind, note = "ignored", ""
            else:
                note = scan_file_for_name(path)
                kind = "ok" if not note else "warned"
                if expected_kind_for_name(path.name) is None:
                    kind, note = "skipped", ""
            files.append({"file": path.name, "size": size,
                          "kind": kind, "note": note})
    counts = {k: sum(1 for f in files if f["kind"] == k)
              for k in ("ok", "warned", "unreadable", "skipped", "ignored")}
    return {"dir": str(out_dir), "scanned": len(files), "ok": counts["ok"],
            "warned": counts["warned"], "unreadable": counts["unreadable"],
            "skipped": counts["skipped"], "ignored": counts["ignored"],
            "files": files}


def move_warned_to_quarantine(out_dir, scan=None, ignore=None):
    """Move the scan's warned files into <out_dir>/quarantine (the
    '--delete-warned' half). Returns (payload, moves): the scan payload
    with kind/kind-counts updated to the final state (moved files become
    kind "moved"), and the [(file, quarantine_path)] moves for logging.
    Files that fail to move (locked, vanished) stay kind "warned" and
    still fail the run. A quarantine folder is created only when there is
    something to move."""
    scan = scan or scan_for_quarantine(out_dir, ignore=ignore)
    out_dir, quarantine = Path(out_dir), Path(out_dir) / "quarantine"
    moves: list[tuple[str, str]] = []
    if not any(f["kind"] == "warned" for f in scan["files"]):
        return scan, moves
    # 'ignored' rows may come from a payload built before the ignore-list
    # existed; normalize the count key so every caller sees it
    scan.setdefault("ignored", sum(1 for f in scan["files"]
                                   if f["kind"] == "ignored"))
    quarantine.mkdir(parents=True, exist_ok=True)
    for f in (f for f in scan["files"] if f["kind"] == "warned"):
        src = out_dir / f["file"]
        target = quarantine / f["file"]
        if target.exists():
            stem, suffix = Path(f["file"]).stem, Path(f["file"]).suffix
            n = 2
            while (quarantine / f"{stem} ({n}){suffix}").exists():
                n += 1
            target = quarantine / f"{stem} ({n}){suffix}"
        try:
            src.replace(target)
        except OSError:
            continue
        f["kind"] = "moved"
        f["quarantine"] = str(target)
        moves.append((f["file"], str(target)))
    for k in ("ok", "warned", "unreadable", "skipped", "ignored"):
        scan[k] = sum(1 for f in scan["files"] if f["kind"] == k)
    scan["moved"] = sum(1 for f in scan["files"] if f["kind"] == "moved")
    scan["quarantine"] = str(quarantine)
    return scan, moves


def restore_from_quarantine(out_dir, names=None, discard=False):
    """Move files back out of <out_dir>/quarantine (or delete them for
    good with discard=True). The engine behind 'idm restore' and the
    GUI's Restore action — all files by default, or just the named ones;
    never overwrites (a colliding name gets a ' (2)' suffix)."""
    out_dir, quarantine = Path(out_dir), Path(out_dir) / "quarantine"
    had_quarantine = quarantine.is_dir()
    available = ([p.name for p in sorted(quarantine.iterdir()) if p.is_file()]
                 if had_quarantine else [])
    names = list(dict.fromkeys(names or []))
    missing = [n for n in names if n not in available]
    targets = [n for n in names if n in available] if names else available

    files: list[dict] = []
    for name in targets:
        src = quarantine / name
        if discard:
            try:
                src.unlink()
            except OSError as e:
                files.append({"file": name, "action": "failed", "error": str(e)})
                continue
            files.append({"file": name, "action": "discarded"})
            continue
        dest = out_dir / name
        if dest.exists():
            stem, suffix = Path(name).stem, Path(name).suffix
            n = 2
            while (out_dir / f"{stem} ({n}){suffix}").exists():
                n += 1
            dest = out_dir / f"{stem} ({n}){suffix}"
        try:
            src.replace(dest)
        except OSError as e:
            files.append({"file": name, "action": "failed", "error": str(e)})
            continue
        files.append({"file": name, "action": "restored", "dest": str(dest)})
    for name in missing:
        files.append({"file": name, "action": "failed",
                      "error": "not in the quarantine folder"})

    counts = {k: sum(1 for f in files if f["action"] == k)
              for k in ("restored", "discarded", "failed")}
    payload = {"dir": str(out_dir), "quarantine": str(quarantine),
               "restored": counts["restored"], "discarded": counts["discarded"],
               "failed": counts["failed"], "files": files}

    # tidy: a quarantine folder we just emptied has no reason to stay
    # (an empty folder we merely found is left alone)
    try:
        if (had_quarantine and (counts["restored"] or counts["discarded"])
                and not any(quarantine.iterdir()) and not counts["failed"]):
            quarantine.rmdir()
    except OSError:
        pass
    return payload


def scan_unclean_names(out_dir):
    """The 'idm sanitize-names' dry run: files in the Save-to folder whose
    names a mangled Content-Disposition produced, with the clean name the
    same _unmangle_cd_name rule _pick_filename applies to new downloads
    would give them. Payload matches the CLI's --json exactly: "dir"/
    "scanned"/"unclean"/"renamed"/"failed" counts plus per-file rows
    {"file", "to", "action": "would-rename"}. Shared by 'idm
    sanitize-names' and the GUI's Tools menu so the verdicts can never
    differ."""
    from .core import _unmangle_cd_name

    files: list[dict] = []
    scanned = 0
    if Path(out_dir).is_dir():
        for path in sorted(Path(out_dir).iterdir()):
            if not path.is_file() or path.name == "idm.state.json":
                continue
            scanned += 1
            target_name = _unmangle_cd_name(path.name)
            if target_name == path.name:
                continue
            files.append({"file": path.name, "to": target_name,
                          "action": "would-rename"})
    return {"dir": str(out_dir), "scanned": scanned,
            "unclean": len(files), "renamed": 0, "failed": 0,
            "files": files}


def apply_name_fixes(out_dir, scan=None):
    """The '--apply' half: rename the scan's would-rename rows in place,
    never overwriting (a colliding name gets a ' (2)' suffix). Returns
    (payload, renames): the payload with rows updated to their final
    action ("renamed"/"failed") and the [(from, to)] renames for
    logging. Files that fail to rename (locked, vanished) stay kind
    "failed" with an "error" note. Rows whose action is not
    "would-rename" (e.g. a failed row from an earlier pass) are left
    alone."""
    scan = scan or scan_unclean_names(out_dir)
    out_dir = Path(out_dir)
    renames: list[tuple[str, str]] = []
    for row in (r for r in scan["files"] if r["action"] == "would-rename"):
        src = out_dir / row["file"]
        target = out_dir / row["to"]
        if target.exists():
            stem, suffix = Path(row["to"]).stem, Path(row["to"]).suffix
            n = 2
            while (out_dir / f"{stem} ({n}){suffix}").exists():
                n += 1
            target = out_dir / f"{stem} ({n}){suffix}"
            row["to"] = target.name
        try:
            src.rename(target)
        except OSError as e:
            row["action"] = "failed"
            row["error"] = str(e)
            continue
        row["action"] = "renamed"
        renames.append((row["file"], target.name))
    counts = {k: sum(1 for r in scan["files"] if r["action"] == k)
              for k in ("would-rename", "renamed", "failed")}
    scan["renamed"] = counts["renamed"]
    scan["failed"] = counts["failed"]
    return scan, renames


def fix_one_name(out_dir, name):
    """Rename one file in the Save-to folder to the clean name
    _unmangle_cd_name would give a fresh download — the per-file half of
    apply_name_fixes behind the GUI's right-click 'Fix this file name'
    action. Returns (renamed, from, to, error): renamed is False on a
    no-op (name already clean, file missing, rename failed) and error
    says why ("" on the clean-name no-op). Never overwrites: a colliding
    target gets a ' (2)' suffix, exactly like 'idm sanitize-names
    --apply'."""
    from .core import _unmangle_cd_name

    out_dir, name = Path(out_dir), str(name)
    src = out_dir / name
    target_name = _unmangle_cd_name(name)
    if not src.is_file():
        return (False, name, "", "file is gone")
    if target_name == name:
        return (False, name, "", "")
    target = out_dir / target_name
    if target.exists():
        stem, suffix = Path(target_name).stem, Path(target_name).suffix
        n = 2
        while (out_dir / f"{stem} ({n}){suffix}").exists():
            n += 1
        target = out_dir / f"{stem} ({n}){suffix}"
    try:
        src.rename(target)
    except OSError as e:
        return (False, name, target.name, str(e))
    return (True, name, target.name, "")


def history_status(history) -> str:
    """One-line summary for the history panel: row count, total bytes,
    provider breakdown, and the oldest entry's age (from the 'ts' stamps).
    Segments with no data are omitted; rows without a stamp are ignored
    for the age but still counted."""
    rows = [r for r in (history or []) if r.get("ok")]
    if not rows:
        return "0 entries"
    parts = [f"{len(rows)} entries"]
    if history_total_bytes(rows):
        parts.append(human_size(history_total_bytes(rows)))
    provs = provider_counts(rows)
    if provs:
        parts.append(", ".join(f"{name} {n}" for name, n in provs))
    stamps = [r["ts"] for r in rows if isinstance(r.get("ts"), (int, float))]
    if stamps:
        parts.append(f"oldest from {format_age(time.time() - min(stamps))} ago")
    return " — ".join(parts)
