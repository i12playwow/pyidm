# Automating PyIDM — the `--json` & `--query` reference

Every reporting and download command in the PyIDM CLI can emit machine-readable
JSON on stdout, so scripts can drive downloads, audit results, and read stats
without scraping human tables. This page lists every `--json` surface, the
exact payload shape it prints, its exit codes, and the jq-style `--query`
filter for extracting single fields without jq installed.

Applies to PyIDM **1.11.20+**. Paths and sizes in the examples are real output
from a live run; substitute your own URLs, dates, and directories.

Conventions that never change, on every command:

- JSON goes to **stdout only** — one JSON document, nothing else. Human
  messages (progress bars, log lines, warnings) never mix into it.
- Written with `json.dumps(..., indent=2)`, trailing newline. ASCII-escaped,
  so any Windows console codepage can pipe it safely.
- **Exit codes mirror the text mode** of the same command. The JSON itself is
  never the reason for an exit code; it reports the same result.
- All byte counts are **real integers** (no "3.1 KB" human units in JSON).
- On stderr-visible problems (bad `--query`, argparse errors) nothing partial
  is printed to stdout.

## Quick index

| Command | JSON flag | Payload shape | Exit codes |
|---|---|---|---|
| `idm get` | `--json` | object | 0 all done/skipped · 1 any error/cancelled |
| `idm batch` | `--json` | object | same as `get` · 1 empty list |
| `idm resume` | `--json` | object (same shape as `get`) | 0 all done/skipped or nothing to resume · 1 any error/cancelled |
| `idm downloads` | `--json` | array of records | 0 ok · 1 invalid filter |
| `idm history` | `--json` | array of entries | 0 ok · 1 invalid filter |
| `idm stats` | `--json` | object (both stores) | always 0 |
| `idm providers` | `--json` | object (health report) | 0 all ok · 1 something down |
| `idm presets` | `--json` | object (presets per dialog kind) | always 0 (list) |
| `idm verify` | `--json` | object (per-file magic-byte scan) | 0 clean · 1 any file warned |
| `idm restore` | `--json` | object (per-file quarantine moves) | 0 all handled · 1 any failure |
| `idm sanitize-names` | `--json` | object (per-file rename plan) | 0 clean/applied · 1 would-rename or failure |
| `idm prune-state` | `--json` | object (stale-record preview/apply) | always 0 (files are never touched) |
| `idm ignore` | `--json` | object (entries + what add/remove changed) | 0 list/add or something removed · 1 usage or nothing removed |
| `idm collect` | (HTTP bridge, no --json) | see its section below | 0 until Ctrl+C |

Every flag above also accepts **`--query Q`** (which implies `--json`) and
**`-r/--raw`** — see [the `--query` filter](#the---query-filter) below.
New to presets? **A preset round trip** (end of the presets section) walks
one through CLI → GUI chip → exported file → a second machine.

---

## `idm get --json`

Downloads one or more URLs. `--json` suppresses the progress bars and log
lines, so stdout is exactly the summary document.

```
idm get https://example.com/movie.mp4 https://example.com/bad.bin --json
```

Exit `1` here because the second URL failed (HTTP 500):

```json
{
  "count": 2,
  "ok": 1,
  "all_ok": false,
  "total_bytes": 262144,
  "by_status": {
    "done": 1,
    "error": 1
  },
  "skipped_count": 0,
  "skipped": [],
  "downloads": [
    {
      "url": "https://example.com/movie.mp4",
      "filename": "movie.mp4",
      "status": "done",
      "total_bytes": 262144,
      "message": "",
      "note": "",
      "dest": "C:\\Users\\you\\downloads\\movie.mp4"
    },
    {
      "url": "https://example.com/bad.bin",
      "filename": "",
      "status": "error",
      "total_bytes": 0,
      "message": "HTTP 500",
      "note": "",
      "dest": ""
    }
  ]
}
```

Fields:

- `count` / `ok` / `all_ok` — how many URLs were requested, how many
  succeeded, whether *all* did. `ok` counts `done` **and** `skipped`
  (a file that already existed and wasn't `--overwrite`d counts as ok).
- `total_bytes` — bytes actually transferred this run (skipped files count
  their on-disk size, errored ones count 0).
- `by_status` — map of status → count. Possible statuses: `done`, `skipped`,
  `error`, `cancelled`, `pending`, `downloading` (the last two only appear if
  something prevented completion).
- `skipped_count` / `skipped` — always present, `0` / `[]` outside `idm
  resume` (see that section: ghost records it declined to retry).
- `downloads[]` — one object per URL, same order as the command line:
  - `url` — the original URL as given.
  - `filename` — the server-derived or `->` renamed filename; `""` when the
    failure happened before a name was known (e.g. connection refused, HTTP 500).
  - `status` — see above.
  - `total_bytes` — bytes this URL transferred.
  - `message` — failure reason (`"HTTP 500"`, `"network error probing after N
    attempt(s): ..."`) or the skip note; `""` on `done`/`skipped` rows.
  - `note` — non-fatal warning shown in the GUI Note column, e.g. the
    magic-byte kind-mismatch warning (`"file saved, but the URL served …"`)
    on a `done` row; `""` when the file is clean.
  - `dest` — absolute path of the written file; `""` when nothing was written.

Exit codes: `0` every URL done/skipped · `1` any error/cancelled. With a bad
`--query`: `1` and nothing on stdout.

## `idm batch --json`

Same payload as `get`, from a batch list file (one URL per line, `#`
comments, optional `url -> name` rename):

```
idm batch urls.txt --json
```

Exit `0` when every URL succeeded:

```json
{
  "count": 2,
  "ok": 2,
  "all_ok": true,
  "total_bytes": 524288,
  "by_status": {
    "done": 2
  },
  "skipped_count": 0,
  "skipped": [],
  "downloads": [
    {
      "url": "https://example.com/album.zip",
      "filename": "music.zip",
      "status": "done",
      "total_bytes": 262144,
      "message": "",
      "note": "",
      "dest": "C:\\Users\\you\\downloads\\music.zip"
    },
    {
      "url": "https://example.com/report.pdf",
      "filename": "report-2026-09-25.pdf",
      "status": "done",
      "total_bytes": 262144,
      "message": "",
      "note": "",
      "dest": "C:\\Users\\you\\downloads\\report-2026-09-25.pdf"
    }
  ]
}
```

Special cases, chosen to match the text mode exactly:

- **Empty list** (no URLs in the file): exits `1` like text mode — it's an
  input error — but still prints the zero payload so your script can parse it:
  `{"count": 0, "ok": 0, "all_ok": true, "total_bytes": 0, "by_status": {},
  "skipped_count": 0, "skipped": [], "downloads": []}`.
- An empty `idm get` argument list can't happen (argparse requires ≥1 URL).

## `idm resume --json`

Retries everything unfinished in the state file. The payload is the same
shape as `idm get --json`; the URL list comes from the state file instead of
the command line, and the URLs must still be reachable.

```
idm -o C:\Users\you\downloads resume --json
```

Exit `1` here because the second URL failed again (HTTP 500):

```json
{
  "count": 2,
  "ok": 1,
  "all_ok": false,
  "total_bytes": 262144,
  "by_status": {
    "done": 1,
    "error": 1
  },
  "skipped_count": 0,
  "skipped": [],
  "downloads": [
    {
      "url": "https://example.com/album.zip",
      "filename": "album.zip",
      "status": "done",
      "total_bytes": 262144,
      "message": "",
      "note": "",
      "dest": "C:\\Users\\you\\downloads\\album.zip"
    },
    {
      "url": "https://example.com/bad.bin",
      "filename": "",
      "status": "error",
      "total_bytes": 0,
      "message": "HTTP 500",
      "note": "",
      "dest": ""
    }
  ]
}
```

Fields are exactly those of `idm get --json`, plus two resume-only keys:
`skipped_count` and `skipped` (a list of `{url, filename}` — the ghost
records that were **not** retried because their file no longer exists;
every skipped row also appears first in `downloads` with status
`"skipped-ghost"` and a message pointing at `idm prune-state`). Behavior
notes:

- Only records with `status != "done"` are retried; `done` records are
  ignored entirely (not even reported).
- A record whose file (and any `.part` partial) is gone is a **ghost**:
  retrying could only fail, so it is skipped and reported instead — and
  its record is left in the state file for `idm prune-state` to remove.
  Ghosts never fail the run (`all_ok` ignores them).
- `filename` hints from the state file are honored.
- Successfully finished records are removed from the state file, exactly
  like text mode.
- **Nothing to resume** (missing or empty state file): exits `0` and prints
  the zero payload:  `{"count": 0, "ok": 0, "all_ok": true, "total_bytes": 0,
  "by_status": {}, "skipped_count": 0, "skipped": [], "downloads": []}` —
  so a scheduled retry loop can run unconditionally and just parse the
  result.
- Exit codes: `0` all done/skipped (or nothing to resume) · `1` any
  error/cancelled · `1` bad `--query` with nothing on stdout.

`idm resume --query '.downloads[].filename' -r` streams the finished names,
and `--query '.ok == .count'` answers "did everything succeed" in one token.

## `idm downloads --json`

Lists the download state file — what `idm resume` would retry. Payload is an
**array** of raw state records (plus the `url` key). `-o DIR` (before the
subcommand) selects which state file to read; the read never creates one.

```
idm -o C:\Users\you\downloads downloads --json
```

```json
[
  {
    "status": "error",
    "filename": "big.iso",
    "size": 3221225472,
    "updated": 1758748800,
    "message": "HTTP 403",
    "url": "https://cdn.example.com/big.iso"
  }
]
```

Fields: every field the state file holds per record — typically `status`,
`filename`, `size`, `updated` (Unix timestamp, local wall clock), `message`
where present — plus `url`. Unfinished records only: `done`/`skipped` records
are removed from the state file on completion, so an empty array `[]` means
"nothing to resume".

Filters work in JSON mode too: `--status error`, `--since 2026-09-01`,
`--until 2026-09-25` (an invalid `--since/--until` exits `1`).

Exit codes: `0` ok (including empty) · `1` invalid date filter.

## `idm history --json`

The subtitle download history — the same OK entries the GUI history panel
shows, every field preserved. Payload is an **array**.

```
idm history --json
```

```json
[
  {
    "path": "C:/Videos/Inception 2010.mkv",
    "ok": true,
    "dest": "C:/Videos/Inception 2010.en.srt",
    "language": "en",
    "provider": "opensubtitles",
    "size": 51200,
    "cues": 812,
    "ts": 1758748800
  }
]
```

Fields: `path` (video), `dest` (subtitle file written), `language`,
`provider`, `size`, `cues`, `ts` (Unix timestamp), `ok` (always `true` here —
failed attempts are not recorded), plus any extra fields a provider stored.
`--provider`, `--since`, `--until` filter before output; an invalid date
exits `1`.

`idm history --json` and `idm history --query .` print the identical
document — the query mode only reads the same data.

## `idm stats --json`

One-glance summary of both stores. Read-only: missing files count as zeros,
nothing is created.

```
idm -o C:\Users\you\downloads stats --json
```

```json
{
  "downloads": {
    "path": "C:\\Users\\you\\downloads\\idm.state.json",
    "pending": 1,
    "total_bytes": 3221225472,
    "by_status": {
      "error": 1
    },
    "oldest": {
      "url": "https://cdn.example.com/big.iso",
      "filename": "big.iso",
      "age_seconds": 31589201
    }
  },
  "subtitles": {
    "path": "C:\\Users\\you\\.idm\\subtitle_history.json",
    "entries": 1,
    "ok": 1,
    "failed": 0,
    "total_bytes": 51200,
    "by_provider": {
      "opensubtitles": 1
    },
    "weak": 0,
    "oldest": {
      "video": "Inception 2010.mkv",
      "age_seconds": 31589201
    }
  }
}
```

Fields:

- `downloads.path` / `subtitles.path` — which files were summarized.
- `downloads.pending` — number of unfinished records; `total_bytes` sums their
  `size` fields; `by_status` is the status → count map; `oldest` is
  `null` when the store is empty, otherwise the oldest record with its
  `age_seconds`.
- `subtitles.entries` / `ok` / `failed` — history counts; `total_bytes` sums
  subtitle sizes; `by_provider` maps provider name → count of OK entries;
  `weak` counts OK subtitles that look truncated/placeholder; `oldest` is the
  oldest OK entry (`null` when none).

Exit code: always `0`. (`--watch` is text-mode only and rejects `--json`,
`--query`, `--export`, and `--preset`.)

## `idm providers --json`

Health check for each configured subtitle provider (DNS, reachability, API
key). Add `--deep` to also fetch a real subtitle end-to-end; the payload
shape is the same, only `mode` changes.

```
idm providers --json
```

Exit `1` here because one provider is down:

```json
{
  "mode": "quick",
  "all_ok": false,
  "providers": [
    {
      "name": "opensubtitles",
      "label": "OpenSubtitles",
      "status": "ok",
      "detail": "API key present",
      "endpoint": "https://api.opensubtitles.com",
      "hints": []
    },
    {
      "name": "subtitlecat",
      "label": "SubtitleCat",
      "status": "ok",
      "detail": "reachable",
      "endpoint": "https://subtitlecat.com",
      "hints": []
    },
    {
      "name": "podnapisi",
      "label": "Podnapisi",
      "status": "down",
      "detail": "DNS blocked",
      "endpoint": "https://www.podnapisi.net",
      "hints": [
        "ISP-level block detected — the chain skips it automatically"
      ]
    }
  ]
}
```

Fields: `mode` (`"quick"` or `"deep"`), `all_ok` (true when nothing is
`down`), and one object per provider: `name` (config identifier),
`label` (display name), raw `status` (`ok` / `warn` / `down` — never the
display marks), human `detail`, `endpoint` (may be `null` for local checks),
and `hints` — actionable strings, e.g. how to set the OpenSubtitles API key.

Exit codes: `0` `all_ok` true · `1` something is down. Warn states do not
fail the check.

---

## `idm presets --json`

Named export presets — filter+query combinations saved from the GUI export
dialogs (or with `idm presets add`) and reusable with
`idm history --preset NAME` / `idm downloads --preset NAME`; the two report
commands have their own kinds: `idm stats --preset NAME` /
`idm providers --preset NAME` accept **query**, **out** and **viewer**
settings — a pinned `out` file gets the command's JSON report written to it
(any `query` result shape applies, `--export FILE` does the same without a
preset), and explicit flags always win over the preset's stored values. In
the GUI,
both Save As… dialogs preselect and apply the **last-used preset** on open,
so a preset created here (or with `idm presets add`) needs only an OK press;
they also show one clickable **chip per preset** under the Preset field
(labelled `name · setting-hints`) that fills the dialog in one click,
**Ctrl+P / F5** inside the dialog applies the last-used preset from the
keyboard, and **middle-clicking a chip removes that preset** (the same
store change and log line as the Delete button, without selecting it
first). The menu bar's **Tools ▸ Run report preset** entries run the
stats/providers presets in-process (same payload, query, `{date}`/`{kind}`
out expansion, and viewer opening as `--preset` here — no subprocess), and
log `exported <kind> report -> <path>` to the GUI log; presets added while
the GUI is open appear after **Tools ▸ Rebuild this menu after adding
presets**.

```
idm presets --json
```

```json
{
  "history": {
    "cat2026": {
      "provider": "subtitlecat",
      "since": "2026-01-01",
      "until": "",
      "query": "[].provider"
    }
  },
  "downloads": {
    "errors": {
      "fmt": "json",
      "query": "[.[] | select(.status == \"error\")] | length"
    }
  },
  "stats": {
    "weekly": {
      "query": ".downloads.pending",
      "out": "reports/{date}-{kind}.json",
      "viewer": "code"
    }
  },
  "providers": {
    "nightly": {
      "query": "[.providers[] | select(.status != \"ok\")]",
      "out": "providers-{date}.json"
    }
  }
}
```

Keys are the preset names (one object per dialog kind); settings hold the
stored values, empty strings meaning "no filter". Managing from the CLI:

```
idm presets add errors --kind downloads --set fmt=json \
  --set 'query=[.[] | select(.status == "error")] | length'
idm presets add weekly --kind history --set provider=subtitlecat \
  --set query=[].provider --set out=weekly.json --set viewer=code
idm presets add daily --kind downloads \
  --set 'query=[.[] | select(.status == "error")] | length' \
  --set out=reports/{date}-{kind}.json
idm presets add nightly --kind providers \
  --set 'query=[.providers[] | select(.status != "ok")]' \
  --set out=providers-{date}.json
idm presets remove errors --kind downloads
idm presets list --kind history
```

Using the report kinds on the commands themselves:

```
idm stats --preset weekly --quiet
idm providers --preset nightly
idm stats --export report.json --query .downloads.pending
```

A preset can also pin an output file (`out`, relative to the current
directory) and a viewer app: applying it writes straight to that file and
offers to open it there — explicit `--out`/`--export` still wins. A pinned
`query` also changes what a `.json` export file holds: the query result,
just like `--query` on stdout. Pinned paths support **`{date}`** (local
`YYYY-MM-DD` at write time) and **`{kind}`** (`history`/`downloads`)
placeholders — e.g. `out=reports/{date}-{kind}.json` — and missing parent
folders are created automatically, so daily exports never overwrite
yesterday's file:

Presets live in `~/.idm/export_prefs.json` (shared with the GUI's remembered
dialog settings and recent queries). `--json` lists always exit `0`; add and
remove exit `0` on success, `1` on an unknown setting key, blank name, or
unknown preset. Valid `--set` keys: history — `provider`, `since`, `until`,
`query`, `out`, `viewer`; downloads — `fmt`, `query`, `out`, `viewer`.

Share presets between machines as a JSON file — export writes all presets
({kind: {name: settings}}), import merges them (same-named presets are
overwritten; `--replace` drops each kind's existing presets first). The file
path may stand in the name position (`idm presets export share.json`):

```
idm presets export share.json          # 2 preset(s) written
idm presets import share.json          # merged; same-named overwritten
idm presets import share.json --replace
```

Import exits `0` with an `imported N preset(s) (merged|replaced; M skipped)`
line, `1` for a missing/unparseable/non-object file. Skipped entries are
malformed ones, blank names, and unknown kinds — files from newer versions
import cleanly because unknown setting keys are dropped by the whitelist.

### A preset round trip — CLI → GUI → file → second machine

One pass through every surface a preset touches, with one store
(`~/.idm/export_prefs.json`) behind them all:

**1. Create it from the CLI.** Seed some downloads (real batches have
failures), then save the export recipe as a named preset:

```
idm batch urls.txt -o downloads --json    # 2/3 succeeded, 1 error
idm presets add errors --kind downloads \
  --set fmt=json \
  --set 'query=[.[] | select(.status == "error")] | length'
idm presets list --kind downloads         # downloads/errors  fmt=json, query=...
```

The GUI reads the same file — nothing else to sync.

**2. Apply it from a GUI chip.** Launch the GUI and open the downloads
tab's **Save As…** dialog: a chip labelled **`errors · fmt=json · query`**
sits under the Preset row (hover it — the tooltip shows everything it
would apply, including the full query text the label abbreviates). One
click fills Format and Query, **OK** runs the export, and the GUI log
prints where the file went (`download state exported to failed.json (1
row(s), json, query …)`). The last-used preset is preselected next time
(just press OK, or **Ctrl+P** from the keyboard), and **middle-clicking a
chip removes that preset** from the shared store.

**3. Verify the exported file.** The GUI wrote the exact bytes a scripted
export produces, because both share one evaluator and writer:

```
idm downloads --json --query '[.[] | select(.status == "error")] | length'
1
```

Same `1` as the file holds — interactive and scripted exports can never
drift.

**4. Round-trip a report preset the same way.** The stats/providers kinds
run from the CLI's `--preset` flags or the GUI menu bar's **Tools ▸ Run
report preset** entries:

```
idm presets add weekly --kind stats \
  --set query=.downloads.pending \
  --set out=reports/{date}-{kind}.json --set viewer=code
idm stats --preset weekly --quiet        # -> reports/2026-09-28-stats.json
```

(The seeded `stats.weekly` in the `--json` example above is exactly this
preset.) In the GUI the same preset is a **Tools ▸ Run stats preset:
weekly** menu entry (added while the GUI was running? **Tools ▸ Rebuild
this menu after adding presets**); clicking it writes the same dated file
and logs `exported stats report -> <path> (query .downloads.pending,
preset weekly)`.

**5. Move the whole collection to a second machine.** Export everything
as one shareable file, carry it over, and import on machine 2 (the file
path stands in the name position):

```
idm presets export share.json             # 2 preset(s) written
idm presets import share.json             # merged; same-named overwritten
```

Machine 2's GUI shows the imported presets as chips the next time an
export dialog opens, and its Tools menu lists the report presets after a
rebuild. Start clean instead with `import share.json --replace` (drops
each kind's existing presets first). Malformed entries and unknown setting
keys are skipped on import, so share files stay compatible across
versions.

---

## `idm collect` (browser extension bridge)

Runs the localhost collector the bundled **browser-extension/** talks to:
the extension's popup scans the page for links/media, and anything sent is
queued in `~/.idm/collect_queue.json` for the GUI (**Tools ▸ Download queue
now**) or downloaded immediately (`POST /download`). `idm collect` runs it
in the foreground until Ctrl+C; the GUI's Tools menu starts/stops the same
server in-process.

```
idm collect --port 27492
```

Endpoints (127.0.0.1 only, JSON or plain batch-list bodies):

- `GET /ping` — handshake: `ok`, `version`, `out_dir`.
- `POST /collect` — body `{"urls": [...], "page_url": ...}` (or `url` +
  `name`, or a plain-text batch list with `url -> name` lines); queues and
  returns `{ok, added, pending}`; duplicates of pending entries are
  skipped.
- `POST /download` — same body; queues, then starts the batch immediately
  (`out_dir` per request overrides the config); the response carries the
  same summary dict `idm batch --json` prints (`ok`, `all_ok`,
  `total_bytes`, `downloads[]`).
- `GET /queue` — the queue payload: `pending[]`, `history[]`,
  `pending_count`.
- `POST /queue/clear` — empties the pending list.

```
curl -s http://127.0.0.1:27492/queue
```

Bad bodies answer `400` with `{"ok": false, "error": ...}` (never a
crash); unknown paths answer `404`. Malformed queue files read as empty —
captures are best-effort like every other store.

---

## `idm verify --json`

Re-runs the magic-byte kind check (the same one a finished download gets —
see [Magic-byte validation](../README.md#when-a-download-succeeds-but-the-file-wont-open))
over the files already in the downloads folder. Warn-only by default:
nothing is ever moved or deleted unless you pass `--delete-warned`.

```
idm verify -o C:\Users\you\downloads --json --delete-warned
```

Exit `0` when nothing is left warned — here `--delete-warned` moved the
mismatched file into quarantine:

```json
{
  "dir": "C:\\Users\\you\\downloads",
  "scanned": 3,
  "ok": 1,
  "warned": 0,
  "unreadable": 0,
  "skipped": 1,
  "ignored": 0,
  "moved": 1,
  "quarantine": "C:\\Users\\you\\downloads\\quarantine",
  "files": [
    {
      "file": "movie.mp4",
      "size": 262144,
      "kind": "ok",
      "note": ""
    },
    {
      "file": "notes.bin",
      "size": 512,
      "kind": "skipped",
      "note": ""
    },
    {
      "file": "poster.webm",
      "size": 1024,
      "kind": "moved",
      "note": "file saved, but the URL served an M3U playlist, not the Matroska/WebM content its name promises — it may not open or play correctly",
      "quarantine": "C:\\Users\\you\\downloads\\quarantine\\poster.webm"
    }
  ]
}
```

Fields:

- `dir` — the scanned folder (global `-o/--out` or config `out_dir`).
- `scanned` — files considered; `files[]` has one object per file, sorted
  by name.
- `ok` / `warned` / `unreadable` / `skipped` / `ignored` / `moved` — counts.
  `warned` means the content contradicts the filename; `skipped` means the
  extension is one the table has no opinion on (`.bin`, `.part`, …);
  `unreadable` is 0 in practice (a file that vanishes mid-scan is silently
  skipped too); `ignored` counts files on the `verify_ignore` config list
  (see `idm ignore` below — they are reported but never warned about nor
  swept); `moved` counts files `--delete-warned` relocated (0 without the
  flag).
- `quarantine` — where `--delete-warned` puts mismatches:
  `<downloads>/quarantine`. The folder is only created when there is
  something to move.
- `files[].note` — the same warning text the download-time check and the
  GUI Note column show; `""` unless `kind` is `warned` or `moved`.
- `files[].quarantine` — the file's new path, present only when `kind` is
  `moved` (a name collision in quarantine gets a ` (2)` suffix).
- `idm.state.json` is always excluded, and subdirectories (including
  `quarantine` itself) are not descended into — a re-run never re-scans
  what it already moved.

**`--delete-warned`** moves every kind-mismatched file into `quarantine`
and reports `[quarantine] <name> -> <path>` per move; files that cannot be
moved (locked by another program) stay put and keep failing the run with
exit `1`. Without the flag nothing is ever touched.

Exit codes: `0` clean (and `0` for a missing or empty folder, printing the
zero payload in JSON mode) · `1` any file warned and left in place — so a
`--delete-warned` sweep ends `0` once nothing is left warned. With a bad
`--query`: `1` and nothing on stdout.

`idm verify --query '[.files[] | select(.kind == "warned")] | .file' -r`
streams just the suspect names — pair it with `--watch`-style loops or a
scheduled scan the same way as `idm resume --json`.

---

## `idm restore --json`

Undoes a `verify --delete-warned` sweep: every file in
`<downloads>/quarantine` moves back into the downloads folder. All of them
by default, or just the named ones (`idm restore 3007.webm`); `--discard`
deletes the quarantined files for good instead. **Never overwrites**: a
colliding name in the downloads folder gets a ` (2)` suffix, and an
emptied quarantine folder is removed.

```
idm -o C:\Users\you\downloads restore --json
```

Exit `0` when every file was handled:

```json
{
  "dir": "C:\\Users\\you\\downloads",
  "quarantine": "C:\\Users\\you\\downloads\\quarantine",
  "restored": 1,
  "discarded": 0,
  "failed": 0,
  "files": [
    {
      "file": "poster.webm",
      "action": "restored",
      "dest": "C:\\Users\\you\\downloads\\poster.webm"
    }
  ]
}
```

Fields:

- `dir` / `quarantine` — the downloads folder and the quarantine it
  contains.
- `restored` / `discarded` / `failed` — counts matching the per-file
  `action` values.
- `files[]` — one object per file: `restored` rows carry `dest` (the
  absolute path after any ` (2)` suffix); `failed` rows carry an `error`
  ("not in the quarantine folder", a locked file, …); `discarded` rows
  carry nothing extra.
- With no names given, every file in quarantine is acted on; named files
  that are not in quarantine are reported as `failed` and exit `1`.
- `--discard` swaps `restored` for `discarded` (and drops `dest`), for
  scripted "quarantine, wait a week, then empty" flows.

Exit codes: `0` every file restored/discarded (also `0` with nothing to
do — a missing quarantine folder or an empty one) · `1` any failure.
With a bad `--query`: `1` and nothing on stdout.

`idm restore --query '.restored' -r` answers "how many came back" in one
token; `idm restore --discard --json` is the scripted empty-the-quarantine
step.

---

## `idm sanitize-names --json`

Lists (dry run by default) downloads files whose names a mangled
Content-Disposition produced — the same `_unmangle_cd_name` rule
`_pick_filename` applies to new downloads, applied to files already on
disk. With `--apply` the renames happen in place; nothing is ever
overwritten (a colliding name gets a ` (2)` suffix).

```
idm -o C:\Users\you\downloads sanitize-names --json --apply
```

Exit `0` after every rename succeeded:

```json
{
  "dir": "C:\\Users\\you\\downloads",
  "scanned": 2,
  "unclean": 1,
  "renamed": 1,
  "failed": 0,
  "files": [
    {
      "file": "130425,_360p.mp4,.mp4,_720p.mp4,",
      "to": "130425,_360p.mp4",
      "action": "renamed"
    }
  ]
}
```

Fields:

- `scanned` — top-level files considered (`idm.state.json` excluded,
  subdirectories not descended into); `unclean` is `len(files)`.
- `files[]` — one object per mangled name: the plan is `file` → `to`.
- `action` — `"would-rename"` (dry run), `"renamed"`, or `"failed"` (a
  locked file keeps its name and carries an `error`).
- `renamed` / `failed` — counts; both 0 on a dry run.
- A clean folder reports `"unclean": 0, "files": []`.

Exit codes: `0` nothing to do or (with `--apply`) every rename succeeded ·
`1` a dry run that found work, or any failed rename. With a bad
`--query`: `1` and nothing on stdout.

`idm sanitize-names --query '.unclean' -r` answers "how many names need
fixing" in one token; run it in a dry run first, then with `--apply`.

---

## `idm prune-state --json`

The downloads state file (`idm.state.json`) keeps one record per URL — that
is what `idm resume` retries and `idm stats` counts. When a file is deleted
by hand (or moved elsewhere), its record becomes a ghost: `resume` retries a
download that already "finished" somewhere else. `idm prune-state` finds
records whose file no longer exists and removes them — **dry-run preview by
default**, `--apply` removes them for real. Files are never touched, and a
record whose file reappeared between preview and apply is kept (records are
re-checked at apply time).

```
idm prune-state                       # preview: which records are stale
idm prune-state --apply               # remove them from idm.state.json
idm prune-state --apply --json        # machine-readable, for scripts
idm prune-state --vacuum              # also rewrite the store compacted
```

With **`--vacuum`** the store is rewritten without indentation while it is
open anyway — the state file grows forever otherwise, since `idm.state.json`
uses indented JSON for hand-editing. The reclaim comes from compaction
alone, so it works even with zero stale records; byte counts report the
file size before and after (`bytes_reclaimed` clamps at 0 — the fresh
`updated` timestamp jitters the size by a byte or two on a re-vacuum).
A missing state file is never created: the run stays a no-op and the byte
fields come back `null`.

Applying after two records existed but only one file survived:

```json
{
  "action": "apply",
  "dir": "C:\\Videos",
  "scanned": 2,
  "stale": 1,
  "live": 1,
  "records": [
    {
      "url": "https://cdn.example.com/big.iso",
      "filename": "big.iso",
      "status": "error",
      "size": 1024,
      "updated": 1790734567.009,
      "removed": true
    }
  ],
  "compacted": true,
  "state_bytes_before": 486,
  "state_bytes_after": 331,
  "bytes_reclaimed": 155
}
```

- `action` — `scan` (dry run) or `apply`.
- `dir` — the downloads folder whose `idm.state.json` was read.
- `scanned` / `stale` / `live` — total records, records whose file is
  missing, records kept. A record without a usable filename is never stale
  (nothing to check).
- `records` — the stale rows only: the URL, the `filename` that is gone,
  its last known `status`/`size`/`updated`, and whether `removed` actually
  happened (on `--apply` a row whose file is back on disk again shows
  `"removed": false` — the record was kept).
- `compacted` — whether the store was rewritten without indentation
  (`--vacuum`). The three byte fields are `null` without it (and for a
  missing store): `state_bytes_before`, `state_bytes_after`, and
  `bytes_reclaimed`.
- A record with partial-download evidence on disk (`filename.part` or
  `filename.part0`) is **never** stale — that download can still resume;
  the same rule keeps `idm resume` from skipping it as a ghost.

Exit codes are always `0`: the prune cannot fail, because it never touches
a file. `idm prune-state --apply --query '.stale'` answers "how many ghosts
were removed" in one token.


## `idm ignore --json`

The escape hatch for the verify scan: filenames you have inspected and
decided are fine (HLS playlists deliberately kept under a media name, for
example) never warn again — and `--delete-warned` never sweeps them.
Entries live in `~/.idm/config.json` under the `verify_ignore` key and are
shared with the GUI's right-click ignore actions. Text mode is unchanged;
`--json` prints a machine-readable payload for every action — `list`
(default), `add`, `remove`:

```
idm ignore                                   # list entries (default action)
idm ignore add kept.webm '*.m3u' '130425*'   # add one or more
idm ignore remove kept.webm                  # remove (exact names)
idm ignore add kept.webm '*.m3u' --json      # any action, machine-readable
```

Adding two entries when the list already holds `130425*`:

```json
{
  "action": "add",
  "added": [
    "kept.webm",
    "*.m3u"
  ],
  "duplicates": [],
  "entries": [
    "130425*",
    "kept.webm",
    "*.m3u"
  ],
  "count": 3,
  "config": "C:\\Users\\me\\.idm\\config.json"
}
```

- `action` — which one ran: `list`, `add`, or `remove`.
- `added` / `duplicates` (add only) — the requested entries split into the
  ones that were new and the ones already on the list, in the order given.
- `removed` / `unknown` (remove only) — the entries actually deleted, and
  the requested names that were not on the list.
- `entries` + `count` — the full resulting list, normalized (stripped and
  deduplicated), in stored order.
- `config` — the file the list is persisted to (`~/.idm/config.json`).

Exit codes mirror text mode: `0` for list and add, and for a remove that
removed something; `1` for a remove that removed nothing (usage errors —
`add`/`remove` without names — print help text, never JSON). With a bad
`--query`: `1` and nothing on stdout.

`idm ignore --json --query '.count'` answers "how many entries are on the
list"; `idm ignore list --query '.entries'` dumps the patterns for a
script to iterate.

Text-mode `add` also sanity-checks the entries against the downloads
folder (`-o` overrides the directory; `--json` never prints advice). An
entry that matches no file on disk right now gets a yellow warning —
verify never warned about anything by that name, so it is usually a
typo — and names its own undo (`idm ignore remove "NAME"`). When several
warned files share the added entry's extension, a hint suggests the one
pattern that would cover them all (`*.ext`); it stays quiet when the
pattern would only repeat the names just added.

Matching, per entry (case-insensitive):

- a plain name (`kept.webm`) matches exactly that filename;
- `*.ext` matches by extension — `*.m3u` covers every `.m3u` file;
- `prefix*` matches by prefix — `130425*` covers every name starting
  `130425`.

The scan reports ignored files as rows with `"kind": "ignored"` and an
empty `note`, counted under the payload's `ignored` key (see `idm verify
--json` above). In text mode the same rules apply — a remove that misses
every name prints a `[warn] not on the ignore list:` line per unknown
entry and exits `1`.

Reading the list through the config also works — `idm config get
verify_ignore` prints the effective entries as a JSON array (or pipe
`~/.idm/config.json` through `jq -r '.verify_ignore[]'` where jq exists) —
but `idm ignore list --json` is the canonical read. Entries can also be
added programmatically by editing the same key (a JSON array of strings);
the scan normalizes and deduplicates whatever it finds, so a malformed
value degrades to "ignore nothing", never an error.


## The `--query` filter

Every JSON command above accepts `--query Q`, a jq-style filter evaluated
against the payload — so scripts can extract one value without jq installed.
`--query` implies `--json` (quiet mode for `get`/`batch` too) and prints a
**single-line** result. Pair it with `-r/--raw` to strip quotes:

```
idm batch urls.txt --query '.downloads[].filename' -r
music.zip
report-2026-09-25.pdf

idm stats --query '.downloads.total_bytes'
3221225472

idm downloads --query 'length'          # how many unfinished downloads
1

idm providers --query '.all_ok'         # unquoted true/false
true
```

What the query language supports (and only this — deliberately):

- **Paths**: `.downloads[0].filename`, negative indices `[-1]`, iteration
  `.downloads[].status` and bare `[].status`, plus optional iteration
  `.downloads[]?` / `.missing[]?` — same thing, but a non-iterable yields an
  empty result instead of an error (jq's error-suppression suffix). Missing
  fields evaluate to `null` instead of failing; iterating an empty list
  yields an empty result.
- **Pipes**: `.downloads | length`, `.downloads | first | .status`.
- **Filtering with `select(COND)`**: keeps only elements whose condition is
  truthy. After `.downloads[]` (or `[]` on an array command) it filters
  element-wise, like jq's streams — chain `.field`, `length`, `add`, ... after
  it. Applied to a plain value it tests the value as-is (a falsy result
  becomes `null`, mirroring jq's empty output):
  `.downloads[] | select(.status == "error") | .filename`.
- **Transforming with `map(f)`**: applies `f` to every element —
  `.downloads | map(.filename)`, `.downloads | map(.total_bytes) | add`.
  (Over a stream it transforms each element; jq would flatten nested
  streams — an accepted divergence.)
- **Testing with `has(key)` / `startswith(prefix)` / `endswith(suffix)` /
  `contains(sub)`**: key/index membership and string predicates (all three
  string forms need strings on both sides) — `.downloads[1] | has("message")`,
  `.downloads[0].url | startswith("https://")`,
  `.downloads[0].filename | endswith(".zip")`,
  `.downloads[1].message | contains("500")`, or combined:
  `.downloads[] | select(.url | startswith("https://")) | .filename`.
- **Builtins**: `length`, `keys`, `add` (sum), `min`, `max`, `first`, `last`.
- **Literals & operators**: strings/numbers/`true`/`false`/`null`,
  `+`/`-`, comparisons `==  !=  <  <=  >  >=`, boolean `and`/`or`/`not`.
- **Output with `-r`**: strings print bare, booleans/null as
  `true`/`false`/`null`, numbers plain; **lists stream one value per line**
  (so `[].field` extraction reads like a jq stream). Nested lists/objects
  print as compact JSON. Without `-r` the result prints as pretty JSON.
- **In the GUI too**: both export dialogs (history Save As… and the
  downloads tab's Save As…) accept the same optional Query, and JSON is a
  first-class export format — the dialogs use the exact evaluator the CLI
  uses, so interactive and scripted exports can never drift.
- **Run Query… windows**: each tab's **Run Query…** button opens a
  live-validated query entry, then a sortable results window — object lists
  become rows (union of keys as columns), scalars/lists a single `value`
  column; click a column heading to sort/reverse. **Copy JSON** puts the
  exact `--json` payload on the clipboard (paste it into `jq`, a file, or a
  ticket); **Copy TSV** a header+rows grid for spreadsheets. The result is
  identical to running the same query through the CLI, so the window doubles
  as a what-you-see preview before scripting an export.
- **Remembered dialog settings**: the export dialogs prefill from your last
  OK'd values — the downloads Save As… remembers format + query, the history
  Save As… remembers provider/since/until/query, and each tab's Run Query…
  remembers its last query. They persist to `~/.idm/export_prefs.json` (one
  JSON doc, best-effort: a broken or unwritable file silently falls back to
  fresh defaults), so repetitive exports don't re-type the same query every
  GUI restart. Each tab's Run Query… also keeps the **last 8 queries**
  (newest first, deduplicated) in a dropdown — pick one to re-run it; the
  most recent prefills the field, and one clickable **chip per recent
  query** sits under it (long queries truncated with …; clicking fills and
  validates, it never runs by itself — a tooltip holds the full query).
  **Middle-clicking a chip drops that query from the tab's recent list**
  (if it was the field's prefill, the field follows the list).
  Preset chips tooltip every setting they would apply, including the full
  query text and pinned out/viewer. Query-results windows remember their
  **size and column widths per tab** and reopen the same way; the downloads
  tab's two tables (batch list and subtitle history) remember their **column
  widths** the same way (both saved on close into the same file). The menu
  bar's **Tools ▸ Run report preset** entries run the stats/providers
  presets in-process (see `idm presets --json` above); presets added while
  the GUI is open appear after **Tools ▸ Rebuild this menu after adding
  presets**. **Help ▸
  "Clear remembered settings…"** deletes the file (with a confirmation
  dialog) and every dialog starts from defaults again.

A syntax or type error prints `invalid --query: ...` and exits `1` — nothing
partial goes to stdout, so `set -e` pipelines stay safe.

Command-specific starters:

```
idm get URL --query '.downloads[] | .filename' -r     # names as they finish
idm batch list.txt --query '.ok == .count'            # "true" = all succeeded
idm batch list.txt --query '.downloads[] | select(.status == "error") | .url' -r
idm downloads --query '[].url' -r                     # URLs to retry
idm downloads --query '[.[] | select(.status == "error")] | length'
idm history --query '[].provider' -r                  # which providers were used
idm history --query '[.[] | select(.size > 100000)] | length'  # big subs only
idm stats --query '.downloads.by_status'              # status -> count map
idm stats --query '.downloads.by_status.error'        # error count (null = none)
idm providers --query '.providers[].name' -r          # provider names
idm downloads --preset errors                         # apply a saved export preset
idm providers --query '.providers[] | select(.status == "down") | .endpoint' -r
idm downloads --query 'map(.filename)'                # names as a JSON array
idm history --query '[.[] | select(.size > 100000)] | length'
idm get URL --query '.downloads[] | select(.message | startswith("network")) | .url' -r
```

---

## Automation recipes

Need the full picture — create a preset on the CLI, click it in the GUI,
verify the exported file, then move it to another machine — in one
narrative? See **A preset round trip** at the end of the presets section
above.

Watchdog that alerts on failed downloads (any shell):

```bash
idm batch nightly.txt --json > result.json || true
if ! grep -q '"all_ok": true' result.json; then
  # exactly which URLs failed, and why — no jq, no JSON parsing:
  idm batch nightly.txt --query '.downloads[] | select(.status == "error") | .url' -r >&2
  idm batch nightly.txt --query '.downloads[] | select(.status == "error") | .message' -r >&2
  exit 1
fi
```

PowerShell: log every finished filename with its size:

```powershell
idm batch urls.txt --json | ConvertFrom-Json |
  ForEach-Object { $_.downloads } |
  Where-Object status -eq 'done' |
  ForEach-Object { "{0} = {1} bytes" -f $_.filename, $_.total_bytes }
```

Re-run everything unfinished — `resume` itself is now scriptable:

```bash
idm -o D:\dl resume --json > result.json || true
grep -q '"all_ok": true' result.json || echo "some retries still failing"
```

Or retry only URLs that matter, straight from the state file:

```bash
idm downloads --query '[].url' -r | while read -r url; do
  idm get "$url" --json > /dev/null || echo "still failing: $url"
done
```

Feed a monitoring dashboard with one number per tick:

```bash
while true; do
  printf '%s %s\n' "$(date +%s)" "$(idm -o D:\dl stats --query '.downloads.pending')"
  sleep 60
done
```

CI gate on provider health before a subtitle job:

```bash
idm providers --query '.all_ok' | grep -q true || { echo "providers down"; exit 1; }
```

## Notes for script authors

- **Which launcher**: from cmd/PowerShell use `pyidm.bat` (quote the query
  with double quotes: `--query ".all_ok"`). From Git Bash/WSL use the bundle's
  `pyidm.sh` — it execs python directly, so queries containing `|`, `"`, or
  `$` survive byte-exact; a `.bat` crossed through bash gets re-parsed by
  cmd.exe and mangles exactly those characters.
- **Smoke-testing an unpacked bundle**: run `./smoke_test.sh` from the bundle
  root. It exercises both launchers offline (a seeded temp state file) —
  including a `--query` with pipes and embedded quotes through `pyidm.sh` —
  and exits non-zero on any packaging regression.

- **Machine with `jq` available?** Pipe the plain `--json` output to it:
  `idm batch urls.txt --json | jq '.total_bytes'`. `--query` exists so you
  don't *need* jq — the payloads are ordinary JSON either way.
- **`--out` exports vs `--json`**: `idm history --out file.json` writes the
  same document to a file (plus a `preview:` line on stdout); `--json`/`--query`
  write to stdout only. Don't mix them in one invocation.
- **Global `-o` placement**: the output-directory flag goes *before* the
  subcommand for `downloads`/`stats` (`idm -o DIR stats --json`). For
  `get`/`batch` it also works after, but "global first" is the habit that
  always parses.
- **Timestamps** (`updated`, `ts`) are Unix seconds; `age_seconds` is computed
  against the current clock at read time.
- Version-sensitive? `idm --version` is stable output; the JSON payloads on
  this page correspond to PyIDM 1.11.20.
