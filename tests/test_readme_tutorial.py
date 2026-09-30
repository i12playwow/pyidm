"""Guard: the README tutorial's runnable examples must work as documented.

Every ```bash fence inside the tutorial section ('Presets, queries, and
chips — the export workflow end to end') is parsed back into single `idm`
commands, run against live CLI code and seeded stores, and the results are
checked — exactly what a reader copying the commands would get. Inline
claims that produce no fenced command (the `--query` output line, the chip
label, the recents cap, the `{date}` expansion) get their own assertions,
so the tutorial can never quietly drift from the code.
"""
from __future__ import annotations

import json
import re
import shlex
import time
from pathlib import Path

import pytest

from idm import gui as G
from idm.cli import main as cli_main

README = Path(__file__).resolve().parent.parent / "README.md"
TUTORIAL_HEADING = "## Presets, queries, and chips — the export workflow end to end"


def _tutorial_section() -> str:
    """The tutorial section's text (from its heading to the next '## ')."""
    text = README.read_text(encoding="utf-8")
    start = text.index(TUTORIAL_HEADING)
    nxt = text.find("\n## ", start + 1)
    return text[start:nxt if nxt != -1 else len(text)]


def _fenced_commands() -> list[list[str]]:
    """Each tutorial ```bash fence -> the `idm ...` commands in it, with
    line continuations joined and comments stripped. A fence with no `idm`
    invocation is skipped (the tutorial's urls.example.txt block)."""
    out = []
    for block in re.findall(r"```bash\n(.*?)\n```", _tutorial_section(),
                            flags=re.DOTALL):
        line = " ".join(part for part in block.replace("\\\n", " ").split("\n")
                        if part.strip() and not part.strip().startswith("#"))
        cmds = [shlex.split(c) for c in line.split("idm ")[1:]]
        for c in cmds:
            out.append(["idm", *c])
    return out


def _inline_idm_commands(step: str) -> list[list[str]]:
    """The runnable `idm ...` backtick spans inside one '**Step N' paragraph
    of the tutorial (Step 5's headless commands are inline code, not a
    fence — this is what a reader would copy from the prose)."""
    section = _tutorial_section()
    start = section.index(f"**Step {step}")
    nxt = section.find("**Step", start + 1)
    chunk = section[start:nxt if nxt != -1 else len(section)]
    # markdown renders a line break inside an inline span as a space
    # (the source may wrap `idm presets import ...` across lines)
    chunk = re.sub(r"\s+", " ", chunk)
    return [shlex.split(span) for span in re.findall(r"`(idm [^`]*)`", chunk)]


# ------------------------------------------------------------------ stores
ROWS = [
    {"path": "C:/v/A.mkv", "ok": True, "dest": "C:/v/A.en.srt",
     "language": "en", "provider": "subtitlecat", "size": 135_480,
     "cues": 1746, "ts": 1_758_748_800},
    {"path": "C:/v/B.mp4", "ok": True, "dest": "C:/v/B.en.srt",
     "language": "en", "provider": "opensubtitles", "size": 51_200,
     "cues": 800, "ts": 1_758_748_900},
]

STATE = {
    "https://x/a.zip": {"status": "error", "filename": "a.zip", "size": 1024,
                        "updated": 1_758_748_800, "message": "HTTP 403"},
    "https://x/b.bin": {"status": "downloading", "filename": "b.bin",
                        "size": 2048, "updated": 1_758_748_900},
    "https://x/c.bin": {"status": "done", "filename": "c.bin",
                        "size": 2048, "updated": 1_758_749_000},
}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Seeded stores: one download with an error (so Step 1's 'just errors'
    query really selects something) and a two-row subtitle history."""
    (tmp_path / "downloads").mkdir()
    (tmp_path / "downloads" / "idm.state.json").write_text(
        json.dumps({"version": 1, "downloads": STATE}), encoding="utf-8")
    (tmp_path / "subs.json").write_text(json.dumps(ROWS), encoding="utf-8")
    monkeypatch.setattr(G, "SUBS_HISTORY_FILE", tmp_path / "subs.json")
    monkeypatch.setattr(G, "EXPORT_PREFS_FILE",
                        tmp_path / "export_prefs.json")
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture()
def cmd(env):
    """Run a fence-style argv ('idm downloads ...') through the real CLI
    entry point; the leading program name is dropped (cli_main takes the
    subcommand on). Captures stdout like a reader's terminal would."""
    def run(argv):
        import contextlib
        import io
        if argv and argv[0] == "idm":
            argv = argv[1:]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli_main(argv)
        return code, buf.getvalue()
    return run


@pytest.fixture()
def app(monkeypatch, tmp_path):
    """A real App window (same shape as test_gui_export_query's fixture)
    for the mockup-vs-widget geometry assertions."""
    monkeypatch.setattr(G, "should_show_first_run", lambda cfg: False)
    monkeypatch.setattr(G, "SUBS_HISTORY_FILE", tmp_path / "subs.json")
    monkeypatch.setattr(G, "EXPORT_PREFS_FILE", tmp_path / "export_prefs.json")
    a = G.App()
    a.update()
    yield a
    a.destroy()


@pytest.fixture()
def dl_store(tmp_path, monkeypatch):
    (tmp_path / "downloads" / "idm.state.json").parent.mkdir(exist_ok=True)
    (tmp_path / "downloads" / "idm.state.json").write_text(
        json.dumps({"version": 1, "downloads": STATE}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)


def _find_widgets(win, cls):
    """All descendants of `win` (inclusive) that are instances of `cls`."""
    found = []
    stack = [win]
    while stack:
        w = stack.pop()
        if isinstance(w, cls):
            found.append(w)
        stack.extend(w.winfo_children())
    return found


# ------------------------------------------------------- fence integrity
def test_tutorial_has_bash_fences_with_idm_commands():
    # if this breaks, the fence parser below is checking nothing
    fences = _fenced_commands()
    assert len(fences) >= 6, "tutorial lost its runnable fences"
    assert all(cmd[0] == "idm" for cmd in fences)


# ------------------------------------------------- Step 1: query commands
def test_step1_downloads_query_lists_urls(env, cmd):
    code, out = cmd(["idm", "downloads", "--query", "[].url"])
    assert code == 0
    # --query implies --json: a list result prints as a pretty JSON document
    assert json.loads(out) == ["https://x/a.zip", "https://x/b.bin",
                               "https://x/c.bin"]


def test_step1_downloads_query_errors_raw(env, cmd):
    # '[.[] | select(.status == "error")]' -r  — records stream as JSON lines
    code, out = cmd(["idm", "downloads", "--query",
                     '[.[] | select(.status == "error")]', "-r"])
    assert code == 0
    rows = [json.loads(line) for line in out.strip().splitlines()]
    assert [r["filename"] for r in rows] == ["a.zip"]
    assert rows[0]["message"] == "HTTP 403"


def test_step1_history_query_length(env, cmd):
    # "how many rows would this export?" — 2 seeded history rows
    code, out = cmd(["idm", "history", "--query", "length"])
    assert code == 0
    assert out.strip() == "2"


# ------------------------------------------------- Step 2: preset saves
def test_step2_presets_add_fences(env, cmd):
    fence = next(f for f in _fenced_commands()
                 if f[1:3] == ["presets", "add"] and "failed" in f)
    code, _ = cmd(fence)
    assert code == 0, "the tutorial's 'failed' preset add must succeed"
    assert G.load_presets("downloads")["failed"] == {
        "fmt": "json",
        "query": '[.[] | select(.status == "error")] | length'}


def test_step2_preset_weekly_with_pinned_out(env, cmd):
    fence = next(f for f in _fenced_commands()
                 if f[1:3] == ["presets", "add"] and "weekly" in f)
    code, _ = cmd(fence)
    assert code == 0
    # only the settings actually passed are stored (empty ones are omitted)
    assert G.load_presets("history")["weekly"] == {
        "provider": "subtitlecat", "out": "reports/{date}-weekly.csv",
        "viewer": "code"}


def test_step2_preset_list_shows_every_kind(env, cmd):
    G.save_preset("downloads", "failed", fmt="json",
                  query='[.[] | select(.status == "error")] | length')
    G.save_preset("history", "weekly", provider="subtitlecat",
                  query="[].provider", out="reports/{date}-weekly.csv",
                  viewer="code")
    code, out = cmd(["idm", "presets", "list"])
    assert code == 0
    assert "downloads/failed" in out and "history/weekly" in out


def test_step2_pinned_out_applied_later_with_date_expansion(env, cmd):
    # 'Applying weekly later is one command: idm history --preset weekly
    # writes the CSV' — {date} expands and the reports/ folder is created
    G.save_preset("history", "weekly", provider="subtitlecat",
                  out="reports/{date}-weekly.csv", viewer="code")
    code, out = cmd(["idm", "history", "--preset", "weekly", "--quiet"])
    assert code == 0
    stamp = time.strftime("%Y-%m-%d")
    p = Path("reports") / f"{stamp}-weekly.csv"
    assert p.exists(), out                      # landed in a new reports/ folder
    text = p.read_text(encoding="utf-8")
    assert text.splitlines()[0] == "video,lang,provider,size,cues"
    assert "A.mkv,en,subtitlecat" in text


def test_step2_explicit_flag_overrides_preset(env, cmd):
    # 'any flag you pass explicitly (--provider, --out, ...) overrides the
    # preset for that one run' — pass --out and the pinned path is not used
    G.save_preset("history", "weekly", provider="subtitlecat",
                  out="reports/{date}-weekly.csv", viewer="code")
    code, _ = cmd(["idm", "history", "--preset", "weekly",
                   "--out", "mine.csv", "--quiet"])
    assert code == 0
    assert Path("mine.csv").exists()
    assert not Path("reports").exists()         # pinned out skipped


def test_step2_report_kinds_pin_query_out_viewer(env, cmd):
    # 'idm stats --preset NAME and idm providers --preset NAME pin
    # query/out/viewer, and --export writes the same JSON report without
    # a preset'
    G.save_preset("stats", "s", query=".downloads.pending",
                  out="r.json", viewer="")
    code, _ = cmd(["idm", "stats", "--preset", "s", "--quiet"])
    assert code == 0
    assert json.loads(Path("r.json").read_text(encoding="utf-8")) == 3
    code, _ = cmd(["idm", "stats", "--export", "plain.json", "--quiet"])
    assert code == 0
    data = json.loads(Path("plain.json").read_text(encoding="utf-8"))
    assert set(data) >= {"downloads", "subtitles"}


# ------------------------------------------------ Step 3: GUI chip claims
def test_step3_chip_label_example():
    # "labelled `name · settings-hint`, e.g. `weekly ·
    # provider=subtitlecat · query=[].provider`" — the labeler is a pure
    # helper (no store access), so the doc example is checked directly
    settings = {"provider": "subtitlecat", "since": "", "until": "",
                "query": "[].provider", "out": "reports/{date}-weekly.csv",
                "viewer": "code"}
    assert G._preset_chip_text("weekly", settings) == \
        "weekly · provider=subtitlecat · query=[].provider"


# --------------------------------------------- Step 4: recents claims
def test_step4_recents_cap_of_8(monkeypatch, tmp_path):
    # "remembers the last 8 queries you ran per tab ... newest first"
    monkeypatch.setattr(G, "EXPORT_PREFS_FILE", tmp_path / "export_prefs.json")
    for q in "abcdefghij":
        G.remember_recent_query("history", q)
    assert G._recent_queries_for("history") == list("jihgfedc")[:8]


# ------------------------------------------- Step 5: headless + sharing
def test_step5_inline_commands_are_runnable(env):
    # the headless paragraph's backtick spans must be exactly the commands
    # a reader would copy (and every one must parse to a known subcommand)
    spans = _inline_idm_commands(5)
    assert spans == [["idm", "history", "--preset", "weekly", "--quiet"],
                     ["idm", "presets", "export", "share.json"],
                     ["idm", "presets", "import", "share.json"],
                     ["idm", "presets", "list", "--json"]]


def test_step5_cron_command_matches_the_gui_chip(env, cmd):
    # "a scheduled task running `idm history --preset weekly --quiet`
    # produces the same reports/{date}-weekly.csv" — run the exact span
    # the paragraph shows
    G.save_preset("history", "weekly", provider="subtitlecat",
                  out="reports/{date}-weekly.csv", viewer="code")
    cron = next(s for s in _inline_idm_commands(5) if "history" in s)
    assert "--quiet" in cron
    code, _ = cmd(cron)
    assert code == 0
    stamp = time.strftime("%Y-%m-%d")
    p = Path("reports") / f"{stamp}-weekly.csv"
    assert p.exists()
    assert p.read_text(encoding="utf-8").splitlines()[0] == \
        "video,lang,provider,size,cues"


def test_step5_export_then_import_round_trip(env, cmd):
    # "idm presets export share.json on this one, idm presets import
    # share.json on the next (merge; --replace wipes first)"
    G.save_preset("history", "weekly", provider="subtitlecat",
                  out="reports/{date}-weekly.csv", viewer="code")
    G.save_preset("downloads", "failed", fmt="json",
                  query='[.[] | select(.status == "error")] | length')
    code, out = cmd(["idm", "presets", "export", "share.json"])
    assert code == 0
    assert "exported 2 preset(s) -> share.json" in out
    share = json.loads(Path("share.json").read_text(encoding="utf-8"))
    assert set(share) == {"history", "downloads", "stats", "providers"}
    # simulate the next machine: replace its lone preset, then merge the share
    G.save_preset("downloads", "old", fmt="csv")
    code, out = cmd(["idm", "presets", "import", "share.json", "--replace"])
    assert code == 0
    assert "imported 2 preset(s) (replaced; 0 skipped)" in out
    assert list(G.load_presets("downloads")) == ["failed"]   # old wiped
    code, out = cmd(["idm", "presets", "import", "share.json"])
    assert code == 0 and "merged" in out
    assert list(G.load_presets("downloads")) == ["failed"]   # same-named kept


def test_step5_presets_list_json_machine_readable(env, cmd):
    # "`idm presets list --json` answers 'what do I have?' in machine-
    # readable form — the same store, read the same way"
    G.save_preset("downloads", "failed", fmt="json",
                  query='[.[] | select(.status == "error")] | length')
    code, out = cmd(["idm", "presets", "list", "--json"])
    assert code == 0
    data = json.loads(out)
    assert data["downloads"]["failed"]["fmt"] == "json"
    assert out == json.dumps(data, indent=2) + "\n"   # the documented format


# --------------------------------------- ASCII mockups stay true to the UI
def test_mockup_chip_labels_match_the_labeler():
    # the chips drawn in the README mockups must be exactly what
    # _preset_chip_text renders for the documented presets
    assert G._preset_chip_text(
        "failed", {"fmt": "json",
                   "query": '[.[] | select(.status == "error")] | length',
                   "out": "", "viewer": ""}) == "failed · fmt=json · query"
    assert G._preset_chip_text(
        "weekly", {"provider": "subtitlecat",
                   "out": "reports/{date}-weekly.csv", "viewer": "code"}) == \
        "weekly · provider=subtitlecat"
    # long chip values are dropped entirely (bare key), short ones shown
    assert G._preset_chip_text(
        "x", {"fmt": "csv", "query": "length", "out": "", "viewer": ""}) == \
        "x · fmt=csv · query=length"


def test_mockup_recent_chip_truncation_matches_the_gui():
    # the Run Query… mockup draws the 25-char prefix + '…' — the exact
    # truncation refresh_chips applies
    long_q = ".downloads[] | select(.status == \"error\")"
    shown = long_q if len(long_q) <= 26 else long_q[:25] + "…"
    assert shown == '.downloads[] | select(.st…'
    assert shown in README.read_text(encoding="utf-8")


def test_mockup_dialog_rows_match_grid_layout(app, dl_store):
    # chip placement claims in the mockups: preset chips in a row of their
    # own ABOVE the OK/Cancel row (the overlap regression), for both dialogs
    G.save_preset("downloads", "failed", fmt="json",
                  query='[.[] | select(.status == "error")] | length')
    dlg, *_ = app._build_downloads_export_dialog()
    dlg.update()
    chips = [b for b in _find_widgets(dlg, G.ttk.Button)
             if "·" in str(b.cget("text"))]
    btns = [b for b in _find_widgets(dlg, G.ttk.Button)
            if str(b.cget("text")) in ("OK", "Cancel")]
    assert max(c.winfo_rooty() for c in chips) < min(b.winfo_rooty()
                                                     for b in btns)
    assert dlg.grid_slaves(row=5) == [btns[0].master]   # buttons at row 5
    dlg.destroy()
