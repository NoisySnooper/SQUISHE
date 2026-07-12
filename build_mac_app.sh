#!/bin/bash
# Build a native macOS .app of the Beamline DAC Data Tool.
# Run this ON a Mac. The app builds for that Mac's architecture:
# Apple Silicon -> arm64 app, Intel -> x86_64 app (build one per kind).
# Result: dist/Beamline DAC Data Tool.app
set -e
cd "$(dirname "$0")"
python3 -m venv build-env-mac
source build-env-mac/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller
pyinstaller --windowed --noconfirm --name "Beamline DAC Data Tool" \
    --collect-data cmcrameri app.py
echo
echo "Done: dist/Beamline DAC Data Tool.app"
echo "The app is unsigned: first launch is right-click -> Open (Gatekeeper)."
