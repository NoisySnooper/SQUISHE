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

APP_TITLE = "Beamline DAC Data Tool  (NSLS-II 22-IR-1)  --  Dr. Lee's Lab"

INFO_TEXT = (
    "ABSORBANCE\n"
    "  A = -log10[(Sample - Dark) / (Background - Dark)]\n\n"
    "X-AXIS UNITS\n"
    "  wavenumber[cm^-1] = 1e7 / wavelength[nm]\n"
    "  energy[eV]        = 1239.84 / wavelength[nm]\n\n"
    "FILENAME FORMAT (must be exact)\n"
    "  vis_{DAC}_{Sample}_{Pressure}[_bg|_s][_2|_3][_C|_D].{seq}\n"
    "    no suffix = dark,  _bg = background,  _s = sample\n"
    "    _2/_3 = retake (latest is used),  seq = 001..004\n"
    "    _C/_D = optional compression/decompression tag (auto-detects branch)\n"
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

REF_VIEWS = {"Absorbance reference": INFO_TEXT,
             "Panel guide": PANEL_GUIDE,
             "Quick start": QUICK_START}


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
        self.last_out_dir = None
        self.settings = self._load_settings()
        self.dark_mode = tk.BooleanVar(value=self.settings.get("dark", False))
        self._tk_widgets = []      # non-ttk widgets to recolor with the theme
        self._slider_entries = []  # (var, entry, fmt) slider<->box sync

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
        self.root.bind("<Control-s>", lambda e: self._save_plot())
        self._redraw()

    def _on_close(self):
        try:
            self.settings["geometry"] = self.root.winfo_geometry()
            self._save_settings()
        except Exception:
            pass
        self.root.destroy()

    def _recolor_tk(self):
        """Recolor plain tk widgets (Text, Canvas) that ignore the ttk theme."""
        bg, fg = ("#1c1d22", "#e6e6e6") if self.dark_mode.get() else ("white",
                                                                       "#1c2530")
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
        if _HAVE_SVTTK:
            try:
                sv_ttk.set_theme("dark" if self.dark_mode.get() else "light")
            except Exception:
                self._clam_palette()
        else:
            self._clam_palette()
        ttk.Style().configure("TLabelframe.Label", font=("Segoe UI", 9, "bold"))
        matplotlib.rcParams["pdf.fonttype"] = 42
        matplotlib.rcParams["ps.fonttype"] = 42

    def _clam_palette(self):
        """Fallback hand-tuned theme if sv_ttk is unavailable."""
        dark = self.dark_mode.get()
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            return
        bg = "#23252b" if dark else "#f3f5f8"
        fg = "#e6e6e6" if dark else "#1c2530"
        fld = "#2c2f37" if dark else "#ffffff"
        self.root.configure(bg=bg)
        style.configure(".", background=bg, foreground=fg, fieldbackground=fld,
                        bordercolor="#555c66" if dark else "#c2c9d2")
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

    def _toggle_dark(self):
        self._init_theme()
        self._center_titles()
        self._recolor_tk()
        self.settings["dark"] = self.dark_mode.get()
        self._save_settings()
        self._redraw()

    def _mpl_colors(self):
        return ("#23252b", "#e6e6e6") if self.dark_mode.get() else ("white", "#1c2530")

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
        titles = ttk.Frame(top)
        titles.pack(side="left")
        ttk.Label(titles, text="Beamline DAC Data Tool",
                  font=("Segoe UI", 15, "bold")).pack(anchor="w")
        ttk.Label(titles, text="Concatenator  -  Absorbance Calculator  -  Plotter",
                  font=("Segoe UI", 9), foreground="#666").pack(anchor="w")
        self.left_btn = ttk.Button(top, text="< Hide left",
                                    command=self._toggle_left)
        self.left_btn.pack(side="right")
        self.right_btn = ttk.Button(top, text="Hide right >",
                                     command=self._toggle_right)
        self.right_btn.pack(side="right", padx=6)
        dm = ttk.Checkbutton(top, text="Dark mode", variable=self.dark_mode,
                             command=self._toggle_dark)
        dm.pack(side="right", padx=10)
        Tooltip(dm, "Toggle a dark theme for the whole app and the plot. "
                    "Remembered between launches.")

    # ---- resizable 3-pane layout -----------------------------------------
    def _build_panes(self):
        self.pw = tk.PanedWindow(self.root, orient="horizontal", sashwidth=6,
                                 sashrelief="raised", bg="#d0d0d0")
        self.pw.pack(side="top", fill="both", expand=True)

        self.left = ttk.Frame(self.pw, padding=(8, 6))
        self.center = ttk.Frame(self.pw)
        self.right_outer = ttk.Frame(self.pw)
        self._build_left(self.left)
        self._build_center(self.center)
        self._build_right(self.right_outer)

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
        ttk.Label(p, text="Data Input", font=("Segoe UI", 12, "bold")).pack(
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

        pgf = ttk.LabelFrame(p, text="Progress", padding=4)
        pgf.pack(fill="both", expand=True, pady=3)
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
    def _build_center(self, p):
        ttk.Label(p, text="Plot Area", font=("Segoe UI", 12, "bold")).pack(
            side="top", anchor="w", padx=6, pady=(6, 2))
        self.fig = Figure(figsize=(6, 5), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.fig, master=p)
        self.canvas.get_tk_widget().pack(side="top", fill="both", expand=True)
        bar = ttk.Frame(p); bar.pack(side="bottom", fill="x")
        NavigationToolbar2Tk(self.canvas, bar)
        self.cursor_lbl = ttk.Label(p, text="", anchor="e", font=("Consolas", 8))
        self.cursor_lbl.pack(side="bottom", fill="x")
        self.canvas.mpl_connect("motion_notify_event", self._on_motion)

    def _on_motion(self, ev):
        if ev.inaxes and ev.xdata is not None and ev.ydata is not None:
            self.cursor_lbl.config(text="x = %.3f    y = %.4f"
                                   % (ev.xdata, ev.ydata))
        else:
            self.cursor_lbl.config(text="")


    # ---- right pane: scrollable controls (width follows the pane) --------
    def _build_right(self, outer):
        ttk.Label(outer, text="Plotting Options",
                  font=("Segoe UI", 12, "bold")).pack(side="top", anchor="w",
                                                       padx=6, pady=(6, 2))
        self.rcanvas = tk.Canvas(outer, highlightthickness=0)
        self._tk_widgets.append(self.rcanvas)
        sb = ttk.Scrollbar(outer, command=self.rcanvas.yview)
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
        self.rcanvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.rcanvas.bind_all("<MouseWheel>",
                             lambda e: self.rcanvas.yview_scroll(
                                 int(-e.delta / 120), "units"))
        r = self.rframe

        # --- Plot mode (wide boxes) ---
        pm = ttk.LabelFrame(r, text="Plot mode", padding=6); pm.pack(fill="x", pady=3)
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

        # --- X axis ---
        ax = ttk.LabelFrame(r, text="X axis", padding=6); ax.pack(fill="x", pady=3)
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
        ttk.Combobox(tr, textvariable=self.topaxis, state="readonly",
                     values=["none", "wl", "wn", "ev"]).pack(side="left",
                                                             fill="x", expand=True)
        self.topaxis.trace_add("write", lambda *a: self._redraw())

        # --- Axis limits ---
        lim = ttk.LabelFrame(r, text="Axis limits", padding=6); lim.pack(fill="x", pady=3)
        self.autoscale = tk.BooleanVar(value=True)
        ttk.Checkbutton(lim, text="Auto", variable=self.autoscale,
                        command=self._redraw).pack(anchor="w")
        self.xmin, self.xmax = tk.StringVar(), tk.StringVar()
        self.ymin, self.ymax = tk.StringVar(), tk.StringVar()
        for lbl, a, b in [("X min/max", self.xmin, self.xmax),
                          ("Y min/max", self.ymin, self.ymax)]:
            row = ttk.Frame(lim); row.pack(fill="x", pady=1)
            ttk.Label(row, text=lbl, width=11).pack(side="left", padx=(0, 4))
            ttk.Entry(row, textvariable=a, width=6).pack(side="left", fill="x",
                                                         expand=True)
            ttk.Entry(row, textvariable=b, width=6).pack(side="left", fill="x",
                                                         expand=True, padx=(4, 0))
        ttk.Button(lim, text="Apply limits", command=self._redraw).pack(anchor="w",
                                                                        pady=(2, 0))

        # --- Axis ticks & spacing ---
        at = ttk.LabelFrame(r, text="Axis ticks", padding=6); at.pack(fill="x", pady=3)
        hh = ttk.Frame(at); hh.pack(fill="x")
        ttk.Label(hh, text="", width=4).pack(side="left")
        ttk.Label(hh, text="major", width=8).pack(side="left")
        ttk.Label(hh, text="minor", width=8).pack(side="left")
        self.xmaj, self.xminor = tk.StringVar(), tk.StringVar()
        self.ymaj, self.yminor = tk.StringVar(), tk.StringVar()
        self.zmaj, self.zminor = tk.StringVar(), tk.StringVar()
        for axlbl, mv, nv in [("X", self.xmaj, self.xminor),
                              ("Y", self.ymaj, self.yminor),
                              ("Z", self.zmaj, self.zminor)]:
            row = ttk.Frame(at); row.pack(fill="x")
            ttk.Label(row, text=axlbl, width=4).pack(side="left")
            e1 = ttk.Entry(row, textvariable=mv, width=8); e1.pack(side="left")
            e2 = ttk.Entry(row, textvariable=nv, width=8); e2.pack(side="left")
            e1.bind("<Return>", lambda e: self._redraw())
            e2.bind("<Return>", lambda e: self._redraw())
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
        ttk.Button(at, text="Apply ticks", command=self._redraw).pack(
            anchor="w", pady=(2, 0))

        # --- Display ---
        d = ttk.LabelFrame(r, text="Display", padding=6); d.pack(fill="x", pady=3)
        cr = ttk.Frame(d); cr.pack(fill="x")
        ttk.Label(cr, text="Colormap", width=9).pack(side="left")
        self.cmap = tk.StringVar(value=self.settings.get("cmap_default", "batlow"))
        cmb = ttk.Combobox(cr, textvariable=self.cmap, values=colormaps.available(),
                           state="readonly")
        cmb.pack(side="left", fill="x", expand=True)
        self.cmap.trace_add("write", lambda *a: self._redraw())
        db = ttk.Button(cr, text="Set default", width=11,
                        command=self._set_cmap_default)
        db.pack(side="left", padx=(4, 0))
        Tooltip(db, "Remember this colormap as the default for next launch.")
        Tooltip(cmb, "Color scale across pressures. Crameri maps (batlow, roma, "
                     "hawaii, lajolla) are perceptually uniform and color-blind "
                     "safe.")
        self.cmap_rev = tk.BooleanVar()
        ttk.Checkbutton(d, text="Reverse colormap", variable=self.cmap_rev,
                        command=self._redraw).pack(anchor="w")
        self.grid_on = tk.BooleanVar()
        ttk.Checkbutton(d, text="Grid", variable=self.grid_on,
                        command=self._redraw).pack(anchor="w")
        self.lw = tk.DoubleVar(value=1.0)
        lwsc, _lwe = self._slider_row(d, "Line width", self.lw, 0.3, 3.0, "%.2f")
        Tooltip(lwsc, "Curve line thickness. Type an exact value or drag.")
        self.fallback_lbl = ttk.Label(d, text="", foreground="#a60",
                                      wraplength=260, font=("Segoe UI", 8))
        self.fallback_lbl.pack(anchor="w")


        # --- Aspect ratio (2D plot box) ---
        asp = ttk.LabelFrame(r, text="Aspect ratio (2D)", padding=6)
        asp.pack(fill="x", pady=3)
        arow = ttk.Frame(asp); arow.pack(fill="x")
        self.aspect_mode = tk.StringVar(value="1:1")
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
        wf = ttk.LabelFrame(r, text="Waterfall", padding=6); wf.pack(fill="x", pady=3)
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
        ttk.Entry(sr, textvariable=self.wf_step).pack(side="left", fill="x",
                                                      expand=True)
        ttk.Button(sr, text="Apply", command=self._redraw).pack(side="left")
        self.wf_label = tk.BooleanVar(value=True)
        ttk.Checkbutton(wf, text="Label each ridge with pressure",
                        variable=self.wf_label, command=self._redraw).pack(anchor="w")

        self._build_3d_opts(r)        # 3D ridge options live right under Waterfall

        # --- Smoothing ---
        sm = ttk.LabelFrame(r, text="Smoothing", padding=6); sm.pack(fill="x", pady=3)
        self.show_smooth = tk.BooleanVar()
        ttk.Checkbutton(sm, text="Show smoothed", variable=self.show_smooth,
                        command=self._redraw).pack(anchor="w")
        self.show_raw = tk.BooleanVar(value=True)
        ttk.Checkbutton(sm, text="Show raw (faded when smoothing)",
                        variable=self.show_raw, command=self._redraw).pack(anchor="w")
        ttk.Button(sm, text="Smoothing settings...",
                   command=self._open_smooth_panel).pack(fill="x", pady=(4, 0))

        # --- Markers ---
        mk = ttk.LabelFrame(r, text="Vertical markers", padding=6); mk.pack(fill="x", pady=3)
        ttk.Label(mk, text="Wavelengths (nm), comma-separated").pack(anchor="w")
        self.markers = tk.StringVar()
        e = ttk.Entry(mk, textvariable=self.markers); e.pack(fill="x")
        e.bind("<Return>", lambda ev: self._redraw())
        ttk.Button(mk, text="Apply markers", command=self._redraw).pack(anchor="w",
                                                                        pady=(2, 0))

        # --- Title / labels / legend ---
        lg = ttk.LabelFrame(r, text="Title / labels / legend", padding=6)
        lg.pack(fill="x", pady=3)
        self.title_v, self.xlabel_v, self.ylabel_v = (tk.StringVar(),
                                                      tk.StringVar(), tk.StringVar())
        for lbl, v in [("Title", self.title_v), ("X label", self.xlabel_v),
                       ("Y label", self.ylabel_v)]:
            row = ttk.Frame(lg); row.pack(fill="x")
            ttk.Label(row, text=lbl, width=7).pack(side="left")
            en = ttk.Entry(row, textvariable=v); en.pack(side="left", fill="x",
                                                         expand=True)
            en.bind("<Return>", lambda ev: self._redraw())
        self.legend_on = tk.BooleanVar(value=True)
        ttk.Checkbutton(lg, text="Legend", variable=self.legend_on,
                        command=self._redraw).pack(anchor="w")
        self.colorbar_on = tk.BooleanVar()
        ttk.Checkbutton(lg, text="Colorbar (continuous maps)",
                        variable=self.colorbar_on, command=self._redraw).pack(anchor="w")
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

        # --- Font ---
        ft = ttk.LabelFrame(r, text="Font", padding=6); ft.pack(fill="x", pady=3)
        fr = ttk.Frame(ft); fr.pack(fill="x")
        self.font_family = tk.StringVar(value="DejaVu Sans")
        ttk.Combobox(fr, textvariable=self.font_family, values=FONTS,
                     state="readonly").pack(side="left", fill="x", expand=True)
        self.font_family.trace_add("write", lambda *a: self._redraw())
        self.font_size = tk.IntVar(value=10)
        ttk.Spinbox(fr, from_=6, to=20, textvariable=self.font_size, width=3,
                    command=self._redraw).pack(side="left")

        # --- Presets (named, stored with the app) ---
        pr = ttk.LabelFrame(r, text="Presets", padding=6); pr.pack(fill="x", pady=3)
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

        # --- Traces (show + D toggle) ---
        tr = ttk.LabelFrame(r, text="Traces  (check = show,  D = decompression)",
                            padding=6); tr.pack(fill="x", pady=3)
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
        ex = ttk.LabelFrame(r, text="Export", padding=6); ex.pack(fill="x", pady=3)
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

        self._build_journal(r)

    # ---- slider + numeric entry, two-way synced --------------------------
    def _slider_row(self, parent, label, var, lo, hi, fmt="%.2f", width=10):
        row = ttk.Frame(parent); row.pack(fill="x")
        ttk.Label(row, text=label, width=width).pack(side="left")
        ent = ttk.Entry(row, width=7)
        sc = ttk.Scale(row, from_=lo, to=hi, variable=var,
                       command=lambda *a: self._on_slider(var, ent, fmt))
        sc.pack(side="left", fill="x", expand=True)
        ent.pack(side="left", padx=(4, 0))
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
        td = ttk.LabelFrame(r, text="3D ridge options", padding=6)
        td.pack(fill="x", pady=3)
        self.wf3d_even = tk.BooleanVar(value=True)
        ttk.Checkbutton(td, text="Even rank spacing", variable=self.wf3d_even,
                        command=self._redraw).pack(anchor="w")
        self.wf3d_fill = tk.BooleanVar(value=True)
        fcb = ttk.Checkbutton(td, text="Filled ridges (joyplot)",
                              variable=self.wf3d_fill, command=self._redraw)
        fcb.pack(anchor="w")
        Tooltip(fcb, "Fill under each curve and draw front-to-back so "
                     "near ridges hide far ones. Removes the clutter.")
        self.wf3d_smooth = tk.BooleanVar(value=True)
        ttk.Checkbutton(td, text="Smoothed ridges", variable=self.wf3d_smooth,
                        command=self._redraw).pack(anchor="w")
        self.wf3d_clean = tk.BooleanVar(value=True)
        ttk.Checkbutton(td, text="Clean panes (white, faint grid)",
                        variable=self.wf3d_clean, command=self._redraw).pack(anchor="w")
        self.wf3d_alpha = tk.DoubleVar(value=0.5)
        sc, _e = self._slider_row(td, "Fill opacity", self.wf3d_alpha,
                                  0.1, 1.0, "%.2f")
        Tooltip(sc, "Transparency of each filled wall. Lower = more see-through.")
        self.wf3d_elev = tk.DoubleVar(value=22)
        self._slider_row(td, "Elev", self.wf3d_elev, 0, 90, "%.0f")
        self.wf3d_azim = tk.DoubleVar(value=-60)
        self._slider_row(td, "Azim", self.wf3d_azim, -180, 180, "%.0f")
        self.wf3d_zoom = tk.DoubleVar(value=1.0)
        scz, _z = self._slider_row(td, "Zoom", self.wf3d_zoom, 0.5, 2.0, "%.2f")
        Tooltip(scz, "Zoom the 3D view in/out (camera distance).")
        ttk.Button(td, text="Reset view", command=self._reset_3d_view).pack(
            anchor="w", pady=(2, 0))
        zr = ttk.Frame(td); zr.pack(fill="x")
        self.zclip_on = tk.BooleanVar(value=True)
        ttk.Checkbutton(zr, text="Z clip", variable=self.zclip_on,
                        command=self._redraw).pack(side="left")
        self.zmin, self.zmax = tk.StringVar(), tk.StringVar()
        ttk.Entry(zr, textvariable=self.zmin, width=6).pack(side="left", fill="x",
                                                            expand=True)
        ttk.Entry(zr, textvariable=self.zmax, width=6).pack(side="left", fill="x",
                                                            expand=True)
        ttk.Label(td, text="(z blank = auto: min..99th pct)",
                  font=("Segoe UI", 8), foreground="#666").pack(anchor="w")

    # ---- journal / figure controls ---------------------------------------
    def _build_journal(self, r):
        # Journal / figure
        jf = ttk.LabelFrame(r, text="Journal / figure", padding=6)
        jf.pack(fill="x", pady=3)
        pr = ttk.Frame(jf); pr.pack(fill="x")
        ttk.Label(pr, text="Size preset", width=10).pack(side="left")
        self.fig_preset = tk.StringVar(value="custom")
        ttk.Combobox(pr, textvariable=self.fig_preset, state="readonly",
                     values=["custom", "single 3.5in", "double 7.0in"]
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
        fr = ttk.Frame(jf); fr.pack(fill="x")
        for lbl, var, dv in [("Title", "title_fs", 13), ("Label", "label_fs", 11),
                             ("Tick", "tick_fs", 10)]:
            ttk.Label(fr, text=lbl).pack(side="left")
            v = tk.IntVar(value=dv); setattr(self, var, v)
            ttk.Spinbox(fr, from_=6, to=28, textvariable=v, width=3,
                        command=self._redraw).pack(side="left", padx=(0, 6))
        self.hide_spines = tk.BooleanVar()
        ttk.Checkbutton(jf, text="Hide top/right spines", variable=self.hide_spines,
                        command=self._redraw).pack(anchor="w")
        jsb = ttk.Button(jf, text="Apply journal style",
                         command=self._journal_style)
        jsb.pack(fill="x", pady=(4, 0))
        Tooltip(jsb, "One click: serif font, no grid, thin spines, "
                     "minor ticks - a clean publication look.")

    def _apply_size_preset(self):
        p = self.fig_preset.get()
        if p == "single 3.5in":
            self.fig_w.set("3.5"); self.fig_h.set("2.8")
        elif p == "double 7.0in":
            self.fig_w.set("7.0"); self.fig_h.set("5.0")
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
        header = ("stat   measurement @ pressure            "
                  "points (valid)   ->  output file")
        def logger(msg):
            self._logline(msg)
            if msg.lstrip().lower().startswith("found "):
                self._logline(header)
        try:
            results, skipped = engine.run(in_dir, dest, log=logger)
        except Exception as e:
            messagebox.showerror("Run failed", str(e))
            self.run_btn.config(state="normal"); return
        results.sort(key=lambda r: r["pressure_val"])
        self.results = results
        self.last_out_dir = dest
        self.smooth_cache.clear()
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

    def _channel(self, r, which):
        return {"absorbance": r["absorbance"], "sample": r["samp_c"],
                "background": r["bg_c"], "dark": r["dark_c"]}[which]

    def _smoothed(self, r):
        if r["label"] not in self.smooth_cache:
            self.smooth_cache[r["label"]] = smoothing.smooth_curve(
                r["wl"], r["absorbance"], self.smooth_params)
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
            self.ax.set_xlim(x0, x1)
        if y0 is not None and y1 is not None:
            self.ax.set_ylim(y0, y1)

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
            self.fig.colorbar(sm, ax=self.ax, label="Pressure (GPa)")
        elif self.legend_on.get() and entries:
            h, l = self._ordered_legend(entries)
            loc = self.legend_loc.get()
            kw = {"fontsize": max(6, int(self.font_size.get()) - 2),
                  "ncol": int(self.legend_cols.get())}
            if loc == "outside right":
                self.ax.legend(h, l, bbox_to_anchor=(1.02, 1),
                               loc="upper left", **kw)
            else:
                self.ax.legend(h, l, loc=loc, **kw)

    # ---- main redraw ------------------------------------------------------
    def _redraw(self, *args):
        try:
            self._redraw_inner()
        except Exception as e:
            self.cursor_lbl.config(text="draw error: " + str(e))
        self._sync_slider_entries()
        if hasattr(self, 'trace_count_lbl') and self.results:
            self.trace_count_lbl.config(
                text='showing %d / %d' % (len(self._shown()), len(self.results)))

    def _redraw_inner(self):
        from matplotlib.cm import ScalarMappable
        from matplotlib.colors import Normalize
        self._apply_font()
        self.fig.clf()
        unit = self.xunit.get()
        cmap_name = self.cmap.get()
        lw = float(self.lw.get())

        if self.wf_mode.get() == "3D ridge" and self.mode.get() == "overlay":
            self.ax = self.fig.add_subplot(111, projection="3d")
            self._draw_wf3d(unit, cmap_name, lw)
            # tight_layout fights 3D axes; use a fixed margin instead
            self.fig.subplots_adjust(left=0.02, right=0.98, top=0.96, bottom=0.04)
            self.canvas.draw_idle(); return

        self.ax = self.fig.add_subplot(111)
        if self.mode.get() == "inspect":
            self._draw_inspect(unit, lw)
        else:
            self._draw_overlay(unit, cmap_name, lw, ScalarMappable, Normalize)

        # axis labels
        self.ax.set_xlabel(self.xlabel_v.get() or unit_label(unit))
        if self.mode.get() == "inspect":
            yl = self.ylabel_v.get() or "Counts"
        else:
            yl = self.ylabel_v.get() or ("Absorbance"
                 if self.ydata.get() == "absorbance" else "Counts")
        self.ax.set_ylabel(yl)
        if self.title_v.get():
            self.ax.set_title(self.title_v.get())

        # vertical markers
        for xp in self._marker_positions(unit):
            self.ax.axvline(xp, color="#888", ls=":", lw=1)

        # top axis
        top = self.topaxis.get()
        if top != "none" and top != unit:
            try:
                fwd, inv = _conv(unit, top)
                sec = self.ax.secondary_xaxis("top", functions=(fwd, inv))
                sec.set_xlabel(unit_label(top))
            except Exception:
                pass

        if self.grid_on.get():
            self.ax.grid(True, alpha=0.3)
        self._cosmetics_2d()
        self._set_limits()
        if self.flipx.get():
            self.ax.invert_xaxis()
        self.fig.tight_layout()
        self._sync_limit_boxes()
        self.canvas.draw_idle()

    def _sync_limit_boxes(self):
        """When Auto is on, show the data limits in the X/Y min/max boxes."""
        if not self.autoscale.get():
            return
        x0, x1 = sorted(self.ax.get_xlim())
        y0, y1 = sorted(self.ax.get_ylim())
        self.xmin.set("%.4g" % x0); self.xmax.set("%.4g" % x1)
        self.ymin.set("%.4g" % y0); self.ymax.set("%.4g" % y1)

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
            mj = fv(majv)
            if mj:
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
        self.fig.set_facecolor(bg)
        self.ax.set_facecolor(bg)
        self.ax.title.set_size(int(self.title_fs.get())); self.ax.title.set_color(fg)
        self.ax.xaxis.label.set_size(int(self.label_fs.get()))
        self.ax.yaxis.label.set_size(int(self.label_fs.get()))
        self.ax.xaxis.label.set_color(fg); self.ax.yaxis.label.set_color(fg)
        self.ax.tick_params(colors=fg, labelsize=int(self.tick_fs.get()))
        for s in self.ax.spines.values():
            s.set_color(fg)
        leg = self.ax.get_legend()
        if leg:
            for t in leg.get_texts():
                t.set_color(fg)
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
                if self.show_raw.get():
                    self.ax.plot(x, y, color=color, lw=lw, ls=ls, alpha=0.18)
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
        from matplotlib.collections import PolyCollection
        shown = sorted(self._shown(), key=lambda r: r["pressure_val"])
        if not shown:
            return
        pvals = [r["pressure_val"] for r in shown]
        pmin, pmax = min(pvals), max(pvals)
        rev = self.cmap_rev.get()
        n = len(shown)
        chan = self.ydata.get()
        use_sm = chan == "absorbance" and (self.wf3d_smooth.get()
                                           or self.show_smooth.get())

        # gather (x, z) per ridge
        ridges = []
        allz = []
        for r in shown:
            x = unit_x(r, unit)
            z = self._smoothed(r) if use_sm else self._channel(r, chan)
            m = np.isfinite(x) & np.isfinite(z)
            ridges.append((x[m], z[m]))
            allz.append(z[m])
        allz = np.concatenate(allz) if allz else np.array([0.0, 1.0])

        # z-clip range (auto = min .. 99th pct unless user sets values)
        def fv(s):
            try:
                return float(s.get())
            except (ValueError, tk.TclError):
                return None
        if self.zclip_on.get():
            zlo = fv(self.zmin); zhi = fv(self.zmax)
            if zlo is None:
                zlo = float(np.nanmin(allz))
            if zhi is None:
                zhi = float(np.nanpercentile(allz, 99))
        else:
            zlo, zhi = float(np.nanmin(allz)), float(np.nanmax(allz))

        even = self.wf3d_even.get()
        ypos = list(range(n)) if even else pvals

        if self.wf3d_fill.get():
            # joyplot: one filled polygon per ridge, placed at y via zdir='y'.
            verts, faces, edges, styles = [], [], [], []
            for rank, (r, (x, z)) in enumerate(zip(shown, ridges)):
                if len(x) < 2:
                    verts.append([(0, zlo), (1, zlo)]); faces.append((1, 1, 1, 0))
                    edges.append((0, 0, 0, 0)); styles.append("-"); continue
                zc = np.clip(z, zlo, zhi)
                poly = [(x[0], zlo)] + list(zip(x, zc)) + [(x[-1], zlo)]
                verts.append(poly)
                cr = (n - 1 - rank) if rev else rank
                cmin, cmax = (pmax, pmin) if rev else (pmin, pmax)
                col = colormaps.color_for(cmap_name, r["pressure_val"], cmin,
                                          cmax, cr, n)
                faces.append((col[0], col[1], col[2], float(self.wf3d_alpha.get())))
                edges.append((0, 0, 0, 0.9))
                styles.append("--" if self._branch_of(r) == "D" else "-")
            pc = PolyCollection(verts, facecolors=faces, edgecolors=edges,
                                linewidths=lw, linestyles=styles)
            pc.set_clip_on(False)   # zoom must not chop ridges at the panel edge
            # zsort default draws back-to-front so near ridges occlude far ones
            self.ax.add_collection3d(pc, zs=ypos, zdir="y")
            self.ax.set_xlim(
                np.nanmin([xx.min() for xx, _ in ridges if len(xx)]),
                np.nanmax([xx.max() for xx, _ in ridges if len(xx)]))
            self.ax.set_ylim(min(ypos), max(ypos))
            zpad = (zhi - zlo) * 0.03 or 0.01
            self.ax.set_zlim(zlo - zpad, zhi + zpad)
        else:
            for rank, (r, (x, z)) in enumerate(zip(shown, ridges)):
                cr = (n - 1 - rank) if rev else rank
                cmin, cmax = (pmax, pmin) if rev else (pmin, pmax)
                col = colormaps.color_for(cmap_name, r["pressure_val"], cmin,
                                          cmax, cr, n)
                ls = "--" if self._branch_of(r) == "D" else "-"
                ln = self.ax.plot(x, np.full_like(x, ypos[rank]),
                                  np.clip(z, zlo, zhi), color=col, lw=lw, ls=ls)
                for _l in ln:
                    _l.set_clip_on(False)
            zpad = (zhi - zlo) * 0.03 or 0.01
            self.ax.set_zlim(zlo - zpad, zhi + zpad)

        # even-spacing ticks show the real pressures
        if even:
            self.ax.set_yticks(list(range(n)))
            self.ax.set_yticklabels(["%.1f" % p for p in pvals])

        bg, fg = self._mpl_colors()
        self.fig.set_facecolor(bg)
        self.ax.set_facecolor(bg)
        self.ax.view_init(elev=float(self.wf3d_elev.get()),
                          azim=float(self.wf3d_azim.get()))
        self.ax.set_xlabel(self.xlabel_v.get() or unit_label(unit),
                          fontsize=int(self.label_fs.get()), color=fg)
        self.ax.set_ylabel("Pressure (GPa)", fontsize=int(self.label_fs.get()),
                          color=fg)
        self.ax.set_zlabel(self.ylabel_v.get() or ("Absorbance"
                           if chan == "absorbance" else "Counts"),
                          fontsize=int(self.label_fs.get()), color=fg)
        self.ax.tick_params(labelsize=int(self.tick_fs.get()), colors=fg)
        self._apply_axis_ticks(self.ax, is3d=True)
        if self.title_v.get():
            self.ax.set_title(self.title_v.get(),
                              fontsize=int(self.title_fs.get()), color=fg)

        if self.wf3d_clean.get():
            pane_bg = "#2b2e36" if self.dark_mode.get() else "white"
            pane_edge = "#4a4f59" if self.dark_mode.get() else "#cccccc"
            for pane in (self.ax.xaxis, self.ax.yaxis, self.ax.zaxis):
                pane.pane.set_facecolor(pane_bg)
                pane.pane.set_edgecolor(pane_edge)
                pane.pane.set_alpha(1.0)
            self.ax.grid(True)
        try:
            self.ax.set_box_aspect((1.7, 1.2, 0.6),
                                   zoom=float(self.wf3d_zoom.get()))
        except TypeError:
            try:
                self.ax.set_box_aspect((1.7, 1.2, 0.6))
            except Exception:
                pass
        except Exception:
            pass

        # legend / colorbar work in 3D too (respect the toggles)
        if self.colorbar_on.get() and not colormaps.is_categorical(cmap_name):
            from matplotlib.cm import ScalarMappable
            from matplotlib.colors import Normalize
            sm = ScalarMappable(norm=Normalize(pmin, pmax),
                                cmap=colormaps._continuous_cmap(cmap_name))
            sm.set_array([])
            cb = self.fig.colorbar(sm, ax=self.ax, label="Pressure (GPa)",
                                   shrink=0.6, pad=0.08)
            cb.ax.yaxis.label.set_color(fg); cb.ax.tick_params(colors=fg)
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
            kw = {"fontsize": max(6, int(self.tick_fs.get()) - 1),
                  "ncol": int(self.legend_cols.get())}
            if loc == "outside right":
                leg = self.ax.legend(h, l, bbox_to_anchor=(1.02, 1),
                                     loc="upper left", **kw)
            else:
                leg = self.ax.legend(h, l, loc=loc, **kw)
            if leg:
                leg.get_frame().set_alpha(0.85)
                for t in leg.get_texts():
                    t.set_color(fg)

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
            ax2.plot(x, r["absorbance"], color="#d62728", lw=lw, label="Absorbance")
            ax2.set_ylabel("Absorbance")
            h2, l2 = ax2.get_legend_handles_labels()
            if self.legend_on.get():
                self.ax.legend(h1 + h2, l1 + l2, fontsize=8, loc="best")
        elif self.legend_on.get():
            self.ax.legend(fontsize=8, loc="best")
        if not self.title_v.get():
            self.ax.set_title(self.inspect_p.get() + "  (channels)")


    # ---- smoothing settings (resizable + scrollable; Apply pinned) -------
    def _open_smooth_panel(self):
        win = tk.Toplevel(self.root)
        win.title("Smoothing settings (Igor 5-step)")
        win.geometry("400x620"); win.minsize(360, 360)
        p = self.smooth_params
        vars_ = {}
        bottom = ttk.Frame(win, padding=6); bottom.pack(side="bottom", fill="x")
        canv = tk.Canvas(win, highlightthickness=0)
        sb = ttk.Scrollbar(win, command=canv.yview)
        body = ttk.Frame(canv)
        body.bind("<Configure>",
                  lambda e: canv.configure(scrollregion=canv.bbox("all")))
        canv.create_window((0, 0), window=body, anchor="nw")
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

        def apply():
            try:
                for k, v in vars_.items():
                    if isinstance(v, tk.BooleanVar):
                        self.smooth_params[k] = v.get()
                    else:
                        val = float(v.get())
                        self.smooth_params[k] = int(val) if k in ints else val
            except ValueError:
                messagebox.showerror("Smoothing", "Numeric fields must be numbers.")
                return
            self.smooth_cache.clear(); self.show_smooth.set(True)
            self._redraw(); win.destroy()

        ttk.Button(bottom, text="Apply", command=apply).pack(side="right")
        ttk.Button(bottom, text="Cancel", command=win.destroy).pack(side="right",
                                                                    padx=6)

    # ---- presets ----------------------------------------------------------
    def _preset_registry(self):
        return {
            "mode": self.mode, "ydata": self.ydata, "xunit": self.xunit,
            "flipx": self.flipx, "topaxis": self.topaxis,
            "autoscale": self.autoscale, "xmin": self.xmin, "xmax": self.xmax,
            "ymin": self.ymin, "ymax": self.ymax, "cmap": self.cmap,
            "cmap_rev": self.cmap_rev, "grid_on": self.grid_on, "lw": self.lw,
            "wf_mode": self.wf_mode, "wf_step": self.wf_step,
            "wf_label": self.wf_label, "show_smooth": self.show_smooth,
            "show_raw": self.show_raw, "markers": self.markers,
            "title": self.title_v, "xlabel": self.xlabel_v, "ylabel": self.ylabel_v,
            "legend_on": self.legend_on, "colorbar_on": self.colorbar_on,
            "legend_loc": self.legend_loc, "legend_cols": self.legend_cols,
            "font_family": self.font_family, "font_size": self.font_size,
            "dpi": self.dpi, "ins_S": self.ins_S, "ins_B": self.ins_B,
            "ins_D": self.ins_D, "ins_A": self.ins_A,
            "wf3d_even": self.wf3d_even, "wf3d_fill": self.wf3d_fill,
            "wf3d_smooth": self.wf3d_smooth, "wf3d_clean": self.wf3d_clean,
            "wf3d_elev": self.wf3d_elev, "wf3d_azim": self.wf3d_azim,
            "wf3d_alpha": self.wf3d_alpha, "wf3d_zoom": self.wf3d_zoom,
            "aspect_mode": self.aspect_mode, "aspect_w": self.aspect_w,
            "aspect_h": self.aspect_h,
            "zclip_on": self.zclip_on, "zmin": self.zmin, "zmax": self.zmax,
            "fig_w": self.fig_w, "fig_h": self.fig_h, "fig_preset": self.fig_preset,
            "title_fs": self.title_fs, "label_fs": self.label_fs,
            "tick_fs": self.tick_fs, "hide_spines": self.hide_spines,
            "minor_ticks": self.minor_ticks, "tick_dir": self.tick_dir,
            "xmaj": self.xmaj, "xminor": self.xminor, "ymaj": self.ymaj,
            "yminor": self.yminor, "zmaj": self.zmaj, "zminor": self.zminor,
            "ticks_allsides": self.ticks_allsides,
            "tick_len_major": self.tick_len_major,
            "tick_len_minor": self.tick_len_minor, "tick_width": self.tick_width,
        }

    def _refresh_presets(self):
        names = sorted(self.settings.get("presets", {}).keys())
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
        data = self.settings.get("presets", {}).get(name)
        if not data:
            messagebox.showinfo("Presets", "Pick a saved preset first."); return
        self._apply_preset_data(data)
        self._logline("Loaded preset '%s'" % name)

    def _delete_named_preset(self):
        name = self.preset_sel.get()
        presets = self.settings.get("presets", {})
        if name in presets and messagebox.askyesno(
                "Delete preset", "Delete preset '%s'?" % name):
            del presets[name]; self._save_settings()
            self.preset_sel.set(""); self._refresh_presets()

    def _reset_defaults(self):
        if not messagebox.askyesno("Reset", "Reset all controls to defaults?"):
            return
        self.smooth_params = dict(smoothing.DEFAULTS); self.smooth_cache.clear()
        self._apply_preset_data(dict(self._defaults))

    def _set_cmap_default(self):
        self.settings["cmap_default"] = self.cmap.get()
        self._save_settings()
        self._logline("Default colormap set to '%s'" % self.cmap.get())

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
        self.fig.savefig(path, dpi=int(self.dpi.get()), bbox_inches="tight",
                         facecolor=self.fig.get_facecolor())
        self.fig.set_size_inches(old)       # restore so the live canvas is intact
        self.canvas.draw_idle()
        self._logline("Saved plot -> " + path)

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
            y = (self._smoothed(r) if self.show_smooth.get() else r["absorbance"])
            a.plot(x, y, ls=ls, color="#205070")
            a.set_xlabel(unit_label(unit)); a.set_ylabel("Absorbance")
            a.set_title(r["label"])
            if self.flipx.get():
                a.invert_xaxis()
            name = re.sub(r"[^A-Za-z0-9.+-]+", "_", r["label"]) + ".png"
            fig.tight_layout(); fig.savefig(os.path.join(folder, name),
                                            dpi=int(self.dpi.get()))
        self._logline("Batch-exported %d PNG(s) -> %s" % (len(shown), folder))

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
