<#
scripts/check_secrets.ps1 - secrets & machine-shape sweep (release audit task T13).

Sweeps every TRACKED file (git ls-files; gitignored paths such as the real
config.local.toml, runtime/, dist/ are never scanned) for secret and
machine-identifying shapes. Exit 0 = clean, 1 = any hit, 2 = git failure.

SELF-EXCLUSION (deliberate): this script embeds every shape it hunts, so it
can never scan itself without self-matching. This mirrors the audit's own
verification gate, which excludes the checker from the shape grep:
    git grep -nE "D:\\232|chv_|config\.local|C:\\Projects|run_task\.bat" -- . ":(exclude)scripts/check_secrets.ps1"
Trade-off: a secret pasted into THIS file would be missed. Accepted.

WHY "passkey" IS A CREDENTIAL-SHAPE RULE, NOT A BARE WORD: the passkey
masking feature and its test suite legitimately discuss passkeys (function
names, masking assertions). A real leak is a passkey ASSIGNED a non-empty
quoted value; announce tokens are additionally caught by the hex32 rule.

WHY machine-d-path FLAGS ONLY NUMERIC FIRST SEGMENTS (D:\232, D:\240): the
numeric-root library directory is the machine tell. Generic alpha fixtures
(D:\Media, D:\Seed) are this project's own synthetic convention and are
deliberately not flagged (dozens of legitimate occurrences in tests/assets).

ALLOWLISTS (per rule, repo-relative forward-slash paths, case-insensitive):
each entry's matching content was reviewed during the T13 sweep and judged
synthetic/legitimate. A new hit anywhere else fails the sweep. Dispositions:
  - passkey-credential / announce-shaped-hex32: torrents.py implements
    masking with "x" * 32; the listed tests exercise synthetic sequential
    hex tokens (0123...cdef, a1b2...cdef) and "passkey=" + "x" * 32 strings.
  - config-local-ref: README.md / install.ps1 / install.sh DOCUMENT the
    gitignored config.local.toml convention; .gitignore and
    scripts/build_plugin_zip.mjs carry the deny rule itself. References are
    not secrets; the ignore rule denies the actual file (probe:
    git check-ignore -v config.local.toml).
  - hamster-key-value: tests build Settings with fake keys
    ("test-api-key", "test_key", "valid-key", "hamster_xyz"); reviewed
    synthetic, tests/ prefix allowlisted.

Usage: powershell -File scripts/check_secrets.ps1
#>
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ($null -eq (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Output 'FATAL: git not found on PATH'
    exit 2
}
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = (& git -C $scriptDir rev-parse --show-toplevel)
if ($LASTEXITCODE -ne 0) { Write-Output "FATAL: not a git repo: $scriptDir"; exit 2 }
$repoRoot = $repoRoot.Trim()

$selfRel = 'scripts/check_secrets.ps1'

$rules = [ordered]@{
    'chv-extension-key'     = @{ pattern = '(?i)chv_';                         allow = @() }
    'passkey-credential'    = @{ pattern = '(?i)passkey\s*[:=]\s*["''][^"'']'; allow = @(
            'backend/empornium_megapack/torrents.py',
            'tests/backend/test_stage5c_passkey_masking.py',
            'tests/backend/test_torrents.py',
            'tests/backend/test_tier5_domain_adversarial.py') }
    'announce-shaped-hex32' = @{ pattern = '(?i)\b[0-9a-f]{32}\b';             allow = @(
            'tests/backend/test_adversarial_m3_challenger.py',
            'tests/backend/test_build.py',
            'tests/backend/test_stage5c_passkey_masking.py',
            'tests/backend/test_torrents.py') }
    'machine-d-path'        = @{ pattern = '(?i)D:[\\/]+[0-9]';                allow = @() }
    'machine-c-projects'    = @{ pattern = '(?i)C:[\\/]+Projects';             allow = @() }
    'machine-identity'      = @{ pattern = '(?i)\bccoggle\b|\bStashUploader\b';        allow = @() }
    'config-local-ref'      = @{ pattern = '(?i)config\.local';                allow = @(
            '.gitignore',
            'README.md',
            'install.ps1',
            'install.sh',
            'scripts/build_plugin_zip.mjs') }
    'hamster-key-value'     = @{ pattern = 'hamster_api_key\s*=\s*"[^"]';      allow = @('tests') }
    'legacy-task-bat'       = @{ pattern = '(?i)run_task\.bat';                allow = @() }
}

function Test-Allowlisted {
    param([string[]] $Allow, [string] $RelLower)
    foreach ($entry in $Allow) {
        $e = $entry.Replace('\', '/').TrimEnd('/').ToLowerInvariant()
        if ($RelLower -eq $e) { return $true }
        if ($RelLower.StartsWith($e + '/')) { return $true }
    }
    return $false
}

$tracked = @(& git -C $repoRoot ls-files)
if ($LASTEXITCODE -ne 0) { Write-Output 'FATAL: git ls-files failed'; exit 2 }

$hits = New-Object System.Collections.Generic.List[string]
$scanned = 0
foreach ($rel in $tracked) {
    $relFwd = $rel.Replace('\', '/')
    if ($relFwd -eq $selfRel) { continue }
    if ($relFwd -match '(^|/)\.env') {
        $hits.Add('tracked-env-file  ' + $relFwd)
    }
    $abs = Join-Path $repoRoot $rel
    $relLower = $relFwd.ToLowerInvariant()
    $lineNo = 0
    foreach ($line in [IO.File]::ReadAllLines($abs)) {
        $lineNo++
        foreach ($name in $rules.Keys) {
            $rule = $rules[$name]
            if ((@($rule['allow']).Count -gt 0) -and (Test-Allowlisted -Allow @($rule['allow']) -RelLower $relLower)) { continue }
            if ($line -match $rule['pattern']) {
                $hits.Add($name + '  ' + $relFwd + ':' + $lineNo)
            }
        }
    }
    $scanned++
}

foreach ($hit in $hits) { Write-Output ('HIT ' + $hit) }
Write-Output ('scanned tracked files: ' + $scanned)
if ($hits.Count -gt 0) {
    Write-Output ('SECRETS_SWEEP: ' + $hits.Count + ' HITS')
    exit 1
}
Write-Output 'SECRETS_SWEEP: CLEAN'
exit 0
