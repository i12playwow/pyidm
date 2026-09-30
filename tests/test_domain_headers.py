"""Unit tests for per-domain request headers (idm.core.headers_for_url)."""
from __future__ import annotations

import pytest

from idm.core import Downloader, headers_for_url

RULES = [
    {"match": "supjav\\.com", "headers": {"Referer": "https://supjav.com/"}},
    {"match": r"cdn\.example\.com", "headers": {"Cookie": "token=x"}},
    {"headers": {"X-Everywhere": "1"}},                 # catch-all (no match)
    {"match": "supjav", "headers": {"Referer": "https://later.example/"}},
]


def test_matching_rule_applies():
    got = headers_for_url("https://supjav.com/459218.html", RULES)
    assert got["Referer"] == "https://later.example/"  # later rule wins
    assert got["X-Everywhere"] == "1"


def test_cdn_rule_applies():
    got = headers_for_url("https://cdn.example.com/a.jpg", RULES)
    assert got["Cookie"] == "token=x"
    assert "Referer" not in got


def test_no_match_yields_catchall_only():
    got = headers_for_url("https://other.net/b.mp4", RULES)
    assert got == {"X-Everywhere": "1"}


def test_no_rules_yields_empty():
    assert headers_for_url("https://x.com/", []) == {}
    assert headers_for_url("https://x.com/", None) == {}


def test_later_rules_win_on_conflicts():
    rules = [
        {"match": "a", "headers": {"Referer": "first", "Keep": "1"}},
        {"match": "a", "headers": {"Referer": "second"}},
    ]
    assert headers_for_url("https://a.com/f", rules) == {
        "Referer": "second", "Keep": "1",
    }


def test_malformed_rules_ignored():
    rules = [
        "not-a-dict",
        5,
        {"match": "x"},                     # no headers
        {"match": "x", "headers": {}},      # empty headers
        {"match": "x", "headers": "nope"},  # not a dict
        {"match": "([", "headers": {"A": "b"}},  # bad regex
        {"match": "x", "headers": {"A": 7, "B": "ok"}},  # non-str value skipped
    ]
    assert headers_for_url("https://x.com/", rules) == {"B": "ok"}


def test_pcre_style_groups_accepted():
    rules = [{"match": r"(?<host>[^/]+)\.example\.com",
              "headers": {"Referer": "https://ok/"}}]
    assert headers_for_url("https://cdn.example.com/f", rules)


def test_downloader_uses_config_rules():
    dl = Downloader(config={
        "domain_headers": RULES,
        "headers": {"User-Agent": "test/1"},
    }, out_dir=".")
    got = dl._request_headers("https://supjav.com/f.jpg",
                              {"Range": "bytes=0-0"})
    assert got["Range"] == "bytes=0-0"                    # base kept
    assert got["Referer"] == "https://later.example/"     # domain rule applied
    assert got["X-Everywhere"] == "1"


def test_downloader_without_rules_is_transparent():
    dl = Downloader(config={}, out_dir=".")
    base = {"Range": "bytes=5-"}
    assert dl._request_headers("https://x.com/f", base) is base
    assert dl._request_headers("https://x.com/f", None) == {}


@pytest.mark.parametrize("url,expect_hit", [
    ("https://SUPJAV.com/f", False),   # case-sensitive, like link_providers
    ("https://supjav.com/f", True),
])
def test_match_is_case_sensitive_like_providers(url, expect_hit):
    rules = [{"match": "supjav", "headers": {"Referer": "r"}}]
    assert bool(headers_for_url(url, rules)) == expect_hit
