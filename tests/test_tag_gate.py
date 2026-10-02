"""Behavior lock for scripts/check_tag_version.py.

The release workflow advertises a workflow_dispatch "pre-tag dry run"; the
gate must skip the tag comparison there (a dispatch ref like refs/heads/main
has no tag name) while still enforcing the tag on every tag-ref run — tag
pushes and manual dispatches on a tag alike, since the release publish steps
fire whenever github.ref is refs/tags/*.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_tag_version.py"


def _version() -> str:
    text = (ROOT / "idm" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r"^__version__\s*=\s*[\"']([^\"']+)[\"']", text, re.M)
    assert m, "__version__ not found in idm/__init__.py"
    return m.group(1)


def _run(env_extra: dict) -> "subprocess.CompletedProcess[str]":
    env = os.environ.copy()
    for key in ("GITHUB_REF_NAME", "GITHUB_REF", "GITHUB_EVENT_NAME"):
        env.pop(key, None)
    env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True, text=True, env=env, cwd=str(ROOT))


def test_dispatch_on_branch_skips_tag_comparison():
    result = _run({
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_REF_NAME": "main",
    })
    assert result.returncode == 0, result.stdout + result.stderr
    assert "dry run" in result.stdout


def test_push_matching_tag_passes():
    version = _version()
    result = _run({
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF": f"refs/tags/v{version}",
        "GITHUB_REF_NAME": f"v{version}",
    })
    assert result.returncode == 0, result.stdout + result.stderr


def test_push_mismatched_tag_fails():
    result = _run({
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF": "refs/tags/v0.0.0",
        "GITHUB_REF_NAME": "v0.0.0",
    })
    assert result.returncode == 1
    assert "does not match" in result.stdout


def test_dispatch_on_tag_is_still_enforced():
    result = _run({
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/tags/v0.0.0",
        "GITHUB_REF_NAME": "v0.0.0",
    })
    assert result.returncode == 1
    assert "does not match" in result.stdout
