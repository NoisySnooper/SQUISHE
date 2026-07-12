"""Multi-tab session isolation tests (v1.4 core).

A "tab" is a stored session swapped into the single shared UI. These lock
the guarantee that data, control settings, trace visibility, folders, and
undo stacks are isolated per session and round-trip on switch. Needs a Tk
display, so skips cleanly on a headless box (like test_legend.py).
"""
import numpy as np
import pytest

try:
    import tkinter as tk
    # Reuse an existing default root if another GUI test module already
    # created one: this Windows Store Python cannot spin up a SECOND
    # independent Tk() interpreter (it fails to locate init.tcl), so a
    # fresh tk.Tk() here would break when run after test_legend.
    _root = tk._default_root or tk.Tk()
    _root.withdraw()
    import app
    _APP = app.App(_root)
    _HAVE_GUI = True
except Exception:
    _HAVE_GUI = False

pytestmark = pytest.mark.skipif(not _HAVE_GUI, reason="no Tk display")


def _res(label, dac, sample, pstr, pval):
    """A minimal valid engine-style result dict with real absorbance."""
    wl = np.linspace(400.0, 1000.0, 60)
    a = np.linspace(0.1, 1.2, 60)
    return {"label": label, "dac": dac, "sample": sample,
            "pressure_str": pstr, "pressure_val": pval, "rep": 1,
            "branch_tag": None, "wl": wl, "wn": 1e7 / wl,
            "absorbance": a, "dark_c": np.ones(60),
            "bg_c": np.full(60, 10.0), "samp_c": np.full(60, 5.0)}


def _reset_to_single():
    """Collapse back to one blank session before each test."""
    while len(_APP.sessions) > 1:
        _APP._close_session(len(_APP.sessions) - 1)
    _APP._new_session(name="base")   # fresh blank active tab
    while len(_APP.sessions) > 1:
        _APP._close_session(0)


def _load(results, dest="d"):
    _APP._finish_run([dict(r) for r in results], [], dest)


def test_data_and_settings_isolated():
    _reset_to_single()
    a = _APP
    _load([_res("A1", "D", "S", "1p0", 1.0), _res("A2", "D", "S", "2p0", 2.0)])
    a.in_var.set("folderA")
    a.cmap.set("viridis")
    a._store_active()
    a.sessions[a.active]["name"] = "A"

    a._new_session(name="B")
    assert a.results == []
    assert a.in_var.get() == ""
    assert a.cmap.get() == a._defaults["cmap"]   # blank tab = defaults

    _load([_res("B1", "E", "T", "5p0", 5.0)])
    a.cmap.set("magma")

    a._switch_session(0)   # back to A
    assert [r["label"] for r in a.results] == ["A1", "A2"]
    assert a.cmap.get() == "viridis"
    assert a.in_var.get() == "folderA"

    a._switch_session(1)   # to B
    assert [r["label"] for r in a.results] == ["B1"]
    assert a.cmap.get() == "magma"
    assert a.in_var.get() == ""


def test_trace_visibility_per_session():
    _reset_to_single()
    a = _APP
    _load([_res("T1", "D", "S", "1p0", 1.0), _res("T2", "D", "S", "2p0", 2.0)])
    a.trace_vars["T2"].set(False)          # hide T2 in tab 0
    a._store_active()

    a._new_session(name="clean")
    _load([_res("T1", "D", "S", "1p0", 1.0), _res("T2", "D", "S", "2p0", 2.0)])
    assert a.trace_vars["T2"].get() is True   # new tab: all shown

    a._switch_session(0)
    assert a.trace_vars["T2"].get() is False  # tab 0 kept T2 hidden


def test_undo_stacks_isolated():
    _reset_to_single()
    a = _APP
    _load([_res("U1", "D", "S", "1p0", 1.0)])
    a.lw.set(3.0)
    a._push_undo("lw change")
    depth0 = len(a._undo_stack)
    assert depth0 >= 2

    a._new_session(name="fresh")
    assert len(a._undo_stack) == 1        # fresh tab: only the initial snap

    a._switch_session(0)
    assert len(a._undo_stack) == depth0   # tab 0 stack intact
    assert abs(float(a.lw.get()) - 3.0) < 1e-9


def test_close_active_loads_neighbor():
    _reset_to_single()
    a = _APP
    _load([_res("K1", "D", "S", "1p0", 1.0)])
    a._store_active()
    a.sessions[a.active]["name"] = "keep"
    a._new_session(name="doomed")
    _load([_res("D1", "E", "T", "9p0", 9.0)])
    assert a.active == 1
    a._close_session(1)                   # close active tab
    assert len(a.sessions) == 1
    assert [r["label"] for r in a.results] == ["K1"]   # neighbor is live


def test_close_last_tab_resets_blank():
    _reset_to_single()
    a = _APP
    _load([_res("L1", "D", "S", "1p0", 1.0)])
    a._close_session(0)                   # only tab -> reset blank
    assert len(a.sessions) == 1
    assert a.results == []
    assert a.active == 0
