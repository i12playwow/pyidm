from __future__ import annotations

import json
import time

import pytest

gui = pytest.importorskip("idm.gui")
from idm import gui as G


def row(ts=None, **kw):
    r = {"path": "C:/v/A.mkv", "ok": True, "dest": "C:/v/A.en.srt",
         "language": "en", "provider": "subtitlecat", "size": 1000, "cues": 50}
    if ts is not None:
        r["ts"] = ts
    r.update(kw)
    return r


# ------------------------------------------------------------ pure helper
def test_prune_disabled_by_default():
    hist = [row(ts=1.0), row()]          # unstamped legacy row included
    assert G.prune_history(hist, 0) == hist
    assert G.prune_history(hist, -3) == hist
    assert G.prune_history(None, 30) == []


def test_prune_drops_only_older_than_cutoff():
    now = time.time()
    hist = [row(ts=now - 10 * 86400),   # 10d old -> dropped at 7d
            row(ts=now - 7 * 86400),    # exactly at cutoff -> kept (>=)
            row(ts=now - 60),           # fresh -> kept
            row()]                      # no stamp -> kept
    out = G.prune_history(hist, 7, now=now)
    assert out == hist[1:]
    assert G.prune_history(hist, 7, now=now)[0]["ts"] == now - 7 * 86400


def test_prune_fractional_days():
    now = time.time()
    hist = [row(ts=now - 3600)]         # 1h old
    assert G.prune_history(hist, 0.5, now=now) == hist    # 12h cutoff keeps 1h
    assert G.prune_history(hist, 0.0001, now=now) == []   # ~9s cutoff drops it


def test_prune_does_not_mutate_input():
    now = time.time()
    hist = [row(ts=now - 100 * 86400)]
    out = G.prune_history(hist, 30, now=now)
    assert out == [] and hist == [row(ts=now - 100 * 86400)]


# ------------------------------------------------- config-driven save path
@pytest.fixture
def hist_file(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "SUBS_HISTORY_FILE", tmp_path / "h.json")
    return tmp_path / "h.json"


def test_save_uses_configured_max_age(hist_file, monkeypatch):
    now = time.time()
    monkeypatch.setattr(G.time, "time", lambda: now)
    p = hist_file
    p.write_text(json.dumps([row(ts=now - 100 * 86400),
                             row(ts=now - 60)]), encoding="utf-8")
    monkeypatch.setattr(G, "get_config",
                        lambda: {"subtitle_history_max_age_days": 30})
    out = G.save_subs_history([row()], path=p)
    assert [r.get("size") for r in out] == [1000, 1000]   # old dropped, fresh kept + new


def test_save_keeps_everything_when_disabled(hist_file, monkeypatch):
    now = time.time()
    monkeypatch.setattr(G.time, "time", lambda: now)
    p = hist_file
    p.write_text(json.dumps([row(ts=1.0)]), encoding="utf-8")
    monkeypatch.setattr(G, "get_config", dict)       # key absent -> disabled
    out = G.save_subs_history([row()], path=p)
    assert len(out) == 2


def test_save_invalid_config_value_disables_prune(hist_file, monkeypatch):
    p = hist_file
    p.write_text(json.dumps([row(ts=1.0)]), encoding="utf-8")
    monkeypatch.setattr(G, "get_config",
                        lambda: {"subtitle_history_max_age_days": "soon"})
    out = G.save_subs_history([row()], path=p)
    assert len(out) == 2
    assert G._history_max_age_days() == 0.0


def test_save_sets_last_prune_dropped(hist_file, monkeypatch):
    now = time.time()
    monkeypatch.setattr(G.time, "time", lambda: now)
    p = hist_file
    p.write_text(json.dumps([row(ts=now - 100 * 86400)]), encoding="utf-8")
    monkeypatch.setattr(G, "get_config",
                        lambda: {"subtitle_history_max_age_days": 30})
    G.save_subs_history([row()], path=p)
    assert G.LAST_PRUNE_DROPPED == 1
    G.save_subs_history([row()], path=p)                   # nothing left to drop
    assert G.LAST_PRUNE_DROPPED == 0


# --------------------------------------------------------- real-App startup
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


def test_startup_prunes_and_logs(app, tmp_path, monkeypatch):
    now = time.time()
    p = tmp_path / "subs_history.json"
    p.write_text(json.dumps([row(ts=now - 100 * 86400), row(ts=now - 60)]),
                 encoding="utf-8")
    monkeypatch.setattr(G, "get_config",
                        lambda: {"subtitle_history_max_age_days": 30})
    app._restore_subs_history()
    assert len(app.subs_tree.get_children()) == 1
    assert "auto-pruned 1 entry" in app.log_text.get("1.0", "end")


def test_startup_no_prune_no_log(app, tmp_path, monkeypatch):
    p = tmp_path / "subs_history.json"
    p.write_text(json.dumps([row(ts=time.time() - 60)]), encoding="utf-8")
    monkeypatch.setattr(G, "get_config",
                        lambda: {"subtitle_history_max_age_days": 30})
    app._restore_subs_history()
    assert len(app.subs_tree.get_children()) == 1
    assert "auto-pruned" not in app.log_text.get("1.0", "end")


def test_startup_disabled_keeps_old_entries(app, tmp_path, monkeypatch):
    p = tmp_path / "subs_history.json"
    p.write_text(json.dumps([row(ts=1.0), row(ts=time.time())]), encoding="utf-8")
    monkeypatch.setattr(G, "get_config", dict)
    app._restore_subs_history()
    assert len(app.subs_tree.get_children()) == 2
    assert "auto-pruned" not in app.log_text.get("1.0", "end")


def test_pruned_file_is_persisted_on_startup(app, tmp_path, monkeypatch):
    """Startup prune must write back, not just trim the view."""
    now = time.time()
    p = tmp_path / "subs_history.json"
    p.write_text(json.dumps([row(ts=now - 100 * 86400), row(ts=now - 60)]),
                 encoding="utf-8")
    monkeypatch.setattr(G, "get_config",
                        lambda: {"subtitle_history_max_age_days": 30})
    app._restore_subs_history()
    on_disk = json.loads(p.read_text(encoding="utf-8"))
    assert len(on_disk) == 1 and on_disk[0]["ts"] == pytest.approx(now - 60, abs=5)
