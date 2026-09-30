"""PyIDM portable-bundle CLI entry point.

pyidm.bat execs this file instead of a -c one-liner, so no argument is ever
re-parsed by a cmd.exe command line — queries with pipes, quotes, or any
other shell-significant characters arrive byte-exact.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "site"))

from idm.cli import main  # noqa: E402

sys.exit(main())
