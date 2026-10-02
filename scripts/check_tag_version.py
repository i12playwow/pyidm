#!/usr/bin/env python3
"""Tag/version gate: fail unless the pushed v* tag matches the package version.

Compares GITHUB_REF_NAME (e.g. "v1.11.77") against idm/__init__.py's
__version__, and cross-checks that pyproject.toml declares the same
version. Used in two places:

* .github/workflows/tag-check.yml  -> named red/green signal on tag pushes
* .github/workflows/release.yml    -> first gate; a drifted tag can never
  build, smoke-test, or publish a release

Stdlib-only and 3.9+-compatible so it runs on any runner's Python.
Exits 1 with a GitHub Actions error annotation on mismatch.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def fail(msg: str) -> None:
    print(f"::error::{msg}")
    sys.exit(1)


def find(pattern: str, text: str, where: str) -> str:
    m = re.search(pattern, text, re.M)
    if not m:
        fail(f"could not find {where}")
    return m.group(1)  # type: ignore[union-attr]  # fail() exits on None


def main() -> None:
    tag = os.environ.get("GITHUB_REF_NAME", "")

    init_text = (ROOT / "idm" / "__init__.py").read_text(encoding="utf-8")
    version = find(r"^__version__\s*=\s*[\"']([^\"']+)[\"']", init_text,
                   "__version__ in idm/__init__.py")

    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    # First top-level `version =` is the [project] table's declaration.
    declared = find(r"^version\s*=\s*[\"']([^\"']+)[\"']", pyproject,
                    "version in pyproject.toml")
    if declared != version:
        fail(f"idm/__init__.py has {version!r} but pyproject.toml has "
             f"{declared!r}; bump both together")

    expected = f"v{version}"
    if tag != expected:
        fail(f"tag {tag!r} does not match the package version {version!r} "
             f"(expected tag {expected!r}); delete the tag, re-tag "
             f"correctly, and push again")
    print(f"tag {tag} matches idm/__init__.py version {version} "
          f"(pyproject.toml agrees)")


if __name__ == "__main__":
    main()
