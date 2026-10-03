"""Guard the enforcement table in REPRODUCIBLE-BUILDS.md against drift.

The "How this is enforced" section names the exact ``.github/workflows``
files that make up the reproducibility chain. Two silent failure modes
when that list drifts from the repository:

1. A row links a workflow that no longer exists (renamed or deleted): the
   doc points readers at a 404 and overstates what actually enforces the
   guarantee.
2. A workflow exists but is missing from the table: either it was added
   without updating the doc, or it genuinely enforces nothing about
   reproducible builds — in which case it must be recorded in
   ``DELIBERATELY_UNLISTED`` with a reason, keeping the table's scope
   honest instead of quietly collecting every workflow in the repo.

This guard compares the two bidirectionally. Only mentions inside the
"How this is enforced" section count: other doc sections may legitimately
reference workflows without claiming membership in the chain.
``tag-check.yml`` is allowlisted — it is a tag/version gate, not part of
the reproducibility chain (it blocks mistagged releases but builds and
verifies nothing itself).
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC = REPO_ROOT / "REPRODUCIBLE-BUILDS.md"

HEADING = "## How this is enforced"
# Any .github/workflows/<file> mention in the section counts as a claim of
# membership (backticked, markdown-linked, or plain prose — all three have
# appeared in the table's cells).
WORKFLOW_REF = re.compile(
    r"\.github/workflows/([A-Za-z0-9][A-Za-z0-9._-]*\.ya?ml)")

# Workflows deliberately absent from the enforcement table, with the reason.
# The guard fails if one of these stops existing — the entry then goes stale.
DELIBERATELY_UNLISTED: dict[str, str] = {
    "tag-check.yml": "tag/version gate, not part of the reproducibility chain",
}


def _where(path: Path) -> Path:
    try:
        return path.relative_to(REPO_ROOT)
    except ValueError:                    # synthetic paths (unit tests)
        return path


def _enforcement_table(path: Path) -> dict[str, int]:
    """Workflow filename -> first 1-based doc line naming it, scoped to the
    "How this is enforced" section (heading to the next ``##`` heading)."""
    lines = path.read_text(encoding="utf-8").splitlines()
    start = next(
        (i for i, line in enumerate(lines) if line.strip() == HEADING), None)
    assert start is not None, (
        f"{_where(path)}: '{HEADING}' heading not found — the enforcement "
        "table this guard checks is gone or renamed")
    end = next(
        (i for i in range(start + 1, len(lines))
         if lines[i].startswith("## ")),
        len(lines),
    )
    table: dict[str, int] = {}
    for i in range(start, end):
        for m in WORKFLOW_REF.finditer(lines[i]):
            table.setdefault(m.group(1), i + 1)
    return table


def _actual_workflows() -> set[str]:
    """Filenames of every workflow under .github/workflows (yml and yaml)."""
    wf_dir = REPO_ROOT / ".github" / "workflows"
    return {p.name for p in wf_dir.iterdir()
            if p.suffix in (".yml", ".yaml")}


def _drift(table: dict[str, int], actual: set[str], doc: Path) -> list[str]:
    """Human-readable disagreements between the table and the inventory."""
    drift: list[str] = []
    for name, lineno in sorted(table.items(), key=lambda item: item[1]):
        if name not in actual:
            drift.append(
                f"{_where(doc)}:{lineno}: table row names "
                f".github/workflows/{name}, which does not exist")
    for name in sorted(actual - table.keys() - DELIBERATELY_UNLISTED.keys()):
        drift.append(
            f".github/workflows/{name} exists but is missing from the "
            "enforcement table — add a row, or (if it enforces nothing "
            "about reproducible builds) record the reason in "
            "DELIBERATELY_UNLISTED in tests/test_doc_workflow_table.py")
    for name in sorted(DELIBERATELY_UNLISTED.keys() - actual):
        drift.append(
            f"DELIBERATELY_UNLISTED names .github/workflows/{name}, which "
            "no longer exists — drop the stale allowlist entry")
    return drift


def test_enforcement_table_and_inventory_parse() -> None:
    # The guard's own premise: the doc still carries the enforcement section
    # with at least one workflow row, and the inventory directory exists.
    table = _enforcement_table(DOC)
    assert table, "no .github/workflows rows parsed from the table section"
    assert _actual_workflows(), "no workflow files found in .github/workflows"


def test_enforcement_table_matches_workflow_inventory() -> None:
    drift = _drift(_enforcement_table(DOC), _actual_workflows(), DOC)
    assert not drift, (
        "REPRODUCIBLE-BUILDS.md's 'How this is enforced' table disagrees "
        "with the .github/workflows inventory, so the doc's enforcement "
        f"claim is stale. {'; '.join(drift)}. Fix the table (or the "
        "allowlist) in the same PR."
    )


def test_table_scanner_scopes_to_enforcement_section(tmp_path: Path) -> None:
    doc = tmp_path / "doc.md"
    doc.write_text(
        ".github/workflows/ci.yml mentioned before the section\n"
        "## How this is enforced\n"
        "| Piece | Role |\n"
        "|-------|------|\n"
        "| [`.github/workflows/release.yml`](.github/workflows/release.yml)"
        " | build |\n"
        "| see also .github/workflows/repro.yml | probe |\n"
        "## Unrelated\n"
        ".github/workflows/ci.yml after the section\n",
        encoding="utf-8")
    table = _enforcement_table(doc)
    # Only in-section mentions count (linked, backticked, or plain), and
    # line numbers land on the mentioning line.
    assert table == {"release.yml": 5, "repro.yml": 6}, table


def test_drift_flags_phantom_rows_and_missing_workflows(tmp_path: Path) -> None:
    doc = tmp_path / "doc.md"
    doc.write_text(
        "## How this is enforced\n"
        "| [`.github/workflows/phantom.yml`](.github/workflows/phantom.yml)"
        " | gone |\n"
        "| [`.github/workflows/ci.yml`](.github/workflows/ci.yml)"
        " | real |\n",
        encoding="utf-8")
    drift = _drift(_enforcement_table(doc), {"ci.yml", "repro.yml"}, doc)
    joined = "; ".join(drift)
    # Phantom row: flagged with its doc line...
    assert f"{doc}:2:" in joined and "phantom.yml" in joined, joined
    assert "does not exist" in joined, joined
    # ...missing workflow: flagged with the fix; listed one: silent.
    assert "repro.yml" in joined and "missing from the" in joined, joined
    assert not any("ci.yml" in d for d in drift), joined


def test_allowlist_suppresses_listed_workflows_only(tmp_path: Path) -> None:
    doc = tmp_path / "doc.md"
    doc.write_text("## How this is enforced\n", encoding="utf-8")
    # The real allowlist entry (tag-check.yml) suppresses exactly itself.
    assert _drift({"ci.yml": 2}, {"ci.yml", "tag-check.yml"}, doc) == []
    # ...but any other unlisted workflow is still flagged.
    drift = _drift({"ci.yml": 2}, {"ci.yml", "tag-check.yml", "new.yml"}, doc)
    assert any("new.yml" in d and "missing from the" in d for d in drift)
    # An allowlist entry whose workflow was deleted from the repo goes stale.
    stale = _drift({"ci.yml": 2}, {"ci.yml"}, doc)
    assert any("tag-check.yml" in d and "no longer exists" in d for d in stale)
