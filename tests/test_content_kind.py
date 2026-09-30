"""Unit tests for magic-byte content detection and kind-mismatch warnings
(html_guard's post-download validation layer)."""
from __future__ import annotations

import pytest

from idm.core import (
    _KIND_COMPAT,
    Downloader,
    detect_content_kind,
    expected_kind_for_name,
)

MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00"
MKV = b"\x1a\x45\xdf\xa3\x01\x00\x00\x00"
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 8
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
GIF = b"GIF89a" + b"\x00" * 8
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 "
AVI = b"RIFF\x24\x00\x00\x00AVI LIST"
WAV = b"RIFF\x24\x00\x00\x00WAVEfmt "
MP3 = b"ID3\x04\x00\x00\x00\x00\x00\x00"
MP3_RAW = b"\xff\xfb\x90\x44" + b"\x00" * 8
OGG = b"OggS\x00\x02" + b"\x00" * 8
FLAC = b"fLaC\x00\x00\x00" + b"\x00" * 8
PDF = b"%PDF-1.7\n"
ZIP = b"PK\x03\x04\x14\x00\x00\x00"
M3U = b"#EXTM3U\n#EXT-X-TARGETDURATION:10\n"
M3U_BOM = b"\xef\xbb\xbf#EXTM3U\n"
HTML = b"<!DOCTYPE html>\n<html><body>x</body></html>\n"
TEXT = b"1\n00:00:01,000 --> 00:00:02,000\nhi\n"
BINARY = b"\x00\x01\x02\x03\x04\x05\x06\x07"


@pytest.mark.parametrize("head,want", [
    (MP4, "mp4"), (MKV, "matroska"), (JPEG, "jpeg"), (PNG, "png"),
    (GIF, "gif"), (WEBP, "webp"), (AVI, "avi"), (WAV, "wav"),
    (MP3, "mp3"), (MP3_RAW, "mp3"), (OGG, "ogg"), (FLAC, "flac"),
    (PDF, "pdf"), (ZIP, "zip"), (M3U, "m3u"), (M3U_BOM, "m3u"),
    (HTML, "html"), (TEXT, "text"), (BINARY, None),
])
def test_detect_content_kind(head, want):
    assert detect_content_kind(head) == want


def test_detect_never_warns_on_none():
    """The None contract: unknown bytes must not trigger warnings."""
    assert detect_content_kind(BINARY) is None
    assert detect_content_kind(b"") is None


@pytest.mark.parametrize("name,want", [
    ("video.mp4", "mp4"), ("clip.MKV", "matroska"), ("a.webm", "matroska"),
    ("poster.jpg", "jpeg"), ("pic.JPEG", "jpeg"), ("i.png", "png"),
    ("list.m3u8", "m3u"), ("sub.srt", "text"), ("page.html", "html"),
    ("noext", None), ("archive.rar", None),
    # mangled Content-Disposition names: trailing punctuation must not
    # hide the extension the name really promises
    ("130425,_360p.mp4,.mp4,_720p.mp4,", "mp4"),
    ("report.pdf..", "pdf"),
    ("notes.txt, ", "text"),
    ("archive.rar,", None),
    ("noext,", None),
])
def test_expected_kind_for_name(name, want):
    assert expected_kind_for_name(name) == want


def test_compat_pairs():
    assert "text" in _KIND_COMPAT["m3u"]
    assert "text" in _KIND_COMPAT["html"]
    assert "m3u" in _KIND_COMPAT["text"]
    assert "mp4" not in _KIND_COMPAT.get("jpeg", set())


def _dl_with_log():
    logs: list[str] = []
    dl = Downloader(config={}, out_dir=".")
    dl.log_cb = lambda msg, level="info": logs.append((level, str(msg)))
    return dl, logs


def test_warn_method_flags_mislabeled_media(tmp_path):
    p = tmp_path / "video.mp4"
    p.write_bytes(JPEG)
    dl, logs = _dl_with_log()
    note = dl._warn_on_kind_mismatch(p, "https://x/v.mp4")
    assert any("[warn]" in m and "JPEG" in m and "MPEG-4" in m
               for _, m in logs), logs
    assert note and "JPEG" in note and "MPEG-4" in note


def test_warn_method_flags_hls_playlist(tmp_path):
    p = tmp_path / "3007.webm"
    p.write_bytes(M3U)
    dl, logs = _dl_with_log()
    note = dl._warn_on_kind_mismatch(p, "https://x/3007")
    assert any("[warn]" in m and "M3U" in m for _, m in logs), logs
    assert note and "M3U" in note


def test_warn_method_silent_on_match(tmp_path):
    p = tmp_path / "video.mp4"
    p.write_bytes(MP4)
    dl, logs = _dl_with_log()
    assert dl._warn_on_kind_mismatch(p, "https://x/v.mp4") == ""
    assert logs == []


def test_warn_method_silent_on_matroska_family(tmp_path):
    # .webm filename with real MKV-family bytes: same EBML kind, no warn
    p = tmp_path / "clip.webm"
    p.write_bytes(MKV)
    dl, logs = _dl_with_log()
    assert dl._warn_on_kind_mismatch(p, "https://x/clip") == ""
    assert logs == []


def test_warn_method_silent_on_compat(tmp_path):
    # .m3u filename holding plain text: compatible family, no warn
    p = tmp_path / "list.m3u"
    p.write_bytes(TEXT)
    dl, logs = _dl_with_log()
    assert dl._warn_on_kind_mismatch(p, "https://x/list") == ""
    assert logs == []


def test_warn_method_silent_on_unknown_and_unopinionated(tmp_path):
    # unknown binary content: never warned
    p = tmp_path / "video.mp4"
    p.write_bytes(BINARY)
    dl, logs = _dl_with_log()
    assert dl._warn_on_kind_mismatch(p, "https://x/v.mp4") == ""
    assert logs == []
    # extension the table has no opinion on: not even read
    p2 = tmp_path / "thing.rar"
    p2.write_bytes(JPEG)
    dl2, logs2 = _dl_with_log()
    assert dl2._warn_on_kind_mismatch(p2, "https://x/t") == ""
    assert logs2 == []


def test_warn_method_silent_on_empty_file(tmp_path):
    p = tmp_path / "video.mp4"
    p.write_bytes(b"")
    dl, logs = _dl_with_log()
    assert dl._warn_on_kind_mismatch(p, "https://x/v.mp4") == ""
    assert logs == []


def test_task_note_field_defaults_empty():
    t = Downloader(config={}, out_dir=".")
    task = __import__("idm.core", fromlist=["DownloadTask"]).DownloadTask(
        url="https://x/f.mp4")
    assert task.note == ""


def test_static_helper_still_works():
    assert Downloader._head_looks_like_html(HTML)
    assert not Downloader._head_looks_like_html(MP4)
