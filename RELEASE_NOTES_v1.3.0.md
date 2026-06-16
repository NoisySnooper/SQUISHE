# Beamline DAC Data Tool v1.3.0 ("Olivine")

A maintenance-plus-features release focused on the 3D ridge view, clearer
legend/colorbar controls, theme consistency, and a tidier UI.

## 3D ridge

- **Log-Z (absorbance) scale.** Plot the absorbance axis on a base-10 log
  scale; non-positive values are dropped cleanly.
- **Reference-guide planes from the 2D markers.** Each vertical (spectral)
  marker becomes a translucent X-plane and each horizontal (absorbance) marker
  becomes a Z-plane inside the 3D box. Color, style, width, and opacity reuse
  the existing marker controls and honor the log-Z transform; markers outside
  the spectral range are skipped.

## Plotting and styling

- **Trace colors are locked to the full loaded dataset**, so a curve keeps its
  color when you toggle other traces on and off instead of re-mapping.
- **Legend / Colorbar / Reference-guides split into their own Style sections.**
- **Optional user-typed legend title** with its own font-size control.
- **Limits and scale on one row:** an Auto checkbox alongside "Apply limits" and
  "Reset axes." Typing a limit or clicking "Apply limits" turns Auto off so your
  zoom sticks; "Reset axes" clears the boxes and re-enables fit-to-data.
- **Quick-access "Reset view"** now resets both the 2D zoom/pan and the 3D
  camera.

## Theme consistency

- Caret, title, and quick-access strip backgrounds now follow the active theme.
- True-black theme corrected; accent tint is on by default.

## Defringe

- **FFT-notch refinements:** the n·t peak-search window and the acceptance
  p-value threshold can now be supplied per call (defaults unchanged, so
  existing behavior is identical).

## UI and data export

- **Export D-list (CSV) by selection** — writes the pressure list back out in
  the same format "Load D list" reads.
- General decluttering and consistency passes on the tick / grid / marker
  controls.

## Packaging

- `pywin32` is now marked Windows-only in `requirements.txt`
  (`pywin32; sys_platform == "win32"`) — a no-op on Windows, but it lets the
  dependencies install on other platforms.

## Build

Windows onedir build via PyInstaller (`beamline_tool.spec`). Ship the whole
`DAC_QuickLook` folder; run `DAC_QuickLook.exe` inside it. The onedir layout
starts faster and is less likely to trip antivirus / SmartScreen than a single
packed exe. On first launch SmartScreen may warn (expected for any unsigned
exe): **More info -> Run anyway.**
