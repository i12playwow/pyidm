"""The portable bundle's launchers must survive shell-significant arguments.

pyidm.bat used to build its command line with a -c one-liner containing
nested quotes; arguments carrying | or " (jq-style --query) got re-parsed by
cmd.exe and broke. The fix under test: a static cli_main.py entry file plus a
native sh launcher, wired into the bundle by build_portable.bat.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_cli_main_entry_runs_real_main():
    src = (ROOT / "portable" / "cli_main.py").read_text(encoding="utf-8")
    assert "from idm.cli import main" in src
    assert "sys.exit(main())" in src
    # the site dir must be resolved relative to the file, not the cwd
    assert 'Path(__file__).resolve().parent / "site"' in src


def test_pyidm_sh_is_posix_and_quoted():
    src = (ROOT / "portable" / "pyidm.sh").read_text(encoding="utf-8")
    assert src.startswith("#!/bin/sh")
    assert 'exec "$HERE/python/python.exe" "$HERE/cli_main.py" "$@"' in src
    assert "$@" in src  # every argument forwarded, none re-parsed


def test_build_wires_launchers_into_bundle():
    bat = (ROOT / "build_portable.bat").read_text(encoding="utf-8")
    # both templates are copied into the bundle
    assert "portable\\cli_main.py" in bat and "cli_main.py" in bat
    assert "portable\\pyidm.sh" in bat and "pyidm.sh" in bat
    # LF conversion so the sh script stays valid under POSIX shells on Windows
    assert "pyidm.sh" in bat and "WriteAllText" in bat
    # the generated pyidm.bat must exec the static entry file, not a -c sandwich
    generated = re.search(
        r"\(\s*echo @echo off.*?\) > \"%OUT%\\pyidm\.bat\"", bat, flags=re.DOTALL)
    assert generated, "pyidm.bat generation block not found"
    block = generated.group(0)
    assert "cli_main.py" in block
    assert "-c" not in block


def test_pyidm_gui_launcher_still_generated():
    bat = (ROOT / "build_portable.bat").read_text(encoding="utf-8")
    assert 'pyidm-gui.bat"' in bat
    assert "pythonw.exe" in bat


def test_smoke_test_script_is_wired_and_real():
    sh = (ROOT / "portable" / "smoke_test.sh").read_text(encoding="utf-8")
    # exercises BOTH launchers and the exact regression: pipes + embedded
    # double quotes inside a --query
    assert "pyidm.sh" in sh and "pyidm.bat" in sh
    assert 'select(.status == "error")' in sh
    assert sh.startswith("#!/bin/sh")
    assert "exit \"$fail\"" in sh            # real pass/fail semantics
    # the build copies it into the bundle and LF-converts it like pyidm.sh
    bat = (ROOT / "build_portable.bat").read_text(encoding="utf-8")
    assert "portable\\smoke_test.sh" in bat
    assert "smoke_test.sh" in bat.split("WriteAllText")[0].split("pyidm.sh")[-1] \
        or "'pyidm.sh','smoke_test.sh'" in bat
