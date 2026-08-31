#!/usr/bin/env bash
# install.sh - DeepSeek Megapack: one-command plugin environment setup (Linux/macOS).
#
# Verifies Python >= 3.12 (backend/pyproject.toml: requires-python = ">=3.12"),
# creates a .venv inside the plugin folder, installs requirements.txt into it,
# probes for ffmpeg, and prints next steps. Writes nothing outside the plugin
# folder. Layout auto-detected (F3-1 fix): repo checkouts keep the plugin tree
# in a plugin/ subfolder beside this script; the shipped zip is FLAT (plugin
# files at the root beside this script).

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Layout detection (F3-1 fix): resolved from THIS script's location, never CWD.
# Repo checkout: plugin tree lives in a plugin/ subfolder beside this script.
# Shipped zip:   plugin files sit at the root BESIDE this script (flat layout).
# Detected by manifest presence, not by convention.
if [ -f "$SCRIPT_DIR/plugin/deepseek-megapack.yml" ]; then
    PLUGIN_DIR="$SCRIPT_DIR/plugin"
    LAYOUT_LABEL="repo (plugin/ subfolder beside install.sh)"
else
    PLUGIN_DIR="$SCRIPT_DIR"
    LAYOUT_LABEL="flat (plugin files beside install.sh - shipped zip layout)"
fi
REQUIREMENTS="$PLUGIN_DIR/requirements.txt"
VENV_DIR="$PLUGIN_DIR/.venv"
VENV_PY="$VENV_DIR/bin/python"
MIN="3.12"

step() { printf '\n== %s\n' "$1"; }
info() { printf '   %s\n' "$1"; }

if [ ! -f "$REQUIREMENTS" ]; then
    printf 'ERROR: %s not found.\n' "$REQUIREMENTS" >&2
    printf 'Expected the plugin files (requirements.txt, task.py, deepseek-megapack.yml)\n' >&2
    printf 'either beside this script (shipped zip layout) or in a plugin/ subfolder\n' >&2
    printf 'beside it (repo checkout). Re-run from the distribution root or the\n' >&2
    printf 'extracted zip folder.\n' >&2
    exit 1
fi

printf 'DeepSeek Megapack - plugin environment installer\n'
printf 'Layout: %s\n' "$LAYOUT_LABEL"
printf 'Plugin folder: %s\n' "$PLUGIN_DIR"

# --- 1/5 Python >= 3.12 -----------------------------------------------------
step "1/5 Checking for Python >= $MIN"

py_version() {  # $1 = interpreter -> echoes "X.Y[.Z]", rc!=0 when not a CPython 3+
    local out
    out="$("$1" --version 2>/dev/null)" || return 1
    case "$out" in
        Python\ [0-9]*.[0-9]*) printf '%s\n' "${out#Python }" ;;
        *) return 1 ;;
    esac
}

at_least() {  # $1 = "X.Y[.Z]" -> rc 0 when >= 3.12
    local major minor
    major="${1%%.*}"
    minor="${1#*.}"
    minor="${minor%%.*}"
    if [ "$major" -gt 3 ]; then return 0; fi
    if [ "$major" -eq 3 ] && [ "$minor" -ge 12 ]; then return 0; fi
    return 1
}

PY=""
PY_LABEL=""
for cand in python3 python; do   # python3 first; plain `python` covers python-is-python3 setups
    command -v "$cand" >/dev/null 2>&1 || continue
    ver="$(py_version "$cand")" || continue
    if at_least "$ver"; then
        PY="$cand"
        PY_LABEL="$cand (Python $ver)"
        break
    else
        info "$cand is Python $ver - below $MIN; trying the next candidate..."
    fi
done

if [ -z "$PY" ]; then
    cat >&2 <<'EOF'
ERROR: Python 3.12 or newer is required (requires-python = ">=3.12").
No suitable interpreter was found. Install one:
    Debian/Ubuntu: sudo apt install python3 python3-venv python-is-python3
    Fedora:        sudo dnf install python3
    macOS:         brew install python@3.12   (or newer)
Then re-run this script.
EOF
    exit 1
fi
info "Using: $PY_LABEL"

# --- 2/5 venv inside plugin/ -------------------------------------------------
step "2/5 Creating virtual environment in $VENV_DIR"
if [ -d "$VENV_DIR" ] && { [ -x "$VENV_DIR/bin/python" ] || [ -x "$VENV_DIR/Scripts/python.exe" ]; }; then
    info "already exists - reusing"
else
    "$PY" -m venv "$VENV_DIR" || {
        printf 'ERROR: failed to create the virtual environment.\n' >&2
        printf '(Debian/Ubuntu: install python3-venv first.)\n' >&2
        exit 1
    }
    info "created"
fi
# Layout: POSIX venvs use bin/python; a venv created by Windows Python
# (e.g. when this script runs under git-bash) uses Scripts/python.exe.
if [ -x "$VENV_DIR/bin/python" ]; then
    VENV_PY="$VENV_DIR/bin/python"
elif [ -x "$VENV_DIR/Scripts/python.exe" ]; then
    VENV_PY="$VENV_DIR/Scripts/python.exe"
else
    printf 'ERROR: no interpreter found in %s (bin/python or Scripts/python.exe)\n' "$VENV_DIR" >&2
    exit 1
fi

# --- 3/5 requirements --------------------------------------------------------
# vcsi 7.0.17's metadata pins pillow==11.2.1 / numpy==2.2.6, which contradict
# this file's pillow>=12. Empirically vcsi 7.0.17 runs correctly with pillow
# 12.x / numpy 2.5.x (proven in the dev venv), so its pins are over-strict:
# we install everything BUT vcsi from requirements.txt, add vcsi's remaining
# runtime deps with relaxed floors, then install vcsi itself with --no-deps.
# If a future requirements.txt has no vcsi line, this degrades to a plain
# "pip install -r".
step "3/5 Installing requirements.txt (fastapi uvicorn pydantic-settings pillow httpx torf vcsi)"
VCSI_SPEC="$(grep -E '^[[:space:]]*vcsi([[:space:]]|=|>|<|!|~|$)' "$REQUIREMENTS" | head -n 1 | tr -d '[:space:]')"
if [ -n "$VCSI_SPEC" ]; then
    FILTERED="$PLUGIN_DIR/.requirements-no-vcsi.tmp"
    grep -vE '^[[:space:]]*vcsi([[:space:]]|=|>|<|!|~|$)' "$REQUIREMENTS" > "$FILTERED"
    "$VENV_PY" -m pip install -r "$FILTERED" -q
    rc=$?
    rm -f "$FILTERED"
    [ "$rc" -eq 0 ] || { printf 'ERROR: pip install (requirements minus vcsi) failed (exit %s)\n' "$rc" >&2; exit 1; }
    # vcsi's true runtime companions (floors relaxed from its METADATA; pillow
    # comes from requirements.txt itself)
    "$VENV_PY" -m pip install -q 'numpy>=2.2' 'jinja2>=3.1.6,<4' 'parsedatetime~=2.6' 'texttable>=1.6.7,<2'
    rc=$?
    [ "$rc" -eq 0 ] || { printf 'ERROR: pip install (vcsi runtime companions) failed (exit %s)\n' "$rc" >&2; exit 1; }
    "$VENV_PY" -m pip install --no-deps -q "$VCSI_SPEC"
    rc=$?
    [ "$rc" -eq 0 ] || { printf 'ERROR: pip install --no-deps %s failed (exit %s)\n' "$VCSI_SPEC" "$rc" >&2; exit 1; }
    info "done (vcsi via --no-deps: upstream pins pillow==11.2.1/numpy==2.2.6 conflict with pillow>=12)"
else
    "$VENV_PY" -m pip install -r "$REQUIREMENTS" -q
    rc=$?
    if [ "$rc" -ne 0 ]; then
        printf 'ERROR: pip install failed (exit %s). Re-run without -q for details:\n' "$rc" >&2
        printf '    "%s" -m pip install -r "%s"\n' "$VENV_PY" "$REQUIREMENTS" >&2
        exit 1
    fi
    info "done"
fi

# --- 4/5 ffmpeg probe --------------------------------------------------------
# Mirrors the backend's runtime fallbacks; the Windows-only %LOCALAPPDATA%\cove
# fallback does not apply here. ffprobe is derived from the ffmpeg location by
# the backend, so one hit covers both.
step "4/5 Probing ffmpeg (PATH -> ~/.stash)"
FFMPEG="$(command -v ffmpeg 2>/dev/null || true)"
if [ -z "$FFMPEG" ] && [ -x "$HOME/.stash/ffmpeg" ]; then
    FFMPEG="$HOME/.stash/ffmpeg"
fi
if [ -n "$FFMPEG" ]; then
    info "FOUND: $FFMPEG"
    info "ffprobe is expected next to ffmpeg (the backend derives it automatically)."
else
    info "MISSING: ffmpeg"
    info "Fix: sudo apt install ffmpeg (macOS: brew install ffmpeg), then re-run;"
    info "a Stash-bundled copy under ~/.stash is picked up automatically by the backend."
fi

# --- 5/5 next steps ----------------------------------------------------------
step "5/5 Next steps"
cat <<'EOF'
   1. Copy the plugin folder into Stash (or symlink it), then:
      Stash -> Settings -> Plugins -> Reload (or restart Stash).
   2. Run a Megapack task from the scene tools - task.py re-execs
      into this .venv on its own; no manual activation needed.
   3. Optional review-UI sidecar: start the backend with the start script
      from the full distribution; it binds 127.0.0.1 only.
   4. Optional config: put config.local.toml in the PLUGIN folder (next to
      task.py) - the backend checks the repository root first in dev
      checkouts, the plugin folder when vendored. The template is in the
      plugin README. This installer never creates or prints config values.
EOF

printf '\nInstall complete.\n'
exit 0
