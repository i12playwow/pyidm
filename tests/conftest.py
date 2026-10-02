"""Shared test-suite hygiene.

Every GUI test creates a real Tk app. Tk treats each interpreter as its
own application, so a leaked interpreter that later takes a grab makes
every subsequent grab_set() across the suite fail with
"grab failed: another application has grab" — an intermittent cascade
that once took out 27 dialog tests in a single CI run (one test patched
App.destroy to a no-op, and the patch outlived the fixture teardown that
was supposed to destroy the app, leaving the interpreter — and via
tkinter's _default_root, the target of any parentless grabbing dialog —
alive for the rest of the process).

This autouse guard tracks every tkinter.Tk created during a test and
force-destroys survivors at teardown (bypassing any instance-level
destroy patch), so a leak can never cascade again. Survivors still warn —
a leak means a test or fixture needs fixing, not just cleanup.
"""
import tkinter as tk
import warnings

import pytest

_REAL_TK_INIT = tk.Tk.__init__
_REAL_TK_DESTROY = tk.Tk.destroy


@pytest.fixture(autouse=True)
def _force_destroy_tk_roots(monkeypatch):
    created = []

    def tracking_init(self, *args, **kwargs):
        _REAL_TK_INIT(self, *args, **kwargs)
        created.append(self)

    monkeypatch.setattr(tk.Tk, "__init__", tracking_init)
    yield
    leaked = []
    for root in created:
        try:
            if not root.winfo_exists():
                continue
        except tk.TclError:
            continue                     # destroyed properly: nothing to do
        leaked.append(root)
        try:
            _REAL_TK_DESTROY(root)       # bypass instance-level destroy patches
        except Exception:                # pragma: no cover - defensive
            pass
    if leaked:
        warnings.warn(
            f"{len(leaked)} leaked Tk root(s) survived this test and were "
            "force-destroyed; a leaked interpreter can hold a grab and "
            "poison every later grab_set() in the run",
            stacklevel=2,
        )
