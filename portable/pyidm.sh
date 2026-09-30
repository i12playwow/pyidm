#!/bin/sh
# PyIDM portable-bundle launcher for POSIX shells (Git Bash, WSL, ...).
# Usage: ./pyidm.sh <args>   — e.g. ./pyidm.sh providers --query '.p[] | select(.ok)' -r
# Bash -> pyidm.bat is fragile: bash consumes the quoting layer, and cmd.exe
# then re-parses the tail, where | and embedded quotes break. This script
# execs python directly, so "$@" arrives byte-exact.
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$HERE/python/python.exe" "$HERE/cli_main.py" "$@"
