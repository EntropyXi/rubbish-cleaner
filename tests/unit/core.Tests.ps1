# core.Tests.ps1 - Pester 5 unit suite for scripts/lib/rubbish-core.ps1.
#
# Covers the shared behavior matrix: Test-DirEmpty (empty dir / dir with a
# file / dir whose only child is a reparse point / nested empty dirs),
# Test-IsJunction, Get-JunkDispositions (12 values), Write-CleanupCsv
# (header written exactly once). Pester 5 syntax ONLY (BeforeAll / It /
# Should -Be / -BeTrue / Set-ItResult); no Pester-3-only patterns (Describe
# tagging / Context-only usage).
#
# Temp fixtures live under <temp>\rubbish-cleaner-tests\<pid>\core (temp root
# resolved via [System.IO.Path]::GetTempPath(), the cross-platform temp root)
# and are removed in AfterAll (own suite dir first, then empty <pid> and
# parent ancestors on a best-effort basis).

BeforeAll {
    # Fixture plumbing lives in BeforeAll (Pester 5: top-level $script: vars
    # set during discovery are NULL in the run phase, so paths built here).
    $script:SuiteRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("rubbish-cleaner-tests\{0}\core" -f $PID)

    # Dot-source the lib INSIDE BeforeAll: Pester 5 runs the file's top level
    # during DISCOVERY in a scope discarded before the run phase, so lib
    # functions (Test-DirEmpty, Test-IsJunction, ...) must be imported in the
    # run phase to be visible to It blocks.
    . (Join-Path $PSScriptRoot '..\..\scripts\lib\rubbish-core.ps1')

    # Helper defined INSIDE BeforeAll for the same scoping reason.
    function New-TestDir {
        param([string]$Name)
        $p = Join-Path $script:SuiteRoot $Name
        New-Item -ItemType Directory -Path $p -Force | Out-Null
        return $p
    }

    # One reparse point shared by the Test-DirEmpty and Test-IsJunction
    # suites. Junction first (no admin rights needed on Windows); fall back to
    # a symbolic link; if BOTH fail (no reparse-point privileges) print the
    # mandated SKIP line and skip only the reparse-dependent assertions (the
    # code under test filters reparse points regardless of the test's ability
    # to create them).
    $script:JunctionTarget = New-TestDir 'junction-target'
    $script:JunctionLink = Join-Path (New-TestDir 'links') 'reparse-link'
    $script:ReparseCreated = $false
    try {
        New-Item -ItemType Junction -Path $script:JunctionLink -Target $script:JunctionTarget -ErrorAction Stop | Out-Null
        $script:ReparseCreated = $true
    } catch {
        try {
            New-Item -ItemType SymbolicLink -Path $script:JunctionLink -Target $script:JunctionTarget -ErrorAction Stop | Out-Null
            $script:ReparseCreated = $true
        } catch {
            $script:ReparseCreated = $false
        }
    }
    if (-not $script:ReparseCreated) {
        Write-Output 'SUITE Test-DirEmpty: SKIP (cannot create reparse points)'
    }
}

Describe 'Test-DirEmpty' {
    It 'returns $true for an empty directory' {
        $d = New-TestDir 'empty'
        Test-DirEmpty -Path $d | Should -BeTrue
    }

    It 'returns $false for a directory containing one file' {
        $d = New-TestDir 'with-file'
        Set-Content -LiteralPath (Join-Path $d 'file.txt') -Value 'x'
        Test-DirEmpty -Path $d | Should -BeFalse
    }

    It 'returns $true when the only child is a reparse point (junction/symlink)' {
        if (-not $script:ReparseCreated) {
            Set-ItResult -Inconclusive -Because 'cannot create reparse points on this machine (junction and symbolic-link creation both failed)'
            return
        }
        $d = New-TestDir 'only-reparse-child'
        $link = Join-Path $d 'link'
        New-Item -ItemType Junction -Path $link -Target $script:JunctionTarget -ErrorAction Stop | Out-Null
        Test-IsJunction -Path $link | Should -BeTrue
        Test-DirEmpty -Path $d | Should -BeTrue
    }

    It 'returns $true for nested empty directories' {
        $nested = New-TestDir 'nested'
        New-Item -ItemType Directory -Path (Join-Path $nested 'a\b\c') -Force | Out-Null
        Test-DirEmpty -Path $nested | Should -BeTrue
    }
}

Describe 'Test-IsJunction' {
    It 'returns $true for a reparse point' {
        if (-not $script:ReparseCreated) {
            Set-ItResult -Inconclusive -Because 'cannot create reparse points on this machine (junction and symbolic-link creation both failed)'
            return
        }
        Test-IsJunction -Path $script:JunctionLink | Should -BeTrue
    }

    It 'returns $false for a regular directory' {
        Test-IsJunction -Path (New-TestDir 'regular-dir') | Should -BeFalse
    }

    It 'returns $false for a regular file' {
        $d = New-TestDir 'regular-file'
        $f = Join-Path $d 'file.txt'
        Set-Content -LiteralPath $f -Value 'x'
        Test-IsJunction -Path $f | Should -BeFalse
    }

    It 'returns $false for a path that does not exist' {
        Test-IsJunction -Path (Join-Path $script:SuiteRoot 'does-not-exist') | Should -BeFalse
    }
}

Describe 'Get-JunkDispositions' {
    It 'returns exactly 12 disposition values' {
        @(Get-JunkDispositions).Count | Should -Be 12
    }

    It 'contains every expected disposition value' {
        $expected = @(
            'OK',
            'SKIP_LOCKED',
            'SKIP_ACCESS_DENIED',
            'SKIP_NOT_FOUND',
            'SKIP_NOT_EMPTY',
            'SKIP_JUNCTION',
            'SKIP_TOO_RECENT',
            'SKIP_WSL_REGISTERED',
            'SKIP_ELEVATION_DENIED',
            'SKIP_SERVICE_RUNNING',
            'QUARANTINED',
            'MOVE_FAILED'
        )
        $actual = @(Get-JunkDispositions)
        foreach ($e in $expected) {
            $actual -contains $e | Should -BeTrue
        }
    }

    It 'contains no duplicates' {
        $actual = @(Get-JunkDispositions)
        @($actual | Select-Object -Unique).Count | Should -Be $actual.Count
    }
}

Describe 'Write-CleanupCsv' {
    It 'writes the pipe-delimited header exactly once across multiple appends' {
        $csv = Join-Path (New-TestDir 'csv') 'cleanup.csv'
        Write-CleanupCsv -CsvPath $csv -Row @{ Phase = 'a'; Action = 'Remove'; Path = 'x'; ErrorMessage = ''; Disposition = 'OK' }
        Write-CleanupCsv -CsvPath $csv -Row @{ Phase = 'b'; Action = 'Remove'; Path = 'y'; ErrorMessage = ''; Disposition = 'SKIP_LOCKED' }

        $lines = @([System.IO.File]::ReadAllLines($csv))
        $lines.Count | Should -Be 3

        # Header exactly once (BOM stripped for comparison).
        ($lines | Where-Object { $_ -like 'Timestamp|*' }).Count | Should -Be 1
        ($lines[0].TrimStart([char]0xFEFF)) | Should -Be 'Timestamp|Phase|Action|Path|ErrorMessage|Disposition'

        # Both data rows carry the right disposition in the last field.
        $lines[1].Split('|')[-1] | Should -Be 'OK'
        $lines[2].Split('|')[-1] | Should -Be 'SKIP_LOCKED'
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
