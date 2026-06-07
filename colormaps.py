"""
colormaps.py  --  colour selection for the DAC quick-look plot.

Continuous maps colour each trace by PRESSURE VALUE (same pressure -> same shade).
Categorical maps colour by PRESSURE RANK (adjacent traces maximally distinct),
matching the Igor v4 policy.

Crameri scientific maps (batlow, roma, hawaii, lajolla) come from the optional
'cmcrameri' package. If it is not installed, those names fall back to a close
matplotlib map and FALLBACKS records the substitution so the GUI can note it.

NQT / Lee Lab -- Jun 2026
"""

import numpy as np
import matplotlib.cm as mpl_cm
from matplotlib.colors import to_rgba

FALLBACKS = {}

try:
    from cmcrameri import cm as _cmc
    _CRAMERI = {"batlow": _cmc.batlow, "roma": _cmc.roma,
                "hawaii": _cmc.hawaii, "lajolla": _cmc.lajolla}
    HAVE_CRAMERI = True
except Exception:
    _CRAMERI = {}
    HAVE_CRAMERI = False
    FALLBACKS = {"batlow": "viridis", "roma": "turbo",
                 "hawaii": "plasma", "lajolla": "inferno"}

# Categorical palettes (hex), order chosen for adjacent-trace contrast
_OKABE_ITO = ["#000000", "#E69F00", "#56B4E9", "#009E73",
              "#F0E442", "#0072B2", "#D55E00", "#CC79A7"]
_TOL_BRIGHT = ["#4477AA", "#EE6677", "#228833", "#CCBB44",
               "#66CCEE", "#AA3377", "#BBBBBB"]
_TOL_MUTED = ["#332288", "#88CCEE", "#44AA99", "#117733", "#999933",
              "#DDCC77", "#CC6677", "#882255", "#AA4499"]

_CONTINUOUS = [
    # perceptually uniform (recommended for publication)
    "batlow", "roma", "hawaii", "lajolla",
    "viridis", "inferno", "plasma", "magma", "cividis", "turbo",
    # grayscale + single-hue sequential
    "Greys", "gray", "bone", "copper", "hot", "afmhot", "cubehelix",
    "Blues", "Greens", "Oranges", "Reds", "Purples",
    # multi-hue sequential
    "YlOrRd", "YlGnBu", "BuPu", "GnBu", "PuBuGn", "gist_earth", "terrain",
    # diverging
    "coolwarm", "RdBu", "RdYlBu", "Spectral", "bwr", "seismic",
    "PiYG", "PRGn", "BrBG",
    # cyclic / misc
    "twilight", "hsv", "nipy_spectral"]
_CATEGORICAL = {"batlowS": None, "tab10": None, "tab20": None,
                "okabeito": _OKABE_ITO, "tolbright": _TOL_BRIGHT,
                "tolmuted": _TOL_MUTED}


def available():
    """Ordered list of colormap names for the dropdown."""
    return _CONTINUOUS + list(_CATEGORICAL)


def is_categorical(name):
    return name in _CATEGORICAL


def _continuous_cmap(name):
    if name in _CRAMERI:
        return _CRAMERI[name]
    if name in FALLBACKS:
        return mpl_cm.get_cmap(FALLBACKS[name])
    return mpl_cm.get_cmap(name)


def _categorical_colors(name, n):
    if name == "okabeito":
        return _OKABE_ITO
    if name == "tolbright":
        return _TOL_BRIGHT
    if name == "tolmuted":
        return _TOL_MUTED
    if name == "tab10":
        return [mpl_cm.get_cmap("tab10")(i % 10) for i in range(max(n, 1))]
    if name == "tab20":
        return [mpl_cm.get_cmap("tab20")(i % 20) for i in range(max(n, 1))]
    if name == "batlowS":  # sample batlow at n discrete levels
        cmap = _continuous_cmap("batlow")
        if n <= 1:
            return [cmap(0.5)]
        return [cmap(i / (n - 1)) for i in range(n)]
    return [mpl_cm.get_cmap("tab10")(i % 10) for i in range(max(n, 1))]


def color_for(name, pressure_val, pmin, pmax, rank, n_traces):
    """
    RGBA for one trace.

    Continuous: by pressure value, normalised to [pmin, pmax].
    Categorical: by pressure rank (0-based) among the shown traces.
    """
    if is_categorical(name):
        cols = _categorical_colors(name, n_traces)
        c = cols[rank % len(cols)]
        return to_rgba(c)
    cmap = _continuous_cmap(name)
    # A reversed range is signalled by pmin > pmax (callers swap the bounds).
    reversed_ = pmin > pmax
    lo, hi = (pmax, pmin) if reversed_ else (pmin, pmax)
    if hi > lo:
        frac = (pressure_val - lo) / (hi - lo)
        if reversed_:
            frac = 1.0 - frac
    else:
        frac = 0.5
    return cmap(float(np.clip(frac, 0.0, 1.0)))
