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
]


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


@pytest.fixture(autouse=True)
def _no_shell_open(monkeypatch):
    monkeypatch.setattr(G, "open_file_safe", lambda p: True)
    monkeypatch.setattr(G, "open_file_with", lambda p, v: True)


def _drive_export(app, tmp_path, monkeypatch, name="h.csv", filters=("", "", "")):
    """Seed history, stub both dialogs, run Save As. Returns the written path."""
    (tmp_path / "subs_history.json").write_text(json.dumps(ROWS), encoding="utf-8")
    out = tmp_path / name
    monkeypatch.setattr(G.filedialog, "asksaveasfilename", lambda **kw: str(out))
    monkeypatch.setattr(G.App, "_open_filter_dialog", lambda self: filters)
    app._save_subs_history_file()
    return out


def test_saveas_logs_preview_matching_file(app, tmp_path, monkeypatch):
    out = _drive_export(app, tmp_path, monkeypatch, "h.csv")
    log = app.log_text.get("1.0", "end")
    assert "preview: " in log
    first3 = " ".join(out.read_text(encoding="utf-8").splitlines()[:3])
    assert first3 in log                     # the preview mirrors the file


def test_saveas_markdown_preview_skips_separator(app, tmp_path, monkeypatch):
    out = _drive_export(app, tmp_path, monkeypatch, "h.md")
    log = app.log_text.get("1.0", "end")
    lines = out.read_text(encoding="utf-8").splitlines()
    expected = " ".join([lines[0], lines[2], lines[3]])   # |---| not a row
    assert expected in log
    assert "---" not in expected


def test_saveas_preview_between_export_and_open(app, tmp_path, monkeypatch):
    _drive_export(app, tmp_path, monkeypatch, "h.csv")
    log_lines = app.log_text.get("1.0", "end").splitlines()
    idx_exp = next(i for i, ln in enumerate(log_lines) if "exported to" in ln)
    idx_prev = next(i for i, ln in enumerate(log_lines) if ln.startswith("preview: "))
    idx_open = next(i for i, ln in enumerate(log_lines) if ln.startswith("opened "))
    assert idx_exp < idx_prev < idx_open


def test_saveas_write_failure_has_no_preview(app, tmp_path, monkeypatch):
    (tmp_path / "subs_history.json").write_text(json.dumps(ROWS), encoding="utf-8")
    out = tmp_path / "blocked.csv"
    monkeypatch.setattr(G.filedialog, "asksaveasfilename", lambda **kw: str(out))
    monkeypatch.setattr(G.App, "_open_filter_dialog", lambda self: ("", "", ""))
    monkeypatch.setattr(G, "write_history_export",
                        lambda *a, **kw: (_ for _ in ()).throw(OSError("disk full")))
    app._save_subs_history_file()
    assert "preview:" not in app.log_text.get("1.0", "end")


def test_saveas_empty_history_header_preview(app, tmp_path, monkeypatch):
    # no history file: the export is header-only, and the header IS the content
    _drive_export(app, tmp_path, monkeypatch, "empty.csv")
    log = app.log_text.get("1.0", "end")
    assert "preview: video,lang,provider,size,cues" in log
