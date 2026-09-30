"""Expiring-link handling: detect expiry and resolve fresh URLs via providers.

A "link provider" tells PyIDM how to turn a stale (expired) URL into a fresh
one. Providers are configured in idm.json / ~/.idm/config.json:

    "link_providers": [
      {
        "match": "^https://cdn\\\\.example\\\\.com/files/([^/?]+)",
        "refresh_url": "https://api.example.com/v1/files/{1}/link",
        "url_field": "data.url"
      },
      {
        "match": "^https://host/(?<id>[A-Za-z0-9]+)",
        "command": "python get_link.py {id}"
      }
    ]

- ``match`` is a regex tested against the URL. Templates can interpolate
  {0} = the full original URL, {1}, {2}, ... = positional capture groups and
  {name} = named groups. Append ``e`` for URL-encoding — {0e}, {1e}, {namee} —
  which you need whenever the value lands in a query string.
- ``refresh_url`` is fetched and a URL is extracted from the JSON response at
  the dotted ``url_field`` path (plain-text/redirect responses also work).
- ``command`` is a shell command whose stdout must contain the fresh URL
  (the last http(s) line wins). IDM_URL env var receives the original URL.
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import requests

EXPIRY_STATUSES = {401, 403, 410, 419}
RETRYABLE_STATUSES = {408, 425, 429, 500, 502, 503, 504}

EXPIRY_BODY_RE = re.compile(
    r"(\b(link|url|token|signature)[^\n]{0,40}\b(expired|invalid)\b"
    r"|\bexpired\b|invalid\s+(token|signature)|access\s+denied|unauthorized)",
    re.IGNORECASE,
)


def looks_expired(status: int, headers=None, body: bytes = b"") -> bool:
    """Heuristically decide whether an HTTP response means the link expired."""
    if status in EXPIRY_STATUSES:
        return True
    if status >= 400:
        ctype = (headers or {}).get("Content-Type", "")
        if "json" in ctype or "html" in ctype or "text" in ctype:
            try:
                text = (body or b"").decode("utf-8", "ignore")
            except Exception:
                text = ""
            return bool(EXPIRY_BODY_RE.search(text[:2048]))
    return False


class LinkExpiredError(RuntimeError):
    pass


_NAMED_GROUP_RE = re.compile(r"\(\?<([A-Za-z_][A-Za-z0-9_]*)>")


def _normalize_pattern(pattern: str) -> str:
    """Allow PCRE-style (?<name>...) groups; Python's re wants (?P<name>...)."""
    return _NAMED_GROUP_RE.sub(r"(?P<\1>", pattern)


@dataclass
class LinkProvider:
    match: str
    refresh_url: str | None = None
    command: str | None = None
    url_field: str = "url"
    headers: dict | None = None
    regex: re.Pattern = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.regex = re.compile(_normalize_pattern(self.match))


def providers_from_config(raw) -> list[LinkProvider]:
    out: list[LinkProvider] = []
    for item in raw or []:
        known = {k: v for k, v in item.items() if k in {"match", "refresh_url", "command", "url_field", "headers"}}
        out.append(LinkProvider(**known))
    return out


def find_provider(url: str, raw_providers) -> LinkProvider | None:
    for p in providers_from_config(raw_providers):
        if p.regex.search(url):
            return p
    return None


def captures_from(url: str, m: re.Match) -> dict[str, str]:
    """Template variables. {0} is the FULL original URL (not the regex match);
    {1}, {2}, ... and {name} are the regex capture groups."""
    caps = {"0": url}
    for i, g in enumerate(m.groups(), start=1):
        caps[str(i)] = g if g is not None else ""
    for k, v in m.groupdict().items():
        if v is not None:
            caps[k] = v
    # URL-encoded variants: {0e}, {1e}, {namee} — safe for query strings.
    for k, v in list(caps.items()):
        caps[k + "e"] = quote(v, safe="")
    return caps


def expand_template(template: str, caps: dict[str, str]) -> str:
    return template.format_map(caps)


def _dig(data: Any, path: str) -> Any:
    cur = data
    for part in path.split("."):
        if isinstance(cur, list):
            cur = cur[int(part)]
        elif isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            raise LinkExpiredError(f"field {path!r} not found in refresh response")
    return cur


def resolve_url(
    url: str,
    provider: LinkProvider,
    session: requests.Session | None = None,
    timeout: float = 30.0,
) -> str:
    """Resolve a fresh download URL for an expired one via the provider."""
    m = provider.regex.search(url)
    if not m:
        raise LinkExpiredError(f"provider pattern {provider.match!r} does not match URL")
    caps = captures_from(url, m)
    own = session or requests.Session()

    if provider.command:
        cmd = expand_template(provider.command, caps)
        env = dict(os.environ, IDM_URL=url)
        try:
            proc = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=max(60.0, timeout * 4), env=env,
            )
        except subprocess.TimeoutExpired as e:
            raise LinkExpiredError(f"provider command timed out: {cmd!r}") from e
        if proc.returncode != 0:
            raise LinkExpiredError(
                f"provider command failed ({proc.returncode}): {(proc.stderr or proc.stdout).strip()[:300]}"
            )
        candidates = [
            ln.strip() for ln in (proc.stdout or "").splitlines()
            if ln.strip().lower().startswith(("http://", "https://"))
        ]
        if not candidates:
            raise LinkExpiredError("provider command did not print a URL")
        return candidates[-1]

    if not provider.refresh_url:
        raise LinkExpiredError("provider has neither 'refresh_url' nor 'command'")

    endpoint = expand_template(provider.refresh_url, caps)
    resp = own.get(endpoint, timeout=timeout, headers=provider.headers or {})
    resp.raise_for_status()
    ctype = resp.headers.get("Content-Type", "")
    text = resp.text.strip()
    if "json" in ctype or text.startswith(("{", "[")):
        try:
            data = resp.json()
        except ValueError as e:
            raise LinkExpiredError(f"refresh endpoint returned invalid JSON: {e}") from e
        value = _dig(data, provider.url_field)
        if not isinstance(value, str) or not value.lower().startswith(("http://", "https://")):
            raise LinkExpiredError(f"JSON field {provider.url_field!r} is not a URL: {value!r}")
        return value
    if text.lower().startswith(("http://", "https://")):
        return text.splitlines()[0].strip()
    raise LinkExpiredError(f"unexpected refresh response (Content-Type {ctype!r})")
