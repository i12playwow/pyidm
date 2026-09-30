from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path


class State:
    """Persistent, thread-safe download state stored as JSON next to the files."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.data: dict[str, dict] = {}
        self.load()

    def load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as f:
                raw = json.load(f)
            self.data = raw.get("downloads", {}) if isinstance(raw, dict) else {}
        except (OSError, ValueError):
            self.data = {}

    def _write_locked(self, compact: bool = False) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            if compact:
                json.dump(
                    {"version": 1, "updated": time.time(), "downloads": self.data},
                    f, separators=(",", ":"),
                )
            else:
                json.dump(
                    {"version": 1, "updated": time.time(), "downloads": self.data},
                    f, indent=2,
                )
        os.replace(tmp, self.path)

    def get(self, url: str) -> dict:
        with self._lock:
            return dict(self.data.get(url, {}))

    def set(self, url: str, **fields) -> None:
        with self._lock:
            rec = self.data.get(url, {})
            rec.update(fields)
            rec["updated"] = time.time()
            self.data[url] = rec
            self._write_locked()

    def remove(self, url: str) -> None:
        with self._lock:
            if self.data.pop(url, None) is not None:
                self._write_locked()

    def records(self) -> dict[str, dict]:
        with self._lock:
            return json.loads(json.dumps(self.data))


# -------------------------------------------------------------- stale prune
def scan_stale_records(state_or_path) -> dict:
    """Records whose download file no longer exists on disk, in the same
    JSON shape every other PyIDM scan reports: counts (scanned/stale/live)
    plus per-record rows {url, filename, status, size, updated, removed}.
    A record without a usable filename is never stale (nothing to check),
    so hand-written or crashed-early records survive — and neither is a
    record with partial-download evidence on disk ('.part' / '.part0'),
    because that download can still resume. Read-only."""
    state = state_or_path if isinstance(state_or_path, State) else State(state_or_path)
    with state._lock:
        records = json.loads(json.dumps(state.data))
        out_dir = Path(state.path).parent
    rows: list[dict] = []
    for url, rec in records.items():
        filename = str(rec.get("filename") or "")
        if not filename:
            continue
        if ((out_dir / filename).exists()
                or (out_dir / (filename + ".part")).exists()
                or (out_dir / (filename + ".part0")).exists()):
            continue
        size = rec.get("size")
        updated = rec.get("updated")
        rows.append({
            "url": url, "filename": filename,
            "status": str(rec.get("status") or ""),
            "size": size if isinstance(size, (int, float)) else None,
            "updated": updated if isinstance(updated, (int, float)) else None,
            "removed": False,
        })
    rows.sort(key=lambda r: str(r["filename"] or r["url"]))
    return {"action": "scan", "dir": str(out_dir), "scanned": len(records),
            "stale": len(rows), "live": len(records) - len(rows),
            "records": rows}


def prune_stale_records(state_or_path, scan: dict | None = None,
                        compact: bool = False) -> tuple[dict, int]:
    """Apply a stale-record prune: remove every previewed record whose
    file is STILL missing (re-checked now, so one that reappeared between
    preview and apply keeps its record), write the store atomically, and
    return (final payload, number of records removed). Files are never
    touched. Pass a scan_stale_records() payload to apply a previously
    shown preview; without one a fresh scan is performed. With compact
    ('--vacuum') the store is rewritten without indentation — bytes are
    reclaimed from compaction alone even when nothing was stale (clamped
    at 0 — the fresh 'updated' timestamp jitters the size by a byte or
    two); the payload reports state_bytes_before/after and
    bytes_reclaimed. A missing store is never created: its byte fields
    stay null."""
    state = state_or_path if isinstance(state_or_path, State) else State(state_or_path)
    if scan is None:
        scan = scan_stale_records(state)
    rows = [dict(r) for r in scan.get("records", [])]
    out_dir = Path(state.path).parent
    removed = 0
    bytes_before = bytes_after = None
    with state._lock:
        before = len(state.data)
        store_existed = state.path.exists()
        if compact and store_existed:
            try:
                bytes_before = state.path.stat().st_size
            except OSError:
                bytes_before = None
        for r in rows:
            filename = str(r.get("filename") or "")
            if filename and (out_dir / filename).exists():
                r["removed"] = False        # file is back: keep the record
                continue
            existed = state.data.pop(r["url"], None) is not None
            r["removed"] = existed
            removed += 1 if existed else 0
        # write only when something actually changed (or a compaction was
        # asked for and there is a store to compact) — a prune of a missing
        # store never creates one
        if (compact and store_existed) or removed:
            state._write_locked(compact=compact)
        if compact and store_existed:
            try:
                bytes_after = state.path.stat().st_size
            except OSError:
                bytes_after = None
    # bytes_reclaimed clamps at 0: the fresh 'updated' timestamp can vary a
    # byte or two between writes, so re-vacuuming an already-compact store
    # may measure a 1-byte GROWTH — that is jitter, not a reclaim.
    reclaimed = None
    if bytes_before is not None and bytes_after is not None:
        reclaimed = max(0, bytes_before - bytes_after)
    payload = {"action": "apply", "dir": str(out_dir), "scanned": before,
               "stale": removed, "live": before - removed, "records": rows,
               "compacted": bool(compact),
               "state_bytes_before": bytes_before,
               "state_bytes_after": bytes_after,
               "bytes_reclaimed": reclaimed}
    return payload, removed
