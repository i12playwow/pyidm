from __future__ import annotations

import pytest

from idm.links import (
    LinkExpiredError,
    captures_from,
    expand_template,
    find_provider,
    looks_expired,
    resolve_url,
)


class FakeResp:
    def __init__(self, payload, ctype="application/json", status=200):
        self._payload = payload
        self.status_code = status
        self.headers = {"Content-Type": ctype}
        self.text = payload if isinstance(payload, str) else ""
        self._json = None if isinstance(payload, str) else payload


def test_looks_expired_statuses():
    assert looks_expired(403)
    assert looks_expired(410)
    assert not looks_expired(200)
    assert not looks_expired(404)  # plain 404 is not an expiry signal


def test_looks_expired_body_text():
    assert looks_expired(400, {"Content-Type": "application/json"},
                         b'{"error": "link expired"}')
    assert looks_expired(400, {"Content-Type": "text/html"},
                         b"<h1>Invalid Signature</h1>")
    assert not looks_expired(400, {"Content-Type": "application/json"},
                             b'{"error": "bad request"}')


def test_provider_matching():
    raw = [
        {"match": r"^https://cdn\.example\.com/files/([^/?]+)",
         "refresh_url": "https://api.example.com/link/{1}"},
        {"match": "other-host", "command": "echo url"},
    ]
    p = find_provider("https://cdn.example.com/files/abc123?sig=x", raw)
    assert p is not None and p.refresh_url.endswith("{1}")
    assert find_provider("https://unrelated.com/x", raw) is None


def test_captures_and_expand():
    from idm.links import LinkProvider

    p = LinkProvider(match=r"^https://h/(?<id>[A-Za-z0-9]+)/f(?<n>\d+)")
    url = "https://h/abc123/f42?sig=xx"
    m = p.regex.search(url)
    assert m is not None  # PCRE-style named groups are normalized automatically
    caps = captures_from(url, m)
    assert caps["0"] == url  # {0} is always the FULL original URL
    assert caps["1"] == "abc123" and caps["id"] == "abc123" and caps["2"] == "42"
    assert expand_template("https://api/x/{id}/f/{n}", caps) == "https://api/x/abc123/f/42"


def test_refresh_url_encoding():
    """{0e} must URL-encode the whole captured URL (query-string safe)."""
    from idm.links import LinkProvider

    seen = {}

    class S:
        def get(self, url, timeout=None, headers=None):
            seen["url"] = url

            class R:
                headers = {"Content-Type": "application/json"}
                text = '{"url": "https://new/file.bin"}'

                def json(self):
                    return {"url": "https://new/file.bin"}

                def raise_for_status(self):
                    pass

            return R()

    p = LinkProvider(
        match=r"^https://cdn\.x/",
        refresh_url="https://api.x/refresh?url={0e}",
        url_field="url",
    )
    expired = "https://cdn.x/dl/abc?token=zz&sig=xy+z/1"
    assert resolve_url(expired, p, session=S()) == "https://new/file.bin"
    assert seen["url"] == (
        "https://api.x/refresh?url="
        "https%3A%2F%2Fcdn.x%2Fdl%2Fabc%3Ftoken%3Dzz%26sig%3Dxy%2Bz%2F1"
    )


def test_resolve_url_json_field():
    p = __import__("idm.links", fromlist=["LinkProvider"]).LinkProvider(
        match=r"^https://old/", refresh_url="https://api/fresh", url_field="data.url")
    class S:
        def get(self, url, timeout=None, headers=None):
            class R:
                status_code = 200
                headers = {"Content-Type": "application/json"}
                text = '{"data": {"url": "https://new/file"}}'
                def json(self):
                    return {"data": {"url": "https://new/file"}}
                def raise_for_status(self):
                    pass
            assert url == "https://api/fresh"
            return R()
    assert resolve_url("https://old/x", p, session=S()) == "https://new/file"


def test_resolve_url_missing_field_raises():
    from idm.links import LinkProvider
    p = LinkProvider(match=r"^https://old/", refresh_url="https://api/fresh", url_field="nope.here")
    class S:
        def get(self, url, timeout=None, headers=None):
            class R:
                headers = {"Content-Type": "application/json"}
                text = '{"data": {}}'
                def json(self):
                    return {"data": {}}
                def raise_for_status(self):
                    pass
            return R()
    with pytest.raises(LinkExpiredError):
        resolve_url("https://old/x", p, session=S())
