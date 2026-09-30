from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def human_size(n) -> str:
    """Format a byte count for humans, e.g. 12.3 MB. Returns '?' for None."""
    if n is None:
        return "?"
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    step = 1000.0
    v = float(n)
    u = units[0]
    for nxt in units[1:]:
        if abs(v) < step:
            break
        v /= step
        u = nxt
    if u == "B":
        return f"{int(v)} B"
    return f"{v:,.1f} {u}"


def sanitize_filename(name: str) -> str:
    """Make a string safe as a filename on Windows/macOS/Linux."""
    name = _ILLEGAL.sub("_", name).strip(" .")
    if len(name) > 180:
        stem, suffix = os.path.splitext(name)
        name = stem[: max(1, 180 - len(suffix))] + suffix
    return name or "download.bin"


def parse_job_lines(text: str) -> list[tuple[str, str | None]]:
    """Parse a batch list: one URL per line, '#' comments, optional 'url -> name'."""
    jobs: list[tuple[str, str | None]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if " -> " in line:
            url, name = line.split(" -> ", 1)
            jobs.append((url.strip(), name.strip() or None))
        else:
            jobs.append((line, None))
    return jobs


def read_url_file(path: str | Path) -> list[tuple[str, str | None]]:
    return parse_job_lines(Path(path).read_text(encoding="utf-8", errors="replace"))


def write_text_newlines(path: str | Path, text: str) -> None:
    """UTF-8 text write with newline="" semantics (byte-exact line endings).

    Path.write_text only gained its newline parameter in Python 3.10; open()'s
    has existed from the start, so this keeps CSV exports' \\r\\n endings
    byte-exact on the 3.9 floor too.
    """
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def clipboard_text() -> str:
    """Best-effort clipboard read (PowerShell on Windows, tkinter fallback)."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0 and out.stdout:
            return out.stdout
    except Exception:
        pass
    try:
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        text = root.clipboard_get()
        root.destroy()
        return text
    except Exception:
        return ""
