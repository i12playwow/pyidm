"""html_guard unit tests: the head-sniffing helpers in idm.core."""
from __future__ import annotations

import pytest

from idm.core import (
    HTML_GUARD_HEAD_BYTES,
    Downloader,
    _looks_like_m3u,
)

M3U = b"#EXTM3U\n#EXT-X-TARGETDURATION:10\n"
HTML = b"<html><body>blocked</body></html>"


def test_html_sigs_detected():
    for sig in (
        b"<!DOCTYPE html>\n<html>",
        b"<!doctype HTML><html lang=en>",
        b"<html><body>hello</body></html>",
        b"<head><title>Blocked</title></head>",
        b"<body onload=x>",
        b"<title>Error</title>",
        b"<script>location.reload()</script>",
        b"<iframe src=\"about:blank\"></iframe>",
        b"<!-- comment -->",
        b"\xef\xbb\xbf<!DOCTYPE html>",  # UTF-8 BOM prefix
        b"   \n\n  <!DOCTYPE html>",      # leading whitespace
    ):
        assert Downloader._head_looks_like_html(sig), sig


def test_media_bodies_not_flagged():
    for body in (
        b"\xff\xd8\xff\xe0" + b"\x00" * 64,        # JPEG
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 64,       # PNG
        b"GIF89a" + b"\x00" * 64,                  # GIF
        b"\x00\x00\x00\x18ftypmp42",               # MP4
        b"\x1a\x45\xdf\xa3" + b"\x00" * 64,        # MKV/WebM
        b"RIFF\x00\x00\x00\x00",                   # RIFF container
        b"PK\x03\x04",                             # zip-like
        b"\x00" * 64,                              # zeros
        b"random binary \x01\x02\x03 garbage",     # arbitrary bytes
    ):
        assert not Downloader._head_looks_like_html(body), body


def test_guard_head_window_bounded():
    """Signatures beyond the sniff window are not checked."""
    filler = b"\x00" * (HTML_GUARD_HEAD_BYTES + 16)
    assert not Downloader._head_looks_like_html(filler + b"<html>")
    assert Downloader._head_looks_like_html(filler[:HTML_GUARD_HEAD_BYTES - 6] + b"<html>")


def test_disabled_by_config():
    cfg = {"html_guard": False}
    dl = Downloader(config=cfg, out_dir=".")
    assert dl.html_guard is False


def test_default_enabled():
    dl = Downloader(config={}, out_dir=".")
    assert dl.html_guard is True


@pytest.mark.parametrize("sig", [b"<html", b"<!doctype html", b"<script"])
def test_signature_list_covers_common_tags(sig):
    from idm.core import _HTML_SIGS
    assert any(s.startswith(sig) for s in _HTML_SIGS)


# --------------------------------------------------------- playlist_guard
def test_m3u_signatures_detected():
    for body in (
        M3U,
        b"#EXTM3U",
        b"#EXT-X-STREAM-INF:BANDWIDTH=1280000\nsegment_1.ts\n",  # EXT-X only
        b"\xef\xbb\xbf#EXTM3U\n",              # UTF-8 BOM prefix
        b"   \n# a comment first\n#EXTM3U\n",  # leading whitespace/comment
    ):
        assert _looks_like_m3u(body), body


def test_media_and_plain_text_not_flagged_as_m3u():
    for body in (
        b"\x00\x00\x00\x18ftypmp42",               # MP4
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 20,      # PNG
        b"# just a comment\nplain notes, no HLS tags\n",  # text without tags
        b"\x00" * 30,                              # zeros
        b"random binary \x01\x02\x03 garbage",     # arbitrary bytes
    ):
        assert not _looks_like_m3u(body), body


def test_m3u_window_bounded():
    """Signatures beyond the sniff window are not checked (same bound as
    the HTML guard)."""
    filler = b"\x00" * (HTML_GUARD_HEAD_BYTES + 16)
    assert not _looks_like_m3u(filler + b"#EXTM3U")
    assert _looks_like_m3u(filler[:HTML_GUARD_HEAD_BYTES - 7] + b"#EXTM3U")


def test_guarded_covers_html_and_playlist():
    dl = Downloader(config={}, out_dir=".")
    assert dl.playlist_guard is True
    assert dl._head_looks_like_guarded(HTML)
    assert dl._head_looks_like_guarded(M3U)
    assert not dl._head_looks_like_guarded(b"\x00\x00\x00\x18ftypmp42")


def test_playlist_guard_disabled_by_config():
    dl = Downloader(config={"playlist_guard": False}, out_dir=".")
    assert dl.playlist_guard is False
    assert dl._head_looks_like_guarded(HTML)   # html_guard still on
    assert not dl._head_looks_like_guarded(M3U)  # playlist now passes


def test_guard_message_matches_what_was_seen():
    dl = Downloader(config={}, out_dir=".")
    assert "playlist" in dl._guard_message(M3U).lower()
    assert "web page" in dl._guard_message(HTML)
    # a body with both signatures reads as HTML (html first, same as detection)
    both = b"#EXTM3U\n" + HTML
    assert "web page" in dl._guard_message(both)
