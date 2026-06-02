# Beamline DAC Data Tool (NSLS-II 22-IR-1)

A standalone Windows tool for the visible-light absorption workflow on
diamond-anvil-cell (DAC) samples at NSLS-II beamline 22-IR-1. It concatenates
the four raw grating segments of each measurement, computes absorbance, and
plots the results with publication-ready 2D and 3D options.

## What it does

- Reads raw spectrometer segment files (`vis_{DAC}_{Sample}_{Pressure}[...].001..004`).
- Concatenates the four segments per measurement and computes
  `A = -log10[(Sample - Dark) / (Background - Dark)]`.
- Writes one absorbance CSV per pressure into an auto-named output subfolder.
- Interactive plotting: overlay, inspect-one-pressure, 2D stacked waterfall,
  and a filled 3D ridge (joyplot) view.
- Crameri perceptually-uniform colormaps, smoothing, journal styling,
  per-axis tick control, light/dark mode, and named presets.

## Run from source

```
pip install -r requirements.txt
python app.py
```

Or double-click `run.bat`.

## Build a standalone .exe (no Python needed on the target PC)

```
python -m venv build-env
build-env\Scripts\activate
pip install -r requirements.txt
pyinstaller beamline_tool.spec
```

The result is a self-contained folder at `dist\DAC_QuickLook\`. Ship the whole
folder; the program is `DAC_QuickLook.exe` inside it. A onedir build (rather
than a single packed .exe) is used deliberately: it starts faster and is far
less likely to be flagged by antivirus / SmartScreen.

If Windows SmartScreen warns on first launch (expected for any unsigned exe):
More info -> Run anyway. To remove the warning entirely, sign the exe with a
code-signing certificate.

## Files

| File | Purpose |
|------|---------|
| `app.py` | GUI, plotting, all controls |
| `engine.py` | parse / concatenate / absorbance / CSV output |
| `smoothing.py` | 5-step smoothing pipeline |
| `colormaps.py` | Crameri + matplotlib colormaps |
| `decomp.py` | known decompression-pressure sets |
| `beamline_tool.spec`, `version_info.txt` | PyInstaller build config |
| `requirements.txt` | dependencies |

## License

Internal lab tool. All rights reserved by the authors.
