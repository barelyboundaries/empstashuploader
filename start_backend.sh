#!/usr/bin/env bash
# start_backend.sh — one-command sidecar start for the distribution repo.
# Uses the venv that install.sh created (repo layout: plugin/.venv;
# shipped zip layout: .venv beside the plugin files at the root).  Does NOT
# auto-create a venv; if the user hasn't run the installer yet, print a
# message and exit non-zero.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# --- layout detection (mirrors install.sh): manifest presence, never CWD ---
# Repo checkout: plugin tree lives in a plugin/ subfolder beside this script.
# Shipped zip:   plugin files sit at the root BESIDE this script (flat layout).
if [ -f "$SCRIPT_DIR/plugin/empornium-megapack.yml" ]; then
    PLUGIN_DIR="$SCRIPT_DIR/plugin"
else
    PLUGIN_DIR="$SCRIPT_DIR"
fi
VENV="$PLUGIN_DIR/.venv"
PYTHON="$VENV/bin/python"

# --- venv gate ---
if [ ! -x "$PYTHON" ]; then
    echo "ERROR: Virtual environment not found at $VENV" >&2
    echo "Run ./install.sh first to set up the environment." >&2
    exit 1
fi

# --- PYTHONPATH: repo checkout backend/, else the plugin dir (vendored layout) ---
if [ -d "$SCRIPT_DIR/backend" ]; then
    PYTHONPATH="$SCRIPT_DIR/backend"
    APP_DIR="$SCRIPT_DIR/backend"
else
    PYTHONPATH="$PLUGIN_DIR"
    APP_DIR="$PLUGIN_DIR"
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

# --- port gate: fail fast with a friendly message when 9941 is occupied ---
# (--host 127.0.0.1 --port 9941 are FIXED, documented constants; never dynamic.)
# CONNECT probe (connect_ex == 0 means something accepted), mirroring the
# TcpClient probe in start_backend.ps1: a bind probe can false-negative where
# SO_REUSEADDR permits overlapping binds.
PORT=9941
if "$PYTHON" -c "import socket; s = socket.socket(); rc = s.connect_ex(('127.0.0.1', $PORT)); s.close(); raise SystemExit(rc)" 2>/dev/null; then
    echo "ERROR: Port $PORT is already in use - another process is listening on 127.0.0.1:$PORT." >&2
    echo "Stop that process (or the already-running sidecar) and retry: ss -ltnp | grep :$PORT" >&2
    exit 1
fi

# --- launch ---
exec "$PYTHON" -m uvicorn empornium_megapack.main:app \
    --host 127.0.0.1 --port 9941 --app-dir "$APP_DIR"
