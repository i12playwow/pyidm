"""The modal-grab helper: dialogs must survive a foreign grab.

Tk treats every interpreter as its own application — a grab held by
another interpreter (another PyIDM window, a leaked test app, any other
process on the desktop) makes grab_set() raise TclError "grab failed:
another application has grab". That was the CI flake that once took 27
dialog tests out in one run. `_grab_modal` retries briefly and then leaves
the dialog open without modality rather than crashing the flow.
"""
from __future__ import annotations

import time
import tkinter as tk

import pytest

gui = pytest.importorskip("idm.gui")
from idm.gui import _grab_modal

GRAB_ERROR = "grab failed: another application has grab"


class _FlakyGrab:
    """Widget stand-in whose grab_set fails N times with the real error."""

    def __init__(self, failures: int, message: str = GRAB_ERROR):
        self.remaining = failures
        self.message = message
        self.calls = 0

    def update_idletasks(self) -> None:
        pass

    def grab_set(self) -> None:
        self.calls += 1
        if self.remaining > 0:
            self.remaining -= 1
            raise tk.TclError(self.message)


def test_grab_modal_retries_and_succeeds(monkeypatch):
    win = _FlakyGrab(failures=2)
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
    _grab_modal(win)                     # must not raise
    assert win.calls == 3                # two failures, then success
    assert sleeps == [0.05, 0.05]        # slept between attempts only


def test_grab_modal_gives_up_without_raising(monkeypatch):
    win = _FlakyGrab(failures=99)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    _grab_modal(win)                     # stays non-modal, no exception
    assert win.calls == 5                # default attempt count


def test_grab_modal_reraises_unrelated_errors():
    win = _FlakyGrab(failures=1, message="bad window path name")
    with pytest.raises(tk.TclError):
        _grab_modal(win)
    assert win.calls == 1                # no retry for non-grab errors


def test_grab_modal_survives_real_foreign_grab():
    """End to end: another interpreter holds a grab; the helper must not
    raise and the dialog must stay usable (destroyable) afterwards."""
    holder_root = tk.Tk()
    holder_root.withdraw()
    holder = tk.Toplevel(holder_root)
    holder.grab_set()
    holder_root.update()
    app_root = tk.Tk()
    app_root.withdraw()
    dlg = tk.Toplevel(app_root)
    try:
        _grab_modal(dlg)                 # foreign grab held: gives up quietly
        dlg.title("still usable")        # dialog keeps working, just non-modal
    finally:
        dlg.destroy()
        app_root.destroy()
        holder.destroy()
        holder_root.destroy()
