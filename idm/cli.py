"""PyIDM command-line interface."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import requests
from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TaskID,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.table import Table

from . import __version__
from .config import (
    clear_user_env,
    get_config,
    get_user_env,
    load_user_config,
    mask_secret,
    set_config_value,
    set_user_env,
)
from .core import Downloader, DownloadTask
from .gui import _PRESET_KINDS, batch_payload
from .jq import JqError, apply_query, emit_json, print_result
from .links import find_provider, resolve_url
from .state import State
from .subtitles import (
    SubtitleError,
    batch_for_folder,
    download_for_title,
    download_for_video,
)
from .utils import clipboard_text, human_size, parse_job_lines, write_text_newlines

console = Console(highlight=False)

STATUS_STYLES = {
    "done": "green",
    "error": "red",
    "expired": "red",
    "cancelled": "yellow",
    "skipped": "yellow",
    "pending": "dim",
    "downloading": "cyan",
}


def _emit_json(payload, args) -> int:
    """Shared JSON emit for every command: without --query, print the full
    payload exactly as the plain --json mode always has; with --query,
    evaluate it jq-style and print the single-line result (--query implies
    --json). JqError -> red message, exit 1."""
    if getattr(args, "query", None):
        try:
            result = apply_query(args.query, payload)
        except JqError as e:
            console.print(f"[red]invalid --query: {e}[/red]")
            return 1
        print_result(result, getattr(args, "raw", False))
        return 0
    indent = 2 if isinstance(payload, (dict, list)) else None
    sys.stdout.write(emit_json(payload, indent=indent) + "\n")
    return 0


def _run_jobs(jobs, args, cfg, skipped=None) -> int:
    if not jobs:
        if getattr(args, "json", False) or getattr(args, "query", None):
            return _emit_json(batch_payload([]), args)
        console.print("[yellow]no URLs to download[/yellow]")
        return 0
    out_dir = Path(args.out) if args.out else Path(cfg.get("out_dir") or "downloads")
    state = State(out_dir / "idm.state.json")

    progress = Progress(
        TextColumn("[progress.description]{task.fields[name]}"),
        BarColumn(bar_width=22),
        TaskProgressColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
    )
    task_ids: dict[str, TaskID] = {}

    def on_progress(t: DownloadTask) -> None:
        label = (t.filename or t.url)[:46]
        tid = task_ids.get(t.url)
        if tid is None:
            task_ids[t.url] = progress.add_task("start", total=t.total, name=label)
            tid = task_ids[t.url]
        progress.update(tid, total=t.total, completed=t.downloaded, name=label)

    def on_log(msg: str, level: str = "info") -> None:
        if getattr(args, "json", False) or getattr(args, "query", None):
            return  # --json/--query: keep stdout parseable; errors surface in the summary
        style = {"warn": "yellow", "error": "red"}.get(level, "dim")
        console.print(msg, style=style)

    dl = Downloader(
        cfg, out_dir=out_dir, workers=args.workers, segments=args.segments,
        retries=args.retries,
        timeout=args.timeout, overwrite=args.overwrite,
        progress_cb=on_progress, log_cb=on_log, state=state,
    )
    if getattr(args, "json", False) or getattr(args, "query", None):
        # Quiet JSON mode: no progress bars or log lines — stdout stays
        # parseable and the JSON summary alone carries the results. Ghost
        # records skipped before the run are reported, never retried.
        results = dl.download_batch(jobs)
        rc = _emit_json(batch_payload(results, skipped=skipped), args)
        if rc:
            return rc  # bad --query
        ok = sum(1 for t in results if t.status in ("done", "skipped"))
        return 0 if ok == len(results) else 1

    with progress:
        results = dl.download_batch(jobs)

    table = Table(title=f"Batch summary — {out_dir}")
    table.add_column("Status")
    table.add_column("File")
    table.add_column("Size", justify="right")
    table.add_column("Note", overflow="fold")
    for t in results:
        table.add_row(
            f"[{STATUS_STYLES.get(t.status, '')}]{t.status}[/]",
            t.filename or t.url,
            human_size(t.downloaded),
            t.message if t.status not in ("done", "skipped") else "",
        )
    console.print(table)
    ok = sum(1 for t in results if t.status in ("done", "skipped"))
    console.print(f"{ok}/{len(results)} succeeded. State file: {state.path}")
    return 0 if ok == len(results) else 1


def cmd_get(args, cfg) -> int:
    jobs = [(u, args.name if i == 0 else None) for i, u in enumerate(args.urls)]
    return _run_jobs(jobs, args, cfg)


def cmd_batch(args, cfg) -> int:
    if args.clipboard:
        text = clipboard_text()
    else:
        text = Path(args.source).read_text(encoding="utf-8", errors="replace")
    jobs = parse_job_lines(text)
    if not jobs:
        if getattr(args, "json", False):
            # same exit code as text mode (an empty list is an input error),
            # but still emit the JSON shape so scripts can parse the result
            _emit_json(batch_payload([]), args)
            return 1
        console.print("[red]no URLs found in the batch list[/red]")
        return 1
    if not getattr(args, "json", False) and not getattr(args, "query", None):
        console.print(f"[bold]{len(jobs)} URL(s) queued[/bold]")
    return _run_jobs(jobs, args, cfg)


def cmd_resume(args, cfg) -> int:
    out_dir = Path(args.out) if args.out else Path(cfg.get("out_dir") or "downloads")
    state = State(out_dir / "idm.state.json")
    records = state.records()
    json_mode = getattr(args, "json", False) or getattr(args, "query", None)
    if not records:
        if json_mode:
            return _emit_json(batch_payload([]), args)   # zero payload, exit 0
        console.print(f"[green]nothing to resume in {out_dir}[/green]")
        return 0
    jobs = [(url, rec.get("filename")) for url, rec in records.items()
            if rec.get("status") != "done"]
    # ghosts: unfinished records whose file is gone — a retry can only fail
    # (or restart from zero somewhere else); skip them and point at prune
    from .state import scan_stale_records
    ghosts = [f["url"] for f in scan_stale_records(out_dir / "idm.state.json")
              ["records"]]
    skipped = [job for job in jobs if job[0] in set(ghosts)]
    jobs = [job for job in jobs if job[0] not in set(ghosts)]
    if not jobs:
        if json_mode:
            # all ghosts (or nothing unfinished): zero payload that still
            # names the skipped records — exit 0, nothing was retried
            return _emit_json(batch_payload([], skipped=skipped), args)
        if skipped:
            console.print(f"[yellow]{len(skipped)} record(s) skipped — their file "
                          "no longer exists; clean them up with "
                          "'idm prune-state'[/yellow]")
        else:
            console.print(f"[green]nothing to resume in {out_dir}[/green]")
        return 0
    if not json_mode:
        console.print(f"[bold]resuming {len(jobs)} download(s) from {out_dir}[/bold]")
        for url, filename in skipped:
            console.print(f"[skip] {filename or url} — file no longer exists "
                          "('idm prune-state' cleans up these records)",
                          style="yellow", markup=False, highlight=False)
    return _run_jobs(jobs, args, cfg, skipped=skipped)


def cmd_refresh(args, cfg) -> int:
    provider = find_provider(args.url, cfg.get("link_providers") or [])
    if not provider:
        console.print("[red]no link provider matches this URL — add one to idm.json "
                      "(link_providers) or ~/.idm/config.json[/red]")
        return 1
    fresh = resolve_url(args.url, provider, timeout=cfg.get("timeout", 30))
    console.print("[green]fresh URL:[/green]", fresh)
    return 0


def cmd_subs(args, cfg) -> int:
    from .players import launch_with_subtitle

    langs = args.langs or cfg.get("subtitle_languages") or "en"
    if getattr(args, "player", None):
        cfg = dict(cfg)
        cfg["player"] = args.player
    target = Path(args.target)
    try:
        if target.is_dir():
            results = batch_for_folder(target, cfg, langs, overwrite=args.overwrite, log=_subs_log)
        elif target.exists():
            results = [download_for_video(target, cfg, langs, overwrite=args.overwrite, log=_subs_log)]
        else:
            results = [download_for_title(args.target, cfg, langs, out_dir=args.out,
                                          overwrite=args.overwrite, log=_subs_log)]
    except SubtitleError as e:
        console.print(f"[red]{e}[/red]")
        return 1
    except requests.RequestException as e:
        console.print(f"[red]OpenSubtitles request failed: {e}[/red]")
        console.print("[dim]check your API key (idm config set opensubtitles_api_key KEY) "
                      "and your internet connection[/dim]")
        return 1

    if not getattr(args, "no_play", False):
        for r in results:
            if r.get("ok") and r.get("dest"):
                info = launch_with_subtitle(r["path"], r["dest"], cfg)
                copied = f" (sidecar: {Path(info['copied']).name})" if info.get("copied") else ""
                console.print(f"[cyan]now playing[/cyan] {Path(r['path']).name} in {info['player']}{copied}")
    return 0 if all(r.get("ok") for r in results) else 1


def _subs_log(msg: str, level: str = "info") -> None:
    style = {"warn": "yellow", "error": "red"}.get(level, "")
    console.print(msg, style=style or None)


def _open_exported(path, viewer: str = "") -> None:
    """Open a freshly written export — with the chosen --viewer app, or the
    default app when no viewer is given. A failed open (app not found, no
    association, shell error) warns but never fails the export."""
    from .gui import open_file_safe, open_file_with

    how = f" in {viewer}" if viewer else " in the default app"
    opened = open_file_with(path, viewer) if viewer else open_file_safe(path)
    if opened:
        console.print(f"[dim]opened {path}{how} (--quiet to suppress)[/dim]")
    else:
        tail = f" '{viewer}'" if viewer else ""
        console.print(f"[yellow]could not open {path} with{tail or ' any app'} — "
                      "the export itself succeeded[/yellow]")


def cmd_subs_history(args, cfg) -> int:
    """List or export the persisted subtitle history using the same data
    pipeline and writers as the GUI history panel."""
    from .gui import (
        expand_preset_out,
        export_preview,
        export_preview_json,
        filter_history,
        history_json_entries,
        history_to_csv,
        history_to_json,
        history_to_markdown,
        load_presets,
        load_subs_history,
        subtitle_rows,
    )

    preset = getattr(args, "preset", None)
    if preset:
        p = load_presets("history").get(preset)
        if p is None:
            console.print(f"[red]unknown preset: {preset} — "
                          "see 'idm presets list'[/red]")
            return 1
        # explicit flags win over the preset; empty preset fields mean 'no filter'
        args.provider = (args.provider if args.provider is not None
                         else p.get("provider") or "")
        args.since = args.since if args.since is not None else p.get("since") or ""
        args.until = args.until if args.until is not None else p.get("until") or ""
        if not getattr(args, "query", None) and p.get("query"):
            args.query = p["query"]
        # a preset may pin an output file (and viewer); the pinned path is
        # relative to the current working directory; {date}/{kind} expand
        if not args.out and isinstance(p.get("out"), str) and p["out"]:
            args.out = expand_preset_out(p["out"], "history")
        if not getattr(args, "viewer", "") and isinstance(p.get("viewer"), str) \
                and p["viewer"]:
            args.viewer = p["viewer"]

    try:
        history = filter_history(load_subs_history(), args.provider,
                                 args.since, args.until)
    except ValueError as e:
        console.print(f"[red]invalid --since/--until: {e}[/red]")
        return 1
    rows = subtitle_rows(history)
    fmt = "json" if (getattr(args, "json", False)
                     or getattr(args, "query", None)) else args.fmt
    if args.out and fmt == "table":
        # --fmt omitted: the output extension picks the format (as in --help)
        fmt = ("markdown" if str(args.out).lower().endswith((".md", ".markdown"))
               else ("json" if str(args.out).lower().endswith(".json") else "csv"))
        if args.json:
            fmt = "json"  # explicit --json wins over the extension
    if args.rows < 0:
        console.print(f"[red]invalid --rows: {args.rows} (must be >= 0; "
                      "0 hides the preview)[/red]")
        return 1
    if args.out:
        n = len(rows)
        if fmt == "csv":
            text = history_to_csv(rows)
        elif fmt == "markdown":
            text = history_to_markdown(rows)
        elif getattr(args, "query", None):
            # --query changes what a .json export holds: the query result,
            # exactly as 'idm history --query ...' would print it
            try:
                result = apply_query(args.query, history_json_entries(history))
            except JqError as e:
                console.print(f"[red]invalid --query: {e}[/red]")
                return 1
            text = emit_json(result, indent=2)
            n = len(result) if isinstance(result, list) else 1
        else:
            text = history_to_json(history_json_entries(history))
        # newline="" keeps the CSV's \r\n byte-exact on Windows; mkdir so
        # templated preset paths like reports/{date}-... can add folders
        _dest = Path(args.out)
        _dest.parent.mkdir(parents=True, exist_ok=True)
        write_text_newlines(_dest, text)
        scope = ", ".join(s for s in (
            f"provider={args.provider}" if args.provider else "",
            f"since {args.since}" if args.since else "",
            f"until {args.until}" if args.until else "",
            f"preset {args.preset}" if getattr(args, "preset", None) else "") if s)
        console.print(f"[green]exported[/green] {n} row(s) -> {args.out} "
                      f"({fmt}{', ' + scope if scope else ''})")
        # with --query the JSON payload can be any shape (scalars, mixed),
        # so use the row-based previewer there; export_preview_json would
        # only handle its list-of-objects layout
        preview = (export_preview_json(text, getattr(args, "rows", 3))
                   if fmt == "json" and not getattr(args, "query", None)
                   else export_preview(text, getattr(args, "rows", 3)))
        console.print(f"[dim]preview: {preview}[/dim]")
        if not getattr(args, "quiet", False):
            _open_exported(args.out, getattr(args, "viewer", "") or "")
        return 0
    if fmt == "csv":
        sys.stdout.write(history_to_csv(rows))
        return 0
    if fmt == "markdown":
        sys.stdout.write(history_to_markdown(rows))
        return 0
    if fmt == "json":
        return _emit_json(history_json_entries(history), args)
    table = Table(show_header=True, header_style="bold")
    for col in ("video", "lang", "provider", "size", "cues"):
        table.add_column(col)
    for row in rows:
        table.add_row(*row)
    console.print(table)
    note = f"{len(rows)} entr{'y' if len(rows) == 1 else 'ies'}"
    if args.provider or args.since or args.until:
        note += " (filtered)"
    console.print(f"[dim]{note} — ~/.idm/subtitle_history.json[/dim]")
    return 0


def cmd_downloads(args, cfg) -> int:
    """List or export the download state file (what 'idm resume' would retry),
    reusing the same CSV/markdown writers and date filters as 'idm history'."""
    from .gui import (
        download_json_records,
        download_rows,
        download_to_csv,
        download_to_markdown,
        expand_preset_out,
        export_preview,
        filter_downloads,
        load_presets,
    )

    preset = getattr(args, "preset", None)
    if preset:
        p = load_presets("downloads").get(preset)
        if p is None:
            console.print(f"[red]unknown preset: {preset} — "
                          "see 'idm presets list'[/red]")
            return 1
        # explicit flags win over the preset; fmt applies only to stdout
        # formats (file exports pick their format from the extension)
        if args.fmt == "table" and p.get("fmt") in ("csv", "markdown", "json") \
                and not args.export:
            args.fmt = p["fmt"]
        if not getattr(args, "query", None) and p.get("query"):
            args.query = p["query"]
        # a preset may pin an export file (and viewer): the pinned path is
        # relative to the current working directory; {date}/{kind} expand.
        # NB: for downloads the export file is args.export ('--out' is its
        # alias at the subcommand level); args.out is the state directory.
        if not args.export and isinstance(p.get("out"), str) and p["out"]:
            args.export = expand_preset_out(p["out"], "downloads")
        if not getattr(args, "viewer", "") and isinstance(p.get("viewer"), str) \
                and p["viewer"]:
            args.viewer = p["viewer"]

    out_dir = Path(args.out) if args.out else Path(cfg.get("out_dir") or "downloads")
    state_path = out_dir / "idm.state.json"
    state = State(state_path)
    try:
        records = filter_downloads(state.records(), args.status,
                                   args.since, args.until)
    except ValueError as e:
        console.print(f"[red]invalid --since/--until: {e}[/red]")
        return 1
    rows = download_rows(records)
    if args.rows < 0:
        console.print(f"[red]invalid --rows: {args.rows} (must be >= 0; "
                      "0 hides the preview)[/red]")
        return 1
    fmt = args.fmt
    if args.export and fmt == "table":
        # --fmt omitted: the output extension picks the format (as in --help)
        fmt = ("markdown" if str(args.export).lower().endswith((".md", ".markdown"))
               else ("json" if str(args.export).lower().endswith(".json")
                     else "csv"))
    if args.export:
        if fmt == "csv":
            text = download_to_csv(rows)
            n = len(rows)
        elif fmt == "markdown":
            text = download_to_markdown(rows)
            n = len(rows)
        else:
            # .json extension (or --json/--query): the query result when
            # --query is active, else the raw state records — same data as --json
            try:
                result = (apply_query(args.query, records)
                          if getattr(args, "query", None)
                          else download_json_records(records))
            except JqError as e:
                console.print(f"[red]invalid --query: {e}[/red]")
                return 1
            text = emit_json(result, indent=2)
            n = len(result) if isinstance(result, list) else 1
        # newline="" keeps the CSV's \r\n byte-exact on Windows; mkdir so
        # templated preset paths like reports/{date}-... can add folders
        _dest = Path(args.export)
        _dest.parent.mkdir(parents=True, exist_ok=True)
        write_text_newlines(_dest, text)
        scope = ", ".join(s for s in (
            f"status={args.status}" if args.status else "",
            f"since {args.since}" if args.since else "",
            f"until {args.until}" if args.until else "",
            f"preset {args.preset}" if getattr(args, "preset", None) else "") if s)
        console.print(f"[green]exported[/green] {n} row(s) -> {args.export} "
                      f"({fmt}{', ' + scope if scope else ''})")
        console.print(f"[dim]preview: {export_preview(text, getattr(args, 'rows', 3))}[/dim]")
        if not getattr(args, "quiet", False):
            _open_exported(args.export, getattr(args, "viewer", "") or "")
        return 0
    if fmt == "csv":
        sys.stdout.write(download_to_csv(rows))
        return 0
    if fmt == "markdown":
        sys.stdout.write(download_to_markdown(rows))
        return 0
    if getattr(args, "json", False) or getattr(args, "query", None):
        return _emit_json(download_json_records(records), args)
    table = Table(show_header=True, header_style="bold")
    for col in ("url", "filename", "status", "size", "updated"):
        table.add_column(col, overflow="fold")
    for url, filename, status, size, updated in rows:
        table.add_row(url, filename,
                      f"[{STATUS_STYLES.get(status, '')}]{status}[/]",
                      size, updated)
    console.print(table)
    note = f"{len(rows)} unfinished download{'s' if len(rows) != 1 else ''}"
    if args.status or args.since or args.until:
        note += " (filtered)"
    console.print(f"[dim]{note} — {state_path} (resume with: idm resume -o {out_dir})[/dim]")
    return 0


def cmd_presets(args, cfg) -> int:
    """List / add / remove the named export presets shared with the GUI
    export dialogs ('presets' in ~/.idm/export_prefs.json)."""
    from .gui import (
        _PRESET_SETTINGS,
        delete_preset,
        export_presets,
        import_presets,
        load_presets,
        save_preset,
    )

    kind = args.preset_kind
    # the file may be given in the name or file position ('presets export
    # share.json' reads naturally); --file also works
    file = args.file or args.name
    if args.action == "export":
        if not file:
            console.print("[red]presets export needs a file path[/red]")
            return 1
        n = export_presets(file)
        console.print(f"[green]exported[/green] {n} preset(s) -> {file}")
        return 0
    if args.action == "import":
        if not file:
            console.print("[red]presets import needs a file path[/red]")
            return 1
        imported, skipped, readable = import_presets(file,
                                                     replace=args.replace)
        if not readable:
            console.print(f"[red]could not read presets from {file}[/red]")
            return 1
        mode = "replaced" if args.replace else "merged"
        console.print(f"[green]imported[/green] {imported} preset(s) "
                      f"({mode}; {skipped} skipped)")
        return 0
    if args.action == "add":
        settings = {}
        for kv in (args.set or []):
            k, _, v = kv.partition("=")
            k = k.strip()
            if k not in _PRESET_SETTINGS[kind]:
                console.print(f"[red]unknown setting for {kind} presets: {k!r} "
                              f"(valid: {', '.join(_PRESET_SETTINGS[kind])})[/red]")
                return 1
            settings[k] = v
        if save_preset(kind, args.name, **settings):
            console.print(f"[green]preset '{args.name}' saved[/green] "
                          f"({kind}: {settings or '{}'})")
            return 0
        console.print(f"[red]invalid preset name: {args.name!r}[/red]")
        return 1
    if args.action == "remove":
        if delete_preset(kind, args.name):
            console.print(f"[green]preset '{args.name}' removed[/green]")
            return 0
        console.print(f"[red]unknown preset: {args.name!r} — see "
                      "'idm presets list'[/red]")
        return 1

    # list (default action)
    if getattr(args, "json", False) or getattr(args, "query", None):
        payload = ({kind: load_presets(kind)} if kind != "all"
                   else {k: load_presets(k) for k in _PRESET_KINDS})
        return _emit_json(payload, args)
    presets = ({kind: load_presets(kind)} if kind != "all"
               else {k: load_presets(k) for k in _PRESET_KINDS})
    any_rows = False
    for k in _PRESET_KINDS:
        slot = presets.get(k) or {}
        if not slot:
            continue
        any_rows = True
        for name, p in slot.items():
            desc = ", ".join(f"{key}={p[key]}" for key in sorted(p) if p[key])
            console.print(f"[bold]{k}/{name}[/bold]  {desc or '(no settings)'}")
    if not any_rows:
        console.print("[dim]no presets saved — create one with "
                      "'idm presets add downloads NAME query=[].url' or "
                      "the GUI export dialogs[/dim]")
        return 0
    console.print("[dim]use with: idm history --preset NAME | "
                  "idm downloads --preset NAME[/dim]")
    return 0


def cmd_stats(args, cfg) -> int:
    """One-glance summary of both stores: counts, bytes, per-status/provider
    breakdown, oldest entries — the same numbers the GUI status lines show,
    with a --json mode for scripts."""
    import time as _time

    from .gui import (
        SUBS_HISTORY_FILE,
        downloads_status,
        history_status,
        load_subs_history,
        stats_payload,
    )

    out_dir = Path(args.out) if args.out else Path(cfg.get("out_dir") or "downloads")
    state_path = out_dir / "idm.state.json"

    def _load() -> tuple[dict, list, Path, Path, Path]:
        """Fresh look at both stores every tick (and for --json)."""
        recs = State(state_path).records()         # missing file -> {}
        hist = load_subs_history()                 # missing file -> []
        return recs, hist, out_dir, state_path, SUBS_HISTORY_FILE

    if args.watch is not None:
        if args.watch < 1:
            console.print(f"[red]invalid --watch: {args.watch} (must be >= 1 "
                          "seconds)[/red]")
            return 1
        if args.json or getattr(args, "query", None):
            console.print("[red]--watch re-prints the text summary; for JSON, "
                          "run 'idm stats --json' in your own loop instead[/red]")
            return 1
        if getattr(args, "export", None) or getattr(args, "preset", None):
            console.print("[red]--watch re-prints the text summary; --export/--preset "
                          "write a file — drop --watch instead[/red]")
            return 1
        console.print(f"[dim]watching every {args.watch}s — Ctrl+C to stop[/dim]")
        first = True
        while True:
            if not first:
                _time.sleep(args.watch)
            first = False
            recs, hist, _out_dir, _state_path, hist_path = _load()
            console.clear()
            console.print(f"[bold]PyIDM stats[/bold] — "
                          f"{_time.strftime('%Y-%m-%d %H:%M:%S')}")
            console.print(f"[bold]Downloads[/bold] — {downloads_status(recs)}")
            console.print(f"[dim]{state_path} (resume with: idm resume -o {out_dir})[/dim]")
            console.print(f"[bold]Subtitles[/bold] — {history_status(hist)}")
            console.print(f"[dim]{hist_path} (list with: idm history)[/dim]")

    records, history, out_dir, state_path, _hist = _load()

    # --preset applies after the filters it owns (query/out/viewer here —
    # stats has none beyond them); explicit flags win, exactly as on
    # history/downloads. The preset's kind is 'stats'.
    if getattr(args, "preset", None):
        from .gui import expand_preset_out, load_presets
        p = load_presets("stats").get(args.preset)
        if p is None:
            console.print(f"[red]unknown preset: {args.preset} — "
                          "see 'idm presets list'[/red]")
            return 1
        if not getattr(args, "query", None) and p.get("query"):
            args.query = p["query"]
        if not args.export and isinstance(p.get("out"), str) and p["out"]:
            args.export = expand_preset_out(p["out"], "stats")
        if not getattr(args, "viewer", "") and isinstance(p.get("viewer"), str) \
                and p["viewer"]:
            args.viewer = p["viewer"]

    payload = stats_payload(history, records)
    payload["downloads"]["path"] = str(state_path)
    payload["subtitles"]["path"] = str(SUBS_HISTORY_FILE)

    # --export wins over stdout (as on downloads): a pinned/CLI --query
    # narrows the file's contents to the query result (any JSON shape,
    # --query semantics); without one the file holds the full summary
    if args.export:
        try:
            result = (apply_query(args.query, payload)
                      if getattr(args, "query", None) else payload)
        except JqError as e:
            console.print(f"[red]invalid --query: {e}[/red]")
            return 1
        _dest = Path(args.export)
        _dest.parent.mkdir(parents=True, exist_ok=True)
        write_text_newlines(_dest, emit_json(result, indent=2) + "\n")
        scope = ", ".join(s for s in (
            f"query {args.query}" if getattr(args, "query", None) else "",
            f"preset {args.preset}" if getattr(args, "preset", None) else "")
            if s)
        console.print(f"[green]exported[/green] stats report -> {args.export}"
                      f"{f' ({scope})' if scope else ''}")
        if not getattr(args, "quiet", False):
            _open_exported(args.export, getattr(args, "viewer", "") or "")
        return 0

    if getattr(args, "json", False) or getattr(args, "query", None):
        return _emit_json(payload, args)

    console.print(f"[bold]Downloads[/bold] — {downloads_status(records)}")
    console.print(f"[dim]{state_path} (resume with: idm resume -o {out_dir})[/dim]")
    console.print(f"[bold]Subtitles[/bold] — {history_status(history)}")
    console.print(f"[dim]{SUBS_HISTORY_FILE} (list with: idm history)[/dim]")
    return 0


def cmd_prune_state(args, cfg) -> int:
    """'idm prune-state': remove download-state records whose file no longer
    exists in the downloads folder — the ghosts 'resume' retries forever
    and 'stats' counts forever. A dry-run preview by default; --apply
    removes the previewed records from idm.state.json (never touching any
    file). Records are re-checked at apply time, so a file that reappeared
    between preview and apply keeps its record. --vacuum rewrites the
    store without indentation while it is open anyway and reports the
    bytes reclaimed (works on its own, with zero stale records)."""
    from .state import prune_stale_records, scan_stale_records

    out_dir = Path(args.out) if args.out else Path(cfg.get("out_dir") or "downloads")
    state_path = out_dir / "idm.state.json"
    jsonish = bool(getattr(args, "json", False) or getattr(args, "query", None))
    do_apply = bool(getattr(args, "apply", False) or getattr(args, "vacuum", False))
    compact = bool(getattr(args, "vacuum", False))

    if do_apply:
        payload, removed = prune_stale_records(state_path, compact=compact)
    else:
        payload = scan_stale_records(state_path)
    rows = payload["records"]

    if jsonish:
        rc = _emit_json(payload, args)
        if rc:                       # e.g. a bad --query: error already shown
            return rc
        return 0                     # prune never fails: files are untouched

    if not rows and not do_apply:
        console.print(f"[green]{payload['scanned']} record(s) in {state_path} — "
                      "every file is still on disk[/green]")
        return 0
    for r in rows:
        if r["removed"]:
            console.print(f"[prune] {r['filename']} — removed ({r['url']})",
                          style="cyan", markup=False, highlight=False)
        elif do_apply:
            console.print(f"[keep] {r['filename']} — file is back, record kept",
                          style="green", markup=False, highlight=False)
        else:
            console.print(f"[stale] {r['filename']} ({r['status'] or 'no status'}) "
                          f"— {r['url']}", style="yellow",
                          markup=False, highlight=False)
    if do_apply:
        tail = f", {payload['live']} record(s) kept"
        if compact:
            if payload["bytes_reclaimed"] is None:
                tail += " — store compacted: nothing to compact (no state file)"
            else:
                tail += (f" — store compacted: "
                         f"{payload['state_bytes_before']} -> "
                         f"{payload['state_bytes_after']} bytes "
                         f"({payload['bytes_reclaimed']} reclaimed)")
        console.print(f"\n{removed} stale record(s) removed from {state_path}"
                      f"{tail}")
    else:
        console.print(f"\n{payload['stale']} of {payload['scanned']} record(s) stale "
                      "(dry run — pass --apply to remove them)")
    return 0


def cmd_config(args, cfg) -> int:
    if args.action in (None, "list"):
        console.print_json(json.dumps(load_user_config() or {}, indent=2))
        console.print("[dim]^ contents of ~/.idm/config.json (merged over defaults)[/dim]")
        key = get_user_env("OPENSUBTITLES_API_KEY")
        if key:
            console.print(f"[dim]env OPENSUBTITLES_API_KEY = {mask_secret(key)} "
                          "(Windows user environment)[/dim]")
        else:
            console.print("[dim]env OPENSUBTITLES_API_KEY is not set "
                          "(keyless providers still work)[/dim]")
        return 0
    if args.action == "get":
        value = cfg.get(args.key)
        console.print_json(json.dumps(value))
        return 0
    if args.action == "set":
        if not args.key or args.value is None:
            console.print("[red]usage: idm config set KEY VALUE[/red]")
            return 1
        set_config_value(args.key, args.value)
        if args.key == "opensubtitles_api_key" and args.value:
            set_user_env("OPENSUBTITLES_API_KEY", args.value)
            console.print("[dim]also stored as Windows user environment variable "
                          "OPENSUBTITLES_API_KEY (for the packaged exes)[/dim]")
        console.print(f"[green]saved[/green] {args.key} -> ~/.idm/config.json")
        return 0
    if args.action == "setenv":
        if not args.key or args.value is None:
            console.print("[red]usage: idm config setenv NAME VALUE[/red]")
            return 1
        set_user_env(args.key, args.value)
        shown = mask_secret(args.value) if args.key.upper().endswith("KEY") else args.value
        console.print(f"[green]saved[/green] {args.key} = {shown} "
                      "(Windows user environment; new processes will see it)")
        return 0
    if args.action == "getenv":
        if not args.key:
            console.print("[red]usage: idm config getenv NAME[/red]")
            return 1
        value = get_user_env(args.key)
        shown = mask_secret(value) if value and args.key.upper().endswith("KEY") else value
        console.print(f"{args.key} = {shown if shown is not None else '(not set)'}")
        return 0
    if args.action == "delenv":
        if not args.key:
            console.print("[red]usage: idm config delenv NAME[/red]")
            return 1
        clear_user_env(args.key)
        console.print(f"[green]deleted[/green] {args.key} from Windows user environment")
        return 0
    return 1


def cmd_providers(args, cfg) -> int:
    from .gui import expand_preset_out, load_presets
    from .health import health_payload, run_checks

    results = run_checks(cfg, deep=args.deep)

    # --preset (kind 'providers'): query/out/viewer only — there are no
    # filters to fill; explicit flags win, as on every --preset command
    if getattr(args, "preset", None):
        p = load_presets("providers").get(args.preset)
        if p is None:
            console.print(f"[red]unknown preset: {args.preset} — "
                          "see 'idm presets list'[/red]")
            return 1
        if not getattr(args, "query", None) and p.get("query"):
            args.query = p["query"]
        if not args.export and isinstance(p.get("out"), str) and p["out"]:
            args.export = expand_preset_out(p["out"], "providers")
        if not getattr(args, "viewer", "") and isinstance(p.get("viewer"), str) \
                and p["viewer"]:
            args.viewer = p["viewer"]

    # --export wins over stdout (as on downloads): the report file IS
    # written even when providers are down (triage material), but the exit
    # code still reports health — 0 all ok, 1 something is down
    if args.export:
        data = health_payload(results, "deep" if args.deep else "quick")
        try:
            result = (apply_query(args.query, data)
                      if getattr(args, "query", None) else data)
        except JqError as e:
            console.print(f"[red]invalid --query: {e}[/red]")
            return 1
        _dest = Path(args.export)
        _dest.parent.mkdir(parents=True, exist_ok=True)
        write_text_newlines(_dest, emit_json(result, indent=2) + "\n")
        scope = ", ".join(s for s in (
            "deep" if args.deep else "",
            f"query {args.query}" if getattr(args, "query", None) else "",
            f"preset {args.preset}" if getattr(args, "preset", None) else "")
            if s)
        console.print(f"[green]exported[/green] provider report -> {args.export}"
                      f"{f' ({scope})' if scope else ''}")
        if not getattr(args, "quiet", False):
            _open_exported(args.export, getattr(args, "viewer", "") or "")
        return 0 if data["all_ok"] else 1

    if args.json or getattr(args, "query", None):
        data = health_payload(results, "deep" if args.deep else "quick")
        rc = _emit_json(data, args)
        if rc:
            return rc  # bad --query
        return 0 if data["all_ok"] else 1

    mode = "deep" if args.deep else "quick"
    console.print(f"[bold]Subtitle provider health[/bold] ({mode} check)\n")
    table = Table(show_header=True, header_style="bold")
    table.add_column("Provider")
    table.add_column("Status")
    table.add_column("Detail")
    table.add_column("Endpoint", style="dim")
    style = {"ok": "green", "warn": "yellow", "down": "red"}
    mark = {"ok": "OK", "warn": "WARN", "down": "DOWN"}
    for r in results:
        table.add_row(r.label,
                      f"[{style[r.status]}]{mark[r.status]}[/{style[r.status]}]",
                      r.detail, r.endpoint)
    console.print(table)
    for r in results:
        for h in r.hints:
            console.print(f"[dim]hint: {h}[/dim]")
    if args.deep:
        console.print("\n[dim]deep check fetched a real subtitle from each "
                      "reachable keyless provider[/dim]")
    down = sum(1 for r in results if r.status == "down")
    if down:
        console.print(f"[red]{down} provider(s) down — the chain skips them "
                      "automatically[/red]")
    return 0 if not down else 1


def doctor_rows(findings: list[dict]) -> list[tuple[str, str, str, str, str, str]]:
    """>Convert diagnose_config findings into display rows:
    (severity, key, hidden_value, shadowed_by, effective_value, kind).
    'hidden' is the value that is NOT being used; 'effective' is what wins.
    Secret-looking keys are masked."""
    from .config import mask_secret

    rows = []
    for f in findings:
        hidden, effective = f["loser_value"], f["winner_value"]
        if f["key"].upper().endswith("KEY"):
            hidden, effective = mask_secret(str(hidden)), mask_secret(str(effective))
        rows.append((f["severity"], f["key"], repr(hidden),
                     f["winner"] if f["kind"] == "override" else "(not a setting)",
                     repr(effective) if f["kind"] == "override" else "(ignored)",
                     f["kind"]))
    return rows


def cmd_verify(args, cfg) -> int:
    """'idm verify': magic-byte scan of files already in the downloads
    folder — same check a finished download gets, applied retroactively.
    Warn-only by default: reports, never touches anything. With
    --delete-warned, kind-mismatched files move into a 'quarantine'
    folder inside the downloads directory instead of staying put."""
    from .config import verify_ignore_list
    from .gui_common import move_warned_to_quarantine, scan_for_quarantine

    out_dir = Path(args.out) if args.out else Path(cfg.get("out_dir") or "downloads")
    jsonish = bool(getattr(args, "json", False) or getattr(args, "query", None))

    payload = scan_for_quarantine(out_dir, ignore=verify_ignore_list(cfg))
    moves: list[tuple[str, str]] = []
    if getattr(args, "delete_warned", False) and out_dir.is_dir():
        payload, moves = move_warned_to_quarantine(out_dir, payload)
    payload.setdefault("moved", 0)
    payload.setdefault("quarantine", str(out_dir / "quarantine"))

    if jsonish:
        rc = _emit_json(payload, args)
        if rc:                       # e.g. a bad --query: error already shown
            return rc
        return 1 if payload["warned"] else 0

    if not out_dir.is_dir():
        console.print(f"[yellow]no downloads folder: {out_dir}[/yellow]")
        return 0
    if not payload["files"]:
        console.print(f"[green]nothing to verify in {out_dir}[/green]")
        return 0
    for f in payload["files"]:
        if f["kind"] == "warned":
            console.print(f"[warn] {f['file']}: {f['note']}",
                          style="yellow", markup=False, highlight=False)
        elif f["kind"] == "unreadable":
            console.print(f"[error] {f['file']}: cannot read the file for scanning",
                          style="red", markup=False, highlight=False)
        elif f["kind"] == "ignored":
            console.print(f"[ignored] {f['file']} (on the verify ignore list)",
                          style="dim", markup=False, highlight=False)
    for name, target in moves:
        console.print(f"[quarantine] {name} -> {target}",
                      style="cyan", markup=False, highlight=False)
    console.print(
        f"\n{payload['scanned']} file(s) scanned in {out_dir}: "
        f"[green]{payload['ok']} clean[/green], "
        f"[yellow]{payload['warned']} warned[/yellow], "
        f"[dim]{payload['skipped']} no opinion[/dim]" +
        (f", [red]{payload['unreadable']} unreadable[/red]"
         if payload["unreadable"] else "") +
        (f", [cyan]{payload['moved']} quarantined[/cyan]"
         if payload["moved"] else "") +
        (f", [dim]{payload['ignored']} ignored[/dim]"
         if payload.get("ignored") else ""))
    return 1 if payload["warned"] else 0


def cmd_restore(args, cfg) -> int:
    """'idm restore': move files back out of the quarantine folder that
    'idm verify --delete-warned' fills, into the downloads folder (all of
    them, or just the named ones). --discard deletes them for good
    instead. Never overwrites: a colliding name gets a ' (2)' suffix."""
    from .gui_common import restore_from_quarantine

    out_dir = Path(args.out) if args.out else Path(cfg.get("out_dir") or "downloads")
    jsonish = bool(getattr(args, "json", False) or getattr(args, "query", None))
    payload = restore_from_quarantine(
        out_dir, names=getattr(args, "files", None),
        discard=bool(getattr(args, "discard", False)))

    if jsonish:
        rc = _emit_json(payload, args)
        if rc:                       # e.g. a bad --query: error already shown
            return rc
        return 1 if payload["failed"] else 0

    if not payload["files"]:
        if (out_dir / "quarantine").is_dir():
            console.print("[green]quarantine is empty[/green]")
        else:
            console.print(f"[yellow]no quarantine folder in {out_dir}[/yellow]")
        return 0
    for f in payload["files"]:
        if f["action"] == "restored":
            console.print(f"[restore] {f['file']} -> {f['dest']}",
                          style="cyan", markup=False, highlight=False)
        elif f["action"] == "discarded":
            console.print(f"[discard] {f['file']}",
                          style="yellow", markup=False, highlight=False)
        else:
            console.print(f"[error] {f['file']}: {f['error']}",
                          style="red", markup=False, highlight=False)
    if payload["discarded"] and not payload["restored"]:
        console.print(f"\n{payload['discarded']} file(s) discarded, "
                      f"{payload['failed']} failed")
    else:
        console.print(f"\n{payload['restored']} file(s) restored from "
                      f"{payload['quarantine']}, {payload['failed']} failed")
    return 1 if payload["failed"] else 0


def cmd_sanitize_names(args, cfg) -> int:
    """'idm sanitize-names': list (or with --apply, fix) downloads files
    whose names a mangled Content-Disposition produced — the same
    _unmangle_cd_name rule _pick_filename applies to new downloads,
    applied to files already on disk. Dry run by default; --apply renames
    in place, never overwriting (a colliding name gets a ' (2)' suffix)."""
    from .gui_common import apply_name_fixes, scan_unclean_names

    out_dir = Path(args.out) if args.out else Path(cfg.get("out_dir") or "downloads")
    jsonish = bool(getattr(args, "json", False) or getattr(args, "query", None))
    do_apply = bool(getattr(args, "apply", False))

    if do_apply:
        payload, _renames = apply_name_fixes(out_dir)
    else:
        payload = scan_unclean_names(out_dir)
    files = payload["files"]

    if jsonish:
        rc = _emit_json(payload, args)
        if rc:                       # e.g. a bad --query: error already shown
            return rc
        return 1 if (payload["failed"]
                     or (not do_apply and payload["unclean"])) else 0

    if not out_dir.is_dir():
        console.print(f"[yellow]no downloads folder: {out_dir}[/yellow]")
        return 0
    if not files:
        console.print(f"[green]all {payload['scanned']} file(s) in {out_dir} have clean "
                      "names[/green]")
        return 0
    for f in files:
        if f["action"] == "failed":
            console.print(f"[error] {f['file']} -> {f['to']}: {f['error']}",
                          style="red", markup=False, highlight=False)
        elif f["action"] == "renamed":
            console.print(f"[rename] {f['file']} -> {f['to']}",
                          style="cyan", markup=False, highlight=False)
        else:
            console.print(f"{f['file']} -> {f['to']}",
                          markup=False, highlight=False)
    if do_apply:
        console.print(f"\n{payload['renamed']} file(s) renamed, "
                      f"{payload['failed']} failed")
    else:
        console.print(f"\n{len(files)} of {payload['scanned']} file(s) have mangled names "
                      "(dry run — pass --apply to rename them)")
    return 1 if (payload["failed"]
                 or (not do_apply and payload["unclean"])) else 0


def cmd_ignore(args, cfg) -> int:
    """'idm ignore': manage the config-driven verify ignore list —
    filenames the magic-byte scan should never warn about (the escape
    hatch for files you inspected and decided are fine). Entries live in
    ~/.idm/config.json under "verify_ignore"; plain names match exactly
    (case-insensitive), '*.ext' by extension, 'prefix*' by prefix. With
    --json every action prints a machine-readable payload: the resulting
    entries, plus what add/remove actually changed."""
    from .config import (
        USER_CONFIG_PATH,
        verify_ignore_add,
        verify_ignore_list,
        verify_ignore_remove,
    )

    action = args.action
    names = [n for n in (args.names or []) if n.strip()]
    jsonish = bool(getattr(args, "json", False) or getattr(args, "query", None))

    if action == "add":
        if not names:
            console.print("[red]usage: idm ignore add NAME [NAME…] — a plain "
                          "name matches exactly; '*.ext' by extension; "
                          "'prefix*' by prefix[/red]")
            return 1
        before = set(verify_ignore_list(cfg))
        current = verify_ignore_add(cfg, names)
        added, duplicates = [], []
        for entry in (str(p).strip() for p in names):
            (duplicates if entry in before or entry in added
             else added).append(entry)
    elif action == "remove":
        if not names:
            console.print("[red]usage: idm ignore remove NAME [NAME…][/red]")
            return 1
        current, removed = verify_ignore_remove(cfg, names)
        unknown = []
        for entry in (str(p).strip() for p in names):
            if entry not in removed and entry not in unknown:
                unknown.append(entry)
    else:  # list (the argparse default)
        current = verify_ignore_list(cfg)

    if jsonish:
        payload = {"action": action}
        if action == "add":
            payload["added"] = added
            payload["duplicates"] = duplicates
        elif action == "remove":
            payload["removed"] = removed
            payload["unknown"] = unknown
        payload["entries"] = current
        payload["count"] = len(current)
        payload["config"] = str(USER_CONFIG_PATH)
        rc = _emit_json(payload, args)
        if rc:                       # e.g. a bad --query: error already shown
            return rc
        return 1 if action == "remove" and not payload["removed"] else 0

    if action == "add":
        for n in names:
            console.print(f"[green]ignored[/green] {n} "
                          "(verify will not warn about it)",
                          markup=False, highlight=False)
        # touchy feedback, text only: entries that match nothing on disk
        # right now, and '*.ext' shortcuts when flagged files share an
        # extension (--json keeps its payload clean for scripts)
        if not jsonish:
            from .gui_common import ignore_add_hints

            out_dir = (Path(args.out) if getattr(args, "out", None)
                       else Path(cfg.get("out_dir") or "downloads"))
            matched, hints = ignore_add_hints(out_dir, names,
                                              ignore=verify_ignore_list(cfg))
            for n in (x for x in names if x not in matched):
                console.print(f"[warn] no file in {out_dir} matches '{n}' — "
                              "verify never warned about anything by that "
                              "name; check for a typo ('idm ignore remove "
                              f'"{n}"\' undoes it)',
                              style="yellow", markup=False, highlight=False)
            for pattern, covered in hints:
                console.print(f"[hint] {len(covered)} warned file(s) share the "
                              f"'{pattern}' extension — 'idm ignore add "
                              f"'{pattern}' would cover all of them: "
                              + ", ".join(covered),
                              style="cyan", markup=False, highlight=False)
    elif action == "remove":
        for n in removed:
            console.print(f"[green]removed[/green] {n} from the ignore list",
                          markup=False, highlight=False)
        for n in unknown:
            console.print(f"[warn] not on the ignore list: {n}",
                          style="yellow", markup=False, highlight=False)
    else:
        if not current:
            console.print("[dim]the ignore list is empty — add entries with "
                          "'idm ignore add NAME [NAME…]'. Plain names match "
                          "exactly; '*.ext' by extension; 'prefix*' by "
                          "prefix.[/dim]")
            return 0
        for n in current:
            console.print(n, markup=False, highlight=False)
    if action == "remove" and names and not removed:
        return 1
    console.print(f"[dim]{len(current)} entr{'y' if len(current) == 1 else 'ies'} "
                  "in ~/.idm/config.json (verify_ignore)[/dim]")
    return 0


def cmd_doctor(args, cfg) -> int:
    from .config import diagnose_config

    console.print("[bold]Config diagnosis[/bold] "
                  "(defaults <- ~/.idm/config.json <- idm.json <- environment)\n")
    findings = diagnose_config(args.config)
    if not findings:
        console.print("[green]OK — no config conflicts or unknown keys.[/green]")
        return 0
    table = Table(show_header=True, header_style="bold")
    table.add_column("Severity")
    table.add_column("Key")
    table.add_column("Hidden value", overflow="fold")
    table.add_column("Shadowed by")
    table.add_column("Effective value", overflow="fold")
    style = {"warn": "red", "info": "yellow"}
    mark = {"warn": "WARN", "info": "INFO"}
    for severity, key, hidden, shadowed_by, effective, _kind in doctor_rows(findings):
        table.add_row(
            f"[{style[severity]}]{mark[severity]}[/{style[severity]}]",
            key, hidden, shadowed_by, effective)
    console.print(table)
    for f in findings:
        if f["kind"] == "unknown":
            console.print(f"[dim]hint: '{f['key']}' in {f['loser']} is not a known "
                          "setting (typo?) — it is ignored entirely[/dim]")
        elif f["severity"] == "warn":
            console.print(f"[dim]hint: remove '{f['key']}' from {f['loser']} or from "
                          f"{f['winner']} — whichever value you didn't intend[/dim]")
        else:
            console.print(f"[dim]hint: '{f['key']}' is harmlessly shadowed; consider "
                          f"deleting it from {f['loser']} for clarity[/dim]")
    warns = sum(1 for f in findings if f["severity"] == "warn")
    console.print(f"\n{warns} warning(s), "
                  f"{len(findings) - warns} info — warnings mean a value you set "
                  "is NOT the one being used")
    return 1 if warns else 0


def cmd_collect(args, cfg) -> int:
    from .collect import run_cli
    return run_cli(args)


def cmd_gui(args, cfg) -> int:
    from .gui import main as gui_main

    gui_main()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="idm",
        description="Internet Download Manager (PyIDM): batch downloads, expiring-link "
                    "refresh, resumable transfers, and subtitle fetching.",
    )
    p.add_argument("--version", action="version", version=f"PyIDM {__version__}")
    p.add_argument("--config", help="path to an extra JSON config file")
    p.add_argument("--out", "-o", help="output directory")
    p.add_argument("--workers", "-w", type=int, help="parallel downloads")
    p.add_argument("--segments", type=int, help="parallel connections per download")
    p.add_argument("--retries", type=int, help="retry attempts per download")
    p.add_argument("--timeout", type=float, help="request timeout in seconds")
    p.add_argument("--overwrite", action="store_true", help="overwrite existing files")

    sub = p.add_subparsers(dest="command")

    def add_common(sp):
        """Accept the global options after the subcommand too. default=SUPPRESS
        keeps top-level values intact when the sub-level flag is absent
        (argparse subparser defaults would otherwise overwrite them with None)."""
        sp.add_argument("--out", "-o", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
        sp.add_argument("--workers", "-w", type=int, default=argparse.SUPPRESS, help=argparse.SUPPRESS)
        sp.add_argument("--segments", type=int, default=argparse.SUPPRESS, help=argparse.SUPPRESS)
        sp.add_argument("--retries", type=int, default=argparse.SUPPRESS, help=argparse.SUPPRESS)
        sp.add_argument("--timeout", type=float, default=argparse.SUPPRESS, help=argparse.SUPPRESS)
        sp.add_argument("--overwrite", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS)

    g = sub.add_parser("get", help="download one or more URLs")
    g.add_argument("urls", nargs="+")
    g.add_argument("-n", "--name", help="filename for the first URL")
    g.add_argument("--json", action="store_true",
                   help="suppress progress output and print a machine-readable "
                        "JSON summary instead (same exit codes: 0 all downloads "
                        "done/skipped, 1 any error/cancelled)")
    g.add_argument("--query", metavar="Q",
                   help="jq-style query over the JSON summary (implies --json); "
                        "e.g. '.downloads[].filename' — run 'idm get --query .' "
                        "to see the full document")
    g.add_argument("-r", "--raw", action="store_true",
                   help="with --query, print strings without quotes and "
                        "bools/null as true/false/null (jq -r style)")
    add_common(g)
    g.set_defaults(func=cmd_get)

    b = sub.add_parser("batch", help="download a batch list from a file")
    b.add_argument("source", nargs="?", help="text file with one URL per line ('url -> name' supported)")
    b.add_argument("--clipboard", action="store_true", help="read the URL list from the clipboard")
    b.add_argument("--json", action="store_true",
                   help="suppress progress output and print a machine-readable "
                        "JSON summary instead (same exit codes: 0 all downloads "
                        "done/skipped, 1 any error/cancelled)")
    b.add_argument("--query", metavar="Q",
                   help="jq-style query over the JSON summary (implies --json); "
                        "e.g. '.downloads[].filename' — run 'idm batch --query .' "
                        "to see the full document")
    b.add_argument("-r", "--raw", action="store_true",
                   help="with --query, print strings without quotes and "
                        "bools/null as true/false/null (jq -r style)")
    add_common(b)
    b.set_defaults(func=cmd_batch)

    r = sub.add_parser("resume", help="retry unfinished downloads recorded in the state file")
    r.add_argument("--json", action="store_true",
                   help="suppress progress output and print a machine-readable "
                        "JSON summary instead (same exit codes: 0 all downloads "
                        "done/skipped, 1 any error/cancelled)")
    r.add_argument("--query", metavar="Q",
                   help="jq-style query over the JSON summary (implies --json); "
                        "e.g. '.downloads[].filename' — run 'idm resume --query .' "
                        "to see the full document")
    r.add_argument("-r", "--raw", action="store_true",
                   help="with --query, print strings without quotes and "
                        "bools/null as true/false/null (jq -r style)")
    add_common(r)
    r.set_defaults(func=cmd_resume)

    s = sub.add_parser("subs", help="download subtitles (OpenSubtitles) for a video file, "
                                    "a folder of videos, or a title string")
    s.add_argument("target", help="video file, folder, or movie/episode title")
    s.add_argument("-l", "--langs", help="comma-separated languages, e.g. en,vi")
    s.add_argument("--player", help="player executable (vlc, mpc-hc64, or a path); default: auto-detect VLC/MPC")
    s.add_argument("--no-play", action="store_true", help="don't launch the player after downloading")
    add_common(s)
    s.set_defaults(func=cmd_subs)

    sh = sub.add_parser("history", help="list or export the subtitle download history "
                                         "(same data as the GUI history panel)")
    sh.add_argument("-f", "--fmt", choices=["table", "csv", "markdown", "json"], default="table",
                    help="output format (default: table; csv/markdown/json print to "
                         "stdout; with --out, extension picks md vs csv when --fmt is "
                         "omitted; 'json' selects the same data as --json)")
    sh.add_argument("--out", help="write the export to a file instead of stdout "
                                  "(extension .md/.markdown picks markdown when --fmt is omitted)")
    sh.add_argument("--json", action="store_true",
                    help="print/write the raw filtered entries as JSON "
                         "(every field preserved; overrides --fmt; "
                         "same data as --fmt json)")
    sh.add_argument("--query", metavar="Q",
                    help="jq-style query over the JSON entries (implies --json); "
                         "e.g. '[].provider' — run 'idm history --query .' to "
                         "see the full document")
    sh.add_argument("-r", "--raw", action="store_true",
                    help="with --query, print strings without quotes and "
                         "bools/null as true/false/null (jq -r style)")
    sh.add_argument("--provider", help="exact provider name, case-insensitive (e.g. subtitlecat)")
    sh.add_argument("--rows", type=int, default=3, metavar="N",
                    help="rows in the --out preview line (default 3; 0 hides "
                         "the preview)")
    sh.add_argument("--quiet", action="store_true",
                    help="don't open the file written by --out in the default app")
    sh.add_argument("--viewer", metavar="APP",
                    help="open the file written by --out with this app instead of "
                         "the default one (name on PATH like 'code', or a path; "
                         "quote multi-word paths)")
    sh.add_argument("--since", help="only entries on/after this date (YYYY-MM-DD)")
    sh.add_argument("--until", help="only entries on/before this date (YYYY-MM-DD)")
    sh.add_argument("--preset", metavar="NAME",
                    help="apply a saved export preset (see 'idm presets list') — "
                         "fills provider/since/until/query you did not pass "
                         "explicitly; explicit flags win")
    sh.set_defaults(func=cmd_subs_history)

    dl = sub.add_parser("downloads", help="list or export the download state file — "
                                           "what 'idm resume' would retry")
    dl.add_argument("-f", "--fmt", choices=["table", "csv", "markdown"], default="table",
                    help="output format (default: table; csv/markdown print to stdout; "
                         "with --out, extension picks md vs csv when --fmt is omitted)")
    dl.add_argument("-e", "--export", "--out", dest="export", metavar="FILE",
                    help="write the export to a file instead of stdout "
                         "(extension .md/.markdown picks markdown when --fmt is omitted); "
                         "the state-file directory stays the global -o/--out or config out_dir")
    dl.add_argument("--status", help="exact status to keep, case-insensitive "
                                     "(e.g. error, cancelled, downloading)")
    dl.add_argument("--since", help="only entries updated on/after this date (YYYY-MM-DD)")
    dl.add_argument("--until", help="only entries updated on/before this date (YYYY-MM-DD)")
    dl.add_argument("--rows", type=int, default=3, metavar="N",
                    help="rows in the --out preview line (default 3; 0 hides "
                         "the preview)")
    dl.add_argument("--quiet", action="store_true",
                    help="don't open the file written by --out in the default app")
    dl.add_argument("--viewer", metavar="APP",
                    help="open the file written by --out with this app instead of "
                         "the default one (name on PATH like 'code', or a path; "
                         "quote multi-word paths)")
    dl.add_argument("--json", action="store_true",
                    help="print the raw state records as JSON (url + every "
                         "state field preserved; overrides --fmt)")
    dl.add_argument("--query", metavar="Q",
                    help="jq-style query over the JSON records (implies --json); "
                         "e.g. '[].filename' — run 'idm downloads --query .' to "
                         "see the full document")
    dl.add_argument("-r", "--raw", action="store_true",
                    help="with --query, print strings without quotes and "
                         "bools/null as true/false/null (jq -r style)")
    dl.add_argument("--preset", metavar="NAME",
                    help="apply a saved export preset (see 'idm presets list') — "
                         "fills any format/query flags you did not pass "
                         "explicitly; explicit flags win")
    dl.set_defaults(func=cmd_downloads)

    pr2 = sub.add_parser("presets", help="list/add/remove named export presets "
                                         "(shared with the GUI export dialogs)")
    pr2.add_argument("action", nargs="?", default="list",
                     choices=["list", "add", "remove", "export", "import"],
                     help="list (default), add, or remove a preset; export/\n"
                          "import all presets as a shareable JSON file")
    pr2.add_argument("name", nargs="?", help="preset name (for add/remove)")
    pr2.add_argument("file", nargs="?", help="JSON file (for export/import)")
    pr2.add_argument("--replace", action="store_true",
                     help="with import: drop each kind's existing presets "
                          "first (default merges, overwriting same-named ones)")
    pr2.add_argument("--kind", dest="preset_kind", default="all",
                     choices=["all"] + list(_PRESET_KINDS),
                     help="which command's presets to act on (default: all; "
                          "history/downloads are the GUI export dialogs, "
                          "stats/providers the CLI report commands)")
    pr2.add_argument("--set", metavar="KEY=VALUE", action="append",
                     help="setting for 'add', repeatable — history: provider, "
                          "since, until, query; downloads: fmt, query; "
                          "stats/providers: query (each kind also takes "
                          "out= and viewer=)")
    pr2.add_argument("--json", action="store_true",
                     help="print the presets as JSON (list action)")
    pr2.add_argument("--query", metavar="Q",
                     help="jq-style query over the presets JSON (list action)")
    pr2.add_argument("-r", "--raw", action="store_true",
                     help="with --query, print strings without quotes (jq -r style)")
    pr2.set_defaults(func=cmd_presets)

    st = sub.add_parser("stats", help="summarize the subtitle history and the download "
                                       "state file: counts, bytes, providers, oldest entries")
    st.add_argument("--json", action="store_true",
                    help="print the same numbers as machine-readable JSON")
    st.add_argument("--query", metavar="Q",
                    help="jq-style query over the JSON summary (implies --json); "
                         "e.g. '.downloads.by_status' — run 'idm stats --query .' "
                         "to see the full document")
    st.add_argument("-r", "--raw", action="store_true",
                    help="with --query, print strings without quotes and "
                         "bools/null as true/false/null (jq -r style)")
    st.add_argument("--watch", type=int, default=None, metavar="N",
                    help="re-print the summary every N seconds (text mode only; "
                         "Ctrl+C to stop)")
    st.add_argument("--export", metavar="FILE",
                    help="write the JSON summary to this .json file (any shape "
                         "from --query applies; --quiet/--viewer control opening "
                         "it; not with --watch)")
    st.add_argument("--preset", metavar="NAME",
                    help="apply a saved report preset (kind 'stats') — fills "
                         "query/out/viewer you did not pass explicitly; "
                         "explicit flags win")
    st.add_argument("--quiet", action="store_true",
                    help="don't open the file written by --export/--preset")
    st.add_argument("--viewer", metavar="APP",
                    help="open the file written by --export/--preset with this "
                         "app instead of the default one")
    st.set_defaults(func=cmd_stats)

    rf = sub.add_parser("refresh", help="test a link provider: resolve a fresh URL for an expired one")
    rf.add_argument("url")
    rf.set_defaults(func=cmd_refresh)

    c = sub.add_parser("config", help="view/edit ~/.idm/config.json")
    c.add_argument("action", nargs="?",
                   choices=["list", "get", "set", "setenv", "getenv", "delenv"],
                   default="list")
    c.add_argument("key", nargs="?")
    c.add_argument("value", nargs="?")
    c.set_defaults(func=cmd_config)

    gui = sub.add_parser("gui", help="launch the desktop GUI")
    gui.set_defaults(func=cmd_gui)

    col = sub.add_parser("collect", help="run the browser-extension collector "
                        "(queue links captured by the PyIDM Collector "
                        "extension) until Ctrl+C")
    col.add_argument("--port", type=int, default=None,
                     help="port to listen on (default: collector_port config, "
                          "27492)")
    col.set_defaults(func=cmd_collect)

    pr = sub.add_parser("providers", help="health check for each configured subtitle "
                                          "provider (DNS, reachability, API key)")
    pr.add_argument("--deep", action="store_true",
                    help="also fetch a real subtitle from each provider (end-to-end)")
    pr.add_argument("--json", action="store_true",
                    help="print the results as machine-readable JSON (same exit "
                         "code: 0 all ok, 1 something is down)")
    pr.add_argument("--query", metavar="Q",
                    help="jq-style query over the JSON report (implies --json); "
                         "e.g. '[].name' — run 'idm providers --query .' to "
                         "see the full document")
    pr.add_argument("-r", "--raw", action="store_true",
                    help="with --query, print strings without quotes and "
                         "bools/null as true/false/null (jq -r style)")
    pr.add_argument("--export", metavar="FILE",
                    help="write the JSON report to this .json file (any shape "
                         "from --query applies; --quiet/--viewer control opening "
                         "it; exit code stays 0 all ok / 1 something down)")
    pr.add_argument("--preset", metavar="NAME",
                    help="apply a saved report preset (kind 'providers') — fills "
                         "query/out/viewer you did not pass explicitly; "
                         "explicit flags win")
    pr.add_argument("--quiet", action="store_true",
                    help="don't open the file written by --export/--preset")
    pr.add_argument("--viewer", metavar="APP",
                    help="open the file written by --export/--preset with this "
                         "app instead of the default one")
    pr.set_defaults(func=cmd_providers)

    v = sub.add_parser("verify", help="magic-byte scan of the files already in "
                                      "the downloads folder — flags finished files "
                                      "whose content contradicts their name (same "
                                      "check a finished download gets)")
    v.add_argument("--json", action="store_true",
                   help="print the scan as machine-readable JSON")
    v.add_argument("--query", metavar="Q",
                   help="jq-style query over the scan JSON (implies --json); "
                        "e.g. '[.files[] | select(.kind == \"warned\")] | length' — "
                        "run 'idm verify --query .' to see the full document")
    v.add_argument("-r", "--raw", action="store_true",
                   help="with --query, print strings without quotes and "
                        "bools/null as true/false/null (jq -r style)")
    v.add_argument("--delete-warned", action="store_true",
                   help="move kind-mismatched files into a 'quarantine' folder "
                        "inside the downloads directory instead of leaving "
                        "them in place (exit 0 once nothing is left warned)")
    add_common(v)
    v.set_defaults(func=cmd_verify)

    ig = sub.add_parser("ignore", help="manage the verify ignore list — files the "
                                       "magic-byte scan should never warn about "
                                       "(stored as the 'verify_ignore' config key; "
                                       "plain names match exactly, '*.ext' by "
                                       "extension, 'prefix*' by prefix)")
    ig.add_argument("action", nargs="?", default="list",
                    choices=["list", "add", "remove"],
                    help="list (default), add, or remove ignore entries")
    ig.add_argument("names", nargs="*",
                    help="filenames / patterns to add or remove")
    ig.add_argument("--json", action="store_true",
                    help="print the result as machine-readable JSON: the "
                         "resulting entries, plus what add/remove changed")
    ig.add_argument("--query", metavar="Q",
                    help="jq-style query over the JSON (implies --json); "
                         "e.g. '.entries' — run 'idm ignore --query .' to "
                         "see the full document")
    ig.add_argument("-r", "--raw", action="store_true",
                    help="with --query, print strings without quotes and "
                         "bools/null as true/false/null (jq -r style)")
    add_common(ig)
    ig.set_defaults(func=cmd_ignore)

    r2 = sub.add_parser("restore", help="move files back out of the quarantine folder "
                                        "(filled by 'idm verify --delete-warned') into "
                                        "the downloads folder — all of them, or just "
                                        "the named ones; never overwrites")
    r2.add_argument("files", nargs="*",
                    help="quarantine filenames to act on (default: all of them)")
    r2.add_argument("--discard", action="store_true",
                    help="delete the files for good instead of restoring them "
                         "(an emptied quarantine folder is removed)")
    r2.add_argument("--json", action="store_true",
                    help="print the moves as machine-readable JSON")
    r2.add_argument("--query", metavar="Q",
                    help="jq-style query over the JSON (implies --json); "
                         "e.g. '.restored' — run 'idm restore --query .' to "
                         "see the full document")
    r2.add_argument("-r", "--raw", action="store_true",
                    help="with --query, print strings without quotes and "
                         "bools/null as true/false/null (jq -r style)")
    add_common(r2)
    r2.set_defaults(func=cmd_restore)

    sn = sub.add_parser("sanitize-names", help="list (or with --apply, rename) downloads "
                                            "files whose names a mangled "
                                            "Content-Disposition produced — dry run "
                                            "by default")
    sn.add_argument("--apply", action="store_true",
                    help="rename the files in place (default: dry run that "
                         "only lists what would change; never overwrites — "
                         "a colliding name gets a ' (2)' suffix)")
    sn.add_argument("--json", action="store_true",
                    help="print the plan as machine-readable JSON")
    sn.add_argument("--query", metavar="Q",
                    help="jq-style query over the JSON (implies --json); "
                         "e.g. '.unclean' — run 'idm sanitize-names --query .' "
                         "to see the full document")
    sn.add_argument("-r", "--raw", action="store_true",
                    help="with --query, print strings without quotes and "
                         "bools/null as true/false/null (jq -r style)")
    add_common(sn)
    sn.set_defaults(func=cmd_sanitize_names)

    d = sub.add_parser("doctor", help="diagnose config layering: values silently "
                                      "overridden by env or another file, unknown keys")
    d.set_defaults(func=cmd_doctor)

    ps = sub.add_parser("prune-state", help="remove download-state records whose file "
                                           "no longer exists in the downloads folder "
                                           "(the ghosts 'resume' retries forever) — "
                                           "dry-run preview by default")
    ps.add_argument("--apply", action="store_true",
                    help="remove the stale records from idm.state.json "
                         "(default: dry run that only lists them; files are "
                         "never touched, and a record whose file reappeared "
                         "between preview and apply is kept)")
    ps.add_argument("--vacuum", action="store_true",
                    help="like --apply, plus rewrite the store without "
                         "indentation while it is open — reports bytes "
                         "reclaimed (reclaims bytes even with nothing "
                         "stale; still a no-op on a missing store)")
    ps.add_argument("--json", action="store_true",
                    help="print the preview/apply result as machine-readable JSON")
    ps.add_argument("--query", metavar="Q",
                    help="jq-style query over the JSON (implies --json); "
                         "e.g. '.stale' — run 'idm prune-state --query .' to "
                         "see the full document")
    ps.add_argument("-r", "--raw", action="store_true",
                    help="with --query, print strings without quotes and "
                         "bools/null as true/false/null (jq -r style)")
    add_common(ps)
    ps.set_defaults(func=cmd_prune_state)
    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = get_config(args.config)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return args.func(args, cfg)
    except KeyboardInterrupt:
        console.print("\n[yellow]interrupted[/yellow]")
        return 130


if __name__ == "__main__":
    sys.exit(main())
