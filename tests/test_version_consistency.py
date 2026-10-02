"""Guard: idm/__init__.py and pyproject.toml must declare the same version.

The tag gate (scripts/check_tag_version.py) compares pushed v* tags against
__version__; this test keeps the two internal declarations from drifting
apart, so a "correct" tag can never release mismatched metadata. Both files
 drifted once (tag v1.11.53 vs 1.11.77 code) — see CONTRIBUTING.md.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_version_declarations_match():
    init_text = (ROOT / "idm" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r"^__version__\s*=\s*[\"']([^\"']+)[\"']", init_text, re.M)
    assert m, "__version__ not found in idm/__init__.py"
    init_version = m.group(1)

    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    # First top-level `version =` is the [project] table's declaration.
    m = re.search(r"^version\s*=\s*[\"']([^\"']+)[\"']", pyproject, re.M)
    assert m, "version not found in pyproject.toml"
    pyproject_version = m.group(1)

    assert init_version == pyproject_version, (
        f"idm/__init__.py has {init_version!r} but pyproject.toml has "
        f"{pyproject_version!r}; bump both together"
    )
