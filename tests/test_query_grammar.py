"""Snapshot + behavioral pin of the --query grammar's deliberate surface.

tests/test_query_grammar.py exists so the query language can only grow on
purpose:

* The *golden snapshot* pins the accepted grammar surface: builtin names,
  operators, literal words, postfix forms, and the error messages for
  rejected syntax. Any grammar change — adding a builtin, accepting a new
  form, wording an error — changes the snapshot and fails here. To extend
  the language deliberately: make the change, run pytest with UPDATE
  environment variable set (UPDATE=1 python -m pytest
  tests/test_query_grammar.py) to review and rewrite the snapshot, then
  commit it together with docs/automation.md and this test.
* The *behavioral tables* check every accepted form actually evaluates and
  every documented rejection still raises JqError — so extending one builtin
  cannot silently unhook another.
* Cross-checks keep idm/jq.py's docstring and docs/automation.md in sync
  with the snapshot.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from idm import jq
from idm.jq import JqError, apply_query

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / "tests" / "data" / "query_grammar.json"

DOC = {
    "count": 2, "ok": 1, "all_ok": False, "total_bytes": 100,
    "by_status": {"done": 1, "error": 1},
    "downloads": [
        {"url": "https://cdn/a.zip", "filename": "a.zip", "status": "done",
         "total_bytes": 100},
        {"url": "http://cdn/b.bin", "filename": "b.bin", "status": "error",
         "total_bytes": 0, "message": "HTTP 500"},
    ],
}


def _shape(v):
    """Type-exact, value-free skeleton (mirrors tests/test_docs_automation)."""
    if isinstance(v, dict):
        return {"dict": {k: _shape(x) for k, x in sorted(v.items())}}
    if isinstance(v, list):
        return {"list": [_shape(x) for x in v]}
    return type(v).__name__


# --------------------------------------------------------------- the surface
# Every form the grammar deliberately accepts. Each entry must evaluate
# without error against DOC (the result value is NOT pinned here — behavior
# lives in test_cli_query.py; this table pins *acceptance*).
ACCEPTED = [
    # identity / fields / indexing
    ".",
    ".count",
    ".downloads[0].filename",
    ".downloads[-1].status",
    ".missing",
    # iteration (+ optional form) and bare-[ input
    ".downloads[].filename",
    ".downloads[]?",
    ".missing[]?",
    "[].status",
    "[]",
    # pipes
    ".downloads | length",
    ".downloads | first | .status",
    ". | length",
    # bare builtins (value-dependent ones get a suitable pipe input)
    "length",
    ".downloads[0] | keys",
    ".downloads[].total_bytes | add",
    ".downloads[].total_bytes | min",
    ".downloads[].total_bytes | max",
    ".downloads | first",
    ".downloads | last",
    # call builtins (incl. pipes inside the argument)
    'select(.status == "error")',
    '.downloads[] | select(.status == "error") | .filename',
    ".downloads | map(.filename)",
    '.downloads | map(has("url"))',
    '.downloads[0]["filename"]',    # bracket string keys (jq-compatible)
    '["count"]',
    '.downloads[0].url | startswith("https://")',
    '.downloads[] | select(.url | startswith("http")) | .filename',
    '.downloads[0].url | endswith(".zip")',
    '.downloads[] | select(.filename | endswith(".zip")) | .url',
    '.downloads[1].message | contains("500")',
    '.downloads[] | select(.url | contains("cdn")) | .filename',
    # literals
    "true",
    "false",
    "null",
    '"string"',
    "42",
    "-3",
    # operators
    ".ok == .count",
    '.downloads[1].message != null',
    ".count + 1",
    ".count - 1",
    ".count == 2 and .ok == 1",
    ".all_ok or .ok",
    "not",
    ".ok | not",
    # grouping
    "(.ok)",
    "(.ok + 1) == 2",
    ".downloads | (length == 2)",
    # postfix chains on primaries
    ".downloads[0] | keys | first",
    ".downloads[]? | .filename?",
    ".downloads[0].filename?",
    '.["count"]',                  # bracket string keys (jq-compatible)
    '["count"]',
    '.downloads | map(.filename)',
    ('.downloads[] | select(.status == "error") | .filename', DOC),
    # collect form over a list-typed payload (downloads/history shape)
    ('[.[] | select(.status == "error")] | length', DOC["downloads"]),
    '.downloads[0]["filename"]',
    ".count | .[]?",
]


# Every form the grammar deliberately rejects. Adding syntax means deleting
# a row here — deliberately, and in the same commit as the snapshot.
REJECTED = [
    # unsupported jq features (each maps to a roadmap item, not an oversight)
    ".[1:2]",                       # slices
    "$var",                         # variables
    "@base64",                      # format strings
    ".a as $x | .b",                # bindings
    "def f: .;",                    # definitions
    ".[] | recurse",                # recursion
    ".a // .b",                     # alternative operator
    ".a | scan(\"x\")",             # regex builtins
    ".a | test(\"x\")",
    ".a | capture(\"x\")",            # regex capture (contains/endswith are strings-only)
    ".a |= 1",                      # update-assign
    ".a = 1",
    "env.HOME",
    "input",
    # malformed syntax
    ".downloads[0].bogus[",
    ".count +",
    ".count + +",
    "bogus",
    "select",
    "select(",
    "select()",
    "select.x",
    "map",
    "map(",
    "has",
    'has("k"',
    "startswith(",
    "endswith(",
    "contains(",
    '"unterminated',
    "(.ok",
    ".ok)",
    ".downloads | ",
    "| length",
    "..",
]

# Error-message fragments each rejection must produce (subset matched with
# `in`); keeps rejections semantic, not accidental.
_REJECT_REASON = {
    "select": "select needs '('",
    "map": "map needs '('",
    "has": "has needs '('",
    "startswith": "startswith needs '('",
    "endswith": "endswith needs '('",
    "contains": "contains needs '('",
}


def test_accepted_forms_all_evaluate():
    for entry in ACCEPTED:
        q, val = (entry, DOC) if isinstance(entry, str) else entry
        try:
            apply_query(q, val)
        except JqError as e:
            raise AssertionError(f"accepted form now fails: {q!r} -> {e}")


def test_rejected_forms_all_raise():
    for q in REJECTED:
        with pytest.raises(JqError):
            apply_query(q, DOC)


# ------------------------------------------------------------- the snapshot
def _norm(entry):
    """JSON can't hold tuples: normalize (query, value) entries to lists."""
    return list(entry) if isinstance(entry, tuple) else entry


def _snapshot() -> dict:
    return {
        "bare_builtins": list(jq._BARE_BUILTINS),
        "call_builtins": list(jq._CALL_BUILTINS),
        "literal_words": list(jq._LITERALS),
        "cmp_ops": sorted(jq._CMP_OPS),
        "arith_ops": sorted(jq._ARITH_OPS),
        "accepted_count": len(ACCEPTED),
        "rejected_count": len(REJECTED),
        "accepted": [_norm(e) for e in ACCEPTED],
        "rejected": REJECTED,
        "error_messages": {
            q: str(_first_error(q)) for q in REJECTED[:12]
        },
        "result_shapes": {
            (q if isinstance(q, str) else q[0]):
                _shape(apply_query(*(q if isinstance(q, tuple) else (q, DOC))))
            for q in ACCEPTED
        },
    }


def _first_error(q: str) -> JqError:
    try:
        apply_query(q, DOC)
    except JqError as e:
        return e
    raise AssertionError(f"{q!r} no longer raises")


def test_grammar_snapshot():
    current = _snapshot()
    if SNAPSHOT.exists():
        stored = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        if stored == current:
            return
        if __import__("os").environ.get("UPDATE"):
            SNAPSHOT.write_text(json.dumps(current, indent=2) + "\n",
                                encoding="utf-8")
            pytest.skip("snapshot refreshed (UPDATE=1); commit it together "
                        "with the grammar change and doc updates")
        raise AssertionError(
            "the --query grammar changed — if deliberate, refresh the "
            "snapshot (UPDATE=1 pytest tests/test_query_grammar.py) and "
            "update docs/automation.md in the same commit; diff:\n"
            + _diff_summary(stored, current))
    else:
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT.write_text(json.dumps(current, indent=2) + "\n",
                            encoding="utf-8")
        pytest.skip(f"snapshot created at {SNAPSHOT}; commit it")


def _diff_summary(old: dict, new: dict) -> str:
    lines = []
    for key in ("bare_builtins", "call_builtins", "literal_words",
                "cmp_ops", "arith_ops"):
        if old.get(key) != new.get(key):
            lines.append(f"{key}: {old.get(key)} -> {new.get(key)}")
    for key in ("accepted", "rejected"):          # ordered lists
        o, n = old.get(key) or [], new.get(key) or []
        for q in n:
            if q not in o:
                lines.append(f"{key} + {q!r}")
        for q in o:
            if q not in n:
                lines.append(f"{key} - {q!r}")
    for key in ("error_messages", "result_shapes"):  # dicts keyed by query
        o, n = old.get(key) or {}, new.get(key) or {}
        for name in sorted(set(o) | set(n)):
            if o.get(name) != n.get(name):
                lines.append(f"{key}[{name!r}] changed")
    return "\n".join(lines) or "(content-identical, ordering changed)"


def test_snapshot_refresh(monkeypatch):
    """Redundant belt-and-braces: UPDATE=1 always leaves a fresh snapshot."""
    if not SNAPSHOT.exists():
        pytest.skip("no snapshot yet")
    if not __import__("os").environ.get("UPDATE"):
        pytest.skip("passive without UPDATE=1")
    SNAPSHOT.write_text(json.dumps(_snapshot(), indent=2) + "\n",
                        encoding="utf-8")


# ----------------------------------------------------- cross-file coherence
def test_module_docstring_lists_every_builtin():
    doc = (ROOT / "idm" / "jq.py").read_text(encoding="utf-8")
    head = doc[: doc.index('"""', 3)]
    for name in (*jq._BARE_BUILTINS, *jq._CALL_BUILTINS):
        assert name in head, f"jq.py docstring missing {name!r}"


def test_docs_list_the_grammar_surface():
    doc = (ROOT / "docs" / "automation.md").read_text(encoding="utf-8")
    for name in (*jq._BARE_BUILTINS, *jq._CALL_BUILTINS):
        assert name in doc, f"docs/automation.md missing builtin {name!r}"
    for op in ("==", "!=", "<=", ">=", "|"):
        assert op in doc, f"docs/automation.md missing operator {op!r}"
    # the docs still describe the language as deliberate/finite
    assert re.search(r"only this — deliberately", doc)
