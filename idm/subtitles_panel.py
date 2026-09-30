"""PyIDM GUI — Subtitle history panel behaviour, as an App mixin.

Split out of gui.py (one-shot refactor); this module is part of the idm.gui
package and is not a stable API.
"""
from __future__ import annotations

import threading
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # method bodies type against the assembled app
    from idm.gui import _AppBase as _MixinBase
else:
    _MixinBase = object


class SubtitlesPanelMixin(_MixinBase):
    """Mixin for idm.gui.App — see gui.py for assembly."""

    def _selected_subtitle_dest(self) -> str | None:
        sel = self.subs_tree.selection()
        if not sel:
            return None
        return self.subs_dest_by_iid.get(sel[0])
    def open_subtitle(self) -> None:
        """Open the selected history row's .srt with its default app."""
        dest = self._selected_subtitle_dest()
        if not dest:
            return
        try:
            _g.open_file(dest)
            self._log(f"opening {Path(dest).name}")
        except OSError as e:
            self._log(f"could not open {dest}: {e}", "warn")
    def reveal_subtitle(self) -> None:
        """Show the selected .srt selected in Explorer."""
        dest = self._selected_subtitle_dest()
        if not dest:
            return
        try:
            _g.reveal_in_explorer(dest)
        except OSError as e:
            self._log(f"could not reveal {dest}: {e}", "warn")
    def _selected_video_folder(self) -> str | None:
        """Folder of the selected row's video (falls back to the .srt's folder)."""
        sel = self.subs_tree.selection()
        if not sel:
            return None
        iid = sel[0]
        video = self.subs_video_by_iid.get(iid)
        if video:
            return str(Path(video).parent)
        dest = self.subs_dest_by_iid.get(iid)
        return str(Path(dest).parent) if dest else None
    def open_video_folder(self) -> None:
        """Open the video's folder in Explorer (the movie, not the subtitle)."""
        folder = self._selected_video_folder()
        if not folder:
            return
        try:
            _g.open_folder(folder)
            self._log(f"opening folder {folder}")
        except OSError as e:
            self._log(f"could not open folder {folder}: {e}", "warn")
    def _selected_subtitle_video(self) -> str | None:
        sel = self.subs_tree.selection()
        if not sel:
            return None
        return self.subs_video_by_iid.get(sel[0])
    def _relocate_subtitle(self) -> None:
        """Re-point a grey (missing) history row at the .srt's new location.
        A folder picker searches for the exact name first, then a unique
        same-stem file (renamed extension); an empty search or Esc falls
        back to picking the file itself. Updates + persists the history and
        re-renders so the row un-greys immediately."""
        dest = self._selected_subtitle_dest()
        if not dest:
            return
        if Path(dest).is_file():
            self._log("that row's subtitle already exists on disk")
            return
        new_dest = None
        folder = filedialog.askdirectory(
            title=f"Pick the folder that now holds {Path(dest).name} "
                  "(cancel to pick the file)")
        if folder:
            new_dest = _g.find_relocated_file(dest, folder)
            if not new_dest:
                self._log(f"no matching .srt for {Path(dest).name} "
                          f"in {folder} — pick the file itself", "warn")
        if not new_dest:
            new_dest = filedialog.askopenfilename(
                title="Pick the relocated subtitle file",
                filetypes=[("Subtitles", "*.srt *.ass *.ssa *.vtt"),
                           ("All files", "*")])
        if not new_dest:
            return  # user backed out — leave the row untouched
        if _g.update_history_dest(dest, new_dest) == 0:
            self._log(f"could not update history for {Path(dest).name}",
                      "warn")
            return
        self._restore_subs_history()  # re-render from the persisted history
        self._log(f"relocated {Path(dest).name} -> {new_dest}")
    def _render_subs_table(self, results) -> None:
        """Rebuild the history table from OK rows: sorted per the combobox,
        with amber tags on weak rows and the dest map rebuilt for open/reveal."""
        rows = _g.sort_subs_results(results, _g.sort_key_from_label(self.subs_sort_var.get()))
        weak = _g.weak_subtitle_indices(rows)
        missing = _g.missing_subtitle_indices(rows)
        self.subs_tree.delete(*self.subs_tree.get_children())
        self.subs_dest_by_iid.clear()
        self.subs_video_by_iid.clear()
        dest_by_video = {Path(v).name: d for v, d in self._pending_finished}
        for i, r in enumerate(rows):
            video, lang, provider, size, cues = _g.subtitle_row(r)
            if i in missing:
                # missing wins the color clash: grey is the honest state.
                # Tk gives precedence to the LAST tag, so "missing" goes last.
                tags: tuple[str, ...] = ("weak", "missing") if i in weak else ("missing",)
            else:
                tags = ("weak",) if i in weak else ()
            iid = self.subs_tree.insert("", "end",
                                        values=(video, lang, provider, size, cues),
                                        tags=tags)
            if r.get("path"):
                self.subs_video_by_iid[str(iid)] = str(r["path"])
            dest = r.get("dest") or dest_by_video.get(video)
            if dest:
                self.subs_dest_by_iid[str(iid)] = str(dest)
        self.subs_status_var.set(_g.history_status(rows))
    def _restore_subs_history(self) -> None:
        """Fill the history table from the persisted JSON (startup + clear-safe).
        Applies config-driven auto-prune first and logs what it dropped."""
        self._pending_finished: list[tuple[str, str]] = []
        before = _g.load_subs_history()
        rows = _g.prune_history(before, _g._history_max_age_days())
        if len(rows) < len(before):
            dropped = len(before) - len(rows)
            self._log(f"history auto-pruned {dropped} "
                      f"entr{'y' if dropped == 1 else 'ies'} "
                      "older than subtitle_history_max_age_days")
            try:  # persist the prune so dropped entries don't resurrect
                import json
                _g.SUBS_HISTORY_FILE.write_text(json.dumps(rows, indent=1),
                                             encoding="utf-8")
            except OSError:
                pass  # best-effort; the view is already pruned
        self._render_subs_table(rows)
    def _copy_subs_history(self, fmt: str) -> None:
        """Copy the full subtitle history table to the clipboard as CSV or
        markdown (fmt: 'csv' | 'markdown')."""
        rows = _g.subtitle_rows(_g.load_subs_history())
        text = _g.history_to_csv(rows) if fmt == "csv" else _g.history_to_markdown(rows)
        self.clipboard_clear()
        self.clipboard_append(text)
        self._log(f"subtitle history copied to clipboard as {fmt} "
                  f"({len(rows)} row(s))")
    def clear_subs_history(self) -> None:
        if not self.subs_tree.get_children():
            return
        if not messagebox.askyesno("PyIDM", "Clear the subtitle history?\n"
                                   "Downloaded .srt files are NOT deleted."):
            return
        try:
            _g.SUBS_HISTORY_FILE.unlink(missing_ok=True)
        except OSError:
            pass
        self._restore_subs_history()  # now-empty table
        self._log("subtitle history cleared (files on disk untouched)")
    def _on_subs_done(self, ok, total, finished, results, cfg) -> None:
        """Render the run summary, the history table, and (optionally) autoplay."""
        self.subs_btn.config(state="normal")
        self._log(f"subtitles finished: {ok}/{total} files", "info")
        self._pending_finished = list(finished or [])
        self._render_subs_table(_g.save_subs_history(results))
        for line in _g.subtitle_hints(results):
            self._log(f"✓ {line}", "info")
        if self.play_var.get():
            for video, dest in finished:
                # stagger multi-file launches off the UI thread — bare
                # self.after(ms) is a no-op (it needs a callback)
                def _stagger(v=video, d=dest) -> None:
                    self._launch_player(v, d)
                self.after(1500, _stagger)
    def _launch_player(self, video, dest) -> None:
        """Autoplay one finished subtitle's video (runs via .after)."""
        cfg = self.cfg
        try:
            info = _g.launch_with_subtitle(video, dest, cfg)
            self._log(f"now playing {Path(video).name} in {info['player']}")
        except Exception as e:
            self._log(f"player launch failed: {e}", "warn")
    def fetch_subs(self) -> None:
        if self.running:
            messagebox.showinfo("PyIDM", "Wait for the batch to finish first.")
            return
        folder = self.subs_dir.get().strip()
        if not folder or not Path(folder).is_dir():
            messagebox.showinfo("PyIDM", "Choose a folder containing video files.")
            return
        langs = self.langs_var.get().strip() or "en"
        self.subs_btn.config(state="disabled")

        def worker():
            self.events.put(("log", f"fetching subtitles ({langs}) for {folder}…", "info"))
            cfg = dict(self.cfg)
            player = self.player_var.get().strip()
            if player:
                cfg["player"] = player
            results = _g.batch_for_folder(
                folder, cfg, langs,
                log=lambda m, lvl="info": self.events.put(("log", m, lvl)),
            )
            ok = sum(1 for r in results if r.get("ok"))
            finished = [(r["path"], r["dest"]) for r in results if r.get("ok") and r.get("dest")]
            self.events.put(("subs_done", ok, len(results), finished, results, dict(cfg)))

        threading.Thread(target=worker, daemon=True).start()


from idm import gui as _g  # noqa: E402  (late import: gui imports this module)
