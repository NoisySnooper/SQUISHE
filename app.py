"""
app.py  --  Beamline DAC Data Tool  (Concatenator / Absorbance Calculator / Plotter)

GUI front end for the NSLS-II 22-IR-1 visible-absorption quick-look workflow.
Pick an input folder of raw segments -> Run -> per-pressure absorbance CSVs
(written to an auto-named subfolder inside your output folder) + an interactive
overlay/waterfall plot. Stage 1 only: no defringe/notch/thickness.

Module map (so this file is easy to navigate and edit):
  - helpers          : unit conversions, INFO_TEXT, small utilities
  - class App
      __init__/settings ............ window setup, remembered folders
      layout (_build_*) ............ top bar, resizable panes, the 3 panels
      actions ...................... browse, Run, logging, trace list, D toggles
      drawing ...................... _redraw dispatch -> overlay / waterfall / inspect
      panels/dialogs ............... smoothing settings, presets, exports
  - main / --selftest

Branches: C = compression, D = decompression. D is auto-detected for the ten
historical experiments (decomp.py), from an optional _C/_D filename tag, or set
by hand per trace. D traces are dashed; legend is ordered C low->high, D high->low.

NQT / Lee Lab -- Jun 2026
"""

import os
import sys
import re
import io
import json
import datetime
import traceback
import threading
import queue
import matplotlib
matplotlib.use("TkAgg")
import numpy as np
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from tkinter import font as tkfont
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import (
    FigureCanvasTkAgg, NavigationToolbar2Tk)
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401 (enables 3d projection)

import engine
import smoothing
import defringe
import colormaps
import decomp

try:
    import sv_ttk            # modern Win11-style theme + dark mode (optional)
    _HAVE_SVTTK = True
except Exception:
    _HAVE_SVTTK = False

EV_NM = 1239.84193                      # E[eV] = EV_NM / wavelength[nm]
TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(TOOL_DIR, ".quicklook_settings.json")
FONTS = ["Jost", "Segoe UI", "DejaVu Sans", "DejaVu Serif",
         "Times New Roman", "Arial", "Calibri", "Cambria", "Georgia",
         "Consolas"]
LEGEND_LOCS = ["best", "upper right", "upper left", "lower left",
               "lower right", "center right", "outside right"]

APP_VERSION = "v1.4.2"
APP_CODENAME = "Olivine"

# ---------------------------------------------------------------------------
# Brand. The application's name lives HERE and only here: change these
# strings and every title, wordmark, and About line follows (design spec:
# DESIGN_SQUISHE.md).
# ---------------------------------------------------------------------------
BRAND = {
    "name": "SQUISHE",
    "wordmark": "squishe",          # lowercase display form (header)
    "dot": ".",                     # accent-colored period after the wordmark
    "expansion": "Spectral QUick-look for In-Situ High-pressure Experiments",
    "subtitle": "Concatenator \u00b7 Absorbance Calculator \u00b7 Plotter",
    "org": "NSLS-II 22-IR-1  --  Dr. Lee's Lab",
}
APP_TITLE = BRAND["name"]          # window title; org details in About

# Brand typeface: Jost (OFL, shipped in fonts/), loaded privately at startup;
# falls back through the geometric faces Windows may already have. UI_FONT is
# resolved once in App.__init__ after the Tk root exists.
FONT_DIR = os.path.join(TOOL_DIR, "fonts")
UI_FONT = "Segoe UI"
UI_FONT_SEMI = "Segoe UI"


def _register_brand_fonts():
    """Load the shipped Jost weights for THIS process only (FR_PRIVATE: no
    installation, nothing persists) and register them with matplotlib.
    Returns the number of GDI registrations (0 = fall back gracefully)."""
    files = ("Jost-Regular.ttf", "Jost-Medium.ttf",
             "Jost-SemiBold.ttf", "Jost-Bold.ttf")
    n = 0
    try:
        import ctypes
        for f in files:
            p = os.path.join(FONT_DIR, f)
            if os.path.exists(p):
                n += ctypes.windll.gdi32.AddFontResourceExW(p, 0x10, 0)
    except Exception:
        return 0
    if n:
        try:
            from matplotlib import font_manager as _fm
            for f in files:
                p = os.path.join(FONT_DIR, f)
                if os.path.exists(p):
                    _fm.fontManager.addfont(p)
        except Exception:
            pass
    return n


def _resolve_ui_font():
    """Pick the best available geometric face (needs a live Tk root)."""
    try:
        fams = set(tkfont.families())
    except Exception:
        return "Segoe UI", "Segoe UI"
    for fam in ("Jost", "Century Gothic", "Bahnschrift"):
        if fam in fams:
            semi = "Jost SemiBold" if (fam == "Jost"
                                       and "Jost SemiBold" in fams) else fam
            return fam, semi
    return "Segoe UI", "Segoe UI"

INFO_TEXT = (
    "ABSORBANCE\n"
    "  A = -log10[(Sample - Dark) / (Background - Dark)]\n\n"
    "X-AXIS UNITS\n"
    "  wavenumber[cm^-1] = 1e7 / wavelength[nm]\n"
    "  energy[eV]        = 1239.84 / wavelength[nm]\n\n"
    "FILENAME FORMAT\n"
    "  vis_{DAC}_{Sample}[_{Pressure}][_bg|_s][_C|_D][_2|_3][.{seq}]\n"
    "    no suffix = dark,  _bg = background,  _s = sample\n"
    "    _C/_D = optional compression/decompression tag (auto-detects branch)\n"
    "    _2/_3 = retake (latest is used),  seq = 001..004; omit .seq for\n"
    "      single-stitch files (treated as one segment)\n"
    "    (_C/_D and _2/_3 may appear in either order)\n"
    "    Pressure uses 'p' for the decimal: 1p39 = 1.39 GPa. 0 GPa is\n"
    "      allowed; a missing pressure field is assumed 0 GPa (noted in log)\n"
    "  Incomplete channel sets (e.g. bg only) load as raw counts with a\n"
    "  channel tag in the trace name; absorbance needs S + B + D.\n"
    "  Files that do not match are skipped (see the log above).\n\n"
    "BRANCHES (C / D)\n"
    "  C = compression (solid),  D = decompression (dashed).\n"
    "  Auto for known experiments and _C/_D tags; toggle D per trace otherwise.\n\n"
    "OUTPUT\n"
    "  CSVs go to an auto-named subfolder inside the output folder you pick,\n"
    "  e.g.  <output>/<inputname>_absorbance/ -- no manual sorting needed."
)

QUICK_START = (
    "QUICK START\n\n"
    "1. Pick the Input folder of raw segment files (vis_..._001..004).\n"
    "   Different filename scheme? Click 'Name format' under the folder\n"
    "   box and teach it from one example file.\n"
    "2. Pick an Output folder, then click Run. The tool joins the four\n"
    "   grating segments per measurement, computes absorbance, and writes\n"
    "   one CSV per pressure to <output>/<inputname>_absorbance/.\n"
    "3. All pressures plot automatically. Format with the right-side panels.\n"
    "4. Publication figure: pick a Journal preset (Nature / Science / RSI /\n"
    "   APS / Elsevier) in the Figure box - it sets the column width AND the\n"
    "   house style (font, sizes, line weight, spines, DPI) in one pick.\n"
    "   Tick 'Preview at export size (WYSIWYG)' to see the true printed\n"
    "   proportions and text size on screen.\n"
    "5. Save plot as PDF or SVG (vector) or PNG at the chosen DPI.\n\n"
    "TABS (top of the plot): each tab is a separate session. '+' opens a\n"
    "  blank tab to Run or load another dataset for side-by-side comparison.\n"
    "  Double-click to rename; Ctrl+T new, Ctrl+W close, Ctrl+Tab to cycle.\n"
    "DATA TABLE (button, bottom-right, or Ctrl+D): the raw numbers for the\n"
    "  selected trace; drag its top edge to resize, copy as TSV, or open in\n"
    "  Excel.\n"
    "'Load previous run' (left) reopens a finished output folder instantly;\n"
    "  recent runs are also listed on a blank tab.\n\n"
    "Hover any control for a tip (toggle with the Helper switch); F1 lists\n"
    "the keyboard shortcuts. Folders, theme, window size, default colormap,\n"
    "notes, and presets are remembered between launches."
)

PANEL_GUIDE = (
    "PANEL GUIDE\n\n"
    "TABS & SESSIONS  (row above the plot)\n"
    "  Each tab is an independent session with its own data, folders,\n"
    "    settings, and undo history. '+' opens a blank tab; double-click a\n"
    "    tab to rename; the x or middle-click closes it. Running or loading\n"
    "    data names the tab after the folder. NUKE clears all tabs to one.\n\n"
    "DATA TABLE  (button at the bottom-right, or Ctrl+D)\n"
    "  A spreadsheet of the selected trace: wavelength, wavenumber,\n"
    "    absorbance, and the raw dark/background/sample counts, plus optional\n"
    "    defringed and smoothed columns. Drag its top edge to resize. 'Copy\n"
    "    all (TSV)' pastes into Excel; 'Open in Excel' writes a temp CSV.\n\n"
    "NAME FORMAT  (left panel, under the input folder)\n"
    "  How filenames are understood. The built-in profile reads the classic\n"
    "    vis_{DAC}_{Sample}[_{Pressure}][_bg|_s][_C|_D][_2|_3][.{seq}] names.\n"
    "  Different scheme? Open 'Name format', pick an example filename, and\n"
    "    label each piece (DAC / sample / pressure / role / ...). The whole\n"
    "    folder previews live, green = parsed, red = skipped. Save it as a\n"
    "    named profile; Run then uses it.\n"
    "  Stubborn files: double-click one in the preview to fix its fields by\n"
    "    hand, or exclude it. Fixes are remembered per folder.\n\n"
    "PLOT MODE\n"
    "  Overlay all pressures - every selected pressure on one set of axes.\n"
    "  Inspect one pressure - one run: Sample/Background/Dark counts on the\n"
    "    left axis and its Absorbance on the right. Use it to sanity-check a\n"
    "    run (good signal, background above sample) before trusting absorbance.\n"
    "  Overlay Y - plot absorbance or a raw channel (sample/background/dark).\n"
    "  Channels S/B/D/Abs - which curves to draw in Inspect mode.\n\n"
    "VIEW  (bottom of '2D plot options')\n"
    "  Pan pad (hold to repeat) + Fit center button; Fit X / Fit Y refit\n"
    "    one axis. Zoom +/- about the view center, on X, Y, or both.\n"
    "  Keyboard: arrow keys pan (5% steps), + and - zoom, 0 fits (ignored\n"
    "    while typing in a box). Drag-box zoom and scroll still work.\n"
    "  In 3D ridge mode the same keys drive the camera: arrows orbit\n"
    "    (3 degrees per step), + / - zoom, 0 resets. Sliders follow.\n\n"
    "AXIS\n"
    "  Four dropdowns, one per axis:\n"
    "  X axis - Wavelength / Wavenumber / Photon energy; same data,\n"
    "    converted on the fly (wn = 1e7/nm, eV = 1239.84/nm).\n"
    "  Y axis - absorbance or a raw counts channel (overlay mode).\n"
    "  Top axis - mirror a 2nd unit across the top.\n"
    "  Right axis - mirror the left Y, or % transmittance.\n"
    "  Flip X / Flip Y - reverse either axis.\n"
    "  Axis line - thickness (points) of the axis lines / spines.\n"
    "  Label gap - X and Y distance (points) from axis to its label.\n\n"
    "AXIS LIMITS\n"
    "  Auto fits the data and fills the boxes with the values in use. Zooming\n"
    "  the plot (drag a box, scroll wheel, or the toolbar) turns Auto off and\n"
    "  fills the boxes, so the zoom sticks; 'Reset axes' clears the boxes and\n"
    "  re-enables Auto. You can also type min/max and click Apply limits.\n\n"
    "DISPLAY\n"
    "  Colormap - color scale across pressures. Crameri maps (batlow, roma,\n"
    "    hawaii, lajolla) are perceptually uniform and color-blind safe.\n"
    "    'Set default' remembers your pick. Reverse flips low<->high color.\n\n"
    "WATERFALL\n"
    "  off - shared baseline. 2D stacked - shift each pressure up by\n"
    "    Offset/step. 3D ridge - x=wavelength, depth=pressure, height=abs.\n\n"
    "3D RIDGE OPTIONS\n"
    "  Even rank spacing - equal depth steps (ticks still real GPa); keeps\n"
    "    crowded pressures legible.\n"
    "  3D look - walls + traces, walls only, or a clean traces-only joyplot;\n"
    "    'Color traces by colormap' colours the outlines.\n"
    "  Stretch X/Y/Z - stretch the box along an axis for visual clarity\n"
    "    without respacing the data (Y fans out crowded ridges).\n"
    "  View presets + Elev/Azim/Zoom - camera. Project - faint shadows on\n"
    "    the back wall / floor. Fill opacity - wall transparency.\n"
    "  Z clip - blank = the full data range. 'Clip Z spikes (99th pct)'\n"
    "    caps the auto top so saturated spikes don't blow out the scale;\n"
    "    typed Z limits always win.\n\n"
    "DEFRINGE\n"
    "  Enable (FFT notch) - remove diamond-anvil interference fringes by\n"
    "    notching the dominant auto-detected fringe out of the raw Sample and\n"
    "    Background counts, then recomputing absorbance. Notch width default\n"
    "    15%; sliding it to 0 disables defringe, any nonzero width enables it.\n"
    "    n*t min/max and p-value max expose the detection gates (defaults\n"
    "    15-100 um, 1e-4). When enabled, Run also writes\n"
    "    {stem}_absorbance_notch.csv files; 'Export defringed CSV' writes\n"
    "    them anytime.\n\n"
    "SMOOTHING\n"
    "  Show smoothed/raw. 'Smoothing settings' exposes the 5-step filter\n"
    "  (cutoff, density, Hampel, split Savitzky-Golay, jump) matching the\n"
    "  established lab pipeline.\n\n"
    "VERTICAL MARKERS\n"
    "  Vertical lines at given wavelengths (comma-separated), e.g. an edge.\n\n"
    "TITLE / LABELS / LEGEND / COLORBAR\n"
    "  Edit title and axis labels. Legend on/off + location ('outside right'\n"
    "    keeps it off the data) + columns + title.\n"
    "  Colorbar - a continuous pressure scale (labeled 'Pressure (GPa)').\n"
    "  'Auto: colorbar for many traces' (off by default) - when on, a\n"
    "    continuous colormap with >10 traces uses a colorbar instead of a\n"
    "    large legend that would hide the data. A categorical colormap always\n"
    "    keeps a discrete legend.\n\n"
    "FIGURE  (journal presets + export)\n"
    "  Journal preset - sets column width AND house style in one pick:\n"
    "    Nature 89/183 mm and Science 5.7/12.1/18.4 cm (sans-serif), RSI/AIP\n"
    "    3.37/6.69 in, APS 3.4/7.0 in (serif), Elsevier 90/190 mm. Also sets\n"
    "    font, sizes, line weight, thin spines, ticks-in, and DPI.\n"
    "  W x H in / Apply - custom size. 'Preview at export size (WYSIWYG)'\n"
    "    renders the on-screen figure at the exact export dimensions so you\n"
    "    see the true printed proportions (off = fill the window).\n"
    "  'Apply clean style' - no grid, thin spines, ticks in (font-agnostic).\n"
    "  Transparent / Tight bbox / Pad / Face - export page options.\n"
    "  (Font family and title/label/tick sizes are in the Style box.)\n\n"
    "PRESETS\n"
    "  Save the whole control state under a name; reload from the dropdown.\n\n"
    "EXPORT\n"
    "  Save plot (PNG/PDF/SVG/EPS/TIFF; vector embeds fonts). Batch PNG =\n"
    "  one image per shown trace. Export smoothed CSV = wl/cm^-1/eV + raw +\n"
    "  smoothed columns."
)

SHORTCUTS_TEXT = (
    "KEYBOARD SHORTCUTS\n\n"
    "Ctrl+S         Save plot\n"
    "Ctrl+Z         Undo\n"
    "Ctrl+Y         Redo  (also Ctrl+Shift+Z)\n"
    "Ctrl+R         Reset 3D view\n"
    "Ctrl+Shift+C   Copy figure to clipboard\n"
    "Ctrl+T         New tab (blank session)\n"
    "Ctrl+W         Close the current tab\n"
    "Ctrl+Tab       Next tab  (Ctrl+Shift+Tab = previous)\n"
    "Ctrl+D         Toggle the raw data table\n"
    "Arrow keys     Pan the 2D view / orbit the 3D camera\n"
    "+ / -          Zoom (2D about center; 3D camera)\n"
    "0              Fit the 2D view / reset the 3D camera\n"
    "1 / 2 / 3      Waterfall: off / 2D stacked / 3D ridge\n"
    "[  /  ]        Previous / next colormap\n"
    "F1             This shortcuts list\n\n"
    "(Number and bracket keys are ignored while typing in a box.)"
)

REF_VIEWS = {"Absorbance reference": INFO_TEXT,
             "Panel guide": PANEL_GUIDE,
             "Quick start": QUICK_START,
             "Keyboard shortcuts": SHORTCUTS_TEXT}


class Tooltip:
    """Hover tooltip for any widget: shows after a short delay so tips do
    not flash while the mouse crosses the panel. The top-bar Helper switch
    flips the class-wide `enabled` flag to silence every tip at once."""
    enabled = True
    DELAY_MS = 450

    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip = None
        self._job = None
        widget.bind("<Enter>", self._schedule)
        widget.bind("<Leave>", self._hide)
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _=None):
        if not Tooltip.enabled or not self.text:
            return
        self._cancel()
        self._job = self.widget.after(self.DELAY_MS, self._show)

    def _cancel(self):
        if self._job is not None:
            try:
                self.widget.after_cancel(self._job)
            except Exception:
                pass
            self._job = None

    def _show(self, _=None):
        self._job = None
        if self.tip or not self.text or not Tooltip.enabled:
            return
        x = self.widget.winfo_rootx() + 18
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.configure(bg="#555555")    # 1px frame around the tip
        tk.Label(self.tip, text=self.text, justify="left", wraplength=300,
                 background="#2b2b2b", foreground="#f0f0f0", relief="flat",
                 font=(UI_FONT, 10), padx=8, pady=5).pack(padx=1, pady=1)
        # clamp to the screen so tips near the right/bottom edge stay whole
        self.tip.update_idletasks()
        tw, th = self.tip.winfo_reqwidth(), self.tip.winfo_reqheight()
        sw, sh = self.tip.winfo_screenwidth(), self.tip.winfo_screenheight()
        if x + tw > sw - 8:
            x = max(8, sw - tw - 8)
        if y + th > sh - 8:
            y = self.widget.winfo_rooty() - th - 6
        self.tip.wm_geometry("+%d+%d" % (x, y))

    def _hide(self, _=None):
        self._cancel()
        if self.tip:
            self.tip.destroy(); self.tip = None


class BrandScale(tk.Canvas):
    """Brand slider: rounded trough, filled progress, ROUND knob (the
    classic Scale's square knob read as ambiguous; sv_ttk's trough images
    cannot follow the theme). Drag anywhere or click to jump."""

    def __init__(self, parent, from_=0.0, to=1.0, variable=None,
                 command=None, **kw):
        super().__init__(parent, height=20, highlightthickness=0, bd=0,
                         bg=kw.get("bg", "#f1eee6"))
        self.lo, self.hi = float(from_), float(to)
        self.var = variable
        self.command = command
        self._trough = "#d9d5c9"
        self._fillc = "#1D3EC0"
        self._knob = "#1D3EC0"
        self._ring = "#f1eee6"
        self._hov = None
        self._hot = False
        self._tr = None
        self.configure(cursor="hand2")
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Button-1>", self._jump)
        self.bind("<B1-Motion>", self._jump)
        self.bind("<Enter>", lambda e: self._set_hot(True))
        self.bind("<Leave>", lambda e: self._set_hot(False))
        if variable is not None:
            self._tr = variable.trace_add("write", lambda *a: self._draw())
        self._draw()

    def destroy(self):
        try:
            if self.var is not None and self._tr:
                self.var.trace_remove("write", self._tr)
        except Exception:
            pass
        super().destroy()

    def retint(self, bg, trough, fill, knob, ring, hov=None):
        try:
            self.configure(bg=bg)
        except tk.TclError:
            return
        self._trough, self._fillc = trough, fill
        self._knob, self._ring = knob, ring
        self._hov = hov
        self._draw()

    def _set_hot(self, hot):
        self._hot = hot
        self._draw()

    def _frac(self):
        try:
            v = float(self.var.get())
        except Exception:
            v = self.lo
        span = (self.hi - self.lo) or 1.0
        return min(1.0, max(0.0, (v - self.lo) / span))

    def _jump(self, e):
        w = max(self.winfo_width(), 1)
        pad = 9
        f = min(1.0, max(0.0, (e.x - pad) / max(w - 2 * pad, 1)))
        v = self.lo + f * (self.hi - self.lo)
        if self.var is not None:
            self.var.set(v)
        if self.command:
            self.command(v)

    def _draw(self):
        try:
            self.delete("all")
        except tk.TclError:
            return
        w = max(self.winfo_width(), 30)
        pad, cy, r = 9, 10, 7
        f = self._frac()
        kx = pad + f * (w - 2 * pad)
        self.create_line(pad, cy, w - pad, cy, width=4, fill=self._trough,
                         capstyle="round")
        if kx > pad + 1:
            self.create_line(pad, cy, kx, cy, width=4, fill=self._fillc,
                             capstyle="round")
        # square knob: the brand's marker shape, unambiguous to grab
        kc = (self._hov or self._knob) if self._hot else self._knob
        self.create_rectangle(kx - r, cy - r, kx + r, cy + r, fill=kc,
                              outline=self._ring, width=2)


def unit_x(result, unit):
    """X array for a result in the chosen unit ('wl' nm, 'wn' cm^-1, 'ev')."""
    if unit == "wn":
        return result["wn"]
    if unit == "ev":
        return np.where(result["wl"] > 0, EV_NM / result["wl"], np.nan)
    return result["wl"]


def unit_label(unit):
    return {"wl": "Wavelength (nm)", "wn": "Wavenumber (cm$^{-1}$)",
            "ev": "Photon energy (eV)"}[unit]


def _conv(frm, to):
    """forward/inverse functions mapping primary-axis values to a top-axis unit.

    nm_to and to_nm have identical bodies on purpose: every unit here maps
    to/from nm by the same self-inverse map (identity, or x -> C/x), so one
    formula converts in either direction."""
    def nm_to(v, u):
        with np.errstate(divide="ignore", invalid="ignore"):
            if u == "wl": return v
            if u == "wn": return 1e7 / v
            return EV_NM / v
    def to_nm(v, u):
        with np.errstate(divide="ignore", invalid="ignore"):
            if u == "wl": return v
            if u == "wn": return 1e7 / v
            return EV_NM / v
    fwd = lambda x: nm_to(to_nm(x, frm), to)
    inv = lambda x: nm_to(to_nm(x, to), frm)
    return fwd, inv


class App:
    def __init__(self, root):
        self.root = root
        self.results = []
        self.trace_vars = {}      # label -> BooleanVar (show/hide)
        self.dvars = {}           # label -> BooleanVar (is decompression)
        self.smooth_cache = {}
        self.smooth_params = dict(smoothing.DEFAULTS)
        self.notch_cache = {}     # label -> {'absorbance','sample','background'}
        self.last_out_dir = None
        self.settings = self._load_settings()
        _tm = self.settings.get("theme",
                                "dark" if self.settings.get("dark") else "black")
        if _tm not in ("light", "dark", "black"):
            _tm = "light"
        self.theme_mode = tk.StringVar(value=_tm)
        self.dark_mode = tk.BooleanVar(value=(_tm != "light"))
        # reduce-motion: kill switch for the small one-shot UI animations
        self.reduce_motion = tk.BooleanVar(
            value=bool(self.settings.get("reduce_motion", False)))
        self._anim_jobs = {}
        self._tk_widgets = []      # non-ttk widgets to recolor with the theme
        self._slider_entries = []  # (var, entry, fmt) slider<->box sync
        # action log stays quiet while the UI builds (control traces fire
        # during construction); switched on at the end of __init__
        self._action_log_on = False
        self._undo_stack = []
        self._redo_stack = []
        self._restoring = False

        # brand typography: private-load Jost and route the named fonts
        # through it so every ttk widget follows (DESIGN_SQUISHE.md). Must
        # run before any widget is built.
        global UI_FONT, UI_FONT_SEMI
        _register_brand_fonts()
        UI_FONT, UI_FONT_SEMI = _resolve_ui_font()
        self._body_size = int(self.settings.get("ui_font_size", 9))
        _bs = self._body_size
        for _nf, _sz in (("TkDefaultFont", _bs), ("TkTextFont", _bs),
                         ("TkMenuFont", _bs), ("TkHeadingFont", _bs),
                         ("TkTooltipFont", _bs - 1),
                         ("TkCaptionFont", _bs - 1)):
            try:
                tkfont.nametofont(_nf).configure(family=UI_FONT, size=_sz)
            except tk.TclError:
                pass

        root.title(APP_TITLE)
        root.geometry(self.settings.get("geometry", "1400x860"))
        root.minsize(1024, 640)     # below this the three panes are unusable
        try:                                # window / taskbar icon
            # _MEIPASS = the PyInstaller bundle dir when frozen; the script
            # dir otherwise. icon.png is shipped as a data file in both.
            _base = getattr(sys, "_MEIPASS",
                            os.path.dirname(os.path.abspath(__file__)))
            _ico = os.path.join(_base, "icon.png")
            self._app_icon = tk.PhotoImage(file=_ico)
            root.iconphoto(True, self._app_icon)
        except Exception:
            pass                            # missing icon never blocks launch
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._init_theme()

        self._build_top()
        self._build_panes()
        self._center_titles()
        self._refresh_fallback_note()
        self._refresh_presets()
        self._defaults = {k: v.get() for k, v in self._preset_registry().items()}
        self._recolor_tk()
        self._bind_shortcuts()
        self._push_undo("initial")
        self._update_undo_buttons()
        self._redraw()

        # start logging user actions only now, with a friendly first line
        self._action_log_on = True
        if not self.log.get("1.0", "end-1c").strip():
            self._logline("Ready. Pick an input folder and press Run, or "
                          "click 'Load previous run\u2026' to reopen a finished "
                          "output.")
        self._set_run_state("Ready", "#2a8a4a")
        self._apply_brand()
        # DWM ignores caption colors set before the window is
        # composited; re-apply once mapped and shortly after
        root.bind("<Map>", lambda e: self._apply_titlebar(), add="+")
        root.after(400, self._apply_titlebar)

        # multi-tab sessions: one shared widget tree, per-tab state swapped
        # in and out (see _capture_session / _load_session). Tab 0 captures
        # the freshly-built state.
        self.sessions = [self._capture_session("Session 1")]
        self.active = 0
        self._render_tabs()

    def _bind_shortcuts(self):
        b = self.root.bind
        b("<Control-s>", lambda e: self._save_plot())
        b("<Control-z>", lambda e: self._undo())
        b("<Control-y>", lambda e: self._redo())
        b("<Control-Z>", lambda e: self._redo())   # Ctrl+Shift+Z
        b("<Control-r>", lambda e: self._reset_3d_view())
        b("<Control-Shift-C>", lambda e: self._copy_figure())
        b("<F1>", lambda e: self._show_shortcuts_popup())
        b("<Key-1>", lambda e: self._hotkey_wf("off"))
        b("<Key-2>", lambda e: self._hotkey_wf("2D stacked"))
        b("<Key-3>", lambda e: self._hotkey_wf("3D ridge"))
        b("<bracketleft>", lambda e: self._cycle_cmap(-1))
        b("<bracketright>", lambda e: self._cycle_cmap(1))
        b("<Control-Tab>", lambda e: self._cycle_tab(1))
        b("<Control-Shift-Tab>", lambda e: self._cycle_tab(-1))
        b("<Control-ISO_Left_Tab>", lambda e: self._cycle_tab(-1))
        b("<Control-t>", lambda e: (self._new_session(), "break")[1])
        b("<Control-w>", lambda e: (self._close_session(self.active),
                                    "break")[1])
        b("<Control-d>", lambda e: self._toggle_drawer())
        # 2D view: arrows pan, +/- zoom, 0 fits (all no-ops while typing)
        b("<Left>", lambda e: self._hot_pan(-1, 0))
        b("<Right>", lambda e: self._hot_pan(+1, 0))
        b("<Up>", lambda e: self._hot_pan(0, +1))
        b("<Down>", lambda e: self._hot_pan(0, -1))
        b("<plus>", lambda e: self._hot_zoom(0.9))
        b("<equal>", lambda e: self._hot_zoom(0.9))
        b("<KP_Add>", lambda e: self._hot_zoom(0.9))
        b("<minus>", lambda e: self._hot_zoom(1.0 / 0.9))
        b("<KP_Subtract>", lambda e: self._hot_zoom(1.0 / 0.9))
        b("<Key-0>", lambda e: self._hot_fit())

    def _typing_in_box(self):
        """True when keyboard focus is in a widget that consumes keystrokes
        itself (text entry, list/tree navigation, slider arrows), so global
        single-key shortcuts must not fire (the promise in SHORTCUTS_TEXT)."""
        w = self.root.focus_get()
        return w is not None and w.__class__.__name__ in (
            "Entry", "TEntry", "Combobox", "TCombobox",
            "Spinbox", "TSpinbox", "Text",
            "Scale", "TScale", "Listbox", "Treeview")

    def _hotkey_wf(self, mode):
        if self._typing_in_box():
            return
        self.wf_mode.set(mode)

    def _cycle_cmap(self, step):
        if self._typing_in_box():
            return
        try:
            maps = colormaps.available()
            i = (maps.index(self.cmap.get()) + step) % len(maps)
            self.cmap.set(maps[i]); self._log_action("Colormap: " + maps[i])
        except Exception:
            pass

    def _show_shortcuts_popup(self):
        messagebox.showinfo("Keyboard shortcuts", SHORTCUTS_TEXT)

    def _toggle_tooltips(self):
        """Helper switch: turn every hover tooltip on or off (remembered)."""
        Tooltip.enabled = bool(self.tooltips_on.get())
        self.settings["tooltips_on"] = Tooltip.enabled
        self._save_settings()
        self._log_action("Helper tips %s"
                         % ("on" if Tooltip.enabled else "off"))

    def _on_close(self):
        self._save_user_notes()
        try:
            self.settings["geometry"] = self.root.winfo_geometry()
            self._save_settings()
        except Exception:
            pass
        self.root.destroy()

    def _themes(self):
        """Accent themes. light/dark/black are built in and untouched; the rest
        tint the chrome only and keep the plot neutral unless 'Tint plot' is on."""
        return {
            "forest":    dict(base="light", bg="#eaf1e8", fg="#1b2e1b",
                              field="#ffffff", accent="#2e7d32"),
            "rose":      dict(base="light", bg="#fbeef3", fg="#3a1b2a",
                              field="#ffffff", accent="#c2185b"),
            "ocean":     dict(base="dark",  bg="#0f2a3a", fg="#dbeefb",
                              field="#13344a", accent="#26c6da"),
            "solarized": dict(base="light", bg="#fdf6e3", fg="#586e75",
                              field="#ffffff", accent="#b58900"),
            "rainbow":   dict(base="dark", bg="#0c0c10", fg="#ececef",
                              field="#16161d", accent="#c64fd0", rainbow=True),
        }

    def _brand(self):
        """The SQUISHE triad for the active theme (DESIGN_SQUISHE.md #3):
        ac1 primary actions, ac2 signal (dot/carets/markers), ac3 highlight,
        ink structural rules, hov = ac1 hover shade."""
        T = {
            "light":     ("#1D3EC0", "#E4581C", "#B98A0E", "#171310"),
            "dark":      ("#5B78F2", "#FF7A45", "#EDBB4A", "#E8E4DA"),
            "black":     ("#6C86FF", "#FF8149", "#F2BE3A", "#EFEBE0"),
            "forest":    ("#2E7D32", "#E4581C", "#C9A227", "#1B2E1B"),
            "rose":      ("#C2185B", "#8A63D2", "#DDA523", "#3A1B2A"),
            "ocean":     ("#26C6DA", "#F2984B", "#7FD1AE", "#DBEEFB"),
            "solarized": ("#268BD2", "#CB4B16", "#B58900", "#586E75"),
            "rainbow":   ("#C64FD0", "#E4581C", "#E3A917", "#ECECEF"),
        }
        ac1, ac2, ac3, ink = T.get(self.theme_mode.get(), T["light"])
        return {"ac1": ac1, "ac2": ac2, "ac3": ac3, "ink": ink,
                "hov": self._shade(ac1, 0.82)}

    @staticmethod
    def _shade(hexcol, f):
        """Darken (f<1) or lighten (f>1) a #rrggbb color."""
        try:
            r, g, b = (int(hexcol[i:i + 2], 16) for i in (1, 3, 5))
            return "#%02x%02x%02x" % tuple(max(0, min(255, int(c * f)))
                                           for c in (r, g, b))
        except Exception:
            return hexcol

    def _theme_palette(self):
        """(ui_bg, ui_fg, field_bg, plot_bg, plot_fg) for the active theme."""
        t = self.theme_mode.get()
        if t == "black":
            # true-black chrome and plot. frames/labels are pushed to pure black
            # by the style.configure overrides in _init_theme; field bg stays a
            # hair above black so entry/combo boxes stay visible.
            return ("#000000", "#e8e8e8", "#0d0d0d", "#000000", "#e8e8e8")
        if t == "dark":
            return ("#23252b", "#e6e6e6", "#2c2f37", "#23252b", "#e6e6e6")
        if t == "light":
            # Bauhaus paper (DESIGN_SQUISHE.md): warm workshop ground; fields
            # and the plot page stay white for publication neutrality.
            return ("#f1eee6", "#201b16", "#ffffff", "white", "#1c2530")
        th = self._themes().get(t)
        if th:
            base_dark = th["base"] == "dark"
            follow = getattr(self, "theme_plot_follow", None)
            if follow is not None and follow.get():
                pb, pf = th["bg"], th["fg"]
            else:
                pb, pf = (("#23252b", "#e6e6e6") if base_dark
                          else ("white", "#1c2530"))
            return (th["bg"], th["fg"], th["field"], pb, pf)
        return ("#f3f5f8", "#1c2530", "#ffffff", "white", "#1c2530")

    def _recolor_accents(self, accent=None, rainbow=False):
        """Bauhaus treatment: the square marker carries the signal color
        (via _apply_brand), the collapse caret goes quiet, titles stay ink.
        Rainbow keeps its per-section caret parade."""
        _u, fg, _fl, _pb, _pf = self._theme_palette()
        muted = "#9aa0a6" if self.dark_mode.get() else "#8a8d93"
        pal = ["#e53935", "#fb8c00", "#fdd835", "#43a047", "#1e88e5", "#8e24aa"]
        for i, rec in enumerate(getattr(self, "_collapsibles", [])):
            col = pal[i % len(pal)] if rainbow else muted
            try:
                rec["caret"].configure(foreground=col)
                if rec.get("title_lbl"):
                    rec["title_lbl"].configure(foreground=fg)
            except Exception:
                pass
        if hasattr(self, "title_lbl"):
            try:
                self.title_lbl.configure(foreground=fg)
            except Exception:
                pass

    def _apply_brand(self):
        """Re-tint every brand element from the active theme's triad: the
        wordmark dot (ac2), the 2px section rules (ink), and the primary
        brand buttons (ac1). Called at init end and from _toggle_dark."""
        br = self._brand()
        if getattr(self, "_wm_dot", None) is not None:
            try:
                self._wm_dot.configure(foreground=br["ac2"])
            except tk.TclError:
                pass
        for rec in getattr(self, "_collapsibles", []):
            r = rec.get("rule")
            if r is not None:
                try:
                    r.configure(bg=br["ink"])
                except tk.TclError:
                    pass
        alive = []
        for b in getattr(self, "_brand_btns", []):
            try:
                b.configure(bg=br["ac1"], activebackground=br["hov"],
                            fg="#ffffff", activeforeground="#ffffff",
                            disabledforeground="#cfcfcf")
                alive.append(b)
            except tk.TclError:
                pass                       # button from a closed dialog
        self._brand_btns = alive

        # geometric icon set: regenerate in this theme's ink/accent and
        # re-attach (references live in self._icons; tk needs them held)
        self._make_icons()
        ic = getattr(self, "_icons", {})

        def seticon(btn, name, compound="left"):
            img = ic.get(name)
            if btn is None or img is None:
                return
            try:
                btn.configure(image=img, compound=compound)
            except tk.TclError:
                pass
        seticon(getattr(self, "run_btn", None), "run")
        seticon(getattr(self, "_data_btn", None), "table")
        seticon(getattr(self, "load_prev_btn", None), "prev")
        seticon(getattr(self, "profile_btn", None), "gear")
        for key in ("reset", "pan", "zoom", "save"):
            seticon(getattr(self, "_tb_btns", {}).get(key), key)
        seticon(getattr(self, "_browse_in_btn", None), "folder")
        seticon(getattr(self, "_browse_out_btn", None), "folder")
        seticon(getattr(self, "_openout_btn", None), "folder_open")
        seticon(getattr(self, "_copylog_btn", None), "copy")
        seticon(getattr(self, "_expset_btn", None), "share")
        seticon(getattr(self, "_collapse_btn", None), "chev_up")
        seticon(getattr(self, "_expand_btn", None), "chev_dn")
        seticon(getattr(self, "_resetall_btn", None), "reset")
        self._apply_titlebar()
        self._repaint_svttk_accent()
        if getattr(self, "_hdr_mark_lbl", None) is not None and ic.get("mark"):
            try:
                self._hdr_mark_lbl.configure(image=ic["mark"])
            except tk.TclError:
                pass
        # section geo markers (ac2 squares); quick-access title takes ac3
        for rec in getattr(self, "_collapsibles", []):
            car = ic.get("caret_closed" if rec.get("collapsed")
                         else "caret_open")
            if car is not None:
                try:
                    rec["caret"].configure(image=car, width=16)
                except tk.TclError:
                    pass
            m = rec.get("marker")
            if m is not None:
                img = ic.get("sec::" + rec["key"]) or ic.get("sec_dot")
                try:
                    if img is not None:
                        m.configure(image=img)
                    else:
                        m.configure(foreground=br["ac2"])
                except tk.TclError:
                    pass
        if getattr(self, "_drawer_title", None) is not None:
            try:
                self._drawer_title.configure(foreground=br["ac3"])
            except tk.TclError:
                pass
        for m in getattr(self, "_lf_markers", []):
            try:
                m.configure(foreground=br["ac2"])
            except tk.TclError:
                pass
        self._iconize_buttons()
        try:
            self.log.tag_config("logerr", foreground="#e15b50")
            self.log.tag_config("logwarn", foreground="#c99a2e")
        except (tk.TclError, AttributeError):
            pass
        try:
            _u3, _f3, fld3, _pb3, _pf3 = self._theme_palette()
            stripe = ((self._shade(fld3, 1.35) if self.dark_mode.get()
                       else self._shade(fld3, 0.955))
                      if fld3.startswith("#") else "#f4f2ec")
            self._drawer_tv.tag_configure("even", background=fld3)
            self._drawer_tv.tag_configure("odd", background=stripe)
        except (tk.TclError, AttributeError):
            pass
        uibg2, _fg2, _fl2, _pb2, _pf2 = self._theme_palette()
        if uibg2.startswith("#"):
            r2 = int(uibg2[1:3], 16); g2 = int(uibg2[3:5], 16)
            b2 = int(uibg2[5:7], 16)
            if self.dark_mode.get():
                tr = "#%02x%02x%02x" % (min(255, r2 + 42),
                                        min(255, g2 + 42),
                                        min(255, b2 + 42))
            else:
                tr = self._shade(uibg2, 0.88)
        else:
            tr = "#d9d5c9"
        keep2 = []
        page = uibg2 if uibg2.startswith("#") else "#f1eee6"
        for sc in getattr(self, "_theme_scales", []):
            try:
                sc.retint(bg=page, trough=tr, fill=br["ac1"],
                          knob=br["ac1"], ring=page, hov=br["hov"])
                keep2.append(sc)
            except tk.TclError:
                pass
        self._theme_scales = keep2
        undo = ic.get("undo12")
        keep3 = []
        for rb in getattr(self, "_slider_resets", []):
            try:
                if undo is not None:
                    rb.configure(image=undo)
                keep3.append(rb)
            except tk.TclError:
                pass
        self._slider_resets = keep3
        keep4 = []
        for lab, name in getattr(self, "_slider_iconlabels", []):
            img = ic.get("sl::" + name)
            try:
                if img is not None:
                    lab.configure(image=img, compound="left")
                keep4.append((lab, name))
            except tk.TclError:
                pass
        self._slider_iconlabels = keep4
        if getattr(self, "_qa_title", None) is not None:
            try:
                self._qa_title.configure(foreground=br["ac3"])
            except tk.TclError:
                pass

    def _make_icons(self):
        """The SQUISHE geometric icon set (DESIGN_SQUISHE.md #5): drawn with
        PIL at 2x and cached as PhotoImages, tinted from the active theme.
        White glyphs live on ac1 brand buttons; ink glyphs on plain chrome;
        ac2 is the single allowed accent fill."""
        try:
            from PIL import Image, ImageDraw, ImageTk
        except Exception:
            self._icons = {}
            return
        br = self._brand()
        _u, fg, _fl, _pb, _pf = self._theme_palette()
        ic = {}

        def new(sz=16):
            im = Image.new("RGBA", (sz * 2, sz * 2), (0, 0, 0, 0))
            return im, ImageDraw.Draw(im), sz

        def keep(name, im, sz):
            ic[name] = ImageTk.PhotoImage(
                im.resize((sz, sz), Image.LANCZOS))

        W = 3                                # ~1.5px stroke after downscale
        # run: solid triangle, white on the ac1 button
        im, d, sz = new()
        d.polygon([(11, 7), (26, 16), (11, 25)], fill="#ffffff")
        keep("run", im, sz)
        # data table: white grid on the ac1 button
        im, d, sz = new()
        d.rectangle([6, 8, 26, 24], outline="#ffffff", width=W)
        d.line([6, 14, 26, 14], fill="#ffffff", width=W)
        d.line([14, 8, 14, 24], fill="#ffffff", width=W)
        keep("table", im, sz)
        # reload (Load previous run): open arc + arrowhead, ink
        im, d, sz = new()
        d.arc([7, 7, 25, 25], start=300, end=210, fill=fg, width=W)
        d.polygon([(4, 12), (13, 10), (8, 18)], fill=fg)
        keep("prev", im, sz)
        # reset view: register target, ink ring + ac2 center
        im, d, sz = new()
        d.ellipse([8, 8, 24, 24], outline=fg, width=W)
        d.line([16, 3, 16, 9], fill=fg, width=W)
        d.line([16, 23, 16, 29], fill=fg, width=W)
        d.line([3, 16, 9, 16], fill=fg, width=W)
        d.line([23, 16, 29, 16], fill=fg, width=W)
        d.ellipse([13, 13, 19, 19], fill=br["ac2"])
        keep("reset", im, sz)
        # pan: four-way arrows, ink
        im, d, sz = new()
        d.line([16, 5, 16, 27], fill=fg, width=W)
        d.line([5, 16, 27, 16], fill=fg, width=W)
        for tri in ([(16, 2), (12, 8), (20, 8)], [(16, 30), (12, 24), (20, 24)],
                    [(2, 16), (8, 12), (8, 20)], [(30, 16), (24, 12), (24, 20)]):
            d.polygon(tri, fill=fg)
        keep("pan", im, sz)
        # zoom: magnifier, ink
        im, d, sz = new()
        d.ellipse([6, 6, 20, 20], outline=fg, width=W)
        d.line([19, 19, 27, 27], fill=fg, width=W + 1)
        keep("zoom", im, sz)
        # save: tray with down arrow, ink
        im, d, sz = new()
        d.line([16, 5, 16, 19], fill=fg, width=W)
        d.polygon([(16, 23), (11, 16), (21, 16)], fill=fg)
        d.line([6, 26, 26, 26], fill=fg, width=W)
        keep("save", im, sz)
        # folder / folder-open, ink
        im, d, sz = new()
        d.polygon([(4, 9), (13, 9), (15, 12), (28, 12), (28, 25), (4, 25)],
                  outline=fg, width=W)
        keep("folder", im, sz)
        im, d, sz = new()
        d.polygon([(4, 9), (13, 9), (15, 12), (26, 12), (26, 14), (30, 14),
                   (25, 25), (4, 25)], outline=fg, width=W)
        keep("folder_open", im, sz)
        # copy: two offset squares, ink
        im, d, sz = new()
        d.rectangle([10, 4, 26, 20], outline=fg, width=W)
        d.rectangle([5, 11, 21, 27], outline=fg, width=W)
        keep("copy", im, sz)
        # share/export-settings: tray with UP arrow, ink
        im, d, sz = new()
        d.line([16, 25, 16, 11], fill=fg, width=W)
        d.polygon([(16, 5), (11, 12), (21, 12)], fill=fg)
        d.line([6, 27, 26, 27], fill=fg, width=W)
        keep("share", im, sz)
        # collapse / expand chevrons, ink
        im, d, sz = new()
        d.line([8, 14, 16, 7], fill=fg, width=W)
        d.line([16, 7, 24, 14], fill=fg, width=W)
        d.line([8, 24, 16, 17], fill=fg, width=W)
        d.line([16, 17, 24, 24], fill=fg, width=W)
        keep("chev_up", im, sz)
        im, d, sz = new()
        d.line([8, 8, 16, 15], fill=fg, width=W)
        d.line([16, 15, 24, 8], fill=fg, width=W)
        d.line([8, 18, 16, 25], fill=fg, width=W)
        d.line([16, 25, 24, 18], fill=fg, width=W)
        keep("chev_dn", im, sz)
        # gear: ring + spokes in ac2 (the ingestion entry point)
        im, d, sz = new()
        d.ellipse([9, 9, 23, 23], outline=br["ac2"], width=W)
        for a, b in (((16, 4), (16, 9)), ((16, 23), (16, 28)),
                     ((4, 16), (9, 16)), ((23, 16), (28, 16)),
                     ((7, 7), (11, 11)), ((21, 21), (25, 25)),
                     ((7, 25), (11, 21)), ((21, 11), (25, 7))):
            d.line([a, b], fill=br["ac2"], width=W)
        keep("gear", im, sz)
        # anvil mark: the brand logo (two ink anvils pressing an ac2 sample)
        for name, px in (("mark", 20), ("mark_lg", 44)):
            im, d, _ = new(px)
            s = px * 2
            d.polygon([(s*0.15, s*0.08), (s*0.85, s*0.08),
                       (s*0.68, s*0.36), (s*0.32, s*0.36)], fill=fg)
            d.polygon([(s*0.32, s*0.64), (s*0.68, s*0.64),
                       (s*0.85, s*0.92), (s*0.15, s*0.92)], fill=fg)
            r = s * 0.09
            d.ellipse([s*0.5 - r, s*0.5 - r, s*0.5 + r, s*0.5 + r],
                      fill=br["ac2"])
            keep(name, im, px)
        # section mini-icons (12px, ac2): one distinct geometric glyph per
        # right-panel section, replacing the plain square markers (plan B3)
        A = br["ac2"]

        def sec(name, fn):
            im, d, _ = new(12)
            fn(d)
            keep("sec::" + name, im, 12)
        sec("Plot mode", lambda d: (d.ellipse([3, 3, 21, 21], outline=A,
            width=W), d.ellipse([9, 9, 15, 15], fill=A)))
        sec("Waterfall (2D / 3D plotting)", lambda d: (
            d.line([3, 7, 21, 7], fill=A, width=W),
            d.line([3, 13, 21, 13], fill=A, width=W),
            d.line([3, 19, 21, 19], fill=A, width=W)))
        sec("2D plot options", lambda d: d.line(
            [3, 19, 9, 9, 14, 14, 21, 4], fill=A, width=W))
        sec("3D plot options", lambda d: (
            d.rectangle([3, 8, 15, 20], outline=A, width=W),
            d.rectangle([9, 3, 21, 15], outline=A, width=W)))
        sec("Axis", lambda d: (d.line([5, 3, 5, 19], fill=A, width=W),
                               d.line([5, 19, 21, 19], fill=A, width=W)))
        sec("Limits & scale", lambda d: (
            d.line([8, 3, 3, 3], fill=A, width=W),
            d.line([3, 3, 3, 21], fill=A, width=W),
            d.line([3, 21, 8, 21], fill=A, width=W),
            d.line([16, 3, 21, 3], fill=A, width=W),
            d.line([21, 3, 21, 21], fill=A, width=W),
            d.line([21, 21, 16, 21], fill=A, width=W)))
        sec("Ticks", lambda d: (d.line([3, 17, 21, 17], fill=A, width=W),
                                d.line([7, 17, 7, 10], fill=A, width=W),
                                d.line([13, 17, 13, 10], fill=A, width=W),
                                d.line([19, 17, 19, 10], fill=A, width=W)))
        sec("Frame & grid", lambda d: (
            d.rectangle([3, 3, 21, 21], outline=A, width=W),
            d.line([3, 12, 21, 12], fill=A, width=2),
            d.line([12, 3, 12, 21], fill=A, width=2)))
        sec("Colors & colormap", lambda d: (
            d.rectangle([3, 8, 9, 16], fill=A),
            d.rectangle([10, 8, 15, 16], outline=A, width=2),
            d.rectangle([16, 8, 21, 16], fill=A)))
        sec("Fonts", lambda d: (d.line([5, 21, 12, 3], fill=A, width=W),
                                d.line([12, 3, 19, 21], fill=A, width=W),
                                d.line([8, 14, 16, 14], fill=A, width=W)))
        sec("Title & axis labels", lambda d: (
            d.line([4, 4, 20, 4], fill=A, width=W),
            d.line([12, 4, 12, 21], fill=A, width=W)))
        sec("Legend", lambda d: (d.rectangle([3, 5, 9, 11], fill=A),
                                 d.line([12, 8, 21, 8], fill=A, width=W),
                                 d.rectangle([3, 14, 9, 20], outline=A,
                                             width=2),
                                 d.line([12, 17, 21, 17], fill=A, width=W)))
        sec("Colorbar", lambda d: (
            d.rectangle([8, 3, 16, 9], fill=A),
            d.rectangle([8, 9, 16, 15], outline=A, width=2),
            d.rectangle([8, 15, 16, 21], fill=A)))
        sec("Reference guides", lambda d: (
            d.line([12, 3, 12, 7], fill=A, width=W),
            d.line([12, 10, 12, 14], fill=A, width=W),
            d.line([12, 17, 12, 21], fill=A, width=W)))
        sec("Smoothing", lambda d: d.arc([3, 6, 21, 20], 180, 360,
                                         fill=A, width=W))
        sec("Defringe", lambda d: (
            d.arc([2, 7, 12, 17], 180, 360, fill=A, width=W),
            d.arc([12, 7, 22, 17], 0, 180, fill=A, width=W)))
        sec("Traces  (check = show,  D = decompression)", lambda d: (
            d.line([3, 7, 8, 12], fill=A, width=W),
            d.line([8, 12, 14, 4], fill=A, width=W),
            d.line([3, 19, 21, 19], fill=A, width=W)))
        sec("Presets & projects", lambda d: d.polygon(
            [(12, 3), (15, 9), (21, 12), (15, 15), (12, 21), (9, 15),
             (3, 12), (9, 9)], fill=A))
        sec("Export", lambda d: (d.line([12, 3, 12, 13], fill=A, width=W),
                                 d.polygon([(12, 17), (8, 11), (16, 11)],
                                           fill=A),
                                 d.line([4, 20, 20, 20], fill=A, width=W)))
        sec("Figure", lambda d: (d.rectangle([3, 5, 21, 19], outline=A,
                                             width=W),
                                 d.ellipse([6, 8, 11, 13], fill=A)))
        im, d, _ = new(12)
        d.rectangle([7, 7, 17, 17], fill=A)
        keep("sec_dot", im, 12)
        # mini glyphs for slider rows + the small per-slider reset
        def mini(name, fn):
            im2, d2, _ = new(13)
            fn(d2)
            keep(name, im2, 13)
        mini("undo12", lambda d2: (
            d2.arc([5, 5, 21, 21], start=300, end=210, fill=fg, width=3),
            d2.polygon([(2, 10), (11, 8), (6, 16)], fill=fg)))
        mini("sl::opacity", lambda d2: (
            d2.ellipse([4, 4, 22, 22], outline=fg, width=2),
            d2.pieslice([4, 4, 22, 22], 270, 90, fill=fg)))
        mini("sl::angle", lambda d2: (
            d2.line([5, 21, 21, 21], fill=fg, width=2),
            d2.line([5, 21, 17, 6], fill=fg, width=2),
            d2.arc([5, 10, 19, 26], 290, 360, fill=fg, width=2)))
        mini("sl::rotate", lambda d2: (
            d2.arc([5, 5, 21, 21], start=0, end=280, fill=fg, width=3),
            d2.polygon([(24, 10), (16, 8), (21, 16)], fill=fg)))
        mini("sl::zoomm", lambda d2: (
            d2.ellipse([4, 4, 17, 17], outline=fg, width=2),
            d2.line([16, 16, 23, 23], fill=fg, width=3)))
        mini("sl::arr_h", lambda d2: (
            d2.line([5, 13, 21, 13], fill=fg, width=2),
            d2.polygon([(2, 13), (7, 9), (7, 17)], fill=fg),
            d2.polygon([(24, 13), (19, 9), (19, 17)], fill=fg)))
        mini("sl::arr_v", lambda d2: (
            d2.line([13, 5, 13, 21], fill=fg, width=2),
            d2.polygon([(13, 2), (9, 7), (17, 7)], fill=fg),
            d2.polygon([(13, 24), (9, 19), (17, 19)], fill=fg)))
        mini("sl::arr_d", lambda d2: (
            d2.line([6, 20, 20, 6], fill=fg, width=2),
            d2.polygon([(22, 3), (15, 6), (20, 11)], fill=fg),
            d2.polygon([(4, 23), (11, 20), (6, 15)], fill=fg)))
        mini("sl::widthI", lambda d2: (
            d2.line([4, 9, 22, 9], fill=fg, width=1),
            d2.line([4, 17, 22, 17], fill=fg, width=4)))
        mini("sl::detail", lambda d2: (
            d2.ellipse([4, 11, 8, 15], fill=fg),
            d2.ellipse([11, 10, 16, 15], fill=fg),
            d2.ellipse([19, 9, 25, 15], fill=fg)))
        # check + cross for in-panel action buttons (plan: iconology)
        im, d, sz = new()
        d.line([6, 17, 13, 24], fill=fg, width=W + 1)
        d.line([13, 24, 26, 8], fill=fg, width=W + 1)
        keep("check", im, sz)
        im, d, sz = new()
        d.line([8, 8, 24, 24], fill=fg, width=W + 1)
        d.line([8, 24, 24, 8], fill=fg, width=W + 1)
        keep("cross", im, sz)
        # collapse carets as images: the text glyphs came from two different
        # fallback fonts and rendered at two sizes. Mustard (ac3) per Nhan.
        im, d, _ = new(13)
        d.polygon([(4, 8), (22, 8), (13, 20)], fill=br["ac3"])
        keep("caret_open", im, 13)
        im, d, _ = new(13)
        d.polygon([(8, 4), (20, 13), (8, 22)], fill=br["ac3"])
        keep("caret_closed", im, 13)
        self._icons = ic

    def _repaint_svttk_accent(self):
        """Evict sv_ttk's baked-in Fluent blue (plan B2): remap the accent-
        hue pixels of every theme sprite to the triad's ac1, so checkboxes,
        radios, switches, sliders and focus rings follow the brand in every
        theme. Originals are cached per (base, image) so a re-theme always
        starts from stock; our own PIL icons (pyimage*) are never touched."""
        if not _HAVE_SVTTK:
            return
        try:
            import base64
            import colorsys
            import io as _io
            from PIL import Image
        except Exception:
            return
        br = self._brand()
        ac = br["ac1"]
        r1, g1, b1 = (int(ac[i:i + 2], 16) for i in (1, 3, 5))
        h1, s1, _v1 = colorsys.rgb_to_hsv(r1 / 255, g1 / 255, b1 / 255)
        base = "dark" if self.dark_mode.get() else "light"
        cache = getattr(self, "_sv_orig", None)
        if cache is None:
            cache = self._sv_orig = {}
        done = getattr(self, "_sv_done", None)
        if done is None:
            done = self._sv_done = {}
        try:
            names = [str(n) for n in self.root.tk.call("image", "names")]
        except tk.TclError:
            return
        for n in names:
            if n.startswith("pyimage"):
                continue
            if done.get(n) == (base, ac):
                continue
            ck = (base, n)
            try:
                if ck not in cache:
                    cache[ck] = str(self.root.tk.call(
                        n, "data", "-format", "png"))
                im = Image.open(_io.BytesIO(
                    base64.b64decode(cache[ck]))).convert("RGBA")
            except Exception:
                continue
            cols = im.getcolors(200000)
            if not cols:
                continue
            lut = {}
            for _cnt, px in cols:
                r, g, b, a2 = px
                if a2 == 0:
                    continue
                h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
                if 190 <= h * 360 <= 235 and s >= 0.25 and v >= 0.25:
                    nr, ng, nb = colorsys.hsv_to_rgb(
                        h1, min(1.0, s1 * (0.35 + 0.75 * s)), v)
                    lut[px] = (int(nr * 255), int(ng * 255),
                               int(nb * 255), a2)
            if lut:
                im.putdata([lut.get(q, q) for q in im.getdata()])
                buf = _io.BytesIO()
                im.save(buf, "png")
                b64 = base64.b64encode(buf.getvalue()).decode("ascii")
                try:
                    self.root.tk.call(n, "configure", "-data", b64)
                except tk.TclError:
                    continue
            done[n] = (base, ac)

    def _lf_header(self, parent, text):
        """Brand header for a left-panel LabelFrame: ac2 square + bold
        title, matching the right panel's section language (plan B4)."""
        f = ttk.Frame(parent)
        m = self._lbl(f, text="\u25a0", font=(UI_FONT, 7),
                      foreground=self._brand()["ac2"])
        m.pack(side="left", padx=(0, 4))
        self._lf_markers = getattr(self, "_lf_markers", [])
        self._lf_markers.append(m)
        self._lbl(f, text=text, font=(UI_FONT, 10, "bold")).pack(side="left")
        return f

    def _iconize_buttons(self):
        """Give in-panel buttons a matching mini icon by their label text
        (Nhan: iconology for the right-panel settings). Re-attached on
        every theme switch because the icon set is regenerated."""
        ic = getattr(self, "_icons", {})
        m = {"Apply": "check", "Apply ticks": "check",
             "Apply limits": "check",
             "Reset": "reset", "Reset all": "reset", "Reset axes": "reset",
             "Reset stretch": "reset", "Defaults": "reset",
             "Reset all to defaults": "reset",
             "Save plot…": "save", "Save as…": "save",
             "Save project…": "save",
             "Copy figure": "copy", "Copy log": "copy",
             "Copy all (TSV)": "copy",
             "Load": "folder_open", "Open project…": "folder_open",
             "Open in Excel": "table",
             "Export CSV…": "share", "Export settings": "share",
             "Batch export PNG (one per shown trace)…": "share",
             "Smoothing settings…": "gear",
             "Load D list…": "folder",
             "Delete": "cross", "Clear": "cross"}

        def walk(w):
            for c in w.winfo_children():
                if c.winfo_class() == "TButton":
                    try:
                        img = ic.get(m.get(str(c.cget("text")), ""))
                        if img is not None:
                            c.configure(image=img, compound="left")
                    except tk.TclError:
                        pass
                walk(c)
        walk(self.root)

    def _apply_titlebar(self, win=None):
        """Paint the Windows caption bar with the theme (Win11 caption/text
        color attributes; immersive-dark fallback). Never raises."""
        try:
            import ctypes
            w = win or self.root
            w.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(w.winfo_id())
            if not hwnd:
                return
            uibg, fg, *_ = self._theme_palette()

            def cref(hexcol):
                r, g, b = (int(hexcol[i:i + 2], 16) for i in (1, 3, 5))
                return (b << 16) | (g << 8) | r

            def dwm(attr, val):
                v = ctypes.c_int(val)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(v), 4)
            dwm(20, 1 if self.dark_mode.get() else 0)
            if uibg.startswith("#") and fg.startswith("#"):
                dwm(35, cref(uibg))          # caption color (Win11)
                dwm(36, cref(fg))            # caption text
        except Exception:
            pass

    def _brand_button(self, parent, text, command, big=False):
        """A primary-action button we fully own (sv_ttk's accent blue is
        image-drawn and cannot be recolored): flat, triad ac1, hover shade."""
        br = self._brand()
        b = tk.Button(parent, text=text, command=command, relief="flat",
                      bd=0, cursor="hand2", bg=br["ac1"], fg="#ffffff",
                      activebackground=br["hov"], activeforeground="#ffffff",
                      disabledforeground="#cfcfcf",
                      font=(UI_FONT, 10, "bold"),
                      padx=14, pady=2)
        if not hasattr(self, "_brand_btns"):
            self._brand_btns = []
        self._brand_btns.append(b)
        b.bind("<Enter>", lambda e: b.configure(bg=self._brand()["hov"]))
        b.bind("<Leave>", lambda e: b.configure(bg=self._brand()["ac1"]))
        return b

    def _recolor_tk(self):
        """Recolor plain tk widgets (Text, Canvas) that ignore the ttk theme."""
        uibg, fg, fld, _pb, _pf = self._theme_palette()
        t = self.theme_mode.get()
        bg = ("#000000" if t == "black" else
              "#1c1d22" if t == "dark" else
              "white" if t == "light" else fld)
        scrolls = set(getattr(self, "_tab_canvases", []))
        for w in getattr(self, "_tk_widgets", []):
            if w in scrolls:
                continue                # settings pages match the chrome below
            try:
                w.configure(background=bg, foreground=fg, insertbackground=fg)
            except tk.TclError:
                try:
                    w.configure(background=bg)      # Canvas has no foreground
                except tk.TclError:
                    pass
        for c in scrolls:
            try:
                c.configure(background=uibg)
            except tk.TclError:
                pass
        # the loop above wipes the status chip's colors; re-tint it
        st = getattr(self, "_last_run_state", None)
        if st:
            self._set_run_state(*st)
        self._sync_tabs()   # tab strip follows the theme

    # ---- theme: modern look + light/dark ---------------------------------
    def _init_theme(self):
        t = self.theme_mode.get()
        th = self._themes().get(t, {})
        base = th.get("base", "light" if t == "light" else "dark")
        self.dark_mode.set(base != "light")
        if _HAVE_SVTTK:
            try:
                sv_ttk.set_theme("dark" if base != "light" else "light")
            except Exception:
                self._clam_palette()
        else:
            self._clam_palette()
        # Always re-assert the chrome backgrounds for the active palette. This
        # is what makes switching BACK to light/dark/black fully revert: ttk
        # style.configure overrides are global and sticky, so an accent theme's
        # tint would otherwise linger.
        uibg, fg, _fld, _pb, _pf = self._theme_palette()
        try:
            st = ttk.Style()
            for w in ("TFrame", "TLabel", "TLabelframe", "TLabelframe.Label",
                      "TCheckbutton", "TRadiobutton", "Card.TFrame"):
                st.configure(w, background=uibg, foreground=fg)
            self.root.configure(bg=uibg)
        except Exception:
            pass
        self._apply_style_fonts()
        # accent (or reset to plain) the carets, group titles and wordmark
        # Re-sync classic tk.Label content labels (they honor bg, unlike
        # sv_ttk ttk.Label). Preserve any caller-set custom fg/bg.
        for _lab, _cfg, _cbg in getattr(self, "_content_labels", []):
            try:
                _lab.configure(bg=(_cbg if _cbg is not None else uibg),
                               fg=(_cfg if _cfg is not None else fg))
            except tk.TclError:
                pass
        self._recolor_accents(th.get("accent"), th.get("rainbow", False))
        matplotlib.rcParams["pdf.fonttype"] = 42
        matplotlib.rcParams["ps.fonttype"] = 42

    def _apply_style_fonts(self):
        """sv_ttk pins fonts two ways at set_theme: named SunValley* fonts
        (used by entries/combos/spinboxes AND our content labels) and per-
        style fonts. Re-point BOTH at the brand face so nothing keeps Segoe.
        Recreated on every theme switch, so this runs from _init_theme."""
        bs = getattr(self, "_body_size", 11)
        semi = UI_FONT_SEMI
        # the named-font family sv_ttk registers (the real culprit): remap
        # every SunValley* face to Jost, scaled around the body size
        sv = {
            "SunValleyBodyFont": (UI_FONT, bs, "normal"),
            "SunValleyBodyLargeFont": (UI_FONT, bs + 3, "normal"),
            "SunValleyBodyStrongFont": (semi, bs, "bold"),
            "SunValleyCaptionFont": (UI_FONT, max(8, bs - 1), "normal"),
            "SunValleySubtitleFont": (semi, bs + 4, "bold"),
            "SunValleyTitleFont": (semi, bs + 10, "bold"),
            "SunValleyTitleLargeFont": (semi, bs + 19, "bold"),
            "SunValleyDisplayFont": (semi, bs + 40, "bold"),
        }
        for name, (fam, sz, wt) in sv.items():
            try:
                tkfont.nametofont(name).configure(family=fam, size=sz,
                                                  weight=wt)
            except tk.TclError:
                pass
        st = ttk.Style()
        body = (UI_FONT, bs)
        for s in ("TButton", "Accent.TButton", "TCheckbutton", "TRadiobutton",
                  "TLabel", "TEntry", "TCombobox", "TSpinbox", "TMenubutton",
                  "Switch.TCheckbutton", "Toolbutton", "Treeview"):
            try:
                st.configure(s, font=body)
            except tk.TclError:
                pass
        try:
            # settings tabs: bold, a size up, selected tab in mustard (ac3)
            st.configure("TNotebook.Tab", font=(UI_FONT, bs + 1, "bold"))
            st.map("TNotebook.Tab",
                   foreground=[("selected", self._brand()["ac3"])])
        except tk.TclError:
            pass
        try:
            st.configure("Treeview.Heading", font=(semi, bs, "bold"))
            st.configure("TLabelframe.Label", font=(semi, bs, "bold"))
            st.configure("NameFmt.TButton", font=(semi, bs, "bold"))
        except tk.TclError:
            pass

    def _clam_palette(self):
        """Fallback hand-tuned theme if sv_ttk is unavailable."""
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            return
        bg, fg, fld, _pb, _pf = self._theme_palette()
        self.root.configure(bg=bg)
        style.configure(".", background=bg, foreground=fg, fieldbackground=fld,
                        bordercolor=("#555c66" if self.dark_mode.get()
                                     else "#c2c9d2"))
        for w in ("TFrame", "TLabel", "TCheckbutton", "TRadiobutton",
                  "TLabelframe"):
            style.configure(w, background=bg, foreground=fg)
        style.configure("TLabelframe", borderwidth=1, relief="solid")
        style.configure("TButton", padding=6, relief="flat")
        style.configure("TEntry", fieldbackground=fld, foreground=fg)
        style.configure("TCombobox", fieldbackground=fld, foreground=fg)

    def _center_titles(self):
        """Center every group-box title (labelanchor='n')."""
        def walk(w):
            for c in w.winfo_children():
                if isinstance(c, ttk.Labelframe):
                    try:
                        c.configure(labelanchor="n")
                    except Exception:
                        pass
                walk(c)
        walk(self.root)

    def _draw_rainbow_banner(self):
        """Paint a Dark-Side-of-the-Moon prism spectrum on the top-bar strip.
        Only shown for the rainbow theme; otherwise the strip is invisible."""
        cv = getattr(self, "rainbow_banner", None)
        if cv is None:
            return
        try:
            cv.delete("all")
            if self.theme_mode.get() != "rainbow":
                # brand strip: one theme color (Nhan: single, per theme)
                ac = self._brand()["ac1"]
                cv.configure(height=3, bg=ac)
                w = cv.winfo_width() or self.root.winfo_width() or 1200
                cv.create_rectangle(0, 0, w, 3, fill=ac, outline=ac)
                return
            import colorsys
            cv.configure(height=6, bg="#000000")
            w = cv.winfo_width() or self.root.winfo_width() or 1200
            for x in range(0, max(w, 2), 2):
                frac = x / max(w - 1, 1)
                r, g, b = colorsys.hsv_to_rgb(frac * 0.83, 0.95, 1.0)
                cv.create_line(x, 0, x, 6, width=2,
                               fill="#%02x%02x%02x" % (int(r * 255),
                                                       int(g * 255),
                                                       int(b * 255)))
        except Exception:
            pass

    def _toggle_dark(self):
        self._init_theme()
        self._center_titles()
        self._recolor_tk()
        self._draw_rainbow_banner()
        self._apply_brand()
        self.settings["theme"] = self.theme_mode.get()
        self.settings["dark"] = self.dark_mode.get()
        self._save_settings()
        self._log_action("Theme: " + self.theme_mode.get())
        self._redraw()

    def _auto_text_color(self, bg):
        """Black or white, whichever is readable on the given background."""
        from matplotlib.colors import to_rgb
        try:
            r, g, b = to_rgb(bg)
        except Exception:
            return "#f0f0f0"
        lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
        return "#101010" if lum > 0.55 else "#f0f0f0"

    def _axis_text_colors(self, fg):
        """(axis/spine color, tick+label text color) honoring user overrides.
        'auto' contrasts with the actual plot background (luminance-based)."""
        from matplotlib.colors import to_rgb
        try:
            auto = self._auto_text_color(self._mpl_colors()[0])
        except Exception:
            auto = fg
        def res(var):
            v = var.get() if var is not None else "auto"
            if v == "auto":
                return auto
            try:
                to_rgb(v); return v
            except Exception:
                return auto
        return (res(getattr(self, "axis_color", None)),
                res(getattr(self, "text_color", None)))

    def _mpl_colors(self):
        _u, _f, _fl, pb, pf = self._theme_palette()
        return (pb, pf)

    # ---- action log + undo/redo ------------------------------------------
    def _log_action(self, msg):
        if not getattr(self, "_action_log_on", False):
            return
        if hasattr(self, "log"):
            self._logline("  . " + msg)

    def _snapshot(self):
        try:
            snap = {k: v.get() for k, v in self._preset_registry().items()}
            snap["_smooth"] = dict(self.smooth_params)
            snap["_traces"] = {k: v.get() for k, v in self.trace_vars.items()}
            snap["_dvars"] = {k: v.get() for k, v in self.dvars.items()}
            return snap
        except Exception:
            return None

    def _push_undo(self, label=""):
        if self._restoring:
            return
        snap = self._snapshot()
        if snap is None:
            return
        if self._undo_stack and self._undo_stack[-1][1] == snap:
            return
        if self._undo_stack and label != "initial":
            self._log_changes(self._undo_stack[-1][1], snap)
        self._undo_stack.append((label, snap))
        if len(self._undo_stack) > 80:
            self._undo_stack.pop(0)
        self._redo_stack.clear()
        self._update_undo_buttons()

    _LOG_NAMES = {
        "wf_mode": "waterfall", "mode": "plot mode", "cmap": "colormap",
        "cmap_rev": "reverse cmap", "xunit": "x unit", "ydata": "overlay Y",
        "theme_mode": "theme", "aspect_mode": "aspect", "wf3d_alpha": "fill opacity",
        "wf3d_elev": "elev", "wf3d_azim": "azim", "wf3d_zoom": "zoom",
        "lw": "line width", "autoscale": "auto-limits", "tick_dir": "tick dir",
        "legend_on": "legend", "colorbar_on": "colorbar", "grid_on": "grid",
        "show_smooth": "show smoothed", "show_notch": "defringe",
        "notch_width": "notch width %",
    }

    def _log_changes(self, a, b):
        changed = []
        for k, v in b.items():
            if k in ("_traces", "_dvars", "_smooth"):
                continue
            if a.get(k) != v:
                nm = self._LOG_NAMES.get(k, k)
                changed.append("%s -> %s" % (nm, v))
        if a.get("_traces") != b.get("_traces"):
            n = sum(1 for x in b.get("_traces", {}).values() if x)
            changed.append("traces shown=%d" % n)
        if a.get("_smooth") != b.get("_smooth"):
            changed.append("smoothing params")
        for c in changed[:5]:
            self._log_action(c)

    def _restore(self, snap):
        self._restoring = True
        try:
            reg = self._preset_registry()
            for k, v in reg.items():
                if k in snap:
                    try:
                        v.set(snap[k])
                    except Exception:
                        pass
            if "_smooth" in snap:
                self.smooth_params.update(snap["_smooth"]); self.smooth_cache.clear()
            for k, val in snap.get("_traces", {}).items():
                if k in self.trace_vars:
                    self.trace_vars[k].set(val)
            for k, val in snap.get("_dvars", {}).items():
                if k in self.dvars:
                    self.dvars[k].set(val)
        finally:
            self._restoring = False
        self._redraw()

    def _undo(self):
        if len(self._undo_stack) < 2:
            return
        cur = self._undo_stack.pop()
        self._redo_stack.append(cur)
        self._restore(self._undo_stack[-1][1])
        self._logline("  < undo: " + (cur[0] or "change"))
        self._update_undo_buttons()

    def _redo(self):
        if not self._redo_stack:
            return
        label, snap = self._redo_stack.pop()
        self._undo_stack.append((label, snap))
        self._restore(snap)
        self._logline("  > redo: " + (label or "change"))
        self._update_undo_buttons()

    def _update_undo_buttons(self):
        try:
            self.undo_btn.config(state=("normal" if len(self._undo_stack) > 1
                                        else "disabled"))
            self.redo_btn.config(state=("normal" if self._redo_stack
                                        else "disabled"))
        except Exception:
            pass

    # ---- multi-tab sessions ----------------------------------------------
    # A "tab" is NOT a duplicate widget tree; it is a stored session state
    # swapped into the single shared UI. The snapshot machinery (used by
    # presets and undo) already serializes every registered control; a
    # session adds the data (results), the per-tab folders, caches, and
    # undo/redo stacks. Theme stays global (it is not in the registry).
    def _capture_session(self, name):
        """Snapshot the live app state into a session dict (holds live refs
        to the mutable objects, which is correct: each session owns distinct
        list/dict objects created at _new_session time)."""
        return {
            "name": name,
            "results": self.results,
            "in_dir": self.in_var.get(),
            "out_dir": self.out_var.get(),
            "last_out_dir": self.last_out_dir,
            "snapshot": self._snapshot(),
            "undo": self._undo_stack,
            "redo": self._redo_stack,
            "smooth_cache": self.smooth_cache,
            "notch_cache": self.notch_cache,
            "smooth_params": dict(self.smooth_params),
            "run_state": getattr(self, "_last_run_state",
                                 ("Ready", "#2a8a4a")),
            "skipped_count": getattr(self, "_skipped_count", 0),
        }

    def _store_active(self):
        """Write the live state back into the active session (keeps name)."""
        if not getattr(self, "sessions", None):
            return
        nm = self.sessions[self.active]["name"]
        self.sessions[self.active] = self._capture_session(nm)

    def _apply_session_snapshot(self, snap):
        """Apply a control snapshot WITHOUT clearing per-tab caches (unlike
        _restore, which undo/redo use). Assumes trace_vars already rebuilt."""
        if not snap:
            return
        reg = self._preset_registry()
        for k, v in reg.items():
            if k in snap:
                try:
                    v.set(snap[k])
                except Exception:
                    pass
        if "_smooth" in snap:
            self.smooth_params = dict(snap["_smooth"])
        for k, val in snap.get("_traces", {}).items():
            if k in self.trace_vars:
                self.trace_vars[k].set(val)
        for k, val in snap.get("_dvars", {}).items():
            if k in self.dvars:
                self.dvars[k].set(val)

    def _tab_load(self, i):
        """Swap session i into the live UI (theme stays global)."""
        s = self.sessions[i]
        self._restoring = True
        try:
            self.results = s["results"]
            self.smooth_cache = s.get("smooth_cache", {})
            self.notch_cache = s.get("notch_cache", {})
            self.smooth_params = dict(s.get("smooth_params",
                                            smoothing.DEFAULTS))
            self.last_out_dir = s.get("last_out_dir")
            self._skipped_count = s.get("skipped_count", 0)
            self._undo_stack = s.get("undo", [])
            self._redo_stack = s.get("redo", [])
            self.in_var.set(s.get("in_dir", ""))
            self.out_var.set(s.get("out_dir", ""))
            self._hide_raw_banner()
            self._build_trace_checks()
            self._apply_session_snapshot(s.get("snapshot"))
            labels = [r["label"] for r in self.results]
            self.inspect_combo.config(values=labels)
            if labels and self.inspect_p.get() not in labels:
                self.inspect_p.set(labels[0])
        finally:
            self._restoring = False
        self.active = i
        self._update_undo_buttons()
        st = s.get("run_state")
        if st:
            self._set_run_state(*st)
        self._redraw()
        self._update_status()
        self._sync_tabs()
        self._refresh_drawer()   # follow the active tab if the drawer is open

    def _switch_session(self, i):
        if not (0 <= i < len(self.sessions)) or i == self.active:
            return
        if self._run_busy():
            return
        self._store_active()
        self._tab_load(i)

    def _new_session(self, name=None, activate=True):
        """Append a blank session with default controls; return its index."""
        if self._run_busy():
            return self.active
        self._store_active()
        name = name or self._unique_session_name()
        self._restoring = True
        try:
            self.results = []
            self.smooth_cache = {}
            self.notch_cache = {}
            self.smooth_params = dict(smoothing.DEFAULTS)
            self.last_out_dir = None
            self._skipped_count = 0
            self._undo_stack = []
            self._redo_stack = []
            self.in_var.set("")
            self.out_var.set("")
            self._hide_raw_banner()
            reg = self._preset_registry()
            for k, val in self._defaults.items():
                if k in reg:
                    try:
                        reg[k].set(val)
                    except Exception:
                        pass
            self._build_trace_checks()
            self.inspect_combo.config(values=[])
        finally:
            self._restoring = False
        self._push_undo("initial")
        self._set_run_state("Ready", "#2a8a4a")
        self.sessions.append(self._capture_session(name))
        idx = len(self.sessions) - 1
        if activate:
            self.active = idx
            self._redraw()
            self._update_status()
            self._update_undo_buttons()
        self._sync_tabs()
        return idx

    def _close_session(self, i):
        if self._run_busy() or not (0 <= i < len(self.sessions)):
            return
        if len(self.sessions) == 1:
            # closing the only tab resets it to a fresh blank session
            self.sessions = []
            self.active = 0
            self._new_session(name="Session 1", activate=True)
            return
        if i != self.active:
            del self.sessions[i]
            if i < self.active:
                self.active -= 1
            self._sync_tabs()
            return
        # closing the active tab: load a neighbor (prefer the left one)
        target = i - 1 if i > 0 else 0
        del self.sessions[i]
        target = max(0, min(target, len(self.sessions) - 1))
        self._tab_load(target)

    def _rename_session(self, i, name):
        name = (name or "").strip()
        if name and 0 <= i < len(self.sessions):
            self.sessions[i]["name"] = name[:40]
            self._sync_tabs()

    def _unique_session_name(self):
        used = {s["name"] for s in getattr(self, "sessions", [])}
        n = len(used) + 1
        while ("Session %d" % n) in used:
            n += 1
        return "Session %d" % n

    def _run_busy(self):
        t = getattr(self, "_run_thread", None)
        return bool(t and t.is_alive())

    def _sync_tabs(self):
        """Redraw the tab strip if it has been built."""
        if getattr(self, "_tabbar", None) is not None:
            self._render_tabs()

    def _render_tabs(self):
        """Rebuild the browser-style tab strip from sessions/active. Built
        from plain tk widgets recolored from the theme palette each call
        (sv_ttk Card/LabelFrame would show mismatched image-fill patches)."""
        bar = getattr(self, "_tabbar", None)
        if bar is None or not getattr(self, "sessions", None):
            return
        for w in bar.winfo_children():
            w.destroy()
        uibg, fg, _fld, _pb, _pf = self._theme_palette()
        accent = self._raw_banner_accent()
        muted = "#9aa0a6" if self.dark_mode.get() else "#6b7280"
        bar.configure(bg=uibg)
        self._session_tabs = {}
        many = len(self.sessions) > 6      # compress padding when crowded
        namepad = 4 if many else 8
        for i, s in enumerate(self.sessions):
            on = (i == self.active)
            nm = s["name"]
            if len(nm) > 18:               # ellipsize long tab names
                nm = nm[:16] + "\u2026"
            tab = tk.Frame(bar, bg=uibg, cursor="hand2")
            tab.pack(side="left", padx=(0, 2))
            self._session_tabs[i] = tab
            row = tk.Frame(tab, bg=uibg); row.pack(side="top", fill="x")
            lbl = tk.Label(row, text=nm, bg=uibg,
                           fg=(fg if on else muted),
                           font=(UI_FONT, 10, "bold" if on else "normal"),
                           padx=namepad, pady=3)
            lbl.pack(side="left")
            # close x: always laid out (no width jitter) but painted
            # invisible on inactive tabs until hovered
            xb = tk.Label(row, text="\u00d7", bg=uibg,
                          fg=(fg if on else uibg),
                          font=(UI_FONT, 10), padx=3, cursor="hand2")
            xb.pack(side="left", padx=(0, 4))
            if not on:
                def _hov(_e, x=xb, c=muted):
                    x.config(fg=c)
                def _out(_e, x=xb, c=uibg):
                    x.config(fg=c)
                for w in (tab, row, lbl, xb):
                    w.bind("<Enter>", _hov)
                    w.bind("<Leave>", _out)
            under = tk.Frame(tab, height=2, bg=(accent if on else uibg))
            under.pack(side="top", fill="x")
            for w in (tab, row, lbl):
                w.bind("<Button-1>", lambda e, k=i: self._switch_session(k))
                w.bind("<Double-Button-1>",
                       lambda e, k=i: self._begin_rename(k))
                w.bind("<Button-2>", lambda e, k=i: self._close_session(k))
            xb.bind("<Button-1>", lambda e, k=i: self._close_session(k))
            if len(self.sessions) > 1:
                Tooltip(lbl, s["name"] + "  (double-click to rename, "
                        "middle-click to close)")
        plus = tk.Label(bar, text="+", bg=uibg, fg=fg,
                        font=(UI_FONT, 13), padx=8, cursor="hand2")
        plus.pack(side="left", padx=(2, 0))
        plus.bind("<Button-1>", lambda e: self._new_session())
        Tooltip(plus, "New tab: a blank session for another run (Ctrl+T).")
        self._update_title()

    def _update_title(self):
        """Window title leads with the active tab's name (D1)."""
        try:
            nm = self.sessions[self.active]["name"]
        except (AttributeError, IndexError, KeyError):
            nm = ""
        generic = not nm or nm.startswith("Session ")
        self.root.title(APP_TITLE if generic else nm + "  -  " + APP_TITLE)

    def _begin_rename(self, i):
        """Inline-rename a tab: an Entry placed over the tab label."""
        if not (0 <= i < len(self.sessions)):
            return
        if i != self.active:
            self._switch_session(i)
            i = self.active
        tabf = self._session_tabs.get(i)
        bar = self._tabbar
        if tabf is None:
            return
        bar.update_idletasks()
        e = tk.Entry(bar, font=(UI_FONT, 10))
        e.insert(0, self.sessions[i]["name"])
        e.select_range(0, "end")
        e.place(x=tabf.winfo_x(), y=1,
                width=max(90, tabf.winfo_width()),
                height=max(20, tabf.winfo_height() - 2))
        e.focus_set()
        done = {"v": False}

        def commit(_=None):
            if done["v"]:
                return
            done["v"] = True
            self._rename_session(i, e.get())
            e.destroy()

        def cancel(_=None):
            done["v"] = True
            e.destroy()
        e.bind("<Return>", commit)
        e.bind("<FocusOut>", commit)
        e.bind("<Escape>", cancel)

    def _cycle_tab(self, d):
        n = len(getattr(self, "sessions", []))
        if n > 1:
            self._switch_session((self.active + d) % n)
        return "break"

    # ---- settings (remember folders) -------------------------------------
    def _load_settings(self):
        try:
            with open(SETTINGS_PATH) as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_settings(self):
        try:
            self.settings.update(in_dir=self.in_var.get(),
                                 out_dir=self.out_var.get())
            # atomic write: a crash mid-write can no longer corrupt settings
            tmp = SETTINGS_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, indent=2)
            os.replace(tmp, SETTINGS_PATH)
        except Exception as e:
            # never silent: surface to the log (guarded for early-init calls)
            try:
                self._logline("Could not save settings: %s" % e)
            except Exception:
                pass

    # ---- top bar (title + collapse buttons) ------------------------------
    def _build_top(self):
        top = ttk.Frame(self.root, padding=(10, 6))
        top.pack(side="top", fill="x")
        # Dark-Side-of-the-Moon prism strip; only painted for the rainbow theme
        self.rainbow_banner = tk.Canvas(self.root, height=1, highlightthickness=0,
                                        bd=0)
        self.rainbow_banner.pack(side="top", fill="x")
        self.rainbow_banner.bind("<Configure>",
                                 lambda e: self._draw_rainbow_banner())
        titles = ttk.Frame(top)
        titles.pack(side="left")
        # the anvil mark (drawn per theme; image attached in _apply_brand)
        self._hdr_mark_lbl = self._lbl(titles)
        self._hdr_mark_lbl.pack(side="left", anchor="s", padx=(0, 7),
                                pady=(0, 2))
        self.title_lbl = self._lbl(titles, text=BRAND["wordmark"],
                                   font=(UI_FONT_SEMI, 15))
        self.title_lbl.pack(side="left", anchor="s")
        self._wm_dot = self._lbl(titles, text=BRAND["dot"],
                                 font=(UI_FONT_SEMI, 15, "bold"))
        self._wm_dot.pack(side="left", anchor="s")
        self._lbl(titles,
                  text="   " + BRAND["subtitle"],
                  font=(UI_FONT, 10), foreground="#888").pack(side="left",
                                                                anchor="s",
                                                                pady=(0, 2))
        vchip = self._lbl(titles, text="   %s" % APP_VERSION,
                          font=(UI_FONT, 10), foreground="#888")
        vchip.pack(side="left", anchor="s", pady=(0, 2))
        vchip.configure(cursor="hand2")
        vchip.bind("<Button-1>", lambda e: self._about())
        Tooltip(vchip, "About this build (click).")
        self.left_btn = ttk.Button(top, text="< Hide left",
                                    command=self._toggle_left)
        self.left_btn.pack(side="right")
        Tooltip(self.left_btn, "Hide the left panel (files and data) to give the plot more room.")
        self.right_btn = ttk.Button(top, text="Hide right >",
                                     command=self._toggle_right)
        self.right_btn.pack(side="right", padx=6)
        Tooltip(self.right_btn, "Hide the right controls panel to widen the plot area.")
        # theme dropdown (Light / Dark / Black)
        ab = ttk.Button(top, text="About / Help", command=self._about)
        ab.pack(side="right", padx=(0, 6))
        Tooltip(ab, "About, the full guide, and credits. F1 shows the "
                    "keyboard shortcuts anytime.")
        thf = ttk.Frame(top); thf.pack(side="right", padx=10)
        self._lbl(thf, text="Theme").pack(side="left", padx=(0, 4))
        thcb = ttk.Combobox(thf, textvariable=self.theme_mode, state="readonly",
                            width=9, values=["light", "dark", "black", "forest",
                                             "rose", "ocean", "solarized", "rainbow"])
        thcb.pack(side="left")
        self.theme_mode.trace_add("write", lambda *a: self._toggle_dark())
        # Helper switch: master on/off for every hover tooltip
        self.tooltips_on = tk.BooleanVar(
            value=bool(self.settings.get("tooltips_on", True)))
        Tooltip.enabled = self.tooltips_on.get()
        hlf = ttk.Frame(top); hlf.pack(side="right", padx=(0, 10))
        self._lbl(hlf, text="Helper").pack(side="left", padx=(0, 4))
        try:
            hsw = ttk.Checkbutton(hlf, style="Switch.TCheckbutton",
                                  variable=self.tooltips_on,
                                  command=self._toggle_tooltips)
        except tk.TclError:                 # theme without the Switch style
            hsw = ttk.Checkbutton(hlf, variable=self.tooltips_on,
                                  command=self._toggle_tooltips)
        hsw.pack(side="left")
        # undo / redo, separated from the theme controls
        urf = ttk.Frame(top); urf.pack(side="right", padx=(10, 18))
        self.undo_btn = ttk.Button(urf, text="\u21b6 Undo", width=8,
                                    command=self._undo)
        self.undo_btn.pack(side="left", padx=(0, 2))
        self.redo_btn = ttk.Button(urf, text="\u21b7 Redo", width=8,
                                    command=self._redo)
        self.redo_btn.pack(side="left")
        Tooltip(self.undo_btn, "Undo the last change (Ctrl+Z).")
        Tooltip(self.redo_btn, "Redo (Ctrl+Y).")
        # NUKE: hard session reset, centered prominently on the top bar
        self.nuke_btn = tk.Button(top, text="\u2622  NUKE", command=self._nuke,
                                  bg="#c0392b", fg="white",
                                  activebackground="#e74c3c", activeforeground="white",
                                  font=(UI_FONT, 13, "bold"), relief="raised",
                                  bd=2, padx=12, pady=0, cursor="hand2")
        self.nuke_btn.place(relx=0.5, rely=0.5, anchor="center")
        Tooltip(self.nuke_btn,
                "Reset everything to a fresh start: clears loaded data, the plot, "
                "the folders, the log, and all controls back to defaults. Your "
                "saved presets and default colormap are kept. Asks first.")

    # ---- resizable 3-pane layout -----------------------------------------
    def _build_panes(self):
        self.pw = tk.PanedWindow(self.root, orient="horizontal", sashwidth=6,
                                 sashrelief="raised", bg="#d0d0d0")
        self.pw.pack(side="top", fill="both", expand=True)

        self.left = ttk.Frame(self.pw, padding=(8, 6))
        self.center = ttk.Frame(self.pw)
        self.right_outer = ttk.Frame(self.pw, padding=(8, 6))
        self._build_left(self.left)
        self._build_center(self.center)
        self._build_right(self.right_outer)
        self._reorder_sections()
        self.root.after(80, self._restore_tab)

        self.pw.add(self.left, minsize=220, width=330, stretch="never")
        self.pw.add(self.center, minsize=400, stretch="always")
        self.pw.add(self.right_outer, minsize=300, width=410, stretch="never")
        self.left_shown = True
        self.right_shown = True

    def _toggle_left(self):
        if self.left_shown:
            self.pw.forget(self.left); self.left_btn.config(text="> Show left")
        else:
            self.pw.add(self.left, before=self.center, minsize=220, width=330,
                        stretch="never")
            self.left_btn.config(text="< Hide left")
        self.left_shown = not self.left_shown

    def _toggle_right(self):
        if self.right_shown:
            self.pw.forget(self.right_outer)
            self.right_btn.config(text="< Show right")
        else:
            self.pw.add(self.right_outer, minsize=300, width=410, stretch="never")
            self.right_btn.config(text="Hide right >")
        self.right_shown = not self.right_shown


    # ---- left pane: folders, run, log, reference -------------------------
    def _build_left(self, p):
        self._lbl(p, text="Data Input", font=(UI_FONT, 11, "bold")).pack(
            anchor="w", pady=(0, 4))

        inf = ttk.LabelFrame(p, labelwidget=self._lf_header(p, "Input folder (raw segments)"),
                             padding=6)
        inf.pack(fill="x", pady=4)
        irow = ttk.Frame(inf); irow.pack(fill="x")
        self.in_var = tk.StringVar(value=self.settings.get("in_dir", ""))
        ient = ttk.Entry(irow, textvariable=self.in_var)
        ient.pack(side="left", fill="x", expand=True)
        self._in_entry = ient
        self._browse_in_btn = ttk.Button(irow, text="Browse",
                                         command=self._browse_in)
        self._browse_in_btn.pack(side="left")

        # hover the box to see the full path (the entry truncates when long)
        self._in_tip = Tooltip(ient, "")
        self.in_var.trace_add("write", lambda *a: self._update_folder_tips())
        nrow = ttk.Frame(inf); nrow.pack(fill="x", pady=(3, 0))
        self.profile_btn = ttk.Button(nrow,
                                      text=" Name format:  22-IR-1 default",
                                      command=self._open_name_format)
        self.profile_btn.pack(fill="x", expand=True)
        self._update_profile_btn()
        Tooltip(self.profile_btn,
                "How filenames are read (which part is the DAC, sample, "
                "pressure, channel...). The default understands the classic "
                "22-IR-1 vis_... names; open this to teach a different "
                "naming scheme or fix stubborn files one by one.")

        ouf = ttk.LabelFrame(p, labelwidget=self._lf_header(p, "Output folder"),
                             padding=6)
        ouf.pack(fill="x", pady=4)
        orow = ttk.Frame(ouf); orow.pack(fill="x")
        self.out_var = tk.StringVar(value=self.settings.get("out_dir", ""))
        oent = ttk.Entry(orow, textvariable=self.out_var)
        oent.pack(side="left", fill="x", expand=True)
        self._out_entry = oent
        self._browse_out_btn = ttk.Button(orow, text="Browse",
                                          command=self._browse_out)
        self._browse_out_btn.pack(side="left")

        self._out_tip = Tooltip(oent, "")
        self.out_var.trace_add("write", lambda *a: self._update_folder_tips())
        self._update_folder_tips()

        brow = ttk.Frame(p); brow.pack(fill="x", pady=(4, 2))
        self.run_btn = self._brand_button(brow, "Run", self._run, big=True)
        self.run_btn.pack(side="left", fill="x", expand=True)
        Tooltip(self.run_btn, "Join the 4 grating segments per measurement, "
                              "compute absorbance, and write one CSV per "
                              "pressure to an auto-named output subfolder.")
        self._openout_btn = ttk.Button(brow, text="Open output",
                                       command=self._open_output)
        self._openout_btn.pack(side="left", padx=(6, 0))
        self.run_state = self._lbl(brow, text="Ready", foreground="#2a8a4a",
                                   font=(UI_FONT, 10, "bold"))
        self.run_state.pack(side="left", padx=(8, 0))

        lrow = ttk.Frame(p); lrow.pack(fill="x", pady=(0, 6))
        self.load_prev_btn = ttk.Button(lrow, text="Load previous run\u2026",
                                        command=self._load_previous)
        self.load_prev_btn.pack(fill="x", expand=True)
        Tooltip(self.load_prev_btn,
                "Reopen a finished run without re-processing: pick the "
                "output subfolder a Run wrote and its *_absorbance.csv "
                "curves load for plotting. Nothing is recomputed or "
                "written; smoothing and defringe still apply live.")

        self.run_prog = ttk.Progressbar(p, mode="determinate")
        self.run_prog.pack(fill="x", pady=(0, 4))

        pgf = ttk.LabelFrame(p, labelwidget=self._lf_header(p, "Progress"),
                             padding=4)
        pgf.pack(fill="both", expand=True, pady=4)
        pgbtn = ttk.Frame(pgf); pgbtn.pack(side="bottom", fill="x")
        cl = self._copylog_btn = ttk.Button(pgbtn, text="Copy log",
                                            command=self._copy_log)
        cl.pack(side="left")
        Tooltip(cl, "Copy the full progress / action log to the clipboard.")
        es = self._expset_btn = ttk.Button(pgbtn, text="Export settings",
                                           command=self._export_settings)
        es.pack(side="left", padx=(4, 0))
        Tooltip(es, "Print the current plot configuration to the log so you can "
                    "paste it into a paper's methods section.")
        self.log = tk.Text(pgf, height=8, wrap="none", font=("Consolas", 9),
                           relief="flat")
        self._tk_widgets.append(self.log)
        sb = ttk.Scrollbar(pgf, command=self.log.yview)
        hb = ttk.Scrollbar(pgf, orient="horizontal", command=self.log.xview)
        self.log.configure(yscrollcommand=sb.set, xscrollcommand=hb.set)
        sb.pack(side="right", fill="y")
        hb.pack(side="bottom", fill="x")
        self.log.pack(side="left", fill="both", expand=True)

        gf = ttk.LabelFrame(p, labelwidget=self._lf_header(p, "Guide / notes"),
                             padding=6)
        gf.pack(fill="both", expand=True, pady=4)
        hdr = ttk.Frame(gf); hdr.pack(fill="x")
        self._lbl(hdr, text="View").pack(side="left")
        _views = list(REF_VIEWS.keys()) + ["My notes"]
        _v0 = self.settings.get("ref_view", "Quick start")
        self.ref_kind = tk.StringVar(value=_v0 if _v0 in _views
                                     else "Quick start")
        cb = ttk.Combobox(hdr, textvariable=self.ref_kind, state="readonly",
                          values=_views)
        cb.pack(side="left", fill="x", expand=True, padx=(4, 0))
        self.ref_kind.trace_add("write", lambda *a: self._set_reference())
        Tooltip(cb, "Switch between the quick-start, the absorbance formulas, "
                    "a guide to every right-side panel, the shortcut list, "
                    "and 'My notes', a free-text scratchpad saved between "
                    "launches.")
        # text settings live behind a small gear so the row stays compact
        self.guide_font_family = tk.StringVar(
            value=self.settings.get("guide_font_family", "Consolas"))
        self.guide_font_size = tk.IntVar(
            value=int(self.settings.get("guide_font_size", 9)))
        self.guide_font_family.trace_add(
            "write", lambda *a: self._apply_guide_font())
        gearb = ttk.Button(hdr, text="\u2699", width=3,
                           command=self._guide_font_popup)
        gearb.pack(side="left", padx=(4, 0))
        Tooltip(gearb, "Text settings for this box (font, size).")
        rw = ttk.Frame(gf); rw.pack(fill="both", expand=True)
        self.ref = tk.Text(rw, height=16, wrap="word", relief="flat",
                          font=(self.settings.get("guide_font_family",
                                                  "Consolas"),
                                int(self.settings.get("guide_font_size", 9))))
        self._tk_widgets.append(self.ref)
        rsb = ttk.Scrollbar(rw, command=self.ref.yview)
        self.ref.configure(yscrollcommand=rsb.set)
        rsb.pack(side="right", fill="y")
        self.ref.pack(side="left", fill="both", expand=True)
        self.ref.bind("<FocusOut>", lambda e: self._save_user_notes())
        self._set_reference()

    def _guide_font_popup(self):
        """Small popover with the guide/notes text settings (gear button)."""
        p = tk.Toplevel(self.root)
        p.title("Guide text")
        p.transient(self.root)
        p.resizable(False, False)
        self._apply_titlebar(p)
        p.geometry("+%d+%d" % (self.root.winfo_pointerx() + 10,
                               self.root.winfo_pointery() + 10))
        b = ttk.Frame(p, padding=10); b.pack()
        self._lbl(b, text="Font").grid(row=0, column=0, sticky="w")
        gfc = ttk.Combobox(b, textvariable=self.guide_font_family,
                           state="readonly", width=12,
                           values=["Consolas", "Segoe UI", "Arial", "Calibri",
                                   "Cambria", "Courier New"])
        gfc.grid(row=0, column=1, padx=(6, 0), pady=1)
        self._lbl(b, text="Size").grid(row=1, column=0, sticky="w")
        gfs = ttk.Spinbox(b, from_=8, to=16, width=5,
                          textvariable=self.guide_font_size,
                          command=self._apply_guide_font)
        gfs.grid(row=1, column=1, padx=(6, 0), pady=1, sticky="w")
        gfs.bind("<Return>", lambda e: self._apply_guide_font())
        gfs.bind("<FocusOut>", lambda e: self._apply_guide_font())
        ttk.Separator(b, orient="horizontal").grid(
            row=2, column=0, columnspan=2, sticky="ew", pady=(8, 6))
        # app-wide interface text size (the whole program, not just this box)
        self._lbl(b, text="App text size").grid(row=3, column=0, sticky="w")
        self._ui_size_var = tk.IntVar(value=self._body_size)
        us = ttk.Spinbox(b, from_=9, to=15, width=5,
                         textvariable=self._ui_size_var,
                         command=self._apply_ui_size)
        us.grid(row=3, column=1, padx=(6, 0), pady=1, sticky="w")
        us.bind("<Return>", lambda e: self._apply_ui_size())
        Tooltip(us, "Size of every button, label and control in the program "
                    "(9-15). Applies live and is remembered.")
        ttk.Button(b, text="Done", command=p.destroy).grid(
            row=4, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        p.bind("<Escape>", lambda e: p.destroy())

    def _apply_ui_size(self):
        """Change the whole interface text size live and persist it."""
        try:
            sz = max(9, min(15, int(self._ui_size_var.get())))
        except (ValueError, tk.TclError):
            return
        self._body_size = sz
        self.settings["ui_font_size"] = sz
        _bs = sz
        for _nf, _s in (("TkDefaultFont", _bs), ("TkTextFont", _bs),
                        ("TkMenuFont", _bs), ("TkHeadingFont", _bs),
                        ("TkTooltipFont", _bs - 1), ("TkCaptionFont", _bs - 1)):
            try:
                tkfont.nametofont(_nf).configure(family=UI_FONT, size=_s)
            except tk.TclError:
                pass
        self._apply_style_fonts()
        self._save_settings()
        self._log_action("Interface text size: %d" % sz)

    def _apply_guide_font(self):
        """Apply + persist the guide/notes typeface and size."""
        try:
            fam = self.guide_font_family.get() or "Consolas"
            size = max(6, min(24, int(self.guide_font_size.get())))
        except (ValueError, tk.TclError):
            fam, size = "Consolas", 9
        try:
            self.ref.configure(font=(fam, size))
        except tk.TclError:
            return
        self.settings["guide_font_family"] = fam
        self.settings["guide_font_size"] = size
        self._save_settings()

    def _set_reference(self):
        """Fill the Guide box for the selected view. 'My notes' is the one
        editable view: free text that persists in the settings file."""
        self._save_user_notes()      # leaving 'My notes'? keep the text
        kind = self.ref_kind.get()
        self._ref_prev = kind
        if self.settings.get("ref_view") != kind:
            self.settings["ref_view"] = kind
            self._save_settings()
        self.ref.config(state="normal")
        self.ref.delete("1.0", "end")
        if kind == "My notes":
            self.ref.insert("1.0", self.settings.get(
                "user_notes",
                "Your notes live here and are saved between launches.\n"
                "Sample list, gasket sizes, beamline phone numbers, todos\u2026"))
            return  # stays editable
        self.ref.insert("1.0", REF_VIEWS.get(kind, INFO_TEXT))
        self.ref.config(state="disabled")

    def _save_user_notes(self):
        """Persist the 'My notes' text (no-op while a read-only view shows)."""
        if getattr(self, "_ref_prev", None) != "My notes":
            return
        try:
            self.settings["user_notes"] = self.ref.get("1.0", "end-1c")
            self._save_settings()
        except Exception:
            pass

    # ---- center pane: plot canvas + toolbar + cursor readout -------------
    def _on_wheel(self, e):
        """Scroll the right panel only when the cursor is over it, so the
        Progress / Guide boxes scroll independently."""
        try:
            w = self.root.winfo_containing(e.x_root, e.y_root)
        except Exception:
            w = None
        n = w
        while n is not None:
            if n in getattr(self, "_tab_canvases", []):
                first, last = n.yview()
                step = int(-e.delta / 120)
                if step == 0:   # precision touchpads send |delta| < 120
                    step = -1 if e.delta > 0 else 1
                if (step < 0 and first <= 0.0) or (step > 0 and last >= 1.0):
                    return
                n.yview_scroll(step, "units")
                return
            n = getattr(n, "master", None)

    # ---- collapsible right-panel groups ----------------------------------
    def _on_tab_changed(self, e=None):
        if getattr(self, "_tabs_ready", False):
            try:
                self.settings["active_tab"] = self.rnotebook.index("current")
                self._save_settings()
            except Exception:
                pass

    def _restore_tab(self):
        try:
            idx = int(self.settings.get("active_tab", 0))
            if 0 <= idx < self.rnotebook.index("end"):
                self.rnotebook.select(idx)
        except Exception:
            pass
        self._tabs_ready = True

    def _fit_scroll(self, canvas):
        """Scroll region pinned to the top so you cannot scroll into blank
        space above the content."""
        bb = canvas.bbox("all")
        canvas.configure(scrollregion=(0, 0, bb[2], bb[3]) if bb else (0, 0, 0, 0))

    def _make_scroll_page(self, notebook, label):
        """Create a scrollable page in the notebook and return its inner
        frame. Each page owns its canvas so the tabs scroll independently."""
        page = ttk.Frame(notebook)
        notebook.add(page, text=label)
        canvas = tk.Canvas(page, highlightthickness=0)
        self._tk_widgets.append(canvas)
        sb = tk.Scrollbar(page, orient="vertical", command=canvas.yview,
                          width=16, bd=1, relief="raised", elementborderwidth=1)
        inner = ttk.Frame(canvas)
        win = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e, c=canvas: self._fit_scroll(c))
        canvas.bind("<Configure>",
                    lambda e, c=canvas, w=win: c.itemconfig(w, width=e.width))
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        self._tab_canvases.append(canvas)
        return inner

    def _default_label_font(self):
        """Match sv_ttk's body font so converted tk.Labels render identically to
        the ttk.Labels they replaced. style.lookup is unreliable mid-build, so
        prefer the named font sv_ttk registers on set_theme."""
        try:
            import tkinter.font as _tkf
            if "SunValleyBodyFont" in _tkf.names():
                return "SunValleyBodyFont"
        except Exception:
            pass
        try:
            f = str(ttk.Style().lookup("TLabel", "font") or "")
            if f:
                return f
        except Exception:
            pass
        return "TkDefaultFont"

    def _lbl(self, parent, text="", **kw):
        """Content label as a classic tk.Label. Unlike sv_ttk's ttk.Label,
        tk.Label honors bg, so these match the panel in every theme. Tracked
        in self._content_labels for re-sync on theme change; an explicit
        foreground/background from the caller is preserved across switches."""
        if not hasattr(self, "_content_labels"):
            self._content_labels = []
        kw.pop("style", None)
        kw.pop("padding", None)
        uibg, themefg, _fld, _pb, _pf = self._theme_palette()
        cfg = kw.pop("fg", None)
        if cfg is None:
            cfg = kw.pop("foreground", None)
        else:
            kw.pop("foreground", None)
        cbg = kw.pop("bg", None)
        if cbg is None:
            cbg = kw.pop("background", None)
        else:
            kw.pop("background", None)
        kw.setdefault("bd", 0)
        kw.setdefault("padx", 0)
        kw.setdefault("pady", 0)
        kw.setdefault("anchor", "w")   # left-align text so width-ed labels do not float
        kw.setdefault("highlightthickness", 0)
        font = kw.pop("font", None)
        if font is None:
            font = self._default_label_font()
        lab = tk.Label(parent, text=text,
                       bg=(cbg if cbg is not None else uibg),
                       fg=(cfg if cfg is not None else themefg),
                       font=font, **kw)
        self._content_labels.append((lab, cfg, cbg))
        return lab

    def _group(self, parent, title):
        """A real accordion section: clickable header + a body frame that
        truly collapses (the whole section shrinks to the header)."""
        cat = getattr(self, "_section_cat", {}).get(title)
        if cat is not None and cat in getattr(self, "_tab_frames", {}):
            parent = self._tab_frames[cat]
        cont = ttk.Frame(parent); cont.pack(fill="x", pady=(5, 12))
        hdr = ttk.Frame(cont); hdr.pack(fill="x")
        caret = self._lbl(hdr, text="\u25bc", width=3,
                          font=(UI_FONT, 10, "bold"))
        caret.pack(side="left")
        # Bauhaus square marker (ac2) sits between the caret and the title
        marker = self._lbl(hdr, text="\u25a0", font=(UI_FONT, 8),
                           foreground=self._brand()["ac2"])
        marker.pack(side="left", padx=(0, 5))
        title_lbl = self._lbl(hdr, text=title, font=(UI_FONT, 10, "bold"))
        title_lbl.pack(side="left")
        # 2px ink rule under each section head (Bauhaus structure; colored
        # per theme in _apply_brand)
        rule = tk.Frame(cont, height=2, bd=0,
                        bg=self._brand()["ink"])
        rule.pack(fill="x", pady=(1, 3))
        body = ttk.Frame(cont, padding=(12, 9))
        body.pack(fill="x")
        rec = {"key": title, "caret": caret, "title_lbl": title_lbl,
               "body": body, "cont": cont, "collapsed": False,
               "rule": rule, "marker": marker, "search_text": None}
        self._collapsibles.append(rec)
        for w in [hdr, caret] + list(hdr.winfo_children()):
            w.configure(cursor="hand2")
            w.bind("<Button-1>", lambda e, rec=rec: self._toggle_section(rec))
            w.bind("<Enter>", lambda e, t=title_lbl: t.configure(
                foreground=self._brand()["ac1"]), add="+")
            w.bind("<Leave>", lambda e, t=title_lbl: t.configure(
                foreground=self._theme_palette()[1]), add="+")
        if self.settings.get("collapsed", {}).get(title):
            self._set_collapsed(rec, True)
        return body

    def _reorder_sections(self):
        """Order sections within each tab top-to-bottom; the tab is the
        category, so no in-column category headers are needed."""
        order = ["Plot mode", "Waterfall (2D / 3D plotting)",
                 "2D plot options", "3D plot options",
                 "Axis", "Limits & scale", "Ticks", "Frame & grid", "Colors & colormap", "Fonts",
                 "Title & axis labels", "Legend", "Colorbar", "Reference guides",
                 "Smoothing", "Defringe",
                 "Presets & projects", "Traces  (check = show,  D = decompression)",
                 "Export", "Figure"]
        by_key = {r["key"]: r for r in getattr(self, "_collapsibles", [])}
        cat_of = getattr(self, "_section_cat", {})
        prev = {}
        try:
            for key in order:
                rec = by_key.get(key)
                if not rec:
                    continue
                cat = cat_of.get(key)
                cont = rec["cont"]
                cont.pack_forget()
                p = prev.get(cat)
                if p is None:
                    cont.pack(fill="x", pady=(2, 7))
                else:
                    cont.pack(fill="x", pady=(2, 7), after=p)
                prev[cat] = cont
        except Exception:
            pass

    def _set_collapsed(self, rec, collapse):
        rec["collapsed"] = collapse
        ic = getattr(self, "_icons", {})
        if collapse:
            rec["body"].pack_forget()
            img = ic.get("caret_closed")
        else:
            rec["body"].pack(fill="x")
            img = ic.get("caret_open")
        if img is not None:
            rec["caret"].config(image=img, width=16)
        else:
            rec["caret"].config(text="\u25b6" if collapse
                                else "\u25bc")

    def _toggle_section(self, rec):
        self._set_collapsed(rec, not rec["collapsed"])
        self._save_collapsed()

    def _save_collapsed(self):
        self.settings["collapsed"] = {r["key"]: r["collapsed"]
                                      for r in self._collapsibles}
        self._save_settings()

    def _collapse_all(self, collapse=True):
        for rec in getattr(self, "_collapsibles", []):
            self._set_collapsed(rec, collapse)
        self._save_collapsed()

    def _section_text(self, rec):
        """Cached lowercase blob of every label/option text in a section,
        used by the control search box."""
        if rec.get("search_text") is None:
            parts = [rec["key"]]
            def walk(w):
                for c in w.winfo_children():
                    for opt in ("text", "values"):
                        try:
                            v = c.cget(opt)
                        except Exception:
                            v = None
                        if v:
                            parts.append(" ".join(str(x) for x in v)
                                         if isinstance(v, (list, tuple))
                                         else str(v))
                    walk(c)
            walk(rec["body"])
            rec["search_text"] = " ".join(parts).lower()
        return rec["search_text"]

    def _mapped_combo(self, parent, var, mapping, command=None, **kw):
        """A readonly combobox that SHOWS friendly labels while var keeps its
        short code value (so presets/projects stay compatible). Two-way
        synced; command (if given) runs after a pick."""
        inv = {v: k for k, v in mapping.items()}
        disp = tk.StringVar(value=mapping.get(var.get(), var.get()))
        cb = ttk.Combobox(parent, textvariable=disp, state="readonly",
                          values=list(mapping.values()), **kw)

        def to_code(_e=None):
            var.set(inv.get(disp.get(), disp.get()))
            if command:
                command()

        def from_code(*_a):
            d = mapping.get(var.get(), var.get())
            if disp.get() != d:
                disp.set(d)
        cb.bind("<<ComboboxSelected>>", to_code)
        var.trace_add("write", from_code)
        return cb

    def _filter_sections(self, *a):
        if getattr(self, "_ph_active", False):
            q = ""                       # gray placeholder text, not a query
        else:
            q = self.section_search.get().strip().lower()
        saved = self.settings.get("collapsed", {})
        for rec in getattr(self, "_collapsibles", []):
            if not q:
                self._set_collapsed(rec, bool(saved.get(rec["key"], False)))
            else:
                self._set_collapsed(rec, q not in self._section_text(rec))

    def _reset_all(self):
        if not messagebox.askyesno("Reset all",
                                   "Reset every plot control to its default?"):
            return
        self._restoring = True
        try:
            reg = self._preset_registry()
            for k, v in reg.items():
                if k in self._defaults:
                    try:
                        v.set(self._defaults[k])
                    except Exception:
                        pass
            self.smooth_params = dict(smoothing.DEFAULTS); self.smooth_cache.clear()
            for vv in (self.xmin, self.xmax, self.ymin, self.ymax, self.zmin,
                       self.zmax, self.xmaj, self.xminor, self.ymaj, self.yminor,
                       self.zmaj, self.zminor, self.title_v, self.xlabel_v,
                       self.ylabel_v):
                vv.set("")
            for k in self._label_edited:
                self._label_edited[k] = False
            for k in self._tick_edited:
                self._tick_edited[k] = False
        finally:
            self._restoring = False
        self._log_action("Reset all controls to defaults")
        self._redraw()
        self._push_undo("reset all")

    def _build_center(self, p):
        # browser-style session tabs live where the "Plot Area" title was;
        # populated by _render_tabs once sessions exist (end of __init__)
        self._tabbar = tk.Frame(p)
        self._tabbar.pack(side="top", fill="x", padx=4, pady=(4, 0))
        self._build_raw_banner(p)
        # vertical paned window: plot on top, a resizable data drawer below.
        # The sash between them lets the user drag the drawer height.
        self._center_pw = ttk.PanedWindow(p, orient="vertical")
        self._center_pw.pack(side="top", fill="both", expand=True)
        plot_pane = ttk.Frame(self._center_pw)
        self._center_pw.add(plot_pane, weight=4)
        self.fig = Figure(figsize=(6, 5), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.fig, master=plot_pane)
        self.canvas.get_tk_widget().pack(side="top", fill="both", expand=True)
        self.canvas.mpl_connect("motion_notify_event", self._on_motion)
        self.canvas.mpl_connect("button_press_event", self._on_press)
        self.canvas.mpl_connect("button_release_event", self._on_release)
        self.canvas.mpl_connect("scroll_event", self._on_scroll)
        self._build_drawer(self._center_pw)   # drawer is a pane, added on toggle
        # bottom bar: slim plot toolbar (left) + status (center) + Data (right)
        barwrap = ttk.Frame(p); barwrap.pack(side="bottom", fill="x",
                                             pady=(4, 4))
        self._center_barwrap = barwrap
        self._build_plot_toolbar(barwrap)
        centerf = ttk.Frame(barwrap)
        centerf.place(relx=0.5, rely=0.5, anchor="center")
        self.status_lbl = self._lbl(centerf, text="", anchor="center",
                                    font=(UI_FONT, 8), foreground="#888")
        self.status_lbl.pack()
        self.cursor_lbl = self._lbl(centerf, text="", anchor="center",
                                    font=("Consolas", 8))
        self.cursor_lbl.pack()
        # prominent Data-table toggle, moved off the tab strip to the bottom
        self._data_btn = self._brand_button(barwrap, " Data table",
                                            self._toggle_drawer)
        self._data_btn.pack(side="right", padx=(0, 6))
        Tooltip(self._data_btn, "Show/hide the raw data table for the current "
                                "trace; drag its top edge to resize (Ctrl+D).")
        # clickable "recent runs" strip, shown on the blank state only
        self._recent_bar = tk.Frame(p)

    def _build_plot_toolbar(self, parent):
        """Slim replacement for the matplotlib nav toolbar. Keeps the
        NavigationToolbar2Tk instance alive (widget hidden) so its pan/zoom
        machinery and the mode string used by _toolbar_active still work."""
        navf = ttk.Frame(parent)              # hidden host for the real toolbar
        self.nav_toolbar = NavigationToolbar2Tk(self.canvas, navf)
        try:
            self.nav_toolbar.update()
            self.nav_toolbar.pack_forget()    # keep instance, hide the widget
        except Exception:
            pass
        strip = ttk.Frame(parent); strip.pack(side="left", padx=(2, 0))

        self._tb_btns = {}

        def mk(key, text, cmd, tip):
            # text buttons carry a left icon; blank text = square icon-only
            b = ttk.Button(strip, text=text, command=cmd,
                           **({"width": len(text) + 1} if text else {}))
            b.pack(side="left", padx=1)
            Tooltip(b, tip)
            self._tb_btns[key] = b
            return b
        mk("reset", "Reset view", self._reset_view,
           "Reset the view to auto-fit (2D and 3D).")
        self._pan_btn = mk("pan", "", self._toolbar_pan,
                           "Pan: drag to move; click again to turn off.")
        self._zoom_btn = mk("zoom", "", self._toolbar_zoom,
                            "Zoom: drag a box; click again to turn off.")
        mk("save", "", self._save_plot, "Save the figure (Ctrl+S).")

    def _toolbar_pan(self):
        try:
            self.nav_toolbar.pan()
        except Exception:
            pass
        self._sync_toolbar_buttons()

    def _toolbar_zoom(self):
        try:
            self.nav_toolbar.zoom()
        except Exception:
            pass
        self._sync_toolbar_buttons()

    def _sync_toolbar_buttons(self):
        mode = ""
        try:
            mode = str(self.nav_toolbar.mode or "").lower()
        except Exception:
            pass
        for btn, key in ((getattr(self, "_pan_btn", None), "pan"),
                         (getattr(self, "_zoom_btn", None), "zoom")):
            if btn is not None:
                try:
                    btn.configure(style="Accent.TButton" if key in mode
                                  else "TButton")
                except Exception:
                    pass

    def _update_toolbar_mode(self):
        """Pan/Zoom are 2D tools; matplotlib's 3D pan-zoom is disorienting
        (the axes don't track the content). Disable them in ridge mode and
        drop any active mode when entering it."""
        is3 = (self.wf_mode.get() == "3D ridge"
               and self.mode.get() != "inspect")
        for key in ("pan", "zoom"):
            b = getattr(self, "_tb_btns", {}).get(key)
            if b is None:
                continue
            try:
                b.state(["disabled"] if is3 else ["!disabled"])
            except tk.TclError:
                pass
        if is3:
            try:
                m = str(self.nav_toolbar.mode or "")
                if "pan" in m:
                    self.nav_toolbar.pan()
                elif "zoom" in m:
                    self.nav_toolbar.zoom()
            except Exception:
                pass
            self._sync_toolbar_buttons()

    # ---- raw-data drawer (Excel-style view of the active trace) -----------
    def _build_drawer(self, parent):
        """Collapsible bottom drawer showing the active trace's raw table.
        Hidden until toggled (Data button on the tab strip, or Ctrl+D)."""
        self._drawer = ttk.Frame(parent)      # packed on demand
        self._drawer_shown = False            # explicit (winfo_ismapped is
        #                                       unreliable for withdrawn/min)
        hdr = ttk.Frame(self._drawer, padding=(4, 3)); hdr.pack(side="top",
                                                                fill="x")
        self._drawer_title = self._lbl(hdr, text="Raw data",
                                       font=(UI_FONT, 10, "bold"))
        self._drawer_title.pack(side="left", padx=(2, 6))
        self.drawer_trace = tk.StringVar()
        self._drawer_combo = ttk.Combobox(hdr, textvariable=self.drawer_trace,
                                           state="readonly", width=24)
        self._drawer_combo.pack(side="left")
        self._drawer_combo.bind("<<ComboboxSelected>>",
                                lambda e: self._refresh_drawer())
        self.drawer_defr = tk.BooleanVar(value=False)
        self.drawer_smooth = tk.BooleanVar(value=False)
        ttk.Checkbutton(hdr, text="defringed", variable=self.drawer_defr,
                        command=self._refresh_drawer).pack(side="left",
                                                           padx=(10, 0))
        ttk.Checkbutton(hdr, text="smoothed", variable=self.drawer_smooth,
                        command=self._refresh_drawer).pack(side="left",
                                                           padx=(4, 0))
        ttk.Button(hdr, text="close", width=7,
                   command=lambda: self._toggle_drawer(False)).pack(
                       side="right")
        xb = ttk.Button(hdr, text="Open in Excel",
                        command=self._drawer_open_excel)
        xb.pack(side="right", padx=(0, 6))
        Tooltip(xb, "Write this trace to a temporary CSV and open it in your "
                    "default spreadsheet app.")
        cpb = ttk.Button(hdr, text="Copy all (TSV)",
                         command=self._drawer_copy_tsv)
        cpb.pack(side="right", padx=(0, 6))
        Tooltip(cpb, "Copy the whole table (tab-separated) - paste straight "
                     "into Excel. Ctrl+C copies just the selected rows.")
        body = ttk.Frame(self._drawer); body.pack(side="top", fill="both",
                                                  expand=True)
        self._drawer_base_cols = ["Wavelength_nm", "Wavenumber_cm-1",
                                  "Absorbance", "Dark", "Background", "Sample"]
        self._drawer_tv = ttk.Treeview(body, show="headings", height=9,
                                       selectmode="extended")
        vsb = ttk.Scrollbar(body, orient="vertical",
                            command=self._drawer_tv.yview)
        self._drawer_tv.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self._drawer_tv.pack(side="left", fill="both", expand=True)
        self._drawer_tv.bind("<Control-c>",
                             lambda e: self._drawer_copy_selection())

    def _toggle_drawer(self, show=None):
        d = getattr(self, "_drawer", None)
        pw = getattr(self, "_center_pw", None)
        if d is None or pw is None:
            return "break"
        show = (not self._drawer_shown) if show is None else bool(show)
        self._drawer_shown = show
        present = str(d) in [str(x) for x in pw.panes()]
        if show and not present:
            pw.add(d, weight=1)               # a resizable pane below the plot
            self._refresh_drawer()
        elif not show and present:
            pw.forget(d)
        self._update_data_btn()
        return "break"

    def _update_data_btn(self):
        b = getattr(self, "_data_btn", None)
        if b is None:
            return
        try:
            b.configure(text=(" Hide data" if self._drawer_shown
                              else " Data table"))
        except Exception:
            pass

    def _refresh_drawer(self):
        d = getattr(self, "_drawer", None)
        if d is None or not getattr(self, "_drawer_shown", False):
            return
        labels = [r["label"] for r in self.results]
        self._drawer_combo.config(values=labels)
        sel = self.drawer_trace.get()
        if sel not in labels:
            sel = labels[0] if labels else ""
            self.drawer_trace.set(sel)
        r = next((x for x in self.results if x["label"] == sel), None)
        cols = list(self._drawer_base_cols)
        extra = []
        if r is not None and self.drawer_defr.get():
            cols.append("Absorbance_defringed")
            try:
                extra.append(np.asarray(
                    self._notch_result(r)["absorbance"], float))
            except Exception:
                extra.append(None)
        if r is not None and self.drawer_smooth.get():
            cols.append("Absorbance_smoothed")
            try:
                extra.append(np.asarray(self._smoothed(r), float))
            except Exception:
                extra.append(None)
        tv = self._drawer_tv
        tv.delete(*tv.get_children())
        tv["columns"] = cols
        for c in cols:
            tv.heading(c, text=c)
            tv.column(c, width=92, anchor="center", stretch=True)
        if r is None:
            return
        wl = np.asarray(r["wl"], float); wn = np.asarray(r["wn"], float)
        ab = np.asarray(r["absorbance"], float)
        dk = np.asarray(r["dark_c"], float); bg = np.asarray(r["bg_c"], float)
        sm = np.asarray(r["samp_c"], float)

        def fmt(v):
            return "" if (isinstance(v, float) and not np.isfinite(v)) \
                else ("%.6g" % v)
        for i in range(len(wl)):
            row = [fmt(wl[i]), fmt(wn[i]), fmt(ab[i]), fmt(dk[i]),
                   fmt(bg[i]), fmt(sm[i])]
            for arr in extra:
                row.append(fmt(arr[i]) if (arr is not None and i < len(arr))
                           else "")
            tv.insert("", "end", values=row,
                      tags=("odd" if i % 2 else "even",))

    def _drawer_tsv(self, only_selected):
        tv = self._drawer_tv
        cols = list(tv["columns"])
        lines = ["\t".join(cols)]
        items = tv.selection() if only_selected else tv.get_children()
        for it in items:
            lines.append("\t".join(str(v) for v in tv.item(it, "values")))
        return "\n".join(lines)

    def _to_clipboard(self, text):
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
        except Exception:
            pass

    def _drawer_copy_tsv(self):
        self._to_clipboard(self._drawer_tsv(False))
        self._toast("Table copied (TSV)")
        self._logline("Copied %d data rows (TSV) to the clipboard."
                      % len(self._drawer_tv.get_children()))

    def _drawer_copy_selection(self):
        sel = self._drawer_tv.selection()
        self._to_clipboard(self._drawer_tsv(bool(sel)))
        return "break"

    def _drawer_open_excel(self):
        sel = self.drawer_trace.get()
        r = next((x for x in self.results if x["label"] == sel), None)
        if r is None:
            return
        import tempfile
        import csv as _csv
        safe = re.sub(r"[^A-Za-z0-9.+-]+", "_", sel) or "trace"
        path = os.path.join(tempfile.gettempdir(), "DAC_%s.csv" % safe)
        try:
            wl = np.asarray(r["wl"]); wn = np.asarray(r["wn"])
            ab = np.asarray(r["absorbance"]); dk = np.asarray(r["dark_c"])
            bg = np.asarray(r["bg_c"]); sm = np.asarray(r["samp_c"])
            with open(path, "w", newline="") as f:
                w = _csv.writer(f)
                w.writerow(self._drawer_base_cols)
                for i in range(len(wl)):
                    w.writerow([wl[i], wn[i], ab[i], dk[i], bg[i], sm[i]])
            if hasattr(os, "startfile"):
                os.startfile(path)          # Windows -> Excel/default
            else:
                self._open_path(path)       # mac/linux best-effort
            self._logline("Wrote %s and opened it in the default app." % path)
        except Exception as e:
            messagebox.showerror("Open in Excel", str(e))

    # ---- raw-only callout banner + one-shot motion helper ----------------
    def _build_raw_banner(self, parent):
        """Dismissible callout shown after a run when some traces have no
        absorbance, offering one click to switch the plot to a raw channel.
        Built from tk (not ttk) widgets so its background can be animated
        without triggering any relayout of the plot."""
        self.raw_banner = tk.Frame(parent, bd=0, highlightthickness=0)
        inner = tk.Frame(self.raw_banner, bd=0, highlightthickness=0)
        inner.pack(fill="x", padx=10, pady=5)
        self.raw_banner_inner = inner   # recolored along with the banner
        self.raw_banner_lbl = tk.Label(inner, text="", anchor="w",
                                       font=(UI_FONT, 10))
        self.raw_banner_lbl.pack(side="left", fill="x", expand=True)
        self.raw_banner_btn = tk.Button(inner, text="Show channel",
                                        relief="flat", bd=0, cursor="hand2",
                                        padx=8, command=self._raw_banner_switch)
        self.raw_banner_btn.pack(side="left", padx=(8, 0))
        self.raw_banner_x = tk.Button(inner, text="\u00d7", relief="flat", bd=0,
                                      cursor="hand2", padx=4,
                                      command=self._hide_raw_banner)
        self.raw_banner_x.pack(side="left", padx=(6, 0))
        self._raw_banner_channel = "background"

    def _raw_banner_switch(self):
        self.ydata.set(self._raw_banner_channel)   # trace fires the redraw
        self._hide_raw_banner()

    def _raw_banner_widgets(self):
        return [w for w in (getattr(self, "raw_banner", None),
                            getattr(self, "raw_banner_inner", None),
                            getattr(self, "raw_banner_lbl", None),
                            getattr(self, "raw_banner_btn", None),
                            getattr(self, "raw_banner_x", None)) if w is not None]

    def _set_banner_bg(self, color):
        for w in self._raw_banner_widgets():
            try:
                w.config(bg=color)
            except tk.TclError:
                pass

    def _raw_banner_accent(self):
        """Primary accent for banners / tab underline = the triad's ac1."""
        try:
            return self._brand()["ac1"]
        except Exception:
            return "#2f6fd6"

    def _show_raw_banner(self, n, channel):
        if not hasattr(self, "raw_banner"):
            return
        self._raw_banner_channel = channel
        self.raw_banner_lbl.config(
            text=("  1 trace has no absorbance and is hidden in this view."
                  if n == 1 else
                  "  %d traces have no absorbance and are hidden in this "
                  "view." % n))
        self.raw_banner_btn.config(text="Show %s" % channel)
        accent = self._raw_banner_accent()
        for w in (self.raw_banner_lbl, self.raw_banner_btn, self.raw_banner_x):
            try:
                w.config(fg="#ffffff", activeforeground="#ffffff",
                         activebackground=accent)
            except tk.TclError:
                pass
        try:
            self.raw_banner.pack(side="top", fill="x",
                                 before=self.canvas.get_tk_widget())
        except Exception:
            self.raw_banner.pack(side="top", fill="x")
        # one-shot colour settle: lighter tint -> full accent. Pure background
        # recolour, so no layout or canvas work happens per frame.
        start = self._mix(accent, "#ffffff", 0.6)
        self.animate("raw_banner", 8, 18,
                     lambda i, nn: self._set_banner_bg(
                         self._mix(start, accent, (i + 1) / nn)),
                     done=lambda: self._set_banner_bg(accent))

    def _hide_raw_banner(self):
        self._cancel_anim("raw_banner")
        if hasattr(self, "raw_banner"):
            try:
                self.raw_banner.pack_forget()
            except tk.TclError:
                pass

    @staticmethod
    def _mix(c1, c2, t):
        """Blend two #rrggbb colours; t in [0,1]."""
        try:
            a = tuple(int(c1[i:i + 2], 16) for i in (1, 3, 5))
            b = tuple(int(c2[i:i + 2], 16) for i in (1, 3, 5))
        except Exception:
            return c2
        t = 0.0 if t < 0 else 1.0 if t > 1 else t
        return "#%02x%02x%02x" % tuple(int(a[k] + (b[k] - a[k]) * t)
                                       for k in range(3))

    def animate(self, key, steps, interval_ms, step, done=None):
        """Short, one-shot, self-cancelling animation. Honours the reduce-motion
        setting and performance mode by jumping straight to the final frame.
        step(i, steps) runs for i=0..steps-1; done() runs once at the end. No
        animation ever loops or runs while the window is idle."""
        self._cancel_anim(key)
        if not hasattr(self, "_anim_jobs"):
            self._anim_jobs = {}
        steps = max(1, int(steps))
        reduce = False
        try:
            reduce = bool(self.reduce_motion.get()) or bool(self.perf_mode.get())
        except Exception:
            pass
        if reduce:
            try:
                step(steps - 1, steps)
            except Exception:
                pass
            if done:
                try:
                    done()
                except Exception:
                    pass
            return

        def _tick(i=0):
            if i >= steps:
                self._anim_jobs.pop(key, None)
                if done:
                    try:
                        done()
                    except Exception:
                        pass
                return
            try:
                step(i, steps)
            except Exception:
                pass
            self._anim_jobs[key] = self.root.after(
                interval_ms, lambda: _tick(i + 1))
        _tick()

    def _cancel_anim(self, key):
        if not hasattr(self, "_anim_jobs"):
            self._anim_jobs = {}
            return
        job = self._anim_jobs.pop(key, None)
        if job is not None:
            try:
                self.root.after_cancel(job)
            except Exception:
                pass

    def _on_motion(self, ev):
        if ev.inaxes and ev.xdata is not None and ev.ydata is not None:
            self.cursor_lbl.config(text="x = %.3f    y = %.4f"
                                   % (ev.xdata, ev.ydata))
        else:
            self.cursor_lbl.config(text="")
        # rubber-band rectangle for the drag-box zoom
        dz = getattr(self, "_dragz", None)
        if dz and ev.inaxes is self.ax and ev.xdata is not None \
                and ev.ydata is not None:
            # same both-axes test as the accept check in _on_release, so a
            # box is only drawn when releasing would actually zoom
            if dz["rect"] is None and (abs(ev.x - dz["px"]) > 6
                                       and abs(ev.y - dz["py"]) > 6):
                from matplotlib.patches import Rectangle
                dz["rect"] = Rectangle((dz["x"], dz["y"]), 0, 0, fill=False,
                                       linestyle="--", linewidth=0.8,
                                       edgecolor="#999999")
                self.ax.add_patch(dz["rect"])
            if dz["rect"] is not None:
                dz["rect"].set_bounds(min(dz["x"], ev.xdata),
                                      min(dz["y"], ev.ydata),
                                      abs(ev.xdata - dz["x"]),
                                      abs(ev.ydata - dz["y"]))
                self.canvas.draw_idle()

    # ---- zoom: drag-box, scroll wheel, toolbar persistence ----------------
    def _toolbar_active(self):
        """True while the matplotlib toolbar's pan or zoom tool is selected."""
        try:
            return bool(str(getattr(self.nav_toolbar, "mode", "") or ""))
        except Exception:
            return False

    def _zoomable_2d(self):
        return getattr(self.ax, "name", "") != "3d"

    def _on_press(self, ev):
        self._dragz = None
        if (ev.button == 3 and ev.inaxes is self.ax
                and not self._toolbar_active()):
            self._reset_axes()           # right-click = zoom out to auto
            return
        if (ev.button != 1 or ev.inaxes is not self.ax or ev.xdata is None
                or ev.ydata is None or not self._zoomable_2d()
                or self._toolbar_active()):
            return
        self._dragz = {"px": ev.x, "py": ev.y, "x": ev.xdata, "y": ev.ydata,
                       "rect": None, "ev": ev}

    def _on_release(self, ev):
        if self._toolbar_active():
            # the toolbar's zoom/pan just changed the view; capture it so the
            # zoom survives the next redraw
            self.root.after(80, self._capture_zoom_limits)
            return
        dz = getattr(self, "_dragz", None)
        self._dragz = None
        if dz is None:
            return
        if dz["rect"] is None:
            # no real drag: treat as a plain click (absorbance readout)
            self._on_plot_click(dz["ev"])
            return
        try:
            dz["rect"].remove()
        except Exception:
            pass
        if (ev.inaxes is self.ax and ev.xdata is not None
                and ev.ydata is not None
                and abs(ev.x - dz["px"]) > 6 and abs(ev.y - dz["py"]) > 6):
            self.xmin.set("%.6g" % min(dz["x"], ev.xdata))
            self.xmax.set("%.6g" % max(dz["x"], ev.xdata))
            self.ymin.set("%.6g" % min(dz["y"], ev.ydata))
            self.ymax.set("%.6g" % max(dz["y"], ev.ydata))
            self.autoscale.set(False)
            self._log_action("Drag-box zoom")
            self._redraw()
        else:
            self.canvas.draw_idle()

    def _on_scroll(self, ev):
        """Mouse-wheel zoom on 2D plots, centred on the cursor. Persists by
        feeding the Axis-limits boxes (Auto turns off)."""
        if (ev.inaxes is not self.ax or ev.xdata is None or ev.ydata is None
                or not self._zoomable_2d()):
            return
        f = 0.9 if ev.button == "up" else 1.1 if ev.button == "down" else None
        if f is None:
            return
        x0, x1 = self.ax.get_xlim(); y0, y1 = self.ax.get_ylim()
        self.ax.set_xlim(ev.xdata - (ev.xdata - x0) * f,
                         ev.xdata + (x1 - ev.xdata) * f)
        self.ax.set_ylim(ev.ydata - (ev.ydata - y0) * f,
                         ev.ydata + (y1 - ev.ydata) * f)
        self._capture_zoom_limits()
        self.canvas.draw_idle()

    def _capture_zoom_limits(self):
        """Copy the live 2D view into the limit boxes and turn Auto off, so
        toolbar/wheel/drag zooms survive the next redraw."""
        if not self._zoomable_2d():
            return
        try:
            x0, x1 = self.ax.get_xlim(); y0, y1 = self.ax.get_ylim()
        except Exception:
            return
        self.xmin.set("%.6g" % min(x0, x1)); self.xmax.set("%.6g" % max(x0, x1))
        self.ymin.set("%.6g" % min(y0, y1)); self.ymax.set("%.6g" % max(y0, y1))
        self.autoscale.set(False)

    def _apply_limits(self):
        """Use the typed min/max boxes (turns Auto off) and redraw."""
        self.autoscale.set(False)
        self._redraw()

    def _reset_axes(self):
        """Clear the limit boxes and re-enable Auto (fit to data)."""
        for v in (self.xmin, self.xmax, self.ymin, self.ymax,
                  self.zmin, self.zmax):
            v.set("")
        self.autoscale.set(True)
        self._log_action("Axes reset (auto-fit)")
        self._redraw()

    # ---- View (2D): pan pad / zoom / fit ----------------------------------
    def _bind_repeat(self, btn, cmd, first=350, every=120):
        """Fire cmd on press and keep firing while the button is held."""
        state = {"id": None}
        def fire():
            cmd()
            state["id"] = btn.after(every, fire)
        def press(_e):
            cmd()
            state["id"] = btn.after(first, fire)
            return "break"
        def release(_e):
            if state["id"] is not None:
                btn.after_cancel(state["id"])
                state["id"] = None
        btn.bind("<ButtonPress-1>", press)
        btn.bind("<ButtonRelease-1>", release)
        btn.bind("<Leave>", release)

    def _pan2d(self, dx, dy, frac=0.05):
        """Nudge the 2D view by a fraction of the current span. Uses the raw
        limit values, so flipped axes still pan in the arrow's screen
        direction. Persists via the limit boxes like every other zoom."""
        if not self._zoomable_2d():
            return
        x0, x1 = self.ax.get_xlim(); y0, y1 = self.ax.get_ylim()
        sx = (x1 - x0) * frac * dx; sy = (y1 - y0) * frac * dy
        self.ax.set_xlim(x0 + sx, x1 + sx)
        self.ax.set_ylim(y0 + sy, y1 + sy)
        self._capture_zoom_limits()
        self.canvas.draw_idle()

    def _zoom2d(self, factor, axis=None):
        """Zoom about the view center; factor < 1 zooms in. axis = 'X' /
        'Y' / 'both' (default: the View (2D) radio choice)."""
        if not self._zoomable_2d():
            return
        axis = axis or (self.zoom2d_axis.get()
                        if getattr(self, "zoom2d_axis", None) else "both")
        x0, x1 = self.ax.get_xlim(); y0, y1 = self.ax.get_ylim()
        cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        hx, hy = (x1 - x0) / 2.0, (y1 - y0) / 2.0
        if axis in ("X", "both"):
            hx *= factor
        if axis in ("Y", "both"):
            hy *= factor
        self.ax.set_xlim(cx - hx, cx + hx)
        self.ax.set_ylim(cy - hy, cy + hy)
        self._capture_zoom_limits()
        self.canvas.draw_idle()

    def _fit2d(self, which="both"):
        """Fit X, Y, or both axes to the plotted data. 'both' is the same as
        Reset axes (Auto back on); X/Y refit one axis and keep the other,
        preserving axis orientation (flips)."""
        if not self._zoomable_2d():
            return
        if which == "both":
            self._reset_axes()
            return
        try:
            bx = self.ax.dataLim.intervalx
            by = self.ax.dataLim.intervaly
        except Exception:
            return
        import math
        if which == "X" and all(map(math.isfinite, bx)):
            m = (bx[1] - bx[0]) * 0.02
            lo, hi = bx[0] - m, bx[1] + m
            x0, x1 = self.ax.get_xlim()
            self.ax.set_xlim((hi, lo) if x0 > x1 else (lo, hi))
        if which == "Y" and all(map(math.isfinite, by)):
            m = (by[1] - by[0]) * 0.05
            lo, hi = by[0] - m, by[1] + m
            y0, y1 = self.ax.get_ylim()
            self.ax.set_ylim((hi, lo) if y0 > y1 else (lo, hi))
        self._capture_zoom_limits()
        self.canvas.draw_idle()

    # ---- View (3D): arrow-key camera orbit (new; sliders/presets remain)
    def _wf3d_active(self):
        return (self.wf_mode.get() == "3D ridge"
                and self.mode.get() != "inspect")

    def _orbit3d(self, dx, dy, step=3.0):
        """Rotate the 3D camera by small steps: left/right = azimuth,
        up/down = elevation. Moves the same variables as the sliders, so
        the sliders follow along."""
        az = float(self.wf3d_azim.get()) + step * dx
        while az > 180.0:
            az -= 360.0
        while az < -180.0:
            az += 360.0
        el = max(0.0, min(90.0, float(self.wf3d_elev.get()) + step * dy))
        self.wf3d_azim.set(az)
        self.wf3d_elev.set(el)
        self._sync_slider_entries()
        self._redraw()

    def _zoom3d_step(self, f):
        z = max(0.5, min(2.0, float(self.wf3d_zoom.get()) * f))
        self.wf3d_zoom.set(z)
        self._sync_slider_entries()
        self._redraw()

    def _hot_pan(self, dx, dy):
        if self._typing_in_box():
            return
        if self._wf3d_active():
            self._orbit3d(dx, dy)     # arrows orbit the 3D camera
        else:
            self._pan2d(dx, dy)

    def _hot_zoom(self, f):
        if self._typing_in_box():
            return
        if self._wf3d_active():
            self._zoom3d_step(1.0 / f)  # 2D f<1 = closer; 3D var >1 = closer
        else:
            self._zoom2d(f)

    def _hot_fit(self):
        if self._typing_in_box():
            return
        if self._wf3d_active():
            self._reset_3d_view()
        else:
            self._fit2d("both")


    # ---- right pane: scrollable controls (width follows the pane) --------
    def _build_right(self, outer):
        self._lbl(outer, text="Plotting Options",
                  font=(UI_FONT, 11, "bold")).pack(side="top", anchor="w",
                                                       padx=6, pady=(6, 2))
        srow = ttk.Frame(outer); srow.pack(side="top", fill="x", padx=6, pady=(0, 3))
        self._lbl(srow, text="Find:").pack(side="left")
        self.section_search = tk.StringVar()
        se = ttk.Entry(srow, textvariable=self.section_search)
        # gray placeholder (D5); _filter_sections ignores it via _ph_active
        self._ph_active = False
        _ph_text = "Find a setting\u2026"

        def _ph_set(_e=None):
            if not self.section_search.get():
                self._ph_active = True
                self.section_search.set(_ph_text)
                try:
                    se.configure(foreground="#888888")
                except tk.TclError:
                    pass

        def _ph_clear(_e=None):
            if self._ph_active:
                self._ph_active = False
                self.section_search.set("")
                try:
                    se.configure(foreground="")
                except tk.TclError:
                    pass
        se.bind("<FocusIn>", _ph_clear)
        se.bind("<FocusOut>", _ph_set)
        se.bind("<Escape>", lambda e: (self.section_search.set(""),
                                       self.root.focus_set()))
        se.after_idle(_ph_set)
        se.pack(side="left", fill="x", expand=True, padx=(3, 0))
        self.section_search.trace_add("write", self._filter_sections)
        Tooltip(se, "Type to find a control (e.g. 'grid', 'legend', 'ridge'). "
                    "Matching sections expand and the rest collapse; clear the "
                    "box to restore your layout.")
        # 4-tab control notebook replaces the single long scroll column.
        self._tab_canvases = []
        self._tab_frames = {}
        self._section_cat = {}
        cbar = ttk.Frame(outer)
        cbar.pack(side="top", fill="x", padx=6, pady=(0, 2))
        self._collapse_btn = ttk.Button(cbar, text="Collapse all", width=13,
                   command=lambda: self._collapse_all(True))
        self._collapse_btn.pack(side="left")
        self._expand_btn = ttk.Button(cbar, text="Expand all", width=12,
                   command=lambda: self._collapse_all(False))
        self._expand_btn.pack(side="left", padx=(4, 0))
        self._resetall_btn = ttk.Button(cbar, text="Reset all", width=11,
                   command=self._reset_all)
        self._resetall_btn.pack(side="right")
        self.rnotebook = ttk.Notebook(outer)
        self.rnotebook.pack(side="top", fill="both", expand=True)
        self.rnotebook.bind_all("<MouseWheel>", self._on_wheel)
        # Wheel over a value control must NOT change it (accidental edits
        # while scrolling the panel). TCombobox/TSpinbox/TScale carry Tk
        # class bindings that adjust the value on wheel; class bindings fire
        # BEFORE bind_all, so we suppress them with "break" and forward the
        # event to the panel scroller ourselves. The open combobox dropdown
        # (a popdown Listbox) keeps its native wheel scrolling.
        def _wheel_guard(e):
            self._on_wheel(e)
            return "break"
        for _cls in ("TCombobox", "TSpinbox", "Spinbox", "TScale", "Scale"):
            self.root.bind_class(_cls, "<MouseWheel>", _wheel_guard)
        self.rnotebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)
        _tabspec = [
            ("Plot", ["Plot mode", "Waterfall (2D / 3D plotting)",
                      "2D plot options",
                      "3D plot options"]),
            ("Axes", ["Axis", "Limits & scale", "Ticks", "Frame & grid"]),
            ("Style", ["Colors & colormap", "Fonts", "Title & axis labels", "Legend", "Colorbar", "Reference guides"]),
            ("Data", ["Smoothing", "Defringe",
                      "Traces  (check = show,  D = decompression)"]),
            ("Export", ["Presets & projects", "Export", "Figure"]),
        ]
        for _label, _titles in _tabspec:
            self._tab_frames[_label] = self._make_scroll_page(self.rnotebook,
                                                              _label)
            for _t in _titles:
                self._section_cat[_t] = _label
        # fallback parent for any unmapped section
        self.rframe = self._tab_frames["Plot"]
        r = self.rframe
        self._collapsibles = []

        # --- Plot mode (wide boxes) ---
        pm = self._group(r, "Plot mode")
        self.mode = tk.StringVar(value="overlay")
        ttk.Radiobutton(pm, text="Overlay all pressures", value="overlay",
                        variable=self.mode, command=self._redraw).pack(anchor="w")
        ttk.Radiobutton(pm, text="Inspect one pressure", value="inspect",
                        variable=self.mode, command=self._redraw).pack(anchor="w")
        o = ttk.Frame(pm); o.pack(fill="x", pady=(4, 1))
        self._lbl(o, text="Overlay Y", width=9).pack(side="left")
        self.ydata = tk.StringVar(value="absorbance")
        ttk.Combobox(o, textvariable=self.ydata, state="readonly",
                     values=["absorbance", "sample", "background", "dark"]
                     ).pack(side="left", fill="x", expand=True)
        self.ydata.trace_add("write", lambda *a: self._redraw())
        i = ttk.Frame(pm); i.pack(fill="x", pady=(4, 1))
        self._lbl(i, text="Inspect P", width=9).pack(side="left")
        self.inspect_p = tk.StringVar()
        self.inspect_combo = ttk.Combobox(i, textvariable=self.inspect_p,
                                          state="readonly")
        self.inspect_combo.pack(side="left", fill="x", expand=True)
        self.inspect_p.trace_add("write", lambda *a: self._redraw())
        Tooltip(self.inspect_combo, "In 'Inspect one pressure' mode, choose which pressure point to display.")
        cf = ttk.Frame(pm); cf.pack(fill="x", pady=(4, 1))
        self._lbl(cf, text="Channels").pack(side="left")
        self.ins_S = tk.BooleanVar(value=True); self.ins_B = tk.BooleanVar(value=True)
        self.ins_D = tk.BooleanVar(value=True); self.ins_A = tk.BooleanVar(value=True)
        for t, v in [("S", self.ins_S), ("B", self.ins_B), ("D", self.ins_D),
                     ("Abs", self.ins_A)]:
            ttk.Checkbutton(cf, text=t, variable=v,
                            command=self._redraw).pack(side="left")
        ttk.Separator(pm, orient="horizontal").pack(fill="x", pady=4)
        rr = ttk.Frame(pm); rr.pack(fill="x", pady=2)
        rdb = ttk.Button(rr, text="Absorbance readout at\u2026",
                         command=self._peak_readout)
        rdb.pack(side="left", fill="x", expand=True)
        Tooltip(rdb, "Enter a wavelength (nm) to list the absorbance at that "
                     "point for every shown pressure - handy for tracking a band.")
        self._click_readout = tk.BooleanVar(value=False)
        crc = ttk.Checkbutton(rr, text="on click",
                              variable=self._click_readout)
        crc.pack(side="left", padx=(6, 0))
        Tooltip(crc, "Same readout, interactively: left-click anywhere on a "
                     "2D plot to open the table at that wavelength.")

        # --- X axis ---
        ax = self._group(r, "Axis")
        # all four axes as one consistent dropdown column
        _units = {"wl": "Wavelength (nm)", "wn": "Wavenumber (cm-1)",
                  "ev": "Photon energy (eV)"}
        self.xunit = tk.StringVar(value="wl")
        xr = ttk.Frame(ax); xr.pack(fill="x", pady=1)
        self._lbl(xr, text="X axis", width=9).pack(side="left")
        xub = self._mapped_combo(xr, self.xunit, _units,
                                 command=self._redraw)
        xub.pack(side="left", fill="x", expand=True)
        Tooltip(xub, "Spectral unit of the bottom axis; the same data is "
                     "converted on the fly.")
        yr = ttk.Frame(ax); yr.pack(fill="x", pady=1)
        self._lbl(yr, text="Y axis", width=9).pack(side="left")
        ycb2 = ttk.Combobox(yr, textvariable=self.ydata, state="readonly",
                            values=["absorbance", "sample", "background",
                                    "dark"])
        ycb2.pack(side="left", fill="x", expand=True)
        Tooltip(ycb2, "What the left Y axis plots in overlay mode: computed "
                      "absorbance or a raw counts channel.")
        tr = ttk.Frame(ax); tr.pack(fill="x", pady=1)
        self._lbl(tr, text="Top axis", width=9).pack(side="left")
        self.topaxis = tk.StringVar(value="none")
        tac = self._mapped_combo(tr, self.topaxis,
                                 {"none": "none", **_units})
        tac.pack(side="left", fill="x", expand=True)
        self.topaxis.trace_add("write", lambda *a: self._redraw())
        Tooltip(tac, "Mirror a second unit across the top. Wavenumber and "
                     "energy are reciprocal in wavelength, so keep X min "
                     "above 0 when using them.")
        rr = ttk.Frame(ax); rr.pack(fill="x", pady=1)
        self._lbl(rr, text="Right axis", width=9).pack(side="left")
        self.rightaxis = tk.StringVar(value="none")
        rac = self._mapped_combo(rr, self.rightaxis,
                                 {"none": "none", "mirror": "mirror left Y",
                                  "%T": "% transmittance"})
        rac.pack(side="left", fill="x", expand=True)
        self.rightaxis.trace_add("write", lambda *a: self._redraw())
        Tooltip(rac, "Add a right Y axis: repeat the left scale, or "
                     "transmittance T = 100 x 10^-A (absorbance mode only). "
                     "2D plots only.")
        flr = ttk.Frame(ax); flr.pack(fill="x", pady=(4, 0))
        self.flipx = tk.BooleanVar()
        ttk.Checkbutton(flr, text="Flip X", variable=self.flipx,
                        command=self._redraw).pack(side="left")
        self.flipy = tk.BooleanVar()
        ttk.Checkbutton(flr, text="Flip Y", variable=self.flipy,
                        command=self._redraw).pack(side="left", padx=(12, 0))
        # axis line thickness + label gap (both axes)
        alr = ttk.Frame(ax); alr.pack(fill="x", pady=(4, 0))
        self._lbl(alr, text="Axis line", width=9).pack(side="left")
        self.spine_lw = tk.DoubleVar(value=0.8)
        sle = ttk.Entry(alr, textvariable=self.spine_lw, width=5)
        sle.pack(side="left")
        sle.bind("<Return>", lambda e: self._redraw())
        sle.bind("<FocusOut>", lambda e: self._redraw())
        self._lbl(alr, text="pt").pack(side="left", padx=(3, 0))
        Tooltip(sle, "Thickness (points) of the axis lines / spines.")
        lgr = ttk.Frame(ax); lgr.pack(fill="x", pady=(2, 2))
        self._lbl(lgr, text="Label gap", width=9).pack(side="left")
        self._lbl(lgr, text="X").pack(side="left")
        self.xlabelpad = tk.DoubleVar(value=4.0)
        xpe = ttk.Entry(lgr, textvariable=self.xlabelpad, width=4)
        xpe.pack(side="left", padx=(2, 6))
        self._lbl(lgr, text="Y").pack(side="left")
        self.ylabelpad = tk.DoubleVar(value=4.0)
        ype = ttk.Entry(lgr, textvariable=self.ylabelpad, width=4)
        ype.pack(side="left", padx=(2, 0))
        for e in (xpe, ype):
            e.bind("<Return>", lambda ev: self._redraw())
            e.bind("<FocusOut>", lambda ev: self._redraw())
        Tooltip(lgr, "Gap (points) between each axis and its label.")

        # --- Limits & scale ---
        lim = self._group(r, "Limits & scale")
        self.autoscale = tk.BooleanVar(value=True)
        self.xmin, self.xmax = tk.StringVar(), tk.StringVar()
        self.ymin, self.ymax = tk.StringVar(), tk.StringVar()
        self.zmin, self.zmax = tk.StringVar(), tk.StringVar()
        self._lim_rows = {}
        for key, a, b in [("X", self.xmin, self.xmax),
                          ("Y", self.ymin, self.ymax),
                          ("Z", self.zmin, self.zmax)]:
            row = ttk.Frame(lim); row.pack(fill="x", pady=1)
            lab = self._lbl(row, text=key + " min/max", width=11)
            lab.pack(side="left", padx=(0, 4))
            self._lim_rows[key] = lab
            ea = ttk.Entry(row, textvariable=a, width=6)
            ea.pack(side="left", fill="x", expand=True)
            eb = ttk.Entry(row, textvariable=b, width=6)
            eb.pack(side="left", fill="x", expand=True, padx=(4, 0))
            ea.bind("<Return>", lambda e: self._apply_limits())
            eb.bind("<Return>", lambda e: self._apply_limits())
            ea.bind("<Button-3>", lambda e, v=a: self._reset_field(v))
            eb.bind("<Button-3>", lambda e, v=b: self._reset_field(v))
        self._lim_hint = self._lbl(lim, text="", font=(UI_FONT, 8),
                                   foreground="#888")
        self._lim_hint.pack(anchor="w")
        scl = ttk.Frame(lim); scl.pack(fill="x", pady=(4, 1))
        self._lbl(scl, text="Scale", width=11).pack(side="left", padx=(0, 4))
        self.xscale = tk.StringVar(value="linear")
        self.yscale = tk.StringVar(value="linear")
        self._lbl(scl, text="X").pack(side="left")
        _xsc = ttk.Combobox(scl, textvariable=self.xscale, values=["linear", "log"],
                            width=6, state="readonly")
        _xsc.pack(side="left", padx=(2, 6))
        _xsc.bind("<<ComboboxSelected>>", lambda e: self._redraw())
        Tooltip(_xsc, "X (spectral) axis scale: linear or logarithmic.")
        self._lbl(scl, text="Y").pack(side="left")
        _ysc = ttk.Combobox(scl, textvariable=self.yscale, values=["linear", "log"],
                            width=6, state="readonly")
        _ysc.pack(side="left", padx=(2, 0))
        _ysc.bind("<<ComboboxSelected>>", lambda e: self._redraw())
        Tooltip(_ysc, "Y (absorbance) axis scale: linear or logarithmic.")
        lbr = ttk.Frame(lim); lbr.pack(fill="x", pady=(4, 1))
        atck = ttk.Checkbutton(lbr, text="Auto", variable=self.autoscale,
                               command=self._redraw)
        atck.pack(side="left", padx=(0, 8))
        Tooltip(atck, "Fit the axes to the data on every redraw. Zooming, "
                      "typing a limit, or Apply limits turns this off so your "
                      "values stick.")
        ttk.Button(lbr, text="Apply limits", command=self._apply_limits).pack(
            side="left", padx=(0, 4))
        rax = ttk.Button(lbr, text="Reset axes", command=self._reset_axes)
        rax.pack(side="left")
        Tooltip(rax, "Clear the min/max boxes and re-enable Auto so the plot "
                     "fits the data again (the way back after any zoom).")

        # --- 2D plot options ---
        twod = self._group(r, "2D plot options")
        _sr = ttk.Frame(twod); _sr.pack(fill="x", pady=1)
        self._lbl(_sr, text="Line style", width=11).pack(side="left", padx=(0, 4))
        self.line_style = tk.StringVar(value="solid")
        _lscb = ttk.Combobox(_sr, textvariable=self.line_style,
                             values=["solid", "dashed", "dotted", "dashdot"],
                             width=8, state="readonly")
        _lscb.pack(side="left")
        _lscb.bind("<<ComboboxSelected>>", lambda e: self._redraw())
        Tooltip(_lscb, "Line style for the 2D curves: solid, dashed, dotted, or dash-dot.")
        self.dash_decomp = tk.BooleanVar(value=True)
        ttk.Checkbutton(twod, text="Dash decompression (D) traces",
                        variable=self.dash_decomp,
                        command=self._redraw).pack(anchor="w", pady=(4, 1))
        ttk.Separator(twod, orient="horizontal").pack(fill="x", pady=4)
        self._lbl(twod, text="Curve line",
                  font=(UI_FONT, 8, "bold")).pack(anchor="w", pady=(12, 3))
        self.lw = tk.DoubleVar(value=1.0)
        lwsc, _lwe = self._slider_row(twod, "Line width", self.lw, 0.3, 3.0, "%.2f")
        Tooltip(lwsc, "Curve line thickness. Type an exact value or drag.")

        # --- Axis ticks & spacing ---
        at = self._group(r, "Ticks")
        hh = ttk.Frame(at); hh.pack(fill="x")
        self._lbl(hh, text="", width=4).pack(side="left")
        self._lbl(hh, text="major", width=8).pack(side="left")
        self._lbl(hh, text="minor", width=8).pack(side="left")
        self.xmaj, self.xminor = tk.StringVar(), tk.StringVar()
        self.ymaj, self.yminor = tk.StringVar(), tk.StringVar()
        self.zmaj, self.zminor = tk.StringVar(), tk.StringVar()
        self._tick_edited = {str(self.xmaj): False, str(self.ymaj): False,
                             str(self.zmaj): False}
        self._tick_autofill = {}   # last value _sync_tick_boxes wrote per var
        for axlbl, mv, nv in [("X", self.xmaj, self.xminor),
                              ("Y", self.ymaj, self.yminor),
                              ("Z", self.zmaj, self.zminor)]:
            row = ttk.Frame(at); row.pack(fill="x")
            self._lbl(row, text=axlbl, width=4).pack(side="left")
            e1 = ttk.Entry(row, textvariable=mv, width=8); e1.pack(side="left")
            e2 = ttk.Entry(row, textvariable=nv, width=8); e2.pack(side="left")
            e1.bind("<Return>", lambda e: self._redraw())
            e2.bind("<Return>", lambda e: self._redraw())
            e1.bind("<KeyRelease>", lambda ev, vv=mv: self._mark_tick_edited(vv))
            e1.bind("<Button-3>", lambda e, v=mv: self._reset_field(v, "tick"))
            e2.bind("<Button-3>", lambda e, v=nv: self._reset_field(v, "tick"))
        self._lbl(at, text="spacing in axis units; blank = auto  (Z = 3D only)",
                  font=(UI_FONT, 8), foreground="#888").pack(anchor="w")
        d1 = ttk.Frame(at); d1.pack(fill="x", pady=(3, 0))
        self._lbl(d1, text="Marks", width=7).pack(side="left")
        self.tick_dir = tk.StringVar(value="out")
        tdc = ttk.Combobox(d1, textvariable=self.tick_dir, state="readonly",
                           width=7, values=["out", "in", "inout"])
        tdc.pack(side="left")
        self.tick_dir.trace_add("write", lambda *a: self._redraw())
        Tooltip(tdc, "out = ticks point outward, in = inward, inout = both "
                     "sides of the axis line.")
        self.minor_ticks = tk.BooleanVar()
        ttk.Checkbutton(at, text="Minor ticks (auto when spacing blank)",
                        variable=self.minor_ticks, command=self._redraw).pack(anchor="w")
        self.ticks_allsides = tk.BooleanVar()
        ttk.Checkbutton(at, text="Ticks on all sides (2D)",
                        variable=self.ticks_allsides,
                        command=self._redraw).pack(anchor="w")
        self.tick_len_major = tk.DoubleVar(value=3.5)
        self.tick_len_minor = tk.DoubleVar(value=2.0)
        self.tick_width = tk.DoubleVar(value=0.8)
        self.tick_fs = tk.IntVar(value=10)
        ln = ttk.Frame(at); ln.pack(fill="x", pady=(4, 1))
        self._lbl(ln, text="Tick length", width=12).pack(side="left")
        self._lbl(ln, text="major").pack(side="left")
        ttk.Entry(ln, textvariable=self.tick_len_major, width=5).pack(
            side="left", padx=(2, 8))
        self._lbl(ln, text="minor").pack(side="left")
        ttk.Entry(ln, textvariable=self.tick_len_minor, width=5).pack(
            side="left", padx=(2, 0))
        ln2 = ttk.Frame(at); ln2.pack(fill="x", pady=(4, 1))
        self._lbl(ln2, text="Tick width", width=12).pack(side="left")
        ttk.Entry(ln2, textvariable=self.tick_width, width=5).pack(side="left")
        self._lbl(ln2, text="Label font").pack(side="left", padx=(14, 0))
        ttk.Spinbox(ln2, from_=6, to=24, textvariable=self.tick_fs, width=4,
                    command=self._redraw).pack(side="left", padx=(3, 0))
        atb = ttk.Frame(at); atb.pack(fill="x", pady=(4, 1))
        ttk.Button(atb, text="Apply ticks", command=self._redraw).pack(side="left")
        ttk.Button(atb, text="Auto", command=self._auto_ticks).pack(side="left",
                                                                    padx=(4, 0))

        # --- Display ---
        fg = self._group(r, "Frame & grid")
        d = self._group(r, "Colors & colormap")
        fr0 = ttk.Frame(d); fr0.pack(fill="x")
        self._lbl(fr0, text="Filter", width=9).pack(side="left")
        self.cmap_filter = tk.StringVar()
        fent = ttk.Entry(fr0, textvariable=self.cmap_filter)
        fent.pack(side="left", fill="x", expand=True)
        Tooltip(fent, "Type to filter the colormap list below (e.g. 'blu', 'div', "
                      "'gray'). Clear to show all.")
        self.cmap_filter.trace_add("write", lambda *a: self._filter_cmaps())
        cr = ttk.Frame(d); cr.pack(fill="x", pady=(4, 1))
        self._lbl(cr, text="Colormap", width=9).pack(side="left")
        self.cmap = tk.StringVar(value=self.settings.get("cmap_default", "batlow"))
        self.cmap_cb = ttk.Combobox(cr, textvariable=self.cmap,
                                    values=colormaps.available(), state="readonly")
        cmb = self.cmap_cb
        cmb.pack(side="left", fill="x", expand=True)
        self.cmap.trace_add("write", lambda *a: self._redraw())
        self.cmap.trace_add("write", lambda *a: self._draw_cmap_swatch())
        self.cmap.trace_add("write", lambda *a: self._update_cmap_default_btn())
        Tooltip(cmb, "Color scale across pressures. Crameri maps (batlow, roma, "
                     "hawaii, lajolla) are perceptually uniform and color-blind "
                     "safe. Use [ and ] to cycle.")
        self.cmap_swatch = tk.Canvas(d, height=14, highlightthickness=1,
                                     highlightbackground="#888")
        self.cmap_swatch.pack(fill="x", pady=(3, 0))
        self.cmap_swatch.bind("<Configure>", lambda e: self._draw_cmap_swatch())
        self.cmap_default_btn = ttk.Button(d, command=self._set_cmap_default)
        self.cmap_default_btn.pack(fill="x", pady=(3, 0))
        self._update_cmap_default_btn()
        Tooltip(self.cmap_default_btn,
                "Save this colormap as the startup default. The star shows when "
                "the current map is already the saved default.")
        self.cmap_rev = tk.BooleanVar()
        rvck = ttk.Checkbutton(d, text="Reverse colormap", variable=self.cmap_rev,
                               command=self._redraw)
        rvck.pack(anchor="w")
        Tooltip(rvck, "Flip the color scale so the highest pressure takes the "
                      "low end of the map instead of the high end.")
        self.lock_colors = tk.BooleanVar(value=True)
        lck = ttk.Checkbutton(d, text="Lock colors to all datasets",
                              variable=self.lock_colors, command=self._redraw)
        lck.pack(anchor="w")
        Tooltip(lck, "Color each dataset from the full loaded set instead of "
                     "only the shown traces, so a curve keeps its color when "
                     "you toggle others on or off.")
        self.theme_plot_follow = tk.BooleanVar(value=True)
        tpf = ttk.Checkbutton(d, text="Tint plot with theme",
                              variable=self.theme_plot_follow,
                              command=self._toggle_dark)
        tpf.pack(anchor="w")
        Tooltip(tpf, "Accent themes (forest, rose, ...) normally keep the plot "
                     "neutral for publication. Enable this to tint the plot "
                     "background to match the theme too.")
        _colvals = ["auto", "black", "white", "gray", "#444444", "#888888"]
        acr = ttk.Frame(d); acr.pack(fill="x", pady=(3, 0))
        self._lbl(acr, text="Axis color", width=11).pack(side="left")
        self.axis_color = tk.StringVar(value="auto")
        acc = ttk.Combobox(acr, textvariable=self.axis_color, state="readonly",
                           width=9, values=_colvals)
        acc.pack(side="left", fill="x", expand=True)
        self.axis_color.trace_add("write", lambda *a: self._redraw())
        Tooltip(acc, "Color of the outer axis lines / spines (2D) and the 3D box "
                     "edges. 'auto' follows the theme.")
        tcr = ttk.Frame(d); tcr.pack(fill="x")
        self._lbl(tcr, text="Text color", width=11).pack(side="left")
        self.text_color = tk.StringVar(value="auto")
        tcc = ttk.Combobox(tcr, textvariable=self.text_color, state="readonly",
                           width=9, values=_colvals)
        tcc.pack(side="left", fill="x", expand=True)
        self.text_color.trace_add("write", lambda *a: self._redraw())
        Tooltip(tcc, "Color of the tick numbers and axis labels (2D and 3D). "
                     "'auto' follows the theme.")
        _gstyles = ["solid", "dotted", "dashed", "dashdot"]
        _gcolors = ["auto", "gray", "black", "white", "#888888", "#cccccc",
                    "#444444"]
        def _grid_row(on_var, col_var, w_var, a_var, st_var, label, tip):
            ttk.Checkbutton(fg, text=label, variable=on_var,
                            command=self._redraw).pack(anchor="w")
            r1 = ttk.Frame(fg); r1.pack(fill="x")
            cb = ttk.Combobox(r1, textvariable=col_var, width=8, state="readonly",
                              values=_gcolors)
            cb.pack(side="left", fill="x", expand=True)
            stb = ttk.Combobox(r1, textvariable=st_var, width=8, state="readonly",
                               values=_gstyles)
            stb.pack(side="left", padx=(3, 0), fill="x", expand=True)
            r2 = ttk.Frame(fg); r2.pack(fill="x")
            self._lbl(r2, text="width").pack(side="left")
            we = ttk.Entry(r2, textvariable=w_var, width=5)
            we.pack(side="left", padx=(2, 0))
            self._lbl(r2, text="opacity").pack(side="left", padx=(6, 0))
            ae = ttk.Entry(r2, textvariable=a_var, width=5)
            ae.pack(side="left", padx=(2, 0))
            for v in (col_var, st_var):
                v.trace_add("write", lambda *a: self._redraw())
            for e in (we, ae):
                e.bind("<Return>", lambda ev: self._redraw())
                e.bind("<FocusOut>", lambda ev: self._redraw())
            Tooltip(cb, tip)
        self.grid_on = tk.BooleanVar()
        self.grid_color = tk.StringVar(value="auto")
        self.grid_width = tk.DoubleVar(value=0.6)
        self.grid_alpha = tk.DoubleVar(value=0.4)
        self.grid_style = tk.StringVar(value="solid")
        _grid_row(self.grid_on, self.grid_color, self.grid_width, self.grid_alpha,
                  self.grid_style, "Major grid",
                  "Major gridlines: color, pattern, width (w), opacity (a). "
                  "'auto' color follows the theme.")
        self.grid_minor_on = tk.BooleanVar()
        self.grid_minor_color = tk.StringVar(value="auto")
        self.grid_minor_width = tk.DoubleVar(value=0.4)
        self.grid_minor_alpha = tk.DoubleVar(value=0.25)
        self.grid_minor_style = tk.StringVar(value="dotted")
        _grid_row(self.grid_minor_on, self.grid_minor_color,
                  self.grid_minor_width, self.grid_minor_alpha,
                  self.grid_minor_style, "Minor grid",
                  "Minor gridlines (2D; needs minor ticks on). Styled "
                  "independently of the major grid.")
        self.hide_spines = tk.BooleanVar()
        ttk.Checkbutton(fg, text="Hide top/right spines", variable=self.hide_spines,
                        command=self._redraw).pack(anchor="w")
        # cmcrameri fallback note: created unpacked; _refresh_fallback_note
        # packs it ONLY when there is text (an always-packed empty label left
        # a dead gap at the bottom of this box).
        self.fallback_lbl = self._lbl(d, text="", foreground="#a60",
                                      wraplength=260, font=(UI_FONT, 8))


        # --- Aspect ratio (2D plot box) ---
        self._lbl(twod, text="Aspect ratio", width=11).pack(anchor="w", pady=(4, 0))
        arow = ttk.Frame(twod); arow.pack(fill="x")
        self.aspect_mode = tk.StringVar(value="Auto (fill)")
        acb = ttk.Combobox(arow, textvariable=self.aspect_mode, state="readonly",
                           values=["Auto (fill)", "1:1", "4:3", "3:2", "16:9",
                                   "custom"])
        acb.pack(side="left", fill="x", expand=True)
        self.aspect_mode.trace_add("write", lambda *a: self._redraw())
        Tooltip(acb, "Shape of the 2D plot box. 1:1 = square (default); "
                     "Auto = fill the area. Use custom for any W:H.")
        crow = ttk.Frame(twod); crow.pack(fill="x")
        self._lbl(crow, text="custom W:H", width=10).pack(side="left")
        self.aspect_w = tk.StringVar(value="1")
        self.aspect_h = tk.StringVar(value="1")
        ttk.Entry(crow, textvariable=self.aspect_w, width=5).pack(side="left")
        self._lbl(crow, text=":").pack(side="left")
        ttk.Entry(crow, textvariable=self.aspect_h, width=5).pack(side="left")
        ttk.Button(crow, text="Apply", command=self._redraw).pack(side="left",
                                                                  padx=(4, 0))

        # --- View: pan pad + zoom, folded into 2D plot options (the 2D
        # counterpart of the 3D camera controls)
        ttk.Separator(twod, orient="horizontal").pack(fill="x", pady=(8, 4))
        self._lbl(twod, text="View", font=(UI_FONT, 10, "bold")).pack(
            anchor="w")
        vw = twod
        vrow = ttk.Frame(vw); vrow.pack(fill="x")
        pad = ttk.Frame(vrow); pad.pack(side="left")
        def _padbtn(txt, dx, dy, r_, c_):
            b = ttk.Button(pad, text=txt, width=3)
            b.grid(row=r_, column=c_, padx=1, pady=1)
            self._bind_repeat(b, lambda: self._pan2d(dx, dy))
            return b
        _padbtn("\u25b2", 0, +1, 0, 1)
        _padbtn("\u25c0", -1, 0, 1, 0)
        fitb = ttk.Button(pad, text="Fit", width=3,
                          command=lambda: self._fit2d("both"))
        fitb.grid(row=1, column=1, padx=1, pady=1)
        _padbtn("\u25b6", +1, 0, 1, 2)
        _padbtn("\u25bc", 0, -1, 2, 1)
        Tooltip(pad, "Pan the 2D view (hold to repeat). Center = fit the "
                     "shown data. Arrow keys do the same when you are not "
                     "typing in a box.")
        fits = ttk.Frame(vrow); fits.pack(side="left", padx=(10, 0))
        ttk.Button(fits, text="Fit X", width=6,
                   command=lambda: self._fit2d("X")).pack(pady=1)
        ttk.Button(fits, text="Fit Y", width=6,
                   command=lambda: self._fit2d("Y")).pack(pady=1)
        zr = ttk.Frame(vw); zr.pack(fill="x", pady=(6, 0))
        self._lbl(zr, text="Zoom", width=6).pack(side="left")
        zout = ttk.Button(zr, text="\u2212", width=3,
                          command=lambda: self._zoom2d(1.0 / 0.9))
        zout.pack(side="left")
        zin = ttk.Button(zr, text="+", width=3,
                         command=lambda: self._zoom2d(0.9))
        zin.pack(side="left", padx=(2, 6))
        Tooltip(zout, "Zoom out about the view center (keyboard: -).")
        Tooltip(zin, "Zoom in about the view center (keyboard: +).")
        self.zoom2d_axis = tk.StringVar(value="both")
        for t in ("both", "X", "Y"):
            ttk.Radiobutton(zr, text=t, value=t,
                            variable=self.zoom2d_axis).pack(side="left",
                                                            padx=(0, 4))
        self._lbl(vw, text="Drag a box on the plot, scroll to zoom at the "
                           "cursor, 0 = fit.", font=(UI_FONT, 8),
                  foreground="#888").pack(anchor="w", pady=(3, 0))

        # --- Waterfall ---
        wf = self._group(r, "Waterfall (2D / 3D plotting)")
        mr = ttk.Frame(wf); mr.pack(fill="x")
        self._lbl(mr, text="Mode", width=9).pack(side="left")
        self.wf_mode = tk.StringVar(value="off")
        wfcb = ttk.Combobox(mr, textvariable=self.wf_mode, state="readonly",
                            values=["off", "2D stacked", "3D ridge"])
        wfcb.pack(side="left", fill="x", expand=True)
        Tooltip(wfcb, "off = shared baseline; 2D stacked = shift each "
                      "pressure up; 3D ridge = 3D mountain-range view.")
        self.wf_mode.trace_add("write", lambda *a: self._redraw())
        sr = ttk.Frame(wf); sr.pack(fill="x")
        self._lbl(sr, text="Offset/step", width=9).pack(side="left")
        self.wf_step = tk.StringVar(value="0.2")
        wse = ttk.Entry(sr, textvariable=self.wf_step)
        wse.pack(side="left", fill="x", expand=True)
        ttk.Button(sr, text="Apply", command=self._redraw).pack(side="left")
        ttk.Button(sr, text="Auto", command=self._auto_offset).pack(side="left",
                                                                    padx=(3, 0))
        Tooltip(wse, "2D stacked: vertical gap added between successive curves. "
                     "3D ridge (with Even rank spacing on): how far apart the "
                     "ridges sit along the pressure axis. Default 0.2.")
        self.wf_label = tk.BooleanVar(value=True)
        ttk.Checkbutton(wf, text="Label each ridge with pressure",
                        variable=self.wf_label, command=self._redraw).pack(anchor="w")

        self._build_3d_opts(r)        # 3D plot options live right under Waterfall

        # --- Defringe (FFT notch) ---
        dfg = self._group(r, "Defringe")
        self.show_notch = tk.BooleanVar(value=False)
        ncb = ttk.Checkbutton(dfg, text="Enable (FFT notch defringe)",
                              variable=self.show_notch, command=self._toggle_notch)
        ncb.pack(anchor="w")
        Tooltip(ncb, "Remove diamond-anvil interference fringes by notching the "
                     "dominant auto-detected fringe (n*t over 15-100 um) out of "
                     "the raw Sample and Background counts independently, then "
                     "recomputing absorbance. Channels with no confident fringe "
                     "are left unchanged. When enabled, Run also writes "
                     "{stem}_absorbance_notch.csv files.")
        self.notch_width = tk.DoubleVar(value=defringe.NOTCH_WIDTH_FRAC * 100.0)
        nwsc, _nwe = self._slider_row(dfg, "Notch width %", self.notch_width,
                                      0.0, 50.0, "%.0f")
        Tooltip(nwsc, "Gaussian-notch half-width as a percent of the fringe "
                      "frequency (default 15%). Wider removes more around the "
                      "fringe. Slide to 0 to disable defringe; any nonzero "
                      "width enables it. Right-click resets.")
        # Width changes invalidate caches and drive the Enable box (0 = off).
        self.notch_width.trace_add("write", self._on_notch_width)
        # detection parameters (defaults = Matthew's defringe_dac.py constants)
        self.notch_nt_min = tk.StringVar(value="%g" % (defringe.FRINGE_NT_MIN_NM
                                                       / 1000.0))
        self.notch_nt_max = tk.StringVar(value="%g" % (defringe.FRINGE_NT_MAX_NM
                                                       / 1000.0))
        self.notch_pmax = tk.StringVar(value="%g" % defringe.FRINGE_PVALUE_MAX)
        ntr = ttk.Frame(dfg); ntr.pack(fill="x", pady=(4, 1))
        self._lbl(ntr, text="n*t min/max (um)", width=15).pack(side="left")
        ne1 = ttk.Entry(ntr, textvariable=self.notch_nt_min, width=6)
        ne1.pack(side="left", fill="x", expand=True)
        ne2 = ttk.Entry(ntr, textvariable=self.notch_nt_max, width=6)
        ne2.pack(side="left", fill="x", expand=True, padx=(4, 0))
        Tooltip(ntr, "Fringe search window: the FFT peak is only accepted when "
                     "the fitted n*t (refractive index x thickness) falls in "
                     "this range, in microns. Defaults 15-100 um.")
        pvr = ttk.Frame(dfg); pvr.pack(fill="x")
        self._lbl(pvr, text="p-value max", width=15).pack(side="left")
        ne3 = ttk.Entry(pvr, textvariable=self.notch_pmax, width=8)
        ne3.pack(side="left", fill="x", expand=True)
        Tooltip(ne3, "Fringe-detection cutoff: a candidate FFT peak is only notched when its p-value is below this. Lower = stricter.")
        ttk.Button(pvr, text="Defaults", width=8,
                   command=self._notch_defaults).pack(side="left", padx=(4, 0))
        Tooltip(pvr, "Fisher g-test gate: a channel is only defringed when its "
                     "detection p-value is BELOW this (default 1e-4). Raise it "
                     "to catch weaker fringes; scientific notation is fine. "
                     "Defaults restores all four detection values.")
        for _ne in (ne1, ne2, ne3):
            _ne.bind("<Return>", lambda e: self._notch_params_changed())
            _ne.bind("<FocusOut>", lambda e: self._notch_params_changed())
        self.suppress_fringe_report = tk.BooleanVar(value=False)
        drb = ttk.Checkbutton(dfg, text="Suppress fringe report",
                              variable=self.suppress_fringe_report)
        drb.pack(anchor="w", pady=(3, 0))
        Tooltip(drb, "The fringe report (which pressures have a detected fringe, "
                     "the fitted n*t in um, and the detection p-value) is logged "
                     "automatically whenever defringe is enabled. Check this to "
                     "stop it being logged.")

        # --- Smoothing ---
        sm = self._group(r, "Smoothing")
        self.show_smooth = tk.BooleanVar()
        scb = ttk.Checkbutton(sm, text="Show smoothed", variable=self.show_smooth,
                              command=self._redraw)
        scb.pack(anchor="w")
        Tooltip(scb, "Overlay the Igor 5-step smoothed curve (2D) or smooth each "
                     "ridge (3D). The raw trace stays visible underneath at the "
                     "opacity below. Edit the filters in 'Smoothing settings...'.")
        self.raw_opacity = tk.DoubleVar(value=0.30)
        rosc, _roe = self._slider_row(sm, "Raw opacity", self.raw_opacity, 0.0,
                                      1.0, "%.2f")
        Tooltip(rosc, "Opacity of the original (raw) trace behind the smoothed "
                      "one. Slide to 0 to hide raw entirely; right-click resets.")
        self.no_raw_bg = tk.BooleanVar(value=False)
        def _toggle_no_raw():
            if self.no_raw_bg.get():
                self._prev_raw = float(self.raw_opacity.get())
                self.raw_opacity.set(0.0)
            else:
                self.raw_opacity.set(getattr(self, "_prev_raw", 0.30) or 0.30)
            self._redraw()
        nrb = ttk.Checkbutton(sm, text="No raw background",
                              variable=self.no_raw_bg, command=_toggle_no_raw)
        nrb.pack(anchor="w")
        Tooltip(nrb, "Hide the raw trace entirely and set Raw opacity to 0. "
                     "Unticking restores the previous opacity.")
        smb = ttk.Frame(sm); smb.pack(fill="x", pady=(4, 0))
        ttk.Button(smb, text="Smoothing settings...",
                   command=self._open_smooth_panel).pack(side="left", fill="x",
                                                         expand=True)
        rsb = ttk.Button(smb, text="Reset", width=7, command=self._reset_smoothing)
        rsb.pack(side="left", padx=(4, 0))
        Tooltip(rsb, "Restore the smoothing filters to their defaults and clear "
                     "the smoothing cache.")

        # --- Reference guides ---
        mk = self._group(r, "Reference guides")
        _mstyles = ["solid", "dotted", "dashed", "dashdot"]
        _mcolors = ["gray", "black", "red", "blue", "green", "orange",
                    "purple", "#888888"]
        def _style_row(parent, cvar, svar, wvar, avar=None):
            r1 = ttk.Frame(parent); r1.pack(fill="x", pady=(4, 1))
            cb = ttk.Combobox(r1, textvariable=cvar, width=8, state="readonly",
                              values=_mcolors)
            cb.pack(side="left", fill="x", expand=True)
            sb = ttk.Combobox(r1, textvariable=svar, width=8, state="readonly",
                              values=_mstyles)
            sb.pack(side="left", padx=(3, 0), fill="x", expand=True)
            r2 = ttk.Frame(parent); r2.pack(fill="x", pady=(3, 1))
            self._lbl(r2, text="width").pack(side="left")
            we = ttk.Entry(r2, textvariable=wvar, width=5)
            we.pack(side="left", padx=(2, 0))
            we.bind("<Return>", lambda ev: self._redraw())
            we.bind("<FocusOut>", lambda ev: self._redraw())
            if avar is not None:
                self._lbl(r2, text="opacity").pack(side="left", padx=(6, 0))
                ae = ttk.Entry(r2, textvariable=avar, width=5)
                ae.pack(side="left", padx=(2, 0))
                ae.bind("<Return>", lambda ev: self._redraw())
                ae.bind("<FocusOut>", lambda ev: self._redraw())
            for v in (cvar, svar):
                v.trace_add("write", lambda *a: self._redraw())
        self._lbl(mk, text="Vertical lines - wavelength (nm)",
                  font=(UI_FONT, 8, "bold")).pack(anchor="w", pady=(12, 3))
        self.markers = tk.StringVar()
        ev = ttk.Entry(mk, textvariable=self.markers); ev.pack(fill="x")
        ev.bind("<Return>", lambda e: self._redraw())
        Tooltip(ev, "Vertical reference lines at these wavelengths (nm), comma "
                    "or space separated (e.g. 450, 620, 700). 2D plots only.")
        self.vmark_color = tk.StringVar(value="gray")
        self.vmark_style = tk.StringVar(value="dotted")
        self.vmark_width = tk.DoubleVar(value=1.0)
        self.vmark_alpha = tk.DoubleVar(value=1.0)
        _style_row(mk, self.vmark_color, self.vmark_style, self.vmark_width,
                   self.vmark_alpha)
        mir = ttk.Frame(mk); mir.pack(fill="x", pady=(4, 1))
        self._lbl(mir, text="Auto every").pack(side="left")
        self.marker_interval = tk.StringVar(value="100")
        ttk.Entry(mir, textvariable=self.marker_interval, width=5).pack(
            side="left", padx=(3, 2))
        self._lbl(mir, text="nm").pack(side="left")
        ttk.Button(mir, text="Fill", width=5,
                   command=self._fill_markers_interval).pack(side="left", padx=(4, 0))
        ttk.Button(mir, text="Clear", width=6,
                   command=self._clear_markers).pack(side="left", padx=(3, 0))
        ttk.Separator(mk, orient="horizontal").pack(fill="x", pady=4)
        self._lbl(mk, text="Horizontal lines - absorbance",
                  font=(UI_FONT, 8, "bold")).pack(anchor="w", pady=(12, 3))
        self.hmarkers = tk.StringVar()
        eh = ttk.Entry(mk, textvariable=self.hmarkers); eh.pack(fill="x")
        eh.bind("<Return>", lambda e: self._redraw())
        Tooltip(eh, "Horizontal reference lines at these Y (absorbance) values, "
                    "comma or space separated (e.g. 1.0, 2.5). 2D plots only.")
        self.hmark_color = tk.StringVar(value="gray")
        self.hmark_style = tk.StringVar(value="dotted")
        self.hmark_width = tk.DoubleVar(value=1.0)
        self.hmark_alpha = tk.DoubleVar(value=1.0)
        _style_row(mk, self.hmark_color, self.hmark_style, self.hmark_width,
                   self.hmark_alpha)
        hir = ttk.Frame(mk); hir.pack(fill="x", pady=(4, 1))
        self._lbl(hir, text="Auto every").pack(side="left")
        self.hmarker_interval = tk.StringVar(value="0.5")
        ttk.Entry(hir, textvariable=self.hmarker_interval, width=5).pack(
            side="left", padx=(3, 2))
        self._lbl(hir, text="abs").pack(side="left")
        ttk.Button(hir, text="Fill", width=5,
                   command=self._fill_hmarkers_interval).pack(side="left", padx=(4, 0))
        ttk.Button(hir, text="Clear", width=6,
                   command=self._clear_hmarkers).pack(side="left", padx=(3, 0))
        bb = ttk.Frame(mk); bb.pack(fill="x", pady=(3, 0))
        ttk.Button(bb, text="Apply", command=self._redraw).pack(side="left")
        syncb = ttk.Button(bb, text="Sync H from V",
                           command=self._sync_marker_style)
        syncb.pack(side="left", padx=(4, 0))
        Tooltip(syncb, "Copy the vertical line style (color, pattern, width) "
                       "onto the horizontal lines.")

        # --- Title / labels / legend ---
        lg = self._group(r, "Title & axis labels")
        self.title_v, self.xlabel_v, self.ylabel_v = (tk.StringVar(),
                                                      tk.StringVar(), tk.StringVar())
        self.zlabel_v = tk.StringVar()
        self._label_edited = {"title": False, "xlabel": False,
                              "ylabel": False, "zlabel": False}
        self._label_keys = {str(self.title_v): "title",
                            str(self.xlabel_v): "xlabel",
                            str(self.ylabel_v): "ylabel",
                            str(self.zlabel_v): "zlabel"}
        self.title_fs = tk.IntVar(value=13)
        self.label_fs = tk.IntVar(value=11)
        # X and Y label rows intentionally share one size var: matplotlib
        # applies a single label size to both axes in _apply_font
        _fsmap = {"Title": self.title_fs, "X label": self.label_fs,
                  "Y label": self.label_fs, "Z label": self.label_fs}
        for lbl, v in [("Title", self.title_v), ("X label", self.xlabel_v),
                       ("Y label", self.ylabel_v),
                       ("Z label", self.zlabel_v)]:
            row = ttk.Frame(lg); row.pack(fill="x")
            self._lbl(row, text=lbl, width=7).pack(side="left")
            en = ttk.Entry(row, textvariable=v); en.pack(side="left", fill="x",
                                                         expand=True)
            en.bind("<Return>", lambda ev: self._redraw())
            en.bind("<KeyRelease>", lambda ev, vv=v: self._mark_label_edited(vv))
            en.bind("<Button-3>", lambda ev, vv=v: self._reset_field(vv, "label"))
            ttk.Spinbox(row, from_=6, to=28, textvariable=_fsmap[lbl], width=3,
                        command=self._redraw).pack(side="left", padx=(3, 0))
        pos1 = ttk.Frame(lg); pos1.pack(fill="x", pady=(4, 1))
        self._lbl(pos1, text="Title pos", width=9).pack(side="left")
        self.title_loc = tk.StringVar(value="center")
        ttk.Combobox(pos1, textvariable=self.title_loc, state="readonly",
                     width=7, values=["left", "center", "right"]
                     ).pack(side="left")
        self.title_loc.trace_add("write", lambda *a: self._redraw())
        self._lbl(pos1, text=" pad").pack(side="left", padx=(6, 0))
        self.title_pad = tk.StringVar()
        tpe = ttk.Entry(pos1, textvariable=self.title_pad, width=5)
        tpe.pack(side="left", padx=(2, 0))
        tpe.bind("<Return>", lambda e: self._redraw())
        tpe.bind("<FocusOut>", lambda e: self._redraw())
        Tooltip(pos1, "Title alignment, and its gap above the axes in "
                      "points (blank = default).")
        pos2 = ttk.Frame(lg); pos2.pack(fill="x", pady=(0, 1))
        self._lbl(pos2, text="X pos", width=9).pack(side="left")
        self.xlabel_loc = tk.StringVar(value="center")
        ttk.Combobox(pos2, textvariable=self.xlabel_loc, state="readonly",
                     width=7, values=["left", "center", "right"]
                     ).pack(side="left")
        self.xlabel_loc.trace_add("write", lambda *a: self._redraw())
        self._lbl(pos2, text=" Y pos").pack(side="left", padx=(6, 0))
        self.ylabel_loc = tk.StringVar(value="center")
        ttk.Combobox(pos2, textvariable=self.ylabel_loc, state="readonly",
                     width=8, values=["bottom", "center", "top"]
                     ).pack(side="left", padx=(2, 0))
        self.ylabel_loc.trace_add("write", lambda *a: self._redraw())
        Tooltip(pos2, "Where the X / Y axis labels sit along their axes "
                      "(2D plots).")
        lg = self._group(r, "Legend")
        self.legend_on = tk.BooleanVar(value=True)
        lgck = ttk.Checkbutton(lg, text="Show legend", variable=self.legend_on,
        command=self._redraw)
        lgck.pack(anchor="w")
        Tooltip(lgck, "Show a per-trace key (pressure + branch).")
        lr = ttk.Frame(lg); lr.pack(fill="x", pady=(4, 1))
        self._lbl(lr, text="Location", width=9).pack(side="left")
        self.legend_loc = tk.StringVar(value="best")
        ttk.Combobox(lr, textvariable=self.legend_loc, values=LEGEND_LOCS,
                     state="readonly").pack(side="left", fill="x", expand=True)
        self.legend_loc.trace_add("write", lambda *a: self._redraw())
        lsw = ttk.Frame(lg); lsw.pack(fill="x", pady=(4, 1))
        self._lbl(lsw, text="Swatch", width=9).pack(side="left")
        self.legend_swatch = tk.StringVar(value="color box")
        swc = ttk.Combobox(lsw, textvariable=self.legend_swatch,
                           state="readonly", values=["color box", "line"])
        swc.pack(side="left", fill="x", expand=True)
        self.legend_swatch.trace_add("write", lambda *a: self._redraw())
        Tooltip(swc, "Legend key style. 'color box' shows a thick color "
                     "block per trace (easy to read, matches the 3D legend); "
                     "'line' shows the artist itself (thin line).")
        lr2 = ttk.Frame(lg); lr2.pack(fill="x", pady=(4, 1))
        self._lbl(lr2, text="Columns", width=9).pack(side="left")
        self.legend_cols = tk.IntVar(value=2)
        ttk.Spinbox(lr2, from_=1, to=6, textvariable=self.legend_cols, width=4,
                    command=self._redraw).pack(side="left")
        self._lbl(lr2, text="Font size").pack(side="left", padx=(14, 0))
        self.legend_fs = tk.IntVar(value=9)
        ttk.Spinbox(lr2, from_=6, to=24, textvariable=self.legend_fs, width=4,
                    command=self._redraw).pack(side="left", padx=(3, 0))
        lr3 = ttk.Frame(lg); lr3.pack(fill="x", pady=(4, 1))
        self._lbl(lr3, text="Title", width=9).pack(side="left")
        self.legend_title = tk.StringVar(value="")
        lte = ttk.Entry(lr3, textvariable=self.legend_title)
        lte.pack(side="left", fill="x", expand=True)
        lte.bind("<Return>", lambda e: self._redraw())
        lte.bind("<FocusOut>", lambda e: self._redraw())
        self._lbl(lr3, text="Font size").pack(side="left", padx=(14, 0))
        self.legend_title_fs = tk.IntVar(value=10)
        ttk.Spinbox(lr3, from_=6, to=24, textvariable=self.legend_title_fs,
                    width=4, command=self._redraw).pack(side="left", padx=(3, 0))
        Tooltip(lte, "Optional heading shown above the legend entries. Leave "
                     "blank for no title.")
        ttk.Separator(lg, orient="horizontal").pack(fill="x", pady=(8, 4))
        self._lbl(lg, text="Frame (shared with colorbar)",
                  font=(UI_FONT, 8, "bold")).pack(anchor="w", pady=(0, 3))
        self.legend_border = tk.BooleanVar(value=True)
        lbck = ttk.Checkbutton(lg, text="Border box", variable=self.legend_border,
                               command=self._redraw)
        lbck.pack(anchor="w")
        Tooltip(lbck, "Draw a border box around the legend (and the colorbar).")
        self.legend_alpha = tk.DoubleVar(value=0.92)
        lasc, _lae = self._slider_row(lg, "Background opacity", self.legend_alpha,
                                      0.0, 1.0, "%.2f")
        Tooltip(lasc, "Legend / colorbar background opacity. 0 = transparent.")
        self.legend_bw = tk.DoubleVar(value=0.8)
        lwsc2, _lwe2 = self._slider_row(lg, "Border width", self.legend_bw,
                            0.0, 3.0, "%.2f")
        Tooltip(lwsc2, "Thickness of the legend / colorbar border.")
        lcr = ttk.Frame(lg); lcr.pack(fill="x", pady=(4, 1))
        self._lbl(lcr, text="Edge color", width=10).pack(side="left")
        self.legend_edge = tk.StringVar(value="auto")
        lec = ttk.Combobox(lcr, textvariable=self.legend_edge, state="readonly",
                           width=10, values=["auto", "gray", "black", "white",
                                             "#888888", "#cccccc"])
        lec.pack(side="left", fill="x", expand=True)
        self.legend_edge.trace_add("write", lambda *a: self._redraw())
        Tooltip(lec, "Legend / colorbar border color; 'auto' follows the theme.")

        cbarg = self._group(r, "Colorbar")
        self.colorbar_on = tk.BooleanVar()
        cbck = ttk.Checkbutton(cbarg, text="Show colorbar (continuous maps)",
                               variable=self.colorbar_on, command=self._redraw)
        cbck.pack(anchor="w")
        Tooltip(cbck, "Show a continuous pressure colorbar instead of the legend "
                      "(best with many traces). Frame styling is shared with the "
                      "legend, set in the Legend box.")
        self.auto_key = tk.BooleanVar(value=False)
        akc = ttk.Checkbutton(cbarg, text="Auto: colorbar for many traces",
                              variable=self.auto_key, command=self._redraw)
        akc.pack(anchor="w")
        Tooltip(akc, "When on, a continuous colormap with more than ~10 traces "
                     "automatically uses a pressure colorbar (the publication "
                     "standard) instead of a large per-trace legend that would "
                     "hide the data. Off (default): the Legend/colorbar "
                     "checkboxes are honored literally. For a discrete legend, "
                     "use a categorical colormap.")
        self.cbar_label = tk.StringVar(value="Pressure (GPa)")
        self.cbar_label_fs = tk.IntVar(value=11)
        self.cbar_tick_fs = tk.IntVar(value=9)
        self.cbar_orient = tk.StringVar(value="vertical")
        self.cbar_width = tk.DoubleVar(value=0.05)
        self.cbar_nticks = tk.IntVar(value=0)
        cb1 = ttk.Frame(cbarg); cb1.pack(fill="x", pady=(4, 1))
        self._lbl(cb1, text="Bar label", width=11).pack(side="left")
        cble = ttk.Entry(cb1, textvariable=self.cbar_label)
        cble.pack(side="left", fill="x", expand=True)
        cble.bind("<Return>", lambda e: self._redraw())
        cble.bind("<FocusOut>", lambda e: self._redraw())
        cb2 = ttk.Frame(cbarg); cb2.pack(fill="x", pady=(4, 1))
        self._lbl(cb2, text="Orientation", width=11).pack(side="left")
        ttk.Combobox(cb2, textvariable=self.cbar_orient, state="readonly",
        values=["vertical", "horizontal"]).pack(side="left",
                                                             fill="x", expand=True)
        self.cbar_orient.trace_add("write", lambda *a: self._redraw())
        cb3 = ttk.Frame(cbarg); cb3.pack(fill="x", pady=(4, 1))
        self._lbl(cb3, text="Label font", width=11).pack(side="left")
        ttk.Spinbox(cb3, from_=6, to=24, textvariable=self.cbar_label_fs, width=4,
                    command=self._redraw).pack(side="left")
        self._lbl(cb3, text="Tick font").pack(side="left", padx=(14, 0))
        ttk.Spinbox(cb3, from_=6, to=24, textvariable=self.cbar_tick_fs, width=4,
                    command=self._redraw).pack(side="left", padx=(3, 0))
        cb4 = ttk.Frame(cbarg); cb4.pack(fill="x", pady=(4, 1))
        self._lbl(cb4, text="Thickness", width=11).pack(side="left")
        ttk.Spinbox(cb4, from_=0.02, to=0.2, increment=0.01, width=6,
                    textvariable=self.cbar_width,
                    command=self._redraw).pack(side="left")
        cb5 = ttk.Frame(cbarg); cb5.pack(fill="x", pady=(4, 1))
        self._lbl(cb5, text="# ticks", width=11).pack(side="left")
        ttk.Spinbox(cb5, from_=0, to=20, textvariable=self.cbar_nticks, width=4,
                    command=self._redraw).pack(side="left")
        self._lbl(cb5, text="(0 = auto)").pack(side="left", padx=(6, 0))
        Tooltip(cble, "Colorbar label, orientation, label/tick font sizes, "
                      "thickness (fraction of the axes), and tick count.")

        # --- Font ---
        ft = self._group(r, "Fonts")
        fr = ttk.Frame(ft); fr.pack(fill="x")
        # the brand face matches the app chrome (falls back to Segoe UI);
        # journal presets override it for publication
        self.font_family = tk.StringVar(value=UI_FONT)
        ttk.Combobox(fr, textvariable=self.font_family, values=FONTS,
                     state="readonly").pack(side="left", fill="x", expand=True)
        self.font_family.trace_add("write", lambda *a: self._redraw())
        self.font_size = tk.IntVar(value=10)   # base size; per-item sizes are
                                               # set next to each text control
        self._lbl(ft, text="(text sizes are set next to each item)",
                  font=(UI_FONT, 8), foreground="#888").pack(anchor="w")

        # --- Presets (named, stored with the app) ---
        pr = self._group(r, "Presets & projects")
        prow = ttk.Frame(pr); prow.pack(fill="x")
        self.preset_sel = tk.StringVar()
        self.preset_cb = ttk.Combobox(prow, textvariable=self.preset_sel,
                                      state="readonly")
        self.preset_cb.pack(side="left", fill="x", expand=True)
        ttk.Button(prow, text="Load", width=6,
                   command=self._load_named_preset).pack(side="left", padx=(4, 0))
        brow2 = ttk.Frame(pr); brow2.pack(fill="x", pady=(4, 0))
        ttk.Button(brow2, text="Save as...", command=self._save_named_preset).pack(
            side="left", fill="x", expand=True)
        ttk.Button(brow2, text="Delete", command=self._delete_named_preset).pack(
            side="left", fill="x", expand=True)
        ttk.Button(pr, text="Reset all to defaults",
                   command=self._reset_defaults).pack(fill="x", pady=(4, 0))
        Tooltip(self.preset_cb, "Saved control states. Pick one and click Load.")
        ttk.Separator(pr, orient="horizontal").pack(fill="x", pady=4)
        self._lbl(pr, text="Project = every setting + the folders",
                  font=(UI_FONT, 8, "bold")).pack(anchor="w", pady=(12, 3))
        self._lbl(pr, text="(presets above store styling only)",
                  font=(UI_FONT, 8), foreground="#888").pack(anchor="w")
        srow = ttk.Frame(pr); srow.pack(fill="x", pady=(4, 1))
        ssb = ttk.Button(srow, text="Save project...", command=self._save_session)
        ssb.pack(side="left", fill="x", expand=True)
        slb = ttk.Button(srow, text="Open project...", command=self._load_session)
        slb.pack(side="left", fill="x", expand=True)
        Tooltip(slb, "Open a saved project: restores all plotting settings and the input/output folders.")
        Tooltip(ssb, "Save every plotting setting plus the input/output folders "
                     "to a .json file, so you can reopen exactly "
                     "where you left off. Presets cover styling only.")

        # --- Traces (show + D toggle) ---
        tr = self._group(r, "Traces  (check = show,  D = decompression)")
        bb = ttk.Frame(tr); bb.pack(fill="x")
        ttk.Button(bb, text="All", width=5,
                   command=lambda: self._set_all(True)).pack(side="left")
        ttk.Button(bb, text="None", width=5,
                   command=lambda: self._set_all(False)).pack(side="left")
        dlb = ttk.Button(bb, text="Load D list...", command=self._load_dlist)
        dlb.pack(side="left")
        ttk.Button(bb, text="?", width=2, command=self._dlist_help).pack(
            side="left", padx=(2, 0))
        Tooltip(dlb, "Load a .txt/.csv of decompression pressures (GPa). "
                     "Matching pressures get flagged D (dashed). Click ? for "
                     "the exact format.")
        bb2 = ttk.Frame(tr); bb2.pack(fill="x", pady=(4, 1))
        ocb = ttk.Button(bb2, text="Only C", width=7,
                         command=lambda: self._only_branch("C"))
        ocb.pack(side="left")
        odb = ttk.Button(bb2, text="Only D", width=7,
                         command=lambda: self._only_branch("D"))
        odb.pack(side="left")
        Tooltip(ocb, "Show only compression (C) traces; hide all D. Lets you "
                     "read the C trend alone.")
        Tooltip(odb, "Show only decompression (D) traces; hide all C. Lets you "
                     "read the D trend alone.")
        self.trace_count_lbl = self._lbl(bb2, text="", font=(UI_FONT, 8))
        self.trace_count_lbl.pack(side="right")
        self.trace_frame = ttk.Frame(tr); self.trace_frame.pack(fill="x")
        exb = ttk.Button(tr, text="Export D list (CSV) by selection",
                         command=self._export_dlist)
        exb.pack(fill="x", pady=(6, 0))
        Tooltip(exb, "Save the pressures currently flagged D (the ticked D boxes) "
                     "to a .csv/.txt, in the same format Load D list reads back.")

        # --- Export ---
        ex = self._group(r, "Export")
        dr = ttk.Frame(ex); dr.pack(fill="x")
        self._lbl(dr, text="DPI").pack(side="left")
        self.dpi = tk.IntVar(value=300)
        ttk.Spinbox(dr, from_=72, to=600, increment=50, textvariable=self.dpi,
                    width=5).pack(side="left")
        ttk.Button(dr, text="Copy figure", command=self._copy_clipboard).pack(
            side="left", padx=(8, 0))
        ttk.Button(ex, text="Save plot...", command=self._save_plot).pack(fill="x",
                                                                          pady=2)
        ttk.Button(ex, text="Batch export PNG (one per shown trace)...",
                   command=self._batch_export).pack(fill="x", pady=2)
        cr2 = ttk.Frame(ex); cr2.pack(fill="x")
        self.crop_on = tk.BooleanVar()
        ttk.Checkbutton(cr2, text="Crop", variable=self.crop_on).pack(side="left")
        self.crop_min, self.crop_max = tk.StringVar(), tk.StringVar()
        ttk.Entry(cr2, textvariable=self.crop_min, width=7).pack(side="left")
        ttk.Entry(cr2, textvariable=self.crop_max, width=7).pack(side="left")
        self._lbl(cr2, text="nm").pack(side="left")
        ecb = ttk.Button(ex, text="Export CSV\u2026")
        ecb.pack(fill="x", pady=2)
        Tooltip(ecb, "Write per-pressure CSVs into a chosen folder: smoothed "
                     "(wl / cm^-1 / eV + raw + smoothed columns) or "
                     "defringed ({stem}_absorbance_notch.csv, FFT-notch at "
                     "the current Notch width). Works whether or not the "
                     "display toggles are on.")

        def _export_menu(_e=None):
            m = tk.Menu(ecb, tearoff=0)
            m.add_command(label="Smoothed CSV (raw + smoothed columns)\u2026",
                          command=self._export_smoothed)
            m.add_command(label="Defringed CSV (FFT-notch absorbance)\u2026",
                          command=self._export_defringed)
            m.tk_popup(ecb.winfo_rootx(),
                       ecb.winfo_rooty() + ecb.winfo_height())
        ecb.configure(command=_export_menu)

        self._build_journal(r)
        # quick-access strip for the most-used controls (always visible on top).
        # Plain TFrame on purpose: sv_ttk's Card.TFrame and Labelframe both
        # paint their interior from theme images that ignore the configured
        # background, so panel-colored labels inside them show as mismatched
        # patches (worst on the black theme). A plain frame follows the
        # configured background exactly; separators give it the grouped look.
        bar = ttk.Frame(outer, padding=(8, 2))
        bar.pack(side="top", fill="x", padx=6, pady=(2, 2), before=self.rnotebook)
        ttk.Separator(outer, orient="horizontal").pack(
            side="top", fill="x", padx=6, pady=(0, 4), before=self.rnotebook)
        self._qa_title = self._lbl(bar, text="Quick access",
                                   font=(UI_FONT, 10, "bold"))
        self._qa_title.pack(pady=(0, 2))
        # two compact rows so nothing clips at larger font sizes:
        # row 1 = waterfall + line width; row 2 = the two toggles
        b1 = ttk.Frame(bar); b1.pack(fill="x")
        self._lbl(b1, text="wf").pack(side="left")
        wfc = ttk.Combobox(b1, textvariable=self.wf_mode, state="readonly",
                           width=9, values=["off", "2D stacked", "3D ridge"])
        wfc.pack(side="left", padx=(3, 0), fill="x", expand=True)
        Tooltip(wfc, "Waterfall mode: off (shared baseline), 2D stacked, or "
                     "3D ridge. Keys 1 / 2 / 3.")
        smc = ttk.Checkbutton(b1, text="sm", variable=self.show_smooth,
                              command=self._redraw)
        smc.pack(side="left", padx=(6, 0))
        Tooltip(smc, "smooth")
        dfc = ttk.Checkbutton(b1, text="df", variable=self.show_notch,
                              command=self._toggle_notch)
        dfc.pack(side="left", padx=(4, 0))
        Tooltip(dfc, "defringe")
        self._lbl(b1, text="lw").pack(side="left", padx=(6, 0))
        lwe = ttk.Entry(b1, textvariable=self.lw, width=4); lwe.pack(side="left")
        Tooltip(lwe, "lw = line width of the curves.")
        lwe.bind("<Return>", lambda e: self._redraw())
        lwe.bind("<FocusOut>", lambda e: self._redraw())
        b2 = ttk.Frame(bar); b2.pack(fill="x", pady=(4, 1))
        self._lbl(b2, text="cmap").pack(side="left")
        rvb = ttk.Button(b2, text="Reset view", width=10,
                         command=self._reset_view)
        rvb.pack(side="right", padx=(4, 0))
        Tooltip(rvb, "Reset the view: clears any 2D zoom/pan back to auto-fit "
                     "and returns the 3D camera (elevation, azimuth, zoom) to "
                     "default.")
        ttk.Combobox(b2, textvariable=self.cmap, state="readonly",
                     values=colormaps.available()).pack(side="left", fill="x",
                                                        expand=True, padx=(3, 0))
        b3 = ttk.Frame(bar); b3.pack(fill="x", pady=(4, 1))
        self._lbl(b3, text="Y axis").pack(side="left")
        ycb = ttk.Combobox(b3, textvariable=self.ydata, state="readonly", width=11,
                           values=["absorbance", "sample", "background", "dark"])
        ycb.pack(side="left", padx=(3, 0))
        self._lbl(b3, text="X axis").pack(side="left", padx=(8, 0))
        xcb = ttk.Combobox(b3, textvariable=self.xunit, state="readonly", width=6,
                           values=["wl", "wn", "ev"])
        xcb.pack(side="left", padx=(3, 0))
        xcb.bind("<<ComboboxSelected>>", lambda e: self._redraw())

    # ---- slider + numeric entry, two-way synced --------------------------
    def _slider_row(self, parent, label, var, lo, hi, fmt="%.2f", width=10,
                    icon=None):
        # Two-line layout: name (with a mini icon when one fits) on its own
        # line, then slider + value + a small reset button below.
        if icon is None:
            icon = {"Fill opacity": "opacity", "Elevation": "angle",
                    "Azimuth": "rotate", "Zoom": "zoomm",
                    "Stretch X": "arr_h", "Stretch Y": "arr_d",
                    "Stretch Z": "arr_v", "3D line width": "widthI",
                    "3D line opacity": "opacity",
                    "3D detail (points/ridge)": "detail",
                    "Line width": "widthI", "Raw opacity": "opacity",
                    "Notch width %": "widthI"}.get(label)
        box = ttk.Frame(parent); box.pack(fill="x", pady=(8, 5))
        lab = self._lbl(box, text=label)
        lab.pack(anchor="w")
        if icon:
            if not hasattr(self, "_slider_iconlabels"):
                self._slider_iconlabels = []
            self._slider_iconlabels.append((lab, icon))
        row = ttk.Frame(box); row.pack(fill="x")
        rb = ttk.Button(row, width=2, takefocus=False)
        rb.pack(side="right", padx=(2, 0))
        if not hasattr(self, "_slider_resets"):
            self._slider_resets = []
        self._slider_resets.append(rb)
        Tooltip(rb, "Reset to default.")
        ent = ttk.Entry(row, width=6)
        ent.pack(side="right", padx=(6, 0))
        sc = BrandScale(row, from_=lo, to=hi, variable=var,
                        command=lambda *a: self._on_slider(var, ent, fmt))
        sc.pack(side="left", fill="x", expand=True, pady=(2, 0))
        if not hasattr(self, "_theme_scales"):
            self._theme_scales = []
        self._theme_scales.append(sc)
        ent.insert(0, fmt % var.get())

        def commit(_=None, lo=lo, hi=hi, var=var, ent=ent, fmt=fmt):
            try:
                v = float(ent.get())
            except ValueError:
                ent.delete(0, "end"); ent.insert(0, fmt % var.get()); return
            v = max(lo, min(hi, v)); var.set(v)
            ent.delete(0, "end"); ent.insert(0, fmt % v)
            self._redraw()
        ent.bind("<Return>", commit); ent.bind("<FocusOut>", commit)

        init = var.get()
        def reset(ev=None, var=var, ent=ent, fmt=fmt, init=init):
            var.set(init); ent.delete(0, "end"); ent.insert(0, fmt % init)
            self._redraw(); return "break"
        ent.bind("<Button-3>", reset); sc.bind("<Button-3>", reset)
        rb.configure(command=reset)

        self._slider_entries.append((var, ent, fmt))
        return sc, ent

    def _on_slider(self, var, ent, fmt):
        try:
            if self.root.focus_get() is not ent:
                ent.delete(0, "end"); ent.insert(0, fmt % var.get())
        except Exception:
            pass
        self._redraw()

    def _sync_slider_entries(self):
        try:
            foc = self.root.focus_get()
        except Exception:
            foc = None
        for var, ent, fmt in getattr(self, "_slider_entries", []):
            if ent is foc:
                continue
            try:
                new = fmt % float(var.get())
                if ent.get() != new:
                    ent.delete(0, "end"); ent.insert(0, new)
            except Exception:
                pass

    def _reset_3d_view(self):
        self.wf3d_elev.set(22); self.wf3d_azim.set(-60); self.wf3d_zoom.set(1.0)
        self._redraw()

    def _reset_view(self):
        """Reset the 2D zoom/pan (back to auto-fit) and the 3D camera."""
        for v in (self.xmin, self.xmax, self.ymin, self.ymax,
                  self.zmin, self.zmax):
            v.set("")
        self.autoscale.set(True)
        self.wf3d_elev.set(22); self.wf3d_azim.set(-60); self.wf3d_zoom.set(1.0)
        self._log_action("View reset (2D auto-fit + 3D camera)")
        self._redraw()

    def _cam_preset(self, elev, azim):
        self.wf3d_elev.set(elev); self.wf3d_azim.set(azim); self._redraw()

    def _reset_stretch(self):
        self.wf3d_sx.set(1.0); self.wf3d_sy.set(1.0); self.wf3d_sz.set(1.0)
        self._sync_slider_entries()
        self._redraw()

    def _dlist_help(self):
        messagebox.showinfo(
            "Load D list - accepted format",
            "A plain .txt or .csv file listing the DECOMPRESSION pressures "
            "in GPa.\n\n"
            "- Separate values by commas, spaces, tabs, or new lines.\n"
            "- 'p' may be used as the decimal point (1p39 = 1.39).\n"
            "- Header lines or stray words are ignored.\n\n"
            "Examples:\n"
            "  1.80, 3.73, 5.55, 7.27\n"
            "  9.31\n"
            "  11.90\n\n"
            "Each value is matched to a loaded pressure (to 0.001 GPa) and "
            "that trace is set to decompression (D, drawn dashed).")

    def _aspect_ratio(self):
        m = self.aspect_mode.get()
        if m == "Auto (fill)":
            return None
        if m == "custom":
            try:
                w = float(self.aspect_w.get()); h = float(self.aspect_h.get())
                return h / w if w else None
            except ValueError:
                return None
        table = {"1:1": (1, 1), "4:3": (4, 3), "3:2": (3, 2), "16:9": (16, 9)}
        w, h = table.get(m, (1, 1))
        return h / w

    # ---- 3D-ridge options (placed under Waterfall) -----------------------
    def _build_3d_opts(self, r):
        # 3D plot options (journal-clean defaults; toggle to revert)
        td = self._group(r, "3D plot options")
        self.wf3d_even = tk.BooleanVar(value=False)
        evck = ttk.Checkbutton(td, text="Even rank spacing", variable=self.wf3d_even,
                               command=self._redraw)
        evck.pack(anchor="w")
        Tooltip(evck, "Space ridges evenly along the pressure axis (1,2,3...) "
                      "regardless of the real GPa gaps, so crowded pressures stay "
                      "readable. Uncheck to place each ridge at its true pressure.")
        # appearance: walls (filled ridges), walls only, or traces only
        self.wf3d_fill = tk.BooleanVar(value=True)    # derived from the selector
        self.wf3d_lines = tk.BooleanVar(value=True)   # draw the ridge outline
        self.wf3d_look = tk.StringVar(value="Walls + traces")
        lkr = ttk.Frame(td); lkr.pack(fill="x")
        self._lbl(lkr, text="3D look").pack(side="left")
        ttk.Combobox(lkr, textvariable=self.wf3d_look, state="readonly", width=14,
                     values=["Walls + traces", "Walls only", "Traces only"]
                     ).pack(side="left", padx=(3, 0))
        def _apply_look(*_a):
            lk = self.wf3d_look.get()
            self.wf3d_fill.set(lk in ("Walls + traces", "Walls only"))
            self.wf3d_lines.set(lk in ("Walls + traces", "Traces only"))
            self._redraw()
        self.wf3d_look.trace_add("write", _apply_look)
        Tooltip(lkr, "Walls + traces: filled joyplot ridges with outlines. "
                     "Walls only: filled, no outline. Traces only: outlines "
                     "with no fill (clean line joyplot).")
        self.wf3d_color_traces = tk.BooleanVar(value=True)
        ctc = ttk.Checkbutton(td, text="Color traces by colormap",
                              variable=self.wf3d_color_traces,
                              command=self._redraw)
        ctc.pack(anchor="w")
        Tooltip(ctc, "Colour the ridge outlines with the trace colormap instead "
                     "of flat black/white. Pairs well with Traces only.")
        self.wf3d_clean = tk.BooleanVar(value=True)
        clck = ttk.Checkbutton(td, text="Clean panes (white, faint grid)",
                               variable=self.wf3d_clean, command=self._redraw)
        clck.pack(anchor="w")
        Tooltip(clck, "Paint the three back walls a solid colour so ridges read "
                      "clearly. Uncheck for matplotlib's default transparent panes. "
                      "Gridline style is set in the Axes box.")
        bfr = ttk.Frame(td); bfr.pack(fill="x", pady=(2, 1))
        self._lbl(bfr, text="Box frame", width=10).pack(side="left")
        self.wf3d_frame = tk.StringVar(value="open front")
        bfc = ttk.Combobox(bfr, textvariable=self.wf3d_frame,
                           state="readonly", width=11,
                           values=["open front", "closed", "none"])
        bfc.pack(side="left")
        self.wf3d_frame.trace_add("write", lambda *a: self._redraw())
        Tooltip(bfc, "Box edges: 'open front' keeps the corner facing you "
                     "open (classic look, nothing between you and the "
                     "data); 'closed' draws all 12 edges; 'none' leaves "
                     "only the walls.")
        self.wf3d_zlog = tk.BooleanVar(value=False)
        zlck = ttk.Checkbutton(td, text="Log Z (absorbance) scale",
                               variable=self.wf3d_zlog, command=self._redraw)
        zlck.pack(anchor="w")
        Tooltip(zlck, "Plot the Z (absorbance) axis on a base-10 log scale. 3D "
                      "axes have no native log mode, so values are log10 "
                      "transformed and ticks relabelled as powers of ten; "
                      "non-positive values are dropped.")
        self.wf3d_alpha = tk.DoubleVar(value=0.5)
        sc, _e = self._slider_row(td, "Fill opacity", self.wf3d_alpha,
                                  0.0, 1.0, "%.2f")
        Tooltip(sc, "Transparency of each filled wall. Lower = more see-through.")
        self.wf3d_elev = tk.DoubleVar(value=22)
        esc, _ee = self._slider_row(td, "Elevation", self.wf3d_elev, 0, 90, "%.0f")
        Tooltip(esc, "Camera elevation angle (degrees above the horizon).")
        self.wf3d_azim = tk.DoubleVar(value=-60)
        asc2, _ae = self._slider_row(td, "Azimuth", self.wf3d_azim, -180, 180, "%.0f")
        Tooltip(asc2, "Camera azimuth: rotate the scene left/right (degrees).")
        self.wf3d_zoom = tk.DoubleVar(value=1.0)
        scz, _z = self._slider_row(td, "Zoom", self.wf3d_zoom, 0.5, 2.0, "%.2f")
        Tooltip(scz, "Zoom the 3D view in/out (camera distance).")
        # camera quick-presets
        cpr = ttk.Frame(td); cpr.pack(fill="x", pady=(4, 1))
        self._lbl(cpr, text="View").pack(side="left")
        for _t, _e, _a in [("Iso", 22, -60), ("Front", 8, -90),
                           ("Side", 8, 0), ("Top", 89, -90)]:
            ttk.Button(cpr, text=_t, width=5,
                       command=lambda e=_e, a=_a: self._cam_preset(e, a)
                       ).pack(side="left", padx=(2, 0))
        Tooltip(cpr, "Snap the camera to a standard angle, then fine-tune with "
                     "the Elevation / Azimuth sliders above.")
        # box stretch: make the 3D box a rectangle without respacing the data
        self.wf3d_sx = tk.DoubleVar(value=1.0)
        self.wf3d_sy = tk.DoubleVar(value=1.0)
        self.wf3d_sz = tk.DoubleVar(value=1.0)
        ssx, _ssx = self._slider_row(td, "Stretch X", self.wf3d_sx,
                                     0.3, 6.0, "%.2f")
        Tooltip(ssx, "Stretch the 3D box along the spectral (wavelength) axis "
                     "without respacing the data. Right-click resets.")
        ssy, _ssy = self._slider_row(td, "Stretch Y", self.wf3d_sy,
                                     0.3, 6.0, "%.2f")
        Tooltip(ssy, "Stretch the pressure axis to fan out crowded ridges "
                     "without respacing the data. Right-click resets.")
        ssz, _ssz = self._slider_row(td, "Stretch Z", self.wf3d_sz,
                                     0.3, 6.0, "%.2f")
        Tooltip(ssz, "Stretch the height (absorbance) axis. Right-click resets.")
        ttk.Button(td, text="Reset stretch", width=14,
                   command=self._reset_stretch).pack(anchor="w", pady=(0, 2))
        # 3D outline width (independent of the 2D curve width)
        self.wf3d_lw = tk.DoubleVar(value=1.0)
        lwsc3, _lw3 = self._slider_row(td, "3D line width", self.wf3d_lw,
                                       0.3, 4.0, "%.2f")
        Tooltip(lwsc3, "Thickness of the 3D ridge outlines.")
        ecr = ttk.Frame(td); ecr.pack(fill="x")
        self._lbl(ecr, text="3D line color", width=10).pack(side="left")
        self.wf3d_edge_color = tk.StringVar(value="auto")
        eccb = ttk.Combobox(ecr, textvariable=self.wf3d_edge_color,
                            state="readonly", width=10,
                            values=["auto", "black", "white", "gray",
                                    "#888888", "#cccccc", "#444444"])
        eccb.pack(side="left", fill="x", expand=True)
        self.wf3d_edge_color.trace_add("write", lambda *a: self._redraw())
        Tooltip(eccb, "Outline color of the 3D ridge edges. 'auto' = white on "
                      "dark themes, black on light. Set explicitly to override.")
        self.wf3d_edge_alpha = tk.DoubleVar(value=0.9)
        easc, _eae = self._slider_row(td, "3D line opacity", self.wf3d_edge_alpha,
                                      0.0, 1.0, "%.2f")
        Tooltip(easc, "Opacity of the 3D ridge outlines (0 = no outline).")
        # axis label gaps: distance from each axis's tick numbers to its title.
        # Bigger values stop a long number (e.g. 26000) clipping the axis name.
        self.lblpad3d_x = tk.DoubleVar(value=15.0)
        self.lblpad3d_y = tk.DoubleVar(value=15.0)
        self.lblpad3d_z = tk.DoubleVar(value=10.0)
        lpx, _lpx = self._slider_row(td, "Label gap X",
                                     self.lblpad3d_x, 0, 40, "%.0f")
        Tooltip(lpx, "Distance from the spectral-axis numbers to its title. "
                     "Raise it if the axis name overlaps the tick numbers; "
                     "lower it to tuck the title back in.")
        lpy, _lpy = self._slider_row(td, "Label gap Y",
                                     self.lblpad3d_y, 0, 40, "%.0f")
        Tooltip(lpy, "Distance from the pressure-axis numbers to its title.")
        lpz, _lpz = self._slider_row(td, "Label gap Z",
                                     self.lblpad3d_z, 0, 40, "%.0f")
        Tooltip(lpz, "Distance from the height-axis numbers to its title.")
        # faint 2D shadow projections onto the back wall / floor
        self.wf3d_project = tk.StringVar(value="Off")
        prr = ttk.Frame(td); prr.pack(fill="x", pady=(4, 1))
        self._lbl(prr, text="Project").pack(side="left")
        ttk.Combobox(prr, textvariable=self.wf3d_project, state="readonly",
                     width=10, values=["Off", "Back wall", "Floor", "Both"]
                     ).pack(side="left", padx=(3, 0))
        self.wf3d_project.trace_add("write", lambda *a: self._redraw())
        Tooltip(prr, "Drop a faint shadow of every trace onto the back wall "
                     "(a 2D overlay) and/or the floor, as a depth and value cue.")
        self.wf3d_detail = tk.IntVar(value=1000)
        dts, _dt = self._slider_row(td, "3D detail (points/ridge)",
                                    self.wf3d_detail, 200, 3000, "%.0f")
        Tooltip(dts, "Points kept per ridge when rendering 3D. Lower = much "
                     "faster rotation on big spectra. Max keeps full resolution "
                     "(the default). 2D plots and all exports always use full "
                     "data.")
        self.perf_mode = tk.BooleanVar(value=False)
        pmc = ttk.Checkbutton(td, text="Performance mode (faster 3D)",
                              variable=self.perf_mode, command=self._redraw)
        pmc.pack(anchor="w")
        Tooltip(pmc, "Off by default. When on, 3D rotation is much smoother on a "
                     "laptop: ridges are decimated harder and the raw ghost is "
                     "skipped. 2D plots and all exports are unaffected.")
        rmc = ttk.Checkbutton(td, text="Reduce motion (no UI animation)",
                              variable=self.reduce_motion)
        rmc.pack(anchor="w")
        Tooltip(rmc, "Turn off the small one-shot UI animations (the raw-only "
                     "banner reveal). They never run during plotting; this "
                     "disables them entirely.")
        self.wf3d_clip99 = tk.BooleanVar(value=False)
        zcc = ttk.Checkbutton(td, text="Clip Z spikes (99th pct)",
                              variable=self.wf3d_clip99, command=self._redraw)
        zcc.pack(anchor="w")
        Tooltip(zcc, "Cap the auto Z range at the 99th percentile so saturated "
                     "spikes don't blow out the ridge scale. Off (default) "
                     "shows the full data range. Typed Z limits always win.")
        ttk.Button(td, text="Reset view", command=self._reset_3d_view).pack(
            anchor="w", pady=(4, 1))
        self._lbl(td, text="Z (absorbance) range is set in Limits & scale above.",
                  font=(UI_FONT, 8), foreground="#888").pack(anchor="w")

    # ---- journal / figure controls ---------------------------------------
    def _journal_presets(self):
        # name -> (w_in, h_in, font, title_fs, label_fs, tick_fs, lw, dpi)
        # Column widths from each publisher's current author guidelines
        # (2025-2026). Nature/Science/Elsevier require sans-serif (Helvetica/
        # Arial); APS allows serif. Font sizes are FINAL (printed) sizes.
        return {
            "Nature single (89 mm)":     (3.50, 2.60, "Arial", 8, 7, 7, 0.75, 300),
            "Nature double (183 mm)":    (7.20, 5.40, "Arial", 8, 7, 7, 0.75, 300),
            "Science 1-col (5.7 cm)":    (2.24, 1.90, "Arial", 8, 7, 6, 0.75, 300),
            "Science 2-col (12.1 cm)":   (4.76, 3.60, "Arial", 8, 7, 7, 0.75, 300),
            "Science 3-col (18.4 cm)":   (7.24, 5.00, "Arial", 8, 7, 7, 0.75, 300),
            "RSI / AIP 1-col (3.37 in)": (3.37, 2.60, "Arial", 9, 8, 8, 1.0, 300),
            "RSI / AIP 2-col (6.69 in)": (6.69, 5.00, "Arial", 9, 8, 8, 1.0, 300),
            "APS 1-col (3.4 in)":        (3.40, 2.60, "DejaVu Serif", 9, 8, 8, 1.0, 300),
            "APS 2-col (7.0 in)":        (7.00, 5.00, "DejaVu Serif", 9, 8, 8, 1.0, 300),
            "Elsevier 1-col (90 mm)":    (3.54, 2.70, "Arial", 8, 7, 7, 0.75, 300),
            "Elsevier 2-col (190 mm)":   (7.48, 5.20, "Arial", 8, 7, 7, 0.75, 300),
            "square (5 in)":             (5.00, 5.00, None, None, None, None, None, None),
            "wide (10 x 4 in)":          (10.0, 4.00, None, None, None, None, None, None),
        }

    def _build_journal(self, r):
        # Journal / figure: publication presets, live WYSIWYG size preview,
        # export quality. A preset sets width AND house style in one pick.
        jf = self._group(r, "Figure")
        pr = ttk.Frame(jf); pr.pack(fill="x")
        self._lbl(pr, text="Journal preset", width=13).pack(side="left")
        self.fig_preset = tk.StringVar(value="custom")
        jpc = ttk.Combobox(pr, textvariable=self.fig_preset, state="readonly",
                           values=["custom"] + list(self._journal_presets()))
        jpc.pack(side="left", fill="x", expand=True)
        self.fig_preset.trace_add("write",
                                  lambda *a: self._apply_journal_preset())
        Tooltip(jpc, "Set the figure to a journal's current column width AND "
                     "house style (font, sizes, line weight, spines, ticks, "
                     "DPI) in one pick. Widths: Nature 89/183 mm, Science "
                     "5.7/12.1/18.4 cm, RSI/AIP 3.37/6.69 in, APS 3.4/7.0 in, "
                     "Elsevier 90/190 mm.")
        sr = ttk.Frame(jf); sr.pack(fill="x", pady=(2, 0))
        self._lbl(sr, text="W x H in", width=13).pack(side="left")
        self.fig_w = tk.StringVar(value="7.0"); self.fig_h = tk.StringVar(value="5.0")
        we = ttk.Entry(sr, textvariable=self.fig_w, width=6)
        we.pack(side="left", fill="x", expand=True)
        he = ttk.Entry(sr, textvariable=self.fig_h, width=6)
        he.pack(side="left", fill="x", expand=True)
        for e in (we, he):
            e.bind("<Return>", lambda ev: self._apply_preview_size())
            e.bind("<FocusOut>", lambda ev: self._apply_preview_size())
        ttk.Button(sr, text="Apply", width=6,
                   command=self._apply_preview_size).pack(side="left")
        self.fig_preview = tk.BooleanVar(value=False)
        pvc = ttk.Checkbutton(jf, text="Preview at export size (WYSIWYG)",
                              variable=self.fig_preview,
                              command=self._apply_preview_size)
        pvc.pack(anchor="w", pady=(2, 0))
        Tooltip(pvc, "Render the on-screen figure at the exact export width and "
                     "height, so you see the true proportions and printed text "
                     "size. Off = fill the window.")
        # publication export quality (used by Save plot / Copy figure)
        self.fig_transparent = tk.BooleanVar(value=False)
        tcb = ttk.Checkbutton(jf, text="Transparent background",
                              variable=self.fig_transparent)
        tcb.pack(anchor="w")
        Tooltip(tcb, "Save with a transparent page (PNG/SVG/PDF). Overrides the "
                     "face color below.")
        self.fig_tight = tk.BooleanVar(value=True)
        tgt = ttk.Checkbutton(jf, text="Tight bounding box",
                              variable=self.fig_tight)
        tgt.pack(anchor="w")
        Tooltip(tgt, "Trim surrounding whitespace on export (bbox_inches=tight).")
        padr = ttk.Frame(jf); padr.pack(fill="x")
        self._lbl(padr, text="Pad (in)", width=10).pack(side="left")
        self.fig_pad = tk.StringVar(value="0.10")
        ttk.Entry(padr, textvariable=self.fig_pad, width=6).pack(side="left")
        self._lbl(padr, text="  Face").pack(side="left")
        self.fig_facecolor = tk.StringVar(value="auto")
        ttk.Combobox(padr, textvariable=self.fig_facecolor, state="readonly",
                     width=8, values=["auto", "white", "black", "none"]
                     ).pack(side="left", fill="x", expand=True)
        Tooltip(padr, "Pad: whitespace margin kept around a tight box. "
                      "Face: page background on export ('auto' = current theme, "
                      "'none' = transparent).")
        jsb = ttk.Button(jf, text="Apply clean style (no grid, thin spines)",
                         command=self._journal_style)
        jsb.pack(fill="x", pady=(4, 0))
        Tooltip(jsb, "Publication-agnostic tidy: no grid, top/right spines "
                     "hidden, ticks in, minor ticks on. Keeps your current font "
                     "(a journal preset sets the font for you).")

    def _apply_journal_preset(self):
        name = self.fig_preset.get()
        p = self._journal_presets().get(name)
        if not p:                       # 'custom' or unknown: keep everything
            self._apply_preview_size()
            return
        w, h, font, tfs, lfs, kfs, lw, dpi = p
        self._restoring = True          # set many vars without undo/log spam
        try:
            self.fig_w.set("%.2f" % w); self.fig_h.set("%.2f" % h)
            if font:
                self.font_family.set(font)
            if tfs:
                self.title_fs.set(tfs); self.label_fs.set(lfs)
                self.tick_fs.set(kfs)
            if lw:
                self.lw.set(lw)
            if dpi:
                try:
                    self.dpi.set(int(dpi))
                except Exception:
                    pass
            if font is not None:        # a real journal (not square/wide): tidy
                self.grid_on.set(False); self.hide_spines.set(True)
                self.tick_dir.set("in"); self.minor_ticks.set(True)
        finally:
            self._restoring = False
        self._apply_preview_size()
        self._push_undo("journal preset: " + name)

    def _apply_preview_size(self):
        """Size the on-screen canvas to the export dimensions when preview is
        on (WYSIWYG); otherwise let it fill the window. Then redraw."""
        try:
            cw = self.canvas.get_tk_widget()
        except Exception:
            return
        if getattr(self, "fig_preview", None) is not None \
                and self.fig_preview.get():
            try:
                w = max(1.0, float(self.fig_w.get()))
                h = max(1.0, float(self.fig_h.get()))
            except (ValueError, tk.TclError):
                w, h = 7.0, 5.0
            # Pin the tk widget to the exact pixel size AND pack fill=none
            # expand=False: matplotlib's canvas Configure handler follows the
            # widget's allocated size, so the widget must be fixed or it would
            # re-inflate the figure to fill the pane. anchor="n" = top-centered.
            dpi = float(self.fig.get_dpi())
            self.fig.set_size_inches(w, h)
            cw.configure(width=int(round(w * dpi)), height=int(round(h * dpi)))
            cw.pack_configure(fill="none", expand=False, anchor="n")
        else:
            cw.pack_configure(fill="both", expand=True)
        self._redraw()

    def _journal_style(self):
        """Publication-agnostic clean look: no grid, thin spines, ticks in,
        minor ticks on. Font is left to the journal preset."""
        self.grid_on.set(False)
        self.hide_spines.set(True)
        self.tick_dir.set("in")
        self.minor_ticks.set(True)
        self._redraw()

    # ---- actions ----------------------------------------------------------
    def _refresh_fallback_note(self):
        txt = ("cmcrameri missing; batlow/roma/hawaii/lajolla use stand-ins."
               if colormaps.FALLBACKS else "")
        self.fallback_lbl.config(text=txt)
        if txt:
            self.fallback_lbl.pack(anchor="w")
        else:
            self.fallback_lbl.pack_forget()

    def _browse_in(self):
        d = filedialog.askdirectory(title="Select input folder")
        if d:
            self.in_var.set(d)
            self._push_recent("in", d)
            if not self.out_var.get():
                self.out_var.set(os.path.dirname(d))

    def _browse_out(self):
        d = filedialog.askdirectory(title="Select output folder")
        if d:
            self.out_var.set(d)
            self._push_recent("out", d)

    def _update_folder_tips(self):
        """Keep the folder boxes usable: the hover tooltip shows the full
        path, and the visible text scrolls to the tail so the folder NAME
        (the part that matters) is shown, not the drive letter."""
        try:
            self._in_tip.text = self.in_var.get() or "No input folder set"
            self._out_tip.text = self.out_var.get() or "No output folder set"
        except Exception:
            pass
        for ent in (getattr(self, "_in_entry", None),
                    getattr(self, "_out_entry", None)):
            if ent is None:
                continue
            try:
                if self.root.focus_get() is not ent:
                    ent.after_idle(lambda e=ent: e.xview_moveto(1.0))
            except Exception:
                pass

    def _push_recent(self, kind, path):
        """Remember the last 5 input/output folders (settings-backed)."""
        if not path:
            return
        key = "recent_" + kind
        lst = [p for p in self.settings.get(key, []) if p != path]
        lst.insert(0, path)
        self.settings[key] = lst[:5]
        self._save_settings()

    def _folder_menu(self, kind):
        """Dropdown on a folder row: open in Explorer + the recent folders."""
        var = self.in_var if kind == "in" else self.out_var
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="Open in Explorer",
                      command=lambda: self._open_folder(var.get()))
        recents = self.settings.get("recent_" + kind, [])
        if recents:
            m.add_separator()
            for pth in recents:
                lbl = pth if len(pth) <= 60 else "..." + pth[-57:]
                m.add_command(label=lbl,
                              command=lambda pth=pth: (var.set(pth),
                                                       self._push_recent(kind,
                                                                         pth)))
        try:
            m.tk_popup(self.root.winfo_pointerx(), self.root.winfo_pointery())
        finally:
            m.grab_release()

    def _open_path(self, d):
        """Open a folder in the OS file browser (Windows / macOS / Linux)."""
        import sys, subprocess
        try:
            if sys.platform == "win32":
                os.startfile(d)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", d])
            else:
                subprocess.Popen(["xdg-open", d])
        except Exception as e:
            messagebox.showinfo("Open folder",
                                "Could not open:\n%s\n(%s)" % (d, e))

    def _open_folder(self, d):
        if d and os.path.isdir(d):
            self._open_path(d)
        else:
            messagebox.showinfo("Open folder", "Folder not set or missing.")

    def _open_output(self):
        d = self.last_out_dir or self.out_var.get()
        if d and os.path.isdir(d):
            self._open_path(d)
        else:
            messagebox.showinfo("Open output", "No output folder yet.")

    def _logline(self, msg):
        tag = ()
        s = msg.lstrip()
        if s.startswith("!") or "FAIL" in s:
            tag = ("logerr",)
        elif s.startswith("SKIP") or "skipped" in s:
            tag = ("logwarn",)
        self.log.insert("end", msg + "\n", tag)
        self.log.see("end")
        self.root.update_idletasks()

    def _dest_folder(self, in_dir, out_dir):
        base = os.path.basename(os.path.normpath(in_dir)) or "run"
        dest = os.path.join(out_dir, base + "_absorbance")
        if os.path.isdir(dest) and os.listdir(dest):
            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
            dest = os.path.join(out_dir, base + "_absorbance_" + stamp)
        return dest

    def _load_previous(self):
        """Viewer mode on demand: reopen a finished run by re-importing the
        *_absorbance.csv files its Run wrote. Nothing is recomputed or
        written, so it is safe on any completed output subfolder."""
        start = (self.last_out_dir or self.out_var.get().strip()
                 or self.in_var.get().strip())
        d = filedialog.askdirectory(
            title="Pick a processed run folder (contains *_absorbance.csv)",
            initialdir=start or None)
        if d:
            self._load_run_folder(d)

    def _push_recent_run(self, path, results):
        """Remember a loaded/processed run for the blank-state recent list."""
        if not path or not results:
            return
        ps = [r["pressure_val"] for r in results]
        entry = {"path": path, "n": len(results),
                 "pmin": round(min(ps), 1), "pmax": round(max(ps), 1)}
        lst = [e for e in self.settings.get("recent_runs", [])
               if e.get("path") != path]
        lst.insert(0, entry)
        self.settings["recent_runs"] = lst[:8]
        self._save_settings()

    def _render_recent_bar(self):
        """Show clickable recent runs on the blank state (empty tab)."""
        bar = getattr(self, "_recent_bar", None)
        if bar is None:
            return
        recents = self.settings.get("recent_runs", [])
        for w in bar.winfo_children():
            w.destroy()
        if self.results or not recents:
            bar.pack_forget()
            return
        uibg, _fg, _fl, _pb, _pf = self._theme_palette()
        accent = self._brand()["ac3"]
        muted = "#9aa0a6" if self.dark_mode.get() else "#6b7280"
        bar.configure(bg=uibg)
        tk.Label(bar, text="Recent runs:", bg=uibg, fg=muted,
                 font=(UI_FONT, 10)).pack(side="left", padx=(10, 6))
        for e in recents[:4]:
            base = os.path.basename(os.path.normpath(e.get("path", ""))) or "?"
            txt = "%s  (%d, %.0f-%.0f GPa)" % (base, e.get("n", 0),
                                               e.get("pmin", 0),
                                               e.get("pmax", 0))
            lab = tk.Label(bar, text=txt, bg=uibg, fg=accent,
                           font=(UI_FONT, 10, "underline"),
                           cursor="hand2", padx=6)
            lab.pack(side="left")
            lab.bind("<Button-1>",
                     lambda ev, p=e.get("path"): self._load_run_folder(p))
            Tooltip(lab, e.get("path", ""))
        bar.pack(side="bottom", fill="x", pady=(0, 2))

    def _load_run_folder(self, d):
        """Load a finished run folder (viewer mode) into the active tab.
        Shared by the dialog, the recent list, and any folder loader."""
        if not d or self._run_busy():
            return
        try:
            names = os.listdir(d)
        except OSError as e:
            messagebox.showerror("Load previous run",
                                 "Could not read:\n%s\n(%s)" % (d, e))
            return
        if not any(n.lower().endswith("_absorbance.csv")
                   and not n.lower().endswith("_absorbance_notch.csv")
                   for n in names):
            messagebox.showinfo(
                "Load previous run",
                "No *_absorbance.csv files in\n%s\n\nPick the output "
                "subfolder a previous Run wrote (the one 'Open output' "
                "opens)." % d)
            return
        self.in_var.set(d)
        self.log.delete("1.0", "end")
        self._logline("Load previous run:  " + d)
        self.run_btn.config(state="disabled")
        self._set_run_state("Loading\u2026", "#c08000")
        self.run_prog.config(mode="determinate", value=0, maximum=100)
        self._run_queue = queue.Queue()
        self._run_cancel = threading.Event()

        def worker():
            q = self._run_queue
            def qlog(msg):
                q.put(("log", msg))
            try:
                loaded = engine.load_processed_folder(d, log=qlog)
                qlog("Viewer mode: loaded %d processed absorbance CSV(s); "
                     "nothing was recomputed or written." % len(loaded))
                q.put(("done", loaded, [], d, False))
            except Exception as e:
                q.put(("error", e))

        self._run_thread = threading.Thread(target=worker, daemon=True)
        self._run_thread.start()
        self._poll_run()

    # status-chip palette: state color -> (text, soft background), one set
    # per chrome brightness so the pill reads right on light AND black
    _STATE_CHIP = {
        False: {"#2a8a4a": ("#1e7a3a", "#e6f4ea"),   # ready / done
                "#c08000": ("#8a6100", "#fdf3d7"),   # working / cancelling
                "#c0392b": ("#b3261e", "#fdecea")},  # failed
        True:  {"#2a8a4a": ("#7fd89a", "#173822"),
                "#c08000": ("#e8c06a", "#3a2c10"),
                "#c0392b": ("#f1948a", "#3d1512")},
    }

    def _set_run_state(self, text, color):
        self._last_run_state = (text, color)   # re-applied on theme change
        if hasattr(self, "run_state"):
            try:
                pal = self._STATE_CHIP[bool(self.dark_mode.get())]
                fg, bg = pal.get(color, (color, None))
                kw = dict(text=" %s " % text, foreground=fg)
                if bg:
                    kw["background"] = bg
                self.run_state.config(**kw)
                self.root.update_idletasks()
            except Exception:
                pass

    # ---- naming profiles (flexible filename ingestion) ---------------------
    def _profiles(self):
        """Saved custom naming profiles (list of dicts) from settings."""
        return self.settings.setdefault("name_profiles", [])

    def _active_profile(self):
        """The profile Run uses: a saved custom profile or the builtin."""
        name = self.settings.get("active_profile", "")
        for p in self._profiles():
            if p.get("name") == name:
                return p
        return engine.BUILTIN_PROFILE

    def _update_profile_btn(self):
        p = self._active_profile()
        nm = p.get("name", "22-IR-1 default")
        try:
            # bold neutral button; the ac2 gear icon (attached in
            # _apply_brand) is the signal, no competing accent fill
            ttk.Style().configure("NameFmt.TButton",
                                  font=(UI_FONT, 10, "bold"))
            self.profile_btn.config(text=" Name format:  " + nm,
                                    style="NameFmt.TButton")
        except tk.TclError:
            pass

    def _folder_key(self, path):
        try:
            return os.path.normcase(os.path.abspath(path))
        except Exception:
            return path

    def _overrides_for(self, in_dir):
        """Per-file fixes saved for this input folder ({} when none)."""
        return dict(self.settings.get("name_overrides", {})
                    .get(self._folder_key(in_dir), {}))

    def _open_name_format(self):
        """Teach-by-example editor for how filenames are understood, with a
        live whole-folder preview and per-file fixes."""
        in_dir = self.in_var.get().strip()
        files = []
        if os.path.isdir(in_dir):
            try:
                files = sorted(f for f in os.listdir(in_dir)
                               if os.path.isfile(os.path.join(in_dir, f)))[:500]
            except OSError:
                files = []

        d = tk.Toplevel(self.root)
        d.title("Name format")
        d.transient(self.root)
        d.geometry("1200x700")
        d.grab_set()
        self._apply_titlebar(d)

        # side panel: plain-language walkthrough + worked example (the
        # smoothing dialog pattern); everything else goes in `main`
        main = ttk.Frame(d)
        main.pack(side="left", fill="both", expand=True)
        helpf = ttk.LabelFrame(d, text="Guide", padding=8)
        helpf.pack(side="right", fill="y", padx=(0, 10), pady=8)
        ht = tk.Text(helpf, width=44, wrap="word", relief="flat",
                     font=(UI_FONT, 10))
        hsb = ttk.Scrollbar(helpf, command=ht.yview)
        ht.configure(yscrollcommand=hsb.set)
        hsb.pack(side="right", fill="y")
        ht.pack(side="left", fill="both", expand=True)
        ht.insert("1.0", (
            "FUNCTION\n"
            "To process a folder, Run must know five things about every "
            "file: which DAC and sample it belongs to, its pressure, "
            "which channel it is (sample / background / dark), and the "
            "grating-segment number. This window teaches the tool how to "
            "read those from your filenames.\n\n"
            "STANDARD PROFILE\n"
            "The default option reads the classic beamline names, e.g.\n"
            "  vis_Y04_Arch29_12p5_bg_C.001\n"
            "  = DAC Y04, sample Arch29, 12.5 GPa,\n"
            "    background, compression, segment 1.\n"
            "If your files look like that, just close this window.\n\n"
            "TO SET UP A NEW SCHEME\n"
            "1.  Click 'Save as\u2026' and give your profile a name.\n"
            "2.  Set the separator (and prefix, decimal, keywords), then "
            "pick a real filename under 'Teach by example' and label "
            "each piece with the dropdown beneath it. Pick 'ignore' for "
            "pieces that don't matter.\n"
            "3.  Watch the preview: green rows parse, red rows don't "
            "(the note says why). When the 'matched N / N' count "
            "satisfies your working requirements, click 'Use this "
            "profile'.\n\n"
            "EXAMPLE\n"
            "A hypothetical file named:  D42-fo90-15.3GPa-s-2.003\n"
            "  Separator: '-'\n"
            "  Prefix: blank\n"
            "  Pressure decimal: '.'\n"
            "  Strip units: 'gpa'\n"
            "  Sample keyword(s): 's'\n"
            "  Pieces labeled: dac / sample / pressure / role / rep\n\n"
            "FIXING ODD FILES\n"
            "Double-click any red row to type its fields by hand, or "
            "select it and press 'Exclude selected'. Fixes are "
            "remembered for this folder and persist across restarts. "
            "A fixed row shows in blue.\n\n"
            "DETAILS\n"
            "- A missing pressure piece reads as 0 GPa.\n"
            "- Files with no role keyword count as dark.\n"
            "- '12p5' means 12.5 when the decimal character is 'p'.\n"
            "- 'branch' = the compression / decompression tag (C / D on the "
            "plot); its keywords are set in the Grammar row.\n"
            "- Nothing is written until you press Run.\n"))
        ht.configure(state="disabled")

        act = self._active_profile()
        st = {"profile": (engine.default_profile("custom")
                          if act.get("builtin") else json.loads(json.dumps(act))),
              "builtin": bool(act.get("builtin")),
              "overrides": self._overrides_for(in_dir),
              "chips": []}

        # -- profile row ------------------------------------------------------
        prow = ttk.Frame(main, padding=(10, 8, 10, 2)); prow.pack(fill="x")
        self._lbl(prow, text="Profile").pack(side="left")
        names = ["22-IR-1 default (built-in)"] + [p.get("name", "?")
                                                  for p in self._profiles()]
        cur = ("22-IR-1 default (built-in)" if st["builtin"]
               else st["profile"].get("name"))
        pvar = tk.StringVar(value=cur if cur in names else names[0])
        pcb = ttk.Combobox(prow, textvariable=pvar, state="readonly",
                           values=names, width=28)
        pcb.pack(side="left", padx=(4, 6))
        savb = ttk.Button(prow, text="Save as\u2026")
        savb.pack(side="left")
        Tooltip(savb, "Save the current grammar under a name. Making a new "
                      "scheme? Click this FIRST, then edit and label.")
        delb = ttk.Button(prow, text="Delete")
        delb.pack(side="left", padx=(4, 0))
        Tooltip(delb, "Delete the selected saved profile (the built-in "
                      "cannot be deleted).")
        hint = self._lbl(prow, text="", foreground="#888",
                         font=(UI_FONT, 8))
        hint.pack(side="left", padx=(10, 0))

        # -- grammar editor ---------------------------------------------------
        ed = ttk.LabelFrame(main, text="Grammar", padding=8)
        ed.pack(fill="x", padx=10, pady=(4, 2))
        r1 = ttk.Frame(ed); r1.pack(fill="x")
        v_prefix = tk.StringVar(); v_sep = tk.StringVar()
        v_seqsep = tk.StringVar(); v_dec = tk.StringVar()
        v_units = tk.StringVar(); v_bg = tk.StringVar(); v_s = tk.StringVar()
        v_dk = tk.StringVar(); v_c = tk.StringVar(); v_d = tk.StringVar()

        # gray example text embedded in each box; vanishes on click and is
        # NEVER read as a real value (vars_to_profile goes through V()).
        ghosted = {}
        ghost_entries = []

        def _ghost(entry, var, ghost):
            key = id(var)
            ghost_entries.append((entry, var, ghost))
            def show(_e=None):
                if not var.get():
                    ghosted[key] = True
                    var.set(ghost)
                    try:
                        entry.configure(foreground="#888888")
                    except tk.TclError:
                        pass
            def hide(_e=None):
                if ghosted.get(key):
                    ghosted[key] = False
                    var.set("")
                    try:
                        entry.configure(foreground="")
                    except tk.TclError:
                        pass
            entry.bind("<FocusIn>", hide, add="+")
            entry.bind("<FocusOut>", show, add="+")
            show()

        def refresh_ghosts():
            """Re-sync after load_vars wrote REAL values: clear every ghost
            flag (a real value may equal its ghost example, e.g. 'bg'),
            then re-ghost only the genuinely empty boxes."""
            for ent_, var_, gh_ in ghost_entries:
                key = id(var_)
                ghosted[key] = False
                try:
                    ent_.configure(foreground="")
                except tk.TclError:
                    pass
                if not var_.get():
                    ghosted[key] = True
                    var_.set(gh_)
                    try:
                        ent_.configure(foreground="#888888")
                    except tk.TclError:
                        pass

        def V(var):
            """A grammar box's REAL value ('' while it shows its ghost)."""
            return "" if ghosted.get(id(var)) else var.get()
        _tips = {
            "Prefix": "Fixed first piece every filename must start with "
                      "(the classic names start with 'vis'). Leave blank "
                      "if your names have no fixed prefix.",
            "Separator": "The character between pieces of the name, "
                         "usually _ or -",
            "Segment sep": "The character before the grating-segment "
                           "number at the very end (vis_....001 uses '.'). "
                           "Blank if your files have no segment numbers.",
        }
        for lab, var, w, gh in (("Prefix", v_prefix, 6, "vis"),
                                ("Separator", v_sep, 4, "_"),
                                ("Segment sep", v_seqsep, 4, ".")):
            self._lbl(r1, text=lab).pack(side="left")
            e_ = ttk.Entry(r1, textvariable=var, width=w)
            e_.pack(side="left", padx=(2, 10))
            Tooltip(e_, _tips[lab])
            _ghost(e_, var, gh)
        self._lbl(r1, text="Pressure decimal").pack(side="left")
        dcb = ttk.Combobox(r1, textvariable=v_dec, state="readonly", width=3,
                           values=["p", ".", ","])
        dcb.pack(side="left", padx=(2, 10))
        Tooltip(dcb, "The character standing in for the decimal point in "
                     "the pressure piece: 'p' reads 12p5 as 12.5; '.' reads "
                     "12.5 directly.")
        self._lbl(r1, text="Strip units").pack(side="left")
        ue = ttk.Entry(r1, textvariable=v_units, width=9)
        ue.pack(side="left", padx=(2, 0))
        Tooltip(ue, "Unit text to remove from the end of the pressure piece "
                    "before reading the number, comma-separated: gpa,kbar "
                    "reads '15.3GPa' as 15.3.")
        _ghost(ue, v_units, "gpa")
        r2 = ttk.Frame(ed); r2.pack(fill="x", pady=(4, 0))
        self._lbl(r2, text="Background keyword(s)").pack(side="left")
        bge = ttk.Entry(r2, textvariable=v_bg, width=10)
        bge.pack(side="left", padx=(2, 10))
        Tooltip(bge, "The piece that marks a BACKGROUND file (classic "
                     "names use 'bg'). Several alternatives: comma-separate "
                     "them (bg,ref).")
        _ghost(bge, v_bg, "bg")
        self._lbl(r2, text="Sample keyword(s)").pack(side="left")
        se_ = ttk.Entry(r2, textvariable=v_s, width=10)
        se_.pack(side="left", padx=(2, 10))
        Tooltip(se_, "The piece that marks a SAMPLE file (classic names "
                     "use 's'). Several alternatives: comma-separate them.")
        _ghost(se_, v_s, "s")
        self._lbl(r2, text="Dark keyword(s)").pack(side="left")
        dke = ttk.Entry(r2, textvariable=v_dk, width=10)
        dke.pack(side="left", padx=(2, 10))
        Tooltip(dke, "Optional piece that explicitly marks a DARK file "
                     "(e.g. 'dark'). Files with NO keyword also count as "
                     "dark, so this can stay empty.")
        _ghost(dke, v_dk, "dark")
        self._lbl(r2, text="(no keyword = dark)", foreground="#888",
                  font=(UI_FONT, 8)).pack(side="left")
        r3 = ttk.Frame(ed); r3.pack(fill="x", pady=(4, 0))
        self._lbl(r3, text="Compression keyword(s)").pack(side="left")
        ce_ = ttk.Entry(r3, textvariable=v_c, width=10)
        ce_.pack(side="left", padx=(2, 10))
        Tooltip(ce_, "The piece that marks a COMPRESSION point (classic "
                     "names use 'c', shown as C on the plot). Several "
                     "alternatives: comma-separate them.")
        _ghost(ce_, v_c, "c")
        self._lbl(r3, text="Decompression keyword(s)").pack(side="left")
        de_ = ttk.Entry(r3, textvariable=v_d, width=10)
        de_.pack(side="left", padx=(2, 10))
        Tooltip(de_, "The piece that marks a DECOMPRESSION point (classic "
                     "names use 'd', shown as D and dashed on the plot). "
                     "Several alternatives: comma-separate them.")
        _ghost(de_, v_d, "d")
        self._lbl(r3, text="(the C / D tag; optional)", foreground="#888",
                  font=(UI_FONT, 8)).pack(side="left")

        # -- teach by example --------------------------------------------------
        exf = ttk.LabelFrame(main, text="Teach by example  (pick a filename that "
                                     "shows every part, then label each piece)",
                             padding=8)
        exf.pack(fill="x", padx=10, pady=2)
        exrow = ttk.Frame(exf); exrow.pack(fill="x")
        v_ex = tk.StringVar(value=files[0] if files else "")
        excb = ttk.Combobox(exrow, textvariable=v_ex, values=files)
        excb.pack(side="left", fill="x", expand=True)
        Tooltip(excb, "A real filename from the input folder, split into "
                      "pieces below. Pick one that shows EVERY part of your "
                      "scheme (pressure, channel keyword, ...) so each "
                      "piece can be labeled.")
        chipf = ttk.Frame(exf); chipf.pack(fill="x", pady=(6, 0))
        ordlbl = self._lbl(exf, text="", foreground="#888",
                           font=(UI_FONT, 8))
        ordlbl.pack(anchor="w", pady=(4, 0))

        # -- preview -----------------------------------------------------------
        pvf = ttk.LabelFrame(main, text="Preview (whole folder)   "
                                        "(drag the column edges to resize; "
                                        "hover a clipped cell to read it)",
                             padding=6)
        pvf.pack(fill="both", expand=True, padx=10, pady=2)
        cols = ("file", "dac", "sample", "P", "role", "br", "rep", "seg",
                "note")
        # grid layout so the tree gets BOTH scrollbars; columns keep a
        # small minwidth so the user can also drag them narrower
        tv = ttk.Treeview(pvf, columns=cols, show="headings", height=9)
        widths = (230, 55, 70, 50, 78, 32, 36, 36, 280)
        for c, w in zip(cols, widths):
            tv.heading(c, text=c)
            tv.column(c, width=w, minwidth=28,
                      stretch=(c in ("file", "note")))
        tvsb = ttk.Scrollbar(pvf, command=tv.yview)
        tvxb = ttk.Scrollbar(pvf, orient="horizontal", command=tv.xview)
        tv.configure(yscrollcommand=tvsb.set, xscrollcommand=tvxb.set)
        tv.grid(row=0, column=0, sticky="nsew")
        tvsb.grid(row=0, column=1, sticky="ns")
        tvxb.grid(row=1, column=0, sticky="ew")
        pvf.rowconfigure(0, weight=1)
        pvf.columnconfigure(0, weight=1)
        tv.tag_configure("ok", foreground="#2a8a4a")
        tv.tag_configure("bad", foreground="#c0392b")
        tv.tag_configure("fix", foreground="#1a6fb5")
        Tooltip(tv, "Every file in the input folder read with the current "
                    "grammar. Green = understood, red = skipped (the note "
                    "says why), blue = fixed by hand. Double-click a row "
                    "to fix it. Drag the column edges to resize.")

        # hover a clipped cell -> floating readout of the full text
        import tkinter.font as tkfont
        _cellfont = tkfont.nametofont("TkDefaultFont")
        celltip = {"win": None, "at": None}

        def _cell_tip_hide(_e=None):
            if celltip["win"] is not None:
                try:
                    celltip["win"].destroy()
                except tk.TclError:
                    pass
                celltip["win"] = None
                celltip["at"] = None

        def _cell_tip(e):
            row = tv.identify_row(e.y)
            col = tv.identify_column(e.x)
            if not row or not col:
                _cell_tip_hide()
                return
            try:
                idx = int(col.lstrip("#")) - 1
                vals = tv.item(row, "values")
                txt = str(vals[idx]) if 0 <= idx < len(vals) else ""
                colw = int(tv.column(cols[idx], "width"))
            except (ValueError, IndexError, tk.TclError):
                _cell_tip_hide()
                return
            clipped = txt and _cellfont.measure(txt) + 14 > colw
            if not clipped:
                _cell_tip_hide()
                return
            if celltip["at"] == (row, col):
                return                     # already showing this cell
            _cell_tip_hide()
            w = tk.Toplevel(d)
            w.overrideredirect(True)
            w.attributes("-topmost", True)
            tk.Label(w, text=txt, bg="#333333", fg="#ffffff",
                     font=(UI_FONT, 10), padx=8, pady=4,
                     wraplength=560, justify="left").pack()
            w.geometry("+%d+%d" % (e.x_root + 14, e.y_root + 12))
            celltip["win"] = w
            celltip["at"] = (row, col)

        tv.bind("<Motion>", _cell_tip)
        tv.bind("<Leave>", _cell_tip_hide)
        tv.bind("<ButtonPress>", _cell_tip_hide, add="+")
        tv.bind("<MouseWheel>", _cell_tip_hide, add="+")
        brow = ttk.Frame(main, padding=(10, 2)); brow.pack(fill="x")
        match_lbl = self._lbl(brow, text="")
        match_lbl.pack(side="left")
        fixb = ttk.Button(brow, text="Fix selected\u2026")
        fixb.pack(side="left", padx=(10, 0))
        Tooltip(fixb, "Type the selected file's fields by hand (also: "
                      "double-click the row). Beats any pattern; saved for "
                      "this folder.")
        excb2 = ttk.Button(brow, text="Exclude selected")
        excb2.pack(side="left", padx=(4, 0))
        Tooltip(excb2, "Leave the selected file(s) out of the run entirely.")
        clrb = ttk.Button(brow, text="Clear all fixes")
        clrb.pack(side="left", padx=(4, 0))
        Tooltip(clrb, "Forget every hand fix and exclusion for this folder.")
        okb = self._brand_button(brow, "Use this profile", None)
        okb.pack(side="right")
        Tooltip(okb, "Make this profile (plus any hand fixes) the one Run "
                     "uses, and remember it. Nothing is processed until "
                     "you press Run.")
        ttk.Button(brow, text="Close",
                   command=d.destroy).pack(side="right", padx=(0, 6))

        # -- logic -------------------------------------------------------------
        def load_vars():
            # Snapshot first and mute the edit traces: each set() below
            # fires rebuild_chips -> vars_to_profile, which would write a
            # half-loaded (ghost-masked) grammar back INTO st["profile"]
            # before we finished reading it (this ate the role keywords).
            st["loading"] = True
            try:
                p = json.loads(json.dumps(st["profile"]))
                v_prefix.set(p.get("prefix", ""))
                v_sep.set(p.get("sep", "_"))
                v_seqsep.set(p.get("seq_sep", "."))
                v_dec.set(p.get("pressure_decimal", "p"))
                v_units.set(",".join(p.get("pressure_unit_strip", ["gpa"])))
                rm = p.get("role_map", {})
                v_bg.set(",".join(k for k, v in rm.items()
                                  if v == "background" and k))
                v_s.set(",".join(k for k, v in rm.items()
                                 if v == "sample" and k))
                v_dk.set(",".join(k for k, v in rm.items()
                                  if v == "dark" and k))
                bt = p.get("branch_tokens", {})
                v_c.set(",".join(k for k, v in bt.items() if v == "C"))
                v_d.set(",".join(k for k, v in bt.items() if v == "D"))
                refresh_ghosts()
            finally:
                st["loading"] = False

        def vars_to_profile():
            p = st["profile"]
            p["prefix"] = V(v_prefix).strip()
            p["sep"] = V(v_sep) or "_"
            p["seq_sep"] = V(v_seqsep)
            p["pressure_decimal"] = v_dec.get() or "p"
            p["pressure_unit_strip"] = [u.strip() for u in
                                        V(v_units).split(",") if u.strip()]
            rm = {"": "dark"}
            for k in V(v_bg).split(","):
                if k.strip():
                    rm[k.strip().lower()] = "background"
            for k in V(v_s).split(","):
                if k.strip():
                    rm[k.strip().lower()] = "sample"
            for k in V(v_dk).split(","):
                if k.strip():
                    rm[k.strip().lower()] = "dark"
            p["role_map"] = rm
            bt = {}
            for k in V(v_c).split(","):
                if k.strip():
                    bt[k.strip().lower()] = "C"
            for k in V(v_d).split(","):
                if k.strip():
                    bt[k.strip().lower()] = "D"
            # empty boxes = the classic c / d tags (engine default)
            p["branch_tokens"] = bt or {"c": "C", "d": "D"}

        def working_profile():
            if st["builtin"]:
                return engine.BUILTIN_PROFILE
            vars_to_profile()
            return st["profile"]

        def rebuild_chips(*_a):
            for w in chipf.winfo_children():
                w.destroy()
            st["chips"] = []
            if st["builtin"]:
                self._lbl(chipf, text="The built-in grammar is fixed. Pick "
                          "or save a custom profile to edit.",
                          foreground="#888").pack(anchor="w")
                ordlbl.config(text="")
                refresh_preview()
                return
            vars_to_profile()
            p = st["profile"]
            name = v_ex.get()
            if name.lower().endswith(".csv"):
                name = name[:-4]
            ss = p.get("seq_sep", ".")
            if ss and ss in name:
                base, tail = name.rsplit(ss, 1)
                if tail.isdigit():
                    name = base
            toks = [t for t in name.split(p.get("sep", "_")) if t != ""]
            pref = p.get("prefix", "")
            if pref and toks and toks[0].lower() == pref.lower():
                toks = toks[1:]
            order = list(p.get("order", []))
            for i, tok in enumerate(toks):
                col = ttk.Frame(chipf)
                col.pack(side="left", padx=2)
                self._lbl(col, text=tok, font=("Consolas", 9, "bold")
                          ).pack()
                fv = tk.StringVar(value=order[i] if i < len(order)
                                  else "ignore")
                fc = ttk.Combobox(col, textvariable=fv, state="readonly",
                                  width=8, values=list(engine.FIELD_CHOICES))
                fc.pack()
                Tooltip(fc, "What the piece '%s' means: dac / sample / "
                            "pressure / role (channel keyword) / branch "
                            "(the C-D compression tag, keywords set above) "
                            "/ rep (retake number) - or ignore." % tok)
                fv.trace_add("write", lambda *a: chips_to_order())
                st["chips"].append(fv)
            chips_to_order(refresh_only=True)

        def chips_to_order(refresh_only=False):
            if not st["builtin"] and st["chips"]:
                st["profile"]["order"] = [c.get() for c in st["chips"]]
            ordlbl.config(text="Order: " +
                          " > ".join(st["profile"].get("order", []))
                          if not st["builtin"] else "")
            refresh_preview()

        def refresh_preview(*_a):
            tv.delete(*tv.get_children())
            prof = working_profile()
            ok = 0
            for f in files:
                rec = engine.parse_with_profile(f, prof)
                ov = st["overrides"].get(f)
                fixed = ov is not None
                if fixed:
                    rec = engine.apply_override(rec, ov)
                if rec.get("skip"):
                    tv.insert("", "end", values=(f, "", "", "", "", "", "",
                                                 "", rec.get("reason", "")),
                              tags=("bad",))
                else:
                    ok += 1
                    tv.insert("", "end", values=(
                        f, rec["dac"], rec["sample"],
                        "%g" % rec["pressure_val"], rec["meas"],
                        rec["branch"] or "", rec["rep"], rec["seq"],
                        "fixed by hand" if fixed else ""),
                        tags=("fix" if fixed else "ok",))
            match_lbl.config(text="matched %d / %d files" % (ok, len(files)))
            probs = engine.validate_profile(prof)
            hint.config(text="; ".join(probs) if probs else "")

        def on_pick_profile(*_a):
            sel = pvar.get()
            if sel.startswith("22-IR-1"):
                st["builtin"] = True
            else:
                st["builtin"] = False
                for p in self._profiles():
                    if p.get("name") == sel:
                        st["profile"] = json.loads(json.dumps(p))
                        break
                load_vars()
            _set_editor_state()
            rebuild_chips()

        def _set_editor_state():
            s = "disabled" if st["builtin"] else "normal"
            for fr in (r1, r2, r3):
                for w in fr.winfo_children():
                    try:
                        w.configure(state=("readonly" if s == "normal" and
                                           isinstance(w, ttk.Combobox)
                                           else s))
                    except tk.TclError:
                        pass

        def save_as():
            nm = simpledialog.askstring("Save profile", "Profile name:",
                                        parent=d)
            if not nm:
                return
            if st["builtin"]:
                st["profile"] = engine.default_profile(nm)
                st["builtin"] = False
            vars_to_profile()
            st["profile"]["name"] = nm
            plist = self._profiles()
            plist[:] = [p for p in plist if p.get("name") != nm]
            plist.append(json.loads(json.dumps(st["profile"])))
            names2 = (["22-IR-1 default (built-in)"] +
                      [p.get("name", "?") for p in plist])
            pcb.config(values=names2)
            pvar.set(nm)
            self._save_settings()
            _set_editor_state()
            rebuild_chips()

        def delete_profile():
            sel = pvar.get()
            plist = self._profiles()
            plist[:] = [p for p in plist if p.get("name") != sel]
            if self.settings.get("active_profile") == sel:
                self.settings["active_profile"] = ""
            self._save_settings()
            pcb.config(values=["22-IR-1 default (built-in)"] +
                       [p.get("name", "?") for p in plist])
            pvar.set("22-IR-1 default (built-in)")
            on_pick_profile()
            self._update_profile_btn()

        def fix_selected():
            sel = tv.selection()
            if not sel:
                return
            fname = tv.item(sel[0], "values")[0]
            prev = st["overrides"].get(fname, {})
            fd = tk.Toplevel(d); fd.title("Fix " + fname)
            fd.transient(d); fd.grab_set()
            body = ttk.Frame(fd, padding=10); body.pack(fill="both")
            vals = {}
            rowdefs = [("Channel role", "meas",
                        ("", "dark", "background", "sample")),
                       ("DAC", "dac", None), ("Sample", "sample", None),
                       ("Pressure (GPa)", "pressure", None),
                       ("Replicate", "rep", None), ("Segment", "seq", None)]
            for i, (lab, key, choices) in enumerate(rowdefs):
                self._lbl(body, text=lab).grid(row=i, column=0, sticky="w",
                                               pady=1)
                v = tk.StringVar(value=str(prev.get(key, "")))
                vals[key] = v
                if choices:
                    ttk.Combobox(body, textvariable=v, state="readonly",
                                 values=list(choices), width=14
                                 ).grid(row=i, column=1, pady=1)
                else:
                    ttk.Entry(body, textvariable=v, width=16
                              ).grid(row=i, column=1, pady=1)
            def apply_fix():
                ov = {k: v.get().strip() for k, v in vals.items()
                      if v.get().strip() != ""}
                if ov:
                    st["overrides"][fname] = ov
                else:
                    st["overrides"].pop(fname, None)
                fd.destroy()
                refresh_preview()
            ttk.Button(body, text="Remove fix", command=lambda: (
                st["overrides"].pop(fname, None), fd.destroy(),
                refresh_preview())).grid(row=len(rowdefs), column=0,
                                         pady=(8, 0))
            ttk.Button(body, text="Apply", style="Accent.TButton",
                       command=apply_fix).grid(row=len(rowdefs), column=1,
                                               pady=(8, 0))

        def exclude_selected():
            for item in tv.selection():
                fname = tv.item(item, "values")[0]
                st["overrides"][fname] = {"skip": True}
            refresh_preview()

        def clear_fixes():
            st["overrides"].clear()
            refresh_preview()

        def use_profile():
            prof = working_profile()
            probs = engine.validate_profile(prof)
            if probs:
                messagebox.showerror("Name format", "Fix first:\n- " +
                                     "\n- ".join(probs), parent=d)
                return
            if st["builtin"]:
                self.settings["active_profile"] = ""
            else:
                nm = st["profile"].get("name", "custom")
                plist = self._profiles()
                plist[:] = [p for p in plist if p.get("name") != nm]
                plist.append(json.loads(json.dumps(st["profile"])))
                self.settings["active_profile"] = nm
            ovs = self.settings.setdefault("name_overrides", {})
            key = self._folder_key(in_dir)
            if st["overrides"]:
                ovs[key] = st["overrides"]
            else:
                ovs.pop(key, None)
            self._save_settings()
            self._update_profile_btn()
            self._logline("Name format set: %s (%s)"
                          % (prof.get("name", "22-IR-1 default"),
                             "%d file fix(es)" % len(st["overrides"])
                             if st["overrides"] else "no file fixes"))
            d.destroy()

        pvar.trace_add("write", on_pick_profile)
        v_ex.trace_add("write", rebuild_chips)
        for var in (v_prefix, v_sep, v_seqsep, v_dec, v_units, v_bg, v_s,
                    v_dk, v_c, v_d):
            var.trace_add("write",
                          lambda *a: (not st["builtin"]
                                      and not st.get("loading")) and
                          rebuild_chips())
        savb.config(command=save_as)
        delb.config(command=delete_profile)
        fixb.config(command=fix_selected)
        excb2.config(command=exclude_selected)
        clrb.config(command=clear_fixes)
        okb.config(command=use_profile)
        tv.bind("<Double-1>", lambda e: fix_selected())

        if not st["builtin"]:
            load_vars()
        _set_editor_state()
        rebuild_chips()

    def _run(self):
        in_dir, out_dir = self.in_var.get().strip(), self.out_var.get().strip()
        if not os.path.isdir(in_dir):
            messagebox.showerror("Input", "Pick a valid input folder."); return
        if not out_dir:
            messagebox.showerror("Output", "Pick an output folder."); return
        dest = self._dest_folder(in_dir, out_dir)
        self.log.delete("1.0", "end")
        self._logline("Input folder:     " + in_dir)
        self._logline("Output subfolder: " + dest)
        self._push_recent("in", in_dir)
        self._push_recent("out", out_dir)
        self.run_btn.config(text="Cancel", command=self._cancel_run)
        self._set_run_state("Working\u2026", "#c08000")
        self.run_prog.config(mode="determinate", value=0, maximum=100)
        # Read every Tk value the worker needs HERE, on the main thread; Tk
        # variables are not safe to touch from the background thread.
        notch_on = self._notch_on()
        nkw = self._notch_params() if notch_on else None
        profile = self._active_profile()          # naming profile (main thread)
        overrides = self._overrides_for(in_dir)
        self._run_queue = queue.Queue()
        self._run_cancel = threading.Event()

        def worker():
            q = self._run_queue
            def qlog(msg):
                q.put(("log", msg))
            try:
                results, skipped = engine.run(
                    in_dir, dest, log=qlog,
                    should_cancel=self._run_cancel.is_set,
                    profile=profile, overrides=overrides)
                wrote_reduction = bool(results)
                if not results:
                    # viewer mode: re-import this tool's own *_absorbance.csv
                    try:
                        loaded = engine.load_processed_folder(in_dir, log=qlog)
                    except Exception as e:
                        loaded = []
                        qlog("  ! processed-CSV import failed: %r" % e)
                    if loaded:
                        results = loaded
                        qlog("Viewer mode: loaded %d processed absorbance "
                             "CSV(s); nothing was recomputed or written."
                             % len(loaded))
                if notch_on and results:
                    nfx = 0
                    for r in results:
                        try:
                            defringe.write_notch_csv(r, dest, **nkw); nfx += 1
                        except Exception as e:
                            qlog("  NOTCH FAIL %s: %r" % (r["label"], e))
                    qlog("Defringe on: wrote %d *_absorbance_notch.csv -> %s"
                         % (nfx, dest))
                # reduction provenance sidecar (thread-safe: locals + module
                # globals only, no Tk reads)
                if wrote_reduction:
                    try:
                        import datetime as _dt
                        engine.write_provenance(
                            os.path.join(dest, "_reduction.provenance.json"), {
                                "tool": "DAC_QuickLook (Beamline DAC Data Tool)",
                                "version": APP_VERSION,
                                "written": _dt.datetime.now().isoformat(
                                    timespec="seconds"),
                                "kind": "reduction",
                                "input_folder": in_dir,
                                "output_folder": dest,
                                "absorbance": "A = -log10[(Sample-Dark)/"
                                              "(Background-Dark)]",
                                "n_curves": len(results),
                                "defringe_on": bool(notch_on),
                                "defringe_params": nkw,
                                "curves": [{"label": r["label"],
                                            "pressure_GPa": r["pressure_val"]}
                                           for r in results]})
                    except Exception as e:
                        qlog("  ! provenance write failed: %r" % e)
                q.put(("done", results, skipped, dest,
                       self._run_cancel.is_set()))
            except Exception as e:
                q.put(("error", e))

        self._run_thread = threading.Thread(target=worker, daemon=True)
        self._run_thread.start()
        self._poll_run()

    def _cancel_run(self):
        """Ask the running worker to stop after its current group."""
        ev = getattr(self, "_run_cancel", None)
        if ev is not None:
            ev.set()
            self._set_run_state("Cancelling\u2026", "#c08000")
            self.run_btn.config(state="disabled")

    def _poll_run(self):
        """Main-thread pump: drain the worker queue, update the log and the
        progress bar, and finish when the worker reports done or error."""
        q = getattr(self, "_run_queue", None)
        if q is None:
            return
        header = ("stat   measurement @ pressure            "
                  "points (valid)   ->  output file")
        try:
            while True:
                item = q.get_nowait()
                kind = item[0]
                if kind == "log":
                    msg = item[1]
                    self._logline(msg)
                    ls = msg.lstrip().lower()
                    if ls.startswith("found "):
                        self._logline(header)
                        # parses engine.run's "Found N measurement group(s);
                        # M file(s) skipped." line; progress total = N + M
                        # (one bar step per OK / SKIP line that follows)
                        nums = re.findall(r"\d+", msg)
                        tot = (sum(int(n) for n in nums[:2])
                               if len(nums) >= 2 else 1)
                        self.run_prog.config(maximum=max(1, tot), value=0)
                    elif ls.startswith("ok") or ls.startswith("skip"):
                        try:
                            self.run_prog.step(1)
                        except Exception:
                            pass
                elif kind == "error":
                    self._finish_run_error(item[1]); return
                elif kind == "done":
                    _, results, skipped, dest, cancelled = item
                    self._finish_run(results, skipped, dest, cancelled); return
        except queue.Empty:
            pass
        self._run_after = self.root.after(40, self._poll_run)

    def _finish_run_error(self, exc):
        self._run_queue = None
        messagebox.showerror("Run failed", str(exc))
        self._set_run_state("Failed", "#c0392b")
        self.run_prog.config(value=0)
        self.run_btn.config(text="Run", command=self._run, state="normal")

    def _finish_run(self, results, skipped, dest, cancelled=False):
        self._run_queue = None
        if not results:
            results = []
        results.sort(key=lambda r: r["pressure_val"])
        self.results = results
        self._skipped_count = len(skipped) if skipped else 0
        if self._skipped_count:
            self._logline("%d file(s) skipped. 'Name format:' (left panel) "
                          "can teach the tool your naming scheme or fix "
                          "files one by one." % self._skipped_count)
        self.last_out_dir = dest
        self.smooth_cache.clear()
        self.notch_cache.clear()
        self._hide_raw_banner()
        raw_only = [r for r in results
                    if not np.isfinite(r["absorbance"]).any()]
        if raw_only:
            self._logline("%d trace(s) are raw channel(s) only (no absorbance):"
                          " set Overlay Y to sample/background/dark, or use"
                          " Inspect." % len(raw_only))
        if results and len(raw_only) == len(results) \
                and self.ydata.get() == "absorbance":
            # nothing here has absorbance -- show the best raw channel instead
            counts = {ch: sum(1 for r in results if np.isfinite(r[key]).any())
                      for ch, key in (("background", "bg_c"),
                                      ("sample", "samp_c"),
                                      ("dark", "dark_c"))}
            best = max(counts, key=lambda ch: counts[ch])
            if counts[best]:
                self.ydata.set(best)
                self._logline("Overlay Y switched to '%s' automatically (no "
                              "trace in this load has absorbance)." % best)
        # some (not all) traces lack absorbance and we are in overlay
        # absorbance view: offer a one-click channel switch via the banner
        if results and 0 < len(raw_only) < len(results) \
                and self.mode.get() == "overlay" \
                and self.ydata.get() == "absorbance":
            rc = {ch: sum(1 for r in raw_only if np.isfinite(r[key]).any())
                  for ch, key in (("background", "bg_c"), ("sample", "samp_c"),
                                  ("dark", "dark_c"))}
            rbest = max(rc, key=lambda ch: rc[ch])
            if rc[rbest]:
                self._show_raw_banner(len(raw_only), rbest)
        self._build_trace_checks()
        labels = [r["label"] for r in results]
        self.inspect_combo.config(values=labels)
        if labels:
            self.inspect_p.set(labels[0])
        if skipped:
            messagebox.showwarning("Skipped files",
                                   "%d file(s) were skipped. See the Progress "
                                   "log for the reasons." % len(skipped))
        self._save_settings()
        self._redraw()
        if cancelled:
            self._set_run_state(
                "Cancelled (%d trace%s)"
                % (len(results), "" if len(results) == 1 else "s"), "#c08000")
        else:
            done = ("Done: %d trace%s"
                    % (len(results), "" if len(results) == 1 else "s"))
            if skipped:
                self._logline("%d file(s) were skipped; see the SKIP lines "
                              "above for the reasons." % len(skipped))
            self._set_run_state(done, "#2a8a4a")
        try:
            self.run_prog.config(value=self.run_prog["maximum"])
        except Exception:
            pass
        self.run_btn.config(text="Run", command=self._run, state="normal")
        # reflect the run in the active tab: auto-name from the input folder
        # (only while the tab still carries a default name) and refresh strip
        try:
            if getattr(self, "sessions", None) and \
                    0 <= self.active < len(self.sessions):
                cur = self.sessions[self.active]["name"]
                if cur.startswith("Session ") or cur in ("base", ""):
                    src = self.in_var.get() or dest
                    base = os.path.basename(os.path.normpath(src))
                    if base:
                        self.sessions[self.active]["name"] = base[:40]
            self._push_recent_run(self.in_var.get() or dest, results)
            self._sync_tabs()
            self._refresh_drawer()
            self._update_status()   # reflect the freshly auto-named tab
        except Exception:
            pass

    def _build_trace_checks(self):
        for w in self.trace_frame.winfo_children():
            w.destroy()
        self.trace_vars, self.dvars = {}, {}
        for r in self.results:
            row = ttk.Frame(self.trace_frame); row.pack(fill="x")
            show = tk.BooleanVar(value=True); self.trace_vars[r["label"]] = show
            # D defaults: explicit tag wins, else the known-experiment list
            tag = r.get("branch_tag")
            d_default = (tag == "D") or (tag is None and r["pressure_str"]
                         in decomp.decompression_set(r["dac"], r["sample"]))
            dv = tk.BooleanVar(value=d_default); self.dvars[r["label"]] = dv
            txt = "%.2f GPa%s" % (r["pressure_val"],
                                  "  (r%d)" % r["rep"] if r["rep"] > 1 else "")
            scb = ttk.Checkbutton(row, text=txt, variable=show,
                                  command=self._redraw)
            scb.pack(side="left")
            scb.bind("<Double-Button-1>",
                     lambda e, lbl=r["label"]: self._solo_trace(lbl))
            Tooltip(scb, "Double-click to show only this trace (solo); "
                         "double-click again to restore the others.")
            ttk.Checkbutton(row, text="D", variable=dv,
                            command=self._redraw).pack(side="right")

    def _set_all(self, state):
        for v in self.trace_vars.values():
            v.set(state)
        self._redraw()

    def _solo_trace(self, label):
        """Double-click a trace: show only it. Double-click again: restore
        what was shown before the solo."""
        if label not in self.trace_vars:
            return "break"
        live = {k: v.get() for k, v in self.trace_vars.items()}
        # the first click of the double-click already toggled this trace's
        # checkbox; undo that in the snapshot we may restore later
        snap = dict(live)
        snap[label] = not snap[label]
        soloed = all((k == label) == st for k, st in snap.items())
        if soloed and getattr(self, "_solo_prev", None):
            for k, v in self.trace_vars.items():
                v.set(self._solo_prev.get(k, True))
            self._solo_prev = None
            self._log_action("Solo off: " + label)
        else:
            self._solo_prev = snap
            for k, v in self.trace_vars.items():
                v.set(k == label)
            self._log_action("Solo: " + label)
        self._redraw()
        return "break"

    def _only_branch(self, branch):
        for r in self.results:
            v = self.trace_vars.get(r['label'])
            if v is not None:
                v.set(self._branch_of(r) == branch)
        self._redraw()

    def _export_dlist(self):
        ds = sorted({round(r["pressure_val"], 3) for r in self.results
                     if self.dvars.get(r["label"]) and self.dvars[r["label"]].get()})
        path = filedialog.asksaveasfilename(
            title="Save selected decompression (D) pressures",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("Text", "*.txt"), ("All", "*.*")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("pressure_GPa\n")
                for p in ds:
                    f.write("%g\n" % p)
        except OSError as e:
            messagebox.showerror("Export D list",
                                 "Could not write:\n%s\n(%s)" % (path, e))
            return
        self._logline("Exported %d D pressure(s) to %s" % (len(ds), path))
        self._provenance(path, "dlist", {"n_pressures": len(ds),
                                         "pressures_GPa": ds}, files=[path])

    def _load_dlist(self):
        path = filedialog.askopenfilename(
            title="Text file of decompression pressures",
            filetypes=[("Text", "*.txt *.csv"), ("All", "*.*")])
        if not path:
            return
        wanted = set()
        try:
            with open(path, encoding="utf-8-sig") as f:  # -sig eats a BOM
                text = f.read()
        except OSError as e:
            messagebox.showerror("Load D list",
                                 "Could not read:\n%s\n(%s)" % (path, e))
            return
        for tok in re.split(r"[\s,]+", text):
            tok = tok.strip()
            if not tok:
                continue
            try:
                wanted.add(round(float(tok.replace("p", ".")), 3))
            except ValueError:
                pass
        for r in self.results:
            if round(r["pressure_val"], 3) in wanted:
                self.dvars[r["label"]].set(True)
        self._redraw()

    def _branch_of(self, r):
        return "D" if self.dvars.get(r["label"]) and self.dvars[r["label"]].get() \
               else "C"

    def _shown(self):
        return [r for r in self.results if self.trace_vars.get(r["label"])
                and self.trace_vars[r["label"]].get()]

    def _trace_color(self, r, cmap_name, shown):
        """Per-trace color. With 'Lock colors to all datasets' on, the color
        basis is the full loaded set (self.results) keyed by label, so a curve
        keeps its color as others are toggled. Off reproduces the legacy
        shown-subset assignment exactly."""
        lock = getattr(self, "lock_colors", None) is not None and self.lock_colors.get()
        base = self.results if (lock and self.results) else shown
        labels = [x["label"] for x in base]
        if r["label"] not in labels:
            base = shown
            labels = [x["label"] for x in base]
        pv = [x["pressure_val"] for x in base]
        pmin, pmax = (min(pv), max(pv)) if pv else (0.0, 1.0)
        rev = self.cmap_rev.get()
        n = len(base)
        idx = labels.index(r["label"]) if r["label"] in labels else 0
        cr = (n - 1 - idx) if rev else idx
        cmin, cmax = (pmax, pmin) if rev else (pmin, pmax)
        return colormaps.color_for(cmap_name, r["pressure_val"], cmin, cmax, cr, n)

    def _notch_result(self, r):
        """FFT-notch the raw Sample and Background counts independently, then
        recompute absorbance from the defringed channels. Cached per trace;
        cleared when the toggle/width changes or new data loads."""
        key = r["label"]
        if key not in self.notch_cache:
            nkw = self._notch_params()
            sc = defringe.defringe_channel(r["wl"], r["samp_c"], **nkw)
            bc = defringe.defringe_channel(r["wl"], r["bg_c"], **nkw)
            s, b, d = sc["clean"], bc["clean"], r["dark_c"]
            with np.errstate(divide="ignore", invalid="ignore"):
                A = -np.log10((s - d) / (b - d))   # == engine.process_group
            A[~np.isfinite(A)] = np.nan
            self.notch_cache[key] = {
                "absorbance": A, "sample": s, "background": b,
                "s_applied": sc["applied"], "s_pvalue": sc["pvalue"],
                "s_nt": sc["nt_um"], "b_applied": bc["applied"],
                "b_pvalue": bc["pvalue"], "b_nt": bc["nt_um"]}
        return self.notch_cache[key]

    def _defringe_report(self, quiet=False):
        """Log a per-pressure fringe-detection summary at the current width.
        quiet=True (auto-run on enable) skips the no-data popup."""
        if not self.results:
            if not quiet:
                messagebox.showinfo("Defringe", "No data loaded. Run first.")
            return
        nkw = self._notch_params()
        self._logline("Defringe report (width %.0f%%, n*t %.0f-%.0f um, p<=%g):"
                      % (nkw["width_frac"] * 100, nkw["nt_min_nm"] / 1000.0,
                         nkw["nt_max_nm"] / 1000.0, nkw["pvalue_max"]))
        nf = 0
        for r in sorted(self.results, key=lambda rr: rr["pressure_val"]):
            nr = self._notch_result(r)
            tags = []
            if nr["s_applied"]:
                tags.append("S n*t=%.1fum p=%.0e" % (nr["s_nt"], nr["s_pvalue"]))
            if nr["b_applied"]:
                tags.append("B n*t=%.1fum p=%.0e" % (nr["b_nt"], nr["b_pvalue"]))
            if tags:
                nf += 1
            self._logline("  %-24s %s" % (r["label"], ", ".join(tags) or "no fringe"))
        self._logline("Fringe detected in %d / %d pressure(s)."
                      % (nf, len(self.results)))

    def _notch_on(self):
        return bool(getattr(self, "show_notch", None)) and self.show_notch.get()

    def _abs(self, r):
        """Absorbance for display: defringed when the notch toggle is on."""
        return self._notch_result(r)["absorbance"] if self._notch_on() \
            else r["absorbance"]

    def _channel(self, r, which):
        if self._notch_on() and which in ("absorbance", "sample", "background"):
            nr = self._notch_result(r)
            return nr["absorbance"] if which == "absorbance" else nr[which]
        return {"absorbance": r["absorbance"], "sample": r["samp_c"],
                "background": r["bg_c"], "dark": r["dark_c"]}[which]

    def _reset_smoothing(self):
        self.smooth_params = dict(smoothing.DEFAULTS)
        self.smooth_cache.clear()
        self._log_action("Smoothing reset to defaults")
        self._redraw()

    def _toggle_notch(self):
        """Defringe on/off: clear caches (smoothed source changes) and redraw.
        Enabling with the width at 0 restores the 15% default."""
        if self.show_notch.get():
            try:
                w = float(self.notch_width.get())
            except (ValueError, tk.TclError):
                w = 0.0
            if w <= 0:
                self.notch_width.set(defringe.NOTCH_WIDTH_FRAC * 100.0)
        self.notch_cache.clear()
        self.smooth_cache.clear()
        self._sync_slider_entries()
        self._log_action("Defringe (FFT notch) %s"
                         % ("on" if self.show_notch.get() else "off"))
        if self.show_notch.get() and not self.suppress_fringe_report.get():
            self._defringe_report(quiet=True)
        self._redraw()

    def _on_notch_width(self, *_a):
        """Width slider drives the Enable box: 0 unchecks it, nonzero checks
        it. Also invalidates the defringe + smoothing caches."""
        self.notch_cache.clear()
        self.smooth_cache.clear()
        if getattr(self, "_restoring", False):
            return                      # preset/undo loads set vars verbatim
        try:
            w = float(self.notch_width.get())
        except (ValueError, tk.TclError):
            return
        if w <= 0 and self.show_notch.get():
            self.show_notch.set(False)
        elif w > 0 and not self.show_notch.get():
            self.show_notch.set(True)

    def _notch_params(self):
        """Defringe kwargs from the GUI (safe fallbacks to the defaults)."""
        def f(var, dflt):
            try:
                return float(var.get())
            except (ValueError, tk.TclError):
                return dflt
        wf = max(f(self.notch_width, defringe.NOTCH_WIDTH_FRAC * 100.0),
                 0.0) / 100.0
        ntmin = f(self.notch_nt_min, defringe.FRINGE_NT_MIN_NM / 1000.0) * 1000.0
        ntmax = f(self.notch_nt_max, defringe.FRINGE_NT_MAX_NM / 1000.0) * 1000.0
        if not 0 < ntmin < ntmax:
            ntmin = defringe.FRINGE_NT_MIN_NM
            ntmax = defringe.FRINGE_NT_MAX_NM
        pmax = f(self.notch_pmax, defringe.FRINGE_PVALUE_MAX)
        return {"width_frac": wf, "nt_min_nm": ntmin, "nt_max_nm": ntmax,
                "pvalue_max": pmax}

    def _notch_params_changed(self):
        self.notch_cache.clear()
        self.smooth_cache.clear()
        self._log_action("Defringe detection parameters changed")
        self._redraw()

    def _notch_defaults(self):
        """Restore the four defringe detection values to the lab defaults."""
        self.notch_nt_min.set("%g" % (defringe.FRINGE_NT_MIN_NM / 1000.0))
        self.notch_nt_max.set("%g" % (defringe.FRINGE_NT_MAX_NM / 1000.0))
        self.notch_pmax.set("%g" % defringe.FRINGE_PVALUE_MAX)
        self.notch_width.set(defringe.NOTCH_WIDTH_FRAC * 100.0)
        self._sync_slider_entries()
        self._notch_params_changed()

    def _smoothed(self, r):
        if r["label"] not in self.smooth_cache:
            self.smooth_cache[r["label"]] = smoothing.smooth_curve(
                r["wl"], self._abs(r), self.smooth_params)
        return self.smooth_cache[r["label"]]


    # ---- drawing helpers --------------------------------------------------
    def _apply_font(self):
        try:
            matplotlib.rcParams["font.family"] = self.font_family.get()
            matplotlib.rcParams["font.size"] = int(self.font_size.get())
        except Exception:
            pass

    def _set_limits(self):
        if self.autoscale.get():
            return
        def fv(s):
            try:
                return float(s.get())
            except (ValueError, tk.TclError):
                return None
        x0, x1 = fv(self.xmin), fv(self.xmax)
        y0, y1 = fv(self.ymin), fv(self.ymax)
        if x0 is not None and x1 is not None:
            if x0 == x1:
                self._warn_once("xlim_eq", "Limits & scale",
                                "X min equals X max; ignoring the X limits.")
            else:
                self.ax.set_xlim(x0, x1)
        if y0 is not None and y1 is not None:
            if y0 == y1:
                self._warn_once("ylim_eq", "Limits & scale",
                                "Y min equals Y max; ignoring the Y limits.")
            else:
                self.ax.set_ylim(y0, y1)

    def _fill_markers_interval(self):
        try:
            step = float(self.marker_interval.get())
        except ValueError:
            return
        if step <= 0 or not self.results:
            return
        lo = min(float(np.nanmin(r["wl"])) for r in self.results)
        hi = max(float(np.nanmax(r["wl"])) for r in self.results)
        start = float(np.ceil(lo / step) * step)
        vals = np.arange(start, hi + step * 0.5, step)
        self.markers.set(", ".join("%g" % v for v in vals))
        self._redraw()

    def _clear_markers(self):
        self.markers.set("")
        self._redraw()

    def _fill_hmarkers_interval(self):
        try:
            step = float(self.hmarker_interval.get())
        except ValueError:
            return
        if step <= 0 or not self.results:
            return
        rng = []
        for r in self.results:
            a = np.asarray(r.get("absorbance", []), dtype=float)
            a = a[np.isfinite(a)]
            if a.size:
                rng.append((float(a.min()), float(a.max())))
        if not rng:
            return
        lo = min(x[0] for x in rng); hi = max(x[1] for x in rng)
        start = float(np.ceil(lo / step) * step)
        vals = np.arange(start, hi + step * 0.5, step)
        self.hmarkers.set(", ".join("%g" % v for v in vals))
        self._redraw()

    def _clear_hmarkers(self):
        self.hmarkers.set("")
        self._redraw()

    @staticmethod
    def _dash_of(name):
        return {"solid": "-", "dotted": ":", "dashed": "--",
                "dashdot": "-."}.get(name, ":")

    def _sync_marker_style(self):
        """Copy the vertical marker style (color, pattern, width, opacity)
        onto the horizontal markers."""
        self.hmark_color.set(self.vmark_color.get())
        self.hmark_style.set(self.vmark_style.get())
        for src_v, dst_v in ((self.vmark_width, self.hmark_width),
                             (self.vmark_alpha, self.hmark_alpha)):
            try:
                dst_v.set(float(src_v.get()))
            except (ValueError, tk.TclError):
                pass
        self._redraw()

    def _hmarker_positions(self):
        out = []
        for tok in self.hmarkers.get().replace(",", " ").split():
            tok = tok.strip()
            if not tok:
                continue
            try:
                out.append(float(tok))
            except ValueError:
                continue
        return out

    def _marker_positions(self, unit):
        out = []
        for tok in self.markers.get().split(","):
            tok = tok.strip()
            if not tok:
                continue
            try:
                nm = float(tok)
            except ValueError:
                continue
            if nm <= 0:
                continue   # 0 is never a wavelength; avoids 1/0 in wn/eV
            out.append(nm if unit == "wl" else
                       1e7 / nm if unit == "wn" else EV_NM / nm)
        return out

    def _chan_note(self, r):
        """' [B only]'-style suffix for raw-only traces (channel availability)."""
        if np.isfinite(r["absorbance"]).any():
            return ""
        have = [t for t, a in (("S", r["samp_c"]), ("B", r["bg_c"]),
                               ("D", r["dark_c"]))
                if np.isfinite(np.asarray(a)).any()]
        if not have:
            return ""
        return (" [%s only]" % have[0]) if len(have) == 1 \
            else (" [%s]" % "+".join(have))

    def _ordered_legend(self, entries):
        """entries: (handle, pressure, branch[, result]). Order C asc then D
        desc. Raw-only traces get their channel tag; colliding labels get the
        sample name appended; exact duplicates collapse to one entry."""
        c = sorted([e for e in entries if e[2] == "C"], key=lambda e: e[1])
        dd = sorted([e for e in entries if e[2] == "D"], key=lambda e: -e[1])
        ordered = c + dd
        labels = []
        for e in ordered:
            lbl = "%.2f GPa - %s" % (e[1], e[2])
            if len(e) > 3 and e[3] is not None:
                lbl += self._chan_note(e[3])
            labels.append(lbl)
        counts = {}
        for lbl in labels:
            counts[lbl] = counts.get(lbl, 0) + 1
        for i, e in enumerate(ordered):
            if counts[labels[i]] > 1 and len(e) > 3 and e[3] is not None:
                labels[i] += "  " + e[3]["sample"]
        handles, out, seen = [], [], set()
        for e, lbl in zip(ordered, labels):
            if lbl in seen:
                continue
            seen.add(lbl)
            handles.append(e[0])
            out.append(lbl)
        return handles, out

    def _legend_handles(self, handles):
        """Honor the legend Swatch option: 'color box' replaces each handle
        with a thick color patch (readable at small sizes, matches the 3D
        look); 'line' keeps the artists as they are.

        Color extraction is VALIDATED: some 3D artists return data scalars
        from get_color() (crashed as 'Invalid RGBA argument'), so every
        candidate goes through to_rgba and a handle whose color can't be
        determined is kept unchanged rather than guessed."""
        if (getattr(self, "legend_swatch", None) is None
                or self.legend_swatch.get() != "color box"):
            return handles
        import matplotlib.patches as mpatches
        from matplotlib.colors import to_rgba
        out = []
        for h in handles:
            rgba = None
            for getter in ("get_color", "get_facecolor", "get_edgecolor"):
                try:
                    c = getattr(h, getter)()
                except Exception:
                    continue
                cand = c
                try:
                    arr = np.asarray(c)
                    if arr.ndim == 2 and arr.shape[-1] in (3, 4) and len(arr):
                        cand = tuple(float(x) for x in arr[0])
                    elif (arr.ndim == 1 and arr.shape[0] in (3, 4)
                          and arr.dtype.kind == "f"):
                        cand = tuple(float(x) for x in arr)
                except Exception:
                    pass
                try:
                    rgba = to_rgba(cand)
                    break
                except (ValueError, TypeError):
                    continue
            out.append(mpatches.Patch(facecolor=rgba, edgecolor="none")
                       if rgba is not None else h)
        return out

    def _legend_or_colorbar(self, entries, cmap_name, pmin, pmax,
                            ScalarMappable, Normalize):
        continuous = not colormaps.is_categorical(cmap_name)
        # A per-trace legend on a continuous (pressure-mapped) colormap with
        # many traces is unpublishable and hides the data; a colorbar is the
        # standard. Auto-switch unless the user turned auto off.
        auto = getattr(self, "auto_key", None) is None or self.auto_key.get()
        auto_cbar = (auto and continuous and self.legend_on.get()
                     and not self.colorbar_on.get() and len(entries) > 10)
        if (self.colorbar_on.get() and continuous) or auto_cbar:
            sm = ScalarMappable(norm=Normalize(pmin, pmax),
                                cmap=colormaps._continuous_cmap(cmap_name))
            sm.set_array([])
            cb = self.fig.colorbar(sm, ax=self.ax, **self._cbar_kwargs())
            self._style_colorbar(cb)
            if auto_cbar and not getattr(self, "_cbar_autonote", False):
                self._cbar_autonote = True
                self._logline("%d traces on a continuous colormap: showing a "
                              "pressure colorbar (publication standard). Turn "
                              "off 'Auto: colorbar for many traces', or use a "
                              "categorical colormap, for a discrete legend."
                              % len(entries))
        elif self.legend_on.get() and entries:
            h, l = self._ordered_legend(entries)
            h = self._legend_handles(h)
            loc = self.legend_loc.get()
            kw = {"fontsize": int(self.legend_fs.get()),
                  "ncol": int(self.legend_cols.get())}
            ttl = self.legend_title.get().strip()
            if ttl:
                kw["title"] = ttl
                kw["title_fontsize"] = int(self.legend_title_fs.get())
            if loc == "outside right":
                leg = self.ax.legend(h, l, bbox_to_anchor=(1.02, 1),
                                     loc="upper left", **kw)
            else:
                leg = self.ax.legend(h, l, loc=loc, **kw)
            self._style_legend(leg)

    def _style_legend(self, leg):
        """Theme + user-style the legend frame and text."""
        if not leg:
            return
        _u, _f, _fl, pb, pf = self._theme_palette()
        tcol = self._axis_text_colors(pf)[1]
        edge = self.legend_edge.get()
        edge = tcol if edge == "auto" else edge
        fr = leg.get_frame()
        fr.set_facecolor(pb)
        fr.set_alpha(float(self.legend_alpha.get()))
        if self.legend_border.get():
            fr.set_edgecolor(edge)
            fr.set_linewidth(float(self.legend_bw.get()))
        else:
            fr.set_edgecolor("none"); fr.set_linewidth(0)
        for t in leg.get_texts():
            t.set_color(tcol)
        ttl = leg.get_title()
        if ttl is not None:
            ttl.set_color(tcol)

    def _cbar_kwargs(self):
        """colorbar() kwargs from the colorbar controls."""
        kw = {"label": self.cbar_label.get(),
              "orientation": self.cbar_orient.get()}
        try:
            kw["fraction"] = max(0.01, float(self.cbar_width.get()))
        except (ValueError, tk.TclError):
            pass
        return kw

    def _style_colorbar(self, cb):
        """Apply the legend frame controls + size/tick controls to a colorbar."""
        _u, _f, _fl, pb, pf = self._theme_palette()
        tcol = self._axis_text_colors(pf)[1]
        edge = self.legend_edge.get()
        edge = tcol if edge == "auto" else edge

        def _num(var, default, cast=float):
            try:
                return cast(var.get())
            except (ValueError, tk.TclError):
                return default
        try:
            cb.ax.tick_params(colors=tcol,
                              labelsize=_num(self.cbar_tick_fs, 10, int))
            cb.set_label(self.cbar_label.get(),
                         fontsize=_num(self.cbar_label_fs, 10, int),
                         color=tcol)
            n = _num(self.cbar_nticks, 0, int)
            if n > 0:
                from matplotlib.ticker import MaxNLocator
                cb.locator = MaxNLocator(n)
                cb.update_ticks()
            cb.outline.set_alpha(_num(self.legend_alpha, 1.0))
            if self.legend_border.get():
                cb.outline.set_edgecolor(edge)
                cb.outline.set_linewidth(_num(self.legend_bw, 1.0))
            else:
                cb.outline.set_linewidth(0)
        except Exception:
            pass

    # ---- main redraw ------------------------------------------------------
    def _logpath(self):
        return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "beamline_tool.log")

    def _warn(self, title, msg):
        """User-facing warning: progress log + dialog."""
        try:
            self._logline("  ! " + title + ": " + msg)
        except Exception:
            pass
        messagebox.showwarning(title, msg)

    def _warn_once(self, key, title, msg):
        """Warn in a dialog only the first time per session; always log."""
        try:
            self._logline("  ! " + title + ": " + msg)
        except Exception:
            pass
        if not hasattr(self, "_warned"):
            self._warned = set()
        if key in self._warned:
            return
        self._warned.add(key)
        messagebox.showwarning(title, msg)

    def _build_right_axis(self, unit):
        """Optional right Y axis: mirror of left, or %transmittance."""
        mode = self.rightaxis.get()
        if mode == "none":
            return
        try:
            if mode == "mirror":
                sec = self.ax.secondary_yaxis("right")
                sec.set_ylabel(self.ax.get_ylabel())
                self._style_secondary_axis(sec, "y")
            elif mode == "%T":
                if self.ydata.get() != "absorbance":
                    return
                def a2t(A):
                    with np.errstate(over="ignore", invalid="ignore"):
                        return 100.0 * np.power(10.0, -A)
                def t2a(T):
                    with np.errstate(divide="ignore", invalid="ignore"):
                        return -np.log10(np.clip(T, 1e-9, None) / 100.0)
                sec = self.ax.secondary_yaxis("right", functions=(a2t, t2a))
                sec.set_ylabel("Transmittance (%)")
                self._style_secondary_axis(sec, "y")
        except Exception as e:
            self._warn_once("rightaxis_fail", "Right axis",
                            "Could not draw the right axis: %s" % e)

    def _style_secondary_axis(self, sec, axis):
        """Match an optional top/right axis to the theme axis/text colors."""
        try:
            fg = self._mpl_colors()[1]
            acol, tcol = self._axis_text_colors(fg)
            for s in sec.spines.values():
                s.set_color(acol)
            sec.tick_params(colors=tcol, labelsize=int(self.tick_fs.get()))
            lab = sec.xaxis.label if axis == "x" else sec.yaxis.label
            lab.set_color(tcol)
            lab.set_size(int(self.label_fs.get()))
        except Exception:
            pass

    def _error_hint(self, exc):
        m = str(exc).lower()
        if "nan or inf" in m or "infinity" in m:
            return ("A top/right axis in wavenumber or energy needs the "
                    "wavelength axis to stay above 0. Set X min to a positive "
                    "value (e.g. 1), or set Top axis to 'none'.")
        if "could not convert" in m or "invalid literal" in m:
            return ("A numeric box contains non-numeric text. Check the "
                    "axis-limit and tick boxes.")
        if "must be" in m and "odd" in m:
            return "A Savitzky-Golay window must be an odd integer."
        return ""

    def _report_error(self, title, exc):
        tb = traceback.format_exc()
        try:
            with open(self._logpath(), "a", encoding="utf-8") as fh:
                fh.write("\n" + "=" * 60 + "\n"
                         + datetime.datetime.now().isoformat() + "  "
                         + title + "\n" + tb + "\n")
        except Exception:
            pass
        try:
            self._logline("  ! " + title + ": " + str(exc))
            self.cursor_lbl.config(text=title + ": " + str(exc))
        except Exception:
            pass
        msg = str(exc)
        hint = self._error_hint(exc)
        if hint:
            msg += "\n\nLikely fix: " + hint
        msg += "\n\n(Details saved to beamline_tool.log)"
        messagebox.showerror(title, msg)

    def _redraw(self, *args):
        # Coalesce bursts (slider drags, cascading var traces, preset loads)
        # into a single repaint scheduled on the next idle cycle. Big win on a
        # laptop where each full replot is expensive.
        if getattr(self, "_restoring", False):
            return self._redraw_now()
        if getattr(self, "_redraw_after", None):
            return
        try:
            self._redraw_after = self.root.after_idle(self._redraw_now)
        except Exception:
            self._redraw_now()

    def _redraw_now(self):
        self._redraw_after = None
        try:
            self._redraw_inner()
        except Exception as e:
            self._report_error("Plot error", e)
        self._sync_slider_entries()
        if hasattr(self, 'trace_count_lbl') and self.results:
            self.trace_count_lbl.config(
                text='showing %d / %d' % (len(self._shown()), len(self.results)))
        self._update_status()
        self._render_recent_bar()
        self._schedule_snapshot()

    def _schedule_snapshot(self):
        """Coalesce rapid changes (slider drags) into one undo step."""
        if self._restoring:
            return
        if getattr(self, "_snap_after", None):
            try:
                self.root.after_cancel(self._snap_after)
            except Exception:
                pass
        self._snap_after = self.root.after(450, self._push_undo)

    def _update_status(self):
        if not hasattr(self, "status_lbl"):
            return
        try:
            mode = ("3D ridge" if self.wf_mode.get() == "3D ridge"
                    else self.mode.get())
            preset = self.preset_sel.get() or "-"
            n = len(self._shown()) if self.results else 0
            tot = len(self.results) if self.results else 0
            tab = (self.sessions[self.active]["name"]
                   if getattr(self, "sessions", None)
                   and 0 <= self.active < len(self.sessions) else "-")
            txt = ("tab: %s   |   mode: %s   |   preset: %s   |   shown: %d/%d"
                   % (tab, mode, preset, n, tot))
            sk = getattr(self, "_skipped_count", 0)
            if sk:
                txt += "   |   skipped: %d" % sk
            self.status_lbl.config(text=txt)
        except Exception:
            pass

    def _auto_offset(self):
        """Even-spacing step so the shown ridges evenly fill the pressure axis
        currently in view (3D). Turns even spacing on."""
        shown = self._shown() or self.results
        n = len(shown)
        if n < 2:
            return
        pvals = [r["pressure_val"] for r in shown]
        lo, hi = min(pvals), max(pvals)
        if not self.autoscale.get():
            def _f(v):
                try:
                    return float(v.get())
                except (ValueError, tk.TclError):
                    return None
            ya, yb = _f(self.ymin), _f(self.ymax)
            if ya is not None and yb is not None and yb > ya:
                lo, hi = ya, yb
        step = (hi - lo) / (n - 1) if hi > lo else 1.0
        self.wf3d_even.set(True)
        self.wf_step.set("%.4g" % step)
        self._redraw()

    def _draw_empty_state(self):
        """Quickstart text shown in the plot area until something loads."""
        self.ax.set_axis_off()
        _bg, fg = self._mpl_colors()
        self.ax.text(0.5, 0.68, "No data loaded", transform=self.ax.transAxes,
                     ha="center", va="center", color=fg, alpha=0.8,
                     fontsize=15, fontweight="bold")
        msg = ("1.  Pick the Input folder of raw segments\n"
               "2.  Pick an Output folder and press Run\n"
               "3.  Style the result with the right-side panels")
        self.ax.text(0.5, 0.5, msg, transform=self.ax.transAxes,
                     ha="center", va="center", ma="left",
                     color=fg, alpha=0.65, fontsize=11, linespacing=2.1)
        self.ax.text(0.5, 0.32,
                     "'Load previous run\u2026' reopens a finished output "
                     "folder.\nHover any control for a tip;  F1 lists the "
                     "shortcuts.", transform=self.ax.transAxes,
                     ha="center", va="center",
                     color=fg, alpha=0.45, fontsize=9, linespacing=1.8)

    def _redraw_inner(self):
        from matplotlib.cm import ScalarMappable
        from matplotlib.colors import Normalize
        self._apply_font()
        self.fig.clf()
        unit = self.xunit.get()
        cmap_name = self.cmap.get()
        lw = float(self.lw.get())
        self._update_toolbar_mode()

        if not self.results:
            # nothing loaded yet: show the quickstart instead of bare axes
            # (theme the figure here too; _cosmetics_2d never runs on this path)
            bg, _fg = self._mpl_colors()
            self.fig.set_facecolor(bg)
            self.ax = self.fig.add_subplot(111)
            self.ax.set_facecolor(bg)
            self._draw_empty_state()
            self.canvas.draw_idle()
            return

        if self.wf_mode.get() == "3D ridge" and self.mode.get() != "inspect":
            # 3D ridge is inherently multi-pressure; render the landscape.
            # Inspect (one pressure's channels) is 2D only, so it overrides 3D.
            self.ax = self.fig.add_subplot(111, projection="3d")
            self._draw_wf3d(unit, cmap_name, lw)
            # tight_layout fights 3D axes; use a fixed margin instead
            self.fig.subplots_adjust(left=0.02, right=0.98, top=0.96, bottom=0.04)
            self._sync_limit_boxes()
            self._sync_tick_boxes(self.ax, True)
            self.canvas.draw_idle(); return

        self.ax = self.fig.add_subplot(111)
        if self.mode.get() == "inspect":
            self._draw_inspect(unit, lw)
        else:
            self._draw_overlay(unit, cmap_name, lw, ScalarMappable, Normalize)

        # axis labels (prefill the fields with the live defaults)
        if self.mode.get() == "inspect":
            _ydef = "Counts"
        else:
            _ydef = ("Absorbance" if self.ydata.get() == "absorbance"
                     else "Counts")
        self._autofill_labels(unit_label(unit), _ydef)
        xkw, ykw, tkw = {}, {}, {}
        if getattr(self, "xlabel_loc", None) is not None and                 self.xlabel_loc.get() in ("left", "center", "right"):
            xkw["loc"] = self.xlabel_loc.get()
        if getattr(self, "ylabel_loc", None) is not None and                 self.ylabel_loc.get() in ("bottom", "center", "top"):
            ykw["loc"] = self.ylabel_loc.get()
        self.ax.set_xlabel(self.xlabel_v.get() or unit_label(unit), **xkw)
        self.ax.set_ylabel(self.ylabel_v.get() or _ydef, **ykw)
        if self.title_v.get():
            if getattr(self, "title_loc", None) is not None and                     self.title_loc.get() in ("left", "center", "right"):
                tkw["loc"] = self.title_loc.get()
            try:
                tkw["pad"] = float(self.title_pad.get())
            except (ValueError, tk.TclError):
                pass
            self.ax.set_title(self.title_v.get(), **tkw)

        # vertical + horizontal reference markers (independent styling)
        vls = self._dash_of(self.vmark_style.get())
        try:
            vlw = float(self.vmark_width.get())
        except (ValueError, tk.TclError):
            vlw = 1.0
        try:
            va = float(self.vmark_alpha.get())
        except (ValueError, tk.TclError):
            va = 1.0
        for xp in self._marker_positions(unit):
            self.ax.axvline(xp, color=self.vmark_color.get(), ls=vls, lw=vlw,
                            alpha=va)
        hls = self._dash_of(self.hmark_style.get())
        try:
            hlw = float(self.hmark_width.get())
        except (ValueError, tk.TclError):
            hlw = 1.0
        try:
            ha = float(self.hmark_alpha.get())
        except (ValueError, tk.TclError):
            ha = 1.0
        for yp in self._hmarker_positions():
            self.ax.axhline(yp, color=self.hmark_color.get(), ls=hls, lw=hlw,
                            alpha=ha)

        # grid / cosmetics / limits first, so the secondary axes see the final
        # x-range
        self._apply_grid(self.ax, is3d=False)
        self._cosmetics_2d()
        self._set_limits()
        if self.flipx.get():
            self.ax.invert_xaxis()
        if self.flipy.get():
            self.ax.invert_yaxis()

        # top axis: wn / eV are reciprocal in wavelength, so the wavelength
        # range must stay > 0. Clamp + warn instead of crashing the draw.
        top = self.topaxis.get()
        if top != "none" and top != unit:
            if top in ("wn", "ev") and unit == "wl":
                x0, x1 = self.ax.get_xlim()
                lo, hi = (x0, x1) if x0 <= x1 else (x1, x0)
                if lo <= 0:
                    lo = 1.0
                    self.ax.set_xlim((lo, hi) if x0 <= x1 else (hi, lo))
                    self._warn_once(
                        "topaxis_recip", "Top axis (%s)" % top,
                        "Wavenumber/energy need wavelength > 0, so the X "
                        "minimum was clamped above 0 for the top axis.")
            try:
                fwd, inv = _conv(unit, top)
                sec = self.ax.secondary_xaxis("top", functions=(fwd, inv))
                sec.set_xlabel(unit_label(top))
                self._style_secondary_axis(sec, "x")
            except Exception as e:
                self._warn_once("topaxis_fail", "Top axis",
                                "Could not draw the top axis: %s" % e)

        self._build_right_axis(unit)
        self.fig.tight_layout()
        self._sync_limit_boxes()
        self._sync_tick_boxes(self.ax, False)
        self.canvas.draw_idle()

    def _toast(self, text, anchor=None):
        """Small self-fading confirmation chip (D4): near the anchor widget
        when given, else bottom-right of the window. Never raises."""
        try:
            t = tk.Toplevel(self.root)
            t.overrideredirect(True)
            t.attributes("-topmost", True)
            f = tk.Frame(t, bg="#333333", padx=12, pady=6)
            f.pack()
            tk.Label(f, text=text, bg="#333333", fg="#ffffff",
                     font=(UI_FONT, 10)).pack()
            if anchor is not None and anchor.winfo_ismapped():
                x = anchor.winfo_rootx()
                y = anchor.winfo_rooty() - 36
            else:
                x = self.root.winfo_rootx() + self.root.winfo_width() - 220
                y = self.root.winfo_rooty() + self.root.winfo_height() - 90
            t.geometry("+%d+%d" % (x, y))
            t.attributes("-alpha", 0.93)

            def fade(step=0):
                try:
                    a = 0.93 - step * 0.13
                    if a <= 0:
                        t.destroy()
                        return
                    t.attributes("-alpha", a)
                    t.after(70, fade, step + 1)
                except tk.TclError:
                    pass
            t.after(1100, fade, 1)
        except Exception:
            pass

    def _copy_log(self):
        try:
            txt = self.log.get("1.0", "end").rstrip()
            self.root.clipboard_clear(); self.root.clipboard_append(txt)
            self._logline("(log copied to clipboard)")
            self._toast("Log copied")
        except Exception:
            pass

    def _export_settings(self):
        """Dump the current plot configuration to the log (methods-section text)."""
        u = self.xunit.get()
        lines = ["", "=== PLOT SETTINGS ===",
                 "tool: %s %s" % (APP_VERSION, APP_CODENAME),
                 "plot mode: %s   waterfall: %s" % (self.mode.get(),
                                                    self.wf_mode.get()),
                 "x unit: %s   overlay Y: %s" % (u, self.ydata.get()),
                 "colormap: %s%s" % (self.cmap.get(),
                                     " (reversed)" if self.cmap_rev.get() else ""),
                 "smoothing shown: %s" % self.show_smooth.get(),
                 "theme: %s   aspect: %s" % (self.theme_mode.get(),
                                             self.aspect_mode.get())]
        if self.wf_mode.get() == "3D ridge":
            lines.append("3D: filled=%s smoothed=%s elev=%s azim=%s zoom=%s"
                         % (self.wf3d_fill.get(), self.show_smooth.get(),
                            self.wf3d_elev.get(), self.wf3d_azim.get(),
                            self.wf3d_zoom.get()))
        lines.append("absorbance: A = -log10[(Sample-Dark)/(Background-Dark)]")
        lines.append("=====================")
        for ln in lines:
            self._logline(ln)

    def _auto_ticks(self):
        """Clear all tick spacing so matplotlib auto-places ticks again."""
        for v in (self.xmaj, self.xminor, self.ymaj, self.yminor,
                  self.zmaj, self.zminor):
            v.set("")
        self.minor_ticks.set(False)
        for k in self._tick_edited:
            self._tick_edited[k] = False
        self._log_action("Axis ticks: auto")
        self._redraw()

    def _mark_tick_edited(self, var):
        # flag as user-edited only when the text actually differs from what
        # _sync_tick_boxes auto-filled: navigation/copy keys (arrows, Home,
        # End, Ctrl+C) fire <KeyRelease> without changing the value
        if str(var) not in self._tick_edited:
            return
        if var.get() != self._tick_autofill.get(str(var)):
            self._tick_edited[str(var)] = True

    def _reset_field(self, var, kind=None):
        """Right-click handler: blank a box and let it return to auto."""
        var.set("")
        if kind == "label" and str(var) in getattr(self, "_label_keys", {}):
            self._label_edited[self._label_keys[str(var)]] = False
        if kind == "tick" and str(var) in getattr(self, "_tick_edited", {}):
            self._tick_edited[str(var)] = False
        self._redraw()
        return "break"

    def _sync_tick_boxes(self, ax, is3d):
        """Show the live major-tick spacing in the boxes (display-only until
        the user types a value)."""
        import numpy as _np
        def spacing(axisobj):
            try:
                locs = sorted(float(t) for t in axisobj.get_majorticklocs())
            except Exception:
                return None
            if len(locs) >= 2:
                d = _np.diff(locs)
                d = d[d > 0]
                if len(d):
                    return float(_np.median(d))
            return None
        items = [(self.xmaj, ax.xaxis), (self.ymaj, ax.yaxis)]
        if is3d and hasattr(ax, "zaxis"):
            items.append((self.zmaj, ax.zaxis))
        for var, axisobj in items:
            if self._tick_edited.get(str(var)):
                continue
            sp = spacing(axisobj)
            if sp:
                s = "%.4g" % sp
                var.set(s)
                self._tick_autofill[str(var)] = s

    def _mark_label_edited(self, var):
        self._label_edited[self._label_keys[str(var)]] = True

    def _autofill_labels(self, xdef, ydef, zdef=None):
        """Show the live default axis labels in the fields until the user
        edits them; keeps the fields honest when switching 2D <-> 3D."""
        if self._restoring:
            return
        for key, var, dflt in [("xlabel", self.xlabel_v, xdef),
                               ("ylabel", self.ylabel_v, ydef),
                               ("zlabel", self.zlabel_v, zdef)]:
            if not self._label_edited.get(key) and dflt and var.get() != dflt:
                var.set(dflt)

    def _sync_limit_boxes(self):
        """Reflect the live data limits in the boxes; relabel per plot mode."""
        is3d = self.wf_mode.get() == "3D ridge"
        if hasattr(self, "_lim_hint"):
            self._lim_hint.config(
                text=("3D: X=wavelength  Y=pressure  Z=absorbance" if is3d
                      else "2D: X=wavelength  Y=absorbance  (Z = 3D only)"))
        if not self.autoscale.get():
            return
        try:
            x0, x1 = sorted(self.ax.get_xlim())
            self.xmin.set("%.4g" % x0); self.xmax.set("%.4g" % x1)
            y0, y1 = sorted(self.ax.get_ylim())
            self.ymin.set("%.4g" % y0); self.ymax.set("%.4g" % y1)
            if is3d and hasattr(self.ax, "get_zlim"):
                z0, z1 = sorted(self.ax.get_zlim())
                self.zmin.set("%.4g" % z0); self.zmax.set("%.4g" % z1)
        except Exception:
            pass

    def _apply_grid(self, ax, is3d=False):
        """Apply major and minor grid, each with its own width, opacity,
        color and dash pattern."""
        def _f(v, dflt):
            try:
                return float(v.get())
            except (ValueError, tk.TclError):
                return dflt
        gw = _f(self.grid_width, 0.6); ga = _f(self.grid_alpha, 0.4)
        gc = self.grid_color.get(); gls = self._dash_of(self.grid_style.get())
        mw = _f(self.grid_minor_width, 0.4); ma = _f(self.grid_minor_alpha, 0.25)
        mc = self.grid_minor_color.get()
        mls = self._dash_of(self.grid_minor_style.get())
        if is3d:
            ax.grid(bool(self.grid_on.get()))
            try:
                for axis in (ax.xaxis, ax.yaxis, ax.zaxis):
                    info = axis._axinfo["grid"]
                    info["linewidth"] = gw
                    info["linestyle"] = gls
                    if gc != "auto":
                        info["color"] = gc
            except Exception:
                pass
            return
        if self.grid_on.get():
            kw = dict(alpha=ga, linewidth=gw, linestyle=gls)
            if gc != "auto":
                kw["color"] = gc
            ax.grid(True, which="major", **kw)
        else:
            ax.grid(False, which="major")
        if self.grid_minor_on.get():
            ax.minorticks_on()
            mk = dict(alpha=ma, linewidth=mw, linestyle=mls)
            if mc != "auto":
                mk["color"] = mc
            ax.grid(True, which="minor", **mk)
        else:
            ax.grid(False, which="minor")

    def _apply_axis_ticks(self, ax, is3d=False):
        """Per-axis major/minor spacing, direction, length, width, sides."""
        from matplotlib.ticker import MultipleLocator, AutoMinorLocator

        def fv(var):
            try:
                v = float(var.get())
                return v if v > 0 else None
            except (ValueError, tk.TclError):
                return None

        def dv(var, default):
            try:
                return float(var.get())
            except (ValueError, tk.TclError):
                return default

        dirn = self.tick_dir.get()
        lmaj = dv(self.tick_len_major, 3.5)
        lmin = dv(self.tick_len_minor, 2.0)
        w = dv(self.tick_width, 0.8)
        ax.tick_params(which="major", direction=dirn, length=lmaj, width=w)
        ax.tick_params(which="minor", direction=dirn, length=lmin, width=w)
        if not is3d and self.ticks_allsides.get():
            ax.tick_params(which="both", top=True, right=True)
        pairs = [(ax.xaxis, self.xmaj, self.xminor),
                 (ax.yaxis, self.ymaj, self.yminor)]
        if is3d:
            pairs.append((ax.zaxis, self.zmaj, self.zminor))
        for axisobj, majv, minv in pairs:
            if is3d:
                # 3D tick direction must be set per-axis (best effort; some
                # matplotlib builds render 3D ticks one-sided regardless).
                try:
                    axisobj.set_tick_params(direction=dirn, length=lmaj, width=w)
                except Exception:
                    pass
            mj = fv(majv)
            # only honor a typed spacing; an auto-filled value stays display-only
            if mj and self._tick_edited.get(str(majv), True):
                axisobj.set_major_locator(MultipleLocator(mj))
            mn = fv(minv)
            if mn:
                axisobj.set_minor_locator(MultipleLocator(mn))
            elif self.minor_ticks.get():
                try:
                    axisobj.set_minor_locator(AutoMinorLocator())
                except Exception:
                    pass

    def _cosmetics_2d(self):
        """Journal fonts/spines/ticks + light/dark palette on the 2D axes."""
        bg, fg = self._mpl_colors()
        acol, tcol = self._axis_text_colors(fg)
        self.fig.set_facecolor(bg)
        self.ax.set_facecolor(bg)
        try:
            self.ax.set_xscale("log" if getattr(self, "xscale", None) and
                               self.xscale.get() == "log" else "linear")
            self.ax.set_yscale("log" if getattr(self, "yscale", None) and
                               self.yscale.get() == "log" else "linear")
        except Exception:
            pass
        self.ax.title.set_size(int(self.title_fs.get())); self.ax.title.set_color(tcol)
        self.ax.xaxis.label.set_size(int(self.label_fs.get()))
        self.ax.yaxis.label.set_size(int(self.label_fs.get()))
        self.ax.xaxis.label.set_color(tcol); self.ax.yaxis.label.set_color(tcol)
        try:                                # label gap (labelpad), both axes
            self.ax.xaxis.labelpad = float(self.xlabelpad.get())
            self.ax.yaxis.labelpad = float(self.ylabelpad.get())
        except (ValueError, tk.TclError):
            pass
        self.ax.tick_params(colors=tcol, labelsize=int(self.tick_fs.get()))
        try:
            slw = float(self.spine_lw.get())
        except (ValueError, tk.TclError):
            slw = None
        for s in self.ax.spines.values():
            s.set_color(acol)
            if slw is not None:
                s.set_linewidth(slw)
        leg = self.ax.get_legend()
        if leg:
            for t in leg.get_texts():
                t.set_color(tcol)
        self._apply_axis_ticks(self.ax, is3d=False)
        if self.hide_spines.get():
            self.ax.spines["top"].set_visible(False)
            self.ax.spines["right"].set_visible(False)
        ratio = self._aspect_ratio()
        if ratio:
            try:
                self.ax.set_box_aspect(ratio)
            except Exception:
                pass

    # ---- overlay (with optional 2D waterfall offset, C solid / D dashed) --
    def _trace_ls(self, r):
        """Base 2D line style, with decompression (D) dashed when toggled on."""
        _LS = {"solid": "-", "dashed": "--", "dotted": ":", "dashdot": "-."}
        base = _LS.get(self.line_style.get(), "-") if hasattr(self, "line_style") else "-"
        dashD = self.dash_decomp.get() if hasattr(self, "dash_decomp") else True
        return "--" if (dashD and self._branch_of(r) == "D") else base

    def _draw_overlay(self, unit, cmap_name, lw, ScalarMappable, Normalize):
        shown = self._shown()
        if not shown:
            return
        _cbase = (self.results if (getattr(self, "lock_colors", None) is not None
                   and self.lock_colors.get() and self.results) else shown)
        # color range spans _cbase (locked colors = ALL loaded traces);
        # geometry (ridge positions, ticks, limits) must use shown only
        pv_color = [r["pressure_val"] for r in _cbase]
        pmin, pmax = min(pv_color), max(pv_color)
        pvals = [r["pressure_val"] for r in shown]
        rev = self.cmap_rev.get()
        n = len(shown)
        chan = self.ydata.get()
        is_abs = chan == "absorbance"
        stacked = self.wf_mode.get() == "2D stacked"
        try:
            step = float(self.wf_step.get())
        except ValueError:
            step = 0.0

        entries = []
        for rank, r in enumerate(shown):
            color = self._trace_color(r, cmap_name, shown)
            ls = self._trace_ls(r)
            x = unit_x(r, unit)
            off = rank * step if stacked else 0.0
            y = self._channel(r, chan) + off
            if is_abs and self.show_smooth.get():
                ra = float(self.raw_opacity.get())
                if ra > 0:
                    self.ax.plot(x, y, color=color, lw=lw, ls=ls, alpha=ra)
                line, = self.ax.plot(x, self._smoothed(r) + off, color=color,
                                     lw=lw * 1.6, ls=ls)
            else:
                line, = self.ax.plot(x, y, color=color, lw=lw, ls=ls)
            entries.append((line, r["pressure_val"], self._branch_of(r), r))
            if stacked and self.wf_label.get():
                xs = x[np.isfinite(x)]
                if len(xs):
                    self.ax.text(np.nanmax(xs), np.nanmedian(y), "%.1f" %
                                 r["pressure_val"], fontsize=7, va="center",
                                 color=color)
        self._legend_or_colorbar(entries, cmap_name, pmin, pmax,
                                 ScalarMappable, Normalize)

    # ---- 3D ridge waterfall (journal-ready) ------------------------------
    def _draw_wf3d(self, unit, cmap_name, lw):
        from mpl_toolkits.mplot3d.art3d import Poly3DCollection
        from mpl_toolkits.mplot3d import proj3d as _proj3d
        shown = sorted(self._shown(), key=lambda r: r["pressure_val"])
        if not shown:
            return
        _cbase = (self.results if (getattr(self, "lock_colors", None) is not None
                   and self.lock_colors.get() and self.results) else shown)
        # color range spans _cbase (locked colors = ALL loaded traces);
        # geometry (ridge positions, ticks, limits) must use shown only
        pv_color = [r["pressure_val"] for r in _cbase]
        pmin, pmax = min(pv_color), max(pv_color)
        pvals = [r["pressure_val"] for r in shown]
        rev = self.cmap_rev.get()
        n = len(shown)
        chan = self.ydata.get()
        use_sm = chan == "absorbance" and self.show_smooth.get()
        lw = float(self.wf3d_lw.get())          # 3D outline width (own control)
        color_lines = self.wf3d_color_traces.get()
        show_lines = self.wf3d_lines.get()
        zlog = getattr(self, "wf3d_zlog", None) is not None and self.wf3d_zlog.get()

        def _zt(zz):
            # log10 transform for the Z (absorbance) axis; non-positive -> NaN
            if not zlog:
                return zz
            zz = np.asarray(zz, dtype=float)
            out = np.full(zz.shape, np.nan)
            pos = np.isfinite(zz) & (zz > 0)
            out[pos] = np.log10(zz[pos])
            return out

        # gather (x, z) per ridge; keep raw alongside for the faded ghost
        ridges = []
        raw_ridges = []
        allz = []
        ghost = use_sm and float(self.raw_opacity.get()) > 0
        cap = int(self.wf3d_detail.get())
        if self.perf_mode.get():
            ghost = False
            cap = min(cap, 400) if cap else 400
        def _dec(xx, zz):
            # decimate to <= cap points for fast 3D rendering (display only)
            if cap and len(xx) > cap:
                idx = np.linspace(0, len(xx) - 1, cap).astype(int)
                return xx[idx], zz[idx]
            return xx, zz
        for r in shown:
            x = unit_x(r, unit)
            z = _zt(self._smoothed(r) if use_sm else self._channel(r, chan))
            m = np.isfinite(x) & np.isfinite(z)
            ridges.append(_dec(x[m], z[m]))
            allz.append(z[m])
            if ghost:
                zr = _zt(self._channel(r, chan))
                mr = np.isfinite(x) & np.isfinite(zr)
                raw_ridges.append(_dec(x[mr], zr[mr]))
            else:
                raw_ridges.append(None)
        allz = np.concatenate(allz) if allz else np.array([0.0, 1.0])
        if allz.size == 0 or not np.isfinite(allz).any():
            self._warn_once("wf3d_nodata", "3D ridge",
                            "No finite data in the selected channel for the "
                            "shown traces. Switch Overlay Y (e.g. sample or "
                            "background) or use Inspect for raw-only data.")
            return

        # Z (absorbance) range from the Axis-limits boxes; blank = auto.
        # Auto = the FULL data range; the optional "Clip Z spikes" checkbox
        # caps it at the 99th percentile (it used to clip silently, which
        # cut real data). Type a Z max to override either.
        def fv(s):
            try:
                return float(s.get())
            except (ValueError, tk.TclError):
                return None
        # When Auto is on, derive Z from the data only. Reading the boxes here
        # would feed back the margin-expanded limits that _sync_limit_boxes
        # writes after each draw, so the Z range (and the ridge height) would
        # run away a little more on every redraw.
        auto = self.autoscale.get()
        zlo = None if auto else fv(self.zmin)
        zhi = None if auto else fv(self.zmax)
        if zlog:
            zlo = np.log10(zlo) if (zlo is not None and zlo > 0) else None
            zhi = np.log10(zhi) if (zhi is not None and zhi > 0) else None
        if zlo is None:
            zlo = float(np.nanmin(allz))
        if zhi is None:
            zmax = float(np.nanmax(allz))
            clip = (auto and getattr(self, "wf3d_clip99", None) is not None
                    and self.wf3d_clip99.get())
            zhi = float(np.nanpercentile(allz, 99)) if clip else zmax
            if clip and zhi < zmax and not getattr(self, "_zclip_noted", False):
                self._zclip_noted = True
                self._logline("Z clipped at 99th pct (%.3g); data max %.3g. "
                              "Untick 'Clip Z spikes' for the full range."
                              % (zhi, zmax))

        even = self.wf3d_even.get()
        try:
            wf_step = float(self.wf_step.get())
        except (ValueError, tk.TclError):
            wf_step = 0.2
        if wf_step <= 0:
            wf_step = 0.2
        # even spacing: ridges sit at rank*step; the box-aspect y below is
        # scaled by the same factor so Offset/step visibly spreads or tightens
        # the ridges (default 0.2 keeps the original look).
        ypos = [i * wf_step for i in range(n)] if even else pvals
        # shared x/z centroid for the monotonic-in-y depth sort below
        _xs = [(xx.min() + xx.max()) / 2 for xx, _ in ridges if len(xx)]
        _xmid = float(np.mean(_xs)) if _xs else 0.0
        _zmid = (zlo + zhi) / 2.0

        if self.wf3d_fill.get():
            # joyplot: each ridge is its OWN Poly3DCollection so matplotlib
            # depth-sorts them per-collection. One combined collection misorders
            # tall ridges at some angles (the rendering glitch); per-ridge fixes it.
            _ealpha = float(self.wf3d_edge_alpha.get())
            _ecname = self.wf3d_edge_color.get()
            if _ecname == "auto":
                _ergb = (0.85, 0.85, 0.85) if self.dark_mode.get() else (0, 0, 0)
            else:
                from matplotlib.colors import to_rgb as _torgb
                try:
                    _ergb = _torgb(_ecname)
                except Exception:
                    _ergb = (0, 0, 0)
            edgecol = (_ergb[0], _ergb[1], _ergb[2], _ealpha)
            # Force strictly monotonic-in-y draw order. matplotlib sorts each
            # collection by its vertex-AVERAGE depth, which tall/spiky ridges
            # throw off (back walls overpaint front ones at some angles). We
            # override the sort key with the ridge's y-plane centroid depth
            # (affine in y, so always correctly ordered as the view rotates).
            def _tag_depth(pc, yc):
                base = pc.do_3d_projection
                def _proj(*a, **k):
                    base(*a, **k)
                    _, _, zs = _proj3d.proj_transform(_xmid, yc, _zmid, pc.axes.M)
                    return zs
                pc.do_3d_projection = _proj
            for rank, (r, (x, z)) in enumerate(zip(shown, ridges)):
                if len(x) < 2:
                    continue
                yv = ypos[rank]
                col = self._trace_color(r, cmap_name, shown)
                style = "--" if self._branch_of(r) == "D" else "-"
                # faded raw ghost drawn first so the smoothed ridge sits on top
                if ghost and raw_ridges[rank] is not None:
                    xr, zr = raw_ridges[rank]
                    if len(xr) >= 2:
                        zrc = np.clip(zr, zlo, zhi)
                        gpoly = [[(xr[0], yv, zlo)]
                                 + list(zip(xr, [yv] * len(xr), zrc))
                                 + [(xr[-1], yv, zlo)]]
                        gface = (col[0], col[1], col[2],
                                 float(self.raw_opacity.get()))
                        gpc = Poly3DCollection(gpoly, facecolors=[gface],
                                               edgecolors=[(0, 0, 0, 0)],
                                               linewidths=0)
                        gpc.set_clip_on(False); gpc._ridge_y = yv
                        _tag_depth(gpc, yv)
                        self.ax.add_collection3d(gpc)
                zc = np.clip(z, zlo, zhi)
                poly3d = [[(x[0], yv, zlo)]
                          + list(zip(x, [yv] * len(x), zc))
                          + [(x[-1], yv, zlo)]]
                face = (col[0], col[1], col[2], float(self.wf3d_alpha.get()))
                ec = (col[0], col[1], col[2], _ealpha) if color_lines else edgecol
                elw = lw if show_lines else 0
                pc = Poly3DCollection(poly3d, facecolors=[face],
                                      edgecolors=[ec], linewidths=elw,
                                      linestyles=[style])
                pc.set_clip_on(False)
                pc._ridge_y = yv
                _tag_depth(pc, yv)
                self.ax.add_collection3d(pc)
            self.ax.set_xlim(
                np.nanmin([xx.min() for xx, _ in ridges if len(xx)]),
                np.nanmax([xx.max() for xx, _ in ridges if len(xx)]))
            self.ax.set_ylim(min(ypos), max(ypos))
            zpad = (zhi - zlo) * 0.03 or 0.01
            self.ax.set_zlim(zlo - zpad, zhi + zpad)
        else:
            if not color_lines:
                _ecn = self.wf3d_edge_color.get()
                if _ecn == "auto":
                    _frgb = (0.85, 0.85, 0.85) if self.dark_mode.get() else (0, 0, 0)
                else:
                    from matplotlib.colors import to_rgb as _torgb
                    try:
                        _frgb = _torgb(_ecn)
                    except Exception:
                        _frgb = (0, 0, 0)
            for rank, (r, (x, z)) in enumerate(zip(shown, ridges)):
                col = self._trace_color(r, cmap_name, shown)
                ls = self._trace_ls(r)
                lcol = col if color_lines else _frgb
                ln = self.ax.plot(x, np.full_like(x, ypos[rank]),
                                  np.clip(z, zlo, zhi), color=lcol, lw=lw, ls=ls)
                for _l in ln:
                    _l.set_clip_on(False)
            zpad = (zhi - zlo) * 0.03 or 0.01
            self.ax.set_zlim(zlo - zpad, zhi + zpad)

        # faint 2D shadow projections onto the back wall and/or floor
        proj = self.wf3d_project.get()
        if proj != "Off" and ridges:
            y_back = max(ypos)
            span = (max(ypos) - min(ypos)) or 1.0
            zrange = (zhi - zlo) or 1.0
            for rank, (r, (x, z)) in enumerate(zip(shown, ridges)):
                if len(x) < 2:
                    continue
                pcol = self._trace_color(r, cmap_name, shown)
                zc = np.clip(z, zlo, zhi)
                if proj in ("Back wall", "Both"):
                    bl = self.ax.plot(x, np.full_like(x, y_back), zc,
                                      color=pcol, lw=0.8, alpha=0.25)
                    for _l in bl:
                        _l.set_clip_on(False)
                if proj in ("Floor", "Both"):
                    yfloor = ypos[rank] + (zc - zlo) / zrange * (span * 0.12)
                    fl = self.ax.plot(x, yfloor, np.full_like(x, zlo),
                                      color=pcol, lw=0.8, alpha=0.22)
                    for _l in fl:
                        _l.set_clip_on(False)

        # label the pressure axis with the real GPa values
        if even:
            self.ax.set_yticks(list(ypos))
            self.ax.set_yticklabels(["%.1f" % p for p in pvals])
        elif n <= 16:
            self.ax.set_yticks(list(pvals))
            self.ax.set_yticklabels(["%.1f" % p for p in pvals])

        # 3D axis-limit overrides (X=wavelength, Y=pressure) when Auto is off
        if not self.autoscale.get():
            xa, xb = fv(self.xmin), fv(self.xmax)
            if xa is not None and xb is not None:
                self.ax.set_xlim(xa, xb)
            if not even:
                ya, yb = fv(self.ymin), fv(self.ymax)
                if ya is not None and yb is not None:
                    self.ax.set_ylim(ya, yb)

        bg, fg = self._mpl_colors()
        acol, tcol = self._axis_text_colors(fg)
        self.fig.set_facecolor(bg)
        self.ax.set_facecolor(bg)
        try:
            self._draw_wf3d_guides(unit, zlog, tcol)
        except Exception:
            pass
        self.ax.view_init(elev=float(self.wf3d_elev.get()),
                          azim=float(self.wf3d_azim.get()))
        self._autofill_labels(unit_label(unit),
                              "Absorbance" if chan == "absorbance" else "Counts")
        def _lp(var, d):
            try:
                return float(var.get())
            except Exception:
                return d
        zdef = "Absorbance" if chan == "absorbance" else "Counts"
        self._autofill_labels(unit_label(unit), "Pressure (GPa)", zdef)
        lkw = {}
        if getattr(self, "xlabel_loc", None) is not None and                 self.xlabel_loc.get() in ("left", "center", "right"):
            lkw["loc"] = self.xlabel_loc.get()
        try:
            self.ax.set_xlabel(self.xlabel_v.get() or unit_label(unit),
                               fontsize=int(self.label_fs.get()), color=tcol,
                               labelpad=_lp(self.lblpad3d_x, 15.0), **lkw)
        except Exception:
            self.ax.set_xlabel(self.xlabel_v.get() or unit_label(unit),
                               fontsize=int(self.label_fs.get()), color=tcol,
                               labelpad=_lp(self.lblpad3d_x, 15.0))
        self.ax.set_ylabel(self.ylabel_v.get() or "Pressure (GPa)",
                          fontsize=int(self.label_fs.get()),
                          color=tcol, labelpad=_lp(self.lblpad3d_y, 15.0))
        self.ax.set_zlabel(self.zlabel_v.get() or zdef,
                          fontsize=int(self.label_fs.get()), color=tcol,
                          labelpad=_lp(self.lblpad3d_z, 10.0))
        self.ax.tick_params(labelsize=int(self.tick_fs.get()), colors=tcol)
        # 3D axis lines (best-effort) follow the axis color
        try:
            for _axis in (self.ax.xaxis, self.ax.yaxis, self.ax.zaxis):
                _axis.line.set_color(acol)
        except Exception:
            pass
        self._apply_axis_ticks(self.ax, is3d=True)
        if zlog:
            from matplotlib.ticker import FuncFormatter, MaxNLocator
            self.ax.zaxis.set_major_locator(MaxNLocator(integer=True))
            self.ax.zaxis.set_major_formatter(
                FuncFormatter(lambda v, _p: r"$10^{%d}$" % int(round(v))))
        if self.title_v.get():
            tkw3 = {}
            if getattr(self, "title_loc", None) is not None and                     self.title_loc.get() in ("left", "center", "right"):
                tkw3["loc"] = self.title_loc.get()
            try:
                tkw3["pad"] = float(self.title_pad.get())
            except (ValueError, tk.TclError):
                pass
            self.ax.set_title(self.title_v.get(),
                              fontsize=int(self.title_fs.get()), color=tcol,
                              **tkw3)

        if self.wf3d_clean.get():
            pane_bg = "#2b2e36" if self.dark_mode.get() else "white"
            pane_edge = "#4a4f59" if self.dark_mode.get() else "#cccccc"
            for pane in (self.ax.xaxis, self.ax.yaxis, self.ax.zaxis):
                pane.pane.set_facecolor(pane_bg)
                pane.pane.set_edgecolor(pane_edge)
                pane.pane.set_alpha(1.0)
        self._apply_grid(self.ax, is3d=True)
        # box frame: mpl3d omits edges depending on the view (Nhan's
        # missing-left-border bug). Draw them explicitly per the Box frame
        # mode: 'open front' skips the three edges meeting at the corner
        # nearest the viewer so nothing sits between you and the data.
        try:
            fmode = (self.wf3d_frame.get()
                     if getattr(self, "wf3d_frame", None) is not None
                     else "open front")
            if fmode != "none":
                import itertools
                import math as _m
                (bx0, bx1) = self.ax.get_xlim()
                (by0, by1) = self.ax.get_ylim()
                (bz0, bz1) = self.ax.get_zlim()
                try:
                    slw = float(self.spine_lw.get())
                except (ValueError, tk.TclError):
                    slw = 0.8
                az = _m.radians(float(self.wf3d_azim.get()))
                near = (bx1 if _m.cos(az) >= 0 else bx0,
                        by1 if _m.sin(az) >= 0 else by0,
                        bz1)
                corners = [(cx, cy, cz) for cx in (bx0, bx1)
                           for cy in (by0, by1) for cz in (bz0, bz1)]
                for a, b in itertools.combinations(corners, 2):
                    if sum(pa != pb for pa, pb in zip(a, b)) != 1:
                        continue
                    if fmode == "open front" and near in (a, b):
                        continue
                    self.ax.plot3D([a[0], b[0]], [a[1], b[1]],
                                   [a[2], b[2]], color=acol, lw=slw,
                                   zorder=1e4)
        except Exception:
            pass
        yaspect = 1.2
        if even:
            yaspect = 1.2 * min(max(wf_step / 0.2, 0.33), 3.0)
        sx = float(self.wf3d_sx.get()); sy = float(self.wf3d_sy.get())
        sz = float(self.wf3d_sz.get())
        try:
            self.ax.set_box_aspect((1.7 * sx, yaspect * sy, 0.6 * sz),
                                   zoom=float(self.wf3d_zoom.get()))
        except TypeError:
            try:
                self.ax.set_box_aspect((1.7 * sx, yaspect * sy, 0.6 * sz))
            except Exception:
                pass
        except Exception:
            pass

        # Flip X in 3D: invert the wavelength axis after limits are set
        if self.flipx.get():
            try:
                self.ax.invert_xaxis()
            except Exception:
                pass

        # legend / colorbar work in 3D too (respect the toggles)
        if self.colorbar_on.get() and not colormaps.is_categorical(cmap_name):
            from matplotlib.cm import ScalarMappable
            from matplotlib.colors import Normalize
            sm = ScalarMappable(norm=Normalize(pmin, pmax),
                                cmap=colormaps._continuous_cmap(cmap_name))
            sm.set_array([])
            cb = self.fig.colorbar(sm, ax=self.ax, shrink=0.6, pad=0.08,
                                   **self._cbar_kwargs())
            self._style_colorbar(cb)
        elif self.legend_on.get():
            import matplotlib.patches as mpatches
            entries = []
            for rank, r in enumerate(shown):
                col = self._trace_color(r, cmap_name, shown)
                entries.append((mpatches.Patch(facecolor=col, edgecolor="none"),
                                r["pressure_val"], self._branch_of(r), r))
            h, l = self._ordered_legend(entries)
            h = self._legend_handles(h)
            loc = self.legend_loc.get()
            kw = {"fontsize": int(self.legend_fs.get()),
                  "ncol": int(self.legend_cols.get())}
            ttl = self.legend_title.get().strip()
            if ttl:
                kw["title"] = ttl
                kw["title_fontsize"] = int(self.legend_title_fs.get())
            if loc == "outside right":
                leg = self.ax.legend(h, l, bbox_to_anchor=(1.02, 1),
                                     loc="upper left", **kw)
            else:
                leg = self.ax.legend(h, l, loc=loc, **kw)
            self._style_legend(leg)

    # ---- inspect one pressure (S/B/D counts + Abs on right axis) ---------
    def _draw_wf3d_guides(self, unit, zlog, fallback_color):
        """Render the 2D reference markers as translucent planes in the 3D box:
        each vertical (spectral) marker becomes an X-plane spanning the pressure
        and absorbance axes; each horizontal (absorbance) marker becomes a
        Z-plane spanning the spectral and pressure axes. Color, style, width and
        opacity reuse the existing marker controls; honors the log-Z transform.
        Vertical markers outside the spectral range are skipped."""
        from mpl_toolkits.mplot3d.art3d import Poly3DCollection as _P3D
        from matplotlib.colors import to_rgb as _torgb
        gx0, gx1 = self.ax.get_xlim()
        gy0, gy1 = self.ax.get_ylim()
        gz0, gz1 = self.ax.get_zlim()

        def _rgb(name):
            try:
                return _torgb(fallback_color if name == "auto" else name)
            except Exception:
                return _torgb(fallback_color)

        def _flt(var, d):
            try:
                return float(var.get())
            except (ValueError, tk.TclError):
                return d

        def _plane(quad, name, a, ls, lw):
            c = _rgb(name)
            pc = _P3D([quad], facecolors=[(c[0], c[1], c[2], 0.15 * a)],
                      edgecolors=[(c[0], c[1], c[2], a)],
                      linewidths=lw, linestyles=[ls])
            pc.set_clip_on(False)
            self.ax.add_collection3d(pc)

        lo, hi = (gx0, gx1) if gx0 <= gx1 else (gx1, gx0)
        vls = self._dash_of(self.vmark_style.get())
        vlw = _flt(self.vmark_width, 1.0); va = _flt(self.vmark_alpha, 1.0)
        for xp in self._marker_positions(unit):
            if lo <= xp <= hi:
                _plane([(xp, gy0, gz0), (xp, gy1, gz0),
                        (xp, gy1, gz1), (xp, gy0, gz1)],
                       self.vmark_color.get(), va, vls, vlw)

        hls = self._dash_of(self.hmark_style.get())
        hlw = _flt(self.hmark_width, 1.0); ha = _flt(self.hmark_alpha, 1.0)
        for yp in self._hmarker_positions():
            if zlog:
                if yp <= 0:
                    continue
                zp = float(np.log10(yp))
            else:
                zp = yp
            _plane([(gx0, gy0, zp), (gx1, gy0, zp),
                    (gx1, gy1, zp), (gx0, gy1, zp)],
                   self.hmark_color.get(), ha, hls, hlw)

        self.ax.set_xlim(gx0, gx1)
        self.ax.set_ylim(gy0, gy1)
        self.ax.set_zlim(gz0, gz1)

    def _draw_inspect(self, unit, lw):
        r = next((x for x in self.results
                  if x["label"] == self.inspect_p.get()), None)
        if r is None:
            return
        x = unit_x(r, unit)
        if self.ins_S.get():
            self.ax.plot(x, r["samp_c"], color="#1f77b4", lw=lw, label="Sample")
        if self.ins_B.get():
            self.ax.plot(x, r["bg_c"], color="#2ca02c", lw=lw, label="Background")
        if self.ins_D.get():
            self.ax.plot(x, r["dark_c"], color="#555555", lw=lw, label="Dark")
        h1, l1 = self.ax.get_legend_handles_labels()
        if self.ins_A.get():
            ax2 = self.ax.twinx()
            ax2.plot(x, self._abs(r), color="#d62728", lw=lw, label="Absorbance")
            ax2.set_ylabel("Absorbance")
            # theme the twin axis: _cosmetics_2d styles only self.ax, so ax2
            # would keep matplotlib-default black text/spines in dark themes
            _fg = self._mpl_colors()[1]
            _acol, _tcol = self._axis_text_colors(_fg)
            ax2.yaxis.label.set_color(_tcol)
            ax2.yaxis.label.set_size(int(self.label_fs.get()))
            ax2.tick_params(colors=_tcol, labelsize=int(self.tick_fs.get()))
            for _sp in ax2.spines.values():
                _sp.set_color(_acol)
            h2, l2 = ax2.get_legend_handles_labels()
            if self.legend_on.get():
                self.ax.legend(h1 + h2, l1 + l2, fontsize=8, loc="best")
        elif self.legend_on.get():
            self.ax.legend(fontsize=8, loc="best")
        if not self.title_v.get():
            self.ax.set_title(self.inspect_p.get() + "  (channels)")


    # ---- smoothing settings (resizable + scrollable; Apply pinned) -------
    def _build_smooth_explainer(self, win):
        """Right-side reference panel: what each filter does, its parameters,
        and the gist of the math. Applied in the order listed."""
        wrap = ttk.Frame(win, padding=(8, 6))
        wrap.pack(side="right", fill="both", expand=True)
        self._lbl(wrap, text="How the 5-step smoother works",
                  font=(UI_FONT, 10, "bold")).pack(anchor="w")
        self._lbl(wrap, text="Filters run top to bottom on raw absorbance. "
                  "Removed points become gaps (NaN), not zeros.",
                  wraplength=300, foreground="#777").pack(anchor="w", pady=(0, 4))
        txtf = ttk.Frame(wrap); txtf.pack(fill="both", expand=True)
        tsb = ttk.Scrollbar(txtf)
        tsb.pack(side="right", fill="y")
        txt = tk.Text(txtf, width=44, wrap="word", yscrollcommand=tsb.set,
                      relief="flat", padx=6, pady=4, font=(UI_FONT, 10),
                      background="#f7f7f7" if not self.dark_mode.get() else "#23252b",
                      foreground="#222" if not self.dark_mode.get() else "#ddd")
        txt.pack(side="left", fill="both", expand=True)
        tsb.config(command=txt.yview)
        txt.tag_configure("h", font=(UI_FONT, 10, "bold"),
                          spacing1=8, spacing3=2)
        txt.tag_configure("m", font=("Consolas", 9), foreground="#1a6")
        secs = [
            ("1. Saturation cutoff",
             "Detector readings above a ceiling are unreliable (the path is "
             "opaque, the signal is in the noise). Any point with A greater "
             "than the cutoff is dropped.\n",
             "drop where  A > Max absorbance\n",
             "Max absorbance - the A ceiling (e.g. 4.0). Higher keeps more.\n"),
            ("2. Density filter",
             "Removes sparse stretches where too few real points survive. Slides "
             "a window along the spectrum and blanks the window if it is mostly "
             "gaps already.\n",
             "in each Window: if (#finite) < Min valid -> blank window\n",
             "Window (pts) - span checked at once.\n"
             "Min valid pts - keep only if at least this many are real.\n"),
            ("3. Hampel despike",
             "Kills isolated spikes (cosmic rays, single-pixel glitches) without "
             "rounding real peaks. Compares each point to the local median and "
             "the median absolute deviation (MAD) around it.\n",
             "replace where |A - med| > Sigma * 1.4826 * MAD\n",
             "Window (pts) - half-width of the local median.\n"
             "Sigma threshold - how many robust SDs counts as a spike "
             "(lower = more aggressive).\n"),
            ("4. Savitzky-Golay (split)",
             "The main smoother: fits a low-order polynomial to a sliding window "
             "by least squares and keeps the fitted center point, preserving "
             "peak shape better than a moving average. The spectrum is split at "
             "a wavelength so each side gets its own window/order (e.g. a noisier "
             "region can be smoothed harder).\n",
             "y_i = polyfit(window, order) evaluated at i\n",
             "Split at (nm) - boundary between the two regimes.\n"
             "Left/Right window - points per fit (odd; bigger = smoother).\n"
             "Left/Right poly - polynomial order (2-3 typical).\n"),
            ("5. Jump filter",
             "Final cleanup for step discontinuities left where segments meet or "
             "a filter blanked a run. If A steps by more than a threshold across "
             "a short distance, the jump and a small buffer around it are "
             "removed.\n",
             "drop where |dA| > Max jump within Step dist (+/- Buffer)\n",
             "Max jump (abs) - step size that counts as a break.\n"
             "Step dist (pts) - distance over which to measure it.\n"
             "Buffer (pts) - extra margin trimmed each side.\n"),
        ]
        for head, desc, math, params in secs:
            txt.insert("end", head + "\n", "h")
            txt.insert("end", desc)
            txt.insert("end", math, "m")
            txt.insert("end", params)
        txt.insert("end", "\nTip: edit values on the left, then Apply. Use the "
                   "Reset button in the Smoothing box to return to defaults.")
        txt.configure(state="disabled")

    def _open_smooth_panel(self):
        win = tk.Toplevel(self.root)
        win.title("Smoothing settings (Igor 5-step)")
        win.geometry("820x640"); win.minsize(560, 380)
        p = self.smooth_params
        orig = dict(self.smooth_params)        # snapshot for Cancel / live revert
        vars_ = {}
        bottom = ttk.Frame(win, padding=6); bottom.pack(side="bottom", fill="x")
        # right-side explainer panel (what each filter does + the math)
        self._build_smooth_explainer(win)
        left = ttk.Frame(win); left.pack(side="left", fill="y")
        canv = tk.Canvas(left, highlightthickness=0, width=380)
        sb = tk.Scrollbar(left, orient="vertical", command=canv.yview, width=14)
        body = ttk.Frame(canv)
        body.bind("<Configure>",
                  lambda e: canv.configure(scrollregion=canv.bbox("all")))
        _bwin = canv.create_window((0, 0), window=body, anchor="nw")
        canv.bind("<Configure>", lambda e: canv.itemconfig(_bwin, width=e.width))
        canv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y"); canv.pack(side="left", fill="both",
                                                   expand=True)

        def chk(parent, key, text):
            v = tk.BooleanVar(value=bool(p[key])); vars_[key] = v
            ttk.Checkbutton(parent, text=text, variable=v).pack(anchor="w")

        def num(parent, key, text):
            row = ttk.Frame(parent); row.pack(fill="x", pady=1)
            self._lbl(row, text=text, width=20).pack(side="left")
            v = tk.StringVar(value=str(p[key])); vars_[key] = v
            ttk.Entry(row, textvariable=v, width=10).pack(side="left")

        groups = [
            ("1. Saturation cutoff", [("chk", "cutoff_on", "Enable"),
                                      ("num", "cutoff_val", "Max absorbance")]),
            ("2. Density filter", [("chk", "density_on", "Enable"),
                                   ("num", "density_win", "Window (pts)"),
                                   ("num", "density_min", "Min valid pts")]),
            ("3. Hampel despike", [("chk", "hampel_on", "Enable"),
                                   ("num", "hampel_win", "Window (pts)"),
                                   ("num", "hampel_sig", "Sigma threshold")]),
            ("4. Savitzky-Golay (split)", [("num", "split_nm", "Split at (nm)"),
                                           ("num", "left_win", "Left window"),
                                           ("num", "left_poly", "Left poly"),
                                           ("num", "right_win", "Right window"),
                                           ("num", "right_poly", "Right poly")]),
            ("5. Jump filter", [("chk", "jump_on", "Enable"),
                                ("num", "jump_thresh", "Max jump (abs)"),
                                ("num", "jump_step", "Step dist (pts)"),
                                ("num", "jump_buff", "Buffer (pts)")]),
        ]
        for title, fields in groups:
            fr = ttk.LabelFrame(body, text=title, padding=6)
            fr.pack(fill="x", padx=8, pady=4)
            for kind, key, text in fields:
                (chk if kind == "chk" else num)(fr, key, text)

        ints = {"density_win", "density_min", "hampel_win", "left_win",
                "left_poly", "right_win", "right_poly", "jump_step", "jump_buff"}

        def _parse_into(target):
            for k, v in vars_.items():
                if isinstance(v, tk.BooleanVar):
                    target[k] = v.get()
                else:
                    val = float(v.get())
                    target[k] = int(val) if k in ints else val

        live = tk.BooleanVar(value=False)

        def _live(*a):
            if not live.get():
                return
            try:
                _parse_into(self.smooth_params)
            except ValueError:
                return
            self.smooth_cache.clear(); self.show_smooth.set(True); self._redraw()

        for _v in vars_.values():
            _v.trace_add("write", _live)

        def apply():
            try:
                _parse_into(self.smooth_params)
            except ValueError:
                messagebox.showerror("Smoothing", "Numeric fields must be numbers.")
                return
            self.smooth_cache.clear(); self.show_smooth.set(True)
            self._redraw(); win.destroy()

        def cancel():
            self.smooth_params.clear(); self.smooth_params.update(orig)
            self.smooth_cache.clear(); self._redraw(); win.destroy()

        lpc = ttk.Checkbutton(bottom, text="Live preview", variable=live,
                              command=_live)
        lpc.pack(side="left")
        Tooltip(lpc, "Apply each change to the plot as you edit, without closing. "
                     "Cancel reverts to the settings from when you opened this "
                     "window.")
        ttk.Button(bottom, text="Apply", command=apply).pack(side="right")
        ttk.Button(bottom, text="Cancel", command=cancel).pack(side="right",
                                                               padx=6)
        win.protocol("WM_DELETE_WINDOW", cancel)

    # ---- presets ----------------------------------------------------------
    def _preset_registry(self):
        return {
            "mode": self.mode, "ydata": self.ydata, "xunit": self.xunit,
            "flipx": self.flipx, "flipy": self.flipy, "topaxis": self.topaxis,
            "zlabel_v": self.zlabel_v, "title_loc": self.title_loc,
            "title_pad": self.title_pad, "xlabel_loc": self.xlabel_loc,
            "ylabel_loc": self.ylabel_loc,
            "spine_lw": self.spine_lw, "xlabelpad": self.xlabelpad,
            "ylabelpad": self.ylabelpad,
            "autoscale": self.autoscale, "xmin": self.xmin, "xmax": self.xmax,
            "ymin": self.ymin, "ymax": self.ymax, "cmap": self.cmap,
            "cmap_rev": self.cmap_rev, "grid_on": self.grid_on,
            "grid_minor_on": self.grid_minor_on, "grid_width": self.grid_width,
            "grid_alpha": self.grid_alpha, "grid_color": self.grid_color,
            "grid_style": self.grid_style,
            "grid_minor_color": self.grid_minor_color,
            "grid_minor_width": self.grid_minor_width,
            "grid_minor_alpha": self.grid_minor_alpha,
            "grid_minor_style": self.grid_minor_style,
            "lw": self.lw,
            "wf_mode": self.wf_mode, "wf_step": self.wf_step,
            "wf_label": self.wf_label, "show_smooth": self.show_smooth,
            "show_notch": self.show_notch, "notch_width": self.notch_width,
            "notch_nt_min": self.notch_nt_min, "notch_nt_max": self.notch_nt_max,
            "notch_pmax": self.notch_pmax,
            "raw_opacity": self.raw_opacity, "markers": self.markers,
            "title": self.title_v, "xlabel": self.xlabel_v, "ylabel": self.ylabel_v,
            "legend_on": self.legend_on, "colorbar_on": self.colorbar_on,
            "legend_loc": self.legend_loc, "legend_cols": self.legend_cols,
            "auto_key": self.auto_key, "legend_swatch": self.legend_swatch,
            "zoom2d_axis": self.zoom2d_axis,
            "legend_border": self.legend_border, "legend_alpha": self.legend_alpha,
            "legend_bw": self.legend_bw, "legend_edge": self.legend_edge,
            "legend_fs": self.legend_fs,
            "legend_title": self.legend_title,
            "legend_title_fs": self.legend_title_fs,
            "cbar_label": self.cbar_label,
            "cbar_label_fs": self.cbar_label_fs, "cbar_tick_fs": self.cbar_tick_fs,
            "cbar_orient": self.cbar_orient, "cbar_width": self.cbar_width,
            "cbar_nticks": self.cbar_nticks, "no_raw_bg": self.no_raw_bg,
            "perf_mode": self.perf_mode, "reduce_motion": self.reduce_motion,
            "font_family": self.font_family, "font_size": self.font_size,
            "dpi": self.dpi, "ins_S": self.ins_S, "ins_B": self.ins_B,
            "ins_D": self.ins_D, "ins_A": self.ins_A,
            "wf3d_even": self.wf3d_even, "wf3d_fill": self.wf3d_fill,
            "wf3d_clip99": self.wf3d_clip99,
            "wf3d_frame": self.wf3d_frame,
            "wf3d_clean": self.wf3d_clean,
            "wf3d_look": self.wf3d_look, "wf3d_color_traces": self.wf3d_color_traces,
            "wf3d_lw": self.wf3d_lw, "wf3d_project": self.wf3d_project,
            "lblpad3d_x": self.lblpad3d_x, "lblpad3d_y": self.lblpad3d_y,
            "lblpad3d_z": self.lblpad3d_z,
            "wf3d_sx": self.wf3d_sx, "wf3d_sy": self.wf3d_sy, "wf3d_sz": self.wf3d_sz,
            "wf3d_elev": self.wf3d_elev, "wf3d_azim": self.wf3d_azim,
            "wf3d_alpha": self.wf3d_alpha, "wf3d_zoom": self.wf3d_zoom,
            "aspect_mode": self.aspect_mode, "aspect_w": self.aspect_w,
            "aspect_h": self.aspect_h,
            "zmin": self.zmin, "zmax": self.zmax,
            "fig_w": self.fig_w, "fig_h": self.fig_h, "fig_preset": self.fig_preset,
            "title_fs": self.title_fs, "label_fs": self.label_fs,
            "tick_fs": self.tick_fs, "hide_spines": self.hide_spines,
            "minor_ticks": self.minor_ticks, "tick_dir": self.tick_dir,
            "xmaj": self.xmaj, "xminor": self.xminor, "ymaj": self.ymaj,
            "yminor": self.yminor, "zmaj": self.zmaj, "zminor": self.zminor,
            "ticks_allsides": self.ticks_allsides,
            "tick_len_major": self.tick_len_major,
            "tick_len_minor": self.tick_len_minor, "tick_width": self.tick_width,
            "wf3d_edge_color": self.wf3d_edge_color,
            "wf3d_edge_alpha": self.wf3d_edge_alpha,
            "wf3d_detail": self.wf3d_detail,
            "wf3d_zlog": self.wf3d_zlog,
            "axis_color": self.axis_color, "text_color": self.text_color,
            "rightaxis": self.rightaxis, "marker_interval": self.marker_interval,
            "hmarker_interval": self.hmarker_interval,
            "hmarkers": self.hmarkers,
            "vmark_color": self.vmark_color, "vmark_style": self.vmark_style,
            "vmark_width": self.vmark_width, "hmark_color": self.hmark_color,
            "hmark_style": self.hmark_style, "hmark_width": self.hmark_width,
            "vmark_alpha": self.vmark_alpha, "hmark_alpha": self.hmark_alpha,
            "fig_transparent": self.fig_transparent, "fig_tight": self.fig_tight,
            "fig_pad": self.fig_pad, "fig_facecolor": self.fig_facecolor,
        }

    def _starter_presets(self):
        """Built-in, read-only presets so the dropdown is useful out of the box.
        Each holds only the keys it wants to set; everything else is untouched."""
        return {
            "Publication 2D": {
                "mode": "overlay", "wf_mode": "off", "cmap": "batlow",
                "cmap_rev": False, "show_smooth": True, "raw_opacity": 0.15,
                "lw": 1.0, "grid_on": False, "grid_minor_on": False,
                "legend_on": True, "colorbar_on": False, "legend_border": True,
                "legend_bw": 0.6, "legend_alpha": 0.9, "legend_edge": "auto"},
            "Vivid 3D": {
                "wf_mode": "3D ridge", "cmap": "plasma", "cmap_rev": False,
                "wf3d_fill": True, "wf3d_clean": True, "wf3d_alpha": 0.6,
                "wf3d_even": True, "show_smooth": True, "raw_opacity": 0.25,
                "colorbar_on": True, "legend_on": False, "grid_on": False},
            "Inspect raw": {
                "mode": "overlay", "wf_mode": "off", "show_smooth": False,
                "raw_opacity": 1.0, "cmap": "viridis", "lw": 1.0,
                "grid_on": True, "grid_minor_on": False, "grid_alpha": 0.35,
                "grid_width": 0.6, "grid_color": "auto", "legend_on": True,
                "colorbar_on": False},
            "Colorblind-safe": {
                "wf_mode": "2D stacked", "cmap": "batlow", "cmap_rev": False,
                "show_smooth": True, "raw_opacity": 0.2, "wf_step": 0.2,
                "wf_label": True, "legend_on": True, "colorbar_on": False,
                "grid_on": False},
            "Journal figure": {
                "font_family": "DejaVu Serif", "grid_on": False,
                "grid_minor_on": False, "hide_spines": True,
                "tick_dir": "in", "minor_ticks": True,
                "title_fs": 13, "label_fs": 12, "tick_fs": 10,
                "legend_on": True, "legend_border": True, "legend_bw": 0.6,
                "fig_tight": True},
            "Poster (large fonts)": {
                "font_family": "DejaVu Sans", "title_fs": 20, "label_fs": 17,
                "tick_fs": 14, "legend_fs": 14, "lw": 1.6, "hide_spines": True,
                "grid_on": False, "tick_dir": "out", "minor_ticks": True,
                "cbar_label_fs": 15, "cbar_tick_fs": 12}
        }

    def _refresh_presets(self):
        starters = ["\u2605 " + n for n in self._starter_presets().keys()]
        names = starters + sorted(self.settings.get("presets", {}).keys())
        self.preset_cb.configure(values=names)
        if names and not self.preset_sel.get():
            self.preset_sel.set(names[0])

    def _apply_preset_data(self, data):
        reg = self._preset_registry()
        for k, v in reg.items():
            if k in data:
                try:
                    v.set(data[k])
                except Exception:
                    pass
        if "_smooth" in data:
            self.smooth_params.update(data["_smooth"]); self.smooth_cache.clear()
        self._redraw()

    def _save_named_preset(self):
        name = simpledialog.askstring("Save preset", "Preset name:",
                                      parent=self.root)
        if not name:
            return
        data = {k: v.get() for k, v in self._preset_registry().items()}
        data["_smooth"] = dict(self.smooth_params)
        self.settings.setdefault("presets", {})[name] = data
        self._save_settings()
        self._refresh_presets()
        self.preset_sel.set(name)
        self._logline("Saved preset '%s'" % name)

    def _load_named_preset(self):
        name = self.preset_sel.get()
        if name.startswith("\u2605 "):
            base = name[2:]
            data = self._starter_presets().get(base)
            if not data:
                messagebox.showinfo("Presets", "Unknown built-in preset."); return
            self._apply_preset_data(data)
            self._logline("Loaded built-in preset '%s'" % base)
            return
        data = self.settings.get("presets", {}).get(name)
        if not data:
            messagebox.showinfo("Presets", "Pick a saved preset first."); return
        self._apply_preset_data(data)
        self._logline("Loaded preset '%s'" % name)

    def _delete_named_preset(self):
        name = self.preset_sel.get()
        if name.startswith("\u2605 "):
            messagebox.showinfo("Presets",
                                "Built-in presets can't be deleted."); return
        presets = self.settings.get("presets", {})
        if name in presets and messagebox.askyesno(
                "Delete preset", "Delete preset '%s'?" % name):
            del presets[name]; self._save_settings()
            self.preset_sel.set(""); self._refresh_presets()

    def _save_session(self):
        path = filedialog.asksaveasfilename(
            title="Save session", defaultextension=".json",
            filetypes=[("Beamline session", "*.json")])
        if not path:
            return
        data = {k: v.get() for k, v in self._preset_registry().items()}
        data["_smooth"] = dict(self.smooth_params)
        data["_session"] = {"in_dir": self.in_var.get(),
                            "out_dir": self.out_var.get(),
                            "theme": self.theme_mode.get()}
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            self._logline("Saved session -> " + path)
        except Exception as e:
            self._warn("Save session", "Could not save the session:\n%s" % e)

    def _load_session(self):
        path = filedialog.askopenfilename(
            title="Load session",
            filetypes=[("Beamline session", "*.json"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            self._warn("Load session", "Could not read the session:\n%s" % e)
            return
        sess = data.pop("_session", {}) or {}
        if sess.get("theme"):
            self.theme_mode.set(sess["theme"])
        self._apply_preset_data(data)
        if sess.get("in_dir"):
            self.in_var.set(sess["in_dir"])
        if sess.get("out_dir"):
            self.out_var.set(sess["out_dir"])
        self._logline("Loaded session <- " + path)
        self._logline("  (click Run to re-process the input folder)")

    def _about(self):
        win = tk.Toplevel(self.root)
        win.title("About / Help"); win.geometry("600x600")
        self._apply_titlebar(win)
        hdr = ttk.Frame(win, padding=(12, 10, 12, 4)); hdr.pack(fill="x")
        ic = getattr(self, "_icons", {})
        if ic.get("mark_lg") is not None:
            tk.Label(hdr, image=ic["mark_lg"], bd=0).pack(side="left",
                                                          padx=(0, 12))
        hcol = ttk.Frame(hdr); hcol.pack(side="left")
        wrow = ttk.Frame(hcol); wrow.pack(anchor="w")
        self._lbl(wrow, text=BRAND["wordmark"],
                  font=(UI_FONT_SEMI, 16)).pack(side="left")
        self._lbl(wrow, text=BRAND["dot"], font=(UI_FONT_SEMI, 16, "bold"),
                  foreground=self._brand()["ac2"]).pack(side="left")
        self._lbl(hcol, text=BRAND["expansion"],
                  font=(UI_FONT, 10), foreground="#888").pack(anchor="w")
        self._lbl(hcol, text="%s  -  NSLS-II 22-IR-1  -  Dr. Lee's Lab"
                  % APP_VERSION, font=(UI_FONT, 10),
                  foreground="#888").pack(anchor="w")
        self._lbl(hcol, text="Nhan Q. Ta  -  FFT defringe by Matthew Diamond",
                  font=(UI_FONT, 10), foreground="#888").pack(anchor="w")
        ghb = ttk.Button(hdr, text="GitHub", command=lambda: __import__(
            "webbrowser").open(
            "https://github.com/NoisySnooper/Beamline-DAC-Data-Tool"))
        ghb.pack(side="right")
        Tooltip(ghb, "Open the project repository in your browser.")
        strip = tk.Canvas(win, height=4, highlightthickness=0, bd=0)
        strip.pack(fill="x", padx=12, pady=(2, 6))

        def _paint_strip(_e=None):
            strip.delete("all")
            w = max(strip.winfo_width(), 1)
            cols = ["#02154F", "#14505E", "#587B41", "#B08A3E",
                    "#E89A70", "#FBC5C0"]
            seg = w / float(len(cols))
            for i, c in enumerate(cols):
                strip.create_rectangle(i * seg, 0, (i + 1) * seg, 4,
                                       fill=c, outline=c)
        strip.bind("<Configure>", _paint_strip)
        txt = tk.Text(win, wrap="word", font=(UI_FONT, 10),
                      padx=10, pady=10)
        txt.pack(fill="both", expand=True)
        body = (
            "%s  (%s)\n" % (BRAND["name"], BRAND["org"])
            + APP_VERSION + "\n\n"
            "Built for Dr. Lee's lab to concatenate DAC absorption segments, "
            "compute absorbance, and plot (2D overlay / stacked, 3D ridge).\n\n"
            "ABSORBANCE\n"
            "  A = -log10[(Sample - Dark) / (Background - Dark)]\n\n"
            "X-AXIS UNITS\n"
            "  wavenumber [cm^-1] = 1e7 / wavelength[nm]\n"
            "  photon energy [eV] = 1239.84 / wavelength[nm]\n\n"
            "FILENAME FORMAT\n"
            "  vis_{DAC}_{Sample}[_{Pressure}][_bg|_s][_C|_D][_2|_3][.{seq}]\n"
            "  no suffix = dark, _bg = background, _s = sample\n"
            "  _C / _D = compression / decompression (auto-detected)\n"
            "  Pressure uses 'p' for the decimal: 1p39 = 1.39 GPa; 0 GPa is\n"
            "  allowed and a missing pressure field is assumed 0 GPa\n"
            "  .{seq} = grating segment; omit it for single-stitch files\n"
            "  Incomplete channel sets (e.g. bg only) load as raw counts\n\n"
            "TIPS\n"
            "  - Move the Waterfall panel controls for 2D stacked / 3D ridge.\n"
            "  - Offset/step sets 3D ridge spacing when Even rank spacing is on.\n"
            "  - Top/right axis in wavenumber or energy needs X min > 0.\n"
            "  - Save / Load session keeps folders + every plotting setting.\n"
            "  - [ and ] cycle the colormap; right-click a slider resets it.\n")
        txt.insert("1.0", body); txt.configure(state="disabled")
        ttk.Button(win, text="Close", command=win.destroy).pack(pady=(0, 8))

    def _peak_readout(self):
        if not self.results:
            self._warn("Readout", "Load data first (pick folders and Run).")
            return
        wl = simpledialog.askfloat("Absorbance readout", "Wavelength (nm):",
                                   parent=self.root, minvalue=0.0)
        if wl is not None:
            self._show_readout_table(wl)

    def _on_plot_click(self, ev):
        """Click the 2D plot to read absorbance across pressures at that x."""
        if not getattr(self, "_click_readout", None) or not self._click_readout.get():
            return
        if ev.inaxes is None or ev.button != 1 or not self.results:
            return
        if self.wf_mode.get() == "3D ridge" or self.mode.get() == "inspect":
            return
        xv = ev.xdata
        if xv is None:
            return
        unit = self.xunit.get()
        try:
            wl = 1e7 / xv if unit == "wn" else (EV_NM / xv if unit == "ev" else xv)
        except ZeroDivisionError:
            return
        if wl and wl > 0:
            self._show_readout_table(wl)

    def _show_readout_table(self, wl):
        if not self.results:
            return
        shown = self._shown() or self.results
        win = tk.Toplevel(self.root)
        win.title("Absorbance at %.1f nm" % wl); win.geometry("340x440")
        cols = ("p", "a")
        tv = ttk.Treeview(win, columns=cols, show="headings")
        tv.heading("p", text="Pressure (GPa)")
        tv.heading("a", text="Absorbance")
        tv.column("p", width=150, anchor="center")
        tv.column("a", width=150, anchor="center")
        tv.pack(fill="both", expand=True, padx=6, pady=6)
        use_sm = self.show_smooth.get()
        for r in sorted(shown, key=lambda rr: rr["pressure_val"]):
            wla = np.asarray(r["wl"])
            ya = self._smoothed(r) if use_sm else self._abs(r)
            idx = int(np.argmin(np.abs(wla - wl)))
            # argmin clamps to the edge pixel for out-of-range requests,
            # which would report a misleading value; show "-" instead
            v = float(ya[idx]) if abs(float(wla[idx]) - wl) <= 5.0 else np.nan
            tv.insert("", "end", values=("%.2f" % r["pressure_val"],
                                         ("%.4f" % v) if np.isfinite(v)
                                         else "-"))
        ttk.Button(win, text="Close", command=win.destroy).pack(pady=(0, 6))

    def _nuke(self):
        if not messagebox.askyesno(
                "NUKE: reset everything",
                "This clears all loaded data, the plot, the input/output "
                "folders, the log, and resets every control to its default.\n\n"
                "Your saved presets and default colormap are kept.\n\nProceed?"):
            return
        # data + caches
        self.results = []
        self.smooth_params = dict(smoothing.DEFAULTS); self.smooth_cache.clear()
        self.notch_cache.clear()
        if hasattr(self, "trace_frame"):
            for w in self.trace_frame.winfo_children():
                w.destroy()
        self.trace_vars, self.dvars = {}, {}
        # folders + log
        try:
            self.in_var.set(""); self.out_var.set("")
        except Exception:
            pass
        try:
            self.log.delete("1.0", "end")
        except Exception:
            pass
        # all controls back to defaults, without spamming the (now cleared) log
        self._restoring = True
        try:
            self._apply_preset_data(dict(self._defaults))
        finally:
            self._restoring = False
        # collapse undo history to one clean baseline so there is nothing to
        # "undo" back into the previous session
        self._undo_stack = []; self._redo_stack = []
        try:
            self._undo_stack.append(self._snapshot())
        except Exception:
            pass
        self._update_undo_buttons()
        self.preset_sel.set("")
        self._logline("Program reset (NUKE). Fresh start.")
        self._redraw(); self._update_status()
        # NUKE is global: collapse to a single fresh tab
        self.sessions = [self._capture_session("Session 1")]
        self.active = 0
        self._render_tabs()

    def _reset_defaults(self):
        if not messagebox.askyesno("Reset", "Reset all controls to defaults?"):
            return
        self.smooth_params = dict(smoothing.DEFAULTS); self.smooth_cache.clear()
        self._apply_preset_data(dict(self._defaults))

    def _filter_cmaps(self):
        q = self.cmap_filter.get().strip().lower()
        allm = colormaps.available()
        vals = [m for m in allm if q in m.lower()] if q else list(allm)
        if not vals:
            vals = list(allm)
        if self.cmap.get() not in vals:
            vals = [self.cmap.get()] + vals
        self.cmap_cb.configure(values=vals)

    def _set_cmap_default(self):
        self.settings["cmap_default"] = self.cmap.get()
        self._save_settings()
        self._update_cmap_default_btn()
        self._logline("Default colormap set to '%s'" % self.cmap.get())
        self._log_action("Saved default colormap: " + self.cmap.get())

    def _update_cmap_default_btn(self, *a):
        if not hasattr(self, "cmap_default_btn"):
            return
        same = self.cmap.get() == self.settings.get("cmap_default")
        self.cmap_default_btn.config(
            text=("\u2605 Current is the default" if same else "Set as default"))

    def _draw_cmap_swatch(self):
        cv = getattr(self, "cmap_swatch", None)
        if cv is None:
            return
        try:
            cv.delete("all")
            w = cv.winfo_width() or 200
            h = cv.winfo_height() or 14
            name = self.cmap.get()
            rev = self.cmap_rev.get() if hasattr(self, "cmap_rev") else False
            nseg = 64
            for i in range(nseg):
                frac = i / (nseg - 1)
                if rev:
                    frac = 1.0 - frac
                try:
                    col = colormaps.color_for(name, frac, 0.0, 1.0, i, nseg)
                except Exception:
                    col = (0.5, 0.5, 0.5)
                hexc = "#%02x%02x%02x" % (int(col[0] * 255), int(col[1] * 255),
                                          int(col[2] * 255))
                x0 = int(w * i / nseg); x1 = int(w * (i + 1) / nseg) + 1
                cv.create_rectangle(x0, 0, x1, h, fill=hexc, outline=hexc)
        except Exception:
            pass

    # ---- exports ----------------------------------------------------------
    def _save_plot(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".png",
            filetypes=[("PNG", "*.png"), ("PDF", "*.pdf"), ("SVG", "*.svg"),
                       ("EPS", "*.eps"), ("TIFF", "*.tif *.tiff")])
        if not path:
            return
        old = self.fig.get_size_inches()
        try:
            self.fig.set_size_inches(float(self.fig_w.get()),
                                     float(self.fig_h.get()))
        except (ValueError, tk.TclError):
            pass
        try:
            self.fig.savefig(path, dpi=int(self.dpi.get()),
                             **self._export_kwargs())
            self._logline("Saved plot -> " + path)
            self._toast("Saved " + os.path.basename(path))
            self._provenance(path, "plot", {
                "dpi": self.dpi.get(), "size_in": [self.fig_w.get(),
                                                   self.fig_h.get()],
                "preset": self.preset_sel.get(), "mode": self.mode.get(),
                "waterfall": self.wf_mode.get(), "cmap": self.cmap.get(),
                "x_unit": self.xunit.get(), "defringe": self.show_notch.get(),
                "smoothed": self.show_smooth.get()}, files=[path])
        except Exception as e:
            messagebox.showerror("Save plot",
                                 "Could not save:\n%s\n(%s)" % (path, e))
        finally:
            self.fig.set_size_inches(old)   # keep the live canvas intact
            self.canvas.draw_idle()

    def _export_kwargs(self):
        """savefig kwargs from the Journal/figure export controls."""
        kw = {}
        if getattr(self, "fig_tight", None) is None or self.fig_tight.get():
            kw["bbox_inches"] = "tight"
            try:
                kw["pad_inches"] = float(self.fig_pad.get())
            except (ValueError, tk.TclError):
                pass
        if getattr(self, "fig_transparent", None) is not None \
                and self.fig_transparent.get():
            kw["transparent"] = True
        else:
            fc = getattr(self, "fig_facecolor", None)
            fc = fc.get() if fc is not None else "auto"
            if fc == "none":
                kw["transparent"] = True
            elif fc == "auto":
                kw["facecolor"] = self.fig.get_facecolor()
            else:
                kw["facecolor"] = fc
        return kw

    def _provenance(self, target, kind, params, files=None):
        """Write <target>.provenance.json (or _export.provenance.json inside a
        folder target) recording tool version, timestamp, source folder, the
        given params, and sha1 of any written files. Best-effort; never raises
        into the caller. Main-thread only (reads self.in_var)."""
        import datetime as _dt
        payload = {
            "tool": "DAC_QuickLook (Beamline DAC Data Tool)",
            "version": APP_VERSION,
            "written": _dt.datetime.now().isoformat(timespec="seconds"),
            "kind": kind,
            "input_folder": self.in_var.get(),
            "params": params,
        }
        if files:
            payload["files"] = [{"name": os.path.basename(p),
                                 "sha1": engine.file_sha1(p)}
                                for p in files if os.path.isfile(p)]
        try:
            sidecar = (os.path.join(target, "_export.provenance.json")
                       if os.path.isdir(target)
                       else target + ".provenance.json")
            return engine.write_provenance(sidecar, payload)
        except Exception as e:
            self._logline("  ! provenance write failed: %r" % e)
            return None

    def _batch_export(self):
        shown = self._shown()
        if not shown:
            messagebox.showinfo("Batch export", "No traces selected."); return
        folder = filedialog.askdirectory(title="Folder for per-trace PNGs")
        if not folder:
            return
        unit = self.xunit.get()
        for r in shown:
            fig = Figure(figsize=(6, 4), dpi=100); a = fig.add_subplot(111)
            x = unit_x(r, unit)
            ls = self._trace_ls(r)
            y = (self._smoothed(r) if self.show_smooth.get() else self._abs(r))
            a.plot(x, y, ls=ls, color="#205070")
            a.set_xlabel(unit_label(unit)); a.set_ylabel("Absorbance")
            a.set_title(r["label"])
            if self.flipx.get():
                a.invert_xaxis()
            if self.flipy.get():
                a.invert_yaxis()
            name = re.sub(r"[^A-Za-z0-9.+-]+", "_", r["label"]) + ".png"
            fig.tight_layout(); fig.savefig(os.path.join(folder, name),
                                            dpi=int(self.dpi.get()))
        self._logline("Batch-exported %d PNG(s) -> %s" % (len(shown), folder))
        self._provenance(folder, "batch_png", {
            "n_traces": len(shown), "dpi": self.dpi.get(),
            "x_unit": self.xunit.get(), "smoothed": self.show_smooth.get(),
            "defringe": self.show_notch.get()})

    def _export_defringed(self):
        """Write {stem}_absorbance_notch.csv for every loaded pressure into a
        chosen folder. Always defringes at the current Notch width % (independent
        of the display toggle)."""
        if not self.results:
            messagebox.showinfo("Export", "No data loaded. Run first."); return
        folder = filedialog.askdirectory(title="Folder for defringed CSVs")
        if not folder:
            return
        nkw = self._notch_params()
        n = 0
        written = []
        for r in self.results:
            try:
                p = defringe.write_notch_csv(r, folder, **nkw)
                written.append(p)
                self._logline("  NOTCH %-30s -> %s"
                              % (r["label"], os.path.basename(p)))
                n += 1
            except Exception as e:
                self._logline("  NOTCH FAIL %s: %r" % (r["label"], e))
        self._logline("Exported %d defringed CSV(s) -> %s" % (n, folder))
        self._provenance(folder, "defringed_csv",
                         {"n_csv": n, "notch_params": nkw}, files=written)
        messagebox.showinfo("Export defringed CSV",
                            "Wrote %d defringed CSV(s) to:\n%s" % (n, folder))

    def _export_smoothed(self):
        import csv
        shown = self._shown()
        if not shown:
            messagebox.showinfo("Export", "No traces selected."); return
        folder = filedialog.askdirectory(title="Folder for smoothed CSVs")
        if not folder:
            return
        try:
            lo, hi = float(self.crop_min.get()), float(self.crop_max.get())
        except ValueError:
            lo = hi = None
        written = []
        for r in shown:
            wl, wn = r["wl"], r["wn"]
            ev = np.where(wl > 0, EV_NM / wl, np.nan)
            raw, sm = r["absorbance"], self._smoothed(r)
            mask = ((wl >= lo) & (wl <= hi)) if (self.crop_on.get()
                    and lo is not None) else np.ones(len(wl), bool)
            name = re.sub(r"[^A-Za-z0-9.+-]+", "_", r["label"]) + "_smoothed.csv"
            fp = os.path.join(folder, name)
            with open(fp, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["Wavelength_nm", "Wavenumber_cm-1", "Energy_eV",
                            "Absorbance_raw", "Absorbance_smoothed"])
                for row in zip(wl[mask], wn[mask], ev[mask], raw[mask], sm[mask]):
                    w.writerow(["" if (isinstance(v, float) and np.isnan(v))
                                else v for v in row])
            written.append(fp)
        self._logline("Exported %d smoothed CSV(s) -> %s" % (len(shown), folder))
        self._provenance(folder, "smoothed_csv",
                         {"n_csv": len(shown),
                          "smoothing": dict(self.smooth_params),
                          "cropped": bool(self.crop_on.get()),
                          "crop_nm": [lo, hi] if lo is not None else None},
                         files=written)

    def _copy_clipboard(self):
        """Copy the current figure to the system clipboard as an image."""
        import sys
        try:
            buf = io.BytesIO()
            self.fig.savefig(buf, format="png", dpi=int(self.dpi.get()),
                             bbox_inches="tight")
            buf.seek(0)
            if sys.platform == "win32":
                import win32clipboard
                from PIL import Image
                img = Image.open(buf).convert("RGB")
                out = io.BytesIO(); img.save(out, "BMP")
                data = out.getvalue()[14:]   # strip BMP header -> DIB
                win32clipboard.OpenClipboard()
                try:
                    win32clipboard.EmptyClipboard()
                    win32clipboard.SetClipboardData(win32clipboard.CF_DIB,
                                                    data)
                finally:
                    win32clipboard.CloseClipboard()   # never hold the
                    # system clipboard open on a failed copy
            elif sys.platform == "darwin":
                import subprocess, tempfile
                tf = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
                tf.write(buf.getvalue()); tf.close()
                subprocess.run(["osascript", "-e",
                                'set the clipboard to (read (POSIX file "%s") '
                                'as \u00abclass PNGf\u00bb)' % tf.name],
                               check=True)
                try:
                    os.unlink(tf.name)   # no leaked temp PNG
                except OSError:
                    pass
            else:
                raise RuntimeError("clipboard image copy not supported on this "
                                   "platform")
            self._logline("Figure copied to clipboard.")
            self._toast("Figure copied")
        except Exception as e:
            messagebox.showinfo("Copy figure",
                                "Clipboard copy failed; use Save plot instead."
                                "\n(%s)" % e)


def _enable_dpi_awareness():
    """Crisp text on Windows high-DPI / 4K monitors. No-op on other platforms.
    Must run before the first Tk window is created."""
    import sys
    if sys.platform != "win32":
        return
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)   # per-monitor (Win8.1+)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()        # older fallback
    except Exception:
        pass


def main():
    _enable_dpi_awareness()
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _enable_dpi_awareness()
        r = tk.Tk(); App(r); r.update_idletasks(); r.destroy(); print("SELFTEST OK")
    else:
        main()
