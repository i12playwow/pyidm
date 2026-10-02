# Reproducible builds

Every PyIDM release attaches exactly one artifact — `PyIDM-portable.zip` —
and the release pipeline commits those exact bytes back into this repo.
This document explains the guarantee, how to verify any downloaded bundle
by sha256 in under a minute, and how to rebuild a bundle from a tag and
compare it yourself.

## The guarantee

For a given release, three things always agree:

1. **The release asset** — `PyIDM-portable.zip` attached to the GitHub
   release (what users download).
2. **The repo copy** — `portable/PyIDM-portable.zip` on `main`.
3. **The checksum sidecar** — `portable/PyIDM-portable.zip.sha256`, format
   `<sha256>  PyIDM-portable.zip`.

(2) and (3) are written by the `sync-repo-zip` job after a release
publishes: it copies the uploaded artifact byte-for-byte to a
`sync/portable-zip-v<version>` branch and lands it on `main` through an
auto-merged PR. No human retypes a hash anywhere.

Byte-level reproducibility comes from [`portable_zip.py`](portable_zip.py),
which pins everything the ZIP format records:

- entries in **sorted order** (filesystem walk order is not stable),
- every timestamp at the **ZIP epoch**, 1980-01-01 00:00:00,
- `create_system` and permissions fixed (unix, 0644).

The same input tree zipped by the same CPython build therefore produces
identical bytes; the residual variables are listed under
[When the hash doesn't match](#when-the-hash-doesnt-match).

## Verify a downloaded bundle

### 1. Hash the file

Git Bash / WSL / Linux:

```bash
sha256sum PyIDM-portable.zip
```

PowerShell:

```powershell
Get-FileHash .\PyIDM-portable.zip -Algorithm SHA256
```

cmd:

```bat
certutil -hashfile PyIDM-portable.zip SHA256
```

### 2. Compare against the release's server-side digest

GitHub computes the sha256 of each asset itself, so this comparison relies
on nothing committed by this project:

```bash
gh api repos/i12playwow/pyidm/releases/tags/v1.11.78 --jq '.assets[0].digest'
# sha256:31456bc64df517d5d000f66a864594934f9f94be4ffab48be85c0bdd5a978836
```

Without `gh`, curl the API:

```bash
curl -s https://api.github.com/repos/i12playwow/pyidm/releases/tags/v1.11.78 \
  | python -c "import json,sys; a=json.load(sys.stdin)['assets'][0]; print(a['name'], a['digest'])"
# PyIDM-portable.zip sha256:31456bc64df517d5d000f66a864594934f9f94be4ffab48be85c0bdd5a978836
```

### 3. Compare against the repo copy

`main` always carries the newest release's zip and sidecar:

```bash
git clone https://github.com/i12playwow/pyidm
cd pyidm
cat portable/PyIDM-portable.zip.sha256
# 31456bc64df517d5d000f66a864594934f9f94be4ffab48be85c0bdd5a978836  PyIDM-portable.zip
```

For an older release, find its sync commit and read the sidecar there:

```bash
git log --oneline -- portable/PyIDM-portable.zip
# 5cecf7b Merge pull request #20 from i12playwow/sync/portable-zip-v1.11.78
git show 5cecf7b:portable/PyIDM-portable.zip.sha256
```

A match against any of the three proves the file is exactly what CI built,
smoke-tested, and published. It is an integrity check, not a security
audit — for that, read the source and the build script.

## Rebuild from source and compare

### Prerequisites

- Windows x64, Git, PowerShell (the build script uses it).
- A full CPython **3.12** install from python.org — same patch line as the
  release build, and **with Tk** (the standard installer includes it).
  `build_portable.bat` copies `_tkinter.pyd`, `tcl86t.dll`, `tk86t.dll`,
  `zlib1.dll`, `tcl\`, and `Lib\tkinter\` from it into the bundle.
- Network access: the script downloads the embeddable runtime
  (`python-3.12.10-embed-amd64.zip`, pinned via `VER` in the script; cached
  as `portable/python-embed.zip`) and pip-installs the exact dependency set
  pinned in [`portable/requirements.txt`](portable/requirements.txt).

### Build

cmd:

```bat
git clone https://github.com/i12playwow/pyidm
cd pyidm
git checkout v1.11.78
set "PY312=C:\Program Files\Python312"
build_portable.bat
```

Git Bash:

```bash
git clone https://github.com/i12playwow/pyidm
cd pyidm
git checkout v1.11.78
PY312="C:/Program Files/Python312" cmd //c '.\build_portable.bat'
```

Smoke-test the result the way CI does (Git Bash):

```bash
cd portable/PyIDM && sh smoke_test.sh
```

### Compare

```bash
sha256sum portable/PyIDM-portable.zip
```

For v1.11.78 the expected value is:

```
31456bc64df517d5d000f66a864594934f9f94be4ffab48be85c0bdd5a978836
```

A match means your machine reproduced the release byte-for-byte — the
published zip was built from this exact source with these exact tools.
(For releases built before the pin file — v1.11.78 and earlier — expect
only content-equality; see the caveats below.)

## When the hash doesn't match

Two inputs beyond the source tree can legitimately change the bytes:

1. **Your CPython 3.12 build.** `portable_zip.py` runs under your local
   `PY312` interpreter, and its zlib is what compresses the deflate
   streams; it also supplies the copied `zlib1.dll` / Tk DLLs. Same CPython
   patch release as the CI build → identical bytes (the release log shows
   the exact Python: `pythonLocation`). The one wildcard was removed from
   the bundle outright: pip's console-script launchers embed the builder's
   interpreter path and vary run to run, so `build_portable.bat` drops
   `site\bin` and its `RECORD` entries — the bundle never used them.
2. **Dependency versions — pre-pin releases only.** The bundle installs
   from the pinned [`portable/requirements.txt`](portable/requirements.txt),
   so rebuilds get identical dependency versions and PyPI drift cannot
   change the bytes. Releases up to v1.11.78 predate that file and used an
   unpinned `pip install requests rich`; rebuilding one of those tags may
   resolve newer versions, and those pre-pin bundles also mirror the
   builder's `core.autocrlf` line endings. From the next release on,
   `build_portable.bat` LF-normalizes every copied source, so the
   checkout's line-ending settings stop mattering. The release bundle
   records exactly what it shipped — e.g.
   `PyIDM/site/requests-<version>.dist-info/`.

When only those differ, the **contents** still match. Compare trees:

```bash
unzip -q PyIDM-portable.zip -d /tmp/release
unzip -q portable/PyIDM-portable.zip -d /tmp/rebuild
diff -r /tmp/release/PyIDM /tmp/rebuild/PyIDM
```

Differences confined to `site/*.dist-info` (`REQUESTED` markers, and
`RECORD` lines for the `site/bin` launchers that pre-pin bundles shipped
but new builds drop) → build-machine and pre-pin artifacts, not real
drift; identical trees → pure compression drift. Anything unexpected
under `site/idm/` → stop and investigate.

To force a full match when rebuilding a pre-pin release, reinstall the
exact dependency versions recorded in that release's bundle into
`portable/PyIDM/site`, then repack:

```bat
rmdir /s /q portable\PyIDM\site
mkdir portable\PyIDM\site
"%PY312%\python.exe" -m pip install -q --target portable\PyIDM\site requests==<version> rich==<version>
xcopy /e /i /q /y idm portable\PyIDM\site\idm
"%PY312%\python.exe" portable_zip.py portable\PyIDM portable\PyIDM-portable.zip
```

## How this is enforced

| Piece | Role |
|-------|------|
| [`portable_zip.py`](portable_zip.py) | deterministic packing (sorted, epoch timestamps, fixed attributes) |
| [`build_portable.bat`](build_portable.bat) | builds the bundle, LF-normalizes copied sources, drops pip's machine-specific launchers, then packs with `portable_zip.py` |
| [`portable/requirements.txt`](portable/requirements.txt) | exact bundle dependency pins — no PyPI drift between rebuilds |
| [`.github/workflows/release.yml`](.github/workflows/release.yml) | tag gate → build → smoke test → zip-root assert → upload → publish, then `sync-repo-zip` commits the published bytes + regenerated sidecar to `main` via an auto-merged PR |
| [`.github/workflows/ci.yml`](.github/workflows/ci.yml) | vermin / ruff / mypy / pytest keep the tooling itself honest |

Changes to the build scripts that affect this guarantee should say so in
the PR description.
