"""Guard doc claims that drift from packaging metadata, and tag-pinned URLs.

Two guards in the same spirit as test_doc_embed_zip_version.py:

1. README's Python floor claims (the shields.io badge and ``Python X.Y+``
   text) must match ``requires-python`` in pyproject.toml. When the floor
   moves, packaging metadata is where the change happens — the docs must
   follow in the same PR instead of silently lagging behind.
2. No doc may pin a concrete tag in a ``releases/download/v...``,
   ``/tree/v...`` or ``/blob/v...`` URL: those freeze install/read
   instructions to an old version. Link ``releases/latest`` / ``tree/main``
   instead; the parameterized ``/releases/tags/v<version>`` API form the
   docs already teach is fine (placeholder, and tags are immutable).
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC_DIRS = (REPO_ROOT, REPO_ROOT / "docs")

REQUIRES_PYTHON = re.compile(r'^requires-python\s*=\s*["\']([^"\']+)["\']', re.M)
FLOOR_NUMBER = re.compile(r"[0-9]+\.[0-9]+")
BADGE_FLOOR = re.compile(r"badge/python-([0-9]+\.[0-9]+)%2B")
TEXT_FLOOR = re.compile(r"Python ([0-9]+\.[0-9]+)\+")
TAG_PINNED_URL = re.compile(
    r"(?:releases/download/|/tree/|/blob/)v[0-9][^)\s\"'>]*")


def _doc_files() -> list[Path]:
    # Committed docs, enumerated explicitly (same rationale as the
    # embed-zip guard: gitignored build copies must not skew results).
    files: list[Path] = []
    for d in DOC_DIRS:
        files.extend(sorted(d.glob("*.md")))
    return files


def _where(path: Path) -> Path:
    try:
        return path.relative_to(REPO_ROOT)
    except ValueError:                    # synthetic paths (unit tests)
        return path


def _requires_python_floor() -> str:
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = REQUIRES_PYTHON.search(pyproject)
    assert m, "requires-python not found in pyproject.toml"
    floor = FLOOR_NUMBER.search(m.group(1))
    assert floor, f"cannot parse a floor from requires-python {m.group(1)!r}"
    return floor.group(0)


def _floor_claim_sites(paths: list[Path], floor: str) -> list[str]:
    """``file:line`` sites whose Python floor claim != ``floor``."""
    sites: list[str] = []
    for path in paths:
        where = _where(path)
        for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1):
            for pat in (BADGE_FLOOR, TEXT_FLOOR):
                for m in pat.finditer(line):
                    if m.group(1) != floor:
                        sites.append(
                            f"{where}:{lineno}: {m.group(0)} "
                            f"(pyproject floor is {floor})")
    return sites


def _tag_pinned_sites(paths: list[Path]) -> list[str]:
    """``file:line`` sites pinning a concrete tag in a doc URL."""
    sites: list[str] = []
    for path in paths:
        where = _where(path)
        for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1):
            for m in TAG_PINNED_URL.finditer(line):
                sites.append(f"{where}:{lineno}: {m.group(0)}")
    return sites


def test_requires_python_floor_parses() -> None:
    # The guard's own premise: pyproject still declares a parseable floor.
    assert re.fullmatch(r"[0-9]+\.[0-9]+", _requires_python_floor())


def test_readme_python_floor_matches_requires_python() -> None:
    floor = _requires_python_floor()
    sites = _floor_claim_sites(_doc_files(), floor)
    assert not sites, (
        "a doc's Python floor claim disagrees with requires-python in "
        f"pyproject.toml ({floor}); move them in the same PR. "
        f"Offending claims: {'; '.join(sites)}"
    )


def test_no_tag_pinned_urls_in_docs() -> None:
    sites = _tag_pinned_sites(_doc_files())
    assert not sites, (
        "a doc pins a concrete tag in a download/tree/blob URL — stale for "
        "anyone expecting current instructions. Link releases/latest or "
        "tree/main instead (the /releases/tags/v<version> API form is "
        f"fine). Offending links: {'; '.join(sites)}"
    )


def test_floor_scanner_flags_only_stale_claims(tmp_path: Path) -> None:
    floor = _requires_python_floor()
    other = "2.7" if floor != "2.7" else "1.5"
    hit = tmp_path / "hit.md"
    ok = tmp_path / "ok.md"
    hit.write_text(
        f"[![Python {other}+](https://img.shields.io/badge/python-{other}%2B"
        "-blue.svg)](x)\n"
        f"Python {other}+ required.\n"
        f"Python {floor}+ is fine.\n",
        encoding="utf-8")
    ok.write_text("no floor claims here\n", encoding="utf-8")
    sites = _floor_claim_sites([hit, ok], floor)
    assert len(sites) == 3, sites        # badge URL + badge alt + text claim
    assert all(str(hit) in s and other in s for s in sites)
    assert all("pyproject floor is" in s for s in sites)


def test_url_scanner_flags_only_concrete_tags(tmp_path: Path) -> None:
    hit = tmp_path / "hit.md"
    ok = tmp_path / "ok.md"
    hit.write_text(
        "bad dl: https://github.com/i12playwow/pyidm/releases/download/"
        "v1.11.79/PyIDM-portable.zip\n"
        "bad tree: https://github.com/i12playwow/pyidm/tree/v1.11.79/docs\n"
        "bad blob: https://github.com/i12playwow/pyidm/blob/v1.11.79/x.py\n"
        "good: https://github.com/i12playwow/pyidm/releases/latest\n"
        "good: https://github.com/i12playwow/pyidm/tree/main\n"
        "good: /releases/tags/v<version>\n",
        encoding="utf-8")
    ok.write_text("no links at all\n", encoding="utf-8")
    sites = _tag_pinned_sites([hit, ok])
    assert len(sites) == 3, sites
    assert all(str(hit) in s and "v1.11.79" in s for s in sites)
    assert all("releases/download" in s or "/tree/" in s or "/blob/" in s
               for s in sites)
