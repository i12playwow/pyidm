"""PyIDM desktop GUI (Tkinter)."""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter.scrolledtext import ScrolledText
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Callable

from . import __version__, jq
from .config import (
    APP_DIR,
    effective_config_with_sources,  # noqa: F401  (_g surface)
    get_config,
    get_user_env,
    mask_secret,  # noqa: F401  (_g surface)
    remove_config_value,
    set_config_value,
    set_user_env,
    verify_ignore_add,
    verify_ignore_list,
    verify_ignore_remove,
)
from .core import Downloader
from .gui_common import (
    _DOWNLOAD_HEADER,
    _HISTORY_HEADER,
    STATUS_MARKS,
    SUBS_SORT_LABELS,
    WEAK_CUES,
    WEAK_SIZE,
    _num_field,
    _table_to_csv,
    _table_to_markdown,
    # patch/read these through idm.gui)
    apply_name_fixes,
    batch_payload,
    download_json_records,
    download_row,
    download_rows,
    download_status_counts,
    download_to_csv,
    download_to_markdown,
    downloads_status,
    downloads_total_bytes,
    export_preview,
    export_preview_json,
    filter_downloads,
    filter_history,
    find_relocated_file,
    fix_one_name,
    format_age,
    history_json_entries,
    history_providers,
    history_status,
    history_to_csv,
    history_to_json,
    history_to_markdown,
    history_total_bytes,
    is_weak_subtitle,
    missing_subtitle_indices,
    move_warned_to_quarantine,
    name_is_ignored,
    parse_date_input,
    parse_export_filters,
    provider_counts,
    provider_hints,
    provider_rows,
    restore_from_quarantine,
    scan_for_quarantine,
    scan_unclean_names,
    sort_key_from_label,
    sort_subs_results,
    stats_payload,
    subtitle_row,
    subtitle_rows,
    weak_subtitle_indices,
)
from .health import health_payload, run_checks
from .players import launch_with_subtitle
from .state import State, prune_stale_records, scan_stale_records
from .subtitles import batch_for_folder
from .utils import clipboard_text, human_size, parse_job_lines, write_text_newlines

__all__ = [
    "STATUS_MARKS",
    "SUBS_SORT_LABELS",
    "WEAK_CUES",
    "WEAK_SIZE",
    "_DOWNLOAD_HEADER",
    "_HISTORY_HEADER",
    "__version__",
    "_num_field",
    "_table_to_csv",
    "_table_to_markdown",
    "apply_name_fixes",
    "batch_for_folder",
    "batch_payload",
    "download_json_records",
    "download_row",
    "download_rows",
    "download_status_counts",
    "download_to_csv",
    "download_to_markdown",
    "downloads_status",
    "downloads_total_bytes",
    "export_match_count",
    "export_preview",
    "export_preview_json",
    "filter_downloads",
    "filter_history",
    "find_relocated_file",
    "fix_one_name",
    "format_age",
    "health_payload",
    "history_json_entries",
    "history_providers",
    "history_status",
    "history_to_csv",
    "history_to_json",
    "history_to_markdown",
    "history_total_bytes",
    "is_weak_subtitle",
    "launch_with_subtitle",
    "missing_subtitle_indices",
    "move_warned_to_quarantine",
    "name_is_ignored",
    "parse_date_input",
    "parse_export_filters",
    "provider_counts",
    "provider_hints",
    "provider_rows",
    "remove_config_value",
    "restore_from_quarantine",
    "run_checks",
    "scan_for_quarantine",
    "scan_unclean_names",
    "set_config_value",
    "set_user_env",
    "sort_key_from_label",
    "sort_subs_results",
    "stats_payload",
    "subtitle_row",
    "subtitle_rows",
    "update_history_dest",
    "weak_subtitle_indices",
]
OS_API_KEYS_URL = "https://www.opensubtitles.com/en/profile/api-keys"

ENV_OVERRIDE_KEYS = {
    "out_dir": "IDM_OUT",
    "workers": "IDM_WORKERS",
    "opensubtitles_api_key": "OPENSUBTITLES_API_KEY",
}

def verify_opensubtitles_key(key: str) -> tuple[bool, str]:
    """Live-check an OpenSubtitles key against the API. Returns (ok, message).
    Rate-limit (429) counts as valid — the key authenticated successfully."""
    import requests

    if not key:
        return False, "empty key"
    try:
        r = requests.get(
            "https://api.opensubtitles.com/api/v1/subtitles",
            headers={"Api-Key": key, "User-Agent": "TemporaryUserAgent",
                     "Accept": "application/json"},
            params={"query": "sintel"}, timeout=10,
        )
    except requests.RequestException as e:
        return False, f"network error ({type(e).__name__})"
    if r.status_code == 200:
        return True, "key accepted"
    if r.status_code == 429:
        return True, "key accepted (rate limited right now)"
    if r.status_code == 401:
        return False, "key rejected (HTTP 401)"
    return False, f"unexpected HTTP {r.status_code}"


def edit_warnings(key: str, cfg: dict, sources: dict[str, str]) -> list[str]:
    """Layer-aware warnings for editing one config key from the About dialog."""
    warnings = []
    src = sources.get(key, "default")
    if key in ENV_OVERRIDE_KEYS:
        env_name = ENV_OVERRIDE_KEYS[key]
        if os.environ.get(env_name):
            warnings.append(
                f"Environment variable {env_name} is set and will override your edit "
                "(this session and any process that inherits it).")
        else:
            warnings.append(
                f"Tip: the environment variable {env_name} is currently unset, so your "
                "edit takes effect everywhere.")
    if key == "opensubtitles_api_key":
        warnings.append(
            "The key is also mirrored to the Windows user environment "
            "(OPENSUBTITLES_API_KEY) so the packaged exes see it.")
    if src == "local (idm.json)":
        warnings.append(
            "This value is currently set in ./idm.json — your edit goes to "
            "~/.idm/config.json and will NOT take effect while idm.json keeps "
            "defining the key (first match wins in the chain).")
    if src == "environment":
        warnings.append(
            "This value comes from the environment right now — the edit is saved "
            "to ~/.idm/config.json but stays overridden until the variable is removed "
            "(idm config delenv NAME).")
    if key == "verify_ignore":
        warnings.append(
            "A JSON array of patterns — e.g. [\"130425,_360p.mp4\", \"*.m3u\"] — a "
            "single filename/pattern, or several separated by spaces. Filenames "
            "may contain commas, so a single comma-heavy name is kept whole; "
            "'idm ignore add' is the precise way to add several entries.")
    return warnings


def should_show_first_run(cfg: dict) -> bool:
    """True only on a genuinely fresh setup: wizard never answered AND no key
    anywhere (config file, process env, or Windows user environment)."""
    return not (cfg.get("wizard_done")
                or cfg.get("opensubtitles_api_key")
                or os.environ.get("OPENSUBTITLES_API_KEY")
                or get_user_env("OPENSUBTITLES_API_KEY"))
STATUS_COLORS = {
    "done": "#1a7f37",
    "error": "#c62828",
    "cancelled": "#b26a00",
    "skipped": "#8a6d3b",
    "downloading": "#1565c0",
}


def _ask_choose_items(title: str, prompt: str, items: list[str],
                      preselect: list[str] | None = None):
    """Modal multi-select picker: returns the chosen items (all by default
    via ``preselect=None``, just ``preselect`` when given, or nothing when
    an empty list is passed) or None when cancelled. A module-level helper
    so tests can patch it."""
    dlg = tk.Toplevel()
    dlg.title(title)
    dlg.transient()
    dlg.grab_set()
    ttk.Label(dlg, text=prompt, wraplength=380, justify="left").pack(
        anchor="w", padx=12, pady=(12, 4))
    box = tk.Listbox(dlg, selectmode="extended", width=52, height=min(14, max(4, len(items))),
                     exportselection=False)
    for item in items:
        box.insert("end", item)
    if preselect:
        for i, item in enumerate(items):
            if item in preselect:
                box.selection_set(i)
    elif preselect is None:
        box.selection_set(0, "end")      # preselect everything
    # preselect=[] selects nothing on purpose (e.g. destructive dialogs)
    box.pack(padx=12, pady=4)
    chosen: list[str] | None = None

    def _ok():
        nonlocal chosen
        chosen = [box.get(i) for i in box.curselection()]
        dlg.destroy()

    btns = ttk.Frame(dlg)
    btns.pack(pady=(2, 12))
    ttk.Button(btns, text="OK", command=_ok).pack(side="left", padx=4)
    ttk.Button(btns, text="Cancel", command=dlg.destroy).pack(side="left", padx=4)
    dlg.wait_window()
    return chosen


def _ask_string(title: str, prompt: str, initial: str = ""):
    """Modal single-line input (tkinter.simpledialog.askstring); returns
    the trimmed string or None when cancelled. A module-level helper so
    tests can patch it."""
    answer = simpledialog.askstring(title, prompt, initialvalue=initial)
    return answer.strip() if answer else None


def open_file(path) -> None:
    """Open a file with its default application (Windows-first, best-effort)."""
    p = str(path)
    if os.name == "nt":
        os.startfile(p)
    elif sys.platform == "darwin":
        subprocess.run(["open", p], check=False)
    else:
        subprocess.run(["xdg-open", p], check=False)
def open_file_safe(path) -> bool:
    """open_file() that reports failure instead of raising — for CLI
    auto-open, where a missing association must not fail the export."""
    try:
        open_file(path)
        return True
    except Exception:
        return False
def open_file_with(path, viewer: str) -> bool:
    """Open a file with a chosen application (the --viewer option).
    Accepts an app name found on PATH ('code', 'notepad'), an absolute path
    to an executable, or a quoted multi-word path. Returns False (instead of
    raising) when the viewer cannot be found or run."""
    import shutil as _shutil

    v = (viewer or "").strip()
    if not v:
        return False
    exe = v
    if v.startswith('"') and v.endswith('"') and len(v) >= 2:
        exe = v[1:-1]          # allow "C:\Program Files\...\app.exe"
    resolved = (_shutil.which(exe)
                or (_shutil.which(v) if v != exe else None)
                or (exe if Path(exe).is_file() else None))
    if not resolved:
        return False
    try:
        if os.name == "nt":
            subprocess.Popen([resolved, str(path)], close_fds=True)
        else:
            subprocess.Popen([resolved, str(path)], close_fds=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False
def reveal_in_explorer(path) -> None:
    """Open Explorer with the file selected (falls back to opening the folder)."""
    p = Path(path)
    if os.name == "nt":
        subprocess.Popen(["explorer", "/select,", str(p)])
    else:
        open_file(p.parent)
def open_folder(path) -> None:
    """Open a folder in the OS file manager (Explorer on Windows)."""
    p = str(path)
    if os.name == "nt":
        os.startfile(p)
    elif sys.platform == "darwin":
        subprocess.run(["open", p], check=False)
    else:
        subprocess.run(["xdg-open", p], check=False)
def update_history_dest(old_dest: str, new_dest: str, path=None) -> int:
    """Re-point every history entry recorded at old_dest to new_dest and
    persist. Returns the number of entries updated (0 also covers a write
    failure, so callers log one honest message either way)."""
    import json
    p = Path(path) if path else SUBS_HISTORY_FILE
    history = load_subs_history(path)
    n = 0
    for r in history:
        if r.get("dest") == old_dest:
            r["dest"] = str(new_dest)
            n += 1
    if not n:
        return 0
    try:
        p.write_text(json.dumps(history, indent=1), encoding="utf-8")
    except OSError:
        return 0  # disk problem; the caller reports the relocation failed
    return n


def export_match_count(provider: str = "", since: str = "", until: str = "") -> int:
    """How many history rows the current filter selections match."""
    return len(filter_history(load_subs_history(), provider, since, until))


def read_state_records(path=None) -> dict:
    """Read-only peek at a download-state file (default: ./downloads/idm.state.json).
    Missing or malformed file -> {} — and unlike State(), this never creates
    the file or its directory, so a peek can't leave stray artifacts."""
    p = Path(path) if path else Path("downloads") / "idm.state.json"
    try:
        import json
        raw = json.loads(p.read_text(encoding="utf-8"))
        d = raw.get("downloads", {}) if isinstance(raw, dict) else {}
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}
SUBS_HISTORY_FILE = APP_DIR / "subtitle_history.json"
EXPORT_PREFS_FILE = APP_DIR / "export_prefs.json"
def load_export_prefs() -> dict:
    """Persisted export-dialog settings, keyed by dialog slot ('history',
    'downloads', 'query:<source>'). Missing or broken file -> {} (defaults)."""
    try:
        data = json.loads(EXPORT_PREFS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}
def _write_export_prefs(stored: dict) -> None:
    """Persist the prefs document (best-effort: write failures are ignored —
    remembering must never break the export flow)."""
    try:
        EXPORT_PREFS_FILE.write_text(
            json.dumps(stored, indent=1, ensure_ascii=True), encoding="utf-8")
    except OSError:
        pass
def remember_export_prefs(key: str, **prefs) -> None:
    """Merge prefs into the stored settings for dialog slot `key` and write
    the file back. Remembering is best-effort: a write failure is ignored so
    the export flow itself can never be broken by it."""
    stored = load_export_prefs()
    slot = stored.get(key)
    stored[key] = {**(slot if isinstance(slot, dict) else {}), **prefs}
    _write_export_prefs(stored)
TABLE_COLW_CLAMP = (30, 2000)
def apply_saved_colw(tree, cols, slot: str) -> None:
    """Restore one table's column widths from export_prefs.json slot `slot`
    ({col: pixels}; missing/out-of-range/mangled entries fall back to the
    tree's existing width — this runs after the build loop set defaults)."""
    layout = load_export_prefs().get(slot)
    saved = layout.get("colw") if isinstance(layout, dict) else None
    if not isinstance(saved, dict):
        return
    lo, hi = TABLE_COLW_CLAMP
    for c in cols:
        w = saved.get(c)
        if isinstance(w, int) and lo <= w <= hi:
            try:
                tree.column(c, width=w)
            except tk.TclError:
                pass
def save_table_colw(tree, cols, slot: str) -> bool:
    """Record one table's current column widths under slot `slot`
    (best-effort like the other prefs writers). Returns False when the
    widget is already gone (app torn down) — nothing is saved then."""
    try:
        widths = {c: int(tree.column(c, "width")) for c in cols}
    except tk.TclError:
        return False
    remember_export_prefs(slot, colw=widths)
    return True
_PRESET_KINDS = ("history", "downloads", "stats", "providers")
_PRESET_SETTINGS = {
    "history": ("provider", "since", "until", "query", "out", "viewer"),
    "downloads": ("fmt", "query", "out", "viewer"),
    "stats": ("query", "out", "viewer"),
    "providers": ("query", "out", "viewer"),
}
def load_presets(kind: str) -> dict:
    """The saved presets for one dialog kind ('history' | 'downloads'):
    {name: settings dict}, name->slot insertion order. Mangled entries are
    dropped, like the other prefs readers."""
    presets = load_export_prefs().get("presets")
    if not isinstance(presets, dict):
        return {}
    slot = presets.get(kind)
    if not isinstance(slot, dict):
        return {}
    return {k: v for k, v in slot.items() if isinstance(v, dict)}
def save_preset(kind: str, name: str, **settings) -> bool:
    """Store settings under `name` for dialog kind `kind` (overwrites an
    existing preset of the same name). Returns False — and saves nothing —
    when the name is blank or the kind is unknown."""
    name = str(name or "").strip()
    if not name or kind not in _PRESET_KINDS:
        return False
    stored = load_export_prefs()
    presets = stored.get("presets")
    presets = presets if isinstance(presets, dict) else {}
    slot = presets.get(kind)
    slot = slot if isinstance(slot, dict) else {}
    slot[name] = {k: settings[k] for k in _PRESET_SETTINGS[kind]
                  if k in settings}
    presets[kind] = slot
    stored["presets"] = presets
    _write_export_prefs(stored)
    return True
def delete_preset(kind: str, name: str) -> bool:
    """Remove the preset `kind/name`; True when it existed. Emptied slots
    are pruned so the file stays tidy."""
    stored = load_export_prefs()
    presets = stored.get("presets")
    if not isinstance(presets, dict):
        return False
    slot = presets.get(kind)
    if not isinstance(slot, dict) or name not in slot:
        return False
    del slot[name]
    if slot:
        presets[kind] = slot
    else:
        presets.pop(kind, None)
    if presets:
        stored["presets"] = presets
    else:
        stored.pop("presets", None)
    _write_export_prefs(stored)
    return True
def export_presets(path) -> int:
    """Write all presets to a shareable JSON file ({kind: {name: settings}},
    pretty-printed like every other JSON output). Returns the number of
    presets written; an empty store exports a valid empty document."""
    presets = {k: load_presets(k) for k in _PRESET_KINDS}
    Path(path).write_text(
        json.dumps(presets, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8")
    return sum(len(v) for v in presets.values())
def import_presets(path, replace: bool = False):
    """Merge presets from a file written by export_presets into the store.
    With replace=True each kind's existing presets are dropped first —
    otherwise the file's entries overwrite same-named ones (unknown setting
    keys are dropped by the whitelist, so files from newer versions import
    cleanly). Returns (imported, skipped, readable): skipped counts entries
    not imported (wrong kind, malformed settings, blank name); readable is
    False only for missing/unparseable/non-object files."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return (0, 0, False)
    if not isinstance(data, dict):
        return (0, 0, False)
    if replace:
        stored = load_export_prefs()
        stored.pop("presets", None)
        _write_export_prefs(stored)
    imported = skipped = 0
    for kind, slot in data.items():
        if kind not in _PRESET_KINDS:
            skipped += len(slot) if isinstance(slot, dict) else 1
            continue
        if not isinstance(slot, dict):
            skipped += 1
            continue
        for name, settings in slot.items():
            if isinstance(settings, dict) and save_preset(kind, name,
                                                          **settings):
                imported += 1
            else:
                skipped += 1
    return (imported, skipped, True)
RECENT_QUERY_LIMIT = 8
def _recent_queries_for(source: str) -> list[str]:
    """The stored recent queries for a Run Query… source, most recent first.
    Tolerates a mangled file: non-list or non-string entries are dropped."""
    prefs = load_export_prefs().get(f"query:{source}", {})
    rec = prefs.get("recent") if isinstance(prefs, dict) else None
    if not isinstance(rec, list):
        return []
    return [q for q in rec if isinstance(q, str)]
def remember_recent_query(source: str, query: str) -> None:
    """Record a successfully run query for `source` (most recent first,
    deduplicated, capped at RECENT_QUERY_LIMIT) and update the slot's
    'query' prefill to match. Best-effort, like the other prefs writers."""
    rec = _recent_queries_for(source)
    if query in rec:
        rec.remove(query)
    rec.insert(0, query)
    remember_export_prefs(f"query:{source}", query=query,
                          recent=rec[:RECENT_QUERY_LIMIT])
def forget_recent_query(source: str, query: str) -> bool:
    """Drop `query` from the remembered recents for `source` (middle-click
    curation on a recent-query chip). The slot's 'query' prefill follows: it
    becomes the new newest entry, or is cleared when the list runs out —
    unless it was pointing at a query that is still remembered. True when
    the query was actually removed."""
    rec = _recent_queries_for(source)
    if query not in rec:
        return False
    rec.remove(query)
    slot = load_export_prefs().get(f"query:{source}")
    query_prefill = slot.get("query") if isinstance(slot, dict) else None
    if isinstance(query_prefill, str) and query_prefill in rec:
        prefill = query_prefill        # prefill still points at a remembered query
    elif rec:
        prefill = rec[0]               # follow the list to its new newest entry
    else:
        prefill = ""                   # the list ran out: clear the prefill
    remember_export_prefs(f"query:{source}", query=prefill, recent=rec)
    return True
def expand_preset_out(path: str, kind: str, now=None) -> str:
    """Expand placeholders in a preset's pinned out path:
    {date} -> local YYYY-MM-DD (when the file is written), {kind} -> the
    preset's dialog kind ('history'/'downloads'). Everything else passes
    through unchanged, so literal paths stay literal."""
    stamp = time.strftime("%Y-%m-%d", time.localtime(
        time.time() if now is None else now))
    return str(path).replace("{date}", stamp).replace("{kind}", str(kind))
class _Tooltip:
    """A small single-tooltip helper: attach to a widget, shows on hover,
    hides on leave/click/destroy. Standard Tk idiom (no styling frills —
    the chips' full settings text is the point)."""

    def __init__(self, widget, text: str):
        self._widget = widget
        self._text = text
        self._tip = None
        self._after_id = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")
        widget.bind("<Destroy>", self._hide, add="+")

    def _schedule(self, _event=None):
        self._cancel()
        self._after_id = self._widget.after(500, self._show)

    def _cancel(self):
        if self._after_id:
            try:
                self._widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _show(self):
        if self._tip or not self._text:
            return
        x = self._widget.winfo_rootx() + 12
        y = self._widget.winfo_rooty() + self._widget.winfo_height() + 4
        self._tip = tw = tk.Toplevel(self._widget)
        tw.wm_overrideredirect(True)          # borderless hint window
        try:
            tw.attributes("-topmost", True)
        except tk.TclError:
            pass
        tw.wm_geometry(f"+{x}+{y}")
        tk.Label(tw, text=self._text, justify="left", background="#ffffe0",
                 relief="solid", borderwidth=1, font=("TkDefaultFont", 9),
                 wraplength=520).pack(ipadx=4, ipady=2)

    def _hide(self, _event=None):
        self._cancel()
        if self._tip:
            try:
                self._tip.destroy()
            except tk.TclError:
                pass
            self._tip = None
def _attach_tooltip(widget, text: str) -> None:
    """Attach a hover tooltip to a widget (skips fake widgets in tests:
    anything without .bind is fine unannotated). The _Tooltip instance is
    kept on the widget as `_tooltip` so tests can drive the show/hide
    lifecycle without real hover events."""
    try:
        widget._tooltip = _Tooltip(widget, text)
    except (AttributeError, tk.TclError):
        pass
def _bind_middle_click(widget, remove) -> None:
    """Bind the chip-removal click (middle button) to a `remove` closure.
    Both <Button-2> and <Button-3> are bound so the gesture works on every
    platform (Windows reports middle as Button-2, X11 as Button-2/3 by
    hardware). The closure is also stored on the widget as `_remove` so
    tests can fire the removal without synthesizing click events."""
    try:
        widget.bind("<Button-2>", lambda _e: remove())
        widget.bind("<Button-3>", lambda _e: remove())
        widget._remove = remove
    except (AttributeError, tk.TclError):
        pass
def _preset_tip_text(name: str, settings: dict) -> str:
    """The full 'what this chip would apply' text for a preset chip's
    tooltip — every stored setting, including the complete query text and
    the pinned out/viewer (placeholders shown unexpanded, like the store)."""
    lines = [f"preset '{name}' applies:"]
    for key in sorted(settings):
        v = settings[key]
        if v:
            lines.append(f"  {key} = {v}")
    if len(lines) == 1:
        lines.append("  (no settings)")
    return "\n".join(lines)
def _query_tip_text(query: str) -> str:
    """The tooltip text for a recent-query chip: the full query text."""
    return f"apply this query:\n  {query}"
def _preset_chip_text(name: str, settings: dict) -> str:
    """A preset's chip label: its name plus a hint of what it does, e.g.
    'errors · json · query'. Long values are dropped — the chips only need
    to be recognizable; Apply shows the real values."""
    bits = [name]
    for key, v in sorted(settings.items()):
        if key in ("out", "viewer") or not v:
            continue
        bits.append(f"{key}={v}" if len(str(v)) <= 14 else key)
    return " · ".join(bits)
def _last_used_preset(kind: str, prefs=None) -> str:
    """The remembered preset name for dialog kind `kind` ('history' /
    'downloads'), or '' when nothing was remembered. `prefs` may be either
    the full prefs document or the dialog's own slot dict."""
    if prefs is None:
        prefs = load_export_prefs()
    slot = prefs.get(kind, prefs)           # slot dict or whole document
    if not isinstance(slot, dict):
        return ""
    p = slot.get("preset")
    return p if isinstance(p, str) else ""
def _build_preset_row(dlg, kind: str, initial_row: int, on_apply,
                      on_save, on_delete, prefs, on_delete_named):
    """The shared Preset row for the export dialogs: an editable combobox
    (pick or type a name), Apply/Save/Delete buttons, one clickable example
    chip per saved preset, and a keyboard shortcut — Ctrl+P (or F5) applies
    the last-used preset without touching the mouse. Returns
    (preset_cb, refresh_chips); refresh_chips() re-renders the chips and
    the dropdown after save/delete. The caller owns the on_* closures;
    on_delete_named(name) removes one named preset (the chips'
    middle-click path) — delete + dropdown re-render + caller log."""
    ttk.Label(dlg, text="Preset:").grid(row=initial_row, column=0,
                                        sticky="e", padx=6, pady=(4, 0))
    preset_cb = ttk.Combobox(dlg, values=sorted(load_presets(kind)),
                             width=18)
    preset_cb.grid(row=initial_row, column=1, padx=6, pady=(4, 0))
    chip_row = ttk.Frame(dlg)
    chip_row.grid(row=initial_row + 1, column=0, columnspan=4,
                  sticky="e", padx=6)

    def _apply_last(_event=None):
        """Keyboard shortcut body: apply the last-used preset, if any."""
        last = _last_used_preset(kind, prefs)
        if last in load_presets(kind):
            preset_cb.set(last)
            on_apply()
            return "break"
        return None

    def refresh_chips():
        for child in chip_row.winfo_children():
            child.destroy()
        presets = load_presets(kind)
        if not presets:
            ttk.Label(chip_row,
                      text="no presets saved — type a name and press Save",
                      foreground="#777").pack(side="left")
            return
        if _last_used_preset(kind, prefs) in presets:
            ttk.Label(chip_row, text="Ctrl+P/F5 applies the last preset —",
                      foreground="#999").pack(side="left", padx=(0, 4))
        for name in sorted(presets):
            chip = ttk.Button(
                chip_row, text=_preset_chip_text(name, presets[name]),
                command=lambda n=name: (preset_cb.set(n), on_apply()))
            _attach_tooltip(chip, _preset_tip_text(name, presets[name]))
            # middle-click removes the preset (the caller's named-delete —
            # same store change + log as the Delete button — plus the chip
            # re-render that button pairs it with) so curating the list
            # never needs the two-step select-then-Delete flow
            _bind_middle_click(
                chip, lambda n=name: (on_delete_named(n), refresh_chips()))
            chip.pack(side="left", padx=(0, 4))

    preset_btns = ttk.Frame(dlg)
    preset_btns.grid(row=initial_row, column=2, columnspan=2, padx=(2, 0),
                     pady=(4, 0))
    ttk.Button(preset_btns, text="Apply", width=6,
               command=on_apply).pack(side="left")
    ttk.Button(preset_btns, text="Save", width=6,
               command=lambda: (on_save(), refresh_chips())).pack(side="left",
                                                                  padx=2)
    ttk.Button(preset_btns, text="Delete", width=6,
               command=lambda: (on_delete(), refresh_chips())).pack(side="left")
    preset_cb.bind("<Return>", on_apply)   # 'break' return: Return in the
    # preset field applies the preset, it does NOT OK the dialog
    for seq in ("<Control-p>", "<Control-P>", "<F5>"):
        dlg.bind(seq, _apply_last)
    dlg.apply_last_preset = _apply_last    # testable handle for the shortcut
    refresh_chips()
    return preset_cb, refresh_chips
LAST_PRUNE_DROPPED = 0
def load_subs_history(path=None) -> list[dict]:
    """Persisted subtitle history rows (oldest first). Missing file -> []."""
    p = Path(path) if path else SUBS_HISTORY_FILE
    try:
        import json
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []
def prune_history(history, max_age_days: float, now=None) -> list[dict]:
    """Drop entries stamped older than max_age_days; entries without a stamp
    (pre-1.10.4 history) are kept regardless. max_age_days <= 0 disables
    pruning entirely."""
    if not max_age_days or max_age_days <= 0:
        return list(history or [])
    t = time.time() if now is None else now
    cutoff = t - max_age_days * 86400
    return [r for r in (history or [])
            if not isinstance(r.get("ts"), (int, float)) or r["ts"] >= cutoff]
def _history_max_age_days() -> float:
    """Configured prune age in days; 0 (disabled) on missing/invalid values."""
    try:
        v = float(get_config().get("subtitle_history_max_age_days", 0))
    except (TypeError, ValueError):
        return 0.0
    return v if v > 0 else 0.0
def save_subs_history(results: list[dict], path=None, cap: int = 200) -> list[dict]:
    """Append successful rows to the persisted history (capped, oldest first)
    and write it back. New rows are timestamped ("ts", unix seconds) so the
    panel can show the oldest entry's age; caller dicts are not mutated.
    Returns the updated history."""
    import json
    p = Path(path) if path else SUBS_HISTORY_FILE
    history = load_subs_history(path)
    ok_rows = [dict(r, ts=r.get("ts", time.time()))
               for r in (results or []) if r.get("ok") and (r.get("dest") or r.get("path"))]
    history.extend(ok_rows)
    history = history[-cap:]
    dropped = len(history)
    history = prune_history(history, _history_max_age_days())
    globals()["LAST_PRUNE_DROPPED"] = dropped - len(history)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(history, indent=1), encoding="utf-8")
    except OSError:
        pass  # persistence is best-effort; the table still works
    return history
def apply_export_query(query: str, data):
    """Evaluate a --query-style jq expression against export data (the same
    evaluator the CLI uses). Returns the result; raises jq.JqError on bad
    syntax or a type mismatch — callers decide how to surface that."""
    return jq.apply_query(query, data)
def export_json_text(result) -> str:
    """Query result / JSON export payload -> file text (pretty, ASCII-escaped
    like the CLI's --json output)."""
    return json.dumps(result, indent=2, ensure_ascii=True) + "\n"
def _query_cell(v) -> str:
    """One query-result value -> display cell text (JSON-style literals)."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return v
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=True)
    return str(v)
def _query_result_rows(result) -> tuple[list[str], list[list[str]]]:
    """A --query result (scalar, dict, or list) -> (columns, display rows)
    for the GUI results window: lists of dicts become rows with the union of
    their keys as columns (first-seen order); lists of scalars and bare
    scalars become a single 'value' column; a dict becomes one row."""
    if isinstance(result, list):
        if result and all(isinstance(r, dict) for r in result):
            cols: list[str] = []
            for r in result:
                for k in r:
                    if k not in cols:
                        cols.append(k)
            return cols, [[_query_cell(r.get(c)) for c in cols] for r in result]
        return ["value"], [[_query_cell(v)] for v in result]
    if isinstance(result, dict):
        cols = list(result.keys())
        return cols, [[_query_cell(result[c]) for c in cols]]
    return ["value"], [[_query_cell(result)]]
def write_history_export(path, fmt: str = "csv", provider: str = "",
                         since: str = "", until: str = "") -> int:
    """Write the persisted history to path as 'csv', 'markdown', or 'json',
    optionally filtered by exact provider and/or a [since, until] date window.
    Returns the number of data rows written (CSV header excluded)."""
    history = filter_history(load_subs_history(), provider, since, until)
    if fmt == "json":
        Path(path).write_text(export_json_text(history_json_entries(history)),
                              encoding="utf-8")
        return len(history_json_entries(history))
    rows = subtitle_rows(history)
    text = history_to_csv(rows) if fmt == "csv" else history_to_markdown(rows)
    # newline="" keeps the text byte-exact: default translation would turn
    # the CSV's \r\n into \r\r\n on Windows. write_text_newlines keeps that
    # guarantee on the py3.9 floor, where Path.write_text has no newline arg.
    write_text_newlines(path, text)
    return len(rows)
def subtitle_hints(results: list[dict]) -> list[str]:
    """Human-readable one-liners per successful subtitle download."""
    out = []
    for video, lang, provider, size, cues in subtitle_rows(results):
        out.append(f"{video} [{lang}] via {provider} — {size}, {cues} cues")
    return out


from .about import AboutDialogsMixin  # noqa: E402  (mixin imports gui)
from .exports import ExportDialogsMixin  # noqa: E402  (mixin imports gui)
from .query_dialogs import QueryDialogsMixin  # noqa: E402
from .subtitles_panel import SubtitlesPanelMixin  # noqa: E402


class _AppBase(tk.Tk):
    if TYPE_CHECKING:

        # Methods supplied by the App mixins (see the bases of App below).
        # Declared so _AppBase's Tk bindings type-check; annotations
        # create no runtime attributes.
        open_subtitle: Callable[..., None]
        reveal_subtitle: Callable[..., None]
        open_video_folder: Callable[..., None]
        clear_subs_history: Callable[..., None]
        _relocate_subtitle: Callable[..., None]
        _restore_subs_history: Callable[..., None]
        _save_subs_history_file: Callable[..., None]
        _copy_subs_history: Callable[..., None]
        run_query: Callable[..., None]
        _api_key_wizard: Callable[..., None]
        fetch_subs: Callable[..., None]
        run_provider_check: Callable[..., None]
        clear_export_prefs: Callable[..., None]
        run_report_preset: Callable[..., None]
        _edit_config_value: Callable[..., None]
        show_about: Callable[..., None]
        _open_downloads_export_dialog: Callable[..., Any]
        _write_downloads_export: Callable[..., None]
        _on_subs_done: Callable[..., None]
        _on_providers_done: Callable[..., None]
        _on_report_done: Callable[..., None]
    def __init__(self):
        super().__init__()
        self.title("PyIDM — batch download manager")
        self.geometry("1000x720")
        self.minsize(860, 600)

        self.cfg = get_config()
        self.events: queue.Queue[tuple] = queue.Queue()
        self.cancel_event = threading.Event()
        self.running = False
        self.iids: dict[str, str] = {}
        self.quar_iids: dict[str, str] = {}   # moved rows: url (or quarantine::file) → iid
        self.speed: dict[str, tuple[float, int]] = {}
        self._prov_running = False
        self._collector = None                 # CollectorServer when running
        self._collector_port = int(self.cfg.get("collector_port") or 27492)
        self._build_ui()
        self._build_menu()
        self.after(100, self._poll)
        self.after(400, self._maybe_first_run_wizard)
        # closing the app remembers the main tables' column widths (the [X]
        # button and every other close route go through this hook)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _save_table_layouts(self) -> None:
        """Remember the downloads tab's two tables' column widths (best-
        effort, like the results windows' layout-on-close)."""
        for tree, cols, slot in getattr(self, "_column_slots", ()):
            save_table_colw(tree, cols, slot)

    def _on_close(self) -> None:
        """App close: stop the collector (it holds the port) and the queue
        poller, save the main tables' column widths, then close."""
        self._stop_collector()
        self._save_table_layouts()
        self.destroy()

    # ------------------------------------------------- collector (browser)
    def _collector_log(self, msg: str, level: str = "info") -> None:
        """Collector log callback — worker threads; route through the event
        queue so the Tk log stays on its own thread."""
        self.events.put(("log", msg, level))

    def _start_collector(self) -> None:
        """Tools ▸ 'Start browser collector': serve the extension's capture
        endpoints on 127.0.0.1 (daemon thread, like every other worker)."""
        if self._collector is not None:
            self._log(f"collector already running on port {self._collector_port}")
            return
        from .collect import start_collector
        port = int(self.cfg.get("collector_port") or 27492)
        try:
            srv = start_collector(port, dict(self.cfg),
                                  log=self._collector_log)
        except OSError as e:
            self._log(f"collector could not bind port {port}: {e} — "
                      "another PyIDM may be running", "warn")
            return
        self._collector = srv
        self._collector_port = srv.server_address[1]   # actual (cfg may say 0)
        self._log(f"browser collector listening on "
                  f"http://127.0.0.1:{self._collector_port} "
                  "— use the PyIDM Collector extension's popup or "
                  "right-click ▸ 'Send link to PyIDM…'")
        self._refresh_tools_menu()

    def _stop_collector(self) -> None:
        """Stop the collector server (idempotent; also the app-close path)."""
        srv, self._collector = self._collector, None
        if srv is not None:
            srv.shutdown()
            srv.server_close()
            self._log(f"browser collector stopped (port {self._collector_port})")
        self._refresh_tools_menu()

    def _download_collector_queue(self) -> None:
        """Tools ▸ 'Download queue now': drain the captured links into a
        batch download to the Save-to directory (off the UI thread)."""
        from .collect import queue_take, start_downloads
        jobs = queue_take()
        if not jobs:
            self._log("collector queue is empty — capture links with the "
                      "browser extension first", "warn")
            return
        out_dir = Path(self.out_var.get().strip() or "downloads")
        cfg = dict(self.cfg)
        self._log(f"collector queue: starting {len(jobs)} download(s) "
                  f"-> {out_dir}")

        def worker():
            summary = start_downloads(jobs, out_dir, cfg,
                                      log=self._collector_log)
            self.events.put(("batch_done", summary.get("ok", 0),
                             summary.get("total", len(jobs))))

        threading.Thread(target=worker, daemon=True).start()

    def _show_collector_queue(self) -> None:
        """Tools ▸ 'Show collector queue': log what the browser sent, oldest
        first — pending items and the last few already handled."""
        from .collect import load_queue
        q = load_queue()
        if not q["pending"]:
            self._log("collector queue is empty")
        for e in q["pending"]:
            src = f" (from {e['page_url']})" if e.get("page_url") else ""
            self._log(f"pending: {e['url']}{src}")
        self._log(f"{len(q['pending'])} pending, {len(q['history'])} already "
                  "handled (full history in ~/.idm/collect_queue.json)")

    # ------------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True)
        self._build_downloads_tab(self.notebook)
        self._build_providers_tab(self.notebook)
        self.notebook.select(0)

    def _build_downloads_tab(self, notebook) -> None:
        pad: dict[str, Any] = {"padx": 8, "pady": 4}
        dl_tab = ttk.Frame(notebook)
        notebook.add(dl_tab, text="Downloads")

        top = ttk.Frame(dl_tab)
        top.pack(fill="x", **pad)
        self.dl_status_var = tk.StringVar(value="0 pending")
        ttk.Label(top, textvariable=self.dl_status_var,
                  foreground="#555").pack(anchor="e")
        top_btns = ttk.Frame(top)
        top_btns.pack(anchor="e")
        ttk.Button(top_btns, text="Run Query…", width=11,
                   command=lambda: self.run_query("downloads")).pack(
            side="left", padx=(0, 4))
        ttk.Button(top_btns, text="Save As…", width=9,
                   command=self._save_downloads_file).pack(side="left")
        ttk.Label(top, text="URLs — one per line, optional 'url -> name' to rename, '#' comments:").pack(anchor="w")
        self.url_text = ScrolledText(top, height=6, wrap="none", undo=True)
        self.url_text.pack(fill="x")

        btns = ttk.Frame(dl_tab)
        btns.pack(fill="x", **pad)
        ttk.Button(btns, text="Load list from file…", command=self.load_file).pack(side="left")
        ttk.Button(btns, text="Paste clipboard", command=self.paste_clipboard).pack(side="left", padx=4)
        ttk.Button(btns, text="Clear", command=lambda: self.url_text.delete("1.0", "end")).pack(side="left")

        opts = ttk.Frame(dl_tab)
        opts.pack(fill="x", **pad)
        ttk.Label(opts, text="Save to:").pack(side="left")
        self.out_var = tk.StringVar(value=str(self.cfg.get("out_dir") or "downloads"))
        ttk.Entry(opts, textvariable=self.out_var, width=38).pack(side="left", padx=4)
        ttk.Button(opts, text="…", width=3, command=self.pick_dir).pack(side="left")
        ttk.Label(opts, text="Workers: ").pack(side="left", padx=(14, 2))
        self.workers_var = tk.IntVar(value=int(self.cfg.get("workers", 4) or 4))
        ttk.Spinbox(opts, from_=1, to=16, width=4, textvariable=self.workers_var).pack(side="left")
        ttk.Label(opts, text="Connections/file:").pack(side="left", padx=(14, 2))
        self.segments_var = tk.IntVar(value=int(self.cfg.get("segments", 8) or 8))
        ttk.Spinbox(opts, from_=1, to=32, width=4, textvariable=self.segments_var).pack(side="left")
        self.start_btn = ttk.Button(opts, text="▶ Start batch", command=self.start_batch)
        self.start_btn.pack(side="left", padx=14)
        self.stop_btn = ttk.Button(opts, text="■ Stop", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left")

        cols = ("url", "file", "status", "progress", "size", "speed", "note")
        widths = (340, 190, 95, 90, 110, 95, 240)
        tree_frame = ttk.Frame(dl_tab)
        tree_frame.pack(fill="both", expand=True, padx=(8, 0), pady=4)
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=12)
        for c, w in zip(cols, widths):
            self.tree.heading(c, text=c.title())
            self.tree.column(c, width=w, anchor="w", stretch=(c in ("url", "file")))
        apply_saved_colw(self.tree, cols, "table:downloads")
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="left", fill="y")
        self.tree.tag_configure("done", foreground=STATUS_COLORS["done"])
        self.tree.tag_configure("error", foreground=STATUS_COLORS["error"])
        self.tree.tag_configure("cancelled", foreground=STATUS_COLORS["cancelled"])
        self.tree.tag_configure("skipped", foreground="#8a6d3b")
        # amber 'warned' badge tag — configured last so it wins over the
        # status tags (a warned done row shows amber, not green)
        self.tree.tag_configure("warned", foreground="#b26a00")
        # gray 'moved' badge tag for rows whose file was swept into
        # quarantine (the tree is a live view of the folder, not the state file)
        self.tree.tag_configure("moved", foreground="#6b7280")
        # violet badge for rows whose file has a mangled name — the Note
        # column shows the clean name the right-click fix would rename to
        self.tree.tag_configure("rename", foreground="#7c3aed")
        # right-click menu on the downloads tree: quarantine workflow
        self.tree_menu = tk.Menu(self, tearoff=0)
        self.tree_menu.add_command(label="Verify files (magic-byte scan)",
                                   command=self.verify_quarantine_from_gui)
        self.tree_menu.add_command(label="Restore quarantined files…",
                                   command=self.restore_quarantine_from_gui)
        self.tree_menu.add_command(label="Ignore this file in verify…",
                                   command=self.ignore_file_from_gui)
        self.tree_menu.add_command(label="Manage verify ignore list…",
                                   command=self.manage_verify_ignore_from_gui)
        self.tree_menu.add_command(label="Fix this file name…",
                                   command=self.fix_one_name_from_gui)
        self.tree_menu.add_command(label="Prune stale records…",
                                   command=self.prune_stale_from_gui)
        self.tree.bind("<Button-2>", self._show_tree_menu)
        self.tree.bind("<Button-3>", self._show_tree_menu)

        self.aggregate = ttk.Progressbar(dl_tab, mode="determinate")
        self.aggregate.pack(fill="x", **pad)
        self.agg_label = ttk.Label(dl_tab, text="")
        self.agg_label.pack(anchor="e", padx=8)

        subs = ttk.LabelFrame(dl_tab, text="Subtitles (OpenSubtitles — set key with: idm config set opensubtitles_api_key KEY)")
        subs.pack(fill="x", **pad)
        ttk.Label(subs, text="Video folder:").pack(side="left", padx=(6, 2))
        self.subs_dir = tk.StringVar()
        ttk.Entry(subs, textvariable=self.subs_dir, width=46).pack(side="left", padx=2)
        ttk.Button(subs, text="…", width=3, command=self.pick_subs_dir).pack(side="left")
        ttk.Label(subs, text="Languages:").pack(side="left", padx=(12, 2))
        self.langs_var = tk.StringVar(value=str(self.cfg.get("subtitle_languages") or "en"))
        ttk.Entry(subs, textvariable=self.langs_var, width=8).pack(side="left")
        self.play_var = tk.BooleanVar(value=bool(self.cfg.get("subs_autoplay")))
        ttk.Checkbutton(subs, text="Open in player when done",
                        variable=self.play_var).pack(side="left", padx=(12, 2))
        ttk.Label(subs, text="Player:").pack(side="left", padx=(6, 2))
        self.player_var = tk.StringVar(value=str(self.cfg.get("player") or ""))
        ttk.Entry(subs, textvariable=self.player_var, width=14).pack(side="left")
        self.subs_btn = ttk.Button(subs, text="Fetch subtitles", command=self.fetch_subs)
        self.subs_btn.pack(side="left", padx=10)
        ttk.Button(subs, text="API key…", command=self._api_key_wizard).pack(side="left")

        # history of subtitle downloads: which provider/file produced each .srt
        hist = ttk.LabelFrame(dl_tab, text="Subtitle history (provider · file · quality)")
        hist.pack(fill="both", **pad)
        hist_cols = ("video", "lang", "provider", "size", "cues")
        self.subs_tree = ttk.Treeview(hist, columns=hist_cols, show="headings", height=5)
        for cid, text, width, anchor in (
            ("video", "Video / title", 260, "w"),
            ("lang", "Lang", 44, "center"),
            ("provider", "Provider", 110, "w"),
            ("size", "Size", 80, "e"),
            ("cues", "Cues", 60, "e"),
        ):
            self.subs_tree.heading(cid, text=text)
            self.subs_tree.column(cid, width=width,
                                  anchor=cast(Any, anchor))  # Tk accepts any anchor str
        apply_saved_colw(self.subs_tree, hist_cols, "table:history")
        self.subs_tree.pack(side="left", fill="both", expand=True)
        ysb = ttk.Scrollbar(hist, orient="vertical", command=self.subs_tree.yview)
        ysb.pack(side="right", fill="y")
        self.subs_tree.configure(yscrollcommand=ysb.set)
        self.subs_dest_by_iid: dict[str, str] = {}
        self.subs_video_by_iid: dict[str, str] = {}
        self.subs_sort_var = tk.StringVar(value="Newest")
        sort_bar = ttk.Frame(hist)
        sort_bar.pack(fill="x")
        ttk.Label(sort_bar, text="Sort:").pack(side="left", padx=(2, 4))
        ttk.Combobox(
            sort_bar, width=13, state="readonly", textvariable=self.subs_sort_var,
            values=tuple(SUBS_SORT_LABELS),
        ).pack(side="left")
        ttk.Label(sort_bar, text="— amber rows are weak downloads worth reviewing",
                  foreground="#b26a00").pack(side="left", padx=8)
        self.subs_status_var = tk.StringVar(value="0 entries")
        ttk.Label(sort_bar, textvariable=self.subs_status_var,
                  foreground="#555").pack(side="right", padx=(0, 2))
        self.subs_sort_var.trace_add("write", lambda *_: self._restore_subs_history())
        self.subs_tree.tag_configure("weak", background="#fff3d6", foreground="#7a5200")
        self.subs_tree.tag_configure("missing", background="#ececec", foreground="#777")
        # for the close hook: which table's widths go under which prefs slot
        self._column_slots = ((self.tree,
                               ("url", "file", "status", "progress", "size",
                                "speed"), "table:downloads"),
                              (self.subs_tree,
                               ("video", "lang", "provider", "size", "cues"),
                               "table:history"))
        ttk.Label(sort_bar, text="— grey rows are missing on disk",
                  foreground="#777").pack(side="left", padx=4)
        hist_btns = ttk.Frame(hist)
        hist_btns.pack(side="right", anchor="n", padx=(0, 4), pady=4)
        ttk.Button(hist_btns, text="Run Query…", width=11,
                   command=lambda: self.run_query("history")).pack(anchor="e")
        ttk.Button(hist_btns, text="Copy CSV", width=9,
                   command=lambda: self._copy_subs_history("csv")).pack(anchor="e")
        ttk.Button(hist_btns, text="Copy MD", width=9,
                   command=lambda: self._copy_subs_history("markdown")).pack(
            anchor="e", pady=(2, 0))
        ttk.Button(hist_btns, text="Save As...", width=9,
                   command=self._save_subs_history_file).pack(anchor="e", pady=(2, 0))
        self.saveas_open_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(hist_btns, text="Open after export",
                        variable=self.saveas_open_var).pack(anchor="e", pady=(4, 0))
        viewer_row = ttk.Frame(hist_btns)
        viewer_row.pack(anchor="e", pady=(2, 0))
        ttk.Label(viewer_row, text="Viewer:").pack(side="left")
        self.saveas_viewer_var = tk.StringVar(value="")
        ttk.Entry(viewer_row, textvariable=self.saveas_viewer_var,
                  width=10).pack(side="left", padx=(2, 0))
        ttk.Button(hist_btns, text="Clear", width=9,
                   command=self.clear_subs_history).pack(anchor="e", pady=(6, 0))
        self.subs_tree.bind("<Double-1>", lambda _e: self.open_subtitle())
        self.subs_tree.bind("<Return>", lambda _e: self.open_subtitle())
        self.subs_menu = tk.Menu(self, tearoff=0)
        self.subs_menu.add_command(label="Open subtitle", command=self.open_subtitle)
        self.subs_menu.add_command(label="Show in Explorer",
                                   command=self.reveal_subtitle)
        self.subs_menu.add_separator()
        self.subs_menu.add_command(label="Open video folder",
                                   command=self.open_video_folder)
        self.subs_menu.add_separator()
        self.subs_menu.add_command(label="Relocate...",
                                   command=self._relocate_subtitle)
        self.subs_tree.bind("<Button-3>", self._show_subs_menu)

        ttk.Label(dl_tab, text="Log:").pack(anchor="w", padx=8)
        self.log_text = ScrolledText(dl_tab, height=7)
        self.log_text.pack(fill="both", padx=8, pady=(0, 8))
        self._log("Tip: expiring-link refresh is configured in idm.json (link_providers) — see README. "
                  "Unfinished downloads can be resumed with 'idm resume'.")
        self._restore_subs_history()  # persisted rows survive GUI restarts
        self._refresh_rename_notes()
        self._refresh_dl_status()     # store summary now, then every 2s

    def _refresh_rename_notes(self) -> None:
        """Mark rows whose file on disk has a mangled Content-Disposition name
        (violet 'rename' badge; Note shows 'mangled name — clean name is:
        <target>'). URLs unknown to the tree get a synthetic dim row, like
        the moved-row handling. Called at startup and after every rename
        path; rows whose file was fixed out-of-band (CLI or the bulk Tools
        action) lose the stale badge, and synthetic rows for now-clean
        names are removed."""
        out_dir = Path(self.out_var.get().strip() or "downloads")
        scan = scan_unclean_names(out_dir)
        mangled = {f["file"]: f["to"] for f in scan["files"]}
        prefix = "mangled name — clean name is: "
        for url, iid in list(self.iids.items()):
            fname = self.tree.set(iid, "file")
            if url.startswith("rename::"):
                if fname in mangled:
                    continue                     # still pending: keep the row
                self.tree.delete(iid)            # fixed out-of-band: row goes
                del self.iids[url]
            elif fname in mangled:
                values = list(self.tree.item(iid, "values"))
                values[6] = prefix + mangled[fname]
                self.tree.item(iid, values=values, tags=("rename",))
            elif self.tree.set(iid, "note").startswith(prefix):
                values = list(self.tree.item(iid, "values"))
                values[6] = ""
                status = self.tree.set(iid, "status")
                self.tree.item(iid, values=values,
                               tags=(status,) if status in STATUS_COLORS else ())
        shown = {self.tree.set(iid, "file") for iid in self.iids.values()}
        for fname, target in mangled.items():
            if fname not in shown:
                iid = self.tree.insert("", "end", values=(
                    "(from an older run)", fname, "", "", "", "",
                    f"mangled name — clean name is: {target}"))
                self.iids[f"rename::{fname}"] = iid

    def _refresh_dl_status(self) -> None:
        """Update the downloads-tab header line (pending count, bytes, per-status
        breakdown, oldest age) — the same summary 'idm stats' prints, reading
        the state file fresh each pass so concurrent CLI downloads show up."""
        self.dl_status_var.set(downloads_status(read_state_records()))
        self.after(2000, self._refresh_dl_status)

    def _build_providers_tab(self, notebook) -> None:
        """Subtitle-provider health tab: same data as 'idm providers [--deep]'"""
        pad: dict[str, Any] = {"padx": 8, "pady": 4}
        tab = ttk.Frame(notebook)
        notebook.add(tab, text="Providers")

        bar = ttk.Frame(tab)
        bar.pack(fill="x", **pad)
        self.prov_btn = ttk.Button(bar, text="Check providers", command=self.run_provider_check)
        self.prov_btn.pack(side="left")
        self.prov_deep_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="Deep check (fetch a real subtitle from each provider)",
                        variable=self.prov_deep_var).pack(side="left", padx=(10, 0))
        self.prov_status_var = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.prov_status_var).pack(side="left", padx=(14, 0))

        cols = ("provider", "status", "detail", "endpoint")
        widths = (140, 70, 420, 260)
        tf = ttk.Frame(tab)
        tf.pack(fill="both", expand=True, padx=8, pady=4)
        self.prov_tree = ttk.Treeview(tf, columns=cols, show="headings", height=6)
        for c, w in zip(cols, widths):
            self.prov_tree.heading(c, text=c.title())
            self.prov_tree.column(c, width=w, anchor="w", stretch=(c in ("detail", "endpoint")))
        vsb = ttk.Scrollbar(tf, orient="vertical", command=self.prov_tree.yview)
        self.prov_tree.configure(yscrollcommand=vsb.set)
        self.prov_tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="left", fill="y")

        ttk.Label(tab, text="Hints:").pack(anchor="w", padx=8)
        self.prov_hints = ScrolledText(tab, height=5)
        self.prov_hints.pack(fill="both", padx=8, pady=(0, 8))
        self.prov_hints.insert("end", "Press 'Check providers' to test DNS, reachability, "
                                       "API key, and (deep) a real subtitle fetch per provider.\n")

    # ------------------------------------------------------------------ menu
    def _build_menu(self) -> None:
        menu = tk.Menu(self)
        help_menu = tk.Menu(menu, tearoff=0)
        help_menu.add_command(label="About PyIDM…", command=self.show_about)
        help_menu.add_command(label="Clear remembered settings…",
                              command=self.clear_export_prefs)
        menu.add_cascade(label="Help", menu=help_menu)

        # Tools ▸ report presets: run the CLI's stats/providers --preset
        # reports in-process (no subprocess), writing each pinned out file
        # exactly as 'idm stats --preset' / 'idm providers --preset' would.
        # A preset saved via 'idm presets add stats nightly ...' becomes a
        # one-click GUI menu item. With no presets the submenu still works
        # (nothing to run) but stays discoverable via the hint entry.
        tools_menu = tk.Menu(menu, tearoff=0)
        self._tools_menu = tools_menu
        self._tools_report_menu = tools_menu
        # --- browser collector section
        running = self._collector is not None
        if running:
            tools_menu.add_command(
                label=f"Stop browser collector (port {self._collector_port})",
                command=self._stop_collector)
        else:
            tools_menu.add_command(label="Start browser collector",
                                   command=self._start_collector)
        tools_menu.add_command(label="Show collector queue",
                               command=self._show_collector_queue)
        tools_menu.add_command(label="Download queue now",
                               command=self._download_collector_queue)
        tools_menu.add_separator()
        # --- sanitize-names section (the CLI's dry run + apply offer)
        tools_menu.add_command(label="Resume unfinished transfers…",
                               command=self.resume_from_gui)
        tools_menu.add_command(label="Sanitize file names (dry run)…",
                               command=self.sanitize_names_from_gui)
        tools_menu.add_separator()
        # --- report presets section
        tools_menu.add_command(label="No report presets saved — add one with "
                               "'idm presets add'", state="disabled")
        for kind in ("stats", "providers"):
            for name in load_presets(kind):
                def _run_preset(k: str = kind, n: str = name) -> None:
                    self.run_report_preset(k, n)
                tools_menu.add_command(
                    label=f"Run {kind} preset: {name}",
                    command=_run_preset)
        tools_menu.add_separator()
        tools_menu.add_command(
            label="Rebuild this menu after adding presets",
            command=self._rebuild_report_menu)
        menu.add_cascade(label="Tools", menu=tools_menu)

        menu.add_cascade(label="Help", menu=help_menu)
        self.config(menu=menu)

    def _rebuild_report_menu(self) -> None:
        """Rebuild the menubar after presets were added/removed outside the
        running GUI (the CLI edits export_prefs.json directly)."""
        self._build_menu()

    def _refresh_tools_menu(self) -> None:
        """Redraw the menubar so the collector section matches reality
        (start/stop flips the first entry's label)."""
        if getattr(self, "_tools_menu", None) is not None:
            self._build_menu()
        n = sum(len(load_presets(k)) for k in ("stats", "providers"))
        self._log(f"Tools menu rebuilt ({n} report preset(s) found)")

    # ------------------------------------------------ About value editing
    # ------------------------------------------------------- providers tab
    # ------------------------------------------------------- first-run wizard
    # --------------------------------------------------------------- actions
    def load_file(self) -> None:
        path = filedialog.askopenfilename(title="Choose a URL list", filetypes=[("Text", "*.txt"), ("All", "*.*")])
        if path:
            self.url_text.insert("end", Path(path).read_text(encoding="utf-8", errors="replace"))

    def paste_clipboard(self) -> None:
        text = clipboard_text().strip()
        if text:
            self.url_text.insert("end", text + "\n")

    def pick_dir(self) -> None:
        d = filedialog.askdirectory(title="Choose download folder")
        if d:
            self.out_var.set(d)

    def pick_subs_dir(self) -> None:
        d = filedialog.askdirectory(title="Choose folder with videos")
        if d:
            self.subs_dir.set(d)

    def start_batch(self) -> None:
        if self.running:
            return
        jobs = parse_job_lines(self.url_text.get("1.0", "end"))
        if not jobs:
            messagebox.showinfo("PyIDM", "Add at least one URL first.")
            return
        out_dir = Path(self.out_var.get().strip() or "downloads")
        self.cancel_event.clear()
        self.running = True
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")

        cfg = dict(self.cfg)
        cfg["out_dir"] = str(out_dir)
        cfg["segments"] = int(self.segments_var.get() or 8)
        workers = int(self.workers_var.get() or 4)
        state = State(out_dir / "idm.state.json")

        def worker():
            dl = Downloader(
                cfg, out_dir=out_dir, workers=workers,
                progress_cb=lambda t: self.events.put(
                    ("task", t.url, t.filename, t.downloaded, t.total,
                     t.status, t.message, t.note)
                ),
                log_cb=lambda m, lvl="info": self.events.put(("log", m, lvl)),
                state=state,
                cancel_event=self.cancel_event,
            )
            results = dl.download_batch(jobs)
            ok = sum(1 for t in results if t.status in ("done", "skipped"))
            self.events.put(("batch_done", ok, len(results)))

        threading.Thread(target=worker, daemon=True).start()
        self._log(f"started batch: {len(jobs)} URL(s) → {out_dir}")

    def stop(self) -> None:
        self.cancel_event.set()
        self._log("stopping… (partial files kept, resumable)", "warn")

    def resume_from_gui(self) -> None:
        """Tools ▸ Resume unfinished downloads: retry every non-done record
        in the state file — the GUI twin of 'idm resume', with the same
        ghost rule: records whose file no longer exists are skipped (a
        retry could only fail), reported, and the log points at
        'idm prune-state' / right-click ▸ Prune stale records…."""
        if self.running:
            return
        out_dir = Path(self.out_var.get().strip() or "downloads")
        state = State(out_dir / "idm.state.json")
        records = state.records()
        if not records:
            messagebox.showinfo("PyIDM", f"Nothing to resume in {out_dir}.")
            return
        jobs = [(url, rec.get("filename")) for url, rec in records.items()
                if rec.get("status") != "done"]
        ghosts = {f["url"]
                  for f in scan_stale_records(out_dir / "idm.state.json")["records"]}
        skipped = [job for job in jobs if job[0] in ghosts]
        jobs = [job for job in jobs if job[0] not in ghosts]
        for url, filename in skipped:
            self._log(f"[skip] {filename or url} — file no longer exists "
                      "('idm prune-state' cleans up these records)", "warn")
        if not jobs:
            if skipped:
                messagebox.showwarning(
                    "PyIDM",
                    f"{len(skipped)} record(s) skipped — their file no longer "
                    f"exists in {out_dir}.\n\nRun 'idm prune-state' (or right-click "
                    "▸ Prune stale records…) to remove them from the state file.")
            else:
                messagebox.showinfo(
                    "PyIDM", f"Nothing to resume in {out_dir} — every record is done.")
            return
        self.cancel_event.clear()
        self.running = True
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")

        cfg = dict(self.cfg)
        cfg["out_dir"] = str(out_dir)
        cfg["segments"] = int(self.segments_var.get() or 8)
        workers = int(self.workers_var.get() or 4)

        def worker():
            dl = Downloader(
                cfg, out_dir=out_dir, workers=workers,
                progress_cb=lambda t: self.events.put(
                    ("task", t.url, t.filename, t.downloaded, t.total,
                     t.status, t.message, t.note)
                ),
                log_cb=lambda m, lvl="info": self.events.put(("log", m, lvl)),
                state=state,
                cancel_event=self.cancel_event,
            )
            results = dl.download_batch(jobs)
            ok = sum(1 for t in results if t.status in ("done", "skipped"))
            self.events.put(("batch_done", ok + len(skipped),
                             len(results) + len(skipped)))
            if skipped:
                self.events.put((
                    "log", (f"{len(skipped)} record(s) skipped (file missing) — "
                            "'idm prune-state' or right-click ▸ Prune stale "
                            "records… removes them"), "warn"))

        threading.Thread(target=worker, daemon=True).start()
        note = f" ({len(skipped)} skipped: file missing)" if skipped else ""
        self._log(f"resuming {len(jobs)} download(s) from {out_dir}{note}")

    # ---- quarantine workflow (right-click on the downloads tree) ----
    def _show_tree_menu(self, event) -> None:
        iid = self.tree.identify_row(event.y)
        if iid:
            self.tree.selection_set(iid)
        self.tree_menu.tk_popup(event.x_root, event.y_root)

    def _mark_moved(self, out_dir, payload, moves) -> None:
        """Reflect the sweep in the tree: a row for the moved URL/file
        gets the gray 'moved' badge; URLs unknown to the tree (e.g. files
        from older runs) get their own dim row at the end."""
        moved_names = {name for name, _ in moves}
        for url, iid in list(self.iids.items()):
            if self.tree.set(iid, "file") in moved_names:
                values = list(self.tree.item(iid, "values"))
                values[2] = "moved"
                self.tree.item(iid, values=values,
                               tags=("moved",))
                self.quar_iids[url] = iid
        shown = {self.tree.set(iid, "file") for iid in self.iids.values()}
        for f in payload["files"]:
            if f["kind"] == "moved" and f["file"] not in shown:
                iid = self.tree.insert("", "end", values=(
                    "(from an older run)", f["file"], "moved", "", "",
                    f"{f['size']} B", "quarantined — right-click ▸ Restore"))
                self.quar_iids[f"quarantine::{f['file']}"] = iid

    def verify_quarantine_from_gui(self) -> None:
        """Right-click ▸ Verify: magic-byte scan of the Save-to folder,
        moving mismatched files into quarantine — the GUI twin of
        'idm verify --delete-warned'. Runs in the background; per-file
        results arrive as 'quarantine_done' events."""
        out_dir = Path(self.out_var.get().strip() or "downloads")
        self.cfg = get_config()          # ignore-list edits may postdate app start

        def worker():
            try:
                payload, moves = move_warned_to_quarantine(
                    out_dir, ignore=verify_ignore_list(self.cfg))
            except OSError as e:
                self.events.put(("log", f"[error] verify failed: {e}",
                                 "error"))
                return
            self.events.put(("quarantine_done", out_dir, payload, moves))

        threading.Thread(target=worker, daemon=True).start()
        self._log(f"verifying files in {out_dir} (magic-byte scan)…")

    def restore_quarantine_from_gui(self, event=None) -> None:
        """Right-click ▸ Restore…: ask which files to bring back, then run
        it — the GUI twin of 'idm restore'. The file under the cursor (the
        row the menu was opened on, or a 'moved' row that was double-
        clicked) arrives preselected in the picker."""
        out_dir = Path(self.out_var.get().strip() or "downloads")
        quarantine = out_dir / "quarantine"
        available = ([p.name for p in sorted(quarantine.iterdir()) if p.is_file()]
                     if quarantine.is_dir() else [])
        if not available:
            messagebox.showinfo(
                "PyIDM", f"Nothing to restore — {quarantine} does not exist or is empty.")
            return
        clicked = (self.tree.focus() or None)
        _clicked: list[str] = []
        if clicked:
            fname = self.tree.set(clicked, "file")
            if fname in available:
                _clicked = [fname]
        picked = _ask_choose_items(
            "Restore from quarantine",
            f"Files in {quarantine}:", available,
            preselect=_clicked or None)
        if not picked:
            return  # cancelled or nothing selected

        def worker():
            payload = restore_from_quarantine(out_dir, names=picked)
            self.events.put(("restore_done", out_dir, payload, picked))

        threading.Thread(target=worker, daemon=True).start()
        self._log(f"restoring {len(picked)} file(s) from {quarantine}…")

    def _on_quarantine_done(self, out_dir, payload, moves) -> None:
        for f in payload["files"]:
            if f["kind"] == "warned":
                self._log(f"[warn] {f['file']}: {f['note']}", "warn")
        for name, target in moves:
            self._log(f"[quarantine] {name} -> {target}")
        # 'warned' counts files still in place; moved ones are reported
        # separately (they have been dealt with, but the user should know)
        flagged = payload["warned"] + payload.get("moved", 0)
        summary = (f"{payload['scanned']} file(s) scanned: "
                   f"{payload['ok']} clean, {payload['warned']} warned, "
                   f"{payload['skipped']} no opinion")
        if payload.get("moved"):
            summary += f", {payload['moved']} quarantined → {out_dir / 'quarantine'}"
        if payload.get("ignored"):
            summary += f", {payload['ignored']} on the ignore list"
        self._log(summary, "warn" if flagged else "info")
        self._mark_moved(out_dir, payload, moves)
        flagged_names = [f["file"] for f in payload["files"]
                         if f["kind"] in ("warned", "moved")]
        if flagged:
            messagebox.showwarning(
                "PyIDM",
                f"{payload['scanned']} file(s) scanned in {out_dir}:\n"
                f"{payload['ok']} clean, {flagged} flagged "
                "(content contradicts its name), "
                f"{payload['skipped']} no opinion\n\n"
                f"Quarantine folder: {out_dir / 'quarantine'}\n"
                "Right-click ▸ Restore quarantined files… to undo.")
            if flagged_names and messagebox.askyesno(
                    "PyIDM",
                    "Tired of these warnings? Add the flagged files to the "
                    "verify ignore list so future scans stay quiet about them "
                    "(right-click ▸ Manage verify ignore list… to undo)?\n\n"
                    + "\n".join(flagged_names[:8])
                    + (f"\n… and {len(flagged_names) - 8} more"
                       if len(flagged_names) > 8 else "")):
                self.cfg = get_config()
                verify_ignore_add(self.cfg, flagged_names)
                self.cfg = get_config()
                self._log("[ignore] " + ", ".join(flagged_names)
                          + " added to the verify ignore list", "warn")
        else:
            messagebox.showinfo(
                "PyIDM",
                f"{payload['scanned']} file(s) scanned in {out_dir}:\n"
                f"{payload['ok']} clean, {payload['skipped']} no opinion "
                "— nothing needed quarantining.")

    def _on_restore_done(self, out_dir, payload, names) -> None:
        handled = {f["file"] for f in payload["files"]
                   if f["action"] in ("restored", "discarded")}
        for url, iid in list(self.quar_iids.items()):
            fname = url.split("::", 1)[1] if url.startswith("quarantine::") \
                else self.tree.set(iid, "file")
            if fname not in handled:
                continue
            if url.startswith("quarantine::"):
                self.tree.delete(iid)          # synthetic row: no URL to revert
            else:
                values = list(self.tree.item(iid, "values"))
                values[2] = "done"
                values[6] = ""
                self.tree.item(iid, values=values, tags=("done",))
            del self.quar_iids[url]
        for f in payload["files"]:
            if f["action"] == "restored":
                self._log(f"[restore] {f['file']} -> {f['dest']}")
            elif f["action"] == "discarded":
                self._log(f"[discard] {f['file']}", "warn")
            else:
                self._log(f"[error] {f['file']}: {f['error']}", "error")
        if payload["restored"]:
            messagebox.showinfo(
                "PyIDM",
                f"{payload['restored']} file(s) restored to {out_dir} "
                f"({payload['failed']} failed).")
        # the restored files are back in the Save-to folder: re-derive the
        # rename badges (a restored file may itself carry a mangled name,
        # and rows fixed out-of-band lose their stale badge)
        self._refresh_rename_notes()

    # ---- prune-stale workflow (right-click on the downloads tree) ----
    def prune_stale_from_gui(self) -> None:
        """Right-click ▸ Prune stale records…: scan the state file for
        records whose file no longer exists in the Save-to folder — the
        GUI twin of 'idm prune-state' (shared engine in idm.state). The
        preview arrives as a 'prune_done' event; accepting the offer
        applies the prune in a background thread ('prune_apply_done')."""
        out_dir = Path(self.out_var.get().strip() or "downloads")

        def worker():
            try:
                payload = scan_stale_records(out_dir / "idm.state.json")
            except OSError as e:
                self.events.put(("log", f"[error] prune scan failed: {e}",
                                 "error"))
                return
            self.events.put(("prune_done", out_dir, payload))

        threading.Thread(target=worker, daemon=True).start()
        self._log(f"scanning {out_dir} for stale state records (dry run)…")

    def _apply_prune_from_gui(self, out_dir) -> None:
        """Remove the stale records for real (the '--apply' half); the
        result arrives as a 'prune_apply_done' event. Records are
        re-checked by the engine, so a file that reappeared in the
        meantime keeps its record."""
        def worker():
            try:
                payload, removed = prune_stale_records(out_dir / "idm.state.json")
            except OSError as e:
                self.events.put(("log", f"[error] prune failed: {e}", "error"))
                return
            self.events.put(("prune_apply_done", out_dir, payload, removed))

        threading.Thread(target=worker, daemon=True).start()
        self._log(f"removing stale records from {out_dir / 'idm.state.json'}…")

    def _on_prune_done(self, out_dir, payload) -> None:
        """Preview popup: nothing applied yet — the askyesno offers the
        '--apply' half (declining leaves the state file untouched)."""
        rows = payload["records"]
        if not rows:
            messagebox.showinfo(
                "PyIDM",
                f"{payload['scanned']} record(s) in {out_dir / 'idm.state.json'}\n"
                "— every file is still on disk. Nothing to prune.")
            return
        shown = "\n".join(f"{r['filename']}  ({r['status'] or 'no status'})"
                          for r in rows[:8])
        more = (f"\n… and {len(rows) - 8} more" if len(rows) > 8 else "")
        self._log(f"[stale] {len(rows)} record(s) whose file is gone: "
                  + ", ".join(r["filename"] for r in rows), "warn")
        if messagebox.askyesno(
                "PyIDM",
                f"{payload['scanned']} record(s) in {out_dir / 'idm.state.json'}, "
                f"{len(rows)} stale — the file no longer exists:\n\n"
                f"{shown}{more}\n\n"
                "Remove these records from the state file?\n"
                "Files are never touched, and a record whose file "
                "reappeared by apply time keeps its record."):
            self._apply_prune_from_gui(out_dir)

    def _on_prune_apply_done(self, out_dir, payload, removed) -> None:
        for r in payload["records"]:
            if r["removed"]:
                self._log(f"[prune] removed {r['filename']} — {r['url']}")
            else:
                self._log(f"[keep] {r['filename']} — file is back, record kept",
                          "warn")
        messagebox.showinfo(
            "PyIDM",
            f"{removed} stale record(s) removed from {out_dir / 'idm.state.json'} "
            f"({payload['live']} record(s) kept).")

    # ---- sanitize-names workflow (Tools menu) -----------------------
    def sanitize_names_from_gui(self) -> None:
        """Tools ▸ Sanitize downloads names (dry run): scan the Save-to
        folder for names a mangled Content-Disposition produced — the GUI
        twin of 'idm sanitize-names' (shared gui_common engine). The dry
        run arrives as a 'sanitize_done' event; accepting the offer applies
        the renames in a background thread ('sanitize_apply_done')."""
        out_dir = Path(self.out_var.get().strip() or "downloads")

        def worker():
            try:
                payload = scan_unclean_names(out_dir)
            except OSError as e:
                self.events.put(("log", f"[error] sanitize-names failed: {e}",
                                 "error"))
                return
            self.events.put(("sanitize_done", out_dir, payload))

        threading.Thread(target=worker, daemon=True).start()
        self._log(f"scanning {out_dir} for mangled file names (dry run)…")

    def _apply_name_fixes_from_gui(self, out_dir, payload) -> None:
        """Rename the dry run's would-rename rows in place (the '--apply'
        half); results arrive as a 'sanitize_apply_done' event."""
        def worker():
            try:
                fresh, renames = apply_name_fixes(out_dir, scan=payload)
            except OSError as e:
                self.events.put(("log", f"[error] sanitize-names failed: {e}",
                                 "error"))
                return
            self.events.put(("sanitize_apply_done", out_dir, fresh, renames))

        threading.Thread(target=worker, daemon=True).start()
        self._log("applying name fixes…")

    def _on_sanitize_done(self, out_dir, payload) -> None:
        for f in payload["files"]:
            self._log(f"[rename] {f['file']} -> {f['to']}", "warn")
        if not payload["unclean"]:
            self._log(f"all {payload['scanned']} file(s) in {out_dir} have "
                      "clean names")
            messagebox.showinfo(
                "PyIDM",
                f"All {payload['scanned']} file(s) in {out_dir} have clean names.")
            return
        self._log(f"{payload['unclean']} of {payload['scanned']} file(s) have "
                  "mangled names", "warn")
        if messagebox.askyesno(
                "PyIDM",
                f"{payload['unclean']} of {payload['scanned']} file(s) in {out_dir} "
                "have mangled names (a mangled Content-Disposition header gave "
                "them several extensions).\n\n"
                "Apply the renames now?"):
            self._apply_name_fixes_from_gui(out_dir, payload)

    def _on_sanitize_apply_done(self, out_dir, payload, renames) -> None:
        for name, to in renames:
            self._log(f"[rename] {name} -> {to}")
        for f in payload["files"]:
            if f["action"] == "failed":
                self._log(f"[error] {f['file']}: {f['error']}", "error")
        summary = (f"{payload['renamed']} file(s) renamed in {out_dir} "
                   f"({payload['failed']} failed).")
        if payload["failed"]:
            messagebox.showerror("PyIDM", summary)
        else:
            messagebox.showinfo("PyIDM", summary)
        self._refresh_rename_notes()          # stale rename badges, if any

    # ---- per-file rename (right-click on a violet 'rename' row) -----
    def fix_one_name_from_gui(self) -> None:
        """Right-click ▸ Fix this file name…: rename the focused row's file
        to the clean name the Note column previews — the per-file twin of
        'idm sanitize-names --apply' (same collision-safe rule, nothing is
        ever overwritten). Runs in the background; the result arrives as a
        'fix_name_done' event."""
        clicked = self.tree.focus()
        if not clicked:
            messagebox.showinfo("PyIDM", "Select a download row first.")
            return
        fname = self.tree.set(clicked, "file")
        out_dir = Path(self.out_var.get().strip() or "downloads")

        def worker():
            _renamed, _from, to, error = fix_one_name(out_dir, fname)
            self.events.put(("fix_name_done", out_dir, fname, to, error))

        threading.Thread(target=worker, daemon=True).start()
        self._log(f"renaming {fname} in {out_dir}…")

    def _on_fix_name_done(self, out_dir, fname, to, error) -> None:
        if error == "file is gone":
            self._log(f"[error] {fname}: file is gone", "error")
            messagebox.showerror("PyIDM", f"{fname}: file is gone.")
            return
        if error:
            self._log(f"[error] {fname} -> {to}: {error}", "error")
            messagebox.showerror("PyIDM", f"{fname} -> {to}:\n{error}")
            return
        if not to:
            self._log(f"[rename] {fname}: name already clean")
            messagebox.showinfo("PyIDM",
                                f"\"{fname}\" already has a clean name.")
            return
        self._log(f"[rename] {fname} -> {to}")
        messagebox.showinfo("PyIDM", f"Renamed {fname}\n        -> {to}")
        self._refresh_rename_notes()          # clear the badge on its row

    # ---- verify ignore-list (right-click on the downloads tree) -----
    def ignore_file_from_gui(self) -> None:
        """Right-click ▸ Ignore this file in verify…: add the focused row's
        file (or a pattern typed over it) to the config-driven verify
        ignore list, so the magic-byte scan stops warning about it."""
        fname = ""
        clicked = self.tree.focus()
        if clicked:
            fname = self.tree.set(clicked, "file") or ""
        suggestion = fname or "*.ext"
        answer = _ask_string(
            "Ignore in verify",
            "Files matching this entry are never warned about (or swept "
            "by Verify files): a plain name matches exactly, '*.ext' by "
            "extension, 'prefix*' by prefix.",
            initial=suggestion)
        if not answer:
            return  # cancelled or empty
        self.cfg = get_config()               # base the add on the current list
        verify_ignore_add(self.cfg, [answer])
        self.cfg = get_config()               # pick up the new key everywhere
        self._log(f"[ignore] {answer} added to the verify ignore list "
                  "(~/.idm/config.json)", "warn")
        messagebox.showinfo(
            "PyIDM",
            f"\"{answer}\" is now on the verify ignore list.\n"
            "Verify scans will not warn about matching files.")

    def manage_verify_ignore_from_gui(self) -> None:
        """Right-click ▸ Manage verify ignore list…: picker over the current
        entries to remove (nothing selected removes nothing); an empty list
        offers to add one instead."""
        self.cfg = get_config()               # entries may have changed on disk
        current = verify_ignore_list(self.cfg)
        if not current:
            self.ignore_file_from_gui()
            return
        picked = _ask_choose_items(
            "Manage verify ignore list",
            "Entries currently ignored by Verify scans — select the ones "
            "to remove (OK with nothing selected removes nothing):",
            current, preselect=[])
        if picked is None:
            return  # cancelled
        if picked:
            _current, removed = verify_ignore_remove(self.cfg, picked)
            self.cfg = get_config()           # pick up the new key everywhere
            for name in removed:
                self._log(f"[ignore] {name} removed from the verify ignore "
                          "list", "warn")
        messagebox.showinfo(
            "PyIDM",
            "Verify ignore list updated."
            + (f"\n\nRemoved: {', '.join(picked)}" if picked else ""))

    # ---- subtitle history interactions -----------------------------
    def _show_subs_menu(self, event) -> None:
        iid = self.subs_tree.identify_row(event.y)
        if iid:
            self.subs_tree.selection_set(iid)
            self.subs_menu.tk_popup(event.x_root, event.y_root)

    EXPORT_FILETYPES = [("CSV", "*.csv"), ("Markdown", "*.md"),
                        ("JSON", "*.json"),
                        ("All files", "*")]

    def _save_downloads_file(self) -> None:
        """Save the download-state records to a .csv, .md, or .json file,
        optionally through a jq-style --query — the GUI twin of
        'idm downloads [--fmt json] [--query Q]' (no date/status filters
        here: the CLI covers --status/--since/--until)."""
        got = self._open_downloads_export_dialog()
        if got is None:
            return  # cancelled
        fmt, query = got
        path = filedialog.asksaveasfilename(
            title="Export download state",
            defaultextension=".csv",
            initialfile=f"pyidm-downloads-{time.strftime('%Y%m%d')}.csv",
            filetypes=self.EXPORT_FILETYPES)
        if not path:
            return
        self._write_downloads_export(path, fmt, query)

    # -------------------------------------------------- query results window
    def _query_source_data(self, source: str):
        """The JSON-able rows a results window evaluates queries against:
        the subtitle history for 'history', the download-state records from
        the Save-to directory for 'downloads' (the same sources the CLI's
        'idm history --query' and 'idm downloads --query' read)."""
        if source == "downloads":
            out_dir = Path(self.out_var.get().strip() or "downloads")
            return download_json_records(
                read_state_records(out_dir / "idm.state.json"))
        return history_json_entries(load_subs_history())

    # ------------------------------------------------------------- event loop
    def _poll(self) -> None:
        try:
            while True:
                ev = self.events.get_nowait()
                kind = ev[0]
                if kind == "task":
                    self._on_task(*ev[1:])
                elif kind == "log":
                    self._log(ev[1], ev[2])
                elif kind == "batch_done":
                    ok, total = ev[1], ev[2]
                    self.running = False
                    self.start_btn.config(state="normal")
                    self.stop_btn.config(state="disabled")
                    self._log(f"batch finished: {ok}/{total} succeeded", "info")
                    if self.cancel_event.is_set():
                        self._log("was cancelled — press Start again or run 'idm resume' to finish the rest", "warn")
                elif kind == "subs_done":
                    self._on_subs_done(*ev[1:])
                elif kind == "providers_done":
                    self._on_providers_done(ev[1])
                elif kind == "report_done":
                    self._on_report_done(*ev[1:])
                elif kind == "quarantine_done":
                    self._on_quarantine_done(ev[1], ev[2], ev[3])
                elif kind == "restore_done":
                    self._on_restore_done(ev[1], ev[2], ev[3])
                elif kind == "prune_done":
                    self._on_prune_done(ev[1], ev[2])
                elif kind == "prune_apply_done":
                    self._on_prune_apply_done(ev[1], ev[2], ev[3])
                elif kind == "sanitize_done":
                    self._on_sanitize_done(ev[1], ev[2])
                elif kind == "sanitize_apply_done":
                    self._on_sanitize_apply_done(ev[1], ev[2], ev[3])
                elif kind == "fix_name_done":
                    self._on_fix_name_done(ev[1], ev[2], ev[3], ev[4])
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _on_task(self, url, filename, downloaded, total, status, message, note="") -> None:
        iid = self.iids.get(url)
        if iid is None:
            iid = self.tree.insert("", "end", values=(url, filename or "…", status, "", "", "", ""))
            self.iids[url] = iid
        # speed estimate
        now = time.monotonic()
        last = self.speed.get(url)
        speed_txt = ""
        if last:
            dt, db = now - last[0], max(0, downloaded - last[1])
            if dt > 0.2:
                speed_txt = f"{human_size(db / dt)}/s"
                self.speed[url] = (now, downloaded)
        else:
            self.speed[url] = (now, downloaded)

        pct = ""
        if total:
            pct = f"{min(100, downloaded * 100 // total)}%"
        elif status == "done":
            pct = "100%"
        self.tree.set(iid, "file", filename or "…")
        self.tree.set(iid, "status", status)
        self.tree.set(iid, "progress", pct)
        self.tree.set(iid, "size", f"{human_size(downloaded)}" + (f" / {human_size(total)}" if total else ""))
        self.tree.set(iid, "speed", speed_txt)
        self.tree.set(iid, "note", note or "")
        tags = [status] if status in STATUS_COLORS else []
        if note:
            tags.append("warned")  # last tag wins: amber beats the status color
        self.tree.item(iid, tags=tuple(tags))
        self._update_aggregate()

    def _update_aggregate(self) -> None:
        done = 0
        for iid in self.iids.values():
            v = self.tree.set(iid)
            if v.get("status") in ("done", "skipped"):
                done += 1
        self.aggregate["maximum"] = max(1, len(self.iids))
        self.aggregate["value"] = done
        self.agg_label.config(text=f"{done} / {len(self.iids)} finished")

    def _log(self, msg: str, level: str = "info") -> None:
        self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")


class App(AboutDialogsMixin, ExportDialogsMixin, QueryDialogsMixin,
         SubtitlesPanelMixin, _AppBase):
    """The Tk application: behaviour lives in the mixins above and in
    _AppBase (the original App, kept here so both stay in gui.py's
    namespace and tests can patch gui-layer helpers it uses)."""


def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
