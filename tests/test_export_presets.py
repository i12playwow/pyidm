"""Named export presets: filter+query combinations saved from the GUI export
dialogs and reusable from the CLI ('idm presets', '--preset' on history and
downloads). Both surfaces share the same store in export_prefs.json, so a
preset saved anywhere works everywhere."""
from __future__ import annotations

import json
import time

import pytest

gui = pytest.importorskip("idm.gui")
from idm import gui as G
from idm.cli import main as cli_main

TS = 1_800_000_000  # 2027-01-15, safely inside 2026-01-01.. windows


@pytest.fixture()
def prefs(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "EXPORT_PREFS_FILE", tmp_path / "export_prefs.json")
    return tmp_path


def _out(capsys):
    return capsys.readouterr().out


# ------------------------------------------------------------ the store
def test_save_load_delete_roundtrip(prefs):
    assert G.save_preset("downloads", "errors", fmt="json",
                         query="[].filename") is True
    assert G.load_presets("downloads") == {
        "errors": {"fmt": "json", "query": "[].filename"}}
    # overwrite keeps a single entry under the same name
    G.save_preset("downloads", "errors", fmt="csv")
    assert G.load_presets("downloads")["errors"] == {"fmt": "csv"}
    assert G.delete_preset("downloads", "errors") is True
    assert G.delete_preset("downloads", "errors") is False   # already gone
    assert G.load_presets("downloads") == {}


def test_save_preset_rejects_blank_names_and_bad_kinds(prefs):
    assert G.save_preset("downloads", "   ") is False
    assert G.save_preset("bogus", "x", fmt="csv") is False
    assert G.load_export_prefs() == {}                # nothing written
    assert G.delete_preset("nope", "x") is False


def test_preset_store_survives_mangled_file(prefs):
    G.save_preset("history", "ok", provider="subtitlecat")
    raw = json.loads(G.EXPORT_PREFS_FILE.read_text(encoding="utf-8"))
    raw["presets"]["history"]["junk"] = "not-a-dict"
    raw["presets"]["downloads"] = "not-a-dict"
    G.EXPORT_PREFS_FILE.write_text(json.dumps(raw), encoding="utf-8")
    assert G.load_presets("history") == {"ok": {"provider": "subtitlecat"}}
    assert G.load_presets("downloads") == {}
    # a broken presets doc degrades to empty, not a crash
    G.EXPORT_PREFS_FILE.write_text('{"presets": "junk"}', encoding="utf-8")
    assert G.load_presets("history") == {}


def test_preset_settings_are_whitelisted_per_kind(prefs):
    G.save_preset("history", "h", provider="p", since="2026-01-01",
                  until="2026-02-01", query="[]", bogus="dropped")
    assert set(G.load_presets("history")["h"]) == {
        "provider", "since", "until", "query"}
    G.save_preset("downloads", "d", fmt="csv", query="x", provider="ignored")
    assert set(G.load_presets("downloads")["d"]) == {"fmt", "query"}


def test_delete_preset_prunes_empty_slots(prefs):
    G.save_preset("history", "h", provider="p")
    G.save_preset("downloads", "d", fmt="csv")
    G.delete_preset("history", "h")
    raw = json.loads(G.EXPORT_PREFS_FILE.read_text(encoding="utf-8"))
    assert "history" not in raw["presets"] and "downloads" in raw["presets"]
    G.delete_preset("downloads", "d")
    raw = json.loads(G.EXPORT_PREFS_FILE.read_text(encoding="utf-8"))
    assert "presets" not in raw


# ------------------------------------------------------- the CLI surface
def test_presets_list_json_and_query(prefs, capsys):
    G.save_preset("downloads", "errs", fmt="json", query="[].filename")
    G.save_preset("history", "recent", provider="subtitlecat")
    assert cli_main(["presets", "list", "--json"]) == 0
    doc = json.loads(_out(capsys))
    assert doc["downloads"]["errs"]["fmt"] == "json"
    assert doc["history"]["recent"]["provider"] == "subtitlecat"
    assert cli_main(["presets", "list", "--kind", "history", "--json"]) == 0
    assert set(json.loads(_out(capsys))) == {"history"}
    assert cli_main(["presets", "list", "--query",
                     ".downloads | keys | first", "-r"]) == 0
    assert _out(capsys).strip() == "errs"


def test_presets_list_text_and_empty_hint(prefs, capsys):
    assert cli_main(["presets", "list"]) == 0
    assert "no presets saved" in _out(capsys)
    G.save_preset("downloads", "errs", fmt="json")
    assert cli_main(["presets", "list"]) == 0
    text = _out(capsys)
    assert "downloads/errs" in text and "fmt=json" in text
    assert "idm downloads --preset NAME" in text


def test_presets_add_remove_via_cli(prefs, capsys):
    assert cli_main(["presets", "add", "dl", "--kind", "downloads",
                     "--set", "fmt=csv", "--set", "query=[].filename"]) == 0
    assert G.load_presets("downloads")["dl"]["query"] == "[].filename"
    assert cli_main(["presets", "remove", "dl", "--kind", "downloads"]) == 0
    assert "dl" not in G.load_presets("downloads")
    # errors: unknown setting key, unknown preset on remove
    assert cli_main(["presets", "add", "x", "--kind", "downloads",
                     "--set", "bogus=1"]) == 1
    assert "unknown setting" in _out(capsys)
    assert cli_main(["presets", "remove", "ghost", "--kind", "downloads"]) == 1
    assert "unknown preset" in _out(capsys)


def test_downloads_preset_applies_fmt_and_query(prefs, capsys, tmp_path):
    G.save_preset("downloads", "errs", fmt="json",
                  query='[.[] | select(.status == "error")] | length')
    # -o IS the download directory: the state file lives right inside it
    (tmp_path / "idm.state.json").write_text(json.dumps({"version": 1, "downloads": {
        "https://x/a.zip": {"status": "error", "filename": "a.zip", "size": 1,
                            "updated": TS},
        "https://x/b.bin": {"status": "done", "filename": "b.bin", "size": 2,
                            "updated": TS}}}), encoding="utf-8")
    assert cli_main(["-o", str(tmp_path), "downloads", "--preset", "errs"]) == 0
    assert _out(capsys).strip() == "1"          # query ran: one error row
    # unknown preset -> clean error, exit 1
    assert cli_main(["-o", str(tmp_path), "downloads", "--preset", "ghost"]) == 1
    assert "unknown preset" in _out(capsys)


def test_downloads_preset_explicit_flags_win(prefs, capsys, tmp_path):
    G.save_preset("downloads", "errs", fmt="json", query="[].filename")
    (tmp_path / "idm.state.json").write_text(
        json.dumps({"version": 1, "downloads": {}}), encoding="utf-8")
    # explicit --query overrides the preset's query
    assert cli_main(["-o", str(tmp_path), "downloads", "--preset", "errs",
                     "--query", "length"]) == 0
    assert _out(capsys).strip() == "0"


def test_history_preset_applies_filters_and_query(prefs, capsys, tmp_path,
                                                  monkeypatch):
    monkeypatch.setattr(G, "SUBS_HISTORY_FILE", tmp_path / "subs.json")
    G.save_preset("history", "recent", provider="subtitlecat",
                  since="2026-01-01", until="", query="[].provider")
    (tmp_path / "subs.json").write_text(json.dumps([
        {"path": "a.mkv", "ok": True, "dest": "a.srt", "language": "en",
         "provider": "subtitlecat", "size": 10, "cues": 5, "ts": TS},
        {"path": "b.mp4", "ok": True, "dest": "b.srt", "language": "en",
         "provider": "opensubtitles", "size": 10, "cues": 5, "ts": TS}]),
        encoding="utf-8")
    assert cli_main(["history", "--preset", "recent", "-r"]) == 0
    assert _out(capsys).split() == ["subtitlecat"]
    # explicit --provider wins over the preset
    assert cli_main(["history", "--preset", "recent", "--provider",
                     "opensubtitles", "-r"]) == 0
    assert _out(capsys).split() == ["opensubtitles"]
    assert cli_main(["history", "--preset", "ghost"]) == 1
    assert "unknown preset" in _out(capsys)


def test_history_preset_with_out_writes_file(prefs, capsys, tmp_path,
                                             monkeypatch):
    monkeypatch.setattr(G, "SUBS_HISTORY_FILE", tmp_path / "subs.json")
    G.save_preset("history", "all", provider="", since="", until="", query="")
    (tmp_path / "subs.json").write_text(json.dumps([
        {"path": "a.mkv", "ok": True, "dest": "a.srt", "language": "en",
         "provider": "subtitlecat", "size": 10, "cues": 5, "ts": TS}]),
        encoding="utf-8")
    out = tmp_path / "hist.csv"
    assert cli_main(["history", "--preset", "all", "--out", str(out),
                     "--quiet"]) == 0
    assert "a.mkv" in out.read_text(encoding="utf-8")


# ------------------------------------------- presets pin --out and viewer
def _seed_state(tmp_path):
    # default out_dir is ./downloads (no -o passed in these tests)
    dl = tmp_path / "downloads"
    dl.mkdir(exist_ok=True)
    (dl / "idm.state.json").write_text(json.dumps(
        {"version": 1, "downloads": {
            "https://x/a.zip": {"status": "error", "filename": "a.zip",
                                "size": 1, "updated": TS},
            "https://x/b.bin": {"status": "done", "filename": "b.bin",
                                "size": 2, "updated": TS}}}),
        encoding="utf-8")


def test_downloads_preset_pins_out_and_viewer(prefs, capsys, tmp_path,
                                              monkeypatch):
    monkeypatch.chdir(tmp_path)        # pinned 'out' is relative to the cwd
    _seed_state(tmp_path)
    G.save_preset("downloads", "report",
                  query='[.[] | select(.status == "error")] | length',
                  out="report.json", viewer="some-app")
    assert cli_main(["downloads", "--preset", "report", "--quiet"]) == 0
    text = _out(capsys)
    assert "exported 1 row(s) -> report.json" in text
    assert "preset report" in text
    # the pinned path holds the query result, like 'idm downloads --query .'
    assert json.loads((tmp_path / "report.json").read_text(
        encoding="utf-8")) == 1
    # an explicit --export overrides the pinned path
    assert cli_main(["downloads", "--preset", "report",
                     "--export", "custom.json", "--quiet"]) == 0
    assert json.loads((tmp_path / "custom.json").read_text(
        encoding="utf-8")) == 1


def test_downloads_export_json_extension_and_query(prefs, capsys, tmp_path,
                                                   monkeypatch):
    monkeypatch.chdir(tmp_path)
    # this test passes -o tmp_path: the state file sits right in tmp_path
    (tmp_path / "idm.state.json").write_text(json.dumps(
        {"version": 1, "downloads": {
            "https://x/a.zip": {"status": "error", "filename": "a.zip",
                                "size": 1, "updated": TS},
            "https://x/b.bin": {"status": "done", "filename": "b.bin",
                                "size": 2, "updated": TS}}}),
        encoding="utf-8")
    # --query changes what a .json export holds: the query result itself
    # (--export belongs to the subcommand; the state dir is the global -o)
    assert cli_main(["-o", str(tmp_path), "downloads", "--export", "q.json",
                     "--query", "[].filename", "--quiet"]) == 0
    assert json.loads((tmp_path / "q.json").read_text(
        encoding="utf-8")) == ["a.zip", "b.bin"]
    # .json extension alone picks the raw records (same data as --json)
    assert cli_main(["-o", str(tmp_path), "downloads", "--export", "plain.json",
                     "--quiet"]) == 0
    data = json.loads((tmp_path / "plain.json").read_text(encoding="utf-8"))
    assert [d["filename"] for d in data] == ["a.zip", "b.bin"]
    # a bad query writes nothing and exits 1
    assert cli_main(["-o", str(tmp_path), "downloads", "--export", "bad.json",
                     "--query", "bogus", "--quiet"]) == 1
    assert not (tmp_path / "bad.json").exists()


def test_history_preset_pins_out_query_and_viewer(prefs, capsys, tmp_path,
                                                  monkeypatch):
    monkeypatch.chdir(tmp_path)        # pinned 'out' is relative to the cwd
    monkeypatch.setattr(G, "SUBS_HISTORY_FILE", tmp_path / "subs.json")
    (tmp_path / "subs.json").write_text(json.dumps([
        {"path": "a.mkv", "ok": True, "dest": "a.srt", "language": "en",
         "provider": "subtitlecat", "size": 10, "cues": 5, "ts": TS}]),
        encoding="utf-8")
    G.save_preset("history", "weekly", provider="subtitlecat",
                  query="[].provider", out="hist.json", viewer="some-app")
    assert cli_main(["history", "--preset", "weekly", "--quiet"]) == 0
    assert json.loads((tmp_path / "hist.json").read_text(
        encoding="utf-8")) == ["subtitlecat"]


def test_history_out_with_query_writes_query_result(prefs, capsys, tmp_path,
                                                    monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(G, "SUBS_HISTORY_FILE", tmp_path / "subs.json")
    (tmp_path / "subs.json").write_text(json.dumps([
        {"path": "a.mkv", "ok": True, "dest": "a.srt", "language": "en",
         "provider": "subtitlecat", "size": 10, "cues": 5, "ts": TS}]),
        encoding="utf-8")
    assert cli_main(["history", "--out", "h.json", "--query", "length",
                     "--quiet"]) == 0
    assert json.loads((tmp_path / "h.json").read_text(
        encoding="utf-8")) == 1
    assert cli_main(["history", "--out", "bad.json", "--query", "bogus",
                     "--quiet"]) == 1
    assert not (tmp_path / "bad.json").exists()


# ------------------------------------------- export/import between machines
def _both_presets():
    G.save_preset("history", "cat2026", provider="subtitlecat",
                  since="2026-01-01", until="", query="[].provider",
                  out="w.json", viewer="code")
    G.save_preset("downloads", "errs", fmt="json",
                  query='[.[] | select(.status == "error")] | length')


def test_export_writes_shareable_document(prefs):
    _both_presets()
    assert G.export_presets(prefs / "share.json") == 2
    doc = json.loads((prefs / "share.json").read_text(encoding="utf-8"))
    assert set(doc) == set(G._PRESET_KINDS)   # every kind, even empty slots
    assert doc["history"]["cat2026"]["provider"] == "subtitlecat"
    assert doc["downloads"]["errs"]["fmt"] == "json"
    assert (prefs / "share.json").read_text(encoding="utf-8").endswith("\n")


def test_export_empty_store_writes_empty_document(prefs):
    assert G.export_presets(prefs / "share.json") == 0
    doc = json.loads((prefs / "share.json").read_text(encoding="utf-8"))
    assert doc == {k: {} for k in G._PRESET_KINDS}


def test_import_into_fresh_machine(prefs, capsys, tmp_path):
    """The sharing story end to end: machine A exports, machine B imports."""
    _both_presets()
    assert cli_main(["presets", "export", str(tmp_path / "share.json")]) == 0
    assert "exported 2 preset(s)" in _out(capsys)
    # machine B: a fresh (empty) prefs store imports everything
    monkeyprefs = tmp_path / "prefs_b.json"
    G.EXPORT_PREFS_FILE = monkeyprefs
    assert cli_main(["presets", "import", str(tmp_path / "share.json")]) == 0
    assert "imported 2 preset(s) (merged; 0 skipped)" in _out(capsys)
    assert G.load_presets("history")["cat2026"]["out"] == "w.json"
    assert G.load_presets("downloads")["errs"]["query"].startswith("[.[]")


def test_import_merges_and_overwrites_same_names(prefs, capsys, tmp_path):
    _both_presets()
    G.export_presets(tmp_path / "share.json")
    G.save_preset("history", "cat2026", provider="opensubtitles")
    G.save_preset("history", "local-only", provider="podnapisi")
    assert cli_main(["presets", "import", str(tmp_path / "share.json")]) == 0
    assert "imported 2 preset(s) (merged; 0 skipped)" in _out(capsys)
    hist = G.load_presets("history")
    assert hist["cat2026"]["provider"] == "subtitlecat"   # file wins on clash
    assert hist["local-only"]["provider"] == "podnapisi"  # untouched


def test_import_replace_drops_existing_first(prefs, capsys, tmp_path):
    _both_presets()
    G.export_presets(tmp_path / "share.json")
    G.save_preset("history", "local-only", provider="podnapisi")
    assert cli_main(["presets", "import", str(tmp_path / "share.json"),
                     "--replace"]) == 0
    assert "imported 2 preset(s) (replaced; 0 skipped)" in _out(capsys)
    assert set(G.load_presets("history")) == {"cat2026"}


def test_import_skips_malformed_and_unknown_kinds(prefs, capsys, tmp_path):
    (tmp_path / "share.json").write_text(json.dumps({
        "history": {"ok": {"provider": "p"}, "bad": "not-a-dict",
                    "": {"provider": "blank-name"}},
        "bogus_kind": {"x": {"a": 1}},
        "downloads": "not-a-dict",}), encoding="utf-8")
    assert cli_main(["presets", "import", str(tmp_path / "share.json")]) == 0
    assert "imported 1 preset(s) (merged; 4 skipped)" in _out(capsys)
    assert G.load_presets("history") == {"ok": {"provider": "p"}}
    assert G.load_presets("downloads") == {}


def test_import_drops_unknown_setting_keys(prefs, capsys, tmp_path):
    # a file from a newer version imports cleanly: extra keys are whitelisted
    # away instead of rejected
    (tmp_path / "share.json").write_text(json.dumps({
        "downloads": {"new": {"fmt": "csv", "query": "[]", "future": 1}}}),
        encoding="utf-8")
    assert cli_main(["presets", "import", str(tmp_path / "share.json")]) == 0
    assert G.load_presets("downloads")["new"] == {"fmt": "csv", "query": "[]"}


def test_import_unreadable_file_fails_cleanly(prefs, capsys, tmp_path):
    missing = tmp_path / "missing.json"
    assert cli_main(["presets", "import", str(missing)]) == 1
    assert "could not read presets" in _out(capsys)
    (tmp_path / "junk.json").write_text("not json", encoding="utf-8")
    assert cli_main(["presets", "import", str(tmp_path / "junk.json")]) == 1
    (tmp_path / "arr.json").write_text("[1, 2]", encoding="utf-8")
    assert cli_main(["presets", "import", str(tmp_path / "arr.json")]) == 1


# --------------------------------- {date}/{kind} placeholders in pinned out
def test_expand_preset_out_placeholders():
    NOW = 1_800_000_000                      # 2027-01-15 local
    assert G.expand_preset_out("reports/{date}-{kind}.json", "downloads",
                               now=NOW) == "reports/2027-01-15-downloads.json"
    assert G.expand_preset_out("plain.csv", "history", now=NOW) == "plain.csv"
    # case-sensitive: only the exact lowercase tokens expand
    assert G.expand_preset_out("{DATE}.csv", "downloads", now=NOW) == "{DATE}.csv"
    # unknown placeholders pass through untouched
    assert G.expand_preset_out("{unknown} {date}", "history",
                               now=NOW).endswith("{unknown} 2027-01-15")


def test_downloads_preset_out_template_creates_folders(prefs, capsys,
                                                       tmp_path,
                                                       monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_state(tmp_path)
    G.save_preset("downloads", "daily", query="[].filename",
                  out="reports/{date}-{kind}.json")
    assert cli_main(["downloads", "--preset", "daily", "--quiet"]) == 0
    p = tmp_path / "reports" / (time.strftime("%Y-%m-%d")
                                + "-downloads.json")
    assert p.exists()                        # parent folder auto-created
    assert json.loads(p.read_text(encoding="utf-8")) == ["a.zip", "b.bin"]
    assert f"reports/{time.strftime('%Y-%m-%d')}-downloads.json" in _out(capsys)


def test_history_preset_out_template(prefs, capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(G, "SUBS_HISTORY_FILE", tmp_path / "subs.json")
    (tmp_path / "subs.json").write_text(json.dumps([
        {"path": "a.mkv", "ok": True, "dest": "a.srt", "language": "en",
         "provider": "subtitlecat", "size": 10, "cues": 5, "ts": TS}]),
        encoding="utf-8")
    G.save_preset("history", "weekly", provider="", query="",
                  out="{kind}s/{date}.csv")
    assert cli_main(["history", "--preset", "weekly", "--quiet"]) == 0
    p = tmp_path / "historys" / (time.strftime("%Y-%m-%d") + ".csv")
    assert p.exists() and "a.mkv" in p.read_text(encoding="utf-8")


# --------------------------------- report presets (stats/providers --preset)
def _seed_stats_stores(tmp_path):
    """Both stores for the stats summary, at ./downloads (the default
    out_dir, matching the other tests' cwd convention)."""
    _seed_state(tmp_path)
    (tmp_path / "subs.json").write_text(json.dumps([
        {"path": "a.mkv", "ok": True, "dest": "a.srt", "language": "en",
         "provider": "subtitlecat", "size": 10, "cues": 5, "ts": TS}]),
        encoding="utf-8")


def test_stats_preset_pins_query_out_and_viewer(prefs, capsys, tmp_path,
                                                monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(G, "SUBS_HISTORY_FILE", tmp_path / "subs.json")
    _seed_stats_stores(tmp_path)
    G.save_preset("stats", "weekly", query=".downloads.pending",
                  out="stats.json", viewer="some-app")
    opened = []
    monkeypatch.setattr("idm.cli._open_exported",
                        lambda path, viewer="": opened.append((path, viewer)))
    assert cli_main(["stats", "--preset", "weekly", "--quiet"]) == 0
    assert json.loads((tmp_path / "stats.json").read_text(
        encoding="utf-8")) == 2          # the pinned query's result
    assert opened == []                  # --quiet suppresses the pinned viewer
    assert "preset weekly" in _out(capsys)
    # the pinned viewer reaches the opener when not --quiet
    assert cli_main(["stats", "--preset", "weekly"]) == 0
    assert opened == [("stats.json", "some-app")]   # raw pinned path form


def test_stats_preset_unknown_fails_cleanly(prefs, capsys, tmp_path,
                                            monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_stats_stores(tmp_path)
    assert cli_main(["stats", "--preset", "ghost"]) == 1
    assert "unknown preset" in _out(capsys)
    assert not (tmp_path / "stats.json").exists()    # nothing written


def test_stats_preset_without_out_is_stdout_only(prefs, capsys, tmp_path,
                                                 monkeypatch):
    """A query-only stats preset must not invent an export file."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(G, "SUBS_HISTORY_FILE", tmp_path / "subs.json")
    _seed_stats_stores(tmp_path)
    G.save_preset("stats", "pending", query=".downloads.pending")
    assert cli_main(["stats", "--preset", "pending"]) == 0
    assert _out(capsys).strip() == "2"               # stdout, --query path
    assert not (tmp_path / "stats.json").exists()


def test_stats_export_with_and_without_query(prefs, capsys, tmp_path,
                                             monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(G, "SUBS_HISTORY_FILE", tmp_path / "subs.json")
    _seed_stats_stores(tmp_path)
    # without --query: the full summary document
    assert cli_main(["stats", "--export", "full.json", "--quiet"]) == 0
    full = json.loads((tmp_path / "full.json").read_text(encoding="utf-8"))
    assert set(full) == {"downloads", "subtitles"}
    assert full["downloads"]["pending"] == 2      # the seed's 2 records
    # with --query: just the result (any shape)
    assert cli_main(["stats", "--export", "q.json", "--query",
                     ".downloads.by_status.error", "--quiet"]) == 0
    assert json.loads((tmp_path / "q.json").read_text(
        encoding="utf-8")) == 1                  # error record among the 2
    # a bad query writes nothing and exits 1
    assert cli_main(["stats", "--export", "bad.json", "--query", "bogus",
                     "--quiet"]) == 1
    assert not (tmp_path / "bad.json").exists()


def test_stats_preset_out_template_creates_folders(prefs, capsys, tmp_path,
                                                   monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_stats_stores(tmp_path)
    G.save_preset("stats", "daily", out="reports/{date}-{kind}.json")
    assert cli_main(["stats", "--preset", "daily", "--quiet"]) == 0
    p = tmp_path / "reports" / (time.strftime("%Y-%m-%d") + "-stats.json")
    assert p.exists()                        # {date}/{kind} expand, mkdir parents
    assert set(json.loads(p.read_text(encoding="utf-8"))) == {
        "downloads", "subtitles"}


def test_stats_watch_rejects_export_and_preset(prefs, capsys, tmp_path,
                                               monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_stats_stores(tmp_path)
    assert cli_main(["stats", "--watch", "1", "--export", "w.json"]) == 1
    assert "--watch" in _out(capsys)
    assert not (tmp_path / "w.json").exists()
    assert cli_main(["stats", "--watch", "1", "--preset", "daily"]) == 1
    assert "--watch" in _out(capsys)


def test_providers_preset_pins_query_out_and_viewer(prefs, capsys, tmp_path,
                                                    monkeypatch):
    import idm.health as H
    from idm.health import ProviderHealth
    monkeypatch.setattr(H, "run_checks", lambda *a, **k: [
        ProviderHealth("subtitlecat", "SubtitleCat", "ok", "reachable",
                       endpoint="https://subtitlecat.com", hints=[])])
    monkeypatch.chdir(tmp_path)
    G.save_preset("providers", "weekly", query=".providers[].name",
                  out="providers.json", viewer="some-app")
    opened = []
    monkeypatch.setattr("idm.cli._open_exported",
                        lambda path, viewer="": opened.append((path, viewer)))
    assert cli_main(["providers", "--preset", "weekly", "--quiet"]) == 0
    assert json.loads((tmp_path / "providers.json").read_text(
        encoding="utf-8")) == ["subtitlecat"]   # pinned query's result
    assert opened == []
    # the pinned viewer reaches the opener when not --quiet
    assert cli_main(["providers", "--preset", "weekly"]) == 0
    assert opened == [("providers.json", "some-app")]  # raw pinned path form
    # unknown preset still exits 1, even when a provider is down
    monkeypatch.setattr(H, "run_checks", lambda *a, **k: [
        ProviderHealth("podnapisi", "Podnapisi", "down", "DNS blocked",
                       hints=[])])
    assert cli_main(["providers", "--preset", "ghost"]) == 1
    assert "unknown preset" in _out(capsys)


def test_providers_export_down_report_still_exits_1(prefs, capsys, tmp_path,
                                                    monkeypatch):
    import idm.health as H
    from idm.health import ProviderHealth
    monkeypatch.setattr(H, "run_checks", lambda *a, **k: [
        ProviderHealth("podnapisi", "Podnapisi", "down", "DNS blocked",
                       hints=[])])
    monkeypatch.chdir(tmp_path)
    # the report file IS written (triage material) but the exit code stays 1
    assert cli_main(["providers", "--export", "rep.json", "--quiet"]) == 1
    data = json.loads((tmp_path / "rep.json").read_text(encoding="utf-8"))
    assert data["all_ok"] is False and data["mode"] == "quick"
    assert cli_main(["providers", "--export", "ok.json", "--query",
                     ".all_ok", "--quiet"]) == 1
    assert json.loads((tmp_path / "ok.json").read_text(
        encoding="utf-8")) is False


def test_presets_add_stats_kind_via_cli(prefs, capsys):
    assert cli_main(["presets", "add", "daily", "--kind", "stats",
                     "--set", "out=reports/{date}-{kind}.json",
                     "--set", "query=.downloads.pending"]) == 0
    assert G.load_presets("stats")["daily"] == {
        "query": ".downloads.pending", "out": "reports/{date}-{kind}.json"}
    assert cli_main(["presets", "remove", "daily", "--kind", "stats"]) == 0
    # stats presets reject history-only settings, like every kind
    assert cli_main(["presets", "add", "x", "--kind", "stats",
                     "--set", "provider=p"]) == 1
    assert "unknown setting" in _out(capsys)


def test_presets_list_json_includes_report_kinds(prefs, capsys):
    G.save_preset("stats", "daily", out="r.json")
    G.save_preset("providers", "weekly", query="[.providers[].name]")
    assert cli_main(["presets", "list", "--json"]) == 0
    doc = json.loads(_out(capsys))
    assert set(doc) == set(G._PRESET_KINDS)
    assert doc["stats"]["daily"] == {"out": "r.json"}
    assert doc["providers"]["weekly"] == {"query": "[.providers[].name]"}
    assert cli_main(["presets", "list", "--kind", "stats", "--json"]) == 0
    assert set(json.loads(_out(capsys))) == {"stats"}


def test_expand_preset_out_report_kinds():
    NOW = 1_800_000_000
    assert G.expand_preset_out("{date}-{kind}.json", "stats",
                               now=NOW) == "2027-01-15-stats.json"
    assert G.expand_preset_out("{kind}.json", "providers",
                               now=NOW) == "providers.json"
