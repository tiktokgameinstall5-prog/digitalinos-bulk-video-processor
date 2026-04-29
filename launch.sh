#!/usr/bin/env bash
# Digitalinos Video Batch Pro - one-click Linux/macOS launcher.
#
# First run: creates .venv and installs requirements. Subsequent runs: just
# launches the app. Requires Python 3.9+ and FFmpeg on PATH.

set -e
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
    echo "[ERROR] python3 not found. Install Python 3.9+ and retry."
    exit 1
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "[WARNING] ffmpeg not found on PATH. Processing will fail until you install it."
    sleep 2
fi

if [ ! -x ".venv/bin/python" ]; then
    echo "Creating virtual environment (first-time setup)..."
    python3 -m venv .venv
    # shellcheck disable=SC1091
    . .venv/bin/activate
    python -m pip install --upgrade pip
    pip install -r requirements.txt
else
    # shellcheck disable=SC1091
    . .venv/bin/activate
fi

echo "Launching Digitalinos Video Batch Pro..."
python run.py
