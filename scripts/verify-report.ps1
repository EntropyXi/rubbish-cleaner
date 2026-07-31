# verify-report.ps1 - verification + summary report for the rubbish-cleaner pipeline.
#
# After a scan (scan-drive.ps1) and optionally a clean (clean-drive.ps1) run,
# this script verifies the outcome and writes <RunDir>\summary.md with EXACTLY
# these 8 sections:
#   ## 1. Baseline Free Space
#   ## 2. Final Free Space
#   ## 3. Total Freed
#   ## 4. Per-Category Freed
#   ## 5. Skipped Items Table
#   ## 6. Quarantine Note
#   ## 7. Verification Assertions
#   ## 8. Recommendations
#
# Flow:
#   (1) dot-source the safety function library
#   (2) read <RunDir>\preflight.txt baseline free space
#   (3) re-read (Get-Volume -DriveLetter X).SizeRemaining as final
#   (4) total freed = final - baseline
#   (5) read <RunDir>\cleanup-errors.csv (if present) and count rows per Disposition
#   (6) per-category freed estimate = sum of SizeBytes from candidates.csv rows
#       whose cleanup disposition (joined by Path) was OK/QUARANTINED; when the
#       cleanup CSV is missing (scan-only run) write the scan-only report with no
#       freed numbers
#   (7) write <RunDir>\summary.md with the 8 sections above, including a tolerance
#       line: a discrepancy vs the per-category sum within +-500 MB is acceptable,
#       otherwise it is reported as a NOTE, NOT a failure
#   (8) exit 0
#
# READ-ONLY verification: this script NEVER deletes anything and NEVER re-runs
# cleanup commands. It only reads run-directory files, measures live free space,
# and writes one markdown summary. It exits non-zero only when the run directory
# itself is unusable (missing preflight / invalid drive); out-of-tolerance totals
# are always noted, never fatal. No paths are hardcoded: every location is derived
# from $Drive / $RunDir / $env:USERPROFILE.

param(
    [Parameter(Mandatory)][string]$Drive,                       # e.g. 'D:'
    [string]$RunDir,                                            # run dir from scan/clean, e.g. <OutDir>\D-<timestamp>; newest under $OutDir when omitted
    [string]$OutDir = "$env:USERPROFILE\Desktop\.omo\evidence\rubbish-cleaner"
)

# ---- dot-source the safety function library (todo 3 deliverable) ----
. (Join-Path $PSScriptRoot 'lib\rubbish-core.ps1')

$ToleranceBytes = 500000000            # +-500 MB (decimal MB) reconciliation tolerance
$utf8NoBom      = New-Object System.Text.UTF8Encoding($false)

function Format-Bytes([long]$Bytes) { return ('{0:N0}' -f $Bytes) }
function Format-GiB([long]$Bytes)    { return ('{0:N2} GiB' -f ($Bytes / 1GB)) }

# =====================================================================
# (0) validate the drive and resolve the run directory
# =====================================================================
$driveLetter = $Drive.TrimEnd(':').ToUpperInvariant()
if ($Drive -notmatch '^[A-Za-z]:$') {
    Write-Error "Invalid drive '$Drive'. Expected a drive letter like 'D:'."
    exit 1
}
$volume = Get-Volume -DriveLetter $driveLetter
if ($null -eq $volume) {
    Write-Error "Volume for drive letter '$driveLetter' not found."
    exit 1
}

if (-not $RunDir) {
    $runs = Get-ChildItem -LiteralPath $OutDir -Directory -Filter "$driveLetter-*" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending
    if ($runs) { $RunDir = $runs[0].FullName }
}
if (-not (Test-Path -LiteralPath $RunDir -PathType Container)) {
    Write-Error "RunDir not found: $RunDir"
    exit 1
}

# =====================================================================
# (2) read <RunDir>\preflight.txt baseline free space (key=value lines)
# =====================================================================
$preflightPath = Join-Path $RunDir 'preflight.txt'
if (-not (Test-Path -LiteralPath $preflightPath)) {
    Write-Error "preflight.txt not found in RunDir: $preflightPath"
    exit 1
}
$kv = @{}
foreach ($line in [System.IO.File]::ReadAllLines($preflightPath)) {
    if ($line -match '^([A-Za-z_]+)=(.*)$') { $kv[$matches[1]] = $matches[2] }
}
if (-not $kv.ContainsKey('BASELINE_FREE_BYTES')) {
    Write-Error "preflight.txt is missing the BASELINE_FREE_BYTES key."
    exit 1
}
$baselineFree = [long]::Parse($kv['BASELINE_FREE_BYTES'])
$totalBytes   = if ($kv.ContainsKey('TOTAL_BYTES')) { [long]::Parse($kv['TOTAL_BYTES']) } else { 0 }
$processes    = if ($kv.ContainsKey('PROCESSES'))    { $kv['PROCESSES'] }                else { '' }

# =====================================================================
# (3) re-read live free space as final
# =====================================================================
$finalFree = [long]$volume.SizeRemaining
$finalSize = [long]$volume.Size

# =====================================================================
# (5) read cleanup-errors.csv disposition counts (if present)
# =====================================================================
$cleanupPath = Join-Path $RunDir 'cleanup-errors.csv'
$scanOnly = -not (Test-Path -LiteralPath $cleanupPath)
$cleanupRows = New-Object System.Collections.Generic.List[object]
$dispCounts  = @{}
if (-not $scanOnly) {
    $lines = [System.IO.File]::ReadAllLines($cleanupPath)
    for ($i = 1; $i -lt $lines.Count; $i++) {
        $p = $lines[$i] -split '\|'
        if ($p.Count -lt 6) { continue }
        $row = [pscustomobject]@{
            Timestamp    = $p[0]
            Phase        = $p[1]
            Action       = $p[2]
            Path         = $p[3]
            ErrorMessage = (($p[4..($p.Count - 2)]) -join '|')
            Disposition  = $p[$p.Count - 1]
        }
        $cleanupRows.Add($row) | Out-Null
        $d = $row.Disposition
        if (-not $dispCounts.ContainsKey($d)) { $dispCounts[$d] = 0 }
        $dispCounts[$d]++
    }
}

# disposition-by-path join map (PowerShell hashtable keys are case-insensitive)
$dispByPath = @{}
foreach ($row in $cleanupRows) { $dispByPath[$row.Path] = $row.Disposition }

# =====================================================================
# (6) per-category freed estimate: candidates.csv rows whose cleanup
#     disposition (joined by Path) was OK/QUARANTINED
# =====================================================================
$candPath = Join-Path $RunDir 'candidates.csv'
$hasCandidates = Test-Path -LiteralPath $candPath
$catStats = @{}      # Category -> @{ Rows; CandidateBytes; FreedBytes }
$candRows = 0
$candBytesTotal = 0L
if ($hasCandidates) {
    $lines = [System.IO.File]::ReadAllLines($candPath)
    for ($i = 1; $i -lt $lines.Count; $i++) {
        $p = $lines[$i] -split '\|'
        if ($p.Count -lt 6) { continue }
        $category = $p[0]
        $path     = $p[2]
        $size     = 0L
        [void][long]::TryParse($p[3], [ref]$size)
        if (-not $catStats.ContainsKey($category)) {
            $catStats[$category] = @{ Rows = 0; CandidateBytes = 0L; FreedBytes = 0L }
        }
        $catStats[$category].Rows++
        $catStats[$category].CandidateBytes += $size
        $candRows++
        $candBytesTotal += $size
        $disp = $dispByPath[$path]
        if ('OK', 'QUARANTINED' -contains $disp) {
            $catStats[$category].FreedBytes += $size
        }
    }
}
$estFreedTotal = 0L
foreach ($cat in $catStats.Keys) { $estFreedTotal += $catStats[$cat].FreedBytes }

# =====================================================================
# (7) verification assertions (live Test-Path checks, PASS/FAIL table)
# =====================================================================
# Quarantine location derived from the drive letter, mirroring clean-drive.ps1's
# default QuarantineDir ("$env:USERPROFILE\Desktop\.omo\quarantine\<letter>").
$quarantineDir = Join-Path (Join-Path $env:USERPROFILE 'Desktop\.omo\quarantine') $driveLetter

$assertions = New-Object System.Collections.Generic.List[object]
$qRows = @($cleanupRows | Where-Object { $_.Disposition -eq 'QUARANTINED' })
for ($j = 0; $j -lt $qRows.Count; $j++) {
    $q = $qRows[$j]
    $n = $j + 1
    $origGone   = -not (Test-Path -LiteralPath $q.Path)
    $leaf       = [System.IO.Path]::GetFileName($q.Path.TrimEnd('\', '/'))
    $copyPath   = Join-Path $quarantineDir $leaf
    $copyPresent = Test-Path -LiteralPath $copyPath
    $assertions.Add([pscustomobject]@{
        Id = "a$n"; Description = "Quarantine original absent: $($q.Path)"
        Result = $(if ($origGone) { 'PASS' } else { 'FAIL' })
        Detail = $(if ($origGone) { 'original not found on disk' } else { 'original STILL PRESENT' })
    }) | Out-Null
    $assertions.Add([pscustomobject]@{
        Id = "b$n"; Description = "Quarantine copy present: $copyPath"
        Result = $(if ($copyPresent) { 'PASS' } else { 'FAIL' })
        Detail = $(if ($copyPresent) { 'copy found in quarantine dir' } else { 'copy NOT found in quarantine dir' })
    }) | Out-Null
}
$neRows = @($cleanupRows | Where-Object { $_.Disposition -eq 'SKIP_NOT_EMPTY' })
for ($j = 0; $j -lt $neRows.Count; $j++) {
    $r = $neRows[$j]
    $n = $j + 1
    $survived = Test-Path -LiteralPath $r.Path -PathType Container
    $assertions.Add([pscustomobject]@{
        Id = "c$n"; Description = "Non-empty dir survived (SKIP_NOT_EMPTY): $($r.Path)"
        Result = $(if ($survived) { 'PASS' } else { 'FAIL' })
        Detail = $(if ($survived) { 'dir still exists' } else { 'dir MISSING' })
    }) | Out-Null
}
$lkRows = @($cleanupRows | Where-Object { $_.Disposition -eq 'SKIP_LOCKED' })
for ($j = 0; $j -lt $lkRows.Count; $j++) {
    $r = $lkRows[$j]
    $n = $j + 1
    $survived = Test-Path -LiteralPath $r.Path
    $assertions.Add([pscustomobject]@{
        Id = "d$n"; Description = "Locked item survived (SKIP_LOCKED): $($r.Path)"
        Result = $(if ($survived) { 'PASS' } else { 'FAIL' })
        Detail = $(if ($survived) { 'item still exists' } else { 'item MISSING (removed despite the skip?)' })
    }) | Out-Null
}
$okRows = @($cleanupRows | Where-Object { $_.Disposition -eq 'OK' })
for ($j = 0; $j -lt $okRows.Count; $j++) {
    $r = $okRows[$j]
    $n = $j + 1
    $gone = -not (Test-Path -LiteralPath $r.Path)
    $assertions.Add([pscustomobject]@{
        Id = "e$n"; Description = "Deleted item absent (OK): $($r.Path)"
        Result = $(if ($gone) { 'PASS' } else { 'FAIL' })
        Detail = $(if ($gone) { 'item gone as expected' } else { 'item STILL PRESENT' })
    }) | Out-Null
}

$passCount    = @($assertions | Where-Object { $_.Result -eq 'PASS' }).Count
$failCount    = @($assertions | Where-Object { $_.Result -eq 'FAIL' }).Count
$totalAssert  = $assertions.Count

# =====================================================================
# build <RunDir>\summary.md (exactly the 8 required sections)
# =====================================================================
$L = New-Object System.Collections.Generic.List[string]
function Add-SummaryLine([string]$Text) { $script:L.Add($Text) | Out-Null }

Add-SummaryLine ("# {0}-Drive Cleanup - Post-Run Summary" -f $driveLetter)
Add-SummaryLine ''
Add-SummaryLine ("Generated: {0} - Mode: READ-ONLY verification (no deletions, no re-runs)" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
Add-SummaryLine ("Run directory: ``{0}``" -f $RunDir)
Add-SummaryLine ''

# ---- section 1 ----
Add-SummaryLine '## 1. Baseline Free Space'
Add-SummaryLine ''
Add-SummaryLine ("Source: ``{0}``" -f $preflightPath)
Add-SummaryLine ("- BASELINE_FREE_BYTES = **{0} bytes** ({1})" -f (Format-Bytes $baselineFree), (Format-GiB $baselineFree))
Add-SummaryLine ("- TOTAL_BYTES = {0} bytes" -f (Format-Bytes $totalBytes))
if ($processes) { Add-SummaryLine ("- PROCESSES = {0}" -f $processes) }
Add-SummaryLine ''

# ---- section 2 ----
Add-SummaryLine '## 2. Final Free Space'
Add-SummaryLine ''
Add-SummaryLine ("Measured live: ``(Get-Volume -DriveLetter {0}).SizeRemaining``" -f $driveLetter)
Add-SummaryLine ("- **{0} bytes** ({1})" -f (Format-Bytes $finalFree), (Format-GiB $finalFree))
Add-SummaryLine ("- Volume Size = {0} bytes" -f (Format-Bytes $finalSize))
Add-SummaryLine ''

# ---- section 3 ----
Add-SummaryLine '## 3. Total Freed'
Add-SummaryLine ''
if ($scanOnly) {
    Add-SummaryLine '**Scan-only run** - ``cleanup-errors.csv`` was not found in the run directory, so no cleanup was executed and no freed figure is computed (the per-category freed estimate requires the cleanup CSV).'
    Add-SummaryLine ''
    Add-SummaryLine ("- Final - Baseline = {0} - {1} = **n/a (ambient delta only; not cleanup freed)**" -f (Format-Bytes $finalFree), (Format-Bytes $baselineFree))
} else {
    $totalFreed = $finalFree - $baselineFree
    $tfStr = if ($totalFreed -ge 0) { '+{0}' -f (Format-Bytes $totalFreed) } else { Format-Bytes $totalFreed }
    Add-SummaryLine ("- Final - Baseline = {0} - {1} = **{2} bytes ({3})**" -f (Format-Bytes $finalFree), (Format-Bytes $baselineFree), $tfStr, (Format-GiB $totalFreed))
    Add-SummaryLine ''
    Add-SummaryLine '**Reconciliation vs per-category freed estimate:**'
    $variance = $totalFreed - $estFreedTotal
    $varStr = if ($variance -ge 0) { '+{0}' -f (Format-Bytes $variance) } else { Format-Bytes $variance }
    Add-SummaryLine ("- Sum of per-category freed estimates = {0} bytes ({1})" -f (Format-Bytes $estFreedTotal), (Format-GiB $estFreedTotal))
    Add-SummaryLine ("- Variance = {0} - {1} = {2} bytes" -f (Format-Bytes $totalFreed), (Format-Bytes $estFreedTotal), $varStr)
    if ([math]::Abs($variance) -le $ToleranceBytes) {
        Add-SummaryLine '- Tolerance: within **+-500 MB (500,000,000 bytes)** - discrepancy within tolerance, OK'
    } else {
        Add-SummaryLine ("- Tolerance: within **+-500 MB (500,000,000 bytes)** - **NOTE: discrepancy of {0} bytes exceeds the +-500 MB tolerance. This is a NOTE, not a failure (normal concurrent writes / snapshot timing).**" -f (Format-Bytes $variance))
    }
}
Add-SummaryLine ''

# ---- section 4 ----
Add-SummaryLine '## 4. Per-Category Freed'
Add-SummaryLine ''
if ($scanOnly) {
    Add-SummaryLine '**Scan-only run** - no cleanup was executed; the per-category freed estimate requires ``cleanup-errors.csv``. No freed numbers are reported.'
} elseif (-not $hasCandidates) {
    Add-SummaryLine 'No ``candidates.csv`` found in the run directory - nothing to estimate.'
} else {
    Add-SummaryLine 'Freed estimate = sum of ``SizeBytes`` from ``candidates.csv`` rows whose cleanup disposition (joined by Path) was ``OK`` or ``QUARANTINED``. Rows skipped during cleanup (SKIP_*) are excluded.'
    Add-SummaryLine ''
    Add-SummaryLine '| Category | Rows | Candidate Bytes | Freed Est. (bytes) | Freed GiB |'
    Add-SummaryLine '|----------|------|-----------------|--------------------|-----------|'
    foreach ($cat in ($catStats.Keys | Sort-Object)) {
        $s = $catStats[$cat]
        Add-SummaryLine ("| {0} | {1} | {2} | {3} | {4} |" -f $cat, $s.Rows, (Format-Bytes $s.CandidateBytes), (Format-Bytes $s.FreedBytes), (Format-GiB $s.FreedBytes))
    }
    Add-SummaryLine ("| **Total** | **{0}** | **{1}** | **{2}** | **{3}** |" -f $candRows, (Format-Bytes $candBytesTotal), (Format-Bytes $estFreedTotal), (Format-GiB $estFreedTotal))
}
Add-SummaryLine ''

# ---- section 5 ----
Add-SummaryLine '## 5. Skipped Items Table'
Add-SummaryLine ''
if ($scanOnly) {
    Add-SummaryLine '**Scan-only run** - no ``cleanup-errors.csv``; no cleanup dispositions were recorded.'
} elseif ($cleanupRows.Count -eq 0) {
    Add-SummaryLine '``cleanup-errors.csv`` contains no data rows.'
} else {
    Add-SummaryLine ("Total rows: **{0}**" -f $cleanupRows.Count)
    Add-SummaryLine ''
    Add-SummaryLine '| Disposition | Count |'
    Add-SummaryLine '|-------------|-------|'
    foreach ($d in ($dispCounts.Keys | Sort-Object)) {
        Add-SummaryLine ("| {0} | {1} |" -f $d, $dispCounts[$d])
    }
}
Add-SummaryLine ''

# ---- section 6 ----
Add-SummaryLine '## 6. Quarantine Note'
Add-SummaryLine ''
if ($qRows.Count -eq 0) {
    Add-SummaryLine 'No quarantined files in this run (no ``QUARANTINED`` disposition rows).'
} else {
    Add-SummaryLine ("Quarantine directory: ``{0}``" -f $quarantineDir)
    Add-SummaryLine ''
    Add-SummaryLine '| File | Original path | Quarantine path |'
    Add-SummaryLine '|------|---------------|-----------------|'
    foreach ($q in $qRows) {
        $leaf = [System.IO.Path]::GetFileName($q.Path.TrimEnd('\', '/'))
        $copyPath = Join-Path $quarantineDir $leaf
        Add-SummaryLine ("| {0} | {1} | {2} |" -f $leaf, $q.Path, $copyPath)
    }
    Add-SummaryLine ''
    Add-SummaryLine 'Quarantined items were MOVED (not deleted) and remain recoverable by moving them back.'
}
Add-SummaryLine ''

# ---- section 7 ----
Add-SummaryLine '## 7. Verification Assertions'
Add-SummaryLine ''
if ($assertions.Count -eq 0) {
    if ($scanOnly) {
        Add-SummaryLine '**Scan-only run** - no cleanup rows to verify live.'
    } else {
        Add-SummaryLine 'No live assertions applicable (no QUARANTINED / SKIP_NOT_EMPTY / SKIP_LOCKED / OK rows).'
    }
} else {
    Add-SummaryLine 'Live ``Test-Path`` checks (recomputed from disk, not trusted from the CSV).'
    Add-SummaryLine ''
    Add-SummaryLine '| # | Assertion | Result | Detail |'
    Add-SummaryLine '|---|-----------|--------|--------|'
    foreach ($a in $assertions) {
        Add-SummaryLine ("| {0} | {1} | **{2}** | {3} |" -f $a.Id, $a.Description, $a.Result, $a.Detail)
    }
    Add-SummaryLine ''
    Add-SummaryLine ("Result: **{0}/{1} PASS**" -f $passCount, $totalAssert)
}
Add-SummaryLine ''

# ---- section 8 ----
Add-SummaryLine '## 8. Recommendations'
Add-SummaryLine ''
$recs = New-Object System.Collections.Generic.List[string]
if ($scanOnly) {
    $recs.Add('This run was **scan-only**: no cleanup was executed. Review ``candidates.csv``, present the categorized list to the user, and run ``clean-drive.ps1`` after approval.') | Out-Null
} else {
    $lk = 0; if ($dispCounts.ContainsKey('SKIP_LOCKED')) { $lk = $dispCounts['SKIP_LOCKED'] }
    if ($lk -gt 0) {
        $recs.Add(("Close the application(s) locking {0} item(s) (SKIP_LOCKED), then re-run clean-drive.ps1 to reclaim them." -f $lk)) | Out-Null
    }
    $ne = 0; if ($dispCounts.ContainsKey('SKIP_NOT_EMPTY')) { $ne = $dispCounts['SKIP_NOT_EMPTY'] }
    if ($ne -gt 0) {
        $recs.Add(("{0} non-empty director(ies) (SKIP_NOT_EMPTY) were left intact on purpose; review them manually." -f $ne)) | Out-Null
    }
    if ($dispCounts.ContainsKey('SKIP_TOO_RECENT')) {
        $recs.Add('Some items were skipped as too recent (7-day rule); they can be retried on a later run.') | Out-Null
    }
    if ($qRows.Count -gt 0) {
        $recs.Add(("{0} file(s) are in quarantine at ``{1}`` - review and either restore them or remove them manually once confirmed unwanted." -f $qRows.Count, $quarantineDir)) | Out-Null
    }
    if ($failCount -gt 0) {
        $recs.Add(("{0} live assertion(s) FAILED - investigate before further cleanup." -f $failCount)) | Out-Null
    }
    $recs.Add('Caches and temp files will refill during normal use; schedule periodic scan + clean runs.') | Out-Null
}
if ($recs.Count -eq 0) { $recs.Add('No outstanding recommendations.') | Out-Null }
foreach ($r in $recs) { Add-SummaryLine ("- {0}" -f $r) }
Add-SummaryLine ''

# =====================================================================
# (7/8) write summary.md and exit 0
# =====================================================================
$summaryPath = Join-Path $RunDir 'summary.md'
[System.IO.File]::WriteAllLines($summaryPath, $L.ToArray(), $utf8NoBom)

Write-Output "verify-report: summary written to $summaryPath"
if ($scanOnly) { Write-Output 'verify-report: scan-only run (no cleanup-errors.csv found) - no freed numbers reported' }
exit 0
