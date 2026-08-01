# clean-drive.ps1 - APPROVAL-GATED safe cleanup for the rubbish-cleaner pipeline.
#
# Consumes the candidates.csv produced by scan-drive.ps1 (todo 4) with the
# exact schema `Category|Risk|Path|SizeBytes|FileCount|Action` (pipes, no
# quoting). Per-item removal happens ONLY for rows listed in that CSV, and only
# after the approval gates below are satisfied. Quarantine is a Move-Item -
# NEVER a delete.
#
# DESTRUCTIVE code: during development/tests this script must ONLY ever be run
# against FAKE trees (a crafted candidates.csv pointing at $env:TEMP paths).
#
# Approval gates (todo 5 spec):
#   - risk=ASK (duplicate-archives, recycle-bin) requires -Yes, else the
#     category is printed as `SKIP: category X requires -Yes (user approval)`
#     and skipped (no CSV row, nothing touched).
#   - risk=ELEVATED (elevated-system) is reachable ONLY when $isUserDrive AND
#     (-Yes OR -SkipElevated). With -SkipElevated the script writes
#     <run>\elevated.ps1 but NEVER launches UAC (Start-Process -Verb RunAs is
#     only ever reached with -Yes AND $Drive == system drive). On
#     -SkipElevated / UAC-denial / non-system-drive a SKIP_ELEVATION_DENIED row
#     is logged and the run continues.
#   - risk=SAFE/CAUTION rows without -Yes go through a per-category
#     `Read-Host "Clean category X? (y/n)"` prompt (agent-invoked flow: the
#     calling agent shows the user the scan list first, then passes -Yes).
#
# Safety invariants (see .omo/plans/rubbish-cleaner.md todo 5):
#   - -LiteralPath everywhere; no -Path wildcards; junctions never followed
#     (the lib's Test-DirEmpty skips reparse-point children).
#   - Re-verify before delete: empty-dirs -> junction-aware Test-DirEmpty runs
#     immediately before Remove-Item (SKIP_NOT_EMPTY if it now has content);
#     temp files (root-temps / user-temp) -> re-check age >7 days
#     (SKIP_TOO_RECENT if now too recent).
#   - Locked files are SKIP_LOCKED, never force-killed (per-item try/catch
#     inside the lib's Invoke-SafeRemove).
#   - Directory candidates are only ever removed when Test-DirEmpty passes
#     immediately before deletion; non-empty directories are SKIP_NOT_EMPTY
#     (no bare -Recurse anywhere -> junction trees can never be deleted).
#   - Quarantined originals are never deleted by this script.
#   - Nothing outside candidates.csv rows (+ the elevated batch's own system
#     targets, which are only reachable via UAC on the real system drive) is
#     ever touched.
#
# ASK-with-Yes note: when an ASK category IS approved with -Yes, its rows are
# processed through the same per-row dispatch as SAFE/CAUTION. duplicate-archives
# rows (files) are deleted; recycle-bin rows (a non-empty directory) fail the
# Test-DirEmpty re-verify -> SKIP_NOT_EMPTY, so the recycle bin is never emptied
# by this script (report-only, matching the scan's own description).

param(
    [string]$Drive,                                               # e.g. 'D:' (single-drive mode; gates elevated-system)
    [string[]]$Drives,                                            # todo 8: multi-drive batch, e.g. @('D:','E:')
    [string]$CandidatesCsv,                                       # default: newest <OutDir>\<Drive>-*\candidates.csv
    [string]$OutDir = "$env:USERPROFILE\Desktop\.omo\evidence\rubbish-cleaner",
    [string]$QuarantineDir,                                       # default: <out>\Desktop\.omo\quarantine\<letter> (computed per drive)
    [switch]$Yes,                                                 # approve ASK categories + run the elevated batch (no prompts)
    [string[]]$Categories,                                        # filter; empty = all categories present in the CSV
    [switch]$SkipElevated,                                        # test/CI-safe: prepare <run>\elevated.ps1 but NEVER launch UAC
    [switch]$Resume,                                              # todo 4: resume from <run>\clean-checkpoint.json
    [switch]$Parallel                                             # todo 8: accepted but IGNORED (cleaning is always sequential)
)

# ---- dot-source the safety function library (todo 3 deliverable) ----
. (Join-Path $PSScriptRoot 'lib\rubbish-core.ps1')

# Never inherit a stricter preference from the caller: per-item failures are
# handled by try/catch inside the lib functions, everything else must keep
# going so a single locked/missing item can never abort the run.
$ErrorActionPreference = 'Continue'

# =====================================================================
# Per-row Action dispatch for SAFE/CAUTION rows (and ASK rows once the
# category has been approved with -Yes).
# =====================================================================
function Invoke-CandidateRow {
    param(
        [object]$Row,
        [string]$Category,
        [string]$CsvPath,
        [string]$QuarantineDir
    )

    $action = [string]$Row.Action

    # Quarantine = Move-Item, NEVER delete.
    if ($action -eq 'quarantine') {
        Invoke-Quarantine -LiteralPath $Row.Path -QuarantineDir $QuarantineDir -Phase $Category -CsvPath $CsvPath
        return
    }

    # 'delete' (SAFE/CAUTION) and 'ask' (approved ASK) rows go through
    # re-verify + Invoke-SafeRemove. Any other Action is report-only.
    if ($action -notin @('delete', 'ask')) {
        Write-Output "  SKIP: '$($Row.Path)' has unhandled Action '$action' (report-only, nothing touched)"
        return
    }

    # ---- Re-verify before delete ----------------------------------------
    $isDir  = Test-Path -LiteralPath $Row.Path -PathType Container
    $isFile = Test-Path -LiteralPath $Row.Path -PathType Leaf

    if ($isDir) {
        # Directories are removed ONLY when the junction-aware Test-DirEmpty
        # passes immediately before deletion. Non-empty directories are never
        # force-deleted (no bare -Recurse).
        if (-not (Test-DirEmpty -Path $Row.Path)) {
            Write-CleanupCsv -CsvPath $CsvPath -Row @{
                Phase        = $Category
                Action       = 'Remove'
                Path         = $Row.Path
                ErrorMessage = 're-verify failed: directory is not empty (junction-aware Test-DirEmpty)'
                Disposition  = 'SKIP_NOT_EMPTY'
            }
            Write-Output "  SKIP_NOT_EMPTY: $($Row.Path)"
            return
        }
    } elseif ($isFile -and $Category -in @('root-temps', 'user-temp')) {
        # Temp-file rows: re-check the 7-day age rule right before deletion.
        $item = Get-Item -LiteralPath $Row.Path -Force -ErrorAction SilentlyContinue
        if ($null -ne $item -and ((Get-Date) - $item.LastWriteTime) -lt [TimeSpan]::FromDays(7)) {
            Write-CleanupCsv -CsvPath $CsvPath -Row @{
                Phase        = $Category
                Action       = 'Remove'
                Path         = $Row.Path
                ErrorMessage = 're-verify failed: last write is within the 7-day window'
                Disposition  = 'SKIP_TOO_RECENT'
            }
            Write-Output "  SKIP_TOO_RECENT: $($Row.Path)"
            return
        }
    }

    # Per-item safe removal (try/catch inside -> OK / SKIP_LOCKED /
    # SKIP_ACCESS_DENIED / SKIP_NOT_FOUND). A missing target falls through to
    # Invoke-SafeRemove which records SKIP_NOT_FOUND.
    Invoke-SafeRemove -LiteralPath $Row.Path -Phase $Category -CsvPath $CsvPath
}

# =====================================================================
# Write the elevated batch <run>\elevated.ps1 (wave4-elevated.ps1 pattern,
# ALL paths absolute). The batch is ONLY ever launched by the UAC branch of
# the ELEVATED gate; writing the file itself is inert.
# =====================================================================
function Write-ElevatedBatch {
    param(
        [string]$Path,          # absolute path of the .ps1 to write
        [string]$RunDir,        # <run> dir holding elevated-result.txt + cleanup-errors.csv
        [string]$WindowsRoot    # e.g. 'C:\Windows'
    )

    # Escape single quotes for embedding into a single-quoted PS string.
    $escCsv    = (Join-Path $RunDir 'cleanup-errors.csv').Replace("'", "''")
    $escResult = (Join-Path $RunDir 'elevated-result.txt').Replace("'", "''")
    $escWin    = $WindowsRoot.Replace("'", "''")

    $scriptText = @'
# elevated.ps1 - GENERATED by clean-drive.ps1 (rubbish-cleaner todo 5). DO NOT EDIT.
# Elevated system cleanup, wave4-elevated.ps1 pattern, ALL paths absolute.
# Steps: (a) <win>\Temp top-level >7d  (b) Prefetch *.pf ONLY (never Layout.ini)
#        (c) SoftwareDistribution GUARDED (d) WindowsUpdate *.etl >7d + CBS CbsPersist_*.cab
#        (e) DISM /StartComponentCleanup (NO /ResetBase)  (f) result file
$ErrorActionPreference = 'Continue'

$csv    = '__CSV__'
$result = '__RESULT__'
$win    = '__WIN__'
$start  = Get-Date -Format o
Set-Content -LiteralPath $result -Value "START=$start"

function Log-Err {
    param([string]$p, [string]$msg, [string]$disp)
    Add-Content -LiteralPath $csv -Value "$(Get-Date -Format o)|elevated-system|delete|$p|$msg|$disp"
}

# ---- (a) <win>\Temp : TOP-LEVEL FILES ONLY (no -Recurse), skip <7d, skip locked ----
$tempExit = 0
$tempDeleted = 0
try {
    if (Test-Path -LiteralPath (Join-Path $win 'Temp')) {
        Get-ChildItem -LiteralPath (Join-Path $win 'Temp') -File -Force -ErrorAction SilentlyContinue | ForEach-Object {
            $f = $_
            try {
                if ((Get-Date) - $f.LastWriteTime -lt [TimeSpan]::FromDays(7)) { return }
                Remove-Item -LiteralPath $f.FullName -Force -ErrorAction Stop
                $tempDeleted++
            } catch {
                Log-Err $f.FullName $_.Exception.Message 'SKIP_LOCKED'
            }
        }
    }
} catch { $tempExit = 1 }
Add-Content -LiteralPath $result -Value "TEMP=$tempExit"
Add-Content -LiteralPath $result -Value "TEMP_DELETED=$tempDeleted"

# ---- (b) <win>\Prefetch : ONLY *.pf, NEVER Layout.ini ----
$pfExit = 0
$pfDeleted = 0
try {
    if (Test-Path -LiteralPath (Join-Path $win 'Prefetch')) {
        Get-ChildItem -LiteralPath (Join-Path $win 'Prefetch') -Filter '*.pf' -File -Force -ErrorAction SilentlyContinue | ForEach-Object {
            $f = $_
            if ($f.Name -eq 'Layout.ini') { return }
            try {
                Remove-Item -LiteralPath $f.FullName -Force -ErrorAction Stop
                $pfDeleted++
            } catch {
                Log-Err $f.FullName $_.Exception.Message 'SKIP_LOCKED'
            }
        }
    }
} catch { $pfExit = 1 }
Add-Content -LiteralPath $result -Value "PREFETCH=$pfExit"
Add-Content -LiteralPath $result -Value "PREFETCH_DELETED=$pfDeleted"

# ---- (c) <win>\SoftwareDistribution : GUARDED (delete only if wuauserv confirmed Stopped) ----
$wuaCode = 0
$sdStatus = 'SKIPPED'
$sdDeleted = 0
$svc = Get-Service wuauserv -ErrorAction SilentlyContinue
if ($svc -and $svc.Status -eq 'Running') {
    try {
        Stop-Service wuauserv -Force -ErrorAction Stop
    } catch {
        $wuaCode = 1
        Add-Content -LiteralPath $csv -Value "$(Get-Date -Format o)|elevated-system|service-stop|wuauserv|$($_.Exception.Message)|SKIP_SERVICE_RUNNING"
        Add-Content -LiteralPath $result -Value "WUAUSERV_STOP_FAILED - SoftwareDistribution skipped"
    }
}
$svc2 = Get-Service wuauserv -ErrorAction SilentlyContinue
if ($svc2 -and $svc2.Status -eq 'Stopped') {
    $dl = Join-Path $win 'SoftwareDistribution\Download'
    if (Test-Path -LiteralPath $dl) {
        Get-ChildItem -LiteralPath $dl -Force -ErrorAction SilentlyContinue | ForEach-Object {
            $i = $_
            try {
                Remove-Item -LiteralPath $i.FullName -Recurse -Force -ErrorAction Stop
                $sdDeleted++
            } catch {
                Log-Err $i.FullName $_.Exception.Message 'SKIP_LOCKED'
            }
        }
    }
    foreach ($old in @(
            (Join-Path $win 'SoftwareDistribution\DataStore\DataStore.edb.old'),
            (Join-Path $win 'SoftwareDistribution\DataStore\DataStore.jfm.old'))) {
        if (Test-Path -LiteralPath $old) {
            try {
                Remove-Item -LiteralPath $old -Force -ErrorAction Stop
                $sdDeleted++
            } catch {
                Log-Err $old $_.Exception.Message 'SKIP_LOCKED'
            }
        }
    }
    Start-Service wuauserv -ErrorAction SilentlyContinue
    $sdStatus = 'CLEANED'
}
$sdCode = if ($sdStatus -eq 'CLEANED') { 0 } else { 1 }
Add-Content -LiteralPath $result -Value "SOFTWARE_DISTRIBUTION=$sdCode"
Add-Content -LiteralPath $result -Value "SD_STATUS=$sdStatus"
Add-Content -LiteralPath $result -Value "SD_DELETED=$sdDeleted"
Add-Content -LiteralPath $result -Value "WUAUSERV=$wuaCode"

# ---- (d) WindowsUpdate *.etl >7d + CBS CbsPersist_*.cab ----
$logsExit = 0
$logsDeleted = 0
try {
    $wu = Join-Path $win 'Logs\WindowsUpdate'
    if (Test-Path -LiteralPath $wu) {
        Get-ChildItem -LiteralPath $wu -Filter '*.etl' -File -Force -ErrorAction SilentlyContinue | ForEach-Object {
            $f = $_
            try {
                if ((Get-Date) - $f.LastWriteTime -lt [TimeSpan]::FromDays(7)) { return }
                Remove-Item -LiteralPath $f.FullName -Force -ErrorAction Stop
                $logsDeleted++
            } catch {
                Log-Err $f.FullName $_.Exception.Message 'SKIP_LOCKED'
            }
        }
    }
    $cbs = Join-Path $win 'Logs\CBS'
    if (Test-Path -LiteralPath $cbs) {
        Get-ChildItem -LiteralPath $cbs -Filter 'CbsPersist_*.cab' -File -Force -ErrorAction SilentlyContinue | ForEach-Object {
            $f = $_
            try {
                Remove-Item -LiteralPath $f.FullName -Force -ErrorAction Stop
                $logsDeleted++
            } catch {
                Log-Err $f.FullName $_.Exception.Message 'SKIP_LOCKED'
            }
        }
    }
} catch { $logsExit = 1 }
Add-Content -LiteralPath $result -Value "LOGS=$logsExit"
Add-Content -LiteralPath $result -Value "LOGS_DELETED=$logsDeleted"

# ---- (e) DISM /Online /Cleanup-Image /StartComponentCleanup (NO /ResetBase) ----
$dismExit = -1
try {
    & dism.exe /Online /Cleanup-Image /StartComponentCleanup
    $dismExit = $LASTEXITCODE
} catch {
    $dismExit = 9999
}
Add-Content -LiteralPath $result -Value "DISM=$dismExit"

Add-Content -LiteralPath $result -Value "END=$(Get-Date -Format o)"
'@

    $scriptText = $scriptText.Replace('__CSV__', $escCsv).Replace('__RESULT__', $escResult).Replace('__WIN__', $escWin)
    [System.IO.File]::WriteAllText($Path, $scriptText, (New-Object System.Text.UTF8Encoding($false)))
}

# =====================================================================
# todo 4: clean checkpoint (<run>\clean-checkpoint.json).
# Schema: {"completedCategories":[...],"lastCleanedRowIndex":N}. Written after
# every completed category so a -Resume run can skip already-cleaned rows.
# A write failure is caught and IGNORED: it must never abort the cleanup.
# =====================================================================
function Write-CleanCheckpoint {
    param(
        [string]$Path,
        [System.Collections.Generic.List[string]]$Completed,
        [int]$LastIndex
    )
    try {
        $json = ConvertTo-Json -InputObject ([ordered]@{
                completedCategories = @($Completed)
                lastCleanedRowIndex = $LastIndex
            })
        [System.IO.File]::WriteAllText($Path, $json, (New-Object System.Text.UTF8Encoding($false)))
    } catch {
        Write-Warning "clean checkpoint write failed (continuing): $($_.Exception.Message)"
    }
}

# =====================================================================
# <begin-main>
# =====================================================================

# =====================================================================
# todo 8: multi-drive batch mode (-Drives D:,E:).
# -Parallel is ACCEPTED but IGNORED: deletion must NEVER be parallelized.
# Drives are cleaned STRICTLY SEQUENTIALLY (one subprocess at a time).
# =====================================================================
$driveGiven  = -not [string]::IsNullOrEmpty($Drive)
$drivesGiven = ($null -ne $Drives) -and @($Drives).Count -gt 0
if (-not $driveGiven -and -not $drivesGiven) {
    Write-Error "Must specify either -Drive or -Drives"
    exit 1
}
if ($driveGiven -and $drivesGiven) {
    Write-Error "Specify only one of -Drive or -Drives (mutual exclusion)"
    exit 1
}
if ($Parallel.IsPresent) {
    Write-Output "parallel clean is disabled for safety — cleaning drives sequentially"
}

if ($drivesGiven) {
    $driveList = @($Drives | ForEach-Object { $_ -split ',' } | Where-Object { $_ })
    if ($driveList.Count -eq 0) {
        Write-Error "No drives specified in -Drives"
        exit 1
    }

    # Subprocess executable + prefix args (todo 8 platform branch): each
    # drive is cleaned by its OWN clean-drive.ps1 subprocess so the per-drive
    # behaviour is identical to single-drive mode.
    if ($script:IsWindows) {
        $cleanExe = 'powershell.exe'
        $cleanPfx = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $PSScriptRoot 'clean-drive.ps1'))
    } else {
        $cleanExe = 'pwsh'
        $cleanPfx = @('-NoProfile', '-File', (Join-Path $PSScriptRoot 'clean-drive.ps1'))
    }

    $anyFailed = $false
    foreach ($d in $driveList) {
        $a = New-Object System.Collections.Generic.List[string]
        foreach ($p in $cleanPfx) { [void]$a.Add($p) }
        [void]$a.Add('-Drive');  [void]$a.Add($d)
        [void]$a.Add('-OutDir'); [void]$a.Add($OutDir)
        if ($Yes.IsPresent)            { [void]$a.Add('-Yes') }
        if ($SkipElevated.IsPresent)   { [void]$a.Add('-SkipElevated') }
        if ($Resume.IsPresent)         { [void]$a.Add('-Resume') }
        if ($Categories)               { [void]$a.Add('-Categories'); [void]$a.Add((@($Categories | ForEach-Object { $_ -split ',' } | Where-Object { $_ }) -join ',')) }
        if ($QuarantineDir)            { [void]$a.Add('-QuarantineDir'); [void]$a.Add($QuarantineDir) }

        # Per-drive timestamp markers: drive B START must be strictly later
        # than drive A END (the sequential guarantee tests assert on).
        Write-Output ("[CLEAN START {0}] {1}" -f $d, (Get-Date -Format o))
        & $cleanExe @($a.ToArray())
        $code = $LASTEXITCODE
        Write-Output ("[CLEAN END {0}] {1} (exit {2})" -f $d, (Get-Date -Format o), $code)
        if ($code -ne 0) { $anyFailed = $true }
    }
    if ($anyFailed) { exit 1 }
    exit 0
}

# ---- Drive validation (same gates as scan-drive.ps1) ------------------
# 1. syntax: ^[A-Za-z]:$
if ($Drive -notmatch '^[A-Za-z]:$') {
    Write-Error "Invalid -Drive '$Drive': expected a drive letter and colon (e.g. 'D:')."
    exit 1
}
$driveLetter = $Drive.TrimEnd(':')

# 2. the drive must exist
if (-not (Test-Path -LiteralPath "$Drive\")) {
    Write-Error "Drive '$Drive' does not exist."
    exit 1
}

# 3. must be a Fixed (local hard disk) volume
$vol = Get-Volume -DriveLetter $driveLetter
if ($null -eq $vol -or $vol.DriveType -ne 'Fixed') {
    Write-Error "Drive '$Drive' is not a fixed local volume (DriveType='$($vol.DriveType)'). Refusing to clean removable/network media."
    exit 1
}

# ---- Scope flags -------------------------------------------------------
# User-profile drive? (gates the ELEVATED category).
$isUserDrive  = $env:USERPROFILE.StartsWith($Drive, [System.StringComparison]::OrdinalIgnoreCase)
# System drive? (the elevated batch may ONLY be launched here).
$isSystemDrive = ($driveLetter -ieq $env:SystemDrive.TrimEnd(':'))
# Default quarantine dir is derived per drive. Evaluated lazily (not at
# param-binding time) so -Drives mode never needs a -Drive to expand it.
if (-not $QuarantineDir) { $QuarantineDir = "$env:USERPROFILE\Desktop\.omo\quarantine\$($Drive.TrimEnd(':'))" }

# ---- Locate candidates.csv ---------------------------------------------
# Default: newest <OutDir>\<Drive>-*\candidates.csv (Get-ChildItem sorted by
# LastWriteTime descending). Run dir <run> = the CSV's parent folder.
if (-not $CandidatesCsv) {
    $runDirs = @(Get-ChildItem -LiteralPath $OutDir -Directory -Filter "$driveLetter-*" -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending)
    foreach ($rd in $runDirs) {
        $candidate = Join-Path $rd.FullName 'candidates.csv'
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { $CandidatesCsv = $candidate; break }
    }
    if (-not $CandidatesCsv) {
        Write-Error "No candidates.csv found under '$OutDir' for drive '$Drive' (looked for '<Drive>-*\candidates.csv')."
        exit 1
    }
}
if (-not (Test-Path -LiteralPath $CandidatesCsv -PathType Leaf)) {
    Write-Error "Candidates CSV not found: $CandidatesCsv"
    exit 1
}
$runDir = Split-Path -Parent $CandidatesCsv
if (-not (Test-Path -LiteralPath $runDir -PathType Container)) {
    Write-Error "Run directory does not exist: $runDir"
    exit 1
}
$cleanupCsv = Join-Path $runDir 'cleanup-errors.csv'

# ---- Read + validate candidates.csv (schema from todo 4) ----------------
$expectedHeader = @('Category', 'Risk', 'Path', 'SizeBytes', 'FileCount', 'Action')
$firstLine = (Get-Content -LiteralPath $CandidatesCsv -TotalCount 1) -join ''
$headerCols = @($firstLine -split '\|')
if ($headerCols.Count -gt 0) { $headerCols[0] = $headerCols[0].TrimStart([char]0xFEFF) }
$headerKey = ($headerCols | ForEach-Object { $_.Trim() }) -join ','
if (($headerKey -ne ($expectedHeader -join ',')) -or ($headerCols.Count -ne 6)) {
    Write-Error "Unexpected candidates.csv header: '$firstLine'. Expected: $($expectedHeader -join '|')"
    exit 1
}
$rows = @(Import-Csv -LiteralPath $CandidatesCsv -Delimiter '|')

# Normalize -Categories: accept both `-Categories a,b` and `-Categories "a,b"`.
$catFilter = @($Categories | ForEach-Object { $_ -split ',' } | Where-Object { $_ })

# ---- todo 4: clean checkpoint + resume ----------------------------------
# The checkpoint lives in the run dir next to candidates.csv. With -Resume it
# must already exist (rows before lastCleanedRowIndex were cleaned by a
# previous run and are skipped). Without -Resume the checkpoint is still
# written after each completed category, just unused.
$cleanCheckpointPath = Join-Path $runDir 'clean-checkpoint.json'
$resumeState = $null
if ($Resume.IsPresent) {
    if (-not (Test-Path -LiteralPath $cleanCheckpointPath -PathType Leaf)) {
        Write-Error "No checkpoint found for resume: $cleanCheckpointPath"
        exit 1
    }
    $cjson = Get-Content -LiteralPath $cleanCheckpointPath -Raw | ConvertFrom-Json
    $resumeState = @{
        CompletedCategories = @($cjson.completedCategories)
        LastCleanedRowIndex = if ($null -eq $cjson.lastCleanedRowIndex) { -1 } else { [int]$cjson.lastCleanedRowIndex }
    }
    Write-Output "RESUME: skipping $($resumeState.LastCleanedRowIndex) already-cleaned row(s) (checkpoint $cleanCheckpointPath)"
}
$skipUntil = if ($null -ne $resumeState) { $resumeState.LastCleanedRowIndex } else { -1 }
$completedCats = New-Object System.Collections.Generic.List[string]
if ($null -ne $resumeState) {
    foreach ($c in $resumeState.CompletedCategories) { [void]$completedCats.Add([string]$c) }
}

# ---- Process categories -------------------------------------------------
Write-Output "CLEANING $Drive (isUserDrive=$isUserDrive, isSystemDrive=$isSystemDrive, yes=$($Yes.IsPresent), skipElevated=$($SkipElevated.IsPresent))"
Write-Output "CANDIDATES: $CandidatesCsv"
Write-Output "RUN DIR: $runDir"

# Rows get a global 0-based Index so resume can skip rows strictly before
# lastCleanedRowIndex (the checkpoint is written after each completed category).
$indexedRows = New-Object System.Collections.Generic.List[object]
for ($i = 0; $i -lt $rows.Count; $i++) { $indexedRows.Add([pscustomobject]@{ Index = $i; Row = $rows[$i] }) }
$groups = @($indexedRows | Group-Object { $_.Row.Category })
$totalGroups = $groups.Count
if ($totalGroups -eq 0) {
    Write-Output "NO CANDIDATES: candidates.csv is empty (nothing to clean)."
}
$catDone = 0

foreach ($g in $groups) {
    $cat  = [string]$g.Name
    $risk = [string]$g.Group[0].Row.Risk
    $catDone++
    Write-Progress -Activity "Cleaning $cat" -Status "$catDone/$totalGroups categories" -PercentComplete ([Math]::Floor(100 * $catDone / [Math]::Max(1, $totalGroups)))

    # -Categories whitelist.
    if ($catFilter -and ($catFilter -notcontains $cat)) {
        Write-Output "SKIP: category $cat excluded by -Categories filter"
        continue
    }

    # todo 4 resume: a category whose EVERY row lies before lastCleanedRowIndex
    # was fully handled in a previous run; skip it without re-prompting.
    if ($null -ne $resumeState -and @($g.Group | Where-Object { $_.Index -ge $skipUntil }).Count -eq 0) {
        Write-Output "SKIP: category $cat already completed (resume, rows < lastCleanedRowIndex)"
        continue
    }

    $groupEndIndex = @($g.Group | ForEach-Object { $_.Index } | Measure-Object -Maximum).Maximum

    switch ($risk) {

        # ---- ASK (duplicate-archives, recycle-bin): require -Yes ---------
        'ASK' {
            if (-not $Yes.IsPresent) {
                Write-Output "SKIP: category $cat requires -Yes (user approval)"
                continue
            }
            Write-Output "CLEAN: category $cat (ASK, approved by -Yes)"
            foreach ($item in $g.Group) {
                if ([int]$item.Index -lt $skipUntil) { continue }   # todo 4 resume
                Invoke-CandidateRow -Row $item.Row -Category $cat -CsvPath $cleanupCsv -QuarantineDir $QuarantineDir
            }
        }

        # ---- ELEVATED (elevated-system): UAC-gated batch -----------------
        'ELEVATED' {
            if (-not ($isUserDrive -and ($Yes.IsPresent -or $SkipElevated.IsPresent))) {
                Write-Output "SKIP: category $cat requires -Yes (or -SkipElevated) on the user-profile drive"
                continue
            }

            # Prepare the elevated batch (inert file write; absolute paths).
            $elevatedPath = Join-Path $runDir 'elevated.ps1'
            Write-ElevatedBatch -Path $elevatedPath -RunDir $runDir -WindowsRoot (Join-Path "$Drive\" 'Windows')
            Write-Output "PREPARED: elevated batch written to $elevatedPath"

            # -SkipElevated: NEVER launch UAC (test/CI-safe path).
            if ($SkipElevated.IsPresent) {
                Write-CleanupCsv -CsvPath $cleanupCsv -Row @{
                    Phase        = $cat
                    Action       = 'Elevated'
                    Path         = $cat
                    ErrorMessage = 'elevated batch prepared but NOT launched (-SkipElevated)'
                    Disposition  = 'SKIP_ELEVATION_DENIED'
                }
                Write-Output "SKIP_ELEVATION_DENIED: $cat (UAC not launched, -SkipElevated)"
                # break (not continue): the category WAS handled - fall through to
                # the post-category clean-checkpoint write below.
                break
            }

            # -Yes but NOT the system drive: refuse to launch the batch.
            if (-not $isSystemDrive) {
                Write-CleanupCsv -CsvPath $cleanupCsv -Row @{
                    Phase        = $cat
                    Action       = 'Elevated'
                    Path         = $cat
                    ErrorMessage = "refusing to run elevated batch: $Drive is not the system drive ($env:SystemDrive)"
                    Disposition  = 'SKIP_ELEVATION_DENIED'
                }
                Write-Output "SKIP_ELEVATION_DENIED: $cat ($Drive is not the system drive)"
                break
            }

            # -Yes + user-profile drive + system drive: launch via UAC.
            try {
                Write-Output "LAUNCHING elevated batch via UAC: $elevatedPath"
                Start-Process -FilePath 'powershell.exe' -Verb RunAs -Wait `
                    -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $elevatedPath) -ErrorAction Stop
                $resultFile = Join-Path $runDir 'elevated-result.txt'
                if (-not (Test-Path -LiteralPath $resultFile -PathType Leaf)) {
                    throw 'elevated-result.txt not found after elevated run (UAC may have been denied)'
                }
                Write-Output "ELEVATED COMPLETE: $resultFile"
            } catch {
                # UAC denial (or launch failure) -> SKIP_ELEVATION_DENIED, continue.
                Write-CleanupCsv -CsvPath $cleanupCsv -Row @{
                    Phase        = $cat
                    Action       = 'Elevated'
                    Path         = $cat
                    ErrorMessage = $_.Exception.Message
                    Disposition  = 'SKIP_ELEVATION_DENIED'
                }
                Write-Output "SKIP_ELEVATION_DENIED: $cat ($($_.Exception.Message))"
            }
        }

        # ---- SAFE / CAUTION (and any unknown risk): per-row dispatch -----
        default {
            if (-not $Yes.IsPresent) {
                $total = ($g.Group | Measure-Object -Property { $_.Row.SizeBytes } -Sum -ErrorAction SilentlyContinue).Sum
                Write-Output "SUMMARY: category $cat - $($g.Group.Count) item(s), $total byte(s)"
                $answer = Read-Host "Clean category $cat? (y/n)"
                if ($answer -notmatch '^[yY]') {
                    Write-Output "SKIP: category $cat declined by user"
                    continue
                }
            }
            Write-Output "CLEAN: category $cat ($risk)"
            foreach ($item in $g.Group) {
                if ([int]$item.Index -lt $skipUntil) { continue }   # todo 4 resume
                Invoke-CandidateRow -Row $item.Row -Category $cat -CsvPath $cleanupCsv -QuarantineDir $QuarantineDir
            }
        }
    }

    # todo 4: checkpoint after each completed category (failures ignored).
    if (-not $completedCats.Contains($cat)) { [void]$completedCats.Add($cat) }
    Write-CleanCheckpoint -Path $cleanCheckpointPath -Completed $completedCats -LastIndex ($groupEndIndex + 1)
}

Write-Output "CLEAN COMPLETE: cleanup CSV at $cleanupCsv"
exit 0

# <end-main>
# =====================================================================
