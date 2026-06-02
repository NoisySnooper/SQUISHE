"""
smoothing.py  --  faithful Python port of the DAC_AutoPlot_v4 Igor smoothing.

Five-step pipeline, applied in this order (each independently toggleable):
  1. Saturation cutoff   : NaN any absorbance above a ceiling (detector saturated)
  2. Density filter       : NaN points in sparse neighbourhoods (isolated junk)
  3. Hampel despike       : replace outliers with the local median (MAD test)
  4. Split Savitzky-Golay : per contiguous run, SG-smooth with different
                            window/poly below vs above a wavelength split point
  5. Jump filter          : NaN a buffer around large point-to-point jumps

Split decision uses WAVELENGTH (nm) regardless of the display axis, matching the
Igor intent (the 600 nm boundary separates two grating/detector regimes).

Defaults below are copied verbatim from DACSmooth_InitParams in the .ipf.

NQT / Lee Lab -- Jun 2026
"""

import numpy as np
from scipy.signal import savgol_filter
from scipy.ndimage import median_filter

# Verbatim from Igor DACSmooth_InitParams()
DEFAULTS = {
    "cutoff_on": True,  "cutoff_val": 4.0,
    "density_on": True, "density_win": 50, "density_min": 10,
    "hampel_on": True,  "hampel_win": 5,  "hampel_sig": 3.0,
    "split_nm": 600.0,
    "left_win": 201, "left_poly": 2,
    "right_win": 101, "right_poly": 2,
    "jump_on": True, "jump_thresh": 0.2, "jump_step": 1, "jump_buff": 2,
}


def _cutoff(y, limit):
    y[~np.isfinite(y) | (y > limit)] = np.nan


def _density(y, win, min_pts):
    """NaN points with fewer than min_pts finite neighbours in a +/- win/2 window."""
    finite = np.isfinite(y).astype(float)
    half = int(win // 2)
    kernel = np.ones(2 * half + 1)
    counts = np.convolve(finite, kernel, mode="same")
    y[counts < min_pts] = np.nan


def _hampel(y, win, sigma):
    """Median/MAD despike. NaNs are bridged for the median, then restored."""
    if win % 2 == 0:
        win += 1
    finite = np.isfinite(y)
    if finite.sum() < win:
        return
    filled = y.copy()
    # bridge NaNs so the median filter is well defined, then re-mask
    idx = np.arange(len(y))
    filled[~finite] = np.interp(idx[~finite], idx[finite], y[finite])
    med = median_filter(filled, size=win, mode="nearest")
    mad = median_filter(np.abs(filled - med), size=win, mode="nearest")
    lim = sigma * 1.4826 * mad
    spike = finite & (np.abs(y - med) > lim)
    y[spike] = med[spike]


def _sg_chunks(y, x_nm, split, lwin, lpoly, rwin, rpoly):
    """Savitzky-Golay each contiguous finite run; params chosen by chunk midpoint."""
    finite = np.isfinite(y)
    if not finite.any():
        return
    # find contiguous runs of finite values
    edges = np.diff(finite.astype(int))
    starts = list(np.where(edges == 1)[0] + 1)
    ends = list(np.where(edges == -1)[0])
    if finite[0]:
        starts = [0] + starts
    if finite[-1]:
        ends = ends + [len(y) - 1]

    for s, e in zip(starts, ends):
        n = e - s + 1
        mid = (s + e) // 2
        if x_nm is not None and np.isfinite(x_nm[mid]) and x_nm[mid] < split:
            win, poly = lwin, lpoly
        elif x_nm is not None and not (x_nm[mid] < split):
            win, poly = rwin, rpoly
        else:
            win, poly = lwin, lpoly
        if win % 2 == 0:
            win += 1
        if win > n:
            win = n if n % 2 == 1 else n - 1
        if win < 5 or win <= poly:
            continue
        y[s:e + 1] = savgol_filter(y[s:e + 1], window_length=win, polyorder=poly)


def _jump(y, thresh, step, buff):
    """NaN a +/- buff window around any |y[i] - y[i+step]| > thresh."""
    n = len(y)
    kill = np.zeros(n, bool)
    for i in range(n - step):
        a, b = y[i], y[i + step]
        if np.isfinite(a) and np.isfinite(b) and abs(a - b) > thresh:
            kill[max(0, i - buff):min(n, i + step + buff + 1)] = True
    y[kill] = np.nan


def smooth_curve(x_nm, y, params=None):
    """
    Return a smoothed copy of absorbance y (NaNs mark removed points).

    x_nm : wavelength in nm (used only for the SG split decision)
    params : dict overriding DEFAULTS; missing keys fall back to DEFAULTS
    """
    p = dict(DEFAULTS)
    if params:
        p.update(params)
    out = np.asarray(y, float).copy()
    x_nm = None if x_nm is None else np.asarray(x_nm, float)

    if p["cutoff_on"]:
        _cutoff(out, p["cutoff_val"])
    if p["density_on"]:
        _density(out, p["density_win"], p["density_min"])
    if p["hampel_on"]:
        _hampel(out, p["hampel_win"], p["hampel_sig"])
    _sg_chunks(out, x_nm, p["split_nm"], p["left_win"], p["left_poly"],
               p["right_win"], p["right_poly"])
    if p["jump_on"]:
        _jump(out, p["jump_thresh"], p["jump_step"], p["jump_buff"])
    return out
