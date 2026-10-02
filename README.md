# PyIDM — batch download manager with expiring-link refresh + subtitles

[![CI](https://github.com/i12playwow/pyidm/actions/workflows/ci.yml/badge.svg)](https://github.com/i12playwow/pyidm/actions/workflows/ci.yml)
[![Release](https://github.com/i12playwow/pyidm/actions/workflows/release.yml/badge.svg)](https://github.com/i12playwow/pyidm/actions/workflows/release.yml)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](pyproject.toml)

An Internet-Download-Manager-style tool for the terminal and desktop:

- **Batch downloads** — queue a list of URLs, download them in parallel with live progress.
- **Expiring download links** — when a signed/one-time URL dies mid-batch (HTTP 401/403/410,
  "link expired", "invalid signature"…), PyIDM automatically calls your **link provider** to
  get a fresh URL and continues the same file.
- **Resumable** — partial `.part` files are kept and continued via HTTP `Range` requests,
  across retries and across runs (`idm resume`).
- **Multi-connection speed** — large files are split into parallel segments like IDM
  (8 connections per file by default) and merged on completion.
- **Subtitle download** — fetch the best-matching subtitle for a video file, a whole folder
  of videos, or just a title, via the [OpenSubtitles API](https://opensubtitles.com).
- **GUI** — `idm-gui` opens a Tkinter window with the queue, progress table, and a
  subtitle panel.

## Install

Python 3.9+ required (3.12 installed via winget during setup). From the project folder:

```bash
pip install -e .
```

This installs two commands: `idm` (CLI) and `idm-gui` (desktop app).
Without installing, run via `python -m idm.cli …` / `python -m idm.gui`.

## Standalone executables (no Python needed)

**Portable bundle (recommended):** `portable\PyIDM\` — copy the folder anywhere
(including other Windows PCs) and run `pyidm.bat` (CLI) or `pyidm-gui.bat`
(GUI). It bundles python.org's embeddable Python plus Tk support, so no
install is needed. A ready-made archive is committed at
`portable\PyIDM-portable.zip` (with a `.sha256` checksum; also attached
to each [GitHub release](https://github.com/i12playwow/pyidm/releases/latest)).
Rebuild with `build_portable.bat`.

**Why not the single-file exes anymore:** Windows 11 **Smart App Control**
(SAC) blocks freshly built unsigned binaries — it has no allowlist, no
exclusions, and no path rules; only binaries with Microsoft cloud reputation
or a trusted (e.g. EV) signature run. `dist\pyidm.exe` / `dist\pyidm-gui.exe`
from earlier builds may therefore be blocked with "did not meet the Enterprise
signing level requirements". The portable bundle sidesteps this entirely:
`.bat` launchers aren't code-integrity-governed and python.org's `python.exe`
carries universal reputation.

To turn SAC off permanently instead (your machine, your choice — this is
irreversible per Microsoft): Settings → Privacy & security → Windows Security
→ App & browser control → Smart App Control settings → Off. After that the
PyInstaller exes run normally; rebuild them with `build_exe.bat`.

Note that unsigned PyInstaller binaries can trigger a SmartScreen warning on
other machines (*More info → Run anyway*).

## Quick start

```bash
# one or more URLs
idm get https://example.com/file.zip https://example.com/movie.mp4 -o downloads
idm get https://example.com/file.zip --json   # quiet JSON summary, same exit codes

# a batch list (one URL per line, '#' comments, optional 'url -> name' rename)
idm batch urls.txt -w 4
idm batch --clipboard          # paste a list you copied
idm batch urls.txt --json      # per-URL status/filename/bytes + totals, for scripts

# --query: jq-style filter over any --json output (no jq needed; implies --json)
idm batch urls.txt --query '.downloads[].filename' -r   # one filename per line
idm stats --query '.downloads.total_bytes'              # bytes as a bare number
idm downloads --query '[].status' -r                    # state of every pending URL
idm history --query '[].provider' -r                    # providers of every sub
idm providers --query '.providers[].name' -r            # provider names, one per line
# -r prints strings bare and bools/null as true/false/null (jq -r style)
# supported: .path [N] [] []? | pipes, select() map() has() startswith()
#   endswith() contains(), length keys add min max first last, ==/!= and/or/not

# named export presets: filter+query combos shared by the GUI export dialogs
# and the CLI — save once, reuse everywhere (stored in ~/.idm/export_prefs.json)
idm presets add errors --kind downloads --set fmt=json \
  --set 'query=[.[] | select(.status == "error")] | length'
idm presets add weekly --kind history --set provider=subtitlecat \
  --set query=[].provider --set out=weekly.json --set viewer=code
idm downloads --preset errors      # explicit flags always win over a preset
idm history --preset cat2026       # fills provider/since/until/query
idm stats --preset daily           # report kinds too: query/out/viewer pin
idm providers --preset nightly     # the JSON report lands in the pinned out
idm presets list [--json]          # what is saved; remove with 'presets remove'
idm presets export share.json      # share presets between machines…
idm presets import share.json      # …as one JSON file (merge; --replace to wipe first)
# presets can pin an output file (out) and viewer app: applying one writes
# straight to that file and offers to open it; a .json out holds the query result
# pinned out paths support {date} (YYYY-MM-DD) and {kind} placeholders and
# auto-create parent folders — e.g. out=reports/{date}-{kind}.json

# from Git Bash / WSL, prefer the sh launcher — it never goes through cmd.exe,
# so queries with pipes and quotes survive byte-exact:
#   ./pyidm.sh providers --query '.providers[] | select(.status == "down") | .name' -r

# after unpacking the portable bundle, prove the packaging works:
#   ./smoke_test.sh        # exercises both launchers + a piped, quoted --query

# resume whatever failed or was interrupted last time
idm resume
idm resume --json              # same quiet summary as get/batch, same exit codes

# subtitle history: list, filter, or export (same data as the GUI panel)
idm history                              # table
idm history -f csv                       # CSV to stdout
idm history --json                       # raw filtered entries (lossless, for scripting)
idm history --provider subtitlecat --since 2026-09-01
idm history --out report.md              # extension picks the format
idm history --out report.md --quiet      # ...and don't open it afterwards
# every --out export also prints 'preview:' — the file's first rows on one line
# --rows N sets the preview length (default 3, file rows incl. header; 0 hides it)
idm history --out report.md --viewer code  # open in a chosen app, not the default

# GUI Save As offers the same knobs: "Open after export" checkbox + Viewer
# entry (empty = default app); failures warn without failing the export
# the GUI log also shows the same one-line 'preview: ...' after every export
# the GUI downloads tab header shows the same summary as 'idm stats', refreshed every 2s
# GUI exports now speak --query too: the history Save As filter dialog and the
# downloads tab's Save As… take an optional jq-style Query; .json is a
# first-class export format — same evaluator as the CLI, so no drift
# both Save As… dialogs default to the last-used preset (preselected and
# applied on open), so a preset saved via 'idm presets add' is one click away
# GUI "Run Query…" (history + downloads tabs): evaluate a query and view the
# result in a sortable window (click a column heading to sort/reverse) with
# Copy JSON (exact CLI payload) and Copy TSV (spreadsheet-ready) buttons
# export dialogs remember your last-used format, filters, and query per
# dialog (Run Query… remembers per tab) across GUI restarts (~/.idm/export_prefs.json);
# its dropdown offers the last 8 queries for that tab (newest first, deduplicated)
# as clickable chips under the field — one click fills and validates, Run executes;
# hovering a chip tooltips the full query (and, for presets, every setting it applies);
# middle-clicking a chip removes that preset / recent query (quick curation)
# query-results windows remember their size and column widths per tab,
# restored on the next open (saved when the window closes)
# the downloads tab's tables (batch list + subtitle history) remember their
# column widths the same way (saved when the app closes)
# Help ▸ "Clear remembered settings…" resets them (with confirmation)

# GUI Tools ▸ "Sanitize file names (dry run)…": runs the
# 'idm sanitize-names' scan from the menu bar and offers to apply the
# renames — same shared engine as the CLI; nothing renamed without your yes
# right-click ▸ "Ignore this file in verify…" / "Manage verify ignore list…":
# the same verify_ignore list 'idm ignore' manages, and the Verify action
# honors it (a sweep's result dialog also offers to add the flagged files)
# mangled names show in the tree: a violet rename badge + Note preview of the
# clean name ("mangled name — clean name is: …"), right-click ▸ "Fix this file
# name…" renames just that file; badges clear once fixed by any route

# download state: what 'idm resume' would retry (list, filter, export)
idm downloads                            # table: url, filename, status, size, updated
idm downloads --status error -f csv      # only failed ones, as CSV
idm downloads --since 2026-09-20 --out state.md

# one-glance summary of both stores (counts, bytes, providers, oldest entries)
idm stats                                # same numbers the GUI status lines show
idm stats --json                         # machine-readable, for scripts
idm stats --watch 5                      # live dashboard: re-prints every 5s, Ctrl+C stops

# provider health check (DNS, reachability, API key) — scriptable via --json
idm providers                            # exit 0 all ok, 1 something is down
idm providers --json --deep              # machine-readable, with end-to-end fetch

# retroactive magic-byte scan of the downloads folder (warn-only)
idm verify                               # exit 0 clean, 1 any file warned
idm verify --json                        # machine-readable, for scripts
idm verify --delete-warned               # move mismatches to downloads/quarantine
idm ignore                               # files verify should never warn about…
idm ignore add kept.webm '*.m3u'         # …plain names, *.ext, or prefix* patterns
idm ignore add kept.webm --json          # machine-readable: added/duplicates + entries
idm restore                              # move quarantined files back (undo the sweep)
idm restore --discard                    # or delete them for good
idm sanitize-names                       # dry run: files with mangled CD names
idm sanitize-names --apply               # rename them to one clean extension
idm prune-state                          # preview state records whose file is gone
idm prune-state --apply                  # drop those ghosts from idm.state.json
idm prune-state --vacuum                 # also rewrite the store without indentation
```

Automating PyIDM? Every `--json` surface, its exact payload shape, exit codes,
and jq-style `--query` recipes are documented in [docs/automation.md](docs/automation.md).

## Presets, queries, and chips — the export workflow end to end

Everything in this section reads and writes one file: `~/.idm/export_prefs.json`.
Save a preset on the command line, and the GUI offers it as a clickable chip on
its next open; craft a filter by hand in a dialog, and `idm history --preset` can
reuse it later from a cron job. Nothing here is sync'd between machines except
via `idm presets export` / `import` (one shareable JSON file).

**Step 1 — find the rows you want with a query.** Queries are the shared language:
the same jq-style expression works on the CLI (`idm history --query …`,
`idm downloads --query …`), in both GUI Save As… dialogs, and in Run Query….
Start on the command line, where mistakes are cheapest:

```bash
idm downloads --query '[].url'                # list every URL
idm downloads --query '[.[] | select(.status == "error")]' -r   # just errors
idm history --query 'length'                  # how many rows would this export?
```

**Step 2 — save the query as a named preset.** Once a query earns a name, store
it — with the format, output file, and viewer app it belongs with:

```bash
idm presets add failed --kind downloads --set fmt=json \
  --set 'query=[.[] | select(.status == "error")] | length'
idm presets add weekly --kind history --set provider=subtitlecat \
  --set out=reports/{date}-weekly.csv --set viewer=code
idm presets list                              # everything saved, any kind
```

The `out=` path is applied when the preset is used, not when it is saved, and it
may contain **`{date}`** (local `YYYY-MM-DD` at write time) and **`{kind}`**
(`history`/`downloads`/`stats`/`providers`) — `reports/{date}-weekly.csv` lands
in a new `reports/` folder with today's date, so daily runs never overwrite
yesterday's file. Applying `weekly` later is one command: `idm history --preset
weekly` writes the CSV and opens it in your editor; any flag you pass explicitly
(`--provider`, `--out`, …) overrides the preset for that one run. The two report
commands have kinds too: `idm stats --preset NAME` and `idm providers --preset
NAME` pin query/out/viewer, and `--export FILE` writes the same JSON report
without a preset.

**Step 3 — one-click from the GUI.** Open either Save As… dialog (history or
downloads tab) and the preset row now shows a **chip** for every saved preset of
that kind — labelled `name · settings-hint`, e.g. `failed · fmt=json · query`
(long values show as the bare key) or `weekly · provider=subtitlecat`.
Clicking a chip fills the whole dialog (filters, query, format); press **OK** to
export, or tweak first. The most recently used preset is already preselected and
applied when the dialog opens, so exporting "the usual thing" is open → OK.
Hovering a chip shows a tooltip with **everything it would apply** — the full
query text the label truncates, plus the pinned `out` and `viewer` — and
**middle-clicking a chip removes that preset** instantly (no select-then-Delete).
Prefer the keyboard? **Ctrl+P** or **F5** applies the last-used preset from
inside the dialog.

Where everything sits — the downloads tab's **Save As…** dialog (the
history one is the same shape with Provider/Since/Until rows instead of
Format): the Preset row sits under the fields, the preset chips hug the
dialog's right edge directly under that row (hover for the tooltip,
middle-click to remove), and OK/Cancel live below the chips:

```
+--------------------------------------------------------------------------+
|  Export download state                                                   |
|                                                                          |
|   Format:                     [ csv v ]                                  |
|   Query (jq-style, optional): [ length                         ]         |
|                                query ok                                  |
|                                                                          |
|   Preset:                     [ failed v ]   [Apply] [Save] [Delete]     |
|                                                                          |
|                    [ failed · fmt=json · query ]  [ weekly · fmt=csv ]   |
|                                                                          |
|                     [ OK ]   [ Cancel ]                                  |
+--------------------------------------------------------------------------+
```

```
+--------------------------------------------------------------------------+
|  Filter history export                                                   |
|                                                                          |
|   Provider:                   [ (all) v ]                                |
|   Since (YYYY-MM-DD):         [ 2026-01-01                   ]           |
|   Until (YYYY-MM-DD):         [                              ]           |
|   Query (jq-style, optional): [                              ]           |
|                      2 matching row(s)                                   |
|                                                                          |
|   Preset:                     [ cat2026 v ]  [Apply] [Save] [Delete]     |
|                                                                          |
|               [ cat2026 · provider=subtitlecat · since=2026-01-01 ]      |
|                                                                          |
|                     [ OK ]   [ Cancel ]                                  |
+--------------------------------------------------------------------------+
```

With no presets saved the chip row shows a gray "no presets saved — type a
name and press Save" hint, and when a last-used preset exists it starts
with a gray "Ctrl+P/F5 applies the last preset —" reminder.

**Step 4 — reuse queries without naming them.** Run Query… (history and
downloads tabs) remembers the last 8 queries you ran per tab and offers them as
**chips under the query field** — newest first, long queries truncated with `…`.
Clicking one fills the field and live-validates it ("query ok — returns N
result(s)"), never runs it by itself; a tooltip holds the full query text.
Middle-click a chip to drop that query from the tab's recents (the field follows
the list if it showed the removed one). Curating recents is for experiments;
curating *presets* (Step 2) is for queries worth keeping forever.

The **Run Query…** dialog is smaller: the query combobox (its dropdown is
the per-tab recents) with the live-validation line under it, the
recent-query chips left-aligned under that — newest first, long queries
truncated with `…`, tooltip holds the full text, middle-click drops one —
and Run/Cancel at the bottom (Run records the query into the recents;
chips only ever fill and validate):

```
+--------------------------------------------------------------------------+
|  Run query — downloads                                                   |
|                                                                          |
|   Query (jq-style):  [ [].url                                  ]         |
|                      query ok — returns 3 result(s)                      |
|                                                                          |
|   [ [].url ]  [ length ]  [ .downloads[] | select(.st… ]                |
|                                                                          |
|   [ Run ]   [ Cancel ]                                                   |
+--------------------------------------------------------------------------+
```

**Step 5 — go fully headless.** Everything the GUI offers is scriptable, and the
preset store is the bridge. A Windows Task Scheduler task (or cron) running
`idm history --preset weekly --quiet` produces the same `reports/{date}-weekly.csv`
the GUI chip would — same evaluator, same writers, same file. To move your
curation between machines: `idm presets export share.json` on this one, `idm
presets import share.json` on the next (merge; `--replace` wipes first). And
`idm presets list --json` answers "what do I have?" in machine-readable form —
the same store, read the same way, from anywhere.

Batch list format (`urls.example.txt`):

```
# comments allowed
https://host/file1.zip
https://host/movie.mp4 -> my-movie.mp4
```

## Expiring download links

Signed CDN links (S3 presigned, tokenized CDNs, etc.) expire after minutes or hours.
PyIDM detects expiry from the HTTP status (401/403/410/419) **or** the body text
("link expired", "invalid signature", "unauthorized"…) and asks a *link provider* to mint
a fresh URL. Configure providers in `idm.json` (project) or `~/.idm/config.json` (global):

```json
{
  "link_providers": [
    {
      "match": "^https://cdn\\.example\\.com/files/([^/?]+)",
      "refresh_url": "https://api.example.com/v1/files/{1}/link",
      "url_field": "data.url"
    },
    {
      "match": "^https://drive\\.example\\.com/(?<id>[A-Za-z0-9]+)",
      "command": "python fetch_link.py {id}"
    }
  ]
}
```

How it works:

- `match` — regex tested against the URL. Capture groups become `{1}`, `{2}`… or named
  `{id}` (PCRE-style `(?<id>…)` groups are accepted and normalized).
- `refresh_url` — GET this URL; the fresh link is read from the dotted `url_field` path in
  the JSON response (e.g. `data.url`). A plain-text or single-line response containing just
  the URL also works.
- `command` — run a shell command instead; its stdout must contain the URL (last
  http(s) line wins). The original URL is passed in `IDM_URL`.
- Each download retries a refresh up to `max_refreshes` times (default 3); refreshes don't
  consume the network retry budget.

Test a provider without downloading anything:

```bash
idm refresh "https://cdn.example.com/files/abc123"
```

## When a download "succeeds" but the file won't open

Some sites answer a direct media URL with **HTTP 200 + a web page** instead of an error
(hotlink protection, consent walls, expired-but-200 CDNs). The bytes are a web page, so
the saved file is a `.jpg`/`.mp4` that no player or viewer can open — often with no file
extension at all when the URL had no filename.

PyIDM's **html_guard** (on by default) sniffs the first KiB of every fresh transfer and
refuses to save anything that is a web page or an HLS/M3U playlist, marking the download
as an error:

```
[error] https://…/poster.jpg: server sent a web page instead of the file
(hotlink protection, expired link, or consent wall) — html_guard refused to save it

[error] https://…/3007.webm: server sent an HLS/M3U playlist, not the media itself
(the video lives in the segment links inside it) — html_guard refused to save it
```

If that happens, the URL needs the site's cookies or a fresh signed link — the guard can't
make the site cooperate (a playlist answer means the URL points at a manifest, not the
media — only a downloader that speaks HLS can follow it). Leftover `.part` files saved by
an older version are detected as web pages or playlists and discarded on the next attempt.
Set `"html_guard": false` in the config to restore the old save-anything behavior, or
`"playlist_guard": false` to keep refusing web pages but save playlists (the magic-byte
check then still warns about them).

**Magic-byte validation.** When a download finishes, the guard also sniffs the saved
file's content (JPEG/PNG/GIF/WebP, MP4, Matroska/WebM, AVI, MP3/Ogg/FLAC/WAV, PDF,
ZIP, M3U playlists, HTML, plain text) and warns when it contradicts the filename:

```
[warn] 3007.webm: the URL served an M3U playlist, not the Matroska/WebM content
its name promises — saved as-is; it may not open or play correctly
```

This catches sites that serve, say, a JPEG under a `.mp4` name or an HLS playlist under
a video name — the download still completes (the content may be exactly what you asked
for under a sloppy name), but the log tells you why it won't play. Unrecognizable bytes
never trigger a warning, and `.bin`-style extensions the table has no opinion on are
skipped entirely. Filenames mangled by a broken `Content-Disposition` header are cleaned
at download time — multi-variant payloads like `130425,_360p.mp4,.mp4,_720p.mp4,` are
cut to the first real extension (`130425,_360p.mp4`), so they land with a proper name
instead of one the kind table can't read. `idm sanitize-names` applies the same rule
to files already on disk: a dry run by default (exit `1` when it found work),
`--apply` renames in place and never overwrites. (A *fresh* HLS/M3U response under a media name is refused outright by
html_guard these days — `"playlist_guard": false` restores this warn-only behavior for
those, and `idm verify` always warns about playlist files already on disk.)

`idm verify` applies the same check retroactively to everything already sitting in the
downloads folder (it honors the global `-o/--out` like every other command):

```
idm verify
[warn] 3007.webm: file saved, but the URL served an M3U playlist, not the
Matroska/WebM content its name promises — it may not open or play correctly

2 file(s) scanned in C:\Videos: 1 clean, 1 warned, 0 no opinion
```

It is warn-only — nothing is ever moved or deleted — unless you pass `--delete-warned`,
which moves every mismatched file into a `quarantine` folder inside the downloads
directory (`[quarantine] 3007.webm -> …`, name collisions get a ` (2)` suffix, the folder
is only created when there is something to move, and locked files stay put and keep the
run failing). `idm verify --json` gives scripts the same verdict per file (`kind`:
`ok` / `warned` / `skipped` / `ignored` / `moved`, plus the note and, for moved files,
the quarantine path); it exits `1` while anything warned is left in place — a completed
`--delete-warned` sweep therefore ends `0`. The payload shape lives in
[docs/automation.md](docs/automation.md). Deleted a file by hand? Its state
record lingers as a ghost that `idm resume` retries forever — `idm prune-state`
previews records whose file is gone, and `--apply` drops them (re-checked at
apply time; files are never touched). `--vacuum` rewrites the store without
indentation while it is open anyway and reports the bytes reclaimed — useful
on its own, since `idm.state.json` only ever grows (a missing store is never
created, and the reclaim clamps at 0 because the fresh `updated` timestamp
jitters the file size).

### The verify ignore list (`idm ignore`)

Files you have inspected and decided are fine — an HLS playlist deliberately kept under
a media name, say — never have to warn again: add them to the config-driven ignore list
and every verify scan reports them as `ignored` (dim `[ignored] name` lines) instead of
warned, and `--delete-warned` never sweeps them:

```
idm ignore                                   # list entries (stored in ~/.idm/config.json)
idm ignore add kept.webm '*.m3u' '130425*'   # plain name · *.ext by extension · prefix*
idm ignore remove kept.webm                  # exact names; unknown ones fail with exit 1
idm config get verify_ignore                 # the effective list, as JSON
```

Matching is case-insensitive; a plain name matches exactly, `*.ext` by extension, and
`prefix*` by prefix. When adding, the CLI sanity-checks the entries against the
downloads folder (`-o` overrides it): an entry matching nothing on disk draws a typo
warning that names the `idm ignore remove` undo, and several warned files sharing the
entry's extension draw a hint for the one `*.ext` pattern that would cover them all.
The list lives in the normal config layering (`~/.idm/config.json`,
`idm.json`), so `idm doctor` sees it and edits via `idm config set verify_ignore '[…]'
work too. The GUI's Verify action honors the same list — see below.

`idm restore` undoes a sweep: every quarantined file moves back into the downloads folder
(all of them by default, or just the named ones), name collisions get a ` (2)` suffix so
nothing is ever overwritten, and an emptied quarantine folder is removed. `idm restore
--discard` deletes the quarantined files for good instead — with either mode,
`--json` reports each file as `restored` / `discarded` / `failed` and exits `1` on any
failure.

### Per-domain headers (Referer / Cookie) for hotlink-protected sites

Most hotlink walls just want a `Referer` (or a site cookie) on the request. Configure
`domain_headers` in `idm.json` (project) or `~/.idm/config.json` (global) — each rule
pairs a URL regex with the headers to add to **every** request for that URL (probe,
parallel segments, and resume):

```json
{
  "domain_headers": [
    {
      "match": "supjav\\.com",
      "headers": {"Referer": "https://supjav.com/"}
    },
    {
      "match": "cdn\\.example\\.com",
      "headers": {"Cookie": "token=abc123"}
    }
  ]
}
```

- `match` — regex tested against the full URL (same syntax as `link_providers`);
  omit it for a catch-all rule that applies everywhere.
- Every matching rule is applied, **later rules win** on conflicting keys — so put
  site-specific rules after broad ones.
- Global `headers` still apply; domain rules are added on top of them.

Verify what actually gets sent by pointing a rule at any echo endpoint, e.g.
`https://httpbin.org/headers`, and downloading it:

```bash
idm get "https://httpbin.org/headers" -> sent-headers.json
```

## Multi-connection downloads

Like real IDM, big files are split across **parallel connections** (default 8) for much
higher throughput. PyIDM probes each file for size and Range support; if the server can
serve ranges and the file is at least 4 MiB, it downloads 8 segments concurrently into
`Name.ext.part0…7` and concatenates them on completion. Segments resume individually, an
expired link is refreshed **once** and shared by all segments, and anything unsupported
(no Range, small files) automatically falls back to a single connection.

Control it with `--segments` / the *Connections/file* spinner in the GUI, or the config:

```bash
idm get https://example.com/big.iso --segments 16
```

| Key | Default | Meaning |
|---|---|---|
| `segments` | `8` | parallel connections per file (1 disables segmentation) |
| `min_segmented_size` | `4194304` | files smaller than this stay single-connection |

## Subtitles (multi-provider chain)

Subtitles work even **without an API key**: providers are tried in order until one
succeeds — OpenSubtitles (needs a free key, best hash-exact matches), then
**YIFYSubtitles** (keyless: title → IMDb id via Wikidata → subtitle zips), then
**SubtitleCat** (keyless keyword catalog). One provider failing — down, blocked,
or out of quota — just moves the chain to the next.

OpenSubtitles key (optional, improves matching):

Get a **free API key**: create an account at <https://www.opensubtitles.com>, then
Profile → API Keys. Store it once:

```bash
idm config set opensubtitles_api_key YOUR_KEY
# or store it as a Windows USER environment variable (picked up by the frozen exes too):
idm config setenv OPENSUBTITLES_API_KEY YOUR_KEY
```

The GUI also has a **first-run wizard**: on a fresh setup (no key configured and the
wizard never answered) it opens a small dialog — paste your key, it's verified live
against the API, then saved to both `~/.idm/config.json` and the Windows user
environment. Choose **Skip for now** to stay keyless (the keyless providers keep
working); an **API key…** button in the subtitle panel reopens the dialog anytime.

Then:

```bash
idm subs "Movie.Name.2024.1080p.mkv"        # by exact-file hash (best match)
idm subs downloads/                          # every video file in a folder
idm subs "Some Series S02E03" -l en,vi      # by title (SxxEyy auto-detected)
```

- The local video file is matched with the OpenSubtitles **movie hash** (size + first/last
  64 KiB checksum), falling back to a filename search.
- Subtitles are saved next to the video as `Name.lang.srt` (or `.ass`/`.vtt` as served).
- Languages default to `subtitle_languages` in the config (default `en`).
- Provider order is configurable, e.g. `idm config set subtitle_providers "subtitlecat,opensubtitles"`.
- Within a SubtitleCat detail page the **largest valid .srt** in a requested
  language is preferred (big files are complete subtitles; small ones are often
  stubs), with progressive query shortening (`Movie 2024` → `Movie`).
- Every downloaded subtitle is **cue-validated**: it must parse to at least one
  well-formed timing pair (SRT/VTT `-->` or ASS `Dialogue:`), otherwise it's
  rejected and the chain tries the next candidate/provider. Cue counts appear
  in the log (`1746 cues`).
- OpenSubtitles blocks generic bot User-Agents (HTTP 403 `kong-user-agent-block`)
  and its download endpoint is `POST /download`; PyIDM handles both — the agent
  is configurable via `opensubtitles_user_agent`.
- Raw filename stems are rejected by OpenSubtitles (HTTP 400); queries are always
  cleaned, and dead file entries (404) are skipped in favor of the next candidate.

Check the health of each source at any time:

```bash
idm providers          # DNS + reachability + API key status, colored table
idm providers --deep   # also fetches a real subtitle from each keyless provider
```

Exit code is 0 unless a provider is fully DOWN (script-friendly). DNS failures
(e.g. ISP-blocked hosts) are detected and reported separately from HTTP problems.
- Keyless providers search by cleaned release name; OpenSubtitles matches by the exact
  movie hash of your file when a key is configured. Keyless providers cover popular movies;
  OpenSubtitles has the widest catalog for everything else.
- OpenSubtitles' free tier allows a limited number of downloads per day (~5); the remaining
  quota is printed after each fetch. If it's out of quota, the chain continues automatically.
- **Auto-play**: after each successful fetch the video launches in your media player with
  the subtitle attached. VLC gets `--sub-file`, MPC-HC/MPC-BE get `/sub`; with any other
  (or no) player the subtitle is additionally copied to `Name.srt` so the sidecar auto-load
  picks it up.

```bash
idm subs "Movie.mkv" --player vlc        # force a player (name or full path)
idm subs "Movie.mkv" --no-play           # download only, don't launch anything
idm config set subs_autoplay true        # GUI checkbox default
idm config set player "C:\path\to.exe"   # persistent player override
```

## GUI

```bash
idm-gui
```

The downloads tab's **Note** column surfaces non-fatal warnings from the download
engine: a row that finished but whose content doesn't match its name (see
[Magic-byte validation](#when-a-download-succeeds-but-the-file-wont-open)) shows
the warning text in the row and turns amber, while clean rows stay green.
`idm get --json` reports the same warning in each download's `note` field.

**Right-click the downloads list** for the quarantine workflow: **Verify files**
runs the magic-byte scan over the Save-to folder, moves mismatched files into a
`quarantine` subfolder, and reports the verdict per file (same engine and exit
semantics as `idm verify --delete-warned`); swept files show in the list as dim
**moved** rows (files from older runs — URLs the list never saw — get their own
dim row); **Restore quarantined files…** picks files to move back, undoing a
sweep (the GUI twin of `idm restore` — it never overwrites, an emptied
quarantine folder is removed, restored rows turn green again, and the file you
right-clicked arrives preselected in the picker). The Verify action honors the
same ignore list as `idm verify` (`idm ignore add …` / the config's
`verify_ignore` key): ignored files are counted in the result but never warned
about nor swept, and after a sweep that flagged files the dialog offers to add
them to the list for you. **Ignore this file in verify…** adds the row you
right-clicked (edit the entry into a `*.ext` or `prefix*` pattern before
confirming), **Manage verify ignore list…** shows every entry with a picker to
remove some. **Prune stale records…** is the GUI twin of `idm prune-state`: it
previews state records whose file no longer exists in the Save-to folder and
offers to remove them from `idm.state.json` (re-checked at apply time; files
are never touched).

Mangled download names are surfaced right in the downloads tab: a row whose
file on disk has a mangled Content-Disposition name gets a violet **rename**
badge and its Note column shows the clean name it should have —
`mangled name — clean name is: 130425,_360p.mp4` — the same preview
`idm sanitize-names` prints. **Fix this file name…** renames just that file
to the previewed clean name (the per-file twin of `idm sanitize-names
--apply`: collision-safe, nothing is ever overwritten); the Tools menu's
**Sanitize file names (dry run)…** still covers the whole folder in one
pass, and badges clear automatically once the name is fixed by any route
(CLI, bulk apply, or the per-file action).

**Tools → Sanitize file names (dry run)…** fixes the other half of the
mangled-name problem straight from the menu bar: it scans the Save-to folder
with the exact engine behind `idm sanitize-names`, logs each pending rename
(`[rename] old -> new`) in the GUI log, and pops an offer to apply them —
accepting renames the files in place, never overwriting (a colliding name gets
a ` (2)` suffix). A folder with nothing to fix just gets an "all clean" note,
so the menu item doubles as a one-click name-health check.

**Help → About PyIDM…** shows the version plus the *effective* merged config —
every setting with the layer that set it (default, `~/.idm/config.json`,
`./idm.json`, or environment), secrets masked, with one-click copy as JSON.
Double-click any row (or select + **Edit selected value…**) to change it: edits
save to `~/.idm/config.json`, with **layer-aware warnings** — an env var that will
override your edit, an `idm.json` value that shadows it, the API-key mirror to
the Windows user environment — plus **Reset to default** (removes the key from
the user config). Numbers/JSON coerce per key; string keys like the API key are
never coerced (an all-digit key stays a string). List keys like `verify_ignore` and
`link_providers` get a **structured row editor** alongside the raw JSON entry: the
current entries are listed one per row, **Add entry** appends (verbatim — a
comma-heavy filename stays whole), **Remove selected** deletes rows, and every change
re-writes the JSON field so **Save** works exactly as before. The JSON entry still
accepts a JSON array, a single filename/pattern, or several separated by spaces.

The subtitle panel keeps a **history table** — every downloaded .srt with
its language, the provider that produced it, file size, and cue count.
Double-click (or press Enter on) a row to open the .srt with its default
app; right-click offers **Show in Explorer** (selects the .srt in its
folder) and **Open video folder** (opens the movie's folder — handy when
the video and its subtitle live in different directories). The history
persists to `~/.idm/subtitle_history.json` and is
restored on the next start (capped at the last 200 entries); **Clear**
empties it — downloaded files are never deleted. **Copy CSV** and
**Copy MD** put the whole table on the clipboard as a CSV (opens directly
in Excel/LibreOffice) or a markdown table (pastes into issues, READMEs,
and chat apps as a real table), and **Save As...** writes the same export
to a real .csv or .md file (format follows the chosen extension).
**Save As...** first opens a filter dialog — pick one provider (exact,
case-insensitive) and/or a `since`/`until` date window (`YYYY-MM-DD`,
whole days, inclusive) with a live "N matching row(s)" counter; leaving
everything empty exports the full history as before. Both export dialogs
also have a **Preset** row: type a name and **Save** to store the current
format/filters/query as a named preset, **Apply** to restore one, **Delete**
to remove it — the same presets `idm presets` / `--preset` manage on the CLI
(see the automation section of the README for the CLI examples). One
**clickable chip per preset** sits under the row ("name · settings-hint")
for one-click apply, and the last used preset is preselected and applied
when the dialog opens, so a preset saved from the CLI is one click away
(just press OK). Hovering a chip shows a tooltip with **everything it would
apply** — including the full query text the label truncates — and
**middle-clicking a chip removes that preset** (same as selecting it and
pressing Delete, without the two steps). Keyboard-only: **Ctrl+P** (or
**F5**) inside the dialog applies the last-used preset without touching
the mouse.

**Run Query…** (on the history and downloads button stacks) goes further:
enter any jq-style query, see it live-validated against the real data (error
in red, else "query ok — returns N result(s)"), and press **Run** to open a
sortable results window — lists of objects become rows (union of keys as
columns), scalars/lists land in a single `value` column. Click a column
heading to sort by it (numbers sort numerically), click again to reverse.
**Copy JSON** puts the exact CLI-style payload on the clipboard (the same
bytes a `--json` export would hold) and **Copy TSV** a header+rows grid for
spreadsheets; a bad query opens nothing and logs a warning.

**Tools → Run report preset** runs the report-only preset kinds
(`stats` / `providers`) right from the menu bar: every preset saved with
`idm presets add stats NAME …` / `idm presets add providers NAME …` gets a
**Run <kind> preset: <name>** entry that builds the same JSON report the
CLI's `--preset` flags produce, applies the preset's query, writes the
pinned `out` file (`{date}`/`{kind}` placeholders expand exactly as on the
CLI), and prints `exported … -> <path>` in the GUI log — opening it in the
preset's `viewer` when one is pinned. Providers presets run their health
checks off the UI thread (like the providers tab); stats reports are built
and written in the same tick. No subprocess is involved, so the file is
byte-identical to what `idm stats --preset` / `idm providers --preset`
would export — one menu click away. Presets added while the GUI is open
show up after **Tools → Rebuild this menu after adding presets**.

The table is **sortable** (Newest, fewest/most cues, smallest/largest —
rows with unknown quality always sort last) and **weak downloads are
highlighted in amber** for review: files under ~20 KB or with fewer than
50 valid cues are likely stubs, truncated, or ad-laden copies. Rows whose
`.srt` no longer exists on disk are **greyed out** so the history stays
honest after you clean up your folders (missing wins over amber when both
apply; rows without a recorded path are left unflagged). A **Relocate...**
context-menu action repairs grey rows: pick the folder that now holds the
subtitle (exact filename first, then a unique same-stem file such as a
renamed `.ass`), or the file itself — the history is updated, persisted,
and the row un-greys immediately. Re-sorting
uses the persisted history, so the view applies to everything ever
fetched, not just the last batch. A status line in the panel shows the
entry count, total downloaded size, per-provider breakdown, and the oldest
entry's age (`12 entries — 18.1 MB — opensubtitles 7, subtitlecat 5 —
oldest from 6d ago`) so it's obvious when the history has grown stale and
**Clear** is worth pressing.

History **auto-prune**: set `subtitle_history_max_age_days` in config (or
`idm config set subtitle_history_max_age_days 30`) to drop entries older
than that age — pruning runs when the GUI starts (logged, and persisted so
entries don't resurrect) and on every save. `0` (the default) disables it;
stamped entries exactly at the cutoff are kept, and legacy rows predating
timestamps are never pruned.

The **Providers tab** runs the same health checks as `idm providers` inside the
GUI: status per provider (OK/WARN/DOWN, color-coded), actionable hints, and an
optional deep check that fetches a real subtitle from each keyless source.

Paste or load a URL list, choose the output folder and worker count, press **▶ Start
batch**, watch per-file progress/speed, and stop anytime — partial files stay resumable.
The subtitle panel fetches subs for a folder of videos.

## Browser extension (collect links)

The bundled **browser-extension/** folder is a Manifest V3 extension that
sends links to a tiny localhost collector so the browser feeds PyIDM
directly. Start the collector from the GUI (**Tools ▸ Start browser
collector** — it logs the port and every capture) or headless with `idm
collect`:

```
idm collect              # or: idm collect --port 27492
```

Then load the extension once: **chrome://extensions → Enable Developer
mode → Load unpacked → browser-extension/** (Edge/Brave: the same page).
The popup offers **Add page links** (every http(s) link on the page, in a
checklist), **Media only** (video/audio/image/resource URLs), and **Queue
page URL**; a right-click menu adds **Send link to PyIDM…** on any link.
Captures land in `~/.idm/collect_queue.json` (pending + last 200 handled,
duplicates skipped), the GUI log shows each one, and **Tools ▸ Download
queue now** starts them all toward the Save-to directory. Check **start
downloading immediately** in the popup to skip the queue and download at
capture time instead. The extension's options page changes the port (the
`collector_port` config key, default 27492); the collector binds
127.0.0.1 only.

## Configuration

Defaults live in code; overrides merge from `~/.idm/config.json`, then `./idm.json`, then
environment variables (`IDM_OUT`, `IDM_WORKERS`, `OPENSUBTITLES_API_KEY`).

| Key | Default | Meaning |
|---|---|---|
| `out_dir` | `downloads` | default output directory |
| `workers` | `4` | parallel downloads in a batch |
| `retries` | `5` | network retry attempts per file |
| `html_guard` | `true` | refuse to save web-page bytes as a download, then warn when a finished file's content contradicts its name (magic-byte check) |
| `playlist_guard` | `true` | part of html_guard: also refuse HLS/M3U playlists served as the media itself (set `false` to save them; the magic-byte check still warns) |
| `max_refreshes` | `3` | max link-refresh cycles per file |
| `timeout` | `30` | request timeout (seconds) |
| `chunk_size` | `1048576` | stream chunk size (bytes) |
| `overwrite` | `false` | re-download instead of skipping existing files |
| `domain_headers` | `[]` | per-domain extra request headers (Referer/Cookie for hotlink-protected sites) — see below |
| `subtitle_languages` | `en` | default subtitle languages |
| `subs_autoplay` | `false` | open the video in a player after subtitle download |
| `player` | — | player executable (auto-detects VLC/MPC-HC/MPC-BE when empty) |
| `opensubtitles_api_key` | — | OpenSubtitles API key (optional) |
| `subtitle_providers` | `opensubtitles,yifysubtitles,subtitlecat` | provider try-order |
| `headers` | — | extra HTTP headers for downloads |
| `link_providers` | `[]` | expiring-link refresh rules (see above) |

```bash
idm config list                  # show ~/.idm/config.json + env-var status
idm config get workers
idm config set workers 8
idm config setenv NAME VALUE     # persist a Windows USER env var (HKCU + broadcast)
idm config getenv NAME           # read it back (secrets masked)
idm config delenv NAME           # remove it
```

`idm config set opensubtitles_api_key KEY` automatically mirrors the value into the
`OPENSUBTITLES_API_KEY` user environment variable, so the packaged `pyidm.exe` /
`pyidm-gui.exe` see it even when run from another folder, and so does every new
terminal — no logoff needed (a WM_SETTINGCHANGE broadcast refreshes Explorer).

## Config diagnosis

Config layering (defaults ← `~/.idm/config.json` ← `./idm.json` ← environment)
is powerful but can silently surprise you: a value you set in one place may be
overridden by another without any error. `idm doctor` audits every key across
all layers:

```bash
idm doctor
```

- **WARN** — a key's value differs between layers and a higher layer wins:
  e.g. `workers: 8` in `idm.json` while `IDM_WORKERS=16` is set — the 8 you
  wrote is silently never used. Fix by removing one of the definitions.
- **INFO** — the same value is defined in multiple layers (harmless shadowing,
  but worth tidying up).
- **Unknown keys** — a config-file key that isn't a known setting (likely a
  typo); it is ignored entirely by the merge.

The table shows, per finding, the *hidden* value (the one that is NOT used),
which layer shadows it, and the *effective* value. Exit code is 1 when there
are warnings (script-friendly); secrets are always masked.

## Resume behavior

- Partial downloads are kept as `Name.ext.part` and continued with `Range` requests
  (an `ETag`/`If-Range` mismatch restarts cleanly).
- Failures are recorded in `<out_dir>/idm.state.json`; `idm resume` re-runs everything
  that didn't finish.
- A record whose file (and `.part` partial) is gone is a **ghost**: `idm resume` skips
  it (a retry could only fail), reports it, and leaves the record for `idm prune-state`
  to remove — the GUI's Tools menu carries the same twin actions (Resume unfinished
  downloads…, right-click ▸ Prune stale records…).
- Duplicate filenames are auto-renamed `name (1).ext`; existing finished files are skipped
  unless `--overwrite`.

## Development

```bash
pip install -e . pytest
pytest tests -q
```

The integration tests spin up a local HTTP server exercising resume, mid-transfer drops,
expiring links with provider refresh, cancellation, and batch dedupe — no internet needed.

## Project layout

```
idm/
  cli.py              command-line interface
  core.py             download engine (resume, retries, refresh, batch)
  links.py            expiry detection + link providers
  subtitles.py        OpenSubtitles client (hash, search, download)
  collect.py          browser-extension bridge (localhost collector)
  config.py           configuration merging
  state.py            persistent download state
  health.py           provider health checks (`idm providers`)
  jq.py               jq-style --query evaluator
  players.py          media-player launching with subtitles
  utils.py            helpers
  gui.py              Tk app assembly + core tabs (batch, providers, menus)
  gui_common.py       pure GUI helpers (formatters, filters, sorting, queries)
  about.py            About dialog + live config editor (App mixin)
  exports.py          Save As dialogs, report presets, API-key wizard (App mixin)
  query_dialogs.py    Run Query… dialogs + results window (App mixin)
  subtitles_panel.py  subtitle history panel (App mixin)
tests/                pytest suite (615 tests)
```

The `App` class in `gui.py` is assembled from the four mixin modules
(`class App(AboutDialogsMixin, ExportDialogsMixin, QueryDialogsMixin,
SubtitlesPanelMixin, _AppBase)`). Mixin bodies reach gui-layer helpers
through `_g = idm.gui`, so helpers stay patchable via `idm.gui` — the
test suite relies on that late binding.
