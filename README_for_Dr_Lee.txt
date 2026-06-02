DAC QUICK-LOOK  --  how to use
NSLS-II 22-IR-1 visible absorption, Lee Lab

No Python needed. Just the files in this folder.

START
  Double-click the "DAC Quick-Look" desktop shortcut (or DAC_QuickLook.exe in
  this folder). If Windows shows "Windows protected your PC": click
  "More info" then "Run anyway". This is normal for in-house tools.

USE
  1. Input folder  -> Browse to the folder of raw spectrometer files for one
                      experiment (the vis_..._.001 etc. files).
  2. Output folder -> Browse to where the absorbance CSVs should be saved.
  3. Click Run. The Progress box lists every measurement processed and every
     file skipped (with the reason). One CSV is written per pressure point.
  4. The plot shows all pressures overlaid. On the right you can:
       - show/hide individual pressures
       - switch Wavelength <-> Wavenumber, flip the X axis
       - change colormap and line width
       - turn on Smoothing (and open its settings to tune the 5 steps)
       - Save plot... to PNG / PDF / SVG
     The toolbar under the plot also gives zoom, pan, and save.

FILE NAMING THE TOOL EXPECTS
  vis_{DAC}_{Sample}_{Pressure}[_bg|_s][_2|_3][_C|_D].{segment}
    no suffix = dark,  _bg = background,  _s = sample
    _2 / _3   = a retake (the tool uses the LATEST retake)
    _C / _D   = optional compression / decompression tag. If you add it, the
                tool auto-sorts the branch (D is dashed). Otherwise set D by
                hand per trace on the right, or it auto-fills for known runs.
    segment   = 001..004 (the four grating captures, joined automatically)
    Pressure  = uses 'p' for the decimal, e.g. 1p39 = 1.39 GPa
  Files that do not match are skipped and listed in the Progress box.

PLOT CONTROLS (right panel)
  Overlay all pressures or inspect one (Sample/Background/Dark + Absorbance).
  X axis nm / cm-1 / eV with optional top axis; flip X; axis min/max boxes.
  Colormap (+reverse), grid, line width, fonts. Waterfall: 2D stacked or 3D.
  Smoothing (5-step) with its own settings window. Vertical wavelength markers.
  Save/load figure presets. Save plot, copy figure, batch-export PNGs, and
  export smoothed CSVs (with optional crop).

WHAT THIS DOES NOT DO
  No defringe / notch / thickness normalization. This is the quick-look /
  concatenation step only. Downstream processing stays separate.

Questions: Nhan.
