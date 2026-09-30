from __future__ import annotations

import json
import time

import pytest

gui = pytest.importorskip("idm.gui")
from idm import gui as G

ROWS = [
    {"path": "C:/v/A.mkv", "ok": True, "dest": "C:/v/A.en.srt",
     "language": "en", "provider": "subtitlecat", "size": 135_480, "cues": 1746},
    {"path": "C:/v/B.mp4", "ok": True, "dest": "C:/v/B.en.srt",
     "language": "en", "provider": "opensubtitles", "size": 51_200, "cues": 800},
    {"path": "C:/v/F.mkv", "ok": False, "message": "nope"},
]


# ------------------------------------------------------------ pure helpers
def test_format_age_buckets():
    assert G.format_age(0) == "just now"
    assert G.format_age(59) == "just now"
    assert G.format_age(60) == "1m"
    assert G.format_age(5 * 60) == "5m"
    assert G.format_age(3600) == "1h"
    assert G.format_age(47 * 3600) == "47h"
    assert G.format_age(48 * 3600) == "2d"
    assert G.format_age(12 * 86400) == "12d"
    assert G.format_age(-5) == "just now"          # clock skew clamped


def test_history_status_empty():
    assert G.history_status([]) == "0 entries"
    assert G.history_status(None) == "0 entries"


def test_history_status_with_stamps():
    now = time.time()
    hist = [dict(ROWS[0], ts=now - 300), dict(ROWS[1], ts=now - 60)]
    out = G.history_status(hist)
    assert out == ("2 entries — 186.7 KB — opensubtitles 1, subtitlecat 1"
                   " — oldest from 5m ago")


def test_history_total_bytes():
    assert G.history_total_bytes([ROWS[0], ROWS[1]]) == 186_680
    assert G.history_total_bytes([ROWS[2]]) == 0                    # failed excluded
    assert G.history_total_bytes([{"ok": True, "path": "x"}]) == 0  # no size
    assert G.history_total_bytes([{"ok": True, "size": True}]) == 0 # bool isn't a number
    assert G.history_total_bytes(None) == 0
    assert G.history_total_bytes([{"ok": True, "size": 100}, ROWS[0]]) == 135_580


def test_provider_counts():
    # tie -> alphabetical, deterministic
    assert G.provider_counts([ROWS[0], ROWS[1]]) == [("opensubtitles", 1),
                                                     ("subtitlecat", 1)]
    # most-used first
    assert G.provider_counts([ROWS[0], ROWS[0], ROWS[1]]) == [("subtitlecat", 2),
                                                              ("opensubtitles", 1)]
    assert G.provider_counts([ROWS[2], {"ok": True, "path": "x"}]) == []
    assert G.provider_counts(None) == []


def test_history_status_segments_omitted_when_unknown():
    assert G.history_status([{"ok": True, "path": "x"}]) == "1 entries"
    assert G.history_status([{"ok": True, "path": "x", "ts": time.time()}]) == \
        "1 entries — oldest from just now ago"


def test_history_status_ignores_failures_and_unstamped():
    out = G.history_status([dict(ROWS[0], ts=time.time())] + [ROWS[2]])
    assert out == ("1 entries — 135.5 KB — subtitlecat 1"
                   " — oldest from just now ago")   # failed row not counted
    assert G.history_status([ROWS[0], ROWS[1]]) == \
        "2 entries — 186.7 KB — opensubtitles 1, subtitlecat 1"  # no stamps


def test_history_status_uses_oldest_stamp():
    now = time.time()
    hist = [dict(ROWS[0], ts=now - 100), dict(ROWS[1], ts=now - 5 * 86400)]
    assert G.history_status(hist).endswith("oldest from 5d ago")


# ------------------------------------------------------------- stamping
def test_save_subs_history_stamps_and_does_not_mutate_caller(tmp_path):
    p = tmp_path / "h.json"
    r = dict(ROWS[0])
    hist = G.save_subs_history([r], path=p)
    assert isinstance(hist[0]["ts"], float)
    assert "ts" not in r                            # caller untouched
    again = G.load_subs_history(p)
    assert again[0]["ts"] == hist[0]["ts"]


def test_save_preserves_existing_stamps(tmp_path):
    p = tmp_path / "h.json"
    old = dict(ROWS[0], ts=123.0)
    hist = G.save_subs_history([old], path=p)
    assert hist[0]["ts"] == 123.0


# --------------------------------------------------- real-App flow (headless)
def _make_app_with_retry(retries: int = 3):
    last = None
    for _ in range(retries):
        try:
            return G.App()
        except gui.tk.TclError as e:  # pragma: no cover - environmental flake
            last = e
            time.sleep(1.0)
    raise last


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setattr(G, "should_show_first_run", lambda cfg: False)
    monkeypatch.setattr(G, "SUBS_HISTORY_FILE", tmp_path / "subs_history.json")
    a = _make_app_with_retry()
    a.update()
    yield a
    a.destroy()


def test_status_label_after_batch(app):
    app._on_subs_done(2, 3, [(r["path"], r["dest"]) for r in ROWS if r["ok"]], ROWS, {})
    txt = app.subs_status_var.get()
    assert txt.startswith("2 entries") and "oldest from just now ago" in txt


def test_status_label_empty_on_start(app):
    assert app.subs_status_var.get() == "0 entries"


def test_status_updates_on_clear(app, monkeypatch):
    monkeypatch.setattr(G.messagebox, "askyesno", lambda *a, **k: True)
    app._on_subs_done(2, 2, [(r["path"], r["dest"]) for r in ROWS[:2]], ROWS[:2], {})
    assert app.subs_status_var.get().startswith("2 entries")
    app.clear_subs_history()
    assert app.subs_status_var.get() == "0 entries"
    assert app.subs_tree.get_children() == ()


def test_status_shows_age_after_restore(app, tmp_path):
    p = tmp_path / "subs_history.json"
    p.write_text(json.dumps([dict(ROWS[0], ts=time.time() - 6 * 86400),
                             dict(ROWS[1], ts=time.time() - 90)]), encoding="utf-8")
    app._restore_subs_history()
    assert app.subs_status_var.get() == ("2 entries — 186.7 KB —"
                                         " opensubtitles 1, subtitlecat 1"
                                         " — oldest from 6d ago")
