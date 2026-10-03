"""Guard the supported-Python range claim against ci.yml's pytest matrix.

CONTRIBUTING.md's 3.9-floor note promises "supports Python 3.9-3.12".
Exactly one thing backs that promise: the pytest matrix in ci.yml, which
is where every supported version is actually exercised. When the two
disagree, the docs promise a version CI never tests — or disown one it
does — and the ceiling drifts just as silently as the floor (the floor
had a guard already: README's claims follow requires-python via
test_doc_floor_and_urls.py).

This guard pins the doc's range to the matrix bidirectionally (floor and
ceiling must both match) and, since the matrix floor is the installable
floor, also pins the matrix floor to ``requires-python`` in
pyproject.toml — closing the chain README <-> pyproject <-> ci.yml <->
CONTRIBUTING.

The claim pattern is tight ("Python 3.X<dash>3.Y", hyphen or en-dash, so
a reworded or removed claim fails the premise test instead of passing
silently). The matrix must be the flow-style list under ci.yml's single
``matrix:`` key; a restyled or moved matrix fails loudly too.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC_DIRS = (REPO_ROOT, REPO_ROOT / "docs")
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"
PYPROJECT = REPO_ROOT / "pyproject.toml"

# The pytest job's matrix: a flow-style list under the `matrix:` key.
# ci.yml carries exactly one such block (the lint jobs pin bare versions).
MATRIX = re.compile(
    r"^\s*matrix:\s*\n\s*python-version:\s*\[([^\]]+)\]", re.M)
DOTTED = re.compile(r"\d+(?:\.\d+)+")
# Range claim: "Python 3.9-3.12" with a hyphen or an en-dash (U+2013) as
# the dash; major pinned to 3 — tighten here before Python 4 matters.
RANGE_CLAIM = re.compile(r"Python\s+(3\.\d+)\s*[-\u2013]\s*(3\.\d+)")
REQUIRES_PYTHON = re.compile(
    r'^requires-python\s*=\s*["\']([^"\']+)["\']', re.M)
FLOOR_NUMBER = re.compile(r"[0-9]+\.[0-9]+")


def _where(path: Path) -> Path:
    try:
        return path.relative_to(REPO_ROOT)
    except ValueError:                    # synthetic paths (unit tests)
        return path


def _doc_files() -> list[Path]:
    # Committed docs, enumerated explicitly (same rationale as the other
    # doc guards: gitignored build copies must not skew results).
    files: list[Path] = []
    for d in DOC_DIRS:
        files.extend(sorted(d.glob("*.md")))
    return files


def _vkey(version: str) -> tuple[int, ...]:
    """Numeric sort key: 3.10 > 3.9, unlike lexicographic order."""
    return tuple(int(part) for part in version.split("."))


def _matrix_versions(path: Path = CI_YML) -> list[str]:
    """The pytest matrix's python-version list, as written in ci.yml."""
    found = MATRIX.findall(path.read_text(encoding="utf-8"))
    assert len(found) == 1, (
        f"{_where(path)}: expected exactly one flow-style 'matrix: "
        f"python-version: [...]' block, found {len(found)} — the matrix "
        "moved or changed shape; update this guard to follow it")
    versions = DOTTED.findall(found[0])
    assert len(versions) >= 2, (
        f"{_where(path)}: matrix {found[0]!r} lists fewer than two versions")
    return versions


def _range_claims(
    paths: list[Path],
) -> list[tuple[Path, int, str, str, str]]:
    """(path, lineno, floor, ceiling, matched text) per range claim."""
    claims: list[tuple[Path, int, str, str, str]] = []
    for path in paths:
        for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1):
            for m in RANGE_CLAIM.finditer(line):
                claims.append(
                    (path, lineno, m.group(1), m.group(2), m.group(0)))
    return claims


def _requires_floor(path: Path = PYPROJECT) -> str:
    """The floor from requires-python in pyproject.toml (e.g. '3.9')."""
    pyproject = path.read_text(encoding="utf-8")
    m = REQUIRES_PYTHON.search(pyproject)
    assert m, f"{_where(path)}: requires-python not found"
    floor = FLOOR_NUMBER.search(m.group(1))
    assert floor, (
        f"{_where(path)}: cannot parse a floor from requires-python "
        f"{m.group(1)!r}")
    return floor.group(0)


def _drift_claims(
    claims: list[tuple[Path, int, str, str, str]], lo: str, hi: str,
) -> list[str]:
    """Rendered, actionable sites for claims outside the matrix range."""
    return [
        f"{_where(path)}:{lineno}: {span!r} — ci.yml's pytest matrix "
        f"tests {lo}-{hi}"
        for path, lineno, floor, ceiling, span in claims
        if (floor, ceiling) != (lo, hi)
    ]


def _floor_mismatch(versions: list[str], floor: str) -> str | None:
    """Message when the matrix floor != requires-python floor, else None."""
    matrix_floor = min(versions, key=_vkey)
    if matrix_floor == floor:
        return None
    return (
        f"ci.yml's pytest matrix floor is {matrix_floor} but requires-python "
        f"in pyproject.toml is >= {floor} — the oldest version that can "
        "install the package must be the oldest one CI tests (README's "
        "floor claims follow requires-python via test_doc_floor_and_urls.py)"
    )


def test_matrix_claims_and_floor_parse() -> None:
    # The guard's own premise: the matrix is a parseable list of distinct
    # dotted versions, the docs still carry a range claim to check, and
    # requires-python still declares a floor.
    versions = _matrix_versions()
    assert len(set(versions)) == len(versions), versions
    assert _range_claims(_doc_files()), (
        "no 'Python X.Y-A.B' range claim found in any doc — the claim this "
        "guard checks was reworded or removed; either restore it or update "
        "RANGE_CLAIM to match the new wording")
    assert re.fullmatch(r"[0-9]+\.[0-9]+", _requires_floor())


def test_doc_range_claims_match_pytest_matrix() -> None:
    versions = _matrix_versions()
    drift = _drift_claims(
        _range_claims(_doc_files()),
        min(versions, key=_vkey), max(versions, key=_vkey))
    assert not drift, (
        "a doc's supported-Python range disagrees with the pytest matrix "
        "in ci.yml, so the docs promise a floor or ceiling CI never tests "
        "(or disown one it does). Move both in the same PR. "
        f"Offending claims: {'; '.join(drift)}"
    )


def test_matrix_floor_matches_requires_python_floor() -> None:
    mismatch = _floor_mismatch(_matrix_versions(), _requires_floor())
    assert not mismatch, mismatch


def test_matrix_parser_orders_numerically(tmp_path: Path) -> None:
    yml = tmp_path / "ci.yml"
    yml.write_text(
        "jobs:\n"
        "  test:\n"
        "    strategy:\n"
        "      matrix:\n"
        '        python-version: ["3.10", "3.9", "3.12"]\n',
        encoding="utf-8")
    versions = _matrix_versions(yml)
    assert versions == ["3.10", "3.9", "3.12"], versions
    lo, hi = min(versions, key=_vkey), max(versions, key=_vkey)
    # 3.10 sorts above 3.9 numerically; lexicographic order would say
    # "3.10" < "3.9" and derive a phantom 3.10 floor.
    assert (lo, hi) == ("3.9", "3.12"), (lo, hi)


def test_matrix_parser_fails_loud_without_flow_list(tmp_path: Path) -> None:
    yml = tmp_path / "ci.yml"
    yml.write_text(
        "    strategy:\n"
        "      matrix:\n"
        "        python-version:\n"
        '          - "3.9"\n'
        '          - "3.12"\n',
        encoding="utf-8")
    with pytest.raises(AssertionError, match="exactly one flow-style"):
        _matrix_versions(yml)


def test_range_scanner_flags_only_wrong_claims(tmp_path: Path) -> None:
    hit = tmp_path / "hit.md"
    ok = tmp_path / "ok.md"
    hit.write_text(
        "PyIDM supports Python 3.9\u20133.13.\n"     # ceiling drifts
        "Or the floor drifts: Python 3.10\u20133.12.\n"
        "Python 3.9\u20133.12 is correct.\n"
        "Python 3.9+ required.\n",                    # floor-only: no claim
        encoding="utf-8")
    ok.write_text("nothing to see\n", encoding="utf-8")
    claims = _range_claims([hit, ok])
    assert [(c[1], c[2], c[3]) for c in claims] == [
        (1, "3.9", "3.13"), (2, "3.10", "3.12"), (3, "3.9", "3.12")], claims
    versions = ["3.9", "3.10", "3.11", "3.12"]
    drift = _drift_claims(
        claims, min(versions, key=_vkey), max(versions, key=_vkey))
    assert len(drift) == 2, drift
    assert all(str(hit) in d for d in drift), drift
    assert "3.13" in drift[0] and "tests 3.9-3.12" in drift[0], drift
    assert "3.10\u20133.12" in drift[1], drift


def test_floor_cross_check_flags_matrix_drift() -> None:
    # Matching floors stay silent; a matrix that drops the requires-python
    # floor is flagged with both sides named.
    assert _floor_mismatch(["3.9", "3.10", "3.11", "3.12"], "3.9") is None
    mismatch = _floor_mismatch(["3.10", "3.11", "3.12"], "3.9")
    assert mismatch is not None and "3.10" in mismatch
    assert ">= 3.9" in mismatch, mismatch
