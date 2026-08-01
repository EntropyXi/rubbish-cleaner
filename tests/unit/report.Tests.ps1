# report.Tests.ps1 - Pester 5 unit suite for scripts/verify-report.ps1
# summary generation against a crafted fixture RunDir.
#
# Fixture (mirrors the shared behavior matrix):
#   preflight.txt          BASELINE_FREE_BYTES=1000000000 + TOTAL_BYTES + PROCESSES
#   cleanup-errors.csv     3 OK + 1 SKIP_LOCKED + 1 QUARANTINED rows
#   candidates.csv         5 rows whose Paths match the cleanup rows so the
#                          per-category freed estimate is deterministic
#
# verify-report.ps1 is invoked with & (its trailing `exit 0` returns to the
# caller) against the REAL drive letter that hosts the temp root - it is fully
# read-only except for writing <RunDir>\summary.md and re-reading live free
# space, so no drive mutation happens. The fixture is crafted so every live
# assertion in section 7 is deterministic:
#   - 3 OK rows point at paths that never exist on disk      -> e# PASS
#   - 1 SKIP_LOCKED row points at a real surviving file      -> d1 PASS
#   - 1 QUARANTINED row: original absent + the quarantine
#     copy created at the skill's own quarantine dir         -> a1/b1 PASS
# The one quarantine copy is created and removed by this suite (documented
# choice: verify-report hardcodes the quarantine location, so the copy must
# physically exist there for the "copy present" assertion to pass).
#
# Expected reconciled totals:
#   per-category freed (OK/QUARANTINED joined by Path): 100+200+0+500 = 800
#   candidate bytes: 100+200+0+300+500 = 1100 ; 5 rows
#   section 7: 6/6 assertions PASS.
#
# Pester 5 syntax ONLY (BeforeAll / It / Should -Be / -BeTrue); no Pester-3-only
# patterns (Describe tagging / Context-only usage).

BeforeAll {
    # Fixture plumbing lives in BeforeAll (Pester 5: top-level $script: vars
    # set during discovery are NULL in the run phase, so paths built here).
    # Temp root comes from [System.IO.Path]::GetTempPath(), the cross-platform
    # temp root.
    $script:SuiteRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("rubbish-cleaner-tests\{0}\report" -f $PID)
    $script:VerifyScript = Join-Path $PSScriptRoot '..\..\scripts\verify-report.ps1'
    . (Join-Path $PSScriptRoot '..\..\scripts\lib\platform.ps1')
    $script:TestDrive = if ($script:IsWin) { 'C:' } else { '/' }
    $script:TestDriveInfo = Resolve-FixedDrive -Drive $script:TestDrive
    if ($null -eq $script:TestDriveInfo) { throw 'no fixed drive available for report fixture' }
    $script:DriveLetter = $script:TestDriveInfo.Id
    $script:QuarantineLeaf = 'q-{0}.dat' -f $PID
    $script:QuarantineDir = Get-DefaultQuarantineDir -DriveId $script:DriveLetter

    $script:RunDir = Join-Path $script:SuiteRoot 'run'
    New-Item -ItemType Directory -Path $script:RunDir -Force | Out-Null
    $script:Preflight = Join-Path $script:RunDir 'preflight.txt'
    $script:CleanupCsv = Join-Path $script:RunDir 'cleanup-errors.csv'
    $script:CandidatesCsv = Join-Path $script:RunDir 'candidates.csv'
    $script:Summary = Join-Path $script:RunDir 'summary.md'
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)

    # ---- preflight.txt ---------------------------------------------------
    [System.IO.File]::WriteAllLines($script:Preflight, @(
            'BASELINE_FREE_BYTES=1000000000',
            'TOTAL_BYTES=500000000000',
            'PROCESSES='
        ), $utf8NoBom)

    # ---- fixture paths ---------------------------------------------------
    $f = $script:RunDir
    $aTmp = Join-Path $f 'Temp\a.tmp'          # OK row      - never created on disk
    $bLog = Join-Path $f 'tmp\b.log'           # OK row      - never created on disk
    $empty1 = Join-Path $f 'empty1'            # OK row      - never created on disk
    $locked = Join-Path $f 'locked.txt'        # SKIP_LOCKED - must EXIST (survives)
    $qPath = Join-Path $f $script:QuarantineLeaf  # QUARANTINED - original never created

    Set-Content -LiteralPath $locked -Value 'locked payload'

    # ---- cleanup-errors.csv (Timestamp|Phase|Action|Path|ErrorMessage|Disposition)
    $cleanupLines = New-Object System.Collections.Generic.List[string]
    $cleanupLines.Add('Timestamp|Phase|Action|Path|ErrorMessage|Disposition')
    $ts = '2026-07-31T00:00:00.0000000+00:00'
    $cleanupLines.Add("$ts|root-temps|Remove|$aTmp||OK")
    $cleanupLines.Add("$ts|root-temps|Remove|$bLog||OK")
    $cleanupLines.Add("$ts|empty-dirs|Remove|$empty1||OK")
    $cleanupLines.Add("$ts|root-logs|Remove|$locked|locked by test|SKIP_LOCKED")
    $cleanupLines.Add("$ts|duplicate-archives|Quarantine|$qPath||QUARANTINED")
    [System.IO.File]::WriteAllLines($script:CleanupCsv, $cleanupLines, $utf8NoBom)

    # ---- candidates.csv (Category|Risk|Path|SizeBytes|FileCount|Action) ---
    $candLines = New-Object System.Collections.Generic.List[string]
    $candLines.Add('Category|Risk|Path|SizeBytes|FileCount|Action')
    $candLines.Add("root-temps|SAFE|$aTmp|100|1|delete")
    $candLines.Add("root-temps|SAFE|$bLog|200|1|delete")
    $candLines.Add("empty-dirs|SAFE|$empty1|0|0|delete")
    $candLines.Add("root-logs|SAFE|$locked|300|1|delete")
    $candLines.Add("duplicate-archives|ASK|$qPath|500|1|ask")
    [System.IO.File]::WriteAllLines($script:CandidatesCsv, $candLines, $utf8NoBom)

    # ---- quarantine copy for the QUARANTINED live assertion (b1) ---------
    New-Item -ItemType Directory -Path $script:QuarantineDir -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $script:QuarantineDir $script:QuarantineLeaf) -Value 'quarantined copy'

    # ---- run verify-report (read-only apart from <RunDir>\summary.md) -----
    & $script:VerifyScript -Drive $script:TestDrive -RunDir $script:RunDir | Out-Null
}

Describe 'verify-report summary' {
    It 'writes summary.md containing all 8 required sections' {
        Test-Path -LiteralPath $script:Summary | Should -BeTrue
        $text = [System.IO.File]::ReadAllText($script:Summary)
        foreach ($heading in @(
                '## 1. Baseline Free Space',
                '## 2. Final Free Space',
                '## 3. Total Freed',
                '## 4. Per-Category Freed',
                '## 5. Skipped Items Table',
                '## 6. Quarantine Note',
                '## 7. Verification Assertions',
                '## 8. Recommendations')) {
            $text.Contains($heading) | Should -BeTrue -Because ("heading '{0}' is present" -f $heading)
        }
    }

    It 'section 1 reports the fixture baseline free space' {
        $text = [System.IO.File]::ReadAllText($script:Summary)
        $text.Contains('BASELINE_FREE_BYTES = **1,000,000,000 bytes**') | Should -BeTrue
    }

    It 'section 3 computes final - baseline and prints the +-500 MB tolerance line' {
        $text = [System.IO.File]::ReadAllText($script:Summary)
        $text.Contains('## 3. Total Freed') | Should -BeTrue
        $text.Contains('Final - Baseline') | Should -BeTrue
        $text.Contains('Tolerance: within **+-500 MB') | Should -BeTrue
    }

    It 'section 4 per-category freed estimates reconcile with the fixture (OK/QUARANTINED joined by path)' {
        $text = [System.IO.File]::ReadAllText($script:Summary)
        $text.Contains('| **Total** | **5** | **1,100** | **800** | **0.00 GiB** |') | Should -BeTrue
        $text.Contains('| duplicate-archives | 1 | 500 | 500 | 0.00 GiB |') | Should -BeTrue
        $text.Contains('| empty-dirs | 1 | 0 | 0 | 0.00 GiB |') | Should -BeTrue
        $text.Contains('| root-logs | 1 | 300 | 0 | 0.00 GiB |') | Should -BeTrue
        $text.Contains('| root-temps | 2 | 300 | 300 | 0.00 GiB |') | Should -BeTrue
    }

    It 'section 5 disposition counts match the fixture rows (3 OK, 1 SKIP_LOCKED, 1 QUARANTINED)' {
        $text = [System.IO.File]::ReadAllText($script:Summary)
        $text.Contains('Total rows: **5**') | Should -BeTrue
        $text.Contains('| OK | 3 |') | Should -BeTrue
        $text.Contains('| QUARANTINED | 1 |') | Should -BeTrue
        $text.Contains('| SKIP_LOCKED | 1 |') | Should -BeTrue
    }

    It 'section 6 names the quarantined file and the quarantine location' {
        $text = [System.IO.File]::ReadAllText($script:Summary)
        $text.Contains($script:QuarantineLeaf) | Should -BeTrue
        $text.Contains($script:QuarantineDir) | Should -BeTrue
    }

    It 'section 7 live assertions all pass (6/6)' {
        $text = [System.IO.File]::ReadAllText($script:Summary)
        $text.Contains('Result: **6/6 PASS**') | Should -BeTrue
    }
}

AfterAll {
    # Remove our own quarantine copy, then the quarantine dir if left empty.
    $copy = Join-Path $script:QuarantineDir $script:QuarantineLeaf
    if (Test-Path -LiteralPath $copy) {
        Remove-Item -LiteralPath $copy -Force -ErrorAction SilentlyContinue
    }
    if ((Test-Path -LiteralPath $script:QuarantineDir) -and -not @(Get-ChildItem -LiteralPath $script:QuarantineDir -Force -ErrorAction SilentlyContinue).Count) {
        Remove-Item -LiteralPath $script:QuarantineDir -Force -ErrorAction SilentlyContinue
    }

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
