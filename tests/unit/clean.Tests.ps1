# clean.Tests.ps1 - Pester 5 unit suite for the safe-delete / quarantine
# behavior of scripts/lib/rubbish-core.ps1 (Invoke-SafeRemove,
# Invoke-Quarantine, Test-FileLocked) plus the Test-DirEmpty re-verify gate
# used by clean-drive.ps1 before any directory removal.
#
# Every destructive action here runs ONLY against files under the pid-keyed
# fake tree $env:TEMP\rubbish-cleaner-tests\<pid>\clean; nothing outside it is
# touched, and the whole tree is removed in AfterAll.
#
# Pester 5 syntax ONLY (BeforeAll / It / Should -Be / -BeTrue); no Pester-3-only
# patterns (Describe tagging / Context-only usage).

$script:SuiteRoot = Join-Path $env:TEMP ("rubbish-cleaner-tests\{0}\clean" -f $PID)
$script:CleanupCsv = Join-Path $script:SuiteRoot 'cleanup-errors.csv'

. (Join-Path $PSScriptRoot '..\..\scripts\lib\rubbish-core.ps1')

# Parse the pipe-delimited cleanup CSV written by the lib functions.
function Get-CleanupRowsFrom {
    param([string]$CsvPath)
    if (-not (Test-Path -LiteralPath $CsvPath)) { return @() }
    $rows = @()
    $lines = [System.IO.File]::ReadAllLines($CsvPath)
    for ($i = 1; $i -lt $lines.Count; $i++) {
        $p = $lines[$i] -split '\|'
        if ($p.Count -lt 6) { continue }
        $rows += [pscustomobject]@{
            Timestamp    = $p[0]
            Phase        = $p[1]
            Action       = $p[2]
            Path         = $p[3]
            ErrorMessage = (($p[4..($p.Count - 2)]) -join '|')
            Disposition  = $p[-1]
        }
    }
    return $rows
}

BeforeAll {
    New-Item -ItemType Directory -Path $script:SuiteRoot -Force | Out-Null
}

Describe 'Invoke-SafeRemove' {
    It 'deletes a normal file and records an OK row' {
        $f = Join-Path $script:SuiteRoot 'normal.txt'
        Set-Content -LiteralPath $f -Value 'payload'
        Test-Path -LiteralPath $f | Should -BeTrue

        Invoke-SafeRemove -LiteralPath $f -Phase 'test' -CsvPath $script:CleanupCsv

        Test-Path -LiteralPath $f | Should -BeFalse
        $rows = @(Get-CleanupRowsFrom -CsvPath $script:CleanupCsv | Where-Object { $_.Path -eq $f })
        $rows.Count | Should -Be 1
        $rows[0].Disposition | Should -Be 'OK'
        $rows[0].Action | Should -Be 'Remove'
    }

    It 'leaves a locked file in place and records SKIP_LOCKED' {
        $f = Join-Path $script:SuiteRoot 'locked.txt'
        Set-Content -LiteralPath $f -Value 'locked payload'
        $handle = $null
        try {
            # Simulate a lock: hold an exclusive FileShare.None handle while
            # Invoke-SafeRemove tries to delete the file.
            $handle = [System.IO.File]::Open($f, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::None)
            Test-FileLocked -LiteralPath $f | Should -BeTrue

            Invoke-SafeRemove -LiteralPath $f -Phase 'test' -CsvPath $script:CleanupCsv

            Test-Path -LiteralPath $f | Should -BeTrue
        } finally {
            if ($null -ne $handle) { $handle.Close() }
        }

        # Once the handle is released the file is deletable again.
        Test-FileLocked -LiteralPath $f | Should -BeFalse

        $rows = @(Get-CleanupRowsFrom -CsvPath $script:CleanupCsv | Where-Object { $_.Path -eq $f })
        $rows.Count | Should -Be 1
        $rows[0].Disposition | Should -Be 'SKIP_LOCKED'
        $rows[0].ErrorMessage | Should -Not -BeNullOrEmpty
    }
}

Describe 'Invoke-Quarantine' {
    It 'moves the source into the quarantine dir (source absent, dest present, QUARANTINED row)' {
        $f = Join-Path $script:SuiteRoot 'quarantine-src.txt'
        $qdir = Join-Path $script:SuiteRoot 'quarantine'
        Set-Content -LiteralPath $f -Value 'quarantine me'

        Invoke-Quarantine -LiteralPath $f -QuarantineDir $qdir -Phase 'test' -CsvPath $script:CleanupCsv

        Test-Path -LiteralPath $f | Should -BeFalse
        Test-Path -LiteralPath (Join-Path $qdir 'quarantine-src.txt') | Should -BeTrue

        $rows = @(Get-CleanupRowsFrom -CsvPath $script:CleanupCsv | Where-Object { $_.Path -eq $f })
        $rows.Count | Should -Be 1
        $rows[0].Disposition | Should -Be 'QUARANTINED'
        $rows[0].Action | Should -Be 'Quarantine'
    }
}

Describe 'Test-DirEmpty re-verify gate' {
    It 'removes an empty directory (gate passes, OK row)' {
        $d = Join-Path $script:SuiteRoot 'gate-empty'
        New-Item -ItemType Directory -Path $d -Force | Out-Null
        Test-DirEmpty -Path $d | Should -BeTrue

        Invoke-SafeRemove -LiteralPath $d -Phase 'test' -CsvPath $script:CleanupCsv

        Test-Path -LiteralPath $d | Should -BeFalse
        $rows = @(Get-CleanupRowsFrom -CsvPath $script:CleanupCsv | Where-Object { $_.Path -eq $d })
        $rows.Count | Should -Be 1
        $rows[0].Disposition | Should -Be 'OK'
    }

    It 'never force-deletes a non-empty directory (gate blocks, dir survives)' {
        $d = Join-Path $script:SuiteRoot 'gate-nonempty'
        New-Item -ItemType Directory -Path $d -Force | Out-Null
        Set-Content -LiteralPath (Join-Path $d 'keep.txt') -Value 'content'
        Test-DirEmpty -Path $d | Should -BeFalse

        Invoke-SafeRemove -LiteralPath $d -Phase 'test' -CsvPath $script:CleanupCsv

        Test-Path -LiteralPath $d -PathType Container | Should -BeTrue
        $rows = @(Get-CleanupRowsFrom -CsvPath $script:CleanupCsv | Where-Object { $_.Path -eq $d })
        $rows.Count | Should -Be 1
        # Invoke-SafeRemove never uses bare -Recurse: the directory removal
        # fails and is classified via the exception chain.
        $rows[0].Disposition | Should -Be 'SKIP_LOCKED'
    }
}

AfterAll {
    if (Test-Path -LiteralPath $script:SuiteRoot) {
        Remove-Item -LiteralPath $script:SuiteRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
    # Best-effort: drop the now-empty <pid> root, then the shared parent.
    $pidRoot = Split-Path -Parent $script:SuiteRoot
    if ((Test-Path -LiteralPath $pidRoot) -and -not @(Get-ChildItem -LiteralPath $pidRoot -Force -ErrorAction SilentlyContinue).Count) {
        Remove-Item -LiteralPath $pidRoot -Force -ErrorAction SilentlyContinue
    }
    $parent = Split-Path -Parent $pidRoot
    if ((Test-Path -LiteralPath $parent) -and -not @(Get-ChildItem -LiteralPath $parent -Force -ErrorAction SilentlyContinue).Count) {
        Remove-Item -LiteralPath $parent -Force -ErrorAction SilentlyContinue
    }
}
