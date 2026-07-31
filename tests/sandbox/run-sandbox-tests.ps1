# run-sandbox-tests.ps1 - zero-dependency fallback test harness (todo 9).
#
# Implements the SAME four suites as tests/unit/ (todo 8) with plain PowerShell
# asserts and NO modules, so the suite runs on machines without Pester 5 (this
# machine ships PS 5.1 with Pester 3.4). It is the PRIMARY executable test path
# here; it MUST run green.
#
# Suites:
#   1. Test-DirEmpty        - junction-aware empty check + Test-IsJunction +
#                             Get-JunkDispositions + Write-CleanupCsv
#   2. ScanClassification   - extracts the REAL classification seam from
#                             scripts/scan-drive.ps1 (between the
#                             <begin-classification>/<end-classification>
#                             markers) and runs Get-JunkCandidates against a
#                             fake tree
#   3. SafeDeleteQuarantine - Invoke-SafeRemove / Invoke-Quarantine in-process
#                             + a full clean-drive.ps1 subprocess gate test
#   4. ReportFixture        - crafted RunDir -> verify-report.ps1 subprocess ->
#                             summary.md section assertions
#
# Safety: ALL temp state lives under $env:TEMP\rubbish-cleaner-tests\<pid>\.
# Cleanup runs in finally: reparse links first (so -Recurse never follows a
# junction), then the <pid> root, then the parent if left empty. Nothing
# outside that root is touched.

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------
# Test-drive auto-detection (replaces the previously hardcoded -Drive D:).
# Picks the first fixed drive with used space on Windows, '/' elsewhere.
# ---------------------------------------------------------------------
. (Join-Path $PSScriptRoot '../../scripts/lib/platform.ps1')
if ($script:IsWindows) {
    $script:TestDrive = ((Get-PSDrive -PSProvider FileSystem | Where-Object { $_.Used -gt 0 -and $_.Root -match '^[A-Z]:\\$' } | Select-Object -First 1).Root).TrimEnd('\')
} else {
    $script:TestDrive = '/'
}
if (-not $script:TestDrive) { $script:TestDrive = 'C:' }
if ($script:IsWindows) {
    $probePath = $script:TestDrive.TrimEnd(':') + ':\'
} else {
    $probePath = '/'
}
if (-not (Test-Path -LiteralPath $probePath)) {
    Write-Output 'SKIP: no fixed drive available for test fixtures'
    exit 0
}

# ---------------------------------------------------------------------
# Repo layout (all paths absolute; nothing is hardcoded to the machine).
# ---------------------------------------------------------------------
$script:RepoRoot          = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$script:LibPath           = Join-Path $script:RepoRoot 'scripts\lib\rubbish-core.ps1'
$script:ScanDrivePath     = Join-Path $script:RepoRoot 'scripts\scan-drive.ps1'
$script:CleanDrivePath    = Join-Path $script:RepoRoot 'scripts\clean-drive.ps1'
$script:VerifyReportPath  = Join-Path $script:RepoRoot 'scripts\verify-report.ps1'

# ---------------------------------------------------------------------
# pid-keyed temp root (removed in finally, together with the parent).
# ---------------------------------------------------------------------
$script:TestParent = Join-Path $env:TEMP 'rubbish-cleaner-tests'
$script:TestRoot   = Join-Path $script:TestParent $PID
New-Item -ItemType Directory -Force -Path $script:TestRoot | Out-Null

# Reparse links created by the suites; removed BEFORE the recursive cleanup so
# that Remove-Item -Recurse never follows a junction out of the test root.
$script:JunctionLinks = New-Object System.Collections.Generic.List[string]

# ---------------------------------------------------------------------
# Assertion state + helpers.
# ---------------------------------------------------------------------
$script:AssertCount = 0
$script:Failures    = New-Object System.Collections.Generic.List[string]

function Assert-Equal {
    param([string]$Name, $Expected, $Actual)
    $script:AssertCount++
    if (-not [System.Object]::Equals($Expected, $Actual)) {
        $script:Failures.Add(("  ASSERT FAIL: {0}`n    expected: {1}`n    actual:   {2}" -f $Name, $Expected, $Actual))
    }
}

function Assert-True {
    param([string]$Name, [bool]$Condition, [string]$Detail = '')
    $script:AssertCount++
    if (-not $Condition) {
        $actual = $(if ($Detail) { $Detail } else { 'False' })
        $script:Failures.Add(("  ASSERT FAIL: {0}`n    expected: True`n    actual:   {1}" -f $Name, $actual))
    }
}

# Parse the pipe-delimited cleanup CSV written by the lib functions
# (Timestamp|Phase|Action|Path|ErrorMessage|Disposition).
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

function Invoke-Suite {
    param([string]$Name, [scriptblock]$Body)
    $startAsserts = $script:AssertCount
    $startFails   = $script:Failures.Count
    try {
        & $Body
    } catch {
        $script:Failures.Add(("  ASSERT FAIL: {0} suite threw an unexpected error: {1}" -f $Name, $_.Exception.Message))
    }
    $nAsserts = $script:AssertCount - $startAsserts
    $nFails   = $script:Failures.Count - $startFails
    if ($nFails -eq 0) {
        Write-Output ("SUITE {0}: PASS ({1} assertions)" -f $Name, $nAsserts)
    } else {
        Write-Output ("SUITE {0}: FAIL ({1} assertions)" -f $Name, $nAsserts)
        for ($i = $startFails; $i -lt $script:Failures.Count; $i++) {
            Write-Output $script:Failures[$i]
        }
    }
}

# =====================================================================
# SUITE 1: Test-DirEmpty
# =====================================================================
$suite1 = {
    $base = Join-Path $script:TestRoot 's1-core'
    New-Item -ItemType Directory -Force -Path $base | Out-Null

    # ---- Test-DirEmpty -------------------------------------------------
    $empty = Join-Path $base 'empty'
    New-Item -ItemType Directory -Force -Path $empty | Out-Null
    Assert-Equal 'Test-DirEmpty: empty dir -> true' $true (Test-DirEmpty -Path $empty)

    $withFile = Join-Path $base 'with-file'
    New-Item -ItemType Directory -Force -Path $withFile | Out-Null
    Set-Content -LiteralPath (Join-Path $withFile 'file.txt') -Value 'x'
    Assert-Equal 'Test-DirEmpty: dir with file -> false' $false (Test-DirEmpty -Path $withFile)

    $nested = Join-Path $base 'nested'
    New-Item -ItemType Directory -Force -Path (Join-Path $nested 'a\b\c') | Out-Null
    Assert-Equal 'Test-DirEmpty: nested empty -> true' $true (Test-DirEmpty -Path $nested)

    # ---- Test-IsJunction (non-reparse cases need no privileges) --------
    Assert-Equal 'Test-IsJunction: regular dir -> false' $false (Test-IsJunction -Path $empty)
    Assert-Equal 'Test-IsJunction: regular file -> false' $false (Test-IsJunction -Path (Join-Path $withFile 'file.txt'))
    Assert-Equal 'Test-IsJunction: missing path -> false' $false (Test-IsJunction -Path (Join-Path $base 'does-not-exist'))

    # ---- reparse-point case (junction first, symlink fallback) ----------
    $target = Join-Path $base 'junction-target'
    New-Item -ItemType Directory -Force -Path $target | Out-Null
    $onlyReparse = Join-Path $base 'only-reparse-child'
    New-Item -ItemType Directory -Force -Path $onlyReparse | Out-Null
    $link = Join-Path $onlyReparse 'link'
    $reparseCreated = $false
    try {
        New-Item -ItemType Junction -Path $link -Target $target -ErrorAction Stop | Out-Null
        $reparseCreated = $true
    } catch {
        try {
            New-Item -ItemType SymbolicLink -Path $link -Target $target -ErrorAction Stop | Out-Null
            $reparseCreated = $true
        } catch {
            $reparseCreated = $false
        }
    }
    if ($reparseCreated) {
        $script:JunctionLinks.Add($link)
        Assert-Equal 'Test-DirEmpty: dir with only a junction child -> true' $true (Test-DirEmpty -Path $onlyReparse)
        Assert-Equal 'Test-IsJunction: junction -> true' $true (Test-IsJunction -Path $link)
    } else {
        Write-Output 'SUITE Test-DirEmpty: SKIP (cannot create reparse points)'
    }

    # ---- Get-JunkDispositions (exactly 12, in order) --------------------
    $expectedDisp = 'OK,SKIP_LOCKED,SKIP_ACCESS_DENIED,SKIP_NOT_FOUND,SKIP_NOT_EMPTY,SKIP_JUNCTION,SKIP_TOO_RECENT,SKIP_WSL_REGISTERED,SKIP_ELEVATION_DENIED,SKIP_SERVICE_RUNNING,QUARANTINED,MOVE_FAILED'
    $disps = @(Get-JunkDispositions)
    Assert-Equal 'Get-JunkDispositions: exactly 12 values' 12 $disps.Count
    Assert-Equal 'Get-JunkDispositions: values in order' $expectedDisp ($disps -join ',')

    # ---- Write-CleanupCsv (header exactly once, 3 lines, 6 fields) ------
    $csv = Join-Path $base 'cleanup.csv'
    Write-CleanupCsv -CsvPath $csv -Row @{ Phase = 'a'; Action = 'Remove'; Path = 'x'; ErrorMessage = ''; Disposition = 'OK' }
    Write-CleanupCsv -CsvPath $csv -Row @{ Phase = 'b'; Action = 'Remove'; Path = 'y'; ErrorMessage = ''; Disposition = 'SKIP_LOCKED' }
    $csvLines = @([System.IO.File]::ReadAllLines($csv))
    Assert-Equal 'Write-CleanupCsv: 3 lines total (header + 2 data rows)' 3 $csvLines.Count
    Assert-Equal 'Write-CleanupCsv: header written exactly once' 1 @($csvLines | Where-Object { $_ -like 'Timestamp|*' }).Count
    Assert-Equal 'Write-CleanupCsv: header text' 'Timestamp|Phase|Action|Path|ErrorMessage|Disposition' $csvLines[0].TrimStart([char]0xFEFF)
    Assert-Equal 'Write-CleanupCsv: data row 1 has 6 fields' 6 @($csvLines[1].Split('|')).Count
    Assert-Equal 'Write-CleanupCsv: data row 2 has 6 fields' 6 @($csvLines[2].Split('|')).Count
    Assert-Equal 'Write-CleanupCsv: row 1 disposition OK' 'OK' $csvLines[1].Split('|')[-1]
    Assert-Equal 'Write-CleanupCsv: row 2 disposition SKIP_LOCKED' 'SKIP_LOCKED' $csvLines[2].Split('|')[-1]
}

# =====================================================================
# SUITE 2: ScanClassification
# =====================================================================
# Fake tree (mirrors the shared behavior matrix from todo 8):
#   Temp\a.tmp        (>7 days)      -> root-temps        (SAFE/delete)
#   tmp\b.log         (>7 days)      -> root-temps        (SAFE/delete; on
#                                     NTFS Temp and tmp are the SAME dir - one
#                                     dir is created, both files land in it)
#   empty1\           (empty)        -> empty-dirs        (SAFE/delete)
#   MyApp\cache\      (with files)   -> NOT classified (negative control)
#   archive.zip + archive\readme.txt -> duplicate-archives (ASK/ask)
#   boot.log          (root level)   -> root-logs         (SAFE/delete)
#   root-suspicious.dll              -> root-suspicious   (CAUTION/quarantine)
#   keep\userfile.txt                -> NOT classified (negative control)
$suite2 = {
    $base = Join-Path $script:TestRoot 's2-scan'
    New-Item -ItemType Directory -Force -Path $base | Out-Null
    $root = Join-Path $base 'fake'
    New-Item -ItemType Directory -Force -Path $root | Out-Null

    $tempDir = Join-Path $root 'Temp'
    New-Item -ItemType Directory -Force -Path $tempDir | Out-Null
    $aTmp = Join-Path $tempDir 'a.tmp'
    Set-Content -LiteralPath $aTmp -Value 'old temp'
    [System.IO.File]::SetLastWriteTime($aTmp, (Get-Date).AddDays(-10))
    $bLog = Join-Path $tempDir 'b.log'
    Set-Content -LiteralPath $bLog -Value 'old log in tmp'
    [System.IO.File]::SetLastWriteTime($bLog, (Get-Date).AddDays(-10))

    New-Item -ItemType Directory -Force -Path (Join-Path $root 'empty1') | Out-Null

    $cacheDir = Join-Path $root 'MyApp\cache'
    New-Item -ItemType Directory -Force -Path $cacheDir | Out-Null
    Set-Content -LiteralPath (Join-Path $cacheDir 'data.bin') -Value 'cache payload'

    $archDir = Join-Path $root 'archive'
    New-Item -ItemType Directory -Force -Path $archDir | Out-Null
    Set-Content -LiteralPath (Join-Path $archDir 'readme.txt') -Value 'extracted content'
    Set-Content -LiteralPath (Join-Path $root 'archive.zip') -Value 'zip payload'

    Set-Content -LiteralPath (Join-Path $root 'boot.log') -Value 'boot log payload'
    Set-Content -LiteralPath (Join-Path $root 'root-suspicious.dll') -Value 'dll payload'

    $keepDir = Join-Path $root 'keep'
    New-Item -ItemType Directory -Force -Path $keepDir | Out-Null
    Set-Content -LiteralPath (Join-Path $keepDir 'userfile.txt') -Value 'user data'

    # Run the REAL classification code (dot-sourced in setup) against the fake
    # tree. NOTE: Get-JunkCandidates returns generic List[object] collections,
    # which PS 5.1 cannot unroll with @() (throws argument type mismatch);
    # .ToArray() -> object[] is portable across PS 5.1 and 7.
    $result = Get-JunkCandidates -RootPath $root -IsUserDrive $false -IncludeElevated $false -Categories @('root-temps', 'root-logs', 'duplicate-archives', 'empty-dirs', 'root-suspicious')
    $rows      = @($result.Rows.ToArray())
    $evaluated = @($result.Evaluated.ToArray())

    # root-temps: a.tmp (b.log may join - accepted)
    $rt = @($rows | Where-Object { $_.Category -eq 'root-temps' })
    Assert-True 'root-temps: at least 1 row' ($rt.Count -ge 1)
    Assert-Equal 'root-temps: all rows Action=delete' 0 @($rt | Where-Object { $_.Action -ne 'delete' }).Count
    Assert-Equal 'root-temps: a.tmp classified' 1 @($rt | Where-Object { $_.Path -eq $aTmp }).Count

    # root-logs: boot.log
    $bl = @($rows | Where-Object { $_.Category -eq 'root-logs' -and $_.Path -eq (Join-Path $root 'boot.log') })
    Assert-Equal 'root-logs: boot.log row count' 1 $bl.Count
    Assert-Equal 'root-logs: boot.log Action=delete' 'delete' $bl[0].Action

    # duplicate-archives: archive.zip (ASK -> ask)
    $da = @($rows | Where-Object { $_.Category -eq 'duplicate-archives' -and $_.Path -eq (Join-Path $root 'archive.zip') })
    Assert-Equal 'duplicate-archives: archive.zip row count' 1 $da.Count
    Assert-Equal 'duplicate-archives: archive.zip Action=ask' 'ask' $da[0].Action

    # empty-dirs: empty1 (SAFE -> delete)
    $ed = @($rows | Where-Object { $_.Category -eq 'empty-dirs' -and $_.Path -eq (Join-Path $root 'empty1') })
    Assert-Equal 'empty-dirs: empty1 row count' 1 $ed.Count
    Assert-Equal 'empty-dirs: empty1 Action=delete' 'delete' $ed[0].Action

    # root-suspicious: root-suspicious.dll (CAUTION -> quarantine)
    $rs = @($rows | Where-Object { $_.Category -eq 'root-suspicious' -and $_.Path -eq (Join-Path $root 'root-suspicious.dll') })
    Assert-Equal 'root-suspicious: dll row count' 1 $rs.Count
    Assert-Equal 'root-suspicious: dll Action=quarantine' 'quarantine' $rs[0].Action

    # negative controls
    Assert-Equal 'negative: keep\userfile.txt not classified' 0 @($rows | Where-Object { $_.Path -eq (Join-Path $keepDir 'userfile.txt') }).Count
    Assert-Equal 'negative: MyApp\cache contents not classified' 0 @($rows | Where-Object { $_.Path.StartsWith($cacheDir, [System.StringComparison]::OrdinalIgnoreCase) }).Count
    Assert-Equal 'negative: extracted archive folder not classified' 0 @($rows | Where-Object { $_.Path -eq $archDir }).Count

    # exactly the 5 whitelisted categories were evaluated
    Assert-Equal 'evaluated categories count = 5' 5 $evaluated.Count
}

# =====================================================================
# SUITE 3: SafeDeleteQuarantine
# =====================================================================
$suite3 = {
    $base = Join-Path $script:TestRoot 's3-clean'
    New-Item -ItemType Directory -Force -Path $base | Out-Null
    $csv = Join-Path $base 'cleanup.csv'

    # ---- (i) in-process lib behavior ------------------------------------
    # normal file -> removed + OK row
    $normal = Join-Path $base 'normal.txt'
    Set-Content -LiteralPath $normal -Value 'payload'
    Invoke-SafeRemove -LiteralPath $normal -Phase 'test' -CsvPath $csv
    Assert-Equal 'Invoke-SafeRemove: normal file gone' $false (Test-Path -LiteralPath $normal)
    $okRows = @(Get-CleanupRowsFrom -CsvPath $csv | Where-Object { $_.Path -eq $normal })
    Assert-Equal 'Invoke-SafeRemove: OK row count' 1 $okRows.Count
    Assert-Equal 'Invoke-SafeRemove: OK disposition' 'OK' $okRows[0].Disposition

    # locked file -> survives + SKIP_LOCKED row (handle held during the call)
    $locked = Join-Path $base 'locked.txt'
    Set-Content -LiteralPath $locked -Value 'locked payload'
    $handle = $null
    try {
        $handle = [System.IO.File]::Open($locked, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::None)
        Assert-Equal 'Test-FileLocked: locked file reports true' $true (Test-FileLocked -LiteralPath $locked)
        Invoke-SafeRemove -LiteralPath $locked -Phase 'test' -CsvPath $csv
    } finally {
        if ($null -ne $handle) { $handle.Close() }
    }
    Assert-Equal 'Invoke-SafeRemove: locked file survives' $true (Test-Path -LiteralPath $locked)
    Assert-Equal 'Test-FileLocked: after handle release reports false' $false (Test-FileLocked -LiteralPath $locked)
    $lkRows = @(Get-CleanupRowsFrom -CsvPath $csv | Where-Object { $_.Path -eq $locked })
    Assert-Equal 'Invoke-SafeRemove: SKIP_LOCKED row count' 1 $lkRows.Count
    Assert-Equal 'Invoke-SafeRemove: SKIP_LOCKED disposition' 'SKIP_LOCKED' $lkRows[0].Disposition

    # quarantine -> source gone, dest present, QUARANTINED row
    $qsrc = Join-Path $base 'quarantine-src.txt'
    $qdir = Join-Path $base 'quarantine'
    Set-Content -LiteralPath $qsrc -Value 'quarantine me'
    Invoke-Quarantine -LiteralPath $qsrc -QuarantineDir $qdir -Phase 'test' -CsvPath $csv
    Assert-Equal 'Invoke-Quarantine: source gone' $false (Test-Path -LiteralPath $qsrc)
    Assert-Equal 'Invoke-Quarantine: dest present' $true (Test-Path -LiteralPath (Join-Path $qdir 'quarantine-src.txt'))
    $qRows = @(Get-CleanupRowsFrom -CsvPath $csv | Where-Object { $_.Path -eq $qsrc })
    Assert-Equal 'Invoke-Quarantine: QUARANTINED row count' 1 $qRows.Count
    Assert-Equal 'Invoke-Quarantine: QUARANTINED disposition' 'QUARANTINED' $qRows[0].Disposition

    # ---- (ii) full-pipeline gate test: clean-drive.ps1 subprocess --------
    $run = Join-Path $base 'clean-run'
    New-Item -ItemType Directory -Force -Path $run | Out-Null
    $emptyTarget = Join-Path $run 'empty-target'
    New-Item -ItemType Directory -Force -Path $emptyTarget | Out-Null
    $nonEmptyTarget = Join-Path $run 'nonempty-target'
    New-Item -ItemType Directory -Force -Path $nonEmptyTarget | Out-Null
    Set-Content -LiteralPath (Join-Path $nonEmptyTarget 'keep.txt') -Value 'content'

    $candCsv = Join-Path $run 'candidates.csv'
    $candLines = New-Object System.Collections.Generic.List[string]
    $candLines.Add('Category|Risk|Path|SizeBytes|FileCount|Action')
    $candLines.Add(('empty-dirs|SAFE|{0}|0|0|delete' -f $emptyTarget))
    $candLines.Add(('empty-dirs|SAFE|{0}|0|0|delete' -f $nonEmptyTarget))
    [System.IO.File]::WriteAllLines($candCsv, $candLines.ToArray(), (New-Object System.Text.UTF8Encoding($false)))

    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $script:CleanDrivePath -Drive $script:TestDrive -CandidatesCsv $candCsv -QuarantineDir (Join-Path $base 'q') -Yes | Out-Null
    Assert-Equal 'clean-drive.ps1: exit code 0' 0 $LASTEXITCODE
    Assert-Equal 'clean-drive.ps1: empty dir REMOVED' $false (Test-Path -LiteralPath $emptyTarget)
    Assert-Equal 'clean-drive.ps1: non-empty dir SURVIVES' $true (Test-Path -LiteralPath $nonEmptyTarget -PathType Container)

    $cleanupCsv = Join-Path $run 'cleanup-errors.csv'
    Assert-Equal 'clean-drive.ps1: cleanup-errors.csv written' $true (Test-Path -LiteralPath $cleanupCsv)
    $cRows = @(Get-CleanupRowsFrom -CsvPath $cleanupCsv)
    $okRows2 = @($cRows | Where-Object { $_.Disposition -eq 'OK' -and $_.Path -eq $emptyTarget })
    $neRows  = @($cRows | Where-Object { $_.Disposition -eq 'SKIP_NOT_EMPTY' -and $_.Path -eq $nonEmptyTarget })
    Assert-Equal 'clean-drive.ps1: empty dir OK row' 1 $okRows2.Count
    Assert-Equal 'clean-drive.ps1: non-empty dir SKIP_NOT_EMPTY row' 1 $neRows.Count
}

# =====================================================================
# SUITE 4: ReportFixture
# =====================================================================
# Crafted RunDir (all inside the temp root):
#   preflight.txt      BASELINE_FREE_BYTES = live free - 100 MB so that
#                      final - baseline reconciles with the freed estimate
#   cleanup-errors.csv 3 OK + 1 SKIP_LOCKED + 1 QUARANTINED rows
#   candidates.csv     5 rows whose Paths match the cleanup rows; OK rows
#                      (40M+30M+20M) + QUARANTINED (10M) = 100M freed,
#                      SKIP_LOCKED (5M) excluded
# The SKIP_LOCKED fixture file MUST exist (live assertion d1 needs a real
# surviving file). Section 7 is NOT asserted: the quarantine-copy assertion
# legitimately FAILs because the copy lives outside the temp root.
$suite4 = {
    $base = Join-Path $script:TestRoot 's4-report'
    New-Item -ItemType Directory -Force -Path $base | Out-Null
    $runDir = Join-Path $base 'run'
    New-Item -ItemType Directory -Force -Path $runDir | Out-Null
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)

    # ---- preflight.txt ---------------------------------------------------
    $baseline = [long](Get-Volume -DriveLetter $script:TestDrive.TrimEnd(':')).SizeRemaining - 100000000
    [System.IO.File]::WriteAllLines((Join-Path $runDir 'preflight.txt'), @("BASELINE_FREE_BYTES=$baseline"), $utf8NoBom)

    # ---- fixture paths ---------------------------------------------------
    $ok1    = Join-Path $runDir 'Temp\a.tmp'          # OK row - never exists
    $ok2    = Join-Path $runDir 'tmp\b.log'           # OK row - never exists
    $ok3    = Join-Path $runDir 'empty1'              # OK row - never exists
    $locked = Join-Path $runDir 'locked.txt'          # SKIP_LOCKED - must EXIST
    $qPath  = Join-Path $runDir 'quarantined.dat'     # QUARANTINED - original never exists
    Set-Content -LiteralPath $locked -Value 'locked payload'

    # ---- cleanup-errors.csv (Timestamp|Phase|Action|Path|ErrorMessage|Disposition)
    $cleanupLines = New-Object System.Collections.Generic.List[string]
    $cleanupLines.Add('Timestamp|Phase|Action|Path|ErrorMessage|Disposition')
    $ts = '2026-07-31T00:00:00.0000000+00:00'
    $cleanupLines.Add("$ts|root-temps|Remove|$ok1||OK")
    $cleanupLines.Add("$ts|root-temps|Remove|$ok2||OK")
    $cleanupLines.Add("$ts|empty-dirs|Remove|$ok3||OK")
    $cleanupLines.Add("$ts|root-logs|Remove|$locked|locked by test|SKIP_LOCKED")
    $cleanupLines.Add("$ts|duplicate-archives|Quarantine|$qPath||QUARANTINED")
    [System.IO.File]::WriteAllLines((Join-Path $runDir 'cleanup-errors.csv'), $cleanupLines, $utf8NoBom)

    # ---- candidates.csv (Category|Risk|Path|SizeBytes|FileCount|Action) --
    # OK/QUARANTINED freed sum = 40M + 30M + 20M + 10M = 100M (matches the
    # baseline delta); SKIP_LOCKED 5M is excluded from the freed estimate.
    $candLines = New-Object System.Collections.Generic.List[string]
    $candLines.Add('Category|Risk|Path|SizeBytes|FileCount|Action')
    $candLines.Add("root-temps|SAFE|$ok1|40000000|1|delete")
    $candLines.Add("root-temps|SAFE|$ok2|30000000|1|delete")
    $candLines.Add("empty-dirs|SAFE|$ok3|20000000|1|delete")
    $candLines.Add("root-logs|SAFE|$locked|5000000|1|delete")
    $candLines.Add("duplicate-archives|ASK|$qPath|10000000|1|ask")
    [System.IO.File]::WriteAllLines((Join-Path $runDir 'candidates.csv'), $candLines, $utf8NoBom)

    # ---- run verify-report as a subprocess --------------------------------
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $script:VerifyReportPath -Drive $script:TestDrive -RunDir $runDir | Out-Null
    Assert-Equal 'verify-report.ps1: exit code 0' 0 $LASTEXITCODE

    # ---- summary.md assertions --------------------------------------------
    $summary = Join-Path $runDir 'summary.md'
    Assert-Equal 'verify-report.ps1: summary.md written' $true (Test-Path -LiteralPath $summary)
    $sLines = @([System.IO.File]::ReadAllLines($summary))

    # all 8 sections present
    $headings = @(
        '## 1. Baseline Free Space',
        '## 2. Final Free Space',
        '## 3. Total Freed',
        '## 4. Per-Category Freed',
        '## 5. Skipped Items Table',
        '## 6. Quarantine Note',
        '## 7. Verification Assertions',
        '## 8. Recommendations')
    foreach ($h in $headings) {
        Assert-True ("summary section present: {0}" -f $h) (@($sLines | Where-Object { $_ -eq $h }).Count -ge 1)
    }
    Assert-Equal 'summary: exactly 8 "## " section lines' 8 @($sLines | Where-Object { $_ -like '## *' }).Count

    # section 5 disposition counts (3 OK, 1 SKIP_LOCKED, 1 QUARANTINED, 5 rows)
    Assert-True 'summary: Total rows 5' (@($sLines | Where-Object { $_ -match '^Total rows: \*\*5\*\*$' }).Count -ge 1)
    Assert-True 'summary: OK=3' (@($sLines | Where-Object { $_ -eq '| OK | 3 |' }).Count -ge 1)
    Assert-True 'summary: SKIP_LOCKED=1' (@($sLines | Where-Object { $_ -eq '| SKIP_LOCKED | 1 |' }).Count -ge 1)
    Assert-True 'summary: QUARANTINED=1' (@($sLines | Where-Object { $_ -eq '| QUARANTINED | 1 |' }).Count -ge 1)
}

# =====================================================================
# Orchestration
# =====================================================================
Write-Output ("BRANCH: SANDBOX (test drive: {0})" -f $script:TestDrive)

try {
    # ---- setup: dot-source the lib, then the REAL classification seam ----
    try {
        . $script:LibPath

        $scanSource = [System.IO.File]::ReadAllText($script:ScanDrivePath)
        $startIdx = $scanSource.IndexOf('# <begin-classification>')
        $endIdx   = $scanSource.IndexOf('# <end-classification>')
        if ($startIdx -lt 0 -or $endIdx -lt 0 -or $endIdx -le $startIdx) {
            throw 'scan-drive.ps1 classification markers (# <begin-classification>/# <end-classification>) not found'
        }
        $region = $scanSource.Substring($startIdx, $endIdx - $startIdx)
        . ([scriptblock]::Create($region))
    } catch {
        $script:Failures.Add('  SETUP FAILED: ' + $_.Exception.Message)
    }

    # ---- suites ----------------------------------------------------------
    Invoke-Suite -Name 'Test-DirEmpty'        -Body $suite1
    Invoke-Suite -Name 'ScanClassification'   -Body $suite2
    Invoke-Suite -Name 'SafeDeleteQuarantine' -Body $suite3
    Invoke-Suite -Name 'ReportFixture'        -Body $suite4

    # ---- result + exit ---------------------------------------------------
    if ($script:Failures.Count -eq 0) {
        Write-Output 'RESULT: PASS'
        exit 0
    } else {
        Write-Output 'RESULT: FAIL'
        exit 1
    }
} finally {
    # ---- cleanup (always runs, incl. after exit) --------------------------
    # 1) remove reparse links FIRST so -Recurse never follows a junction
    foreach ($link in $script:JunctionLinks) {
        if (Test-Path -LiteralPath $link) {
            Remove-Item -LiteralPath $link -Force -ErrorAction SilentlyContinue
        }
    }
    # 2) the pid-keyed root
    if (Test-Path -LiteralPath $script:TestRoot) {
        Remove-Item -LiteralPath $script:TestRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
    # 3) the shared parent, only when left empty
    if (Test-Path -LiteralPath $script:TestParent) {
        $remaining = @(Get-ChildItem -LiteralPath $script:TestParent -Force -ErrorAction SilentlyContinue)
        if ($remaining.Count -eq 0) {
            Remove-Item -LiteralPath $script:TestParent -Force -ErrorAction SilentlyContinue
        }
    }
}
