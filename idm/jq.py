"""Tiny jq-style query evaluator for PyIDM's --json output.

Zero dependencies, deliberately small: dot paths (``.downloads[].filename``),
pipes, ``[N]``/``[]``/``[]?`` indexing and iteration, a few jq builtins
(``length``, ``keys``, ``add``, ``min``, ``max``, ``first``, ``last``),
``select(COND)`` filters, ``map(f)``, ``has(key)``, ``startswith(prefix)``,
``endswith(suffix)``, ``contains(sub)``, string/number/bool/null literals,
``+``/``-`` between numbers, comparisons,
and ``and``/``or``/``not``. Not a jq replacement — just the parts of jq that
cover extracting and filtering fields from our summaries, so scripts don't
need jq installed. The supported surface is pinned by
``tests/test_query_grammar.py`` — extend it deliberately, and update the
docs and that snapshot in the same commit.
"""
from __future__ import annotations

import json
import operator

_NUM = {"0": 0, "1": 1, "2": 2, "3": 3, "4": 4, "5": 5,
        "6": 6, "7": 7, "8": 8, "9": 9}
_CMP_OPS = {"==": operator.eq, "!=": operator.ne,
            "<": operator.lt, "<=": operator.le,
            ">": operator.gt, ">=": operator.ge}
_ARITH_OPS = {"+": operator.add, "-": operator.sub}

# The grammar's deliberate surface (pinned by tests/test_query_grammar.py).
# Bare builtins read the piped value; call builtins take one argument which
# may itself be a pipe expression.
_BARE_BUILTINS = ("length", "keys", "add", "min", "max", "first", "last")
_CALL_BUILTINS = ("select", "map", "has", "startswith", "endswith",
                  "contains")
_LITERALS = ("true", "false", "null", "not")


class JqError(ValueError):
    """Bad query syntax or a value the query can't be applied to."""


# --------------------------------------------------------------------- lexer

def _lex(s: str) -> list[str]:
    """Split a query into tokens. Returns [] for empty/whitespace queries."""
    toks: list[str] = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c.isspace():
            i += 1
        elif c == '"':
            j = i + 1
            while j < n:
                if s[j] == "\\" and j + 1 < n:
                    j += 2
                    continue
                if s[j] == '"':
                    break
                j += 1
            if j >= n:
                raise JqError(f"unterminated string in query: {s!r}")
            toks.append(s[i:j + 1])
            i = j + 1
        elif c.isdigit():
            j = i + 1
            while j < n and (s[j].isdigit() or s[j] == "."):
                j += 1
            toks.append(s[i:j])
            i = j
        elif c.isalpha() or c == "_":
            j = i + 1
            while j < n and (s[j].isalnum() or s[j] == "_"):
                j += 1
            toks.append(s[i:j])
            i = j
        elif c == "." and i + 1 < n and (s[i + 1].isalpha() or s[i + 1] == "_"):
            j = i + 2
            while j < n and (s[j].isalnum() or s[j] == "_"):
                j += 1
            toks.append(s[i:j])
            i = j
        else:
            for op in ("==", "!=", "<=", ">="):     # 2-char ops first
                if s.startswith(op, i):
                    toks.append(op)
                    i += len(op)
                    break
            else:
                toks.append(c)
                i += 1
    return toks


# -------------------------------------------------------------------- parser
# Grammar (loosest-binding first): pipe > or > and > cmp > additive > postfix
# (indexing/iteration/fields chained on any primary).

def _parse(toks: list[str]):
    expr, rest = _parse_pipe(toks)
    if rest:
        raise JqError(f"unexpected {rest[0]!r} in query")
    return expr


def _parse_pipe(toks):
    expr, rest = _parse_or(toks)
    while rest and rest[0] == "|":
        rhs, rest = _parse_or(rest[1:])
        expr = ("pipe", expr, rhs)
    return expr, rest


def _parse_or(toks):
    expr, rest = _parse_and(toks)
    while rest and rest[0] == "or":
        rhs, rest = _parse_and(rest[1:])
        expr = ("or", expr, rhs)
    return expr, rest


def _parse_and(toks):
    expr, rest = _parse_cmp(toks)
    while rest and rest[0] == "and":
        rhs, rest = _parse_cmp(rest[1:])
        expr = ("and", expr, rhs)
    return expr, rest


def _parse_cmp(toks):
    expr, rest = _parse_add(toks)
    if rest and rest[0] in _CMP_OPS:
        op = rest[0]
        rhs, rest = _parse_add(rest[1:])
        expr = ("cmp", op, expr, _CMP_OPS[op], rhs)
    return expr, rest


def _parse_add(toks):
    expr, rest = _parse_postfix(toks)
    while rest and rest[0] in _ARITH_OPS:
        op = _ARITH_OPS[rest[0]]
        rhs, rest = _parse_postfix(rest[1:])
        expr = ("arith", op, expr, rhs)
    return expr, rest


def _parse_call(toks, name):
    """Parse the '(' expr ')' after a function name; -> (expr, rest).
    The argument is a full pipe expression (select(.url | startswith(..)))."""
    if len(toks) < 2 or toks[1] != "(":
        raise JqError(f"{name} needs '(' argument ')'")
    expr, rest = _parse_pipe(toks[2:])
    if not rest or rest[0] != ")":
        raise JqError(f"expected ')' after {name} argument")
    return expr, rest[1:]


def _parse_postfix(toks):
    expr, rest = _parse_primary(toks)
    while rest:
        tok = rest[0]
        if tok == "[":
            if len(rest) > 1 and rest[1] == "]":
                expr = ("iter", expr)
                rest = rest[2:]
            else:
                idx, rest = _parse_or(rest[1:])
                if not rest or rest[0] != "]":
                    raise JqError("expected ']' in query")
                expr = ("index", expr, idx)
                rest = rest[1:]
        elif tok == "?" and expr[0] == "iter":
            expr = ("iter?", expr[1])        # '.[]?': tolerate non-iterables
            rest = rest[1:]
        elif tok == "?" and expr[0] == "getfield":
            expr = ("getfield?",) + expr[1:]  # '.field?': tolerate bad types
            rest = rest[1:]
        elif tok.startswith(".") and len(tok) > 1:
            expr = ("getfield", expr, tok[1:])
            rest = rest[1:]
        else:
            break
    return expr, rest


def _parse_primary(toks):
    if not toks:
        raise JqError("unexpected end of query")
    tok = toks[0]
    if tok == ".":                                    # identity
        return ".", toks[1:]
    if tok.startswith(".") and len(tok) > 1:          # root .field access
        return ("getfield", ".", tok[1:]), toks[1:]
    if tok in _BARE_BUILTINS:
        return (tok,), toks[1:]
    if tok in _CALL_BUILTINS:
        expr, rest = _parse_call(toks, tok)
        return (tok, expr), rest
    if tok == "not":
        return ("not",), toks[1:]
    if tok == "true":
        return ("lit", True), toks[1:]
    if tok == "false":
        return ("lit", False), toks[1:]
    if tok == "null":
        return ("lit", None), toks[1:]
    if tok[0] == '"':
        try:
            return ("lit", json.loads(tok)), toks[1:]
        except ValueError:
            raise JqError(f"bad string literal {tok!r}") from None
    if tok[0] in _NUM:
        return ("lit", json.loads(tok)), toks[1:]
    if tok == "[":
        # bare '[...]': iterate/index the whole input, like jq's '.[]'
        if len(toks) > 1 and toks[1] == "]":
            return ("iter", "."), toks[2:]
        if len(toks) > 2 and toks[1][0] in _NUM and toks[2] == "]":
            return ("index", ".", ("lit", json.loads(toks[1]))), toks[3:]
        # '[expr]' collects the expression's stream back into a list
        expr, rest = _parse_pipe(toks[1:])
        if not rest or rest[0] != "]":
            raise JqError("expected ']' in query")
        return ("collect", expr), rest[1:]
    if tok == "(":
        expr, rest = _parse_or(toks[1:])
        if not rest or rest[0] != ")":
            raise JqError("expected ')' in query")
        return expr, rest[1:]
    if tok == "-":
        expr, rest = _parse_primary(toks[1:])
        return ("neg", expr), rest
    raise JqError(f"unexpected {tok!r} in query")


# ---------------------------------------------------------------- evaluator

class _Stream(list):
    """Marks values produced by '.[]' iteration (jq's generator streams).

    jq's select filters a *stream* element-wise, but tests a plain value
    (e.g. a whole array) as-is. We keep both semantics: streams carry this
    marker, everything else is tested whole."""


def _truthy(v) -> bool:
    return v is not None and v is not False and v != 0 and v != ""


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _get_index(base, idx):
    if isinstance(base, list) and _is_num(idx):
        try:
            return base[int(idx)]
        except IndexError:
            return None
    if isinstance(base, dict) and isinstance(idx, str):
        return base.get(idx)
    if base is None:
        return None
    raise JqError(f"cannot index {type(base).__name__} with {idx!r}")


def _eval(node, val):
    kind = node[0]
    if kind == ".":
        return val
    if kind == "lit":
        return node[1]
    if kind == "getfield":
        base = _eval(node[1], val)
        if isinstance(base, dict):
            return base.get(node[2])
        if isinstance(base, list):
            # jq maps .field over arrays (after '.[]' this is the usual
            # '.downloads[].filename' extract); nulls are skipped so the
            # result feeds cleanly into add/max/length. A stream stays a
            # stream so a following select() keeps filtering per element.
            out = [e.get(node[2]) for e in base
                   if isinstance(e, dict) and e.get(node[2]) is not None]
            return _Stream(out) if isinstance(base, _Stream) else out
        if base is None:
            return None
        raise JqError(f"cannot index {type(base).__name__} with '{node[2]}'")
    if kind == "pipe":
        return _eval(node[2], _eval(node[1], val))
    if kind == "collect":
        v = _eval(node[1], val)
        if v is None:
            return []
        if isinstance(v, list):
            return list(v)
        return [v]                      # single-value stream -> 1-element list
    if kind == "getfield?":
        try:
            return _eval(("getfield",) + node[1:], val)
        except JqError:
            return None                     # '.field?' swallows the error
    if kind == "index":
        base = _eval(node[1], val)
        return _get_index(base, _eval(node[2], val))
    if kind in ("iter", "iter?"):
        base = _eval(node[1], val)
        if isinstance(base, list):
            return _Stream(x for x in base if x is not None)
        if isinstance(base, dict):
            return _Stream(base.values())
        if base is None:
            return _Stream()
        if kind == "iter?":
            return _Stream()      # '.[]?': swallow the error like jq
        raise JqError(f"cannot iterate over {type(base).__name__}")
    if kind == "length":
        if val is None:
            return 0
        if isinstance(val, (list, dict, str)):
            return len(val)
        raise JqError(f"{type(val).__name__} has no length")
    if kind == "keys":
        if isinstance(val, dict):
            return sorted(val.keys())
        if isinstance(val, list):
            return list(range(len(val)))
        raise JqError(f"{type(val).__name__} has no keys")
    if kind == "add":
        if not isinstance(val, (list, dict)):
            raise JqError(f"cannot add over {type(val).__name__}")
        items = val if isinstance(val, list) else list(val.values())
        if not items:
            return None
        total = items[0]
        for x in items[1:]:
            try:
                total = total + x
            except TypeError:
                raise JqError("add: mixed types") from None
        return total
    if kind in ("min", "max"):
        if not isinstance(val, list):
            raise JqError(f"{kind}: not a list")
        return (min if kind == "min" else max)(val) if val else None
    if kind == "first":
        if not isinstance(val, list):
            raise JqError("first: not a list")
        return val[0] if val else None
    if kind == "last":
        if not isinstance(val, list):
            raise JqError("last: not a list")
        return val[-1] if val else None
    if kind == "not":
        return not _truthy(val)
    if kind == "map":
        # node[1] is the transform; jq's map(f) is '[.[] | f]'
        if isinstance(val, _Stream):
            return _Stream(_eval(node[1], e) for e in val)
        if isinstance(val, list):
            return [_eval(node[1], e) for e in val]
        if isinstance(val, dict):
            return [_eval(node[1], v) for v in val.values()]
        raise JqError("map: not a list or object")
    if kind in ("has", "startswith", "endswith", "contains"):
        # the argument is evaluated against the same input (jq semantics)
        arg = _eval(node[1], val)
        if node[0] == "has":
            if isinstance(val, dict) and isinstance(arg, str):
                return arg in val
            if isinstance(val, list) and _is_num(arg):
                return 0 <= int(arg) < len(val)
            raise JqError(f"has: cannot check {type(val).__name__} for {arg!r}")
        if isinstance(val, str) and isinstance(arg, str):
            if node[0] == "startswith":
                return val.startswith(arg)
            if node[0] == "endswith":
                return val.endswith(arg)
            return arg in val                       # contains
        raise JqError(f"{node[0]}: needs strings, got {type(val).__name__}")
    if kind == "select":
        if isinstance(val, _Stream):
            # jq filters generator streams element-wise: keep the elements
            # whose condition is truthy (the '.downloads[] | select(...)'
            # idiom). Chain '.field', 'length', 'add', ... afterwards.
            return _Stream(e for e in val if _truthy(_eval(node[1], e)))
        # plain value (scalar, dict, or a whole array): jq tests it as-is
        return val if _truthy(_eval(node[1], val)) else None
    if kind == "neg":
        v = _eval(node[1], val)
        if not _is_num(v):
            raise JqError("cannot negate a non-number")
        return -v
    if kind == "arith":
        a, b = _eval(node[2], val), _eval(node[3], val)
        if not (_is_num(a) and _is_num(b)):
            raise JqError("arithmetic needs numbers")
        return node[1](a, b)
    if kind in ("and", "or"):
        a = _eval(node[1], val)
        ta = _truthy(a)
        if kind == "and":
            return _truthy(_eval(node[2], val)) if ta else False
        return True if ta else _truthy(_eval(node[2], val))
    if kind == "cmp":
        _, _op, l, fn, r = node
        return fn(_eval(l, val), _eval(r, val))
    raise JqError(f"internal: unknown node {kind!r}")


# ------------------------------------------------------------------- public

def apply_query(query: str, value):
    """Evaluate *query* against a JSON-able *value*; raise JqError on any
    problem (bad syntax, type mismatch, or incomparable values)."""
    try:
        tree = _parse(_lex(query))
        return _eval(tree, value)
    except JqError:
        raise
    except Exception as e:  # e.g. comparing a list with a number -> clean error
        raise JqError(f"bad query: {e}") from None


def query_or_none(query: str, value):
    """apply_query with errors mapped to None — None means 'no match'."""
    try:
        return apply_query(query, value)
    except JqError:
        return None


# ------------------------------------------------------------ JSON emitting

def emit_json(value, indent: int | None = None) -> str:
    """Compact by default; query mode pretty-prints (indent=2). ASCII-escaped
    like history_to_json so any console codepage can take the output."""
    return json.dumps(value, indent=indent, ensure_ascii=True,
                      separators=None if indent else (",", ":"))


def _raw_scalar(x) -> str:
    """One jq -r style value: strings bare, bools/null as literals,
    numbers plain, nested lists/dicts as compact JSON."""
    if isinstance(x, str):
        return x
    if x is True:
        return "true"
    if x is False:
        return "false"
    if x is None:
        return "null"
    return emit_json(x, indent=None)


def format_output(result, raw: bool) -> str:
    """Query result -> printable text. With -r (jq -r style): strings bare,
    bools/null as true/false/null, numbers plain, and lists one element per
    line (jq streams, so '[].field' extraction reads line-by-line).
    Without -r, the result renders as pretty JSON."""
    if raw:
        if isinstance(result, list):
            return "\n".join(_raw_scalar(x) for x in result)
        return _raw_scalar(result)
    return emit_json(result, indent=2)


def print_result(result, raw: bool) -> None:
    """Print one query result (or the whole document when no --query was
    given). An empty list under -r prints nothing, like an empty jq stream."""
    import sys
    if raw and isinstance(result, list) and not result:
        return
    sys.stdout.write(format_output(result, raw) + "\n")
