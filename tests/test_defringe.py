"""
Defringe sanity + parameter-passthrough tests.

Conservative on purpose: they lock the public contract (same-length finite
output, the v1.2.x keyword parameters are accepted, a clean signal is not
destroyed) without asserting detector internals that depend on resampling.
"""
import numpy as np
import defringe


def _wl():
    return np.linspace(400.0, 1000.0, 600)


def _clean_signal(wl):
    # smooth baseline + one broad absorption dip, no high-freq fringe
    return 1000.0 + 200.0 * np.exp(-((wl - 700.0) / 120.0) ** 2)


def test_curve_same_length_and_finite():
    wl = _wl()
    y = _clean_signal(wl)
    out = defringe.defringe_curve(wl, y)
    assert out.shape == y.shape
    assert np.isfinite(out).all()


def test_clean_signal_survives():
    wl = _wl()
    y = _clean_signal(wl)
    out = defringe.defringe_curve(wl, y)
    # a fringe-free curve must come back close to itself
    assert np.max(np.abs(out - y)) < 0.05 * np.ptp(y)


def test_channel_returns_clean_key():
    wl = _wl()
    res = defringe.defringe_channel(wl, _clean_signal(wl))
    assert "clean" in res
    assert np.asarray(res["clean"]).shape == wl.shape


def test_custom_parameters_accepted():
    wl = _wl()
    y = _clean_signal(wl)
    # the GUI-exposed knobs must be honored without error
    out = defringe.defringe_curve(wl, y, width_frac=0.10,
                                  nt_min_nm=20_000, nt_max_nm=80_000,
                                  pvalue_max=1e-3)
    assert out.shape == y.shape and np.isfinite(out).all()


def test_constants_present():
    for name in ("NOTCH_WIDTH_FRAC", "FRINGE_NT_MIN_NM",
                 "FRINGE_NT_MAX_NM", "FRINGE_PVALUE_MAX"):
        assert hasattr(defringe, name)
