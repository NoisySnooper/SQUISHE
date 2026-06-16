"""
defringe.py  --  FFT-notch defringe for DAC raw spectra.

Self-contained, dependency-light port of the FFT-notch fringe remover from the
downstream `defringe_dac.py` pipeline, reduced to what the Quick-Look tool needs:
notch the dominant diamond-anvil interference fringe out of a raw intensity
channel (Sample or Background) and, for export, recompute absorbance from the
two defringed channels.

Faithful to `defringe_dac.py` (operates on RAW counts, per channel, then forms
the absorbance ratio):
  - wavenumber = 1 / lambda_nm           (nm^-1)
  - fringe frequency f_center = 2 * n*t   (n*t in nm)
  - peak search restricted to n*t in [FRINGE_NT_MIN_NM, FRINGE_NT_MAX_NM]
  - Fisher g-test gate: a channel with no confident fringe is left UNCHANGED.

NaN-safe: invalid points are dropped for the FFT (the uniform-wavenumber
resample bridges the gaps) and restored as NaN in the output, so results align
1:1 with the input. Uses only numpy + scipy + stdlib csv (no pandas).

NQT / Lee Lab port -- Jun 2026
"""

import os
import csv
import numpy as np
from scipy.signal import find_peaks
from scipy.special import comb

# -- Constants (verbatim from defringe_dac.py) -------------------------------
NOTCH_WIDTH_FRAC  = 0.15      # default Gaussian-notch half-width / centre freq
FRINGE_NT_MIN_NM  = 15_000    # min n*t for FFT peak search (nm) ~ 15 um
FRINGE_NT_MAX_NM  = 100_000   # max n*t for FFT peak search (nm) ~ 100 um
FRINGE_PVALUE_MAX = 1e-4      # Fisher g-test p-value above which "no fringe"

_NM_TO_UM = 1.0e-3


def fisher_g_pvalue(periodogram):
    """Fisher's exact test for periodicity in a periodogram.

    Returns (g, pvalue). Small p-value => significant periodicity.
    Verbatim from defringe_dac.py (Wichert et al. 2004, eq. 6).
    """
    P = np.asarray(periodogram, dtype=float)
    n = len(P)
    if n < 2 or P.sum() <= 0:
        return 1.0, 1.0
    g = float(P.max() / P.sum())
    if g <= 0:
        return g, 1.0
    p_terms = int(1.0 / g)            # floor(1/g)
    pvalue = 0.0
    for j in range(1, p_terms + 1):
        term = (-1.0) ** (j - 1) * comb(n, j, exact=True) * (1.0 - j * g) ** (n - 1)
        pvalue += term
    pvalue = max(0.0, min(1.0, pvalue))
    return g, pvalue


def detect_fringe_nt(wn_u, sig_u, nt_min_nm=None, nt_max_nm=None):
    """Locate the dominant fringe frequency on a uniform-wavenumber signal.

    Core of `fft_initial_guess` (defringe_dac.py:653). Divisive detrend
    `sig_u/trend - 1` (correct for raw counts with a multiplicative lamp
    envelope), Hann window, FFT, strongest prominent peak in the physical n*t
    range, Fisher g-test.

    Returns (nt_nm, pvalue): nt_nm is n*t in nm (fringe freq = 2*nt), or None
    when there is no frequency in range. pvalue small => confident periodicity.
    """
    trend = np.polyval(np.polyfit(wn_u, sig_u, 4), wn_u)
    trend = np.maximum(trend, 0.01 * float(trend.max()))
    norm_u = sig_u / trend - 1.0
    window = np.hanning(len(norm_u))
    sig_win = norm_u * window

    dw = wn_u[1] - wn_u[0]
    fft_complex = np.fft.rfft(sig_win)
    fft_amp = np.abs(fft_complex)
    freqs = np.fft.rfftfreq(len(sig_win), d=dw)

    freq_min = 2.0 * (FRINGE_NT_MIN_NM if nt_min_nm is None else float(nt_min_nm))
    freq_max = 2.0 * (FRINGE_NT_MAX_NM if nt_max_nm is None else float(nt_max_nm))
    valid = (freqs >= freq_min) & (freqs <= freq_max)
    if not valid.any():
        return None, 1.0

    peaks, _ = find_peaks(fft_amp, prominence=fft_amp[valid].max() * 0.005)
    peaks_in = peaks[(freqs[peaks] >= freq_min) & (freqs[peaks] <= freq_max)]
    if len(peaks_in) > 0:
        peak_idx = int(peaks_in[np.argmax(fft_amp[peaks_in])])
    else:
        peak_idx = int(np.argmax(np.where(valid, fft_amp, 0.0)))

    _, pvalue = fisher_g_pvalue(fft_amp[valid] ** 2)
    return freqs[peak_idx] / 2.0, pvalue


def _notch(wn_u, sig_u, wl, raw, nt_nm, width_frac):
    """Subtract the Gaussian-notched fringe band, mapped back to the wl grid.

    Verbatim from defringe_dac.defringe_fft_notch (sans the n*t return value).
    """
    N = len(sig_u)
    dw = np.median(np.abs(np.diff(wn_u)))
    f_center = 2.0 * nt_nm

    # Mirror-pad so the signal is periodic (no spectral leakage at the edges).
    pad = N // 2
    sig_padded = np.concatenate([sig_u[pad:0:-1], sig_u, sig_u[-2:-pad - 2:-1]])
    N_pad = len(sig_padded)

    S = np.fft.rfft(sig_padded)
    freqs = np.fft.rfftfreq(N_pad, d=dw)

    sigma_f = width_frac * f_center
    notch = 1.0 - np.exp(-0.5 * ((freqs - f_center) / sigma_f) ** 2)
    sig_filtered_padded = np.fft.irfft(S * notch, n=N_pad)
    sig_filtered = sig_filtered_padded[pad:pad + N]

    fringe_wn = sig_u - sig_filtered          # the removed component (wn grid)
    fringe_on_wl = np.interp(1.0 / wl, wn_u, fringe_wn)
    return raw - fringe_on_wl


def defringe_channel(wl_nm, counts, width_frac=NOTCH_WIDTH_FRAC,
                     nt_min_nm=None, nt_max_nm=None, pvalue_max=None):
    """FFT-notch defringe one raw intensity channel.

    nt_min_nm / nt_max_nm / pvalue_max override the module constants when
    given (None = the defringe_dac.py defaults, identical behavior).

    Returns a dict: {'clean', 'applied', 'nt_um', 'pvalue'}.
      clean   : defringed counts (same shape as input; a copy of `counts` when
                no fringe is removed). NaNs in the input are preserved.
      applied : True iff a confident fringe was found and notched.
      nt_um   : detected n*t in micron (None if not applied).
      pvalue  : Fisher g-test p-value of the detection (1.0 if not applied).

    This is the single source of truth for both the GUI and the CSV writer.
    """
    wl_nm = np.asarray(wl_nm, float)
    y = np.asarray(counts, float)
    out = y.copy()
    result = {"clean": out, "applied": False, "nt_um": None, "pvalue": 1.0}
    if width_frac <= 0:
        return result

    finite = np.isfinite(y) & np.isfinite(wl_nm) & (wl_nm > 0)
    if finite.sum() < 16:
        return result

    wl_f = wl_nm[finite]
    y_f = y[finite]

    # Uniform wavenumber grid (nm^-1), ascending; interpolation bridges any gaps
    # left by the dropped non-finite points.
    wn = 1.0 / wl_f
    sidx = np.argsort(wn)
    wn_s, sig_s = wn[sidx], y_f[sidx]
    wn_u = np.linspace(wn_s[0], wn_s[-1], len(wn_s))
    sig_u = np.interp(wn_u, wn_s, sig_s)

    nt_nm, pvalue = detect_fringe_nt(wn_u, sig_u, nt_min_nm, nt_max_nm)
    result["pvalue"] = pvalue
    pmax = FRINGE_PVALUE_MAX if pvalue_max is None else float(pvalue_max)
    if nt_nm is None or nt_nm <= 0 or pvalue > pmax:
        return result

    out[finite] = _notch(wn_u, sig_u, wl_f, y_f, nt_nm, width_frac)
    result["applied"] = True
    result["nt_um"] = float(nt_nm) * _NM_TO_UM
    return result


def defringe_curve(wl_nm, y, width_frac=NOTCH_WIDTH_FRAC, **kw):
    """Thin wrapper returning just the defringed array (for simple call sites)."""
    return defringe_channel(wl_nm, y, width_frac, **kw)["clean"]


# ---------------------------------------------------------------------------
# CSV export  (reduced schema -- no noise-floor / best-case / dispersion cols)
# ---------------------------------------------------------------------------
_NOTCH_CSV_BASE = ["Wavelength", "Dark", "Background", "Sample", "Absorbance"]
_NOTCH_CSV_NOTCH = ["Background_notch", "Sample_notch", "Absorbance_notch"]


def _result_stem(result):
    stem = "%s_%s_%s" % (result["dac"], result["sample"], result["pressure_str"])
    if result.get("branch_tag"):
        stem += "_" + result["branch_tag"]
    return stem


def write_notch_csv(result, out_dir, width_frac=NOTCH_WIDTH_FRAC,
                    nt_min_nm=None, nt_max_nm=None, pvalue_max=None):
    """Write one defringed CSV for an engine result dict.

    Notches the raw Sample and Background counts independently, then recomputes
    absorbance from the defringed (or, per channel, original) counts. The three
    `_notch` columns are written only when at least one channel had a confident
    fringe; an un-notched channel's column is left blank.

    Columns: Wavelength, Dark, Background, Sample, Absorbance
             [, Background_notch, Sample_notch, Absorbance_notch]
    Returns the path written.
    """
    wl = np.asarray(result["wl"], float)
    dark = np.asarray(result["dark_c"], float)
    bg = np.asarray(result["bg_c"], float)
    s = np.asarray(result["samp_c"], float)
    bg_ds = bg - dark
    s_ds = s - dark

    with np.errstate(divide="ignore", invalid="ignore"):
        abs_straight = np.log10(bg_ds / s_ds)           # = +absorbance
    abs_straight[~np.isfinite(abs_straight)] = np.nan

    bg_ch = defringe_channel(wl, bg, width_frac, nt_min_nm, nt_max_nm,
                             pvalue_max)
    s_ch = defringe_channel(wl, s, width_frac, nt_min_nm, nt_max_nm,
                            pvalue_max)
    has_notch = bg_ch["applied"] or s_ch["applied"]

    bg_for_abs = (bg_ch["clean"] - dark) if bg_ch["applied"] else bg_ds
    s_for_abs = (s_ch["clean"] - dark) if s_ch["applied"] else s_ds
    with np.errstate(divide="ignore", invalid="ignore"):
        abs_notch = np.log10(bg_for_abs / s_for_abs)
    abs_notch[~np.isfinite(abs_notch)] = np.nan

    # Un-notched channels write a blank column (all-NaN -> "").
    bg_notch_col = bg_ch["clean"] if bg_ch["applied"] else np.full_like(wl, np.nan)
    s_notch_col = s_ch["clean"] if s_ch["applied"] else np.full_like(wl, np.nan)

    header = list(_NOTCH_CSV_BASE)
    cols = [wl, dark, bg, s, abs_straight]
    if has_notch:
        header += _NOTCH_CSV_NOTCH
        cols += [bg_notch_col, s_notch_col, abs_notch]

    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, _result_stem(result) + "_absorbance_notch.csv")
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for row in zip(*cols):
            w.writerow(["" if (isinstance(v, float) and np.isnan(v)) else v
                        for v in row])
    return path
