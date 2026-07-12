# SQUISHE

*Spectral QUick-look for In-Situ High-pressure Experiments* — reduction and
publication-quality visualization of visible / near-IR optical-absorption
spectra from diamond-anvil-cell experiments (developed for NSLS-II beamline
22-IR-1; formerly the Beamline DAC Data Tool).

![SQUISHE — 19-pressure absorbance series in the paper theme](docs/screenshot.png)

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
  icon set, themed title bar, eight themes including true-black and paper,
  and an adjustable interface text size.

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
| `DESIGN_SQUISHE.md` | the visual design system |

## Version history

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
