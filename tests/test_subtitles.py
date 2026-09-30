from __future__ import annotations

import requests

from idm import subtitles as S
from idm.subtitles import (
    _looks_like_srt,
    _provider_names,
    _release_query,
    _sc_parse_detail,
    _sc_parse_search,
    _yys_pick_srt,
    is_video,
    opensubtitles_hash,
    pick_best,
)


def ref_hash(data: bytes) -> str:
    """Independent reference implementation of the OpenSubtitles hash."""
    size = len(data)
    acc = size
    for part in (data[:65536], data[-65536:] if size > 65536 else b""):
        acc += sum(
            int.from_bytes(part[i: i + 8], "little")
            for i in range(0, len(part) // 8 * 8, 8)
        )
    return "%016x" % (acc & 0xFFFFFFFFFFFFFFFF)


def test_hash_small_file(tmp_path):
    data = b"\x00\x01\x02\x03" * 1000
    p = tmp_path / "v.mkv"
    p.write_bytes(data)
    assert opensubtitles_hash(p) == ref_hash(data)


def test_hash_large_file_matches_reference(tmp_path):
    head = bytes(range(256)) * 256          # 64 KiB
    tail = bytes((255 - i) % 256 for i in range(65536))
    data = head + b"x" * (200 * 1024) + tail
    p = tmp_path / "v.mp4"
    p.write_bytes(data)
    assert opensubtitles_hash(p) == ref_hash(data)


def test_is_video():
    assert is_video("movie.MKV") and is_video("x/ep.mp4")
    assert not is_video("notes.txt") and not is_video("image.jpg")


def test_pick_best_prefers_human_high_quality():
    entries = [
        {"attributes": {"ai_translated": True, "ratings": 90, "download_count": 10,
                        "files": [{"file_id": 2}]}},
        {"attributes": {"ratings": 10, "download_count": 500,
                        "files": [{"file_id": 1}]}},
    ]
    picked = pick_best(entries)
    assert picked is not None
    attrs, f = picked
    assert f["file_id"] == 1  # human translation wins over AI-translated


def test_pick_best_empty():
    assert pick_best([]) is None
    assert pick_best([{"attributes": {}}]) is None


# ----------------------------------------------------------------- providers
def test_provider_default_chain():
    assert _provider_names({}) == ["opensubtitles", "yifysubtitles", "subtitlecat"]


def test_provider_order_configurable():
    assert _provider_names({"subtitle_providers": "subtitlecat,opensubtitles"}) \
        == ["subtitlecat", "opensubtitles"]


def test_release_query_strips_release_noise():
    q = _release_query("Movie.Name.2024.1080p.WEB-DL.x264-[YTS.MX].mp4")
    assert "x264" not in q.lower() and "1080p" not in q.lower()
    assert q == "Movie Name 2024 DL"  # WEB-DL leaves 'DL' after cleaning


def test_looks_like_srt():
    assert _looks_like_srt(b"1\n00:00:01,000 --> 00:00:02,000\nhi\n")
    assert _looks_like_srt(b"[Script Info]\nTitle: x\n")
    assert not _looks_like_srt(b"<html><body>blocked</body></html>")
    assert not _looks_like_srt(b"")


def test_sc_parse_search_and_detail():
    search_html = '<a href="/subs/751/Sintel.2010.720p.BluRay-%5BYTS.MX%5D.html">x</a>'
    pages = _sc_parse_search(search_html)
    assert pages and pages[0][1].endswith(".html")
    detail_html = (
        '<span><img alt="en"></span> <span>English</span>'
        '<a href="/subs/756/Sintel-en.srt">Download</a>'
        '<span><img alt="xx"></span> <span>Martian</span>'
        '<a href="/subs/756/Sintel-xx.srt">Download</a>'
    )
    rows = _sc_parse_detail(detail_html)
    assert rows[0][1] == "en"
    assert rows[1][1] == "xx"  # unknown language falls back to flag code


def test_yys_pick_srt_prefers_largest():
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("small.srt", b"1\n00:00:01 --> 00:00:02\ntiny\n")
        zf.writestr("big.srt", b"1\n00:00:01 --> 00:00:02\n" + b"line\n" * 500)
        zf.writestr("readme.txt", b"not a subtitle")
    name, data = _yys_pick_srt(zipfile.ZipFile(io.BytesIO(buf.getvalue())))
    assert name == "big.srt" and b"-->" in data


class ChainRecorder:
    """Stub provider that records calls and can raise or succeed."""

    def __init__(self, behavior):
        self.behavior = behavior
        self.calls = []

    def __call__(self, cfg, langs, moviehash=None, filesize=None, query=None, log=print):
        self.calls.append(query)
        action = self.behavior[len(self.calls) - 1]
        if action == "fail":
            raise S.SubtitleError("simulated unavailability")
        if action == "boom":
            raise requests.ConnectionError("simulated outage")
        return {"data": b"1\n00:00:01,000 --> 00:00:02,000\nhello\n",
                "file_name": "Movie.en.srt", "lang": "en",
                "remaining": None, "provider": action}


def run_chain(monkeypatch, tmp_path, behavior,
              providers="opensubtitles,yifysubtitles,subtitlecat"):
    video = tmp_path / "Movie.mkv"
    video.write_bytes(b"video")
    rec = ChainRecorder(behavior)
    monkeypatch.setattr(S, "_provider_names", lambda cfg: providers.split(","))
    monkeypatch.setattr(S, "_download_from_opensubtitles", rec, raising=False)
    monkeypatch.setattr(S, "_download_from_yifysubtitles", rec, raising=False)
    monkeypatch.setattr(S, "_download_from_subtitlecat", rec, raising=False)
    return video, rec


def test_chain_falls_through_quota_or_down(monkeypatch, tmp_path):
    video, rec = run_chain(monkeypatch, tmp_path,
                           ["fail", "fail", "os"])
    r = S.download_for_video(video, {}, "en")
    assert r["ok"] and r["language"] == "en"
    assert (tmp_path / "Movie.en.srt").exists()
    assert len(rec.calls) == 3


def test_chain_survives_network_outage(monkeypatch, tmp_path):
    video, rec = run_chain(monkeypatch, tmp_path,
                           ["boom", "sc"])
    r = S.download_for_video(video, {}, "en")
    assert r["ok"] and (tmp_path / "Movie.en.srt").exists()
    assert len(rec.calls) == 2


def test_chain_reports_all_failures(monkeypatch, tmp_path):
    video, rec = run_chain(monkeypatch, tmp_path,
                           ["fail", "fail", "fail"])
    r = S.download_for_video(video, {}, "en")
    assert not r["ok"]
    assert "all providers failed" in r["message"]
    assert len(rec.calls) == 3


def test_chain_opensubtitles_used_first_when_keyed(monkeypatch, tmp_path):
    video, rec = run_chain(monkeypatch, tmp_path,
                           ["os"])
    r = S.download_for_video(video, {"opensubtitles_api_key": "k"}, "en")
    assert r["ok"]
    assert len(rec.calls) == 1


def test_save_subtitle_uses_provider_lang(tmp_path):
    video = tmp_path / "Movie.mkv"
    dest = S.save_subtitle(video, "whatever.en.srt", b"data", "vi")
    assert dest.name == "Movie.vi.srt"


# ------------------------------------------ SubtitleCat largest-.srt selection
def test_sc_pick_largest_prefers_requested_language_and_biggest_file():
    rows = [
        ("English", "en", "url-small-en"),
        ("English", "en", "url-big-en"),
        ("Vietnamese", "vi", "url-vi"),
    ]
    sizes = {"url-small-en": 100, "url-big-en": 9000, "url-vi": 5000}

    def fetch(url):
        return b"1\n00:00:01,000 --> 00:00:02,000\n" + b"x" * sizes[url]

    name, code, url, data = S._sc_pick_largest(rows, ["en"], fetch=fetch)
    assert code == "en"
    assert url == "url-big-en"            # biggest valid .srt of the language
    assert b"x" * 9000 in data


def test_sc_pick_largest_falls_back_to_other_language():
    rows = [("French", "fr", "url-fr")]
    _name, code, _url, data = S._sc_pick_largest(
        rows, ["en"], fetch=lambda u: b"1\n00:00:01,000 --> 00:00:02,000\nhi\n")
    assert code == "fr" and b"-->" in data


def test_sc_pick_largest_skips_errors_and_invalid_payloads():
    rows = [("English", "en", "bad"), ("English", "en", "html"), ("English", "en", "good")]

    def fetch(url):
        if url == "bad":
            raise requests.RequestException("boom")
        if url == "html":
            return b"<html><body>blocked</body></html>"
        return b"1\n00:00:01,000 --> 00:00:02,000\nok\n"

    picked = S._sc_pick_largest(rows, ["en"], fetch=fetch)
    assert picked is not None and picked[2] == "good"


def test_sc_pick_largest_returns_none_when_nothing_valid():
    assert S._sc_pick_largest(
        [("English", "en", "u")], ["en"], fetch=lambda u: b"<html>") is None


def test_sc_pick_largest_ties_choose_first():
    rows = [("English", "en", "a"), ("English", "en", "b")]
    body = b"1\n00:00:01,000 --> 00:00:02,000\nsame\n"
    _n, _c, url, _d = S._sc_pick_largest(rows, ["en"], fetch=lambda u: body)
    assert url == "a"


# --------------------------------------------------------- cue-count validator
def test_count_cues_parses_srt():
    body = (b"1\n00:00:01,000 --> 00:00:02,000\nhi\n\n"
            b"2\n00:00:03,500 --> 00:00:04,250\nthere\n")
    assert S.count_cues(body) == 2


def test_count_cues_parses_vtt_and_ass():
    vtt = b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nhi\n"
    assert S.count_cues(vtt) == 1
    ass = b"[Events]\nDialogue: 0,0:00:01.00,0:00:02.50,Default,,0,0,0,,hello\n"
    assert S.count_cues(ass) == 1


def test_count_cues_ignores_reversed_or_broken_timings():
    bad = b"1\n00:00:05,000 --> 00:00:02,000\nend before start\n"
    assert S.count_cues(bad) == 0
    assert S.count_cues(b"no timings here at all") == 0
    assert S.count_cues(b"") == 0


def test_count_cues_counts_only_wellformed_among_watermarks():
    body = (b"0\n00:00:01,000 --> 00:00:04,000\nDownloaded From site\n\n"
            b"1\n00:00:55,995 --> 00:00:57,804\nreal\n")
    assert S.count_cues(body) == 2


def test_pick_largest_rejects_big_cueless_file():
    # 9 KB that sniffs like a subtitle (contains '-->') but has NO valid cue
    junk = b"random --> arrows --> without\nany\ntiming\nstructure\n" * 300
    rows = [("English", "en", "big-junk"), ("English", "en", "small-good")]

    def fetch(url):
        if url == "big-junk":
            return junk
        return b"1\n00:00:01,000 --> 00:00:02,000\nok\n"

    _n, _c, url, data = S._sc_pick_largest(rows, ["en"], fetch=fetch)
    assert url == "small-good"          # smaller but actually valid, wins
    assert S.count_cues(data) == 1


def test_chain_falls_through_when_provider_returns_cueless(monkeypatch, tmp_path):
    # first provider (subtitlecat in the default chain) returns a payload with
    # no valid cues; the chain must treat it as unavailable and try the next
    video, rec = run_chain(monkeypatch, tmp_path, ["junk", "sc"],
                           providers="subtitlecat,yifysubtitles")

    def junk_result(cfg, langs, moviehash=None, filesize=None, query=None, log=print):
        return {"data": b"<html>looks like nothing</html>", "file_name": "x.srt",
                "lang": "en", "remaining": None, "provider": "junk"}

    monkeypatch.setattr(S, "_download_from_subtitlecat", junk_result, raising=False)
    r = S.download_for_video(video, {}, "en")
    assert r["ok"] and r["language"] == "en"
    assert (tmp_path / "Movie.en.srt").exists()
    assert len(rec.calls) == 1          # the real (second) provider served the result
