"""Guard the py3.9 floor: ``Path.write_text(newline=...)`` is Python 3.10+.

``Path.write_text`` grew its *newline* parameter in 3.10 (and ``read_text``
in 3.13); on 3.9 both raise ``TypeError``. This is exactly how every file
export broke when the 3.9 leg first ran — 69 failures across the CLI export
commands, the GUI Save-As dialogs, and the report presets, invisible on a
3.12 dev box. The sanctioned replacement is
:meth:`idm.utils.write_text_newlines`, which uses ``open()``'s newline
parameter — available on every supported version.

The check is AST-based, so it catches the pattern however it is formatted
(single line or split across lines) and runs identically on every matrix leg.
"""
from __future__ import annotations

import ast
from pathlib import Path

from idm.utils import write_text_newlines

REPO_ROOT = Path(__file__).resolve().parent.parent
# tests/ too: a violation there would crash the 3.9 CI leg itself.
SCAN_PACKAGES = ("idm", "tests")


def _newline_text_sites() -> list[str]:
    """``.write_text(...)``/``.read_text(...)`` calls passing ``newline=``.

    AST-level, so argument lists wrapped over several lines are seen whole —
    a single-line grep missed three real sites during the original fix.
    """
    bad: list[str] = []
    for pkg in SCAN_PACKAGES:
        for path in sorted((REPO_ROOT / pkg).glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr in ("write_text", "read_text")):
                    continue
                if any(kw.arg == "newline" for kw in node.keywords):
                    bad.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    return bad


def test_no_path_write_text_newline_kwarg() -> None:
    sites = _newline_text_sites()
    assert not sites, (
        "Path.write_text/read_text(newline=...) needs Python >=3.10 "
        "(read_text: >=3.13); the package floor is 3.9. "
        "Use idm.utils.write_text_newlines(path, text) instead. "
        f"Offending sites: {', '.join(sites)}"
    )


def test_write_text_newlines_helper_writes_exact_bytes(tmp_path) -> None:
    # The sanctioned replacement must keep newline="" semantics: on Windows a
    # plain text write would turn the CSV's \r\n into \r\r\n. Byte-exact both
    # ways proves translation is off (and that the helper cannot be deleted
    # without this suite noticing).
    p = tmp_path / "probe.csv"
    write_text_newlines(p, "a,b\r\n1,2\r\n")
    assert p.read_bytes() == b"a,b\r\n1,2\r\n"
