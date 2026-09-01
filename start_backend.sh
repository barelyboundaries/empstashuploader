#!/usr/bin/env bash
# start_backend.sh — one-command sidecar start for the distribution repo.
# Uses the venv that install.sh created under plugin/.venv.  Does NOT
# auto-create a venv; if the user hasn't run the installer yet, print a
# message and exit non-zero.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$SCRIPT_DIR/plugin/.venv"
PYTHON="$VENV/bin/python"

# --- venv gate ---
if [ ! -x "$PYTHON" ]; then
    echo "ERROR: Virtual environment not found at $VENV" >&2
    echo "Run ./install.sh first to set up the environment." >&2
    exit 1
fi

# --- PYTHONPATH: prefer repo checkout backend/, else plugin/ (vendored layout) ---
if [ -d "$SCRIPT_DIR/backend" ]; then
    PYTHONPATH="$SCRIPT_DIR/backend"
    APP_DIR="$SCRIPT_DIR/backend"
else
    PYTHONPATH="$SCRIPT_DIR/plugin"
    APP_DIR="$SCRIPT_DIR/plugin"
fi
export PYTHONPATH

# --- ffmpeg: EMPORNIUM_FFMPEG_DIR wins; fall back to ~/.stash ---
FFMPEG_DIR="${EMPORNIUM_FFMPEG_DIR:-}"
if [ -n "$FFMPEG_DIR" ] && [ -x "$FFMPEG_DIR/ffmpeg" ]; then
    :
elif [ -d "$HOME/.stash" ] && [ -x "$HOME/.stash/ffmpeg" ]; then
    FFMPEG_DIR="$HOME/.stash"
else
    FFMPEG_DIR=""
fi
if [ -n "$FFMPEG_DIR" ]; then
    export PATH="$FFMPEG_DIR:$PATH"
fi

# --- launch ---
exec "$PYTHON" -m uvicorn empornium_megapack.main:app \
    --host 127.0.0.1 --port 9941 --app-dir "$APP_DIR"
