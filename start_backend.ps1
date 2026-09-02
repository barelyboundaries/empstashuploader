#!/usr/bin/env pwsh
# start_backend.ps1 — one-command sidecar start for the distribution repo.
# Uses the venv that install.ps1 created (repo layout: plugin\.venv;
# shipped zip layout: .venv beside the plugin files at the root).  Does NOT
# auto-create a venv; if the user hasn't run the installer yet, print a
# message and exit non-zero.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

# --- layout detection (mirrors install.ps1): manifest presence, never CWD ---
# Repo checkout: plugin tree lives in a plugin\ subfolder beside this script.
# Shipped zip:   plugin files sit at the root BESIDE this script (flat layout).
$repoPluginDir = Join-Path $root "plugin"
if (Test-Path (Join-Path $repoPluginDir "empornium-megapack.yml")) {
    $pluginDir = $repoPluginDir
} else {
    $pluginDir = $root
}
$venv = Join-Path $pluginDir ".venv"
$python = Join-Path $venv "Scripts\python.exe"

# --- venv gate ---
if (-not (Test-Path $python)) {
    Write-Host "ERROR: Virtual environment not found at $venv" -ForegroundColor Red
    Write-Host "Run .\install.ps1 first to set up the environment." -ForegroundColor Yellow
    exit 1
}

# --- PYTHONPATH: repo checkout backend/, else the plugin dir (vendored layout) ---
$backendDir = Join-Path $root "backend"
if (Test-Path $backendDir) {
    $env:PYTHONPATH = $backendDir
    $appDir = $backendDir
} else {
    $env:PYTHONPATH = $pluginDir
    $appDir = $pluginDir
}

# --- ffmpeg: EMPORNIUM_FFMPEG_DIR wins; fall back to ~/.stash ---
$ffmpegDir = $env:EMPORNIUM_FFMPEG_DIR
if (-not ($ffmpegDir -and (Test-Path (Join-Path $ffmpegDir "ffmpeg.exe")))) {
    $stashFfmpegDir = Join-Path $HOME ".stash"
    if (Test-Path (Join-Path $stashFfmpegDir "ffmpeg.exe")) {
        $ffmpegDir = $stashFfmpegDir
    }
}
if ($ffmpegDir -and (Test-Path (Join-Path $ffmpegDir "ffmpeg.exe"))) {
    $env:PATH = "$ffmpegDir;$env:PATH"
}

# --- port gate: fail fast with a friendly message when 9941 is occupied ---
# (--host 127.0.0.1 --port 9941 are FIXED, documented constants; never dynamic.)
# uvicorn's bind failure already propagates a non-zero exit below; this probe
# just makes the failure legible BEFORE the launch output.  It CONNECTS rather
# than binds: on Windows, SO_REUSEADDR lets a bind probe "succeed" against a
# port an SO_REUSEADDR listener (e.g. python -m http.server) already holds.
$probe = New-Object System.Net.Sockets.TcpClient
try {
    $probe.Connect("127.0.0.1", 9941)
    $probe.Close()
    Write-Host "ERROR: Port 9941 is already in use - another process is listening on 127.0.0.1:9941." -ForegroundColor Red
    Write-Host "Stop that process (or the already-running sidecar) and retry:" -ForegroundColor Yellow
    Write-Host "  netstat -ano | findstr :9941" -ForegroundColor Yellow
    exit 1
} catch {
    $probe.Close()
}

# --- launch ---
# Propagate uvicorn's exit code (bind failure previously surfaced as exit 0).
& $python -m uvicorn empornium_megapack.main:app --host 127.0.0.1 --port 9941 --app-dir $appDir
exit $LASTEXITCODE
