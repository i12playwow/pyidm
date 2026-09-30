"""_unmangle_cd_name: cleaning multi-variant Content-Disposition filenames
(e.g. '130425,_360p.mp4,.mp4,_720p.mp4,') to a single-extension name —
plus the end-to-end download behavior through _pick_filename's real
header parser."""
from __future__ import annotations

import pytest

from idm.core import Downloader, _unmangle_cd_name, expected_kind_for_name


@pytest.mark.parametrize("raw,want", [
    # the real-world shape that littered the downloads folder
    ("130425,_360p.mp4,.mp4,_720p.mp4,", "130425,_360p.mp4"),
    ("149953,_360p.mp4,.mp4,_720p.mp4,", "149953,_360p.mp4"),
    # same variant repeated
    ("video.mp4,video.mp4", "video.mp4"),
    # already clean names pass through untouched
    ("movie.mp4", "movie.mp4"),
    ("Movie.2024.1080p.mkv", "Movie.2024.1080p.mkv"),
    ("archive.rar", "archive.rar"),
    ("no_ext_file", "no_ext_file"),
    # an honest double extension must survive (last part unknown to the table)
    ("mytrip.webm.notes", "mytrip.webm.notes"),
    ("recording.mp4.txt", "recording.mp4.txt"),
    # case-insensitive match, original case kept
    ("CLIP.MP4,CLIP.MP4", "CLIP.MP4"),
])
def test_unmangle_cd_name(raw, want):
    assert _unmangle_cd_name(raw) == want


def test_unmangled_names_are_kind_checkable():
    """The point of the cleanup: the result has a normal extension, so the
    magic-byte check has an opinion on it (the old mangled name had none)."""
    mangled = "130425,_360p.mp4,.mp4,_720p.mp4,"
    assert expected_kind_for_name(mangled) == "mp4"   # trailing-punct fix
    cleaned = _unmangle_cd_name(mangled)
    assert cleaned.endswith(".mp4")
    assert expected_kind_for_name(cleaned) == "mp4"


class _Resp:
    """Just enough of a requests.Response for _pick_filename."""

    def __init__(self, cd="", url="http://x/a/b.mp4"):
        self.headers = {"Content-Disposition": cd} if cd else {}
        self.url = url


@pytest.mark.parametrize("cd,want", [
    # filename= header with the mangled multi-variant payload
    ('attachment; filename="130425,_360p.mp4,.mp4,_720p.mp4,"',
     "130425,_360p.mp4"),
    # filename*= UTF-8 variant, mangled too
    ("attachment; filename*=UTF-8''video.mp4%2Cvideo.mp4",
     "video.mp4"),
    # honest names are unchanged end to end
    ('attachment; filename="Movie 2024.1080p.mkv"', "Movie 2024.1080p.mkv"),
    ('attachment; filename="clip.webm"', "clip.webm"),
    # no CD header at all: URL fallback still gets cleaned
    ("", "clip.mp4"),
])
def test_pick_filename_unmangles(cd, want, monkeypatch):
    url = "http://x/v/clip.mp4" if not cd else "http://x/v/anything"
    dl = Downloader(config={}, out_dir=".")
    assert dl._pick_filename(_Resp(cd, url), url, None) == want


def test_pick_filename_clean_url_path_untouched():
    dl = Downloader(config={}, out_dir=".")
    assert dl._pick_filename(
        _Resp(url="http://x/v/movie.mkv"), "http://x/v/movie.mkv", None) == "movie.mkv"
