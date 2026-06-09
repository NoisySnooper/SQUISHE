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
import re
import io
import json
import datetime
import traceback
import matplotlib
matplotlib.use("TkAgg")
import numpy as np
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
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
FONTS = ["DejaVu Sans", "DejaVu Serif", "Times New Roman", "Arial",
         "Calibri", "Cambria", "Georgia", "Consolas"]
LEGEND_LOCS = ["best", "upper right", "upper left", "lower left",
               "lower right", "center right", "outside right"]

APP_VERSION = "v1.2"
APP_CODENAME = "Olivine"
APP_TITLE = "Beamline DAC Data Tool  (NSLS-II 22-IR-1)  --  Dr. Lee's Lab"

INFO_TEXT = (
    "ABSORBANCE\n"
    "  A = -log10[(Sample - Dark) / (Background - Dark)]\n\n"
    "X-AXIS UNITS\n"
    "  wavenumber[cm^-1] = 1e7 / wavelength[nm]\n"
    "  energy[eV]        = 1239.84 / wavelength[nm]\n\n"
    "FILENAME FORMAT (must be exact)\n"
    "  vis_{DAC}_{Sample}_{Pressure}[_bg|_s][_C|_D][_2|_3].{seq}\n"
    "    no suffix = dark,  _bg = background,  _s = sample\n"
    "    _C/_D = optional compression/decompression tag (auto-detects branch)\n"
    "    _2/_3 = retake (latest is used),  seq = 001..004\n"
    "    (_C/_D and _2/_3 may appear in either order)\n"
    "    Pressure uses 'p' for the decimal: 1p39 = 1.39 GPa\n"
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
    "2. Pick an Output folder, then click Run. The tool joins the four\n"
    "   grating segments per measurement, computes absorbance, and writes\n"
    "   one CSV per pressure to <output>/<inputname>_absorbance/.\n"
    "3. All pressures plot automatically. Format with the right-side panels.\n"
    "4. Publication 3D figure: Waterfall = 3D ridge, then tune the 3D ridge\n"
    "   options (filled, smoothed, opacity, angle, z clip).\n"
    "5. Click 'Apply journal style', set a figure Size preset, Save as PDF.\n\n"
    "Hover any control for a tip. Folders, theme, window size, default\n"
    "colormap, and presets are remembered between launches."
)

PANEL_GUIDE = (
    "PANEL GUIDE  (right-side controls)\n\n"
    "PLOT MODE\n"
    "  Overlay all pressures - every selected pressure on one set of axes.\n"
    "  Inspect one pressure - one run: Sample/Background/Dark counts on the\n"
    "    left axis and its Absorbance on the right. Use it to sanity-check a\n"
    "    run (good signal, background above sample) before trusting absorbance.\n"
    "  Overlay Y - plot absorbance or a raw channel (sample/background/dark).\n"
    "  Channels S/B/D/Abs - which curves to draw in Inspect mode.\n\n"
    "X AXIS\n"
    "  Wavelength / Wavenumber / Photon energy - same data, converted on the\n"
    "    fly (wn = 1e7/nm, eV = 1239.84/nm).\n"
    "  Flip X - reverse the axis. Top axis - add a 2nd unit across the top.\n\n"
    "AXIS LIMITS\n"
    "  Auto fits the data and fills the boxes with the values in use; uncheck\n"
    "  to type your own min/max and click Apply limits.\n\n"
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
    "  Filled ridges - fill under each curve, drawn front-to-back so near\n"
    "    ridges hide far ones. This is what removes the clutter.\n"
    "  Smoothed ridges - draw the smoothed curve so noise doesn't dominate.\n"
    "  Fill opacity - transparency of each wall. Elev/Azim - camera angle.\n"
    "  Z clip - clamp the height axis (blank = auto min..99th pct) so\n"
    "    saturated spikes don't blow out the scale.\n\n"
    "DEFRINGE\n"
    "  Enable (FFT notch) - remove diamond-anvil interference fringes by\n"
    "    notching the dominant auto-detected fringe out of the raw Sample and\n"
    "    Background counts, then recomputing absorbance. Notch width default\n"
    "    15%. When enabled, Run also writes {stem}_absorbance_notch.csv files;\n"
    "    'Export defringed CSV' writes them anytime.\n\n"
    "SMOOTHING\n"
    "  Show smoothed/raw. 'Smoothing settings' exposes the 5-step filter\n"
    "  (cutoff, density, Hampel, split Savitzky-Golay, jump) matching the\n"
    "  established lab pipeline.\n\n"
    "VERTICAL MARKERS\n"
    "  Vertical lines at given wavelengths (comma-separated), e.g. an edge.\n\n"
    "TITLE / LABELS / LEGEND\n"
    "  Edit title and axis labels. Legend on/off + location + columns.\n"
    "  Colorbar - pressure color scale for continuous maps.\n\n"
    "FONT / JOURNAL / FIGURE\n"
    "  Font family + separate title/label/tick sizes. Size preset is the\n"
    "  target export size (applied on Save, not on screen). Hide top/right\n"
    "  spines, tick direction, minor ticks. 'Apply journal style' = serif,\n"
    "  no grid, thin spines in one click.\n\n"
    "PRESETS\n"
    "  Save the whole control state under a name; reload from the dropdown.\n\n"
    "EXPORT\n"
    "  Save plot (PNG/PDF/SVG/EPS/TIFF; vector embeds fonts). Batch PNG =\n"
    "  one image per shown trace. Export smoothed CSV = wl/cm^-1/eV + raw +\n"
    "  smoothed columns."
)

SHORTCUTS_TEXT = (
    "KEYBOARD SHORTCUTS\n\n"
    "Ctrl+S        Save plot\n"
    "Ctrl+Z        Undo\n"
    "Ctrl+Y        Redo  (also Ctrl+Shift+Z)\n"
    "Ctrl+R        Reset 3D view\n"
    "Ctrl+Shift+C  Copy figure to clipboard\n"
    "1 / 2 / 3     Waterfall: off / 2D stacked / 3D ridge\n"
    "[  /  ]       Previous / next colormap\n"
    "F1            This shortcuts list\n\n"
    "(Number and bracket keys are ignored while typing in a box.)"
)

REF_VIEWS = {"Absorbance reference": INFO_TEXT,
             "Panel guide": PANEL_GUIDE,
             "Quick start": QUICK_START,
             "Keyboard shortcuts": SHORTCUTS_TEXT}


class Tooltip:
    """Lightweight hover tooltip for any widget."""
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip = None
        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._hide)

    def _show(self, _=None):
        if self.tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 18
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry("+%d+%d" % (x, y))
        tk.Label(self.tip, text=self.text, justify="left", wraplength=300,
                 background="#ffffe0", foreground="#222", relief="solid",
                 borderwidth=1, font=("Segoe UI", 8), padx=6, pady=3).pack()

    def _hide(self, _=None):
        if self.tip:
            self.tip.destroy(); self.tip = None


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
    """forward/inverse functions mapping primary-axis values to a top-axis unit."""
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
        _tm = self.settings.get("theme", "dark" if self.settings.get("dark") else "light")
        if _tm not in ("light", "dark", "black"):
            _tm = "light"
        self.theme_mode = tk.StringVar(value=_tm)
        self.dark_mode = tk.BooleanVar(value=(_tm != "light"))
        self._tk_widgets = []      # non-ttk widgets to recolor with the theme
        self._slider_entries = []  # (var, entry, fmt) slider<->box sync
        self._action_log_on = True
        self._undo_stack = []
        self._redo_stack = []
        self._restoring = False

        root.title(APP_TITLE)
        root.geometry(self.settings.get("geometry", "1400x860"))
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

    def _hotkey_wf(self, mode):
        if self.root.focus_get().__class__.__name__ in ("Entry", "TEntry",
                                                         "Combobox", "TCombobox"):
            return
        self.wf_mode.set(mode)

    def _cycle_cmap(self, step):
        try:
            maps = colormaps.available()
            i = (maps.index(self.cmap.get()) + step) % len(maps)
            self.cmap.set(maps[i]); self._log_action("Colormap: " + maps[i])
        except Exception:
            pass

    def _show_shortcuts_popup(self):
        messagebox.showinfo("Keyboard shortcuts", SHORTCUTS_TEXT)

    def _on_close(self):
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

    def _theme_palette(self):
        """(ui_bg, ui_fg, field_bg, plot_bg, plot_fg) for the active theme."""
        t = self.theme_mode.get()
        if t == "black":
            return ("#000000", "#e8e8e8", "#0d0d0d", "#000000", "#e8e8e8")
        if t == "dark":
            return ("#23252b", "#e6e6e6", "#2c2f37", "#23252b", "#e6e6e6")
        if t == "light":
            return ("#f3f5f8", "#1c2530", "#ffffff", "white", "#1c2530")
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
        """Tint carets, group titles and the wordmark for accent themes."""
        _u, fg, _fl, _pb, _pf = self._theme_palette()
        pal = ["#e53935", "#fb8c00", "#fdd835", "#43a047", "#1e88e5", "#8e24aa"]
        for i, rec in enumerate(getattr(self, "_collapsibles", [])):
            col = (pal[i % len(pal)] if rainbow else accent) or fg
            try:
                rec["caret"].configure(foreground=col)
                if rec.get("title_lbl"):
                    rec["title_lbl"].configure(foreground=col)
            except Exception:
                pass
        if hasattr(self, "title_lbl"):
            try:
                self.title_lbl.configure(foreground=accent or fg)
            except Exception:
                pass

    def _recolor_tk(self):
        """Recolor plain tk widgets (Text, Canvas) that ignore the ttk theme."""
        uibg, fg, fld, _pb, _pf = self._theme_palette()
        t = self.theme_mode.get()
        bg = ("#000000" if t == "black" else
              "#1c1d22" if t == "dark" else
              "white" if t == "light" else fld)
        for w in getattr(self, "_tk_widgets", []):
            try:
                w.configure(background=bg, foreground=fg, insertbackground=fg)
            except tk.TclError:
                try:
                    w.configure(background=bg)      # Canvas has no foreground
                except tk.TclError:
                    pass

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
        ttk.Style().configure("TLabelframe.Label", font=("Segoe UI", 9, "bold"))
        # accent (or reset to plain) the carets, group titles and wordmark
        self._recolor_accents(th.get("accent"), th.get("rainbow", False))
        matplotlib.rcParams["pdf.fonttype"] = 42
        matplotlib.rcParams["ps.fonttype"] = 42

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
                cv.configure(height=1, bg=self._theme_palette()[0])
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
        self.settings["theme"] = self.theme_mode.get()
        self.settings["dark"] = self.dark_mode.get()
        self._save_settings()
        self._log_action("Theme: " + self.theme_mode.get())
        self._redraw()

    def _axis_text_colors(self, fg):
        """(axis/spine color, tick+label text color) honoring user overrides."""
        from matplotlib.colors import to_rgb
        def res(var):
            v = var.get() if var is not None else "auto"
            if v == "auto":
                return fg
            try:
                to_rgb(v); return v
            except Exception:
                return fg
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
            with open(SETTINGS_PATH, "w") as f:
                json.dump(self.settings, f)
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
        self.title_lbl = ttk.Label(titles, text="Beamline DAC Data Tool",
                                   font=("Cambria", 17, "bold"))
        self.title_lbl.pack(side="left", anchor="s")
        ttk.Label(titles,
                  text="   Concatenator \u00b7 Absorbance Calculator \u00b7 Plotter",
                  font=("Segoe UI", 9), foreground="#888").pack(side="left",
                                                                anchor="s",
                                                                pady=(0, 2))
        ttk.Label(titles, text="   %s" % APP_VERSION,
                  font=("Segoe UI", 9), foreground="#888").pack(side="left",
                                                                anchor="s",
                                                                pady=(0, 2))
        self.left_btn = ttk.Button(top, text="< Hide left",
                                    command=self._toggle_left)
        self.left_btn.pack(side="right")
        self.right_btn = ttk.Button(top, text="Hide right >",
                                     command=self._toggle_right)
        self.right_btn.pack(side="right", padx=6)
        # theme dropdown (Light / Dark / Black)
        ttk.Button(top, text="About / Help",
                   command=self._about).pack(side="right", padx=(0, 6))
        thf = ttk.Frame(top); thf.pack(side="right", padx=10)
        ttk.Label(thf, text="Theme").pack(side="left", padx=(0, 4))
        thcb = ttk.Combobox(thf, textvariable=self.theme_mode, state="readonly",
                            width=9, values=["light", "dark", "black", "forest",
                                             "rose", "ocean", "solarized", "rainbow"])
        thcb.pack(side="left")
        self.theme_mode.trace_add("write", lambda *a: self._toggle_dark())
        Tooltip(thcb, "Light, Dark, Black (true-black for OLED), plus accent "
                      "themes (forest, rose, ocean, solarized, pride) that tint "
                      "the app chrome only. The plot stays neutral unless you "
                      "enable 'Tint plot with theme' in the Display box. Remembered.")
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
                                  font=("Segoe UI", 10, "bold"), relief="raised",
                                  bd=2, padx=14, pady=2, cursor="hand2")
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

        self.pw.add(self.left, minsize=220, width=330, stretch="never")
        self.pw.add(self.center, minsize=400, stretch="always")
        self.pw.add(self.right_outer, minsize=250, width=320, stretch="never")
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
            self.pw.add(self.right_outer, minsize=250, width=320, stretch="never")
            self.right_btn.config(text="Hide right >")
        self.right_shown = not self.right_shown


    # ---- left pane: folders, run, log, reference -------------------------
    def _build_left(self, p):
        ttk.Label(p, text="Data Input", font=("Cambria", 13, "bold")).pack(
            anchor="w", pady=(0, 4))

        inf = ttk.LabelFrame(p, text="Input folder (raw segments)", padding=6)
        inf.pack(fill="x", pady=3)
        irow = ttk.Frame(inf); irow.pack(fill="x")
        self.in_var = tk.StringVar(value=self.settings.get("in_dir", ""))
        ttk.Entry(irow, textvariable=self.in_var).pack(side="left", fill="x",
                                                       expand=True)
        ttk.Button(irow, text="Browse", command=self._browse_in).pack(side="left")

        ouf = ttk.LabelFrame(p, text="Output folder", padding=6)
        ouf.pack(fill="x", pady=3)
        orow = ttk.Frame(ouf); orow.pack(fill="x")
        self.out_var = tk.StringVar(value=self.settings.get("out_dir", ""))
        ttk.Entry(orow, textvariable=self.out_var).pack(side="left", fill="x",
                                                        expand=True)
        ttk.Button(orow, text="Browse", command=self._browse_out).pack(side="left")

        ttk.Label(p, text="Filenames must match vis_DAC_Sample_Pressure[_bg|_s].seq",
                  foreground="#a60", font=("Segoe UI", 8)).pack(anchor="w")
        brow = ttk.Frame(p); brow.pack(fill="x", pady=(2, 6))
        self.run_btn = ttk.Button(brow, text="Run", command=self._run)
        self.run_btn.pack(side="left", fill="x", expand=True)
        Tooltip(self.run_btn, "Join the 4 grating segments per measurement, "
                              "compute absorbance, and write one CSV per "
                              "pressure to an auto-named output subfolder.")
        ttk.Button(brow, text="Open output",
                   command=self._open_output).pack(side="left", padx=(6, 0))
        self.run_state = ttk.Label(brow, text="Ready", foreground="#2a8a4a",
                                   font=("Segoe UI", 9, "bold"))
        self.run_state.pack(side="left", padx=(8, 0))

        self.run_prog = ttk.Progressbar(p, mode="determinate")
        self.run_prog.pack(fill="x", pady=(0, 3))

        pgf = ttk.LabelFrame(p, text="Progress", padding=4)
        pgf.pack(fill="both", expand=True, pady=3)
        pgbtn = ttk.Frame(pgf); pgbtn.pack(side="bottom", fill="x")
        cl = ttk.Button(pgbtn, text="Copy log", command=self._copy_log)
        cl.pack(side="left")
        Tooltip(cl, "Copy the full progress / action log to the clipboard.")
        es = ttk.Button(pgbtn, text="Export settings", command=self._export_settings)
        es.pack(side="left", padx=(4, 0))
        Tooltip(es, "Print the current plot configuration to the log so you can "
                    "paste it into a paper's methods section.")
        self.log = tk.Text(pgf, height=8, wrap="none", font=("Consolas", 8),
                           relief="flat")
        self._tk_widgets.append(self.log)
        sb = ttk.Scrollbar(pgf, command=self.log.yview)
        hb = ttk.Scrollbar(pgf, orient="horizontal", command=self.log.xview)
        self.log.configure(yscrollcommand=sb.set, xscrollcommand=hb.set)
        sb.pack(side="right", fill="y")
        hb.pack(side="bottom", fill="x")
        self.log.pack(side="left", fill="both", expand=True)

        gf = ttk.LabelFrame(p, text="Guide / reference", padding=6)
        gf.pack(fill="both", expand=True, pady=3)
        hdr = ttk.Frame(gf); hdr.pack(fill="x")
        ttk.Label(hdr, text="View").pack(side="left")
        self.ref_kind = tk.StringVar(value="Absorbance reference")
        cb = ttk.Combobox(hdr, textvariable=self.ref_kind, state="readonly",
                          values=list(REF_VIEWS.keys()))
        cb.pack(side="left", fill="x", expand=True, padx=(4, 0))
        self.ref_kind.trace_add("write", lambda *a: self._set_reference())
        Tooltip(cb, "Switch between the absorbance formulas, a guide to every "
                    "right-side panel, and a quick-start workflow.")
        rw = ttk.Frame(gf); rw.pack(fill="both", expand=True)
        self.ref = tk.Text(rw, height=16, wrap="word", font=("Consolas", 8),
                          relief="flat")
        self._tk_widgets.append(self.ref)
        rsb = ttk.Scrollbar(rw, command=self.ref.yview)
        self.ref.configure(yscrollcommand=rsb.set)
        rsb.pack(side="right", fill="y")
        self.ref.pack(side="left", fill="both", expand=True)
        self._set_reference()

    def _set_reference(self):
        self.ref.config(state="normal")
        self.ref.delete("1.0", "end")
        self.ref.insert("1.0", REF_VIEWS.get(self.ref_kind.get(), INFO_TEXT))
        self.ref.config(state="disabled")

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
            if n is self.rcanvas or n is getattr(self, "rframe", None):
                self.rcanvas.yview_scroll(int(-e.delta / 120), "units")
                return
            n = getattr(n, "master", None)

    # ---- collapsible right-panel groups ----------------------------------
    def _group(self, parent, title):
        """A real accordion section: clickable header + a body frame that
        truly collapses (the whole section shrinks to the header)."""
        cont = ttk.Frame(parent); cont.pack(fill="x", pady=(2, 4))
        hdr = ttk.Frame(cont); hdr.pack(fill="x")
        caret = ttk.Label(hdr, text="\u25bc", width=3,
                          font=("Segoe UI", 12, "bold"))
        caret.pack(side="left")
        title_lbl = ttk.Label(hdr, text=title, font=("Segoe UI", 9, "bold"))
        title_lbl.pack(side="left")
        ttk.Separator(cont, orient="horizontal").pack(fill="x", pady=(1, 2))
        body = ttk.Frame(cont)
        body.pack(fill="x")
        rec = {"key": title, "caret": caret, "title_lbl": title_lbl,
               "body": body, "cont": cont, "collapsed": False,
               "search_text": None}
        self._collapsibles.append(rec)
        for w in [hdr, caret] + list(hdr.winfo_children()):
            w.configure(cursor="hand2")
            w.bind("<Button-1>", lambda e, rec=rec: self._toggle_section(rec))
        if self.settings.get("collapsed", {}).get(title):
            self._set_collapsed(rec, True)
        return body

    def _reorder_sections(self):
        """Enforce an intuitive top-to-bottom order for the control sections."""
        order = ["Plot mode", "Waterfall (2D / 3D plotting)", "3D ridge options",
                 "X axis", "Axis limits", "Axes, ticks & guides", "Axis ticks",
                 "Markers & guides", "Display", "Aspect ratio (2D)", "Defringe",
                 "Smoothing",
                 "Title / labels / legend", "Font", "Presets",
                 "Traces  (check = show,  D = decompression)", "Export",
                 "Journal / figure"]
        by_key = {r["key"]: r for r in getattr(self, "_collapsibles", [])}
        prev = None
        try:
            for key in order:
                rec = by_key.get(key)
                if not rec:
                    continue
                rec["cont"].pack_forget()
                if prev is None:
                    rec["cont"].pack(fill="x", pady=(2, 4))
                else:
                    rec["cont"].pack(fill="x", pady=(2, 4), after=prev["cont"])
                prev = rec
            for rec in self._collapsibles:        # any not listed -> end
                if rec["key"] not in order:
                    rec["cont"].pack_forget()
                    rec["cont"].pack(fill="x", pady=(2, 4))
        except Exception:
            pass

    def _set_collapsed(self, rec, collapse):
        rec["collapsed"] = collapse
        if collapse:
            rec["body"].pack_forget()
            rec["caret"].config(text="\u25b6")
        else:
            rec["body"].pack(fill="x")
            rec["caret"].config(text="\u25bc")

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

    def _filter_sections(self, *a):
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
        ttk.Label(p, text="Plot Area", font=("Cambria", 13, "bold")).pack(
            side="top", anchor="w", padx=6, pady=(6, 2))
        self.fig = Figure(figsize=(6, 5), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.fig, master=p)
        self.canvas.get_tk_widget().pack(side="top", fill="both", expand=True)
        barwrap = ttk.Frame(p); barwrap.pack(side="bottom", fill="x", pady=(6, 4))
        navf = ttk.Frame(barwrap); navf.pack(side="left", padx=(2, 0))
        NavigationToolbar2Tk(self.canvas, navf)
        # centered on the FULL width via place(), so the nav toolbar on the left
        # does not push it off-center
        centerf = ttk.Frame(barwrap)
        centerf.place(relx=0.5, rely=0.5, anchor="center")
        self.status_lbl = ttk.Label(centerf, text="", anchor="center",
                                    font=("Segoe UI", 8), foreground="#888")
        self.status_lbl.pack()
        self.cursor_lbl = ttk.Label(centerf, text="", anchor="center",
                                    font=("Consolas", 8))
        self.cursor_lbl.pack()
        self.canvas.mpl_connect("motion_notify_event", self._on_motion)
        self.canvas.mpl_connect("button_press_event", self._on_plot_click)

    def _on_motion(self, ev):
        if ev.inaxes and ev.xdata is not None and ev.ydata is not None:
            self.cursor_lbl.config(text="x = %.3f    y = %.4f"
                                   % (ev.xdata, ev.ydata))
        else:
            self.cursor_lbl.config(text="")


    # ---- right pane: scrollable controls (width follows the pane) --------
    def _build_right(self, outer):
        ttk.Label(outer, text="Plotting Options",
                  font=("Cambria", 13, "bold")).pack(side="top", anchor="w",
                                                       padx=6, pady=(6, 2))
        srow = ttk.Frame(outer); srow.pack(side="top", fill="x", padx=6, pady=(0, 3))
        ttk.Label(srow, text="Find:").pack(side="left")
        self.section_search = tk.StringVar()
        se = ttk.Entry(srow, textvariable=self.section_search)
        se.pack(side="left", fill="x", expand=True, padx=(3, 0))
        self.section_search.trace_add("write", self._filter_sections)
        Tooltip(se, "Type to find a control (e.g. 'grid', 'legend', 'ridge'). "
                    "Matching sections expand and the rest collapse; clear the "
                    "box to restore your layout.")
        self.rcanvas = tk.Canvas(outer, highlightthickness=0)
        self._tk_widgets.append(self.rcanvas)
        try:
            ttk.Style().configure("Big.Vertical.TScrollbar",
                                  arrowsize=18, width=18)
        except Exception:
            pass
        sb = tk.Scrollbar(outer, orient="vertical", command=self.rcanvas.yview,
                          width=16, bd=1, relief="raised", elementborderwidth=1)
        self.rscroll = sb
        self.rframe = ttk.Frame(self.rcanvas)
        self.rwin = self.rcanvas.create_window((0, 0), window=self.rframe,
                                               anchor="nw")
        self.rframe.bind("<Configure>", lambda e: self.rcanvas.configure(
            scrollregion=self.rcanvas.bbox("all")))
        # KEY FIX: make the inner frame track the canvas width so every box
        # stretches when you drag the divider.
        self.rcanvas.bind("<Configure>",
                          lambda e: self.rcanvas.itemconfig(self.rwin,
                                                            width=e.width))
        self.rcanvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")            # pack the bar FIRST so it
        self.rcanvas.pack(side="left", fill="both", expand=True)  # always shows
        self.rcanvas.bind_all("<MouseWheel>", self._on_wheel)
        r = self.rframe
        self._collapsibles = []
        cbar = ttk.Frame(r); cbar.pack(fill="x", pady=(0, 4))
        ttk.Button(cbar, text="Collapse all", width=11,
                   command=lambda: self._collapse_all(True)).pack(side="left")
        ttk.Button(cbar, text="Expand all", width=10,
                   command=lambda: self._collapse_all(False)).pack(side="left",
                                                                   padx=(4, 0))
        ttk.Button(cbar, text="Reset all", width=9,
                   command=self._reset_all).pack(side="right")

        # --- Plot mode (wide boxes) ---
        pm = self._group(r, "Plot mode")
        self.mode = tk.StringVar(value="overlay")
        ttk.Radiobutton(pm, text="Overlay all pressures", value="overlay",
                        variable=self.mode, command=self._redraw).pack(anchor="w")
        ttk.Radiobutton(pm, text="Inspect one pressure", value="inspect",
                        variable=self.mode, command=self._redraw).pack(anchor="w")
        o = ttk.Frame(pm); o.pack(fill="x", pady=(2, 0))
        ttk.Label(o, text="Overlay Y", width=9).pack(side="left")
        self.ydata = tk.StringVar(value="absorbance")
        ttk.Combobox(o, textvariable=self.ydata, state="readonly",
                     values=["absorbance", "sample", "background", "dark"]
                     ).pack(side="left", fill="x", expand=True)
        self.ydata.trace_add("write", lambda *a: self._redraw())
        i = ttk.Frame(pm); i.pack(fill="x", pady=(2, 0))
        ttk.Label(i, text="Inspect P", width=9).pack(side="left")
        self.inspect_p = tk.StringVar()
        self.inspect_combo = ttk.Combobox(i, textvariable=self.inspect_p,
                                          state="readonly")
        self.inspect_combo.pack(side="left", fill="x", expand=True)
        self.inspect_p.trace_add("write", lambda *a: self._redraw())
        cf = ttk.Frame(pm); cf.pack(fill="x", pady=(2, 0))
        ttk.Label(cf, text="Channels").pack(side="left")
        self.ins_S = tk.BooleanVar(value=True); self.ins_B = tk.BooleanVar(value=True)
        self.ins_D = tk.BooleanVar(value=True); self.ins_A = tk.BooleanVar(value=True)
        for t, v in [("S", self.ins_S), ("B", self.ins_B), ("D", self.ins_D),
                     ("Abs", self.ins_A)]:
            ttk.Checkbutton(cf, text=t, variable=v,
                            command=self._redraw).pack(side="left")
        ttk.Separator(pm, orient="horizontal").pack(fill="x", pady=4)
        rdb = ttk.Button(pm, text="Absorbance readout at wavelength...",
                         command=self._peak_readout)
        rdb.pack(fill="x", pady=2)
        Tooltip(rdb, "Enter a wavelength (nm) to list the absorbance at that "
                     "point for every shown pressure - handy for tracking a band.")
        self._click_readout = tk.BooleanVar(value=False)
        crc = ttk.Checkbutton(pm, text="Click plot to read absorbance",
                              variable=self._click_readout)
        crc.pack(anchor="w")
        Tooltip(crc, "When on, left-clicking anywhere on a 2D plot opens the "
                     "absorbance-vs-pressure table at that wavelength.")

        # --- X axis ---
        ax = self._group(r, "X axis")
        self.xunit = tk.StringVar(value="wl")
        for t, val in [("Wavelength (nm)", "wl"), ("Wavenumber (cm-1)", "wn"),
                       ("Photon energy (eV)", "ev")]:
            ttk.Radiobutton(ax, text=t, value=val, variable=self.xunit,
                            command=self._redraw).pack(anchor="w")
        self.flipx = tk.BooleanVar()
        ttk.Checkbutton(ax, text="Flip X", variable=self.flipx,
                        command=self._redraw).pack(anchor="w")
        tr = ttk.Frame(ax); tr.pack(fill="x")
        ttk.Label(tr, text="Top axis", width=9).pack(side="left")
        self.topaxis = tk.StringVar(value="none")
        tac = ttk.Combobox(tr, textvariable=self.topaxis, state="readonly",
                           values=["none", "wl", "wn", "ev"])
        tac.pack(side="left", fill="x", expand=True)
        self.topaxis.trace_add("write", lambda *a: self._redraw())
        Tooltip(tac, "Add a mirrored top axis in another unit. Wavenumber (wn) "
                     "and energy (ev) are reciprocal in wavelength, so keep X "
                     "min above 0 when using them.")
        rr = ttk.Frame(ax); rr.pack(fill="x")
        ttk.Label(rr, text="Right axis", width=9).pack(side="left")
        self.rightaxis = tk.StringVar(value="none")
        rac = ttk.Combobox(rr, textvariable=self.rightaxis, state="readonly",
                           values=["none", "mirror", "%T"])
        rac.pack(side="left", fill="x", expand=True)
        self.rightaxis.trace_add("write", lambda *a: self._redraw())
        Tooltip(rac, "Add a right Y axis. 'mirror' repeats the left scale; "
                     "'%T' shows transmittance T = 100 x 10^-A (absorbance "
                     "mode only). 2D plots only.")

        # --- Axis limits ---
        lim = self._group(r, "Axis limits")
        self.autoscale = tk.BooleanVar(value=True)
        ttk.Checkbutton(lim, text="Auto", variable=self.autoscale,
                        command=self._redraw).pack(anchor="w")
        self.xmin, self.xmax = tk.StringVar(), tk.StringVar()
        self.ymin, self.ymax = tk.StringVar(), tk.StringVar()
        self.zmin, self.zmax = tk.StringVar(), tk.StringVar()
        self._lim_rows = {}
        for key, a, b in [("X", self.xmin, self.xmax),
                          ("Y", self.ymin, self.ymax),
                          ("Z", self.zmin, self.zmax)]:
            row = ttk.Frame(lim); row.pack(fill="x", pady=1)
            lab = ttk.Label(row, text=key + " min/max", width=11)
            lab.pack(side="left", padx=(0, 4))
            self._lim_rows[key] = lab
            ea = ttk.Entry(row, textvariable=a, width=6)
            ea.pack(side="left", fill="x", expand=True)
            eb = ttk.Entry(row, textvariable=b, width=6)
            eb.pack(side="left", fill="x", expand=True, padx=(4, 0))
            ea.bind("<Return>", lambda e: self._redraw())
            eb.bind("<Return>", lambda e: self._redraw())
            ea.bind("<Button-3>", lambda e, v=a: self._reset_field(v))
            eb.bind("<Button-3>", lambda e, v=b: self._reset_field(v))
        self._lim_hint = ttk.Label(lim, text="", font=("Segoe UI", 8),
                                   foreground="#888")
        self._lim_hint.pack(anchor="w")
        ttk.Button(lim, text="Apply limits", command=self._redraw).pack(anchor="w",
                                                                        pady=(2, 0))

        # --- Axis ticks & spacing ---
        at = self._group(r, "Axes, ticks & guides")
        ttk.Label(at, text="Ticks", font=("Segoe UI", 8, "bold")).pack(anchor="w")
        hh = ttk.Frame(at); hh.pack(fill="x")
        ttk.Label(hh, text="", width=4).pack(side="left")
        ttk.Label(hh, text="major", width=8).pack(side="left")
        ttk.Label(hh, text="minor", width=8).pack(side="left")
        self.xmaj, self.xminor = tk.StringVar(), tk.StringVar()
        self.ymaj, self.yminor = tk.StringVar(), tk.StringVar()
        self.zmaj, self.zminor = tk.StringVar(), tk.StringVar()
        self._tick_edited = {str(self.xmaj): False, str(self.ymaj): False,
                             str(self.zmaj): False}
        for axlbl, mv, nv in [("X", self.xmaj, self.xminor),
                              ("Y", self.ymaj, self.yminor),
                              ("Z", self.zmaj, self.zminor)]:
            row = ttk.Frame(at); row.pack(fill="x")
            ttk.Label(row, text=axlbl, width=4).pack(side="left")
            e1 = ttk.Entry(row, textvariable=mv, width=8); e1.pack(side="left")
            e2 = ttk.Entry(row, textvariable=nv, width=8); e2.pack(side="left")
            e1.bind("<Return>", lambda e: self._redraw())
            e2.bind("<Return>", lambda e: self._redraw())
            e1.bind("<KeyRelease>", lambda ev, vv=mv: self._mark_tick_edited(vv))
            e1.bind("<Button-3>", lambda e, v=mv: self._reset_field(v, "tick"))
            e2.bind("<Button-3>", lambda e, v=nv: self._reset_field(v, "tick"))
        ttk.Label(at, text="spacing in axis units; blank = auto  (Z = 3D only)",
                  font=("Segoe UI", 8), foreground="#888").pack(anchor="w")
        d1 = ttk.Frame(at); d1.pack(fill="x", pady=(3, 0))
        ttk.Label(d1, text="Marks", width=7).pack(side="left")
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
        ln = ttk.Frame(at); ln.pack(fill="x")
        ttk.Label(ln, text="len maj/min", width=11).pack(side="left")
        self.tick_len_major = tk.DoubleVar(value=3.5)
        self.tick_len_minor = tk.DoubleVar(value=2.0)
        ttk.Entry(ln, textvariable=self.tick_len_major, width=5).pack(side="left")
        ttk.Entry(ln, textvariable=self.tick_len_minor, width=5).pack(side="left")
        ttk.Label(ln, text="width").pack(side="left")
        self.tick_width = tk.DoubleVar(value=0.8)
        ttk.Entry(ln, textvariable=self.tick_width, width=5).pack(side="left")
        ttk.Label(ln, text="text").pack(side="left")
        self.tick_fs = tk.IntVar(value=10)
        ttk.Spinbox(ln, from_=6, to=24, textvariable=self.tick_fs, width=3,
                    command=self._redraw).pack(side="left")
        atb = ttk.Frame(at); atb.pack(fill="x", pady=(2, 0))
        ttk.Button(atb, text="Apply ticks", command=self._redraw).pack(side="left")
        ttk.Button(atb, text="Auto", command=self._auto_ticks).pack(side="left",
                                                                    padx=(4, 0))
        _colvals = ["auto", "black", "white", "gray", "#444444", "#888888"]
        acr = ttk.Frame(at); acr.pack(fill="x", pady=(3, 0))
        ttk.Label(acr, text="Axis color", width=11).pack(side="left")
        self.axis_color = tk.StringVar(value="auto")
        acc = ttk.Combobox(acr, textvariable=self.axis_color, state="readonly",
                           width=9, values=_colvals)
        acc.pack(side="left", fill="x", expand=True)
        self.axis_color.trace_add("write", lambda *a: self._redraw())
        Tooltip(acc, "Color of the outer axis lines / spines (2D) and the 3D box "
                     "edges. 'auto' follows the theme.")
        tcr = ttk.Frame(at); tcr.pack(fill="x")
        ttk.Label(tcr, text="Text color", width=11).pack(side="left")
        self.text_color = tk.StringVar(value="auto")
        tcc = ttk.Combobox(tcr, textvariable=self.text_color, state="readonly",
                           width=9, values=_colvals)
        tcc.pack(side="left", fill="x", expand=True)
        self.text_color.trace_add("write", lambda *a: self._redraw())
        Tooltip(tcc, "Color of the tick numbers and axis labels (2D and 3D). "
                     "'auto' follows the theme.")

        # --- Display ---
        d = self._group(r, "Display")
        fr0 = ttk.Frame(d); fr0.pack(fill="x")
        ttk.Label(fr0, text="Filter", width=9).pack(side="left")
        self.cmap_filter = tk.StringVar()
        fent = ttk.Entry(fr0, textvariable=self.cmap_filter)
        fent.pack(side="left", fill="x", expand=True)
        Tooltip(fent, "Type to filter the colormap list below (e.g. 'blu', 'div', "
                      "'gray'). Clear to show all.")
        self.cmap_filter.trace_add("write", lambda *a: self._filter_cmaps())
        cr = ttk.Frame(d); cr.pack(fill="x", pady=(2, 0))
        ttk.Label(cr, text="Colormap", width=9).pack(side="left")
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
        self.theme_plot_follow = tk.BooleanVar(value=False)
        tpf = ttk.Checkbutton(d, text="Tint plot with theme",
                              variable=self.theme_plot_follow,
                              command=self._toggle_dark)
        tpf.pack(anchor="w")
        Tooltip(tpf, "Accent themes (forest, rose, ...) normally keep the plot "
                     "neutral for publication. Enable this to tint the plot "
                     "background to match the theme too.")
        _gstyles = ["solid", "dotted", "dashed", "dashdot"]
        _gcolors = ["auto", "gray", "black", "white", "#888888", "#cccccc",
                    "#444444"]
        ttk.Separator(at, orient="horizontal").pack(fill="x", pady=4)
        ttk.Label(at, text="Grid", font=("Segoe UI", 8, "bold")).pack(anchor="w")
        def _grid_row(on_var, col_var, w_var, a_var, st_var, label, tip):
            ttk.Checkbutton(at, text=label, variable=on_var,
                            command=self._redraw).pack(anchor="w")
            r1 = ttk.Frame(at); r1.pack(fill="x")
            cb = ttk.Combobox(r1, textvariable=col_var, width=8, state="readonly",
                              values=_gcolors)
            cb.pack(side="left", fill="x", expand=True)
            stb = ttk.Combobox(r1, textvariable=st_var, width=8, state="readonly",
                               values=_gstyles)
            stb.pack(side="left", padx=(3, 0), fill="x", expand=True)
            r2 = ttk.Frame(at); r2.pack(fill="x")
            ttk.Label(r2, text="width").pack(side="left")
            we = ttk.Entry(r2, textvariable=w_var, width=5)
            we.pack(side="left", padx=(2, 0))
            ttk.Label(r2, text="opacity").pack(side="left", padx=(6, 0))
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
        ttk.Separator(at, orient="horizontal").pack(fill="x", pady=4)
        ttk.Label(at, text="Curve line",
                  font=("Segoe UI", 8, "bold")).pack(anchor="w")
        self.lw = tk.DoubleVar(value=1.0)
        lwsc, _lwe = self._slider_row(at, "Line width", self.lw, 0.3, 3.0, "%.2f")
        Tooltip(lwsc, "Curve line thickness. Type an exact value or drag.")
        ecr = ttk.Frame(d); ecr.pack(fill="x")
        ttk.Label(ecr, text="3D line color", width=10).pack(side="left")
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
        easc, _eae = self._slider_row(d, "3D line opacity", self.wf3d_edge_alpha,
                                      0.0, 1.0, "%.2f")
        Tooltip(easc, "Opacity of the 3D ridge outlines (0 = no outline).")
        self.fallback_lbl = ttk.Label(d, text="", foreground="#a60",
                                      wraplength=260, font=("Segoe UI", 8))
        self.fallback_lbl.pack(anchor="w")


        # --- Aspect ratio (2D plot box) ---
        asp = self._group(r, "Aspect ratio (2D)")
        arow = ttk.Frame(asp); arow.pack(fill="x")
        self.aspect_mode = tk.StringVar(value="Auto (fill)")
        acb = ttk.Combobox(arow, textvariable=self.aspect_mode, state="readonly",
                           values=["Auto (fill)", "1:1", "4:3", "3:2", "16:9",
                                   "custom"])
        acb.pack(side="left", fill="x", expand=True)
        self.aspect_mode.trace_add("write", lambda *a: self._redraw())
        Tooltip(acb, "Shape of the 2D plot box. 1:1 = square (default); "
                     "Auto = fill the area. Use custom for any W:H.")
        crow = ttk.Frame(asp); crow.pack(fill="x")
        ttk.Label(crow, text="custom W:H", width=10).pack(side="left")
        self.aspect_w = tk.StringVar(value="1")
        self.aspect_h = tk.StringVar(value="1")
        ttk.Entry(crow, textvariable=self.aspect_w, width=5).pack(side="left")
        ttk.Label(crow, text=":").pack(side="left")
        ttk.Entry(crow, textvariable=self.aspect_h, width=5).pack(side="left")
        ttk.Button(crow, text="Apply", command=self._redraw).pack(side="left",
                                                                  padx=(4, 0))

        # --- Waterfall ---
        wf = self._group(r, "Waterfall (2D / 3D plotting)")
        mr = ttk.Frame(wf); mr.pack(fill="x")
        ttk.Label(mr, text="Mode", width=9).pack(side="left")
        self.wf_mode = tk.StringVar(value="off")
        wfcb = ttk.Combobox(mr, textvariable=self.wf_mode, state="readonly",
                            values=["off", "2D stacked", "3D ridge"])
        wfcb.pack(side="left", fill="x", expand=True)
        Tooltip(wfcb, "off = shared baseline; 2D stacked = shift each "
                      "pressure up; 3D ridge = 3D mountain-range view.")
        self.wf_mode.trace_add("write", lambda *a: self._redraw())
        sr = ttk.Frame(wf); sr.pack(fill="x")
        ttk.Label(sr, text="Offset/step", width=9).pack(side="left")
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

        self._build_3d_opts(r)        # 3D ridge options live right under Waterfall

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
                                      1.0, 50.0, "%.0f")
        Tooltip(nwsc, "Gaussian-notch half-width as a percent of the fringe "
                      "frequency (default 15%). Wider removes more around the "
                      "fringe; right-click resets.")
        # Width changes invalidate the cached defringe + downstream smoothing.
        self.notch_width.trace_add(
            "write", lambda *_: (self.notch_cache.clear(), self.smooth_cache.clear()))
        drb = ttk.Button(dfg, text="Defringe report",
                         command=self._defringe_report)
        drb.pack(fill="x", pady=(3, 0))
        Tooltip(drb, "Log which pressures have a detected fringe (Sample / "
                     "Background), the fitted n*t in um, and the detection "
                     "p-value, at the current notch width. Good for QC.")

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

        # --- Markers (folded into Axes, ticks & guides) ---
        ttk.Separator(at, orient="horizontal").pack(fill="x", pady=4)
        ttk.Label(at, text="Reference markers",
                  font=("Segoe UI", 8, "bold")).pack(anchor="w")
        mk = at
        _mstyles = ["solid", "dotted", "dashed", "dashdot"]
        _mcolors = ["gray", "black", "red", "blue", "green", "orange",
                    "purple", "#888888"]
        def _style_row(parent, cvar, svar, wvar, avar=None):
            r1 = ttk.Frame(parent); r1.pack(fill="x")
            cb = ttk.Combobox(r1, textvariable=cvar, width=8, state="readonly",
                              values=_mcolors)
            cb.pack(side="left", fill="x", expand=True)
            sb = ttk.Combobox(r1, textvariable=svar, width=8, state="readonly",
                              values=_mstyles)
            sb.pack(side="left", padx=(3, 0), fill="x", expand=True)
            r2 = ttk.Frame(parent); r2.pack(fill="x")
            ttk.Label(r2, text="width").pack(side="left")
            we = ttk.Entry(r2, textvariable=wvar, width=5)
            we.pack(side="left", padx=(2, 0))
            we.bind("<Return>", lambda ev: self._redraw())
            we.bind("<FocusOut>", lambda ev: self._redraw())
            if avar is not None:
                ttk.Label(r2, text="opacity").pack(side="left", padx=(6, 0))
                ae = ttk.Entry(r2, textvariable=avar, width=5)
                ae.pack(side="left", padx=(2, 0))
                ae.bind("<Return>", lambda ev: self._redraw())
                ae.bind("<FocusOut>", lambda ev: self._redraw())
            for v in (cvar, svar):
                v.trace_add("write", lambda *a: self._redraw())
        ttk.Label(mk, text="Vertical lines - wavelength (nm)",
                  font=("Segoe UI", 8, "bold")).pack(anchor="w")
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
        mir = ttk.Frame(mk); mir.pack(fill="x", pady=(2, 0))
        ttk.Label(mir, text="Auto every").pack(side="left")
        self.marker_interval = tk.StringVar(value="100")
        ttk.Entry(mir, textvariable=self.marker_interval, width=5).pack(
            side="left", padx=(3, 2))
        ttk.Label(mir, text="nm").pack(side="left")
        ttk.Button(mir, text="Fill", width=5,
                   command=self._fill_markers_interval).pack(side="left", padx=(4, 0))
        ttk.Button(mir, text="Clear", width=6,
                   command=self._clear_markers).pack(side="left", padx=(3, 0))
        ttk.Separator(mk, orient="horizontal").pack(fill="x", pady=4)
        ttk.Label(mk, text="Horizontal lines - absorbance",
                  font=("Segoe UI", 8, "bold")).pack(anchor="w")
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
        bb = ttk.Frame(mk); bb.pack(fill="x", pady=(3, 0))
        ttk.Button(bb, text="Apply", command=self._redraw).pack(side="left")
        syncb = ttk.Button(bb, text="Sync H from V",
                           command=self._sync_marker_style)
        syncb.pack(side="left", padx=(4, 0))
        Tooltip(syncb, "Copy the vertical line style (color, pattern, width) "
                       "onto the horizontal lines.")

        # --- Title / labels / legend ---
        lg = self._group(r, "Title / labels / legend")
        self.title_v, self.xlabel_v, self.ylabel_v = (tk.StringVar(),
                                                      tk.StringVar(), tk.StringVar())
        self._label_edited = {"title": False, "xlabel": False, "ylabel": False}
        self._label_keys = {str(self.title_v): "title",
                            str(self.xlabel_v): "xlabel",
                            str(self.ylabel_v): "ylabel"}
        self.title_fs = tk.IntVar(value=13)
        self.label_fs = tk.IntVar(value=11)
        _fsmap = {"Title": self.title_fs, "X label": self.label_fs,
                  "Y label": self.label_fs}
        for lbl, v in [("Title", self.title_v), ("X label", self.xlabel_v),
                       ("Y label", self.ylabel_v)]:
            row = ttk.Frame(lg); row.pack(fill="x")
            ttk.Label(row, text=lbl, width=7).pack(side="left")
            en = ttk.Entry(row, textvariable=v); en.pack(side="left", fill="x",
                                                         expand=True)
            en.bind("<Return>", lambda ev: self._redraw())
            en.bind("<KeyRelease>", lambda ev, vv=v: self._mark_label_edited(vv))
            en.bind("<Button-3>", lambda ev, vv=v: self._reset_field(vv, "label"))
            ttk.Spinbox(row, from_=6, to=28, textvariable=_fsmap[lbl], width=3,
                        command=self._redraw).pack(side="left", padx=(3, 0))
        self.legend_on = tk.BooleanVar(value=True)
        lgck = ttk.Checkbutton(lg, text="Legend", variable=self.legend_on,
                               command=self._redraw)
        lgck.pack(anchor="w")
        Tooltip(lgck, "Show a per-trace key (pressure + branch). Frame style is "
                      "set by the controls just below.")
        self.colorbar_on = tk.BooleanVar()
        cbck = ttk.Checkbutton(lg, text="Colorbar (continuous maps)",
                               variable=self.colorbar_on, command=self._redraw)
        cbck.pack(anchor="w")
        Tooltip(cbck, "Show a continuous pressure colorbar instead of the legend "
                      "(best with many traces). Uses the same frame styling.")
        self.cbar_label = tk.StringVar(value="Pressure (GPa)")
        self.cbar_label_fs = tk.IntVar(value=11)
        self.cbar_tick_fs = tk.IntVar(value=9)
        self.cbar_orient = tk.StringVar(value="vertical")
        self.cbar_width = tk.DoubleVar(value=0.05)
        self.cbar_nticks = tk.IntVar(value=0)
        cb1 = ttk.Frame(lg); cb1.pack(fill="x")
        ttk.Label(cb1, text="Bar label", width=8).pack(side="left")
        cble = ttk.Entry(cb1, textvariable=self.cbar_label)
        cble.pack(side="left", fill="x", expand=True)
        cble.bind("<Return>", lambda e: self._redraw())
        cble.bind("<FocusOut>", lambda e: self._redraw())
        cb2 = ttk.Frame(lg); cb2.pack(fill="x")
        ttk.Label(cb2, text="orient").pack(side="left")
        ttk.Combobox(cb2, textvariable=self.cbar_orient, state="readonly", width=9,
                     values=["vertical", "horizontal"]).pack(side="left",
                                                              padx=(2, 4))
        ttk.Label(cb2, text="lbl").pack(side="left")
        ttk.Spinbox(cb2, from_=6, to=24, textvariable=self.cbar_label_fs, width=3,
                    command=self._redraw).pack(side="left")
        ttk.Label(cb2, text="tick").pack(side="left", padx=(3, 0))
        ttk.Spinbox(cb2, from_=6, to=24, textvariable=self.cbar_tick_fs, width=3,
                    command=self._redraw).pack(side="left")
        cb3 = ttk.Frame(lg); cb3.pack(fill="x")
        ttk.Label(cb3, text="width").pack(side="left")
        ttk.Spinbox(cb3, from_=0.02, to=0.2, increment=0.01, width=5,
                    textvariable=self.cbar_width,
                    command=self._redraw).pack(side="left")
        ttk.Label(cb3, text="# ticks (0=auto)").pack(side="left", padx=(4, 0))
        ttk.Spinbox(cb3, from_=0, to=20, textvariable=self.cbar_nticks, width=3,
                    command=self._redraw).pack(side="left")
        self.cbar_orient.trace_add("write", lambda *a: self._redraw())
        Tooltip(cb1, "Colorbar: label text, orientation, label/tick font size, "
                     "thickness (fraction of the axes), and tick count.")
        lr = ttk.Frame(lg); lr.pack(fill="x")
        ttk.Label(lr, text="Loc").pack(side="left")
        self.legend_loc = tk.StringVar(value="best")
        ttk.Combobox(lr, textvariable=self.legend_loc, values=LEGEND_LOCS,
                     state="readonly", width=12).pack(side="left", fill="x",
                                                      expand=True)
        self.legend_loc.trace_add("write", lambda *a: self._redraw())
        ttk.Label(lr, text="cols").pack(side="left")
        self.legend_cols = tk.IntVar(value=2)
        ttk.Spinbox(lr, from_=1, to=6, textvariable=self.legend_cols, width=3,
                    command=self._redraw).pack(side="left")
        ttk.Label(lr, text="size").pack(side="left", padx=(4, 0))
        self.legend_fs = tk.IntVar(value=9)
        ttk.Spinbox(lr, from_=6, to=24, textvariable=self.legend_fs, width=3,
                    command=self._redraw).pack(side="left")
        # legend frame customization (applies to legend + colorbar)
        self.legend_border = tk.BooleanVar(value=True)
        lbck = ttk.Checkbutton(lg, text="Legend border", variable=self.legend_border,
                               command=self._redraw)
        lbck.pack(anchor="w")
        Tooltip(lbck, "Draw a border box around the legend / colorbar.")
        self.legend_alpha = tk.DoubleVar(value=0.92)
        lasc, _lae = self._slider_row(lg, "Legend bg opacity", self.legend_alpha,
                                      0.0, 1.0, "%.2f")
        Tooltip(lasc, "Legend / colorbar background (frame) opacity. "
                      "0 = fully transparent background.")
        self.legend_bw = tk.DoubleVar(value=0.8)
        lwsc2, _lwe2 = self._slider_row(lg, "Border width", self.legend_bw,
                                        0.0, 3.0, "%.2f")
        Tooltip(lwsc2, "Thickness of the legend / colorbar border.")
        lcr = ttk.Frame(lg); lcr.pack(fill="x")
        ttk.Label(lcr, text="Edge color", width=10).pack(side="left")
        self.legend_edge = tk.StringVar(value="auto")
        lec = ttk.Combobox(lcr, textvariable=self.legend_edge, state="readonly",
                           width=10, values=["auto", "gray", "black", "white",
                                             "#888888", "#cccccc"])
        lec.pack(side="left", fill="x", expand=True)
        self.legend_edge.trace_add("write", lambda *a: self._redraw())
        Tooltip(lec, "Legend / colorbar border color; 'auto' follows the theme.")

        # --- Font ---
        ft = self._group(r, "Font")
        fr = ttk.Frame(ft); fr.pack(fill="x")
        self.font_family = tk.StringVar(value="DejaVu Sans")
        ttk.Combobox(fr, textvariable=self.font_family, values=FONTS,
                     state="readonly").pack(side="left", fill="x", expand=True)
        self.font_family.trace_add("write", lambda *a: self._redraw())
        self.font_size = tk.IntVar(value=10)   # base size; per-item sizes are
                                               # set next to each text control
        ttk.Label(ft, text="(text sizes are set next to each item)",
                  font=("Segoe UI", 8), foreground="#888").pack(anchor="w")

        # --- Presets (named, stored with the app) ---
        pr = self._group(r, "Presets")
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
        ttk.Label(pr, text="Project = every setting + the folders",
                  font=("Segoe UI", 8, "bold")).pack(anchor="w")
        ttk.Label(pr, text="(presets above store styling only)",
                  font=("Segoe UI", 8), foreground="#888").pack(anchor="w")
        srow = ttk.Frame(pr); srow.pack(fill="x", pady=(2, 0))
        ssb = ttk.Button(srow, text="Save project...", command=self._save_session)
        ssb.pack(side="left", fill="x", expand=True)
        slb = ttk.Button(srow, text="Open project...", command=self._load_session)
        slb.pack(side="left", fill="x", expand=True)
        Tooltip(ssb, "Save every plotting setting plus the input/output folders "
                     "to a .json file, so you (or Dr. Lee) can reopen exactly "
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
        bb2 = ttk.Frame(tr); bb2.pack(fill="x", pady=(2, 0))
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
        self.trace_count_lbl = ttk.Label(bb2, text="", font=("Segoe UI", 8))
        self.trace_count_lbl.pack(side="right")
        self.trace_frame = ttk.Frame(tr); self.trace_frame.pack(fill="x")

        # --- Export ---
        ex = self._group(r, "Export")
        dr = ttk.Frame(ex); dr.pack(fill="x")
        ttk.Label(dr, text="DPI").pack(side="left")
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
        ttk.Label(cr2, text="nm").pack(side="left")
        ttk.Button(ex, text="Export smoothed CSV...",
                   command=self._export_smoothed).pack(fill="x", pady=2)
        edb = ttk.Button(ex, text="Export defringed CSV...",
                         command=self._export_defringed)
        edb.pack(fill="x", pady=2)
        Tooltip(edb, "Write one {stem}_absorbance_notch.csv per loaded pressure "
                     "into a chosen folder: FFT-notch defringes the raw Sample "
                     "and Background counts (at the current Notch width %) and "
                     "recomputes absorbance. Works whether or not the Defringe "
                     "display toggle is on.")

        self._build_journal(r)
        # quick-access strip for the most-used controls (always visible on top)
        bar = ttk.LabelFrame(outer, text="Quick access", padding=(6, 3))
        bar.pack(side="top", fill="x", padx=6, pady=(2, 4), before=self.rcanvas)
        b1 = ttk.Frame(bar); b1.pack(fill="x")
        ttk.Combobox(b1, textvariable=self.wf_mode, state="readonly", width=10,
                     values=["off", "2D stacked", "3D ridge"]).pack(side="left")
        ttk.Checkbutton(b1, text="smooth", variable=self.show_smooth,
                        command=self._redraw).pack(side="left", padx=(5, 0))
        ttk.Checkbutton(b1, text="defringe", variable=self.show_notch,
                        command=self._toggle_notch).pack(side="left", padx=(5, 0))
        ttk.Label(b1, text="lw").pack(side="left", padx=(5, 0))
        lwe = ttk.Entry(b1, textvariable=self.lw, width=4); lwe.pack(side="left")
        lwe.bind("<Return>", lambda e: self._redraw())
        lwe.bind("<FocusOut>", lambda e: self._redraw())
        b2 = ttk.Frame(bar); b2.pack(fill="x", pady=(2, 0))
        ttk.Label(b2, text="cmap").pack(side="left")
        ttk.Combobox(b2, textvariable=self.cmap, state="readonly",
                     values=colormaps.available()).pack(side="left", fill="x",
                                                        expand=True, padx=(3, 0))
        Tooltip(b1, "Quick access to the controls you touch most. These mirror "
                    "the full controls in the sections below.")

    # ---- slider + numeric entry, two-way synced --------------------------
    def _slider_row(self, parent, label, var, lo, hi, fmt="%.2f", width=10):
        # Two-line layout: name on its own line, then slider + value below.
        # The value entry sits to the right so the slider thumb never hides it.
        box = ttk.Frame(parent); box.pack(fill="x", pady=(3, 1))
        ttk.Label(box, text=label).pack(anchor="w")
        row = ttk.Frame(box); row.pack(fill="x")
        ent = ttk.Entry(row, width=8)
        ent.pack(side="right", padx=(6, 0))
        sc = ttk.Scale(row, from_=lo, to=hi, variable=var,
                       command=lambda *a: self._on_slider(var, ent, fmt))
        sc.pack(side="left", fill="x", expand=True)
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

        step = (hi - lo) / 100.0 or 0.01
        def wheel(ev, lo=lo, hi=hi, var=var, ent=ent, fmt=fmt, step=step):
            v = max(lo, min(hi, float(var.get())
                            + (step if ev.delta > 0 else -step)))
            var.set(v); ent.delete(0, "end"); ent.insert(0, fmt % v)
            self._redraw(); return "break"
        sc.bind("<MouseWheel>", wheel)

        init = var.get()
        def reset(ev, var=var, ent=ent, fmt=fmt, init=init):
            var.set(init); ent.delete(0, "end"); ent.insert(0, fmt % init)
            self._redraw(); return "break"
        ent.bind("<Button-3>", reset); sc.bind("<Button-3>", reset)
        Tooltip(sc, "Scroll to nudge. Right-click to reset to default.")

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

    def _cam_preset(self, elev, azim):
        self.wf3d_elev.set(elev); self.wf3d_azim.set(azim); self._redraw()

    def _reset_stretch(self):
        self.wf3d_sx.set(1.0); self.wf3d_sy.set(1.0); self.wf3d_sz.set(1.0)
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
        # 3D ridge options (journal-clean defaults; toggle to revert)
        td = self._group(r, "3D ridge options")
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
        ttk.Label(lkr, text="3D look").pack(side="left")
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
                      "Gridline style is set in the Display box.")
        self.wf3d_alpha = tk.DoubleVar(value=0.5)
        sc, _e = self._slider_row(td, "Fill opacity", self.wf3d_alpha,
                                  0.1, 1.0, "%.2f")
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
        cpr = ttk.Frame(td); cpr.pack(fill="x", pady=(2, 0))
        ttk.Label(cpr, text="View").pack(side="left")
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
        stx = ttk.Frame(td); stx.pack(fill="x", pady=(2, 0))
        ttk.Label(stx, text="Stretch").pack(side="left")
        for _lbl, _v in [("X", self.wf3d_sx), ("Y", self.wf3d_sy),
                         ("Z", self.wf3d_sz)]:
            ttk.Label(stx, text=_lbl).pack(side="left", padx=(4, 0))
            sp = ttk.Spinbox(stx, from_=0.3, to=6.0, increment=0.1, width=4,
                             textvariable=_v, command=self._redraw)
            sp.pack(side="left")
            sp.bind("<Return>", lambda ev: self._redraw())
            sp.bind("<FocusOut>", lambda ev: self._redraw())
        ttk.Button(stx, text="Reset", width=6,
                   command=self._reset_stretch).pack(side="left", padx=(4, 0))
        Tooltip(stx, "Stretch the 3D box along an axis for clarity without "
                     "respacing the data. X = spectral, Y = pressure, "
                     "Z = absorbance. Use Y to fan out crowded ridges.")
        # 3D outline width (independent of the 2D curve width)
        self.wf3d_lw = tk.DoubleVar(value=1.0)
        lwsc3, _lw3 = self._slider_row(td, "3D line width", self.wf3d_lw,
                                       0.3, 4.0, "%.2f")
        Tooltip(lwsc3, "Thickness of the 3D ridge outlines.")
        # faint 2D shadow projections onto the back wall / floor
        self.wf3d_project = tk.StringVar(value="Off")
        prr = ttk.Frame(td); prr.pack(fill="x", pady=(2, 0))
        ttk.Label(prr, text="Project").pack(side="left")
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
        ttk.Button(td, text="Reset view", command=self._reset_3d_view).pack(
            anchor="w", pady=(2, 0))
        ttk.Label(td, text="Z (absorbance) range is set in Axis limits above.",
                  font=("Segoe UI", 8), foreground="#888").pack(anchor="w")

    # ---- journal / figure controls ---------------------------------------
    def _build_journal(self, r):
        # Journal / figure
        jf = self._group(r, "Journal / figure")
        pr = ttk.Frame(jf); pr.pack(fill="x")
        ttk.Label(pr, text="Size preset", width=10).pack(side="left")
        self.fig_preset = tk.StringVar(value="custom")
        ttk.Combobox(pr, textvariable=self.fig_preset, state="readonly",
                     values=["custom", "AGU single 3.5in", "AGU double 7.5in",
                             "Nature single 89mm", "Nature double 183mm",
                             "square 5in", "wide 10x4in"]
                     ).pack(side="left", fill="x", expand=True)
        self.fig_preset.trace_add("write", lambda *a: self._apply_size_preset())
        sr = ttk.Frame(jf); sr.pack(fill="x")
        ttk.Label(sr, text="W x H in", width=10).pack(side="left")
        self.fig_w = tk.StringVar(value="7.0"); self.fig_h = tk.StringVar(value="5.0")
        ttk.Entry(sr, textvariable=self.fig_w, width=6).pack(side="left", fill="x",
                                                            expand=True)
        ttk.Entry(sr, textvariable=self.fig_h, width=6).pack(side="left", fill="x",
                                                            expand=True)
        ttk.Button(sr, text="Apply", command=self._redraw).pack(side="left")
        self.hide_spines = tk.BooleanVar()
        ttk.Checkbutton(jf, text="Hide top/right spines", variable=self.hide_spines,
                        command=self._redraw).pack(anchor="w")
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
        ttk.Label(padr, text="Pad (in)", width=10).pack(side="left")
        self.fig_pad = tk.StringVar(value="0.10")
        ttk.Entry(padr, textvariable=self.fig_pad, width=6).pack(side="left")
        ttk.Label(padr, text="  Face").pack(side="left")
        self.fig_facecolor = tk.StringVar(value="auto")
        ttk.Combobox(padr, textvariable=self.fig_facecolor, state="readonly",
                     width=8, values=["auto", "white", "black", "none"]
                     ).pack(side="left", fill="x", expand=True)
        Tooltip(padr, "Pad: whitespace margin kept around a tight box. "
                      "Face: page background on export ('auto' = current theme, "
                      "'none' = transparent).")
        jsb = ttk.Button(jf, text="Apply journal style",
                         command=self._journal_style)
        jsb.pack(fill="x", pady=(4, 0))
        Tooltip(jsb, "One click: serif font, no grid, thin spines, "
                     "minor ticks - a clean publication look.")

    def _apply_size_preset(self):
        sizes = {
            "AGU single 3.5in": ("3.5", "2.8"),
            "AGU double 7.5in": ("7.5", "5.0"),
            "Nature single 89mm": ("3.50", "2.80"),   # 89 mm
            "Nature double 183mm": ("7.20", "5.20"),  # 183 mm
            "square 5in": ("5.0", "5.0"),
            "wide 10x4in": ("10.0", "4.0"),
        }
        wh = sizes.get(self.fig_preset.get())
        if wh:
            self.fig_w.set(wh[0]); self.fig_h.set(wh[1])
        self._redraw()

    def _journal_style(self):
        """One-click clean look: serif font, no grid, thin spines, tight ticks."""
        self.font_family.set("DejaVu Serif")
        self.grid_on.set(False)
        self.hide_spines.set(True)
        self.tick_dir.set("in")
        self.minor_ticks.set(True)
        self.title_fs.set(13); self.label_fs.set(12); self.tick_fs.set(10)
        self._redraw()

    # ---- actions ----------------------------------------------------------
    def _refresh_fallback_note(self):
        self.fallback_lbl.config(
            text=("cmcrameri missing; batlow/roma/hawaii/lajolla use stand-ins."
                  if colormaps.FALLBACKS else ""))

    def _browse_in(self):
        d = filedialog.askdirectory(title="Select input folder")
        if d:
            self.in_var.set(d)
            if not self.out_var.get():
                self.out_var.set(os.path.dirname(d))

    def _browse_out(self):
        d = filedialog.askdirectory(title="Select output folder")
        if d:
            self.out_var.set(d)

    def _open_output(self):
        d = self.last_out_dir or self.out_var.get()
        if d and os.path.isdir(d):
            os.startfile(d)
        else:
            messagebox.showinfo("Open output", "No output folder yet.")

    def _logline(self, msg):
        self.log.insert("end", msg + "\n"); self.log.see("end")
        self.root.update_idletasks()

    def _dest_folder(self, in_dir, out_dir):
        base = os.path.basename(os.path.normpath(in_dir)) or "run"
        dest = os.path.join(out_dir, base + "_absorbance")
        if os.path.isdir(dest) and os.listdir(dest):
            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
            dest = os.path.join(out_dir, base + "_absorbance_" + stamp)
        return dest

    def _set_run_state(self, text, color):
        if hasattr(self, "run_state"):
            try:
                self.run_state.config(text=text, foreground=color)
                self.root.update_idletasks()
            except Exception:
                pass

    def _run(self):
        in_dir, out_dir = self.in_var.get().strip(), self.out_var.get().strip()
        if not os.path.isdir(in_dir):
            messagebox.showerror("Input", "Pick a valid input folder."); return
        if not out_dir:
            messagebox.showerror("Output", "Pick an output folder."); return
        dest = self._dest_folder(in_dir, out_dir)
        self.log.delete("1.0", "end")
        self._logline("Output subfolder: " + dest)
        self.run_btn.config(state="disabled")
        self._set_run_state("Working\u2026", "#c08000")
        self.run_prog.config(mode="determinate", value=0, maximum=100)
        header = ("stat   measurement @ pressure            "
                  "points (valid)   ->  output file")
        def logger(msg):
            self._logline(msg)
            ls = msg.lstrip().lower()
            if ls.startswith("found "):
                self._logline(header)
                import re
                nums = re.findall(r"\d+", msg)
                tot = sum(int(n) for n in nums[:2]) if len(nums) >= 2 else 1
                self.run_prog.config(maximum=max(1, tot), value=0)
            elif ls.startswith("ok") or ls.startswith("skip"):
                try:
                    self.run_prog.step(1); self.run_prog.update_idletasks()
                except Exception:
                    pass
        try:
            results, skipped = engine.run(in_dir, dest, log=logger)
        except Exception as e:
            messagebox.showerror("Run failed", str(e))
            self._set_run_state("Failed", "#c0392b")
            self.run_prog.config(value=0)
            self.run_btn.config(state="normal"); return
        results.sort(key=lambda r: r["pressure_val"])
        self.results = results
        self._skipped_count = len(skipped) if skipped else 0
        self.last_out_dir = dest
        self.smooth_cache.clear()
        self.notch_cache.clear()
        # When defringe is enabled, also write the *_absorbance_notch.csv files
        # next to the absorbance CSVs on Run (matches the Defringe help text).
        if self._notch_on():
            wf = max(self.notch_width.get(), 0.0) / 100.0
            nfx = 0
            for r in results:
                try:
                    defringe.write_notch_csv(r, dest, wf); nfx += 1
                except Exception as e:
                    self._logline("  NOTCH FAIL %s: %r" % (r["label"], e))
            self._logline("Defringe on: wrote %d *_absorbance_notch.csv -> %s"
                          % (nfx, dest))
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
        done = "Done: %d trace%s" % (len(results), "" if len(results) == 1 else "s")
        if skipped:
            done += " (%d skipped)" % len(skipped)
        self._set_run_state(done, "#2a8a4a")
        try:
            self.run_prog.config(value=self.run_prog["maximum"])
        except Exception:
            pass
        self.run_btn.config(state="normal")

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
            ttk.Checkbutton(row, text=txt, variable=show,
                            command=self._redraw).pack(side="left")
            ttk.Checkbutton(row, text="D", variable=dv,
                            command=self._redraw).pack(side="right")

    def _set_all(self, state):
        for v in self.trace_vars.values():
            v.set(state)
        self._redraw()

    def _only_branch(self, branch):
        for r in self.results:
            v = self.trace_vars.get(r['label'])
            if v is not None:
                v.set(self._branch_of(r) == branch)
        self._redraw()

    def _load_dlist(self):
        path = filedialog.askopenfilename(
            title="Text file of decompression pressures",
            filetypes=[("Text", "*.txt *.csv"), ("All", "*.*")])
        if not path:
            return
        wanted = set()
        with open(path) as f:
            for tok in re.split(r"[\s,]+", f.read()):
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

    def _notch_result(self, r):
        """FFT-notch the raw Sample and Background counts independently, then
        recompute absorbance from the defringed channels. Cached per trace;
        cleared when the toggle/width changes or new data loads."""
        key = r["label"]
        if key not in self.notch_cache:
            wf = max(self.notch_width.get(), 0.0) / 100.0
            sc = defringe.defringe_channel(r["wl"], r["samp_c"], wf)
            bc = defringe.defringe_channel(r["wl"], r["bg_c"], wf)
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

    def _defringe_report(self):
        """Log a per-pressure fringe-detection summary at the current width."""
        if not self.results:
            messagebox.showinfo("Defringe", "No data loaded. Run first."); return
        self._logline("Defringe report (notch width %.0f%%):"
                      % self.notch_width.get())
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
        """Defringe on/off: clear caches (smoothed source changes) and redraw."""
        self.notch_cache.clear()
        self.smooth_cache.clear()
        self._log_action("Defringe (FFT notch) %s"
                         % ("on" if self.show_notch.get() else "off"))
        self._redraw()

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
                self._warn_once("xlim_eq", "Axis limits",
                                "X min equals X max; ignoring the X limits.")
            else:
                self.ax.set_xlim(x0, x1)
        if y0 is not None and y1 is not None:
            if y0 == y1:
                self._warn_once("ylim_eq", "Axis limits",
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

    @staticmethod
    def _dash_of(name):
        return {"solid": "-", "dotted": ":", "dashed": "--",
                "dashdot": "-."}.get(name, ":")

    def _sync_marker_style(self):
        """Copy the vertical marker style onto the horizontal markers."""
        self.hmark_color.set(self.vmark_color.get())
        self.hmark_style.set(self.vmark_style.get())
        try:
            self.hmark_width.set(float(self.vmark_width.get()))
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
            out.append(nm if unit == "wl" else
                       1e7 / nm if unit == "wn" else EV_NM / nm)
        return out

    def _ordered_legend(self, entries):
        """entries: list of (handle, pressure, branch). Order C asc then D desc."""
        c = sorted([e for e in entries if e[2] == "C"], key=lambda e: e[1])
        dd = sorted([e for e in entries if e[2] == "D"], key=lambda e: -e[1])
        ordered = c + dd
        handles = [e[0] for e in ordered]
        labels = ["%.2f GPa - %s" % (e[1], e[2]) for e in ordered]
        return handles, labels

    def _legend_or_colorbar(self, entries, cmap_name, pmin, pmax,
                            ScalarMappable, Normalize):
        if self.colorbar_on.get() and not colormaps.is_categorical(cmap_name):
            sm = ScalarMappable(norm=Normalize(pmin, pmax),
                                cmap=colormaps._continuous_cmap(cmap_name))
            sm.set_array([])
            cb = self.fig.colorbar(sm, ax=self.ax, **self._cbar_kwargs())
            self._style_colorbar(cb)
        elif self.legend_on.get() and entries:
            h, l = self._ordered_legend(entries)
            loc = self.legend_loc.get()
            kw = {"fontsize": int(self.legend_fs.get()),
                  "ncol": int(self.legend_cols.get())}
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
        edge = self.legend_edge.get()
        edge = pf if edge == "auto" else edge
        fr = leg.get_frame()
        fr.set_facecolor(pb)
        fr.set_alpha(float(self.legend_alpha.get()))
        if self.legend_border.get():
            fr.set_edgecolor(edge)
            fr.set_linewidth(float(self.legend_bw.get()))
        else:
            fr.set_edgecolor("none"); fr.set_linewidth(0)
        for t in leg.get_texts():
            t.set_color(pf)

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
        edge = self.legend_edge.get()
        edge = pf if edge == "auto" else edge
        try:
            cb.ax.tick_params(colors=pf, labelsize=int(self.cbar_tick_fs.get()))
            cb.set_label(self.cbar_label.get(),
                         fontsize=int(self.cbar_label_fs.get()), color=pf)
            n = int(self.cbar_nticks.get())
            if n > 0:
                from matplotlib.ticker import MaxNLocator
                cb.locator = MaxNLocator(n)
                cb.update_ticks()
            cb.outline.set_alpha(float(self.legend_alpha.get()))
            if self.legend_border.get():
                cb.outline.set_edgecolor(edge)
                cb.outline.set_linewidth(float(self.legend_bw.get()))
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
        except Exception as e:
            self._warn_once("rightaxis_fail", "Right axis",
                            "Could not draw the right axis: %s" % e)

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
            txt = ("theme: %s   |   mode: %s   |   preset: %s   |   shown: %d/%d"
                   % (self.theme_mode.get(), mode, preset, n, tot))
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

    def _redraw_inner(self):
        from matplotlib.cm import ScalarMappable
        from matplotlib.colors import Normalize
        self._apply_font()
        self.fig.clf()
        unit = self.xunit.get()
        cmap_name = self.cmap.get()
        lw = float(self.lw.get())

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
        self.ax.set_xlabel(self.xlabel_v.get() or unit_label(unit))
        self.ax.set_ylabel(self.ylabel_v.get() or _ydef)
        if self.title_v.get():
            self.ax.set_title(self.title_v.get())

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
            except Exception as e:
                self._warn_once("topaxis_fail", "Top axis",
                                "Could not draw the top axis: %s" % e)

        self._build_right_axis(unit)
        self.fig.tight_layout()
        self._sync_limit_boxes()
        self._sync_tick_boxes(self.ax, False)
        self.canvas.draw_idle()

    def _copy_log(self):
        try:
            txt = self.log.get("1.0", "end").rstrip()
            self.root.clipboard_clear(); self.root.clipboard_append(txt)
            self._logline("(log copied to clipboard)")
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
        if str(var) in self._tick_edited:
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
                var.set(("%.4g" % sp))

    def _mark_label_edited(self, var):
        self._label_edited[self._label_keys[str(var)]] = True

    def _autofill_labels(self, xdef, ydef):
        """Show the live default axis labels in the fields until the user edits."""
        if self._restoring:
            return
        for key, var, dflt in [("xlabel", self.xlabel_v, xdef),
                               ("ylabel", self.ylabel_v, ydef)]:
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
        self.ax.title.set_size(int(self.title_fs.get())); self.ax.title.set_color(tcol)
        self.ax.xaxis.label.set_size(int(self.label_fs.get()))
        self.ax.yaxis.label.set_size(int(self.label_fs.get()))
        self.ax.xaxis.label.set_color(tcol); self.ax.yaxis.label.set_color(tcol)
        self.ax.tick_params(colors=tcol, labelsize=int(self.tick_fs.get()))
        for s in self.ax.spines.values():
            s.set_color(acol)
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
    def _draw_overlay(self, unit, cmap_name, lw, ScalarMappable, Normalize):
        shown = self._shown()
        if not shown:
            return
        pvals = [r["pressure_val"] for r in shown]
        pmin, pmax = min(pvals), max(pvals)
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
            cr = (n - 1 - rank) if rev else rank
            cmin, cmax = (pmax, pmin) if rev else (pmin, pmax)
            color = colormaps.color_for(cmap_name, r["pressure_val"], cmin,
                                        cmax, cr, n)
            ls = "--" if self._branch_of(r) == "D" else "-"
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
            entries.append((line, r["pressure_val"], self._branch_of(r)))
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
        pvals = [r["pressure_val"] for r in shown]
        pmin, pmax = min(pvals), max(pvals)
        rev = self.cmap_rev.get()
        n = len(shown)
        chan = self.ydata.get()
        use_sm = chan == "absorbance" and self.show_smooth.get()
        lw = float(self.wf3d_lw.get())          # 3D outline width (own control)
        color_lines = self.wf3d_color_traces.get()
        show_lines = self.wf3d_lines.get()

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
            z = self._smoothed(r) if use_sm else self._channel(r, chan)
            m = np.isfinite(x) & np.isfinite(z)
            ridges.append(_dec(x[m], z[m]))
            allz.append(z[m])
            if ghost:
                zr = self._channel(r, chan)
                mr = np.isfinite(x) & np.isfinite(zr)
                raw_ridges.append(_dec(x[mr], zr[mr]))
            else:
                raw_ridges.append(None)
        allz = np.concatenate(allz) if allz else np.array([0.0, 1.0])

        # Z (absorbance) range from the Axis-limits boxes; blank = auto.
        # Auto high defaults to the 99th percentile so saturated spikes don't
        # blow out the scale; type a Z max to override.
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
        if zlo is None:
            zlo = float(np.nanmin(allz))
        if zhi is None:
            zhi = (float(np.nanpercentile(allz, 99)) if auto
                   else float(np.nanmax(allz)))

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
                cr = (n - 1 - rank) if rev else rank
                cmin, cmax = (pmax, pmin) if rev else (pmin, pmax)
                col = colormaps.color_for(cmap_name, r["pressure_val"], cmin,
                                          cmax, cr, n)
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
                cr = (n - 1 - rank) if rev else rank
                cmin, cmax = (pmax, pmin) if rev else (pmin, pmax)
                col = colormaps.color_for(cmap_name, r["pressure_val"], cmin,
                                          cmax, cr, n)
                ls = "--" if self._branch_of(r) == "D" else "-"
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
                cr = (n - 1 - rank) if rev else rank
                cmin, cmax = (pmax, pmin) if rev else (pmin, pmax)
                pcol = colormaps.color_for(cmap_name, r["pressure_val"], cmin,
                                           cmax, cr, n)
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
        self.ax.view_init(elev=float(self.wf3d_elev.get()),
                          azim=float(self.wf3d_azim.get()))
        self._autofill_labels(unit_label(unit),
                              "Absorbance" if chan == "absorbance" else "Counts")
        self.ax.set_xlabel(self.xlabel_v.get() or unit_label(unit),
                          fontsize=int(self.label_fs.get()), color=tcol)
        self.ax.set_ylabel("Pressure (GPa)", fontsize=int(self.label_fs.get()),
                          color=tcol)
        self.ax.set_zlabel(self.ylabel_v.get() or ("Absorbance"
                           if chan == "absorbance" else "Counts"),
                          fontsize=int(self.label_fs.get()), color=tcol)
        self.ax.tick_params(labelsize=int(self.tick_fs.get()), colors=tcol)
        # 3D axis lines (best-effort) follow the axis color
        try:
            for _axis in (self.ax.xaxis, self.ax.yaxis, self.ax.zaxis):
                _axis.line.set_color(acol)
        except Exception:
            pass
        self._apply_axis_ticks(self.ax, is3d=True)
        if self.title_v.get():
            self.ax.set_title(self.title_v.get(),
                              fontsize=int(self.title_fs.get()), color=tcol)

        if self.wf3d_clean.get():
            pane_bg = "#2b2e36" if self.dark_mode.get() else "white"
            pane_edge = "#4a4f59" if self.dark_mode.get() else "#cccccc"
            for pane in (self.ax.xaxis, self.ax.yaxis, self.ax.zaxis):
                pane.pane.set_facecolor(pane_bg)
                pane.pane.set_edgecolor(pane_edge)
                pane.pane.set_alpha(1.0)
        self._apply_grid(self.ax, is3d=True)
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
                cr = (n - 1 - rank) if rev else rank
                cmin, cmax = (pmax, pmin) if rev else (pmin, pmax)
                col = colormaps.color_for(cmap_name, r["pressure_val"], cmin,
                                          cmax, cr, n)
                entries.append((mpatches.Patch(facecolor=col, edgecolor="none"),
                                r["pressure_val"], self._branch_of(r)))
            h, l = self._ordered_legend(entries)
            loc = self.legend_loc.get()
            kw = {"fontsize": int(self.legend_fs.get()),
                  "ncol": int(self.legend_cols.get())}
            if loc == "outside right":
                leg = self.ax.legend(h, l, bbox_to_anchor=(1.02, 1),
                                     loc="upper left", **kw)
            else:
                leg = self.ax.legend(h, l, loc=loc, **kw)
            self._style_legend(leg)

    # ---- inspect one pressure (S/B/D counts + Abs on right axis) ---------
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
        ttk.Label(wrap, text="How the 5-step smoother works",
                  font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Label(wrap, text="Filters run top to bottom on raw absorbance. "
                  "Removed points become gaps (NaN), not zeros.",
                  wraplength=300, foreground="#777").pack(anchor="w", pady=(0, 4))
        txtf = ttk.Frame(wrap); txtf.pack(fill="both", expand=True)
        tsb = ttk.Scrollbar(txtf)
        tsb.pack(side="right", fill="y")
        txt = tk.Text(txtf, width=44, wrap="word", yscrollcommand=tsb.set,
                      relief="flat", padx=6, pady=4, font=("Segoe UI", 9),
                      background="#f7f7f7" if not self.dark_mode.get() else "#23252b",
                      foreground="#222" if not self.dark_mode.get() else "#ddd")
        txt.pack(side="left", fill="both", expand=True)
        tsb.config(command=txt.yview)
        txt.tag_configure("h", font=("Segoe UI", 9, "bold"),
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
            ttk.Label(row, text=text, width=20).pack(side="left")
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
            "flipx": self.flipx, "topaxis": self.topaxis,
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
            "raw_opacity": self.raw_opacity, "markers": self.markers,
            "title": self.title_v, "xlabel": self.xlabel_v, "ylabel": self.ylabel_v,
            "legend_on": self.legend_on, "colorbar_on": self.colorbar_on,
            "legend_loc": self.legend_loc, "legend_cols": self.legend_cols,
            "legend_border": self.legend_border, "legend_alpha": self.legend_alpha,
            "legend_bw": self.legend_bw, "legend_edge": self.legend_edge,
            "legend_fs": self.legend_fs, "cbar_label": self.cbar_label,
            "cbar_label_fs": self.cbar_label_fs, "cbar_tick_fs": self.cbar_tick_fs,
            "cbar_orient": self.cbar_orient, "cbar_width": self.cbar_width,
            "cbar_nticks": self.cbar_nticks, "no_raw_bg": self.no_raw_bg,
            "perf_mode": self.perf_mode,
            "font_family": self.font_family, "font_size": self.font_size,
            "dpi": self.dpi, "ins_S": self.ins_S, "ins_B": self.ins_B,
            "ins_D": self.ins_D, "ins_A": self.ins_A,
            "wf3d_even": self.wf3d_even, "wf3d_fill": self.wf3d_fill,
            "wf3d_clean": self.wf3d_clean,
            "wf3d_look": self.wf3d_look, "wf3d_color_traces": self.wf3d_color_traces,
            "wf3d_lw": self.wf3d_lw, "wf3d_project": self.wf3d_project,
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
            "axis_color": self.axis_color, "text_color": self.text_color,
            "rightaxis": self.rightaxis, "marker_interval": self.marker_interval,
            "hmarkers": self.hmarkers,
            "vmark_color": self.vmark_color, "vmark_style": self.vmark_style,
            "vmark_width": self.vmark_width, "hmark_color": self.hmark_color,
            "hmark_style": self.hmark_style, "hmark_width": self.hmark_width,
            "vmark_alpha": self.vmark_alpha, "hmark_alpha": self.hmark_alpha,
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
        win.title("About / Help"); win.geometry("580x540")
        txt = tk.Text(win, wrap="word", font=("Segoe UI", 9),
                      padx=10, pady=10)
        txt.pack(fill="both", expand=True)
        body = (
            APP_TITLE + "\n" + APP_VERSION + "\n\n"
            "Built for Dr. Lee's lab to concatenate DAC absorption segments, "
            "compute absorbance, and plot (2D overlay / stacked, 3D ridge).\n\n"
            "ABSORBANCE\n"
            "  A = -log10[(Sample - Dark) / (Background - Dark)]\n\n"
            "X-AXIS UNITS\n"
            "  wavenumber [cm^-1] = 1e7 / wavelength[nm]\n"
            "  photon energy [eV] = 1239.84 / wavelength[nm]\n\n"
            "FILENAME FORMAT (must be exact)\n"
            "  vis_{DAC}_{Sample}_{Pressure}[_bg|_s][_C|_D][_2|_3].{seq}\n"
            "  no suffix = dark, _bg = background, _s = sample\n"
            "  _C / _D = compression / decompression (auto-detected)\n"
            "  Pressure uses 'p' for the decimal: 1p39 = 1.39 GPa\n\n"
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
            tv.insert("", "end", values=("%.2f" % r["pressure_val"],
                                         "%.4f" % float(ya[idx])))
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
        self.fig.savefig(path, dpi=int(self.dpi.get()), **self._export_kwargs())
        self.fig.set_size_inches(old)       # restore so the live canvas is intact
        self.canvas.draw_idle()
        self._logline("Saved plot -> " + path)

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
            ls = "--" if self._branch_of(r) == "D" else "-"
            y = (self._smoothed(r) if self.show_smooth.get() else self._abs(r))
            a.plot(x, y, ls=ls, color="#205070")
            a.set_xlabel(unit_label(unit)); a.set_ylabel("Absorbance")
            a.set_title(r["label"])
            if self.flipx.get():
                a.invert_xaxis()
            name = re.sub(r"[^A-Za-z0-9.+-]+", "_", r["label"]) + ".png"
            fig.tight_layout(); fig.savefig(os.path.join(folder, name),
                                            dpi=int(self.dpi.get()))
        self._logline("Batch-exported %d PNG(s) -> %s" % (len(shown), folder))

    def _export_defringed(self):
        """Write {stem}_absorbance_notch.csv for every loaded pressure into a
        chosen folder. Always defringes at the current Notch width % (independent
        of the display toggle)."""
        if not self.results:
            messagebox.showinfo("Export", "No data loaded. Run first."); return
        folder = filedialog.askdirectory(title="Folder for defringed CSVs")
        if not folder:
            return
        wf = max(self.notch_width.get(), 0.0) / 100.0
        n = 0
        for r in self.results:
            try:
                p = defringe.write_notch_csv(r, folder, wf)
                self._logline("  NOTCH %-30s -> %s"
                              % (r["label"], os.path.basename(p)))
                n += 1
            except Exception as e:
                self._logline("  NOTCH FAIL %s: %r" % (r["label"], e))
        self._logline("Exported %d defringed CSV(s) -> %s" % (n, folder))
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
        for r in shown:
            wl, wn = r["wl"], r["wn"]
            ev = np.where(wl > 0, EV_NM / wl, np.nan)
            raw, sm = r["absorbance"], self._smoothed(r)
            mask = ((wl >= lo) & (wl <= hi)) if (self.crop_on.get()
                    and lo is not None) else np.ones(len(wl), bool)
            name = re.sub(r"[^A-Za-z0-9.+-]+", "_", r["label"]) + "_smoothed.csv"
            with open(os.path.join(folder, name), "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["Wavelength_nm", "Wavenumber_cm-1", "Energy_eV",
                            "Absorbance_raw", "Absorbance_smoothed"])
                for row in zip(wl[mask], wn[mask], ev[mask], raw[mask], sm[mask]):
                    w.writerow(["" if (isinstance(v, float) and np.isnan(v))
                                else v for v in row])
        self._logline("Exported %d smoothed CSV(s) -> %s" % (len(shown), folder))

    def _copy_clipboard(self):
        """Copy the current figure to the Windows clipboard as an image."""
        try:
            import win32clipboard
            from PIL import Image
            buf = io.BytesIO()
            self.fig.savefig(buf, format="png", dpi=int(self.dpi.get()),
                             bbox_inches="tight")
            buf.seek(0)
            img = Image.open(buf).convert("RGB")
            out = io.BytesIO(); img.save(out, "BMP")
            data = out.getvalue()[14:]   # strip BMP header -> DIB
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32clipboard.CF_DIB, data)
            win32clipboard.CloseClipboard()
            self._logline("Figure copied to clipboard.")
        except Exception as e:
            messagebox.showinfo("Copy figure",
                                "Clipboard copy needs pywin32 + pillow.\n"
                                "Use Save plot instead.\n(%s)" % e)


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        r = tk.Tk(); App(r); r.update_idletasks(); r.destroy(); print("SELFTEST OK")
    else:
        main()
