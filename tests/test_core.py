from __future__ import annotations

from idm.config import _deep_merge, get_config
from idm.utils import human_size, parse_job_lines, sanitize_filename


def test_human_size():
    assert human_size(None) == "?"
    assert human_size(0) == "0 B"
    assert human_size(999) == "999 B"
    assert human_size(1500) == "1.5 KB"
    assert human_size(5 * 1000 * 1000).endswith("MB")


def test_sanitize_filename():
    assert sanitize_filename('bad:file/name?.mp4') == "bad_file_name_.mp4"
    assert sanitize_filename("  spaced .mp4 ") == "spaced .mp4"
    assert sanitize_filename("") == "download.bin"
    long = sanitize_filename("x" * 500 + ".mkv")
    assert long.endswith(".mkv") and len(long) <= 180


def test_parse_job_lines():
    text = """
# comment line
https://a.com/f1.mp4
https://a.com/f2.mp4 -> renamed.mp4
   https://a.com/f3.mp4
"""
    jobs = parse_job_lines(text)
    assert jobs == [
        ("https://a.com/f1.mp4", None),
        ("https://a.com/f2.mp4", "renamed.mp4"),
        ("https://a.com/f3.mp4", None),
    ]


def test_deep_merge_and_env(monkeypatch):
    base = {"a": 1, "nested": {"x": 1, "y": 2}}
    merged = _deep_merge(base, {"nested": {"y": 3}, "b": 2})
    assert merged == {"a": 1, "b": 2, "nested": {"x": 1, "y": 3}}

    monkeypatch.setenv("IDM_WORKERS", "9")
    cfg = get_config()
    assert cfg["workers"] == 9
    assert cfg["out_dir"]  # defaults still present
