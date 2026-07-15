# SQUISHE

*Spectral QUick-look for In-Situ High-pressure Experiments* — reduction and
publication-quality visualization of visible / near-IR optical-absorption
spectra from diamond-anvil-cell experiments (developed for NSLS-II beamline
22-IR-1; formerly the Beamline DAC Data Tool).

![SQUISHE — filled 3D ridge view of a 19-pressure absorbance series](docs/screenshot.png)

## What it does

- Reads raw spectrometer segments, concatenates the grating segments of each
  measurement, and computes `A = -log10[(Sample - Dark) / (Background - Dark)]`,
  writing one absorbance CSV per pressure point.
- **Flexible filename ingestion**: the classic
  `vis_{DAC}_{Sample}[_{P}][_bg|_s][_C|_D][_n][.{seq}]` convention works with
  zero setup, and any other naming scheme can be taught from a single example
  file ("Name format" editor: label the pieces of a real filename, watch the
  whole folder validate live, fix or exclude stubborn files by hand, save the
  grammar as a reusable profile).
- **Removes diamond-anvil interference fringes** (FFT-notch defringe with a
  Fisher g-test acceptance gate) and applies the lab's 5-step
  Savitzky-Golay smoothing pipeline.
- Interactive plotting: overlay, inspect-one-pressure, 2D stacked waterfall,
  and a filled 3D ridge view with camera presets, keyboard orbit, box-frame
  options, and per-axis stretch.

## Highlights (v1.4)

- **Multi-tab sessions**: browser-style tabs, each with independent data,
  folders, settings, and undo history — compare loads side by side.
- **Raw data table**: a resizable spreadsheet under the plot (wavelength,
  wavenumber, absorbance, raw channels, defringed and smoothed columns) with
  copy-as-TSV and open-in-Excel.
- **Journal figure presets**: one pick applies a publisher's current column
  width and house style with a WYSIWYG preview at the exact export size. 
- **Publication controls**: color-block legend keys or an automatic pressure
  colorbar for dense series, title and axis-label positioning, per-axis label
  gaps and spine widths, Crameri perceptually-uniform colormaps.
- **Traceability**: every reduction and export writes a JSON provenance
  sidecar (tool version, parameters, timestamps, file hashes).
- **A designed interface**: the SQUISHE visual system — bundled Jost
  typeface (OFL), per-theme accent triads across every control, geometric
  icon set, themed title bar and top banner, a named theme set (from clean
  Standard Light and true-black to Rainbow, Coast Guard, and more), and an
  adjustable interface text size.

## New in v1.4.5

- **Themes, expanded**: a named theme set in the top-bar Theme menu —
  Standard Light, Flashbang White, Kinda Dark, Black Hole, and the accent
  themes Semper Paratus, Touch Grass, Pink Pony Club, Davy Jones, New
  Mexico, Ocean, Rainbow, Synthwave, Christmas, and Tet — each with a
  themed top banner and per-section accent colors down the settings panels.
- **Sturdier reduction**: a data-integrity pass — grating channels aligned
  by wavelength (not array index), tolerant file decoding, non-finite
  pressures rejected, and a re-entrancy guard so a mid-run rescan can't
  corrupt output — with the test suite grown to 53.
- **3D axis frame** now genuinely draws on top at every camera angle
  (occlusion-proof), and an all-hidden 3D view shows a themed empty state
  instead of a blank white box.
- **Interface polish**: readable dropdown lists in every theme, uniform
  button spacing, a grab handle on each panel divider, and field text that
  stays legible on light fields in dark themes.

## New in v1.4.4

- **Direct labels at curves**: label every curve at its end with its
  pressure, in the trace color, instead of a legend box (2D overlay,
  2D stacked, and 3D ridge).
- **Legend frame control**: border opacity independent of background
  opacity, edge color, and up to 16 columns.
- **"3 axes" box frame** is the new 3D default: the classic matplotlib
  look with just the x / y / z tick axes facing you, always drawn on top
  so ridge walls can't hide them; custom mode can force them into any
  edge mix.
- In-app guide, About page, and tooltips brought up to date with all of
  the above (and stale claims removed).

## New in v1.4.3

- **Work the plot directly**: click a curve to select it, double-click to
  solo it, click a legend entry to hide it, right-click a curve for quick
  actions (inspect that pressure, hide, mark decompression, defringe
  compare, open the data table) that drive the matching panel controls.
- **Guess format**: the Name format dialog can read the input folder and
  propose the whole filename grammar automatically; correct anything wrong
  in the live preview, then adopt it.
- **Quality flags**: every reduced point gets quick checks (saturation,
  missing channels, negative absorbance); flagged traces show a colored
  dot with the reason on hover.
- **Rescan** re-reduces only when new files appeared in the input folder
  (the between-measurements top-up), and **defringe compare** overlays the
  selected trace's pre-defringe curve in gray.
- **Adaptive interface**: text size follows screen resolution and Windows
  display scale (manual 9-15 override), DPI-proof panel widths, a flat
  hairline-card look, and a themed-plot-background toggle.
- **3D box & panes**: frame modes (open front, floor only, custom
  per-edge), frame shade and width, pane color and opacity; "none" now
  removes every edge.
- Smoothing defaults retuned on real 22-IR-1 spectra (Savitzky-Golay
  windows 201/101 -> 101/51: keeps ~97% of the noise suppression at a
  third of the absorption-edge distortion; the other steps stay verbatim
  Igor).

## Run from source

```
pip install -r requirements.txt
python app.py
```

Or double-click `run.bat`. Windows 10/11; the bundled Jost typeface loads
privately at startup (no font installation).

## Build a standalone .exe (no Python needed on the target PC)

```
python -m venv build-env
build-env\Scripts\activate
pip install -r requirements.txt
pyinstaller beamline_tool.spec
```

The result is a self-contained folder at `dist\DAC_QuickLook\`. Ship the whole
folder. A onedir build (rather than a single packed .exe) is used
deliberately: it starts faster and is far less likely to be flagged by
antivirus / SmartScreen. If SmartScreen warns on first launch (expected for
any unsigned exe): More info -> Run anyway.

## Files

| File | Purpose |
|------|---------|
| `app.py` | GUI, plotting, all controls |
| `engine.py` | parse / concatenate / absorbance / naming profiles / CSV + provenance |
| `defringe.py` | FFT-notch defringe (interference-fringe removal) |
| `smoothing.py` | 5-step smoothing pipeline |
| `colormaps.py` | Crameri + matplotlib colormaps |
| `decomp.py` | known decompression-pressure sets |
| `fonts/` | Jost typeface (OFL license included) |
| `tests/` | pytest suite (parser, engine, sessions, plotting) |
| `beamline_tool.spec`, `version_info.txt` | PyInstaller build config |

## Version history

- **v1.4.5** — expanded, named theme set with themed banners and
  per-section accent colors; data-integrity hardening (channel
  wavelength-alignment, tolerant decoding, re-entrancy guard) and a
  53-test suite; occlusion-proof 3D axis frame; readable dropdowns in
  every theme and interface polish.
- **v1.4.4** — direct trace labels (2D + 3D); independent legend border
  opacity and up to 16 columns; "3 axes" box frame (new 3D default,
  occlusion-proof); refreshed in-app guides.
- **v1.4.3** — clickable plot (select / solo / hide / right-click quick
  actions); filename-grammar auto-guess; per-point quality flags; Rescan;
  defringe compare; adaptive text size and DPI-proof layout; hairline-card
  design; 3D box-frame and pane controls; smoothing defaults retuned for
  22-IR-1.
- **v1.4** — SQUISHE rebrand and visual system; multi-tab sessions; flexible
  filename ingestion; raw data table; journal presets with WYSIWYG preview;
  provenance sidecars; 2D/3D view controls; 43-test suite.
- **v1.3** — reference-guide planes, log-Z ridge, locked trace colors,
  legend/colorbar styling split, adjustable defringe gates.
- **v1.2** — FFT-notch defringe (contributed by
  [Matthew Diamond](https://github.com/matthewrdiamond)); 3D ridge stretch,
  camera presets, projections.
- **v1.1** — per-item text sizes, colorbar customization, performance mode.

## Credits

- FFT-notch defringe (`defringe.py`) contributed by
  [Matthew Diamond](https://github.com/matthewrdiamond).
- Developed in Dr. Kanani K. M. Lee's lab for NSLS-II beamline 22-IR-1.

## License

Internal lab tool. All rights reserved by the authors.
