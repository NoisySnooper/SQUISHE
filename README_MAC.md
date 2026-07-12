# Beamline DAC Data Tool on macOS

The tool is pure Python (tkinter / numpy / scipy / matplotlib) and runs on
macOS. Windows-only bits (Explorer open, clipboard copy) have macOS
equivalents built in.

## Requirement (one time)

Install Python 3.11 or newer from https://www.python.org/downloads/macos/
(the python.org installer bundles Tk, which the GUI needs). Homebrew Python
works too if you also `brew install python-tk`.

## Option A - run from source (recommended, 2 minutes)

1. Unzip the tool folder anywhere user-writable (Desktop, Documents).
2. In Terminal, once:  `cd` to the folder, then `chmod +x run_mac.command build_mac_app.sh`
3. Double-click `run_mac.command`. First run builds a local environment
   (1-2 min); after that it starts in seconds.

## Option B - native .app

On the Mac that will use it, run `./build_mac_app.sh`. The app lands in
`dist/Beamline DAC Data Tool.app`; drag it to Applications if you like.
First launch: right-click -> Open (it is unsigned, Gatekeeper will warn).
Note: an Apple Silicon build does not run on Intel Macs and vice versa;
build on the machine kind you will use.

## Processing without the GUI

`engine.py` has no GUI dependencies. From any OS with numpy installed:

    python engine.py <input_folder> <output_folder>

writes the same absorbance CSVs as the Run button.

## Known macOS differences

- Fonts: the UI specifies Segoe UI; macOS substitutes the system font.
  Cosmetic only.
- Settings (.quicklook_settings.json) save next to the tool, same as
  Windows, so keep the folder somewhere writable.

Questions or anything misbehaving: send Nhan the log text and a screenshot.
