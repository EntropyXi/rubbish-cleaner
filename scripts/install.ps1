<#
.SYNOPSIS
    Install the rubbish-cleaner skill into one or more agent skill directories.

.DESCRIPTION
    Copies the repository content (SKILL.md, scripts/, references/, agents/,
    tests/, README.md, LICENSE, requirements.txt - everything EXCEPT .git,
    .omo and .codegraph) into the requested target directory(ies):

      - claude   : $env:USERPROFILE\.claude\skills\rubbish-cleaner\
      - codex    : $env:USERPROFILE\.codex\skills\rubbish-cleaner\
      - opencode : $env:USERPROFILE\.config\opencode\skills\automation\rubbish-cleaner\

    Idempotent: re-running overwrites existing copies (Copy-Item -Force).
    Copy-only: never deletes anything in the targets. No admin rights needed.

.PARAMETER Target
    Which platform(s) to install to: all | claude | codex | opencode. Default 'all'.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install.ps1

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install.ps1 -Target opencode
#>
[CmdletBinding()]
param(
    [ValidateSet('all', 'claude', 'codex', 'opencode')]
    [string]$Target = 'all'
)

$ErrorActionPreference = 'Stop'

# Repo root = parent of the scripts\ directory this file lives in.
$repoRoot = Split-Path -Parent $PSScriptRoot

# Everything EXCEPT these top-level entries gets copied.
$exclude = @('.git', '.omo', '.codegraph')

# Target install dirs per platform.
$targets = @{
    'claude'   = Join-Path $env:USERPROFILE '.claude\skills\rubbish-cleaner'
    'codex'    = Join-Path $env:USERPROFILE '.codex\skills\rubbish-cleaner'
    'opencode' = Join-Path $env:USERPROFILE '.config\opencode\skills\automation\rubbish-cleaner'
}

# Resolve which platform(s) to install to.
$selected = switch ($Target) {
    'claude'   { @('claude') }
    'codex'    { @('codex') }
    'opencode' { @('opencode') }
    default    { @('claude', 'codex', 'opencode') }
}

$copied = @()
try {
    foreach ($name in $selected) {
        $dest = $targets[$name]
        New-Item -ItemType Directory -Path $dest -Force | Out-Null

        # Copy every top-level repo entry (except the excluded ones) into $dest.
        # Copy-Item -Recurse -Force merges/overwrites -> idempotent on re-run.
        Get-ChildItem -LiteralPath $repoRoot -Force |
            Where-Object { $_.Name -notin $exclude } |
            ForEach-Object {
                Copy-Item -LiteralPath $_.FullName -Destination $dest -Recurse -Force
            }

        $copied += $dest
    }
}
catch {
    Write-Error "INSTALL FAILED: $($_.Exception.Message)"
    exit 1
}

foreach ($dest in $copied) {
    Write-Output "COPIED: $dest"
}
Write-Output 'INSTALL: PASS'
exit 0
