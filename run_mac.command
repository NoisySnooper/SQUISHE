#!/bin/bash
# SQUISHE - macOS launcher. Double-click to run.
# First time only, in Terminal in this folder:  chmod +x run_mac.command
# If you see "bad interpreter", run it as:      bash run_mac.command
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 not found. Install Python 3.11+ from python.org first."
    read -n 1 -p "Press any key to close."; exit 1
fi
if [ ! -d ".venv" ]; then
    echo "First run: creating the Python environment (1-2 minutes)..."
    python3 -m venv .venv
fi
source .venv/bin/activate
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt
python app.py
