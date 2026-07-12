"""
Legend ordering / dedup / channel-tag tests for App._ordered_legend.

Locks the v1.2.2 fix that collapsed nine identical "0.00 GPa - C" entries
into distinct, tagged labels. Needs a Tk display, so the whole module skips
cleanly on a headless box (the lab Windows machine has one).
"""
import numpy as np
import pytest

try:
    import tkinter as tk
    _root = tk.Tk()
    _root.withdraw()
    import app
    _APP = app.App(_root)
    _HAVE_GUI = True
except Exception:                      # no display, or Tk missing
    _HAVE_GUI = False

pytestmark = pytest.mark.skipif(not _HAVE_GUI, reason="no Tk display")


def _raw(sample, ch):
    """A raw-only result dict: only one finite channel, absorbance all-NaN."""
    nan = np.full(4, np.nan)
    fin = np.ones(4)
    return {"sample": sample, "absorbance": nan,
            "samp_c": fin if ch == "s" else nan,
            "bg_c": fin if ch == "b" else nan,
            "dark_c": fin if ch == "d" else nan}


def _full(sample):
    fin = np.ones(4)
    return {"sample": sample, "absorbance": fin,
            "samp_c": fin, "bg_c": fin, "dark_c": fin}


def _labels(entries):
    return _APP._ordered_legend(entries)[1]


def test_same_pressure_raw_disambiguated_by_sample():
    e = [(1, 0.0, "C", _raw("gasket2", "b")),
         (2, 0.0, "C", _raw("gasket3", "b")),
         (3, 0.1, "C", _full("gasket"))]
    assert _labels(e) == ["0.00 GPa - C [B only]  gasket2",
                          "0.00 GPa - C [B only]  gasket3",
                          "0.10 GPa - C"]


def test_exact_duplicates_collapse():
    r = _raw("gasket2", "b")
    e = [(1, 0.0, "C", r), (2, 0.0, "C", r), (3, 0.1, "C", _full("gasket"))]
    assert _labels(e) == ["0.00 GPa - C [B only]  gasket2", "0.10 GPa - C"]


def test_three_tuple_backward_compatible():
    e = [(1, 0.5, "C"), (2, 1.0, "D")]
    assert _labels(e) == ["0.50 GPa - C", "1.00 GPa - D"]
