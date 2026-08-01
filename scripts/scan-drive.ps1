# scan-drive.ps1 - READ-ONLY inventory + classification for the rubbish-cleaner pipeline.
#
# Scans a single fixed drive for junk candidates (temp files, logs, duplicate
# archives, empty dirs, recycle bin, suspicious root binaries, app caches) and
# writes a pipe-delimited candidates.csv plus a scan-report.json. NEVER deletes
# or moves anything - this script only classifies. Actual cleanup is done by
# clean-drive.ps1 (todo 5) after explicit user approval.
#
# Safety invariants (see .omo/plans/rubbish-cleaner.md todo 4):
#   - READ-ONLY: no Remove-Item / Move-Item / Clear-RecycleBin anywhere.
#   - Every item access uses -LiteralPath (never -Path; no wildcard-prone calls).
#   - Junctions are NEVER followed: reparse-point children are skipped without
#     descending (PowerShell 5.1 bare -Recurse follows NTFS junctions).
#   - Nothing outside the scanned drive is touched; user-profile categories run
#     only when the user profile lives on the scanned drive ($isUserDrive).
#   - Drive letters and user names are never hardcoded ($Drive, $env:USERPROFILE).
#
# Outputs (in <OutDir>\<DriveLetter>-<yyyyMMdd-HHmmss>\):
#   preflight.txt      exactly 3 key=value lines (BASELINE_FREE_BYTES,
#                      TOTAL_BYTES, PROCESSES) - consumed by verify-report.ps1
#   candidates.csv     header `Category|Risk|Path|SizeBytes|FileCount|Action`
#                      (pipes, no quoting); Action is the FIXED per-risk mapping
#                      SAFE->delete, CAUTION->quarantine, ASK->ask,
#                      ELEVATED->report-only.
#   scan-report.json   per category: { name, risk, candidates[] }

[CmdletBinding(DefaultParameterSetName = 'SingleDrive')]
param(
    [Parameter(ParameterSetName = 'SingleDrive')]
    [Parameter(ParameterSetName = 'MultiDrive')]
    [string]$Drive = $null,                                     # e.g. 'D:' (single-drive mode)
    [Parameter(ParameterSetName = 'MultiDrive')]
    [Parameter(ParameterSetName = 'SingleDrive')]
    [string[]]$Drives = $null,                                  # todo 8: multi-drive batch, e.g. @('D:','E:')
    [string]$OutDir,
    [switch]$IncludeElevated,                                   # enable elevated-system (report-only)
    [string[]]$Categories,                                      # filter; empty = all applicable
    [switch]$Resume,                                            # todo 4: resume from <run>\scan-checkpoint.json
    [switch]$Parallel                                           # todo 8: run per-drive subprocesses concurrently
)

# ---- dot-source the safety function library (todo 3 deliverable) ----
. (Join-Path $PSScriptRoot 'lib\rubbish-core.ps1')
# ---- dot-source the cross-platform detection layer (todo 2 deliverable) ----
. (Join-Path $PSScriptRoot 'lib\platform.ps1')
if (-not $OutDir) { $OutDir = Get-DefaultEvidenceDir }

# =====================================================================
# <begin-classification>
# Classification logic. Kept between these markers so tests (todos 8-9)
# can extract and invoke it directly against a fake tree without running
# the script's main validation/IO block.
# =====================================================================

# Fixed risk -> Action mapping applied to EVERY row (spec: FIXED mapping).
$script:CategoryRiskMap = @{
    'root-temps'         = 'SAFE'
    'root-logs'          = 'SAFE'
    'duplicate-archives' = 'ASK'
    'empty-dirs'         = 'SAFE'
    'recycle-bin'        = 'ASK'
    'root-suspicious'    = 'CAUTION'
    'app-caches'         = 'SAFE'
    'browser-caches'     = 'SAFE'
    'gpu-shader'         = 'SAFE'
    'dev-caches'         = 'SAFE'
    'ide-caches'         = 'SAFE'
    'crash-dumps'        = 'SAFE'
    'thumbnail-cache'    = 'SAFE'
    'user-temp'          = 'SAFE'
    'elevated-system'    = 'ELEVATED'
}
$script:RiskActionMap = @{
    'SAFE'     = 'delete'
    'CAUTION'  = 'quarantine'
    'ASK'      = 'ask'
    'ELEVATED' = 'report-only'
}

# =====================================================================
# todo 4: Write-Progress / scan-checkpoint / -Resume machinery.
# All mutable state flows through ONE hashtable (New-ScanCheckpointState) so
# nested functions can update it from any scope. When checkpointing is disabled
# (tests calling Get-JunkCandidates directly, or no -Resume) the helpers are
# no-ops and the script behaves exactly as before.
# =====================================================================

# Windows / POSIX category execution order + the user-profile subset, used to
# compute the Write-Progress total ("N/M categories").
$script:WindowsCatOrder = @('root-temps','root-logs','duplicate-archives','empty-dirs','recycle-bin','root-suspicious','app-caches','browser-caches','gpu-shader','dev-caches','ide-caches','crash-dumps','thumbnail-cache','user-temp','elevated-system')
$script:PosixCatOrder    = @('root-temps','dev-caches','user-temp','browser-caches','ide-caches','crash-dumps','thumbnail-cache','recycle-bin')

# Builds the mutable checkpoint state. Returns $null when checkpointing is
# disabled (e.g. tests calling Get-JunkCandidates directly).
function New-ScanCheckpointState {
    param([string]$Path, [string]$Drive)
    if (-not $Path) { return $null }
    return @{
        Path                = $Path
        Drive               = $Drive
        CompletedCategories = (New-Object System.Collections.Generic.List[string])
        FileCounter         = [int64]0   # files in the CURRENT category (reset each category)
        LastPath            = ''          # last file FullName visited
        TotalBytes          = [int64]0    # cumulative candidate bytes so far
    }
}

# Writes <run>\scan-checkpoint.json with schema
#   {"drive":"X:","completedCategories":[...],"currentCategory":"X",
#    "lastPath":"X:\\...","totalBytesSoFar":123456,"timestamp":"..."}
# A write failure is caught and IGNORED: a checkpoint error must never block or
# fail the scan (MUST NOT).
function Write-ScanCheckpoint {
    param([hashtable]$State, [string]$CurrentCategory, [string]$LastPath)
    if ($null -eq $State) { return }
    try {
        $cp = [ordered]@{
            drive               = [string]$State['Drive']
            completedCategories = @($State['CompletedCategories'])
            currentCategory     = $CurrentCategory
            lastPath            = $LastPath
            totalBytesSoFar     = [int64]$State['TotalBytes']
            timestamp           = (Get-Date).ToString('o')
        }
        [System.IO.File]::WriteAllText([string]$State['Path'],
            (ConvertTo-Json -InputObject $cp), (New-Object System.Text.UTF8Encoding($false)))
    } catch {
        Write-Warning "checkpoint write failed (continuing scan): $($_.Exception.Message)"
    }
}

# Marks one category complete (added to completedCategories) and persists the
# checkpoint. Idempotent for a category already marked complete.
function Complete-ScanCategory {
    param([hashtable]$State, [string]$Category)
    if ($null -eq $State) { return }
    if (-not $State['CompletedCategories'].Contains($Category)) {
        [void]$State['CompletedCategories'].Add($Category)
    }
    Write-ScanCheckpoint -State $State -CurrentCategory $Category -LastPath ([string]$State['LastPath'])
}

# Ticks one enumerated file through the checkpoint counters. Every 500 files
# within the CURRENT category a mid-category checkpoint is written (with the
# file's FullName as lastPath) so an interrupted scan can resume from there.
# No checkpoint state -> no-op (per-file overhead only when checkpointing).
function Update-ScanFileTick {
    param([hashtable]$State, [string]$Category, [string]$FullName, [int64]$SizeBytes)
    if ($null -eq $State) { return }
    $State['LastPath']    = $FullName
    $State['TotalBytes']  = [int64]$State['TotalBytes'] + $SizeBytes
    $State['FileCounter'] = [int64]$State['FileCounter'] + 1
    if (([int64]$State['FileCounter'] % 500) -eq 0) {
        Write-ScanCheckpoint -State $State -CurrentCategory $Category -LastPath $FullName
    }
}

# Junction-safe recursive size + file count for a directory.
# Iterative (stack-based) walk; reparse-point children are skipped WITHOUT
# descending, so junctions/symlinks are never followed.
# todo 4: -Checkpoint/-Category optionally tick the scan checkpoint per file so
# long directory walks (app-caches etc.) can emit mid-category checkpoints.
function Get-DirStatsNoJunction {
    param(
        [string]$LiteralPath,
        [hashtable]$Checkpoint = $null,
        [string]$Category = ''
    )

    $size = [int64]0
    $count = [int64]0
    $stack = New-Object System.Collections.Stack
    $stack.Push($LiteralPath)

    while ($stack.Count -gt 0) {
        $dir = $stack.Pop()
        foreach ($item in Get-ChildItem -LiteralPath $dir -Force -ErrorAction SilentlyContinue) {
            if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) { continue }
            if ($item.PSIsContainer) {
                $stack.Push($item.FullName)
            } else {
                $size += [int64]$item.Length
                $count++
                if ($null -ne $Checkpoint) {
                    Update-ScanFileTick -State $Checkpoint -Category $Category -FullName $item.FullName -SizeBytes ([int64]$item.Length)
                }
            }
        }
    }
    return @{ SizeBytes = $size; FileCount = $count }
}

# Junction-safe recursive search for directories with an exact name
# (e.g. every `cache` dir under xwechat_files). A found dir is reported but
# NOT descended into (its contents are covered by its own row).
function Find-DirsNamed {
    param(
        [string]$LiteralRoot,
        [string]$Name
    )

    $found = New-Object System.Collections.Generic.List[string]
    $stack = New-Object System.Collections.Stack
    $stack.Push($LiteralRoot)

    while ($stack.Count -gt 0) {
        $dir = $stack.Pop()
        foreach ($item in Get-ChildItem -LiteralPath $dir -Force -Directory -ErrorAction SilentlyContinue) {
            if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) { continue }
            if ($item.Name -eq $Name) {
                $found.Add($item.FullName)
            } else {
                $stack.Push($item.FullName)
            }
        }
    }
    return $found
}

# Core classifier. Returns @{ Rows = List[candidate]; Evaluated = List[@{name;risk}] }.
# Candidate = @{ Category; Risk; Path; SizeBytes; FileCount; Action }.
#   -RootPath     drive root (e.g. 'X:\'); user-profile categories resolve
#                 against the real $env:USERPROFILE only when -IsUserDrive.
#   -IsUserDrive  $env:USERPROFILE lives under -RootPath.
#   -IncludeElevated  scan elevated-system (report-only) category.
#   -Categories   whitelist; empty/null = every applicable category.
function Get-JunkCandidates {
    param(
        [string]$RootPath,
        [bool]$IsUserDrive,
        [bool]$IncludeElevated,
        [string[]]$Categories,
        # ---- todo 4: checkpoint + resume (optional; absent = current behavior) ----
        [hashtable]$Checkpoint = $null,   # mutable state from New-ScanCheckpointState
        [hashtable]$ResumeState = $null   # @{ CompletedCategories; CurrentCategory; LastPath }
    )

    $root = $RootPath.TrimEnd('\') + '\'
    $result = @{
        Rows      = (New-Object System.Collections.Generic.List[object])
        Evaluated = (New-Object System.Collections.Generic.List[object])
    }
    # Normalize: accept both `-Categories a,b` (array) and `-Categories "a,b"`
    # (single quoted string) by splitting every element on commas.
    $catFilter = @($Categories | ForEach-Object { $_ -split ',' } | Where-Object { $_ })

    # ---- todo 4: progress total + resume bookkeeping ----
    $orderCats = if ($script:IsWin) { $script:WindowsCatOrder } else { $script:PosixCatOrder }
    $userCats  = if ($script:IsWin) {
        @('browser-caches','gpu-shader','dev-caches','ide-caches','crash-dumps','thumbnail-cache','user-temp')
    } else {
        @('dev-caches','user-temp','browser-caches','ide-caches','crash-dumps','thumbnail-cache','recycle-bin')
    }
    $totalCats = @($orderCats | Where-Object {
            $c = $_
            if ($c -eq 'elevated-system') { return $IncludeElevated }
            if ($userCats -contains $c)   { return $IsUserDrive }
            return $true
        } | Where-Object { -not $catFilter -or $catFilter -contains $_ }).Count
    $pg = @{ Done = 0; Total = $totalCats }

    $cpState       = $Checkpoint
    $resumeActive  = ($null -ne $ResumeState)
    $resumeSkipped = @{}
    $resumeCat     = ''
    $resumeLast    = ''
    if ($resumeActive) {
        foreach ($c in @($ResumeState['CompletedCategories'])) { $resumeSkipped[[string]$c] = $true }
        $resumeCat  = [string]$ResumeState['CurrentCategory']
        $resumeLast = if ($null -ne $ResumeState['LastPath']) { [string]$ResumeState['LastPath'] } else { '' }
    }

    # Emits the category-level Write-Progress line (todo 4 spec: category-level
    # granularity, NOT per-file). $Pg is a mutable {Done;Total} hashtable.
    # PercentComplete is clamped to 0..100 (pwsh 7 validates the range; the
    # Done counter also counts categories skipped by resume, so it can exceed
    # the filtered Total).
    function Update-CategoryProgress {
        param([hashtable]$Pg, [string]$Category)
        $Pg['Done'] = [int]$Pg['Done'] + 1
        $pct = [Math]::Floor(100 * [int]$Pg['Done'] / [Math]::Max(1, [int]$Pg['Total']))
        if ($pct -gt 100) { $pct = 100 }
        if ($pct -lt 0)   { $pct = 0 }
        Write-Progress -Activity "Scanning $Category" `
            -Status "$($Pg['Done'])/$($Pg['Total']) categories" `
            -PercentComplete $pct
    }

    # todo 4 resume filter for a per-file loop. ALWAYS re-sorts the input by
    # FullName (OrdinalIgnoreCase) before comparing - the raw Get-ChildItem
    # order is never relied on for the resume cut. Files sorting STRICTLY
    # BEFORE the checkpoint $lastPath are dropped; the file AT $lastPath is
    # RE-SCANNED (its row may have been partially written).
    #
    # KNOWN LIMITATION (todo 4 spec): a file INSERTED between the checkpoint
    # write and this resume run that sorts before $lastPath is silently missed
    # on this pass (it is picked up by the next full scan). Acceptable for a
    # cleanup tool.
    function Select-FilesForResume {
        param([object[]]$Files, [string]$Category)
        if (-not $resumeActive -or $Category -ne $resumeCat -or [string]::IsNullOrEmpty($resumeLast)) {
            return $Files
        }
        $sorted = New-Object System.Collections.Generic.List[object]
        foreach ($f in $Files) { [void]$sorted.Add($f) }
        $sorted.Sort([System.Comparison[object]]{ param($a, $b)
            return [System.StringComparer]::OrdinalIgnoreCase.Compare([string]$a.FullName, [string]$b.FullName)
        })
        return @($sorted | Where-Object {
            [System.StringComparer]::OrdinalIgnoreCase.Compare([string]$_.FullName, $resumeLast) -ge 0
        })
    }

    # Emit one candidate row.
    function Add-Candidate {
        param(
            [hashtable]$Result,
            [string]$Category,
            [string]$Path,
            [int64]$SizeBytes,
            [int64]$FileCount
        )
        $risk = $script:CategoryRiskMap[$Category]
        $Result.Rows.Add(@{
            Category   = $Category
            Risk       = $risk
            Path       = $Path
            SizeBytes  = $SizeBytes
            FileCount  = $FileCount
            Action     = $script:RiskActionMap[$risk]
        })
    }

    $cutoff = (Get-Date).AddDays(-7)   # 7-day age rule

    # ----------------------------------------------------------------
    # root-temps (SAFE): top-level FILES older than 7 days in
    # <Drive>\Temp, <Drive>\tmp, <Drive>\temp -- never subdirs.
    # ----------------------------------------------------------------
    if (-not $catFilter -or $catFilter -contains 'root-temps') {
        Update-CategoryProgress -Pg $pg -Category 'root-temps'
        if (-not $resumeSkipped.ContainsKey('root-temps')) {
            $result.Evaluated.Add(@{ name = 'root-temps'; risk = 'SAFE' })
            if ($null -ne $cpState) { $cpState['FileCounter'] = [int64]0 }
            # NTFS is case-insensitive, so 'Temp'/'tmp'/'temp' may resolve to the
            # same directory; dedupe on the actual (resolved) path.
            $seen = New-Object System.Collections.Generic.HashSet[string] ([System.StringComparer]::OrdinalIgnoreCase)
            foreach ($name in @('Temp', 'tmp', 'temp')) {
                $dir = Join-Path $root $name
                if (-not (Test-Path -LiteralPath $dir -PathType Container)) { continue }
                $actual = (Get-Item -LiteralPath $dir -Force).FullName
                if (-not $seen.Add($actual)) { continue }
                foreach ($f in @(Select-FilesForResume -Category 'root-temps' -Files @(Get-ChildItem -LiteralPath $actual -Force -File -ErrorAction SilentlyContinue))) {
                    Update-ScanFileTick -State $cpState -Category 'root-temps' -FullName $f.FullName -SizeBytes ([int64]$f.Length)
                    if ($f.LastWriteTime -lt $cutoff) {
                        Add-Candidate -Result $result -Category 'root-temps' -Path $f.FullName -SizeBytes ([int64]$f.Length) -FileCount 1
                    }
                }
            }
            Complete-ScanCategory -State $cpState -Category 'root-temps'
        }
    }

    # ----------------------------------------------------------------
    # root-logs (SAFE): <Drive>\*.log, <Drive>\*.tmp, *_install*.log at
    # drive root only (no recursion).
    # ----------------------------------------------------------------
    if (-not $catFilter -or $catFilter -contains 'root-logs') {
        Update-CategoryProgress -Pg $pg -Category 'root-logs'
        if (-not $resumeSkipped.ContainsKey('root-logs')) {
            $result.Evaluated.Add(@{ name = 'root-logs'; risk = 'SAFE' })
            if ($null -ne $cpState) { $cpState['FileCounter'] = [int64]0 }
            foreach ($f in @(Select-FilesForResume -Category 'root-logs' -Files @(Get-ChildItem -LiteralPath $root -Force -File -ErrorAction SilentlyContinue))) {
                Update-ScanFileTick -State $cpState -Category 'root-logs' -FullName $f.FullName -SizeBytes ([int64]$f.Length)
                if ($f.Extension -in @('.log', '.tmp') -or $f.Name -like '*_install*.log') {
                    Add-Candidate -Result $result -Category 'root-logs' -Path $f.FullName -SizeBytes ([int64]$f.Length) -FileCount 1
                }
            }
            Complete-ScanCategory -State $cpState -Category 'root-logs'
        }
    }

    # ----------------------------------------------------------------
    # duplicate-archives (ASK): <Drive>\*.zip|rar|7z where a same-name
    # extracted folder exists next to it (archive only, never the folder).
    # ----------------------------------------------------------------
    if (-not $catFilter -or $catFilter -contains 'duplicate-archives') {
        Update-CategoryProgress -Pg $pg -Category 'duplicate-archives'
        if (-not $resumeSkipped.ContainsKey('duplicate-archives')) {
            $result.Evaluated.Add(@{ name = 'duplicate-archives'; risk = 'ASK' })
            if ($null -ne $cpState) { $cpState['FileCounter'] = [int64]0 }
            foreach ($f in @(Select-FilesForResume -Category 'duplicate-archives' -Files @(Get-ChildItem -LiteralPath $root -Force -File -ErrorAction SilentlyContinue))) {
                Update-ScanFileTick -State $cpState -Category 'duplicate-archives' -FullName $f.FullName -SizeBytes ([int64]$f.Length)
                if ($f.Extension -notin @('.zip', '.rar', '.7z')) { continue }
                $base = [System.IO.Path]::GetFileNameWithoutExtension($f.Name)
                if (Test-Path -LiteralPath (Join-Path $root $base) -PathType Container) {
                    Add-Candidate -Result $result -Category 'duplicate-archives' -Path $f.FullName -SizeBytes ([int64]$f.Length) -FileCount 1
                }
            }
            Complete-ScanCategory -State $cpState -Category 'duplicate-archives'
        }
    }

    # ----------------------------------------------------------------
    # empty-dirs (SAFE): top-level dirs passing the junction-aware
    # Test-DirEmpty; skip $RECYCLE.BIN, System Volume Information,
    # .claude; never bare -Recurse; junctions never followed.
    # ----------------------------------------------------------------
    if (-not $catFilter -or $catFilter -contains 'empty-dirs') {
        Update-CategoryProgress -Pg $pg -Category 'empty-dirs'
        if (-not $resumeSkipped.ContainsKey('empty-dirs')) {
            $result.Evaluated.Add(@{ name = 'empty-dirs'; risk = 'SAFE' })
            if ($null -ne $cpState) { $cpState['FileCounter'] = [int64]0 }
            $skip = @('$RECYCLE.BIN', 'SYSTEM VOLUME INFORMATION', '.CLAUDE')
            foreach ($d in Get-ChildItem -LiteralPath $root -Force -Directory -ErrorAction SilentlyContinue) {
                if ($skip -contains $d.Name.ToUpperInvariant()) { continue }
                if (Test-IsJunction -Path $d.FullName) { continue }
                if (Test-DirEmpty -Path $d.FullName -ErrorAction SilentlyContinue) {
                    Add-Candidate -Result $result -Category 'empty-dirs' -Path $d.FullName -SizeBytes 0 -FileCount 0
                }
            }
            Complete-ScanCategory -State $cpState -Category 'empty-dirs'
        }
    }

    # ----------------------------------------------------------------
    # recycle-bin (ASK): <Drive>\$RECYCLE.BIN content size; never
    # auto-deleted (report for user approval only).
    # ----------------------------------------------------------------
    if (-not $catFilter -or $catFilter -contains 'recycle-bin') {
        Update-CategoryProgress -Pg $pg -Category 'recycle-bin'
        if (-not $resumeSkipped.ContainsKey('recycle-bin')) {
            $result.Evaluated.Add(@{ name = 'recycle-bin'; risk = 'ASK' })
            if ($null -ne $cpState) { $cpState['FileCounter'] = [int64]0 }
            $rb = Join-Path $root '$RECYCLE.BIN'
            if (Test-Path -LiteralPath $rb -PathType Container) {
                $stats = Get-DirStatsNoJunction -LiteralPath $rb -Checkpoint $cpState -Category 'recycle-bin'
                Add-Candidate -Result $result -Category 'recycle-bin' -Path $rb -SizeBytes $stats.SizeBytes -FileCount $stats.FileCount
            }
            Complete-ScanCategory -State $cpState -Category 'recycle-bin'
        }
    }

    # ----------------------------------------------------------------
    # root-suspicious (CAUTION): <Drive>\*.dll and *.exe whose basename
    # (without extension) matches NEITHER any top-level dir name on the
    # drive NOR any subdir under <Drive>\Program Files (x86) -- D-drive
    # precedent dinput8.dll / sdhdship.exe. Action=quarantine.
    # ----------------------------------------------------------------
    if (-not $catFilter -or $catFilter -contains 'root-suspicious') {
        Update-CategoryProgress -Pg $pg -Category 'root-suspicious'
        if (-not $resumeSkipped.ContainsKey('root-suspicious')) {
            $result.Evaluated.Add(@{ name = 'root-suspicious'; risk = 'CAUTION' })
            if ($null -ne $cpState) { $cpState['FileCounter'] = [int64]0 }
            $excluded = New-Object System.Collections.Generic.HashSet[string] ([System.StringComparer]::OrdinalIgnoreCase)
            foreach ($d in Get-ChildItem -LiteralPath $root -Force -Directory -ErrorAction SilentlyContinue) {
                [void]$excluded.Add($d.Name)
            }
            foreach ($pf in @((Join-Path $root 'Program Files'), (Join-Path $root 'Program Files (x86)'))) {
                if (Test-Path -LiteralPath $pf -PathType Container) {
                    foreach ($d in Get-ChildItem -LiteralPath $pf -Force -Directory -ErrorAction SilentlyContinue) {
                        [void]$excluded.Add($d.Name)
                    }
                }
            }
            foreach ($f in @(Select-FilesForResume -Category 'root-suspicious' -Files @(Get-ChildItem -LiteralPath $root -Force -File -ErrorAction SilentlyContinue))) {
                Update-ScanFileTick -State $cpState -Category 'root-suspicious' -FullName $f.FullName -SizeBytes ([int64]$f.Length)
                if ($f.Extension -notin @('.dll', '.exe')) { continue }
                $base = [System.IO.Path]::GetFileNameWithoutExtension($f.Name)
                if ($excluded.Contains($base)) { continue }
                Add-Candidate -Result $result -Category 'root-suspicious' -Path $f.FullName -SizeBytes ([int64]$f.Length) -FileCount 1
            }
            Complete-ScanCategory -State $cpState -Category 'root-suspicious'
        }
    }

    # ----------------------------------------------------------------
    # app-caches (SAFE): per-app-path-map templates resolved against
    # $root, existence-checked.
    # ----------------------------------------------------------------
    if (-not $catFilter -or $catFilter -contains 'app-caches') {
        Update-CategoryProgress -Pg $pg -Category 'app-caches'
        if (-not $resumeSkipped.ContainsKey('app-caches')) {
            $result.Evaluated.Add(@{ name = 'app-caches'; risk = 'SAFE' })
            if ($null -ne $cpState) { $cpState['FileCounter'] = [int64]0 }

            # {D}\anaconda3\pkgs\cache
            $p = Join-Path $root 'anaconda3\pkgs\cache'
            if (Test-Path -LiteralPath $p -PathType Container) {
                $stats = Get-DirStatsNoJunction -LiteralPath $p -Checkpoint $cpState -Category 'app-caches'
                Add-Candidate -Result $result -Category 'app-caches' -Path $p -SizeBytes $stats.SizeBytes -FileCount $stats.FileCount
            }

            # {D}\Wegame\*\tiny_cache and {D}\Wegame\*\cache
            foreach ($sub in @('tiny_cache', 'cache')) {
                $wg = Join-Path $root 'Wegame'
                if (Test-Path -LiteralPath $wg -PathType Container) {
                    foreach ($d in Get-ChildItem -LiteralPath $wg -Force -Directory -ErrorAction SilentlyContinue) {
                        $p = Join-Path $d.FullName $sub
                        if (Test-Path -LiteralPath $p -PathType Container) {
                            $stats = Get-DirStatsNoJunction -LiteralPath $p -Checkpoint $cpState -Category 'app-caches'
                            Add-Candidate -Result $result -Category 'app-caches' -Path $p -SizeBytes $stats.SizeBytes -FileCount $stats.FileCount
                        }
                    }
                }
            }

            # {D}\WeiXin\xwechat_files\**\cache (junction-safe recursive)
            $wx = Join-Path $root 'WeiXin\xwechat_files'
            if (Test-Path -LiteralPath $wx -PathType Container) {
                foreach ($p in @(Find-DirsNamed -LiteralRoot $wx -Name 'cache')) {
                    $stats = Get-DirStatsNoJunction -LiteralPath $p -Checkpoint $cpState -Category 'app-caches'
                    Add-Candidate -Result $result -Category 'app-caches' -Path $p -SizeBytes $stats.SizeBytes -FileCount $stats.FileCount
                }
            }

            # {D}\SteamLibrary\steamapps\common\* empty dirs (junction-aware;
            # appmanifest files are never dirs, so never touched)
            $steam = Join-Path $root 'SteamLibrary\steamapps\common'
            if (Test-Path -LiteralPath $steam -PathType Container) {
                foreach ($d in Get-ChildItem -LiteralPath $steam -Force -Directory -ErrorAction SilentlyContinue) {
                    if (Test-IsJunction -Path $d.FullName) { continue }
                    if (Test-DirEmpty -Path $d.FullName -ErrorAction SilentlyContinue) {
                        Add-Candidate -Result $result -Category 'app-caches' -Path $d.FullName -SizeBytes 0 -FileCount 0
                    }
                }
            }

            # {D}\Ubisoft Game Launcher\cache
            $p = Join-Path $root 'Ubisoft Game Launcher\cache'
            if (Test-Path -LiteralPath $p -PathType Container) {
                $stats = Get-DirStatsNoJunction -LiteralPath $p -Checkpoint $cpState -Category 'app-caches'
                Add-Candidate -Result $result -Category 'app-caches' -Path $p -SizeBytes $stats.SizeBytes -FileCount $stats.FileCount
            }

            Complete-ScanCategory -State $cpState -Category 'app-caches'
        }
    }

    # ----------------------------------------------------------------
    # User-profile categories: ONLY when the user profile lives on the
    # scanned drive. All paths resolve via $env:LOCALAPPDATA /
    # $env:APPDATA / $env:USERPROFILE and are existence-checked.
    # ----------------------------------------------------------------
    if ($IsUserDrive) {

        # browser-caches: Chrome/Edge Default\{Cache, Code Cache, GPUCache}
        # + Crashpad\reports
        if (-not $catFilter -or $catFilter -contains 'browser-caches') {
            Update-CategoryProgress -Pg $pg -Category 'browser-caches'
            if (-not $resumeSkipped.ContainsKey('browser-caches')) {
                $result.Evaluated.Add(@{ name = 'browser-caches'; risk = 'SAFE' })
                if ($null -ne $cpState) { $cpState['FileCounter'] = [int64]0 }
                foreach ($browser in @('Google\Chrome', 'Microsoft\Edge')) {
                    $default = Join-Path $env:LOCALAPPDATA "$browser\User Data\Default"
                    foreach ($sub in @('Cache', 'Code Cache', 'GPUCache', 'Crashpad\reports')) {
                        $p = Join-Path $default $sub
                        if (Test-Path -LiteralPath $p -PathType Container) {
                            $stats = Get-DirStatsNoJunction -LiteralPath $p -Checkpoint $cpState -Category 'browser-caches'
                            Add-Candidate -Result $result -Category 'browser-caches' -Path $p -SizeBytes $stats.SizeBytes -FileCount $stats.FileCount
                        }
                    }
                }
                Complete-ScanCategory -State $cpState -Category 'browser-caches'
            }
        }

        # gpu-shader: NVIDIA\DXCache + NVIDIA\GLCache, D3DSCache
        if (-not $catFilter -or $catFilter -contains 'gpu-shader') {
            Update-CategoryProgress -Pg $pg -Category 'gpu-shader'
            if (-not $resumeSkipped.ContainsKey('gpu-shader')) {
                $result.Evaluated.Add(@{ name = 'gpu-shader'; risk = 'SAFE' })
                if ($null -ne $cpState) { $cpState['FileCounter'] = [int64]0 }
                foreach ($p in @(
                        (Join-Path $env:LOCALAPPDATA 'NVIDIA\DXCache'),
                        (Join-Path $env:LOCALAPPDATA 'NVIDIA\GLCache'),
                        (Join-Path $env:LOCALAPPDATA 'D3DSCache'))) {
                    if (Test-Path -LiteralPath $p -PathType Container) {
                        $stats = Get-DirStatsNoJunction -LiteralPath $p -Checkpoint $cpState -Category 'gpu-shader'
                        Add-Candidate -Result $result -Category 'gpu-shader' -Path $p -SizeBytes $stats.SizeBytes -FileCount $stats.FileCount
                    }
                }
                Complete-ScanCategory -State $cpState -Category 'gpu-shader'
            }
        }

        # dev-caches: pip\cache, npm-cache, .cache\{torch,huggingface,
        # opencode,codex-runtimes,pkg}
        if (-not $catFilter -or $catFilter -contains 'dev-caches') {
            Update-CategoryProgress -Pg $pg -Category 'dev-caches'
            if (-not $resumeSkipped.ContainsKey('dev-caches')) {
                $result.Evaluated.Add(@{ name = 'dev-caches'; risk = 'SAFE' })
                if ($null -ne $cpState) { $cpState['FileCounter'] = [int64]0 }
                foreach ($p in @(
                        (Join-Path $env:LOCALAPPDATA 'pip\cache'),
                        (Join-Path $env:LOCALAPPDATA 'npm-cache'))) {
                    if (Test-Path -LiteralPath $p -PathType Container) {
                        $stats = Get-DirStatsNoJunction -LiteralPath $p -Checkpoint $cpState -Category 'dev-caches'
                        Add-Candidate -Result $result -Category 'dev-caches' -Path $p -SizeBytes $stats.SizeBytes -FileCount $stats.FileCount
                    }
                }
                foreach ($sub in @('torch', 'huggingface', 'opencode', 'codex-runtimes', 'pkg')) {
                    $p = Join-Path $env:USERPROFILE (Join-Path '.cache' $sub)
                    if (Test-Path -LiteralPath $p -PathType Container) {
                        $stats = Get-DirStatsNoJunction -LiteralPath $p -Checkpoint $cpState -Category 'dev-caches'
                        Add-Candidate -Result $result -Category 'dev-caches' -Path $p -SizeBytes $stats.SizeBytes -FileCount $stats.FileCount
                    }
                }
                Complete-ScanCategory -State $cpState -Category 'dev-caches'
            }
        }

        # ide-caches: JetBrains *\caches + *\log (+ Toolbox cache/logs);
        # Zotero cache2/startupCache/shader-cache; Jedi *.pkl
        if (-not $catFilter -or $catFilter -contains 'ide-caches') {
            Update-CategoryProgress -Pg $pg -Category 'ide-caches'
            if (-not $resumeSkipped.ContainsKey('ide-caches')) {
                $result.Evaluated.Add(@{ name = 'ide-caches'; risk = 'SAFE' })
                if ($null -ne $cpState) { $cpState['FileCounter'] = [int64]0 }
                $jb = Join-Path $env:LOCALAPPDATA 'JetBrains'
                if (Test-Path -LiteralPath $jb -PathType Container) {
                    foreach ($d in Get-ChildItem -LiteralPath $jb -Force -Directory -ErrorAction SilentlyContinue) {
                        $isToolbox = $d.Name -in @('Toolbox', 'Toolbox-Dev')
                        foreach ($sub in @('caches', 'log') + $(if ($isToolbox) { @('cache', 'logs') } else { @() })) {
                            $p = Join-Path $d.FullName $sub
                            if (Test-Path -LiteralPath $p -PathType Container) {
                                $stats = Get-DirStatsNoJunction -LiteralPath $p -Checkpoint $cpState -Category 'ide-caches'
                                Add-Candidate -Result $result -Category 'ide-caches' -Path $p -SizeBytes $stats.SizeBytes -FileCount $stats.FileCount
                            }
                        }
                    }
                }
                $profiles = Join-Path $env:APPDATA 'Zotero\Zotero\Profiles'
                if (Test-Path -LiteralPath $profiles -PathType Container) {
                    foreach ($d in Get-ChildItem -LiteralPath $profiles -Force -Directory -ErrorAction SilentlyContinue) {
                        foreach ($sub in @('cache2', 'startupCache', 'shader-cache')) {
                            $p = Join-Path $d.FullName $sub
                            if (Test-Path -LiteralPath $p -PathType Container) {
                                $stats = Get-DirStatsNoJunction -LiteralPath $p -Checkpoint $cpState -Category 'ide-caches'
                                Add-Candidate -Result $result -Category 'ide-caches' -Path $p -SizeBytes $stats.SizeBytes -FileCount $stats.FileCount
                            }
                        }
                    }
                }
                $jedi = Join-Path $env:LOCALAPPDATA 'Jedi\Jedi'
                if (Test-Path -LiteralPath $jedi -PathType Container) {
                    foreach ($d in Get-ChildItem -LiteralPath $jedi -Force -Directory -ErrorAction SilentlyContinue) {
                        foreach ($f in @(Select-FilesForResume -Category 'ide-caches' -Files @(Get-ChildItem -LiteralPath $d.FullName -Force -File -Filter '*.pkl' -ErrorAction SilentlyContinue))) {
                            Update-ScanFileTick -State $cpState -Category 'ide-caches' -FullName $f.FullName -SizeBytes ([int64]$f.Length)
                            Add-Candidate -Result $result -Category 'ide-caches' -Path $f.FullName -SizeBytes ([int64]$f.Length) -FileCount 1
                        }
                    }
                }
                Complete-ScanCategory -State $cpState -Category 'ide-caches'
            }
        }

        # crash-dumps: %LOCALAPPDATA%\CrashDumps + top-level Crashpad dirs
        if (-not $catFilter -or $catFilter -contains 'crash-dumps') {
            Update-CategoryProgress -Pg $pg -Category 'crash-dumps'
            if (-not $resumeSkipped.ContainsKey('crash-dumps')) {
                $result.Evaluated.Add(@{ name = 'crash-dumps'; risk = 'SAFE' })
                if ($null -ne $cpState) { $cpState['FileCounter'] = [int64]0 }
                $p = Join-Path $env:LOCALAPPDATA 'CrashDumps'
                if (Test-Path -LiteralPath $p -PathType Container) {
                    $stats = Get-DirStatsNoJunction -LiteralPath $p -Checkpoint $cpState -Category 'crash-dumps'
                    Add-Candidate -Result $result -Category 'crash-dumps' -Path $p -SizeBytes $stats.SizeBytes -FileCount $stats.FileCount
                }
                foreach ($d in Get-ChildItem -LiteralPath $env:LOCALAPPDATA -Force -Directory -ErrorAction SilentlyContinue) {
                    if ($d.Name -eq 'Crashpad' -and -not (Test-IsJunction -Path $d.FullName)) {
                        $stats = Get-DirStatsNoJunction -LiteralPath $d.FullName -Checkpoint $cpState -Category 'crash-dumps'
                        Add-Candidate -Result $result -Category 'crash-dumps' -Path $d.FullName -SizeBytes $stats.SizeBytes -FileCount $stats.FileCount
                    }
                }
                Complete-ScanCategory -State $cpState -Category 'crash-dumps'
            }
        }

        # thumbnail-cache: Explorer thumbcache_*.db + iconcache_*.db
        if (-not $catFilter -or $catFilter -contains 'thumbnail-cache') {
            Update-CategoryProgress -Pg $pg -Category 'thumbnail-cache'
            if (-not $resumeSkipped.ContainsKey('thumbnail-cache')) {
                $result.Evaluated.Add(@{ name = 'thumbnail-cache'; risk = 'SAFE' })
                if ($null -ne $cpState) { $cpState['FileCounter'] = [int64]0 }
                $explorer = Join-Path $env:LOCALAPPDATA 'Microsoft\Windows\Explorer'
                if (Test-Path -LiteralPath $explorer -PathType Container) {
                    foreach ($f in @(Select-FilesForResume -Category 'thumbnail-cache' -Files @(Get-ChildItem -LiteralPath $explorer -Force -File -ErrorAction SilentlyContinue))) {
                        Update-ScanFileTick -State $cpState -Category 'thumbnail-cache' -FullName $f.FullName -SizeBytes ([int64]$f.Length)
                        if ($f.Name -like 'thumbcache_*.db' -or $f.Name -like 'iconcache_*.db') {
                            Add-Candidate -Result $result -Category 'thumbnail-cache' -Path $f.FullName -SizeBytes ([int64]$f.Length) -FileCount 1
                        }
                    }
                }
                Complete-ScanCategory -State $cpState -Category 'thumbnail-cache'
            }
        }

        # user-temp: %LOCALAPPDATA%\Temp top-level files older than 7 days
        # (never subdirs)
        if (-not $catFilter -or $catFilter -contains 'user-temp') {
            Update-CategoryProgress -Pg $pg -Category 'user-temp'
            if (-not $resumeSkipped.ContainsKey('user-temp')) {
                $result.Evaluated.Add(@{ name = 'user-temp'; risk = 'SAFE' })
                if ($null -ne $cpState) { $cpState['FileCounter'] = [int64]0 }
                $p = Join-Path $env:LOCALAPPDATA 'Temp'
                if (Test-Path -LiteralPath $p -PathType Container) {
                    foreach ($f in @(Select-FilesForResume -Category 'user-temp' -Files @(Get-ChildItem -LiteralPath $p -Force -File -ErrorAction SilentlyContinue))) {
                        Update-ScanFileTick -State $cpState -Category 'user-temp' -FullName $f.FullName -SizeBytes ([int64]$f.Length)
                        if ($f.LastWriteTime -lt $cutoff) {
                            Add-Candidate -Result $result -Category 'user-temp' -Path $f.FullName -SizeBytes ([int64]$f.Length) -FileCount 1
                        }
                    }
                }
                Complete-ScanCategory -State $cpState -Category 'user-temp'
            }
        }
    }

    # ----------------------------------------------------------------
    # elevated-system (ELEVATED, report-only): only with -IncludeElevated.
    # Everything here is REPORTED for the later elevated clean; nothing
    # is ever executed or deleted by the scan.
    # ----------------------------------------------------------------
    if ($IncludeElevated) {
        if (-not $catFilter -or $catFilter -contains 'elevated-system') {
            Update-CategoryProgress -Pg $pg -Category 'elevated-system'
            if (-not $resumeSkipped.ContainsKey('elevated-system')) {
                $result.Evaluated.Add(@{ name = 'elevated-system'; risk = 'ELEVATED' })
                if ($null -ne $cpState) { $cpState['FileCounter'] = [int64]0 }

                # Windows\Temp top-level files older than 7 days
                $p = Join-Path $root 'Windows\Temp'
                if (Test-Path -LiteralPath $p -PathType Container) {
                    foreach ($f in @(Select-FilesForResume -Category 'elevated-system' -Files @(Get-ChildItem -LiteralPath $p -Force -File -ErrorAction SilentlyContinue))) {
                        Update-ScanFileTick -State $cpState -Category 'elevated-system' -FullName $f.FullName -SizeBytes ([int64]$f.Length)
                        if ($f.LastWriteTime -lt $cutoff) {
                            Add-Candidate -Result $result -Category 'elevated-system' -Path $f.FullName -SizeBytes ([int64]$f.Length) -FileCount 1
                        }
                    }
                }

                # Prefetch *.pf only -- never Layout.ini
                $p = Join-Path $root 'Windows\Prefetch'
                if (Test-Path -LiteralPath $p -PathType Container) {
                    foreach ($f in @(Select-FilesForResume -Category 'elevated-system' -Files @(Get-ChildItem -LiteralPath $p -Force -File -Filter '*.pf' -ErrorAction SilentlyContinue))) {
                        Update-ScanFileTick -State $cpState -Category 'elevated-system' -FullName $f.FullName -SizeBytes ([int64]$f.Length)
                        if ($f.Name -eq 'Layout.ini') { continue }
                        Add-Candidate -Result $result -Category 'elevated-system' -Path $f.FullName -SizeBytes ([int64]$f.Length) -FileCount 1
                    }
                }

                # SoftwareDistribution -- guarded (report the dir, never touch)
                $p = Join-Path $root 'Windows\SoftwareDistribution'
                if (Test-Path -LiteralPath $p -PathType Container) {
                    $stats = Get-DirStatsNoJunction -LiteralPath $p -Checkpoint $cpState -Category 'elevated-system'
                    Add-Candidate -Result $result -Category 'elevated-system' -Path $p -SizeBytes $stats.SizeBytes -FileCount $stats.FileCount
                }

                # WindowsUpdate *.etl older than 7 days
                $p = Join-Path $root 'Windows\Logs\WindowsUpdate'
                if (Test-Path -LiteralPath $p -PathType Container) {
                    foreach ($f in @(Select-FilesForResume -Category 'elevated-system' -Files @(Get-ChildItem -LiteralPath $p -Force -File -Filter '*.etl' -ErrorAction SilentlyContinue))) {
                        Update-ScanFileTick -State $cpState -Category 'elevated-system' -FullName $f.FullName -SizeBytes ([int64]$f.Length)
                        if ($f.LastWriteTime -lt $cutoff) {
                            Add-Candidate -Result $result -Category 'elevated-system' -Path $f.FullName -SizeBytes ([int64]$f.Length) -FileCount 1
                        }
                    }
                }

                # CBS CbsPersist_*.cab
                $p = Join-Path $root 'Windows\Logs\CBS'
                if (Test-Path -LiteralPath $p -PathType Container) {
                    foreach ($f in @(Select-FilesForResume -Category 'elevated-system' -Files @(Get-ChildItem -LiteralPath $p -Force -File -Filter 'CbsPersist_*.cab' -ErrorAction SilentlyContinue))) {
                        Update-ScanFileTick -State $cpState -Category 'elevated-system' -FullName $f.FullName -SizeBytes ([int64]$f.Length)
                        Add-Candidate -Result $result -Category 'elevated-system' -Path $f.FullName -SizeBytes ([int64]$f.Length) -FileCount 1
                    }
                }

                # DISM /StartComponentCleanup (no /ResetBase) -- executed later by
                # the elevated clean-drive batch; report-only marker row here.
                Add-Candidate -Result $result -Category 'elevated-system' -Path 'DISM StartComponentCleanup (no /ResetBase)' -SizeBytes 0 -FileCount 0

                Complete-ScanCategory -State $cpState -Category 'elevated-system'
            }
        }
    }

    # ----------------------------------------------------------------
    # Linux/macOS equivalents (todo 2): SAME category ids as the Windows
    # branches above -- only the path templates differ, resolved via
    # Get-UserCacheDir / Get-SystemTempDir from lib/platform.ps1.
    # $IsUserDrive on non-Windows = ($Drive -eq '/'), so the user-profile
    # categories below run whenever the single '/' drive is scanned.
    # ----------------------------------------------------------------
    if (-not $script:IsWin) {
        $cache = Get-UserCacheDir
        $tmp = Get-SystemTempDir

        # root-temps (SAFE): {temp}/* top-level files older than 7 days
        if (-not $catFilter -or $catFilter -contains 'root-temps') {
            Update-CategoryProgress -Pg $pg -Category 'root-temps'
            if (-not $resumeSkipped.ContainsKey('root-temps')) {
                $result.Evaluated.Add(@{ name = 'root-temps'; risk = 'SAFE' })
                if ($null -ne $cpState) { $cpState['FileCounter'] = [int64]0 }
                if (Test-Path -LiteralPath $tmp -PathType Container) {
                    foreach ($f in @(Select-FilesForResume -Category 'root-temps' -Files @(Get-ChildItem -LiteralPath $tmp -Force -File -ErrorAction SilentlyContinue))) {
                        Update-ScanFileTick -State $cpState -Category 'root-temps' -FullName $f.FullName -SizeBytes ([int64]$f.Length)
                        if ($f.LastWriteTime -lt $cutoff) {
                            Add-Candidate -Result $result -Category 'root-temps' -Path $f.FullName -SizeBytes ([int64]$f.Length) -FileCount 1
                        }
                    }
                }
                Complete-ScanCategory -State $cpState -Category 'root-temps'
            }
        }

        if ($IsUserDrive) {

            # dev-caches: {cache}/{pip,npm,torch,huggingface,opencode,codex-runtimes}
            if (-not $catFilter -or $catFilter -contains 'dev-caches') {
                Update-CategoryProgress -Pg $pg -Category 'dev-caches'
                if (-not $resumeSkipped.ContainsKey('dev-caches')) {
                    $result.Evaluated.Add(@{ name = 'dev-caches'; risk = 'SAFE' })
                    if ($null -ne $cpState) { $cpState['FileCounter'] = [int64]0 }
                    foreach ($sub in @('pip', 'npm', 'torch', 'huggingface', 'opencode', 'codex-runtimes')) {
                        $p = Join-Path $cache $sub
                        if (Test-Path -LiteralPath $p -PathType Container) {
                            $stats = Get-DirStatsNoJunction -LiteralPath $p -Checkpoint $cpState -Category 'dev-caches'
                            Add-Candidate -Result $result -Category 'dev-caches' -Path $p -SizeBytes $stats.SizeBytes -FileCount $stats.FileCount
                        }
                    }
                    Complete-ScanCategory -State $cpState -Category 'dev-caches'
                }
            }

            # user-temp (SAFE): {cache}/* top-level files older than 7 days
            if (-not $catFilter -or $catFilter -contains 'user-temp') {
                Update-CategoryProgress -Pg $pg -Category 'user-temp'
                if (-not $resumeSkipped.ContainsKey('user-temp')) {
                    $result.Evaluated.Add(@{ name = 'user-temp'; risk = 'SAFE' })
                    if ($null -ne $cpState) { $cpState['FileCounter'] = [int64]0 }
                    foreach ($f in @(Select-FilesForResume -Category 'user-temp' -Files @(Get-ChildItem -LiteralPath $cache -Force -File -ErrorAction SilentlyContinue))) {
                        Update-ScanFileTick -State $cpState -Category 'user-temp' -FullName $f.FullName -SizeBytes ([int64]$f.Length)
                        if ($f.LastWriteTime -lt $cutoff) {
                            Add-Candidate -Result $result -Category 'user-temp' -Path $f.FullName -SizeBytes ([int64]$f.Length) -FileCount 1
                        }
                    }
                    Complete-ScanCategory -State $cpState -Category 'user-temp'
                }
            }

            # browser-caches: Chrome/Edge Default\{Cache, Code Cache, GPUCache};
            # Firefox {cache}/mozilla/firefox/*/cache2/
            if (-not $catFilter -or $catFilter -contains 'browser-caches') {
                Update-CategoryProgress -Pg $pg -Category 'browser-caches'
                if (-not $resumeSkipped.ContainsKey('browser-caches')) {
                    $result.Evaluated.Add(@{ name = 'browser-caches'; risk = 'SAFE' })
                    if ($null -ne $cpState) { $cpState['FileCounter'] = [int64]0 }
                    foreach ($browser in @('google-chrome', 'microsoft-edge')) {
                        $default = Join-Path $cache "$browser/Default"
                        foreach ($sub in @('Cache', 'Code Cache', 'GPUCache')) {
                            $p = Join-Path $default $sub
                            if (Test-Path -LiteralPath $p -PathType Container) {
                                $stats = Get-DirStatsNoJunction -LiteralPath $p -Checkpoint $cpState -Category 'browser-caches'
                                Add-Candidate -Result $result -Category 'browser-caches' -Path $p -SizeBytes $stats.SizeBytes -FileCount $stats.FileCount
                            }
                        }
                    }
                    $ff = Join-Path $cache 'mozilla/firefox'
                    if (Test-Path -LiteralPath $ff -PathType Container) {
                        foreach ($d in Get-ChildItem -LiteralPath $ff -Force -Directory -ErrorAction SilentlyContinue) {
                            $c2 = Join-Path $d.FullName 'cache2'
                            if (Test-Path -LiteralPath $c2 -PathType Container) {
                                $stats = Get-DirStatsNoJunction -LiteralPath $c2 -Checkpoint $cpState -Category 'browser-caches'
                                Add-Candidate -Result $result -Category 'browser-caches' -Path $c2 -SizeBytes $stats.SizeBytes -FileCount $stats.FileCount
                            }
                        }
                    }
                    Complete-ScanCategory -State $cpState -Category 'browser-caches'
                }
            }

            # ide-caches: JetBrains */{caches,log}; VS Code ~/.config/Code/
            # {Cache, CachedData, logs}; Zotero {cache2, startupCache, shader-cache}
            if (-not $catFilter -or $catFilter -contains 'ide-caches') {
                Update-CategoryProgress -Pg $pg -Category 'ide-caches'
                if (-not $resumeSkipped.ContainsKey('ide-caches')) {
                    $result.Evaluated.Add(@{ name = 'ide-caches'; risk = 'SAFE' })
                    if ($null -ne $cpState) { $cpState['FileCounter'] = [int64]0 }
                    $jb = Join-Path $cache 'JetBrains'
                    if (Test-Path -LiteralPath $jb -PathType Container) {
                        foreach ($d in Get-ChildItem -LiteralPath $jb -Force -Directory -ErrorAction SilentlyContinue) {
                            foreach ($sub in @('caches', 'log')) {
                                $p = Join-Path $d.FullName $sub
                                if (Test-Path -LiteralPath $p -PathType Container) {
                                    $stats = Get-DirStatsNoJunction -LiteralPath $p -Checkpoint $cpState -Category 'ide-caches'
                                    Add-Candidate -Result $result -Category 'ide-caches' -Path $p -SizeBytes $stats.SizeBytes -FileCount $stats.FileCount
                                }
                            }
                        }
                    }
                    $vsc = Join-Path $env:HOME '.config/Code'
                    foreach ($sub in @('Cache', 'CachedData', 'logs')) {
                        $p = Join-Path $vsc $sub
                        if (Test-Path -LiteralPath $p -PathType Container) {
                            $stats = Get-DirStatsNoJunction -LiteralPath $p -Checkpoint $cpState -Category 'ide-caches'
                            Add-Candidate -Result $result -Category 'ide-caches' -Path $p -SizeBytes $stats.SizeBytes -FileCount $stats.FileCount
                        }
                    }
                    $zo = Join-Path $cache 'zotero'
                    foreach ($sub in @('cache2', 'startupCache', 'shader-cache')) {
                        $p = Join-Path $zo $sub
                        if (Test-Path -LiteralPath $p -PathType Container) {
                            $stats = Get-DirStatsNoJunction -LiteralPath $p -Checkpoint $cpState -Category 'ide-caches'
                            Add-Candidate -Result $result -Category 'ide-caches' -Path $p -SizeBytes $stats.SizeBytes -FileCount $stats.FileCount
                        }
                    }
                    Complete-ScanCategory -State $cpState -Category 'ide-caches'
                }
            }

            # crash-dumps: /var/crash (apport crash reports)
            if (-not $catFilter -or $catFilter -contains 'crash-dumps') {
                Update-CategoryProgress -Pg $pg -Category 'crash-dumps'
                if (-not $resumeSkipped.ContainsKey('crash-dumps')) {
                    $result.Evaluated.Add(@{ name = 'crash-dumps'; risk = 'SAFE' })
                    if ($null -ne $cpState) { $cpState['FileCounter'] = [int64]0 }
                    if (Test-Path -LiteralPath '/var/crash' -PathType Container) {
                        $stats = Get-DirStatsNoJunction -LiteralPath '/var/crash' -Checkpoint $cpState -Category 'crash-dumps'
                        Add-Candidate -Result $result -Category 'crash-dumps' -Path '/var/crash' -SizeBytes $stats.SizeBytes -FileCount $stats.FileCount
                    }
                    Complete-ScanCategory -State $cpState -Category 'crash-dumps'
                }
            }

            # thumbnail-cache: {cache}/thumbnails
            if (-not $catFilter -or $catFilter -contains 'thumbnail-cache') {
                Update-CategoryProgress -Pg $pg -Category 'thumbnail-cache'
                if (-not $resumeSkipped.ContainsKey('thumbnail-cache')) {
                    $result.Evaluated.Add(@{ name = 'thumbnail-cache'; risk = 'SAFE' })
                    if ($null -ne $cpState) { $cpState['FileCounter'] = [int64]0 }
                    $p = Join-Path $cache 'thumbnails'
                    if (Test-Path -LiteralPath $p -PathType Container) {
                        $stats = Get-DirStatsNoJunction -LiteralPath $p -Checkpoint $cpState -Category 'thumbnail-cache'
                        Add-Candidate -Result $result -Category 'thumbnail-cache' -Path $p -SizeBytes $stats.SizeBytes -FileCount $stats.FileCount
                    }
                    Complete-ScanCategory -State $cpState -Category 'thumbnail-cache'
                }
            }

            # recycle-bin (ASK): ~/.local/share/Trash -- report only
            if (-not $catFilter -or $catFilter -contains 'recycle-bin') {
                Update-CategoryProgress -Pg $pg -Category 'recycle-bin'
                if (-not $resumeSkipped.ContainsKey('recycle-bin')) {
                    $result.Evaluated.Add(@{ name = 'recycle-bin'; risk = 'ASK' })
                    if ($null -ne $cpState) { $cpState['FileCounter'] = [int64]0 }
                    $trash = Join-Path $env:HOME '.local/share/Trash'
                    if (Test-Path -LiteralPath $trash -PathType Container) {
                        $stats = Get-DirStatsNoJunction -LiteralPath $trash -Checkpoint $cpState -Category 'recycle-bin'
                        Add-Candidate -Result $result -Category 'recycle-bin' -Path $trash -SizeBytes $stats.SizeBytes -FileCount $stats.FileCount
                    }
                    Complete-ScanCategory -State $cpState -Category 'recycle-bin'
                }
            }
        }
    }

    return $result
}

# <end-classification>
# =====================================================================

# =====================================================================
# <begin-main>
# =====================================================================

# =====================================================================
# todo 8: multi-drive batch mode (-Drives D:,E:).
# Both -Drive and -Drives are NON-mandatory ($null default) so the custom
# presence / mutual-exclusion errors fire before PowerShell's own binding
# error. When -Drives is used, each drive is scanned by its OWN subprocess
# (no in-process scope isolation) - Windows -> powershell.exe, Linux/macOS
# -> pwsh, selected via platform.ps1's $script:IsWin.
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

if ($drivesGiven) {
    $driveList = @($Drives | ForEach-Object { $_ -split ',' } | Where-Object { $_ })
    if ($driveList.Count -eq 0) {
        Write-Error "No drives specified in -Drives"
        exit 1
    }

    # Combined summary dir: <OutDir>\multidrive-<timestamp>\drives.csv
    $multiRoot = Join-Path $OutDir ("multidrive-{0}" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
    New-Item -ItemType Directory -Force -Path $multiRoot | Out-Null

    # Subprocess executable + prefix args (todo 8 platform branch).
    $scanExe = Get-PowerShellExecutable
    $scanPfx = if ($script:IsWin) {
        @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $PSScriptRoot 'scan-drive.ps1'))
    } else {
        @('-NoProfile', '-File', (Join-Path $PSScriptRoot 'scan-drive.ps1'))
    }

    # Builds the argument list handed to one per-drive scan subprocess.
    function New-ScanSubprocessArgs {
        param([string]$DriveArg)
        $a = New-Object System.Collections.Generic.List[string]
        foreach ($p in $scanPfx) { [void]$a.Add($p) }
        [void]$a.Add('-Drive');  [void]$a.Add($DriveArg)
        [void]$a.Add('-OutDir'); [void]$a.Add($OutDir)
        if ($IncludeElevated.IsPresent) { [void]$a.Add('-IncludeElevated') }
        if ($Categories) { [void]$a.Add('-Categories'); [void]$a.Add((@($Categories | ForEach-Object { $_ -split ',' } | Where-Object { $_ }) -join ',')) }
        if ($Resume.IsPresent) { [void]$a.Add('-Resume') }
        return $a.ToArray()
    }

    # Resolves the per-drive run dir produced by the finished subprocess
    # (the NEWEST <Letter>-* dir under $OutDir for that drive).
    function Resolve-DriveRunDir {
        param([string]$DriveArg)
        $driveInfo = Resolve-FixedDrive -Drive $DriveArg
        if ($null -eq $driveInfo) { return $null }
        $prefix = $driveInfo.Id
        $dir = @(Get-ChildItem -LiteralPath $OutDir -Directory -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -like "$prefix-*" } |
                Sort-Object LastWriteTime -Descending | Select-Object -First 1)
        if ($dir.Count -eq 0) { return $null }
        return $dir[0].FullName
    }

    function Get-CandidateTotals {
        param([string]$RunDirArg)
        if ([string]::IsNullOrEmpty($RunDirArg)) { return @{ Count = 0; Bytes = 0L } }
        $csv = Join-Path $RunDirArg 'candidates.csv'
        if (-not (Test-Path -LiteralPath $csv -PathType Leaf)) { return @{ Count = 0; Bytes = 0L } }
        $count = 0; $bytes = 0L
        $lines = [System.IO.File]::ReadAllLines($csv)
        for ($i = 1; $i -lt $lines.Count; $i++) {
            $p = $lines[$i] -split '\|'
            if ($p.Count -lt 6) { continue }
            $count++
            $s = 0L; [void][long]::TryParse($p[3], [ref]$s); $bytes += $s
        }
        return @{ Count = $count; Bytes = $bytes }
    }

    # Builds one drives.csv row, records the aggregate failure flag.
    function Add-DriveScanResult {
        param([string]$DriveArg, [int]$ExitCode, [System.Collections.Generic.List[object]]$Results)
        $runDir = Resolve-DriveRunDir -DriveArg $DriveArg
        $totals = Get-CandidateTotals -RunDirArg $runDir
        if ($ExitCode -ne 0) { $script:anyFailed = $true }
        [void]$Results.Add([pscustomobject]@{
            Drive          = $DriveArg
            RunDir         = $(if ($runDir) { $runDir } else { '' })
            ExitCode       = $ExitCode
            Status         = $(if ($ExitCode -eq 0) { 'OK' } else { 'FAIL' })
            Candidates     = $totals.Count
            CandidateBytes = $totals.Bytes
        })
    }

    $results   = New-Object System.Collections.Generic.List[object]
    $script:anyFailed = $false

    if ($Parallel.IsPresent) {
        # ---- concurrent per-drive subprocesses --------------------------
        if ($script:IsPwsh7) {
            # pwsh 7: Start-Process -PassThru per drive, output redirected to
            # per-drive logs, then WaitForExit for all.
            $procs = New-Object System.Collections.Generic.List[object]
            $procDrives = New-Object System.Collections.Generic.List[string]
            foreach ($d in $driveList) {
                $argsArr   = New-ScanSubprocessArgs -DriveArg $d
                $outLog    = Join-Path $multiRoot ("{0}-scan.out.log" -f $(if ($script:IsWin) { $d.TrimEnd(':').ToUpperInvariant() } else { 'ROOT' }))
                $errLog    = Join-Path $multiRoot ("{0}-scan.err.log" -f $(if ($script:IsWin) { $d.TrimEnd(':').ToUpperInvariant() } else { 'ROOT' }))
                $argLine   = (($argsArr | ForEach-Object { if ($_ -match '[\s"]') { '"' + $_.Replace('"', '""') + '"' } else { $_ } }) -join ' ')
                [void]$procDrives.Add($d)
                [void]$procs.Add((Start-Process -FilePath $scanExe -ArgumentList $argLine -PassThru -WindowStyle Hidden -RedirectStandardOutput $outLog -RedirectStandardError $errLog))
            }
            for ($i = 0; $i -lt $procs.Count; $i++) {
                $procs[$i].WaitForExit()
                $procs[$i].Refresh()
                $code = $(if ($null -ne $procs[$i].ExitCode) { [int]$procs[$i].ExitCode } else { 1 })
                Add-DriveScanResult -DriveArg $procDrives[$i] -ExitCode $code -Results $results
            }
        } else {
            # PS 5.1: Start-Job per drive (one powershell process each); the
            # job echoes a SUBEXIT= marker so the parent can read the code.
            $jobs = New-Object System.Collections.Generic.List[object]
            $jobDrives = New-Object System.Collections.Generic.List[string]
            foreach ($d in $driveList) {
                $argsArr = New-ScanSubprocessArgs -DriveArg $d
                [void]$jobDrives.Add($d)
                [void]$jobs.Add((Start-Job -ScriptBlock {
                        param($exePath, $argList)
                        & $exePath @argList
                        Write-Output "SUBEXIT=$LASTEXITCODE"
                    } -ArgumentList $scanExe, $argsArr))
            }
            if ($jobs.Count -gt 0) { $null = Wait-Job -Job $jobs }
            for ($i = 0; $i -lt $jobs.Count; $i++) {
                $jobOut = @(Receive-Job -Job $jobs[$i])
                $joined = ($jobOut -join "`n")
                $code = if ($joined -match 'SUBEXIT=(\d+)') { [int]$matches[1] } else { 1 }
                foreach ($line in $jobOut) { if ($line) { Write-Output $line } }
                Add-DriveScanResult -DriveArg $jobDrives[$i] -ExitCode $code -Results $results
            }
            foreach ($j in $jobs) { Remove-Job -Job $j -Force }
        }
    } else {
        # ---- sequential per-drive subprocesses (default) ----------------
        foreach ($d in $driveList) {
            $argsArr = New-ScanSubprocessArgs -DriveArg $d
            & $scanExe @argsArr
            Add-DriveScanResult -DriveArg $d -ExitCode $LASTEXITCODE -Results $results
        }
    }

    # ---- combined summary: <multiRoot>\drives.csv -----------------------
    $csvLines = New-Object System.Collections.Generic.List[string]
    $csvLines.Add('Drive|RunDir|ExitCode|Status|Candidates|CandidateBytes')
    foreach ($r in $results) {
        $csvLines.Add(('{0}|{1}|{2}|{3}|{4}|{5}' -f $r.Drive, $r.RunDir, $r.ExitCode, $r.Status, $r.Candidates, $r.CandidateBytes))
    }
    $csvPath = Join-Path $multiRoot 'drives.csv'
    [System.IO.File]::WriteAllLines($csvPath, $csvLines.ToArray(), (New-Object System.Text.UTF8Encoding($false)))

    Write-Output "MULTI-DRIVE SCAN COMPLETE: $($results.Count) drive(s) scanned."
    foreach ($r in $results) {
        Write-Output ("  {0}: {1} (exit {2}) -> {3}" -f $r.Drive, $r.Status, $r.ExitCode, $(if ($r.RunDir) { $r.RunDir } else { '<no run dir>' }))
    }
    Write-Output "DRIVES CSV: $csvPath"
    if ($script:anyFailed) { exit 1 }
    exit 0
}

# ---- Drive validation -------------------------------------------------
$volume = Resolve-FixedDrive -Drive $Drive
if ($null -eq $volume) {
    Write-Error "Drive '$Drive' is not an available fixed local volume. Refusing to scan removable/network media."
    exit 1
}
$driveLetter = $volume.Id

# ---- User-profile scope ------------------------------------------------
# User-profile categories apply ONLY when the user profile lives on $Drive.
# On Linux/macOS there is a single '/' drive, so any '/' scan is the user drive.
$isUserDrive = if ($script:IsWin) {
    $env:USERPROFILE.StartsWith($Drive, [System.StringComparison]::OrdinalIgnoreCase)
} else {
    $Drive -eq '/'
}

# ---- Run directory + todo 4 checkpoint/resume ---------------------------
# A normal run creates a fresh timestamped run dir. With -Resume we instead
# reuse the NEWEST run dir under $OutDir that already holds a
# scan-checkpoint.json for this drive (a fresh dir would have nothing to
# resume from). Rows already written to candidates.csv by the interrupted run
# are preserved and merged with the resumed rows.
$checkpointPath = $null
$resumeState = $null

if ($Resume.IsPresent) {
    $cpRuns = @(Get-ChildItem -LiteralPath $OutDir -Directory -Filter "$driveLetter-*" -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending)
    foreach ($rd in $cpRuns) {
        $cand = Join-Path $rd.FullName 'scan-checkpoint.json'
        if (Test-Path -LiteralPath $cand -PathType Leaf) { $checkpointPath = $cand; $runDir = $rd.FullName; break }
    }
    if ($null -eq $checkpointPath -or -not (Test-Path -LiteralPath $checkpointPath -PathType Leaf)) {
        Write-Error "No checkpoint found for resume"
        exit 1
    }
    $cpJson = Get-Content -LiteralPath $checkpointPath -Raw | ConvertFrom-Json
    $resumeState = @{
        CompletedCategories = @($cpJson.completedCategories)
        CurrentCategory     = [string]$cpJson.currentCategory
        LastPath            = if ($null -eq $cpJson.lastPath) { '' } else { [string]$cpJson.lastPath }
    }
    Write-Output "RESUME: resuming from $checkpointPath ($($resumeState.CompletedCategories.Count) completed category/categories)"
} else {
    $runName = "$($driveLetter.ToUpperInvariant())-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    $runDir  = Join-Path $OutDir $runName
    New-Item -ItemType Directory -Force -Path $runDir | Out-Null
}

# Checkpoint state is ALWAYS active during a real scan (written, unused when
# not resuming). Write failures are swallowed by Write-ScanCheckpoint.
$checkpointState = New-ScanCheckpointState -Path (Join-Path $runDir 'scan-checkpoint.json') -Drive $Drive

# ---- Pre-flight (exactly 3 key=value lines, parseable by verify-report) --
$baselineFree = [int64]$volume.Free
$totalBytes   = [int64]$volume.Size
$processes    = @(Get-Process chrome, msedge, WeChat, WeChatApp, Weixin, WeGame, steam, pip, npm -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty Name)
$preflight = @(
    "BASELINE_FREE_BYTES=$baselineFree",
    "TOTAL_BYTES=$totalBytes",
    "PROCESSES=$($processes -join ',')"
)
[System.IO.File]::WriteAllLines((Join-Path $runDir 'preflight.txt'), $preflight, (New-Object System.Text.UTF8Encoding($false)))

# ---- Classify ----------------------------------------------------------
Write-Output "SCANNING $Drive (isUserDrive=$isUserDrive, includeElevated=$($IncludeElevated.IsPresent)) -> $runDir"
$scan = Get-JunkCandidates -RootPath $volume.Root -IsUserDrive $isUserDrive -IncludeElevated $IncludeElevated.IsPresent -Categories $Categories -Checkpoint $checkpointState -ResumeState $resumeState

# ---- candidates.csv (header `Category|Risk|Path|SizeBytes|FileCount|Action`) --
# On -Resume, rows already in the run dir's candidates.csv are preserved
# (resumed categories are not re-evaluated, so their rows must survive).
$existingRows = New-Object System.Collections.Generic.List[object]
if (Test-Path -LiteralPath (Join-Path $runDir 'candidates.csv') -PathType Leaf) {
    $oldLines = @(Get-Content -LiteralPath (Join-Path $runDir 'candidates.csv'))
    if ($oldLines.Count -gt 0) { $oldLines = @($oldLines | Select-Object -Skip 1) }
    foreach ($line in $oldLines) {
        $cols = @($line -split '\|')
        if ($cols.Count -ne 6) { continue }
        $existingRows.Add([pscustomobject]@{
            Category   = $cols[0]
            Risk       = $cols[1]
            Path       = $cols[2]
            SizeBytes  = [int64]$cols[3]
            FileCount  = [int64]$cols[4]
            Action     = $cols[5]
        })
    }
}
$csv = New-Object System.Collections.Generic.List[string]
$csv.Add('Category|Risk|Path|SizeBytes|FileCount|Action')
$combinedRows = New-Object System.Collections.Generic.List[object]
$seenKey = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
foreach ($row in $existingRows) {
    [void]$seenKey.Add("$($row.Category)|$($row.Path)")
    $combinedRows.Add($row)
    $csv.Add(('{0}|{1}|{2}|{3}|{4}|{5}' -f $row.Category, $row.Risk, $row.Path, [int64]$row.SizeBytes, [int64]$row.FileCount, $row.Action))
}
foreach ($row in $scan.Rows) {
    if ($seenKey.Add("$($row.Category)|$($row.Path)")) {
        $combinedRows.Add($row)
        $csv.Add(('{0}|{1}|{2}|{3}|{4}|{5}' -f $row.Category, $row.Risk, $row.Path, [int64]$row.SizeBytes, [int64]$row.FileCount, $row.Action))
    }
}
[System.IO.File]::WriteAllLines((Join-Path $runDir 'candidates.csv'), $csv, (New-Object System.Text.UTF8Encoding($false)))

# ---- scan-report.json (per category: name, risk, candidates array) ------
# Merges the preserved rows with the resumed evaluation so every category that
# produced rows in this or the interrupted run still appears in the report.
$riskByCat = @{}
foreach ($e in $scan.Evaluated) { $riskByCat[[string]$e.name] = [string]$e.risk }
foreach ($r in $existingRows) { if (-not $riskByCat.ContainsKey([string]$r.Category)) { $riskByCat[[string]$r.Category] = [string]$r.Risk } }
$report = [ordered]@{}
foreach ($catName in @(@($combinedRows | ForEach-Object { $_.Category } | Select-Object -Unique) + @($scan.Evaluated | ForEach-Object { $_.name } | Select-Object -Unique) | Select-Object -Unique)) {
    $catRows = @($combinedRows | Where-Object { $_.Category -eq $catName })
    $report[$catName] = @{
        name       = $catName
        risk       = $riskByCat[$catName]
        candidates = @($catRows)
    }
}
# Preserve report entries for categories not re-evaluated in a resumed run
# (resume skips completed categories, so their entries would otherwise vanish).
if (Test-Path -LiteralPath (Join-Path $runDir 'scan-report.json') -PathType Leaf) {
    $oldReport = Get-Content -LiteralPath (Join-Path $runDir 'scan-report.json') -Raw | ConvertFrom-Json
    foreach ($p in @($oldReport.PSObject.Properties)) {
        if (-not $report.Contains($p.Name)) {
            $report[$p.Name] = @{
                name       = $p.Name
                risk       = $p.Value.risk
                candidates = @($p.Value.candidates)
            }
        }
    }
}
$json = ConvertTo-Json -InputObject $report -Depth 6
[System.IO.File]::WriteAllText((Join-Path $runDir 'scan-report.json'), $json, (New-Object System.Text.UTF8Encoding($false)))

Write-Output "SCAN COMPLETE: $($combinedRows.Count) candidate(s) across $($report.Count) category/categories."
Write-Output "OUTPUT: $runDir"
exit 0

# <end-main>
# =====================================================================
