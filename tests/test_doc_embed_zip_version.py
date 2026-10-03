"""Guard the docs against naming a stale embed-zip patch version.

``build_portable.bat`` pins the bundle's embeddable CPython via
``set "VER=..."``; REPRODUCIBLE-BUILDS.md documents that runtime as
``python-<VER>-embed-amd64.zip`` precisely so the docs never hardcode the
patch. A doc that names a concrete ``python-3.12.10-embed-amd64.zip`` will
silently go stale on the next VER bump — the exact failure mode the docs'
release-agnostic pass (PR #28, then #30) exists to prevent.

This test scans every doc file for versioned embed-zip filenames and fails
when one disagrees with the live VER pin. The parameterized form
(``python-<VER>-embed-amd64.zip``) is not a versioned filename and passes,
as does the unversioned cache name ``portable/python-embed.zip``. If a doc
ever needs to cite an older patch on purpose (history section), anchor it
to the tag it shipped in or parameterize it — this guard stays strict.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# The pin: set "VER=3.12.10" — first match wins, dotted patch required.
VER_LINE = re.compile(r'^set "VER=([0-9]+(?:\.[0-9]+)+)"', re.M)
# Versioned embed-zip filenames only; <VER> placeholders don't match this.
EMBED_ZIP = re.compile(r"python-([0-9]+(?:\.[0-9]+)+)-embed-amd64\.zip")

DOC_DIRS = (REPO_ROOT, REPO_ROOT / "docs")


def _pinned_ver() -> str:
    """The embed-zip patch version pinned in build_portable.bat."""
    bat = (REPO_ROOT / "build_portable.bat").read_text(encoding="utf-8")
    m = VER_LINE.search(bat)
    assert m, 'could not parse set "VER=..." from build_portable.bat'
    return m.group(1)


def _doc_files() -> list[Path]:
    """Committed docs, enumerated explicitly so gitignored build copies
    under portable/ can never make this test's result machine-dependent."""
    files: list[Path] = []
    for d in DOC_DIRS:
        files.extend(sorted(d.glob("*.md")))
    return files


def _embed_version_sites(paths: list[Path], ver: str) -> list[str]:
    """``file:line`` sites naming a versioned embed zip that != ``ver``."""
    sites: list[str] = []
    for path in paths:
        try:
            where: Path = path.relative_to(REPO_ROOT)
        except ValueError:            # synthetic paths (unit tests)
            where = path
        for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1):
            for m in EMBED_ZIP.finditer(line):
                if m.group(1) != ver:
                    sites.append(
                        f"{where}:{lineno}: "
                        f"{m.group(0)} (VER pins {ver})")
    return sites


def test_pinned_ver_parses_from_build_script() -> None:
    # The guard's own premise: build_portable.bat still carries a parseable
    # VER pin (fails loudly if the variable is renamed or undotted).
    ver = _pinned_ver()
    assert re.fullmatch(r"[0-9]+(?:\.[0-9]+)+", ver), ver


def test_docs_embed_zip_mentions_match_ver_pin() -> None:
    ver = _pinned_ver()
    sites = _embed_version_sites(_doc_files(), ver)
    assert not sites, (
        "a doc names a concrete embed-zip version that disagrees with the "
        f"VER pin ({ver}) in build_portable.bat, so it goes stale on the "
        "next VER bump. Use the parameterized form "
        "python-<VER>-embed-amd64.zip (see REPRODUCIBLE-BUILDS.md). "
        f"Offending mentions: {'; '.join(sites)}"
    )


def test_scanner_flags_only_mismatched_versions(tmp_path: Path) -> None:
    ver = _pinned_ver()
    other = "9.9.9" if ver != "9.9.9" else "8.8.8"
    hit = tmp_path / "hit.md"
    ok = tmp_path / "ok.md"
    hit.write_text(
        f"old: python-{other}-embed-amd64.zip\n"
        "current: python-<VER>-embed-amd64.zip\n"
        "cache: portable/python-embed.zip\n"
        f"right: python-{ver}-embed-amd64.zip\n",
        encoding="utf-8")
    ok.write_text("nothing versioned here\n", encoding="utf-8")
    sites = _embed_version_sites([hit, ok], ver)
    assert len(sites) == 1 and sites[0].startswith(f"{hit}:1: "), sites
    assert other in sites[0] and "<VER>" not in sites[0]
