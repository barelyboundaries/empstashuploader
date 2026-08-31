#!/usr/bin/env pwsh
# start_backend.ps1 — one-command sidecar start for the distribution repo.
# Uses the venv that install.ps1 created under plugin\.venv.  Does NOT
# auto-create a venv; if the user hasn't run the installer yet, print a
# message and exit non-zero.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$venv = Join-Path $root "plugin\.venv"
$python = Join-Path $venv "Scripts\python.exe"

# --- venv gate ---
if (-not (Test-Path $python)) {
    Write-Host "ERROR: Virtual environment not found at $venv" -ForegroundColor Red
    Write-Host "Run .\install.ps1 first to set up the environment." -ForegroundColor Yellow
    exit 1
}

# --- PYTHONPATH: prefer repo checkout backend/, else plugin/ (vendored layout) ---
$backendDir = Join-Path $root "backend"
if (Test-Path $backendDir) {
    $env:PYTHONPATH = $backendDir
    $appDir = $backendDir
} else {
    $pluginDir = Join-Path $root "plugin"
    $env:PYTHONPATH = $pluginDir
    $appDir = $pluginDir
}

# --- ffmpeg: DEEPSEEK_FFMPEG_DIR wins; fall back to ~/.stash ---
$ffmpegDir = $env:DEEPSEEK_FFMPEG_DIR
if (-not ($ffmpegDir -and (Test-Path (Join-Path $ffmpegDir "ffmpeg.exe")))) {
    $stashFfmpegDir = Join-Path $HOME ".stash"
    if (Test-Path (Join-Path $stashFfmpegDir "ffmpeg.exe")) {
        $ffmpegDir = $stashFfmpegDir
    }
}
if ($ffmpegDir -and (Test-Path (Join-Path $ffmpegDir "ffmpeg.exe"))) {
    $env:PATH = "$ffmpegDir;$env:PATH"
}

# --- launch ---
# Propagate uvicorn's exit code (bind failure previously surfaced as exit 0).
& $python -m uvicorn deepseek_megapack.main:app --host 127.0.0.1 --port 9941 --app-dir $appDir
exit $LASTEXITCODE
