#!/bin/sh
# PyIDM portable-bundle smoke test. Run from the bundle root:
#     ./smoke_test.sh
# Exits 0 when every check passes; prints FAIL lines and exits 1 otherwise.
# Guards the packaging regressions that matter most:
#   * pyidm.sh forwards a --query containing pipes AND embedded double
#     quotes byte-exact (bash -> cmd.exe used to mangle exactly these)
#   * pyidm.bat execs the static cli_main.py entry file (no -c sandwich)
#   * the sh launcher is LF-only so POSIX shells accept it
# Offline by design: it seeds its own download-state file instead of
# touching the network or the user's stores.
set -u

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PY="$HERE/python/python.exe"
fail=0

note() { printf '%s\n' "$*"; }
bad()  { printf 'FAIL: %s\n' "$*"; fail=1; }

[ -x "$PY" ] || { bad "bundle python missing: $PY"; }
[ -f "$HERE/cli_main.py" ] || bad "cli_main.py missing from the bundle"
if grep -q "$(printf '\r')" "$HERE/pyidm.sh" 2>/dev/null; then
    bad "pyidm.sh contains CR (must be LF-only)"
fi
if grep -q '\-c' "$HERE/pyidm.bat" 2>/dev/null; then
    bad "pyidm.bat still uses a -c one-liner (quoting sandwich regression)"
fi

# A disposable store: one finished URL and one errored one.
WORK=$(mktemp -d 2>/dev/null || echo "${TEMP:-/tmp}/pyidm-smoke-$$")
mkdir -p "$WORK" 2>/dev/null
cat > "$WORK/idm.state.json" <<'JSON'
{"version": 1, "downloads": {
  "https://example.com/ok.zip":    {"status": "done", "filename": "ok.zip",
                                    "size": 262144, "updated": 1758748800},
  "https://example.com/bad.bin":   {"status": "error", "filename": "bad.bin",
                                    "size": 0, "updated": 1758748801,
                                    "message": "HTTP 403"}}}
JSON

# 1. pyidm.sh + piped, quoted --query: names of the errored record only.
Q='.[] | select(.status == "error") | .filename'
OUT=$("$HERE/pyidm.sh" -o "$WORK" downloads --query "$Q" -r 2>/dev/null)
[ "$OUT" = "bad.bin" ] || bad "pyidm.sh --query (pipes+quotes) -> [$OUT]"

# 2. pyidm.sh plain --json: parseable, shape holds (2 records).
OUT=$("$HERE/pyidm.sh" -o "$WORK" downloads --json 2>/dev/null)
echo "$OUT" | "$PY" -c "import json,sys; d=json.load(sys.stdin); assert len(d)==2" \
    2>/dev/null || bad "pyidm.sh --json not a 2-record JSON array"

# 3. pyidm.bat from cmd.exe. A .bat re-parses its tail through cmd, so
#    embedded double quotes can never survive (bash->cmd loses regardless);
#    the bat check therefore uses a piped query WITHOUT string literals —
#    the pipe is the character cmd eats, and it must survive. The command
#    is baked into a temp .cmd file (quotes as literal file bytes) because
#    a bash->cmd //c one-liner mangles the tail before cmd even parses it —
#    this way cmd drives pyidm.bat exactly as a cmd user would. The store
#    dir uses its 8.3 short path so no quotes are needed around it.
SHORT=$(cygpath -d "$WORK" 2>/dev/null)
V1=$("$HERE/pyidm.sh" --version 2>/dev/null)
V2=$V1
if [ -n "$SHORT" ] && [ -n "${TEMP:-}" ]; then
    HEREW=$(cygpath -w "$HERE" 2>/dev/null)
    RUNCMD=$(mktemp "$TEMP/smoke-XXXXXX.cmd" 2>/dev/null) || RUNCMD="${TEMP}/smoke-$$.cmd"
    printf '@echo off\r\ncd /d "%s"\r\ncall .\\pyidm.bat -o %s downloads --query ".[] | select(.size > 0) | .filename" -r\r\n' \
        "$HEREW" "$SHORT" > "$RUNCMD"
    OUT=$(cmd //c "$(cygpath -w "$RUNCMD")" 2>/dev/null | tr -d '\r')
    [ "$OUT" = "ok.zip" ] || bad "pyidm.bat piped --query from cmd.exe -> [$OUT]"
    V2=$(cmd //c "$(cygpath -w "$HEREW")\\pyidm.bat" --version 2>/dev/null)
    rm -f "$RUNCMD" 2>/dev/null
else
    note "note: no 8.3 short path or TEMP; skipping pyidm.bat checks"
fi
[ "$V1" = "$V2" ] && [ -n "$V1" ] || bad "launcher versions disagree: [$V1] vs [$V2]"

rm -rf "$WORK" 2>/dev/null

if [ "$fail" -eq 0 ]; then
    note "smoke test: all checks passed ($V1)"
else
    note "smoke test: FAILED"
fi
exit "$fail"
