# install.ps1 - DeepSeek Megapack: one-command plugin environment setup (Windows).
#
# Verifies Python >= 3.12 (backend/pyproject.toml: requires-python = ">=3.12"),
# creates a .venv inside the plugin folder, installs requirements.txt into it,
# probes for ffmpeg, and prints next steps. Writes nothing outside the plugin
# folder. Layout auto-detected (F3-1 fix): repo checkouts keep the plugin tree
# in a plugin\ subfolder beside this script; the shipped zip is FLAT (plugin
# files at the root beside this script).

$ErrorActionPreference = 'Stop'

# Layout detection (F3-1 fix): resolved from THIS script's location, never CWD.
# Repo checkout: plugin tree lives in a plugin\ subfolder beside this script.
# Shipped zip:   plugin files sit at the root BESIDE this script (flat layout).
# Detected by manifest presence, not by convention.
$repoPluginDir = Join-Path $PSScriptRoot 'plugin'
if (Test-Path (Join-Path $repoPluginDir 'deepseek-megapack.yml')) {
    $pluginDir = $repoPluginDir
    $layoutLabel = 'repo (plugin\ subfolder beside install.ps1)'
}
else {
    $pluginDir = $PSScriptRoot
    $layoutLabel = 'flat (plugin files beside install.ps1 - shipped zip layout)'
}
$requirements = Join-Path $pluginDir 'requirements.txt'
$venvDir      = Join-Path $pluginDir '.venv'
$venvPython   = Join-Path $venvDir 'Scripts\python.exe'
$minVer       = [version]'3.12'

function Step([string]$m) { Write-Host ""; Write-Host "== $m" }
function Info([string]$m) { Write-Host "   $m" }
function FailInstall([string]$m) {
    Write-Host "ERROR: $m" -ForegroundColor Red
    Write-Host "See output above; re-run the failing pip command without -q for details."
    exit 1
}

# Capture a native command's stdout without tripping $ErrorActionPreference='Stop'
# on incidental stderr (the PS 5.1 `2>&1` NativeCommandError trap). stdout is
# returned; stderr flows to the console untouched.
function Get-NativeOut([scriptblock]$sb) {
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { return (& $sb) } finally { $ErrorActionPreference = $old }
}

function Get-PythonVersion([string]$Exe, [string[]]$Prefix) {
    # Returns [version] or $null. Windows Store stubs exit 9009 with no stdout -> $null.
    $lines = Get-NativeOut { & $Exe @Prefix --version }
    if ($LASTEXITCODE -ne 0) { return $null }
    $text = ($lines | Out-String).Trim()
    if ($text -match 'Python\s+(\d+)\.(\d+)(?:\.(\d+))?') {
        $micro = 0
        if ($Matches[3]) { $micro = [int]$Matches[3] }
        return [version]("{0}.{1}.{2}" -f [int]$Matches[1], [int]$Matches[2], $micro)
    }
    return $null
}

function Find-LauncherPython {
    # Highest interpreter >= 3.12 known to the py launcher ("py -0p"); $null if none.
    $py = Get-Command py -ErrorAction SilentlyContinue
    if (-not $py) { return $null }
    $lines = Get-NativeOut { & $py.Source -0p }
    if ($LASTEXITCODE -ne 0) { return $null }
    $best = $null
    foreach ($line in ($lines | Out-String) -split "`r?`n") {
        $tag = $null
        if ($line -match '-V:(\d+)\.(\d+)') {
            $tag = @{ Major = [int]$Matches[1]; Minor = [int]$Matches[2] }
        }
        elseif ($line -match '-\s*(\d+)\.(\d+)-\d+') {   # legacy listing: " - 3.12-64"
            $tag = @{ Major = [int]$Matches[1]; Minor = [int]$Matches[2] }
        }
        if ($tag) {
            $v = [version]("$($tag.Major).$($tag.Minor).0")
            if ($v -ge $minVer -and (-not $best -or $v -gt $best.Ver)) {
                $best = @{ Ver = $v; Prefix = @("-$($tag.Major).$($tag.Minor)") }
            }
        }
    }
    return $best
}

if (-not (Test-Path $requirements)) {
    Write-Host "ERROR: $requirements not found." -ForegroundColor Red
    Write-Host "Expected the plugin files (requirements.txt, task.py, deepseek-megapack.yml)"
    Write-Host "either beside this script (shipped zip layout) or in a plugin\ subfolder"
    Write-Host "beside it (repo checkout). Re-run from the distribution root or the"
    Write-Host "extracted zip folder."
    exit 1
}

Write-Host "DeepSeek Megapack - plugin environment installer"
Write-Host "Layout: $layoutLabel"
Write-Host "Plugin folder: $pluginDir"

# --- 1/5 Python >= 3.12 -----------------------------------------------------
Step "1/5 Checking for Python >= $minVer"

$pyExe = $null; $pyPrefix = @(); $pyLabel = ''

$onPath = Get-Command python -ErrorAction SilentlyContinue
if ($onPath) {
    $v = Get-PythonVersion $onPath.Source @()
    if ($v -and $v -ge $minVer) {
        $pyExe = $onPath.Source; $pyLabel = "$($onPath.Source) (Python $v)"
    }
    elseif ($v) {
        Info "python on PATH is $v - below $minVer; checking the py launcher for a newer interpreter..."
    }
    else {
        Info "python on PATH did not report a version (Windows Store stub?); checking the py launcher..."
    }
}
if (-not $pyExe) {
    $found = Find-LauncherPython
    if ($found) {
        $pyExe = 'py'; $pyPrefix = $found.Prefix; $pyLabel = "py launcher (Python $($found.Ver))"
    }
}
if (-not $pyExe) {
    $py3 = Get-Command python3 -ErrorAction SilentlyContinue
    if ($py3) {
        $v = Get-PythonVersion $py3.Source @()
        if ($v -and $v -ge $minVer) { $pyExe = $py3.Source; $pyLabel = "$($py3.Source) (Python $v)" }
    }
}
if (-not $pyExe) {
    Write-Host ""
    Write-Host "ERROR: Python $minVer or newer is required (requires-python = `">=3.12`")." -ForegroundColor Red
    Write-Host "No suitable interpreter was found on PATH or in the py launcher. Install one:"
    Write-Host "    winget install Python.Python.3.14"
    Write-Host "    choco install python"
    Write-Host "    https://www.python.org/downloads/"
    Write-Host "Then open a NEW terminal (so PATH updates) and re-run this script."
    exit 1
}
Info "Using: $pyLabel"

# --- 2/5 venv inside plugin\ ------------------------------------------------
Step "2/5 Creating virtual environment in $venvDir"
if (Test-Path $venvPython) {
    Info "already exists - reusing"
}
else {
    & $pyExe @pyPrefix -m venv $venvDir
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: failed to create the virtual environment (exit $LASTEXITCODE)." -ForegroundColor Red
        exit 1
    }
    Info "created"
}

# --- 3/5 requirements -------------------------------------------------------
# vcsi 7.0.17's metadata pins pillow==11.2.1 / numpy==2.2.6, which contradict
# this file's pillow>=12 and have no Python 3.14 wheels. Empirically vcsi
# 7.0.17 runs correctly with pillow 12.x / numpy 2.5.x (proven in the dev
# venv), so its pins are over-strict: we install everything BUT vcsi from
# requirements.txt, add vcsi's remaining runtime deps with relaxed floors,
# then install vcsi itself with --no-deps. If a future requirements.txt has
# no vcsi line, this degrades to a plain "pip install -r".
Step "3/5 Installing requirements.txt (fastapi uvicorn pydantic-settings pillow httpx torf vcsi)"
$allLines = Get-Content $requirements
$vcsiSpec = $allLines | Where-Object { $_ -match '^\s*vcsi\b' } | Select-Object -First 1
if ($vcsiSpec) {
    $vcsiSpec = $vcsiSpec.Trim()
    $filtered = Join-Path $pluginDir '.requirements-no-vcsi.tmp'
    $allLines | Where-Object { $_ -notmatch '^\s*vcsi\b' } | Set-Content -Path $filtered -Encoding ASCII
    & $venvPython -m pip install -r $filtered -q
    $rc = $LASTEXITCODE
    Remove-Item $filtered -Force -ErrorAction SilentlyContinue
    if ($rc -ne 0) { FailInstall "pip install (requirements minus vcsi) failed (exit $rc)" }
    # vcsi's true runtime companions (floors relaxed from its METADATA; pillow
    # comes from requirements.txt itself)
    & $venvPython -m pip install -q 'numpy>=2.2' 'jinja2>=3.1.6,<4' 'parsedatetime~=2.6' 'texttable>=1.6.7,<2'
    if ($LASTEXITCODE -ne 0) { FailInstall "pip install (vcsi runtime companions) failed (exit $LASTEXITCODE)" }
    & $venvPython -m pip install --no-deps -q $vcsiSpec
    if ($LASTEXITCODE -ne 0) { FailInstall "pip install --no-deps $vcsiSpec failed (exit $LASTEXITCODE)" }
    Info "done (vcsi via --no-deps: upstream pins pillow==11.2.1/numpy==2.2.6 conflict with pillow>=12)"
}
else {
    & $venvPython -m pip install -r $requirements -q
    if ($LASTEXITCODE -ne 0) { FailInstall "pip install -r requirements.txt failed (exit $LASTEXITCODE)" }
    Info "done"
}

# --- 4/5 ffmpeg probe -------------------------------------------------------
# Mirrors the backend's runtime fallbacks (settings -> PATH -> cove -> ~\.stash);
# the installer probes PATH -> ~\.stash -> cove. ffprobe is derived from the
# ffmpeg location by the backend, so one hit covers both.
Step "4/5 Probing ffmpeg (PATH -> ~\.stash -> %LOCALAPPDATA%\cove\ffmpeg)"
$ffmpeg = $null
$c = Get-Command ffmpeg -ErrorAction SilentlyContinue
if ($c) { $ffmpeg = $c.Source }
if (-not $ffmpeg) {
    $p = Join-Path $HOME '.stash\ffmpeg.exe'
    if (Test-Path $p) { $ffmpeg = $p }
}
if (-not $ffmpeg -and $env:LOCALAPPDATA) {
    $p = Join-Path $env:LOCALAPPDATA 'cove\ffmpeg\ffmpeg.exe'
    if (Test-Path $p) { $ffmpeg = $p }
}
if ($ffmpeg) {
    Info "FOUND: $ffmpeg"
    Info "ffprobe is expected next to ffmpeg (the backend derives it automatically)."
}
else {
    Info "MISSING: ffmpeg"
    Info "Fix: winget install Gyan.FFmpeg (or choco install ffmpeg), then re-open the terminal;"
    Info "a Stash-bundled copy under ~\.stash is picked up automatically by the backend."
}

# --- 5/5 next steps ---------------------------------------------------------
Step "5/5 Next steps"
Write-Host @"
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
"@

Write-Host ""
Write-Host "Install complete." -ForegroundColor Green
exit 0
