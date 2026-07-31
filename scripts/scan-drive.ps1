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

param(
    [Parameter(Mandatory)][string]$Drive,                       # e.g. 'D:'
    [string]$OutDir = "$env:USERPROFILE\Desktop\.omo\evidence\rubbish-cleaner",
    [switch]$IncludeElevated,                                   # enable elevated-system (report-only)
    [string[]]$Categories                                       # filter; empty = all applicable
)

# ---- dot-source the safety function library (todo 3 deliverable) ----
. (Join-Path $PSScriptRoot 'lib\rubbish-core.ps1')
# ---- dot-source the cross-platform detection layer (todo 2 deliverable) ----
. (Join-Path $PSScriptRoot 'lib\platform.ps1')

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

# Junction-safe recursive size + file count for a directory.
# Iterative (stack-based) walk; reparse-point children are skipped WITHOUT
# descending, so junctions/symlinks are never followed.
function Get-DirStatsNoJunction {
    param([string]$LiteralPath)

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
        [string[]]$Categories
    )

    $root = $RootPath.TrimEnd('\') + '\'
    $result = @{
        Rows      = (New-Object System.Collections.Generic.List[object])
        Evaluated = (New-Object System.Collections.Generic.List[object])
    }
    # Normalize: accept both `-Categories a,b` (array) and `-Categories "a,b"`
    # (single quoted string) by splitting every element on commas.
    $catFilter = @($Categories | ForEach-Object { $_ -split ',' } | Where-Object { $_ })

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
        $result.Evaluated.Add(@{ name = 'root-temps'; risk = 'SAFE' })
        # NTFS is case-insensitive, so 'Temp'/'tmp'/'temp' may resolve to the
        # same directory; dedupe on the actual (resolved) path.
        $seen = New-Object System.Collections.Generic.HashSet[string] ([System.StringComparer]::OrdinalIgnoreCase)
        foreach ($name in @('Temp', 'tmp', 'temp')) {
            $dir = Join-Path $root $name
            if (-not (Test-Path -LiteralPath $dir -PathType Container)) { continue }
            $actual = (Get-Item -LiteralPath $dir -Force).FullName
            if (-not $seen.Add($actual)) { continue }
            foreach ($f in Get-ChildItem -LiteralPath $actual -Force -File -ErrorAction SilentlyContinue) {
                if ($f.LastWriteTime -lt $cutoff) {
                    Add-Candidate -Result $result -Category 'root-temps' -Path $f.FullName -SizeBytes ([int64]$f.Length) -FileCount 1
                }
            }
        }
    }

    # ----------------------------------------------------------------
    # root-logs (SAFE): <Drive>\*.log, <Drive>\*.tmp, *_install*.log at
    # drive root only (no recursion).
    # ----------------------------------------------------------------
    if (-not $catFilter -or $catFilter -contains 'root-logs') {
        $result.Evaluated.Add(@{ name = 'root-logs'; risk = 'SAFE' })
        foreach ($f in Get-ChildItem -LiteralPath $root -Force -File -ErrorAction SilentlyContinue) {
            if ($f.Extension -in @('.log', '.tmp') -or $f.Name -like '*_install*.log') {
                Add-Candidate -Result $result -Category 'root-logs' -Path $f.FullName -SizeBytes ([int64]$f.Length) -FileCount 1
            }
        }
    }

    # ----------------------------------------------------------------
    # duplicate-archives (ASK): <Drive>\*.zip|rar|7z where a same-name
    # extracted folder exists next to it (archive only, never the folder).
    # ----------------------------------------------------------------
    if (-not $catFilter -or $catFilter -contains 'duplicate-archives') {
        $result.Evaluated.Add(@{ name = 'duplicate-archives'; risk = 'ASK' })
        foreach ($f in Get-ChildItem -LiteralPath $root -Force -File -ErrorAction SilentlyContinue) {
            if ($f.Extension -notin @('.zip', '.rar', '.7z')) { continue }
            $base = [System.IO.Path]::GetFileNameWithoutExtension($f.Name)
            if (Test-Path -LiteralPath (Join-Path $root $base) -PathType Container) {
                Add-Candidate -Result $result -Category 'duplicate-archives' -Path $f.FullName -SizeBytes ([int64]$f.Length) -FileCount 1
            }
        }
    }

    # ----------------------------------------------------------------
    # empty-dirs (SAFE): top-level dirs passing the junction-aware
    # Test-DirEmpty; skip $RECYCLE.BIN, System Volume Information,
    # .claude; never bare -Recurse; junctions never followed.
    # ----------------------------------------------------------------
    if (-not $catFilter -or $catFilter -contains 'empty-dirs') {
        $result.Evaluated.Add(@{ name = 'empty-dirs'; risk = 'SAFE' })
        $skip = @('$RECYCLE.BIN', 'SYSTEM VOLUME INFORMATION', '.CLAUDE')
        foreach ($d in Get-ChildItem -LiteralPath $root -Force -Directory -ErrorAction SilentlyContinue) {
            if ($skip -contains $d.Name.ToUpperInvariant()) { continue }
            if (Test-IsJunction -Path $d.FullName) { continue }
            if (Test-DirEmpty -Path $d.FullName -ErrorAction SilentlyContinue) {
                Add-Candidate -Result $result -Category 'empty-dirs' -Path $d.FullName -SizeBytes 0 -FileCount 0
            }
        }
    }

    # ----------------------------------------------------------------
    # recycle-bin (ASK): <Drive>\$RECYCLE.BIN content size; never
    # auto-deleted (report for user approval only).
    # ----------------------------------------------------------------
    if (-not $catFilter -or $catFilter -contains 'recycle-bin') {
        $result.Evaluated.Add(@{ name = 'recycle-bin'; risk = 'ASK' })
        $rb = Join-Path $root '$RECYCLE.BIN'
        if (Test-Path -LiteralPath $rb -PathType Container) {
            $stats = Get-DirStatsNoJunction -LiteralPath $rb
            Add-Candidate -Result $result -Category 'recycle-bin' -Path $rb -SizeBytes $stats.SizeBytes -FileCount $stats.FileCount
        }
    }

    # ----------------------------------------------------------------
    # root-suspicious (CAUTION): <Drive>\*.dll and *.exe whose basename
    # (without extension) matches NEITHER any top-level dir name on the
    # drive NOR any subdir under <Drive>\Program Files (x86) -- D-drive
    # precedent dinput8.dll / sdhdship.exe. Action=quarantine.
    # ----------------------------------------------------------------
    if (-not $catFilter -or $catFilter -contains 'root-suspicious') {
        $result.Evaluated.Add(@{ name = 'root-suspicious'; risk = 'CAUTION' })
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
        foreach ($f in Get-ChildItem -LiteralPath $root -Force -File -ErrorAction SilentlyContinue) {
            if ($f.Extension -notin @('.dll', '.exe')) { continue }
            $base = [System.IO.Path]::GetFileNameWithoutExtension($f.Name)
            if ($excluded.Contains($base)) { continue }
            Add-Candidate -Result $result -Category 'root-suspicious' -Path $f.FullName -SizeBytes ([int64]$f.Length) -FileCount 1
        }
    }

    # ----------------------------------------------------------------
    # app-caches (SAFE): per-app-path-map templates resolved against
    # $root, existence-checked.
    # ----------------------------------------------------------------
    if (-not $catFilter -or $catFilter -contains 'app-caches') {
        $result.Evaluated.Add(@{ name = 'app-caches'; risk = 'SAFE' })

        # {D}\anaconda3\pkgs\cache
        $p = Join-Path $root 'anaconda3\pkgs\cache'
        if (Test-Path -LiteralPath $p -PathType Container) {
            $stats = Get-DirStatsNoJunction -LiteralPath $p
            Add-Candidate -Result $result -Category 'app-caches' -Path $p -SizeBytes $stats.SizeBytes -FileCount $stats.FileCount
        }

        # {D}\Wegame\*\tiny_cache and {D}\Wegame\*\cache
        foreach ($sub in @('tiny_cache', 'cache')) {
            $wg = Join-Path $root 'Wegame'
            if (Test-Path -LiteralPath $wg -PathType Container) {
                foreach ($d in Get-ChildItem -LiteralPath $wg -Force -Directory -ErrorAction SilentlyContinue) {
                    $p = Join-Path $d.FullName $sub
                    if (Test-Path -LiteralPath $p -PathType Container) {
                        $stats = Get-DirStatsNoJunction -LiteralPath $p
                        Add-Candidate -Result $result -Category 'app-caches' -Path $p -SizeBytes $stats.SizeBytes -FileCount $stats.FileCount
                    }
                }
            }
        }

        # {D}\WeiXin\xwechat_files\**\cache (junction-safe recursive)
        $wx = Join-Path $root 'WeiXin\xwechat_files'
        if (Test-Path -LiteralPath $wx -PathType Container) {
            foreach ($p in @(Find-DirsNamed -LiteralRoot $wx -Name 'cache')) {
                $stats = Get-DirStatsNoJunction -LiteralPath $p
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
            $stats = Get-DirStatsNoJunction -LiteralPath $p
            Add-Candidate -Result $result -Category 'app-caches' -Path $p -SizeBytes $stats.SizeBytes -FileCount $stats.FileCount
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
            $result.Evaluated.Add(@{ name = 'browser-caches'; risk = 'SAFE' })
            foreach ($browser in @('Google\Chrome', 'Microsoft\Edge')) {
                $default = Join-Path $env:LOCALAPPDATA "$browser\User Data\Default"
                foreach ($sub in @('Cache', 'Code Cache', 'GPUCache', 'Crashpad\reports')) {
                    $p = Join-Path $default $sub
                    if (Test-Path -LiteralPath $p -PathType Container) {
                        $stats = Get-DirStatsNoJunction -LiteralPath $p
                        Add-Candidate -Result $result -Category 'browser-caches' -Path $p -SizeBytes $stats.SizeBytes -FileCount $stats.FileCount
                    }
                }
            }
        }

        # gpu-shader: NVIDIA\DXCache + NVIDIA\GLCache, D3DSCache
        if (-not $catFilter -or $catFilter -contains 'gpu-shader') {
            $result.Evaluated.Add(@{ name = 'gpu-shader'; risk = 'SAFE' })
            foreach ($p in @(
                    (Join-Path $env:LOCALAPPDATA 'NVIDIA\DXCache'),
                    (Join-Path $env:LOCALAPPDATA 'NVIDIA\GLCache'),
                    (Join-Path $env:LOCALAPPDATA 'D3DSCache'))) {
                if (Test-Path -LiteralPath $p -PathType Container) {
                    $stats = Get-DirStatsNoJunction -LiteralPath $p
                    Add-Candidate -Result $result -Category 'gpu-shader' -Path $p -SizeBytes $stats.SizeBytes -FileCount $stats.FileCount
                }
            }
        }

        # dev-caches: pip\cache, npm-cache, .cache\{torch,huggingface,
        # opencode,codex-runtimes,pkg}
        if (-not $catFilter -or $catFilter -contains 'dev-caches') {
            $result.Evaluated.Add(@{ name = 'dev-caches'; risk = 'SAFE' })
            foreach ($p in @(
                    (Join-Path $env:LOCALAPPDATA 'pip\cache'),
                    (Join-Path $env:LOCALAPPDATA 'npm-cache'))) {
                if (Test-Path -LiteralPath $p -PathType Container) {
                    $stats = Get-DirStatsNoJunction -LiteralPath $p
                    Add-Candidate -Result $result -Category 'dev-caches' -Path $p -SizeBytes $stats.SizeBytes -FileCount $stats.FileCount
                }
            }
            foreach ($sub in @('torch', 'huggingface', 'opencode', 'codex-runtimes', 'pkg')) {
                $p = Join-Path $env:USERPROFILE (Join-Path '.cache' $sub)
                if (Test-Path -LiteralPath $p -PathType Container) {
                    $stats = Get-DirStatsNoJunction -LiteralPath $p
                    Add-Candidate -Result $result -Category 'dev-caches' -Path $p -SizeBytes $stats.SizeBytes -FileCount $stats.FileCount
                }
            }
        }

        # ide-caches: JetBrains *\caches + *\log (+ Toolbox cache/logs);
        # Zotero cache2/startupCache/shader-cache; Jedi *.pkl
        if (-not $catFilter -or $catFilter -contains 'ide-caches') {
            $result.Evaluated.Add(@{ name = 'ide-caches'; risk = 'SAFE' })
            $jb = Join-Path $env:LOCALAPPDATA 'JetBrains'
            if (Test-Path -LiteralPath $jb -PathType Container) {
                foreach ($d in Get-ChildItem -LiteralPath $jb -Force -Directory -ErrorAction SilentlyContinue) {
                    $isToolbox = $d.Name -in @('Toolbox', 'Toolbox-Dev')
                    foreach ($sub in @('caches', 'log') + $(if ($isToolbox) { @('cache', 'logs') } else { @() })) {
                        $p = Join-Path $d.FullName $sub
                        if (Test-Path -LiteralPath $p -PathType Container) {
                            $stats = Get-DirStatsNoJunction -LiteralPath $p
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
                            $stats = Get-DirStatsNoJunction -LiteralPath $p
                            Add-Candidate -Result $result -Category 'ide-caches' -Path $p -SizeBytes $stats.SizeBytes -FileCount $stats.FileCount
                        }
                    }
                }
            }
            $jedi = Join-Path $env:LOCALAPPDATA 'Jedi\Jedi'
            if (Test-Path -LiteralPath $jedi -PathType Container) {
                foreach ($d in Get-ChildItem -LiteralPath $jedi -Force -Directory -ErrorAction SilentlyContinue) {
                    foreach ($f in Get-ChildItem -LiteralPath $d.FullName -Force -File -Filter '*.pkl' -ErrorAction SilentlyContinue) {
                        Add-Candidate -Result $result -Category 'ide-caches' -Path $f.FullName -SizeBytes ([int64]$f.Length) -FileCount 1
                    }
                }
            }
        }

        # crash-dumps: %LOCALAPPDATA%\CrashDumps + top-level Crashpad dirs
        if (-not $catFilter -or $catFilter -contains 'crash-dumps') {
            $result.Evaluated.Add(@{ name = 'crash-dumps'; risk = 'SAFE' })
            $p = Join-Path $env:LOCALAPPDATA 'CrashDumps'
            if (Test-Path -LiteralPath $p -PathType Container) {
                $stats = Get-DirStatsNoJunction -LiteralPath $p
                Add-Candidate -Result $result -Category 'crash-dumps' -Path $p -SizeBytes $stats.SizeBytes -FileCount $stats.FileCount
            }
            foreach ($d in Get-ChildItem -LiteralPath $env:LOCALAPPDATA -Force -Directory -ErrorAction SilentlyContinue) {
                if ($d.Name -eq 'Crashpad' -and -not (Test-IsJunction -Path $d.FullName)) {
                    $stats = Get-DirStatsNoJunction -LiteralPath $d.FullName
                    Add-Candidate -Result $result -Category 'crash-dumps' -Path $d.FullName -SizeBytes $stats.SizeBytes -FileCount $stats.FileCount
                }
            }
        }

        # thumbnail-cache: Explorer thumbcache_*.db + iconcache_*.db
        if (-not $catFilter -or $catFilter -contains 'thumbnail-cache') {
            $result.Evaluated.Add(@{ name = 'thumbnail-cache'; risk = 'SAFE' })
            $explorer = Join-Path $env:LOCALAPPDATA 'Microsoft\Windows\Explorer'
            if (Test-Path -LiteralPath $explorer -PathType Container) {
                foreach ($f in Get-ChildItem -LiteralPath $explorer -Force -File -ErrorAction SilentlyContinue) {
                    if ($f.Name -like 'thumbcache_*.db' -or $f.Name -like 'iconcache_*.db') {
                        Add-Candidate -Result $result -Category 'thumbnail-cache' -Path $f.FullName -SizeBytes ([int64]$f.Length) -FileCount 1
                    }
                }
            }
        }

        # user-temp: %LOCALAPPDATA%\Temp top-level files older than 7 days
        # (never subdirs)
        if (-not $catFilter -or $catFilter -contains 'user-temp') {
            $result.Evaluated.Add(@{ name = 'user-temp'; risk = 'SAFE' })
            $p = Join-Path $env:LOCALAPPDATA 'Temp'
            if (Test-Path -LiteralPath $p -PathType Container) {
                foreach ($f in Get-ChildItem -LiteralPath $p -Force -File -ErrorAction SilentlyContinue) {
                    if ($f.LastWriteTime -lt $cutoff) {
                        Add-Candidate -Result $result -Category 'user-temp' -Path $f.FullName -SizeBytes ([int64]$f.Length) -FileCount 1
                    }
                }
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
            $result.Evaluated.Add(@{ name = 'elevated-system'; risk = 'ELEVATED' })

            # Windows\Temp top-level files older than 7 days
            $p = Join-Path $root 'Windows\Temp'
            if (Test-Path -LiteralPath $p -PathType Container) {
                foreach ($f in Get-ChildItem -LiteralPath $p -Force -File -ErrorAction SilentlyContinue) {
                    if ($f.LastWriteTime -lt $cutoff) {
                        Add-Candidate -Result $result -Category 'elevated-system' -Path $f.FullName -SizeBytes ([int64]$f.Length) -FileCount 1
                    }
                }
            }

            # Prefetch *.pf only -- never Layout.ini
            $p = Join-Path $root 'Windows\Prefetch'
            if (Test-Path -LiteralPath $p -PathType Container) {
                foreach ($f in Get-ChildItem -LiteralPath $p -Force -File -Filter '*.pf' -ErrorAction SilentlyContinue) {
                    if ($f.Name -eq 'Layout.ini') { continue }
                    Add-Candidate -Result $result -Category 'elevated-system' -Path $f.FullName -SizeBytes ([int64]$f.Length) -FileCount 1
                }
            }

            # SoftwareDistribution -- guarded (report the dir, never touch)
            $p = Join-Path $root 'Windows\SoftwareDistribution'
            if (Test-Path -LiteralPath $p -PathType Container) {
                $stats = Get-DirStatsNoJunction -LiteralPath $p
                Add-Candidate -Result $result -Category 'elevated-system' -Path $p -SizeBytes $stats.SizeBytes -FileCount $stats.FileCount
            }

            # WindowsUpdate *.etl older than 7 days
            $p = Join-Path $root 'Windows\Logs\WindowsUpdate'
            if (Test-Path -LiteralPath $p -PathType Container) {
                foreach ($f in Get-ChildItem -LiteralPath $p -Force -File -Filter '*.etl' -ErrorAction SilentlyContinue) {
                    if ($f.LastWriteTime -lt $cutoff) {
                        Add-Candidate -Result $result -Category 'elevated-system' -Path $f.FullName -SizeBytes ([int64]$f.Length) -FileCount 1
                    }
                }
            }

            # CBS CbsPersist_*.cab
            $p = Join-Path $root 'Windows\Logs\CBS'
            if (Test-Path -LiteralPath $p -PathType Container) {
                foreach ($f in Get-ChildItem -LiteralPath $p -Force -File -Filter 'CbsPersist_*.cab' -ErrorAction SilentlyContinue) {
                    Add-Candidate -Result $result -Category 'elevated-system' -Path $f.FullName -SizeBytes ([int64]$f.Length) -FileCount 1
                }
            }

            # DISM /StartComponentCleanup (no /ResetBase) -- executed later by
            # the elevated clean-drive batch; report-only marker row here.
            Add-Candidate -Result $result -Category 'elevated-system' -Path 'DISM StartComponentCleanup (no /ResetBase)' -SizeBytes 0 -FileCount 0
        }
    }

    # ----------------------------------------------------------------
    # Linux/macOS equivalents (todo 2): SAME category ids as the Windows
    # branches above -- only the path templates differ, resolved via
    # Get-UserCacheDir / Get-SystemTempDir from lib/platform.ps1.
    # $IsUserDrive on non-Windows = ($Drive -eq '/'), so the user-profile
    # categories below run whenever the single '/' drive is scanned.
    # ----------------------------------------------------------------
    if (-not $script:IsWindows) {
        $cache = Get-UserCacheDir
        $tmp = Get-SystemTempDir

        # root-temps (SAFE): {temp}/* top-level files older than 7 days
        if (-not $catFilter -or $catFilter -contains 'root-temps') {
            $result.Evaluated.Add(@{ name = 'root-temps'; risk = 'SAFE' })
            if (Test-Path -LiteralPath $tmp -PathType Container) {
                foreach ($f in Get-ChildItem -LiteralPath $tmp -Force -File -ErrorAction SilentlyContinue) {
                    if ($f.LastWriteTime -lt $cutoff) {
                        Add-Candidate -Result $result -Category 'root-temps' -Path $f.FullName -SizeBytes ([int64]$f.Length) -FileCount 1
                    }
                }
            }
        }

        if ($IsUserDrive) {

            # dev-caches: {cache}/{pip,npm,torch,huggingface,opencode,codex-runtimes}
            if (-not $catFilter -or $catFilter -contains 'dev-caches') {
                $result.Evaluated.Add(@{ name = 'dev-caches'; risk = 'SAFE' })
                foreach ($sub in @('pip', 'npm', 'torch', 'huggingface', 'opencode', 'codex-runtimes')) {
                    $p = Join-Path $cache $sub
                    if (Test-Path -LiteralPath $p -PathType Container) {
                        $stats = Get-DirStatsNoJunction -LiteralPath $p
                        Add-Candidate -Result $result -Category 'dev-caches' -Path $p -SizeBytes $stats.SizeBytes -FileCount $stats.FileCount
                    }
                }
            }

            # user-temp (SAFE): {cache}/* top-level files older than 7 days
            if (-not $catFilter -or $catFilter -contains 'user-temp') {
                $result.Evaluated.Add(@{ name = 'user-temp'; risk = 'SAFE' })
                foreach ($f in Get-ChildItem -LiteralPath $cache -Force -File -ErrorAction SilentlyContinue) {
                    if ($f.LastWriteTime -lt $cutoff) {
                        Add-Candidate -Result $result -Category 'user-temp' -Path $f.FullName -SizeBytes ([int64]$f.Length) -FileCount 1
                    }
                }
            }

            # browser-caches: Chrome/Edge Default\{Cache, Code Cache, GPUCache};
            # Firefox {cache}/mozilla/firefox/*/cache2/
            if (-not $catFilter -or $catFilter -contains 'browser-caches') {
                $result.Evaluated.Add(@{ name = 'browser-caches'; risk = 'SAFE' })
                foreach ($browser in @('google-chrome', 'microsoft-edge')) {
                    $default = Join-Path $cache "$browser/Default"
                    foreach ($sub in @('Cache', 'Code Cache', 'GPUCache')) {
                        $p = Join-Path $default $sub
                        if (Test-Path -LiteralPath $p -PathType Container) {
                            $stats = Get-DirStatsNoJunction -LiteralPath $p
                            Add-Candidate -Result $result -Category 'browser-caches' -Path $p -SizeBytes $stats.SizeBytes -FileCount $stats.FileCount
                        }
                    }
                }
                $ff = Join-Path $cache 'mozilla/firefox'
                if (Test-Path -LiteralPath $ff -PathType Container) {
                    foreach ($d in Get-ChildItem -LiteralPath $ff -Force -Directory -ErrorAction SilentlyContinue) {
                        $c2 = Join-Path $d.FullName 'cache2'
                        if (Test-Path -LiteralPath $c2 -PathType Container) {
                            $stats = Get-DirStatsNoJunction -LiteralPath $c2
                            Add-Candidate -Result $result -Category 'browser-caches' -Path $c2 -SizeBytes $stats.SizeBytes -FileCount $stats.FileCount
                        }
                    }
                }
            }

            # ide-caches: JetBrains */{caches,log}; VS Code ~/.config/Code/
            # {Cache, CachedData, logs}; Zotero {cache2, startupCache, shader-cache}
            if (-not $catFilter -or $catFilter -contains 'ide-caches') {
                $result.Evaluated.Add(@{ name = 'ide-caches'; risk = 'SAFE' })
                $jb = Join-Path $cache 'JetBrains'
                if (Test-Path -LiteralPath $jb -PathType Container) {
                    foreach ($d in Get-ChildItem -LiteralPath $jb -Force -Directory -ErrorAction SilentlyContinue) {
                        foreach ($sub in @('caches', 'log')) {
                            $p = Join-Path $d.FullName $sub
                            if (Test-Path -LiteralPath $p -PathType Container) {
                                $stats = Get-DirStatsNoJunction -LiteralPath $p
                                Add-Candidate -Result $result -Category 'ide-caches' -Path $p -SizeBytes $stats.SizeBytes -FileCount $stats.FileCount
                            }
                        }
                    }
                }
                $vsc = Join-Path $env:HOME '.config/Code'
                foreach ($sub in @('Cache', 'CachedData', 'logs')) {
                    $p = Join-Path $vsc $sub
                    if (Test-Path -LiteralPath $p -PathType Container) {
                        $stats = Get-DirStatsNoJunction -LiteralPath $p
                        Add-Candidate -Result $result -Category 'ide-caches' -Path $p -SizeBytes $stats.SizeBytes -FileCount $stats.FileCount
                    }
                }
                $zo = Join-Path $cache 'zotero'
                foreach ($sub in @('cache2', 'startupCache', 'shader-cache')) {
                    $p = Join-Path $zo $sub
                    if (Test-Path -LiteralPath $p -PathType Container) {
                        $stats = Get-DirStatsNoJunction -LiteralPath $p
                        Add-Candidate -Result $result -Category 'ide-caches' -Path $p -SizeBytes $stats.SizeBytes -FileCount $stats.FileCount
                    }
                }
            }

            # crash-dumps: /var/crash (apport crash reports)
            if (-not $catFilter -or $catFilter -contains 'crash-dumps') {
                $result.Evaluated.Add(@{ name = 'crash-dumps'; risk = 'SAFE' })
                if (Test-Path -LiteralPath '/var/crash' -PathType Container) {
                    $stats = Get-DirStatsNoJunction -LiteralPath '/var/crash'
                    Add-Candidate -Result $result -Category 'crash-dumps' -Path '/var/crash' -SizeBytes $stats.SizeBytes -FileCount $stats.FileCount
                }
            }

            # thumbnail-cache: {cache}/thumbnails
            if (-not $catFilter -or $catFilter -contains 'thumbnail-cache') {
                $result.Evaluated.Add(@{ name = 'thumbnail-cache'; risk = 'SAFE' })
                $p = Join-Path $cache 'thumbnails'
                if (Test-Path -LiteralPath $p -PathType Container) {
                    $stats = Get-DirStatsNoJunction -LiteralPath $p
                    Add-Candidate -Result $result -Category 'thumbnail-cache' -Path $p -SizeBytes $stats.SizeBytes -FileCount $stats.FileCount
                }
            }

            # recycle-bin (ASK): ~/.local/share/Trash -- report only
            if (-not $catFilter -or $catFilter -contains 'recycle-bin') {
                $result.Evaluated.Add(@{ name = 'recycle-bin'; risk = 'ASK' })
                $trash = Join-Path $env:HOME '.local/share/Trash'
                if (Test-Path -LiteralPath $trash -PathType Container) {
                    $stats = Get-DirStatsNoJunction -LiteralPath $trash
                    Add-Candidate -Result $result -Category 'recycle-bin' -Path $trash -SizeBytes $stats.SizeBytes -FileCount $stats.FileCount
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

# ---- Drive validation -------------------------------------------------
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
    Write-Error "Drive '$Drive' is not a fixed local volume (DriveType='$($vol.DriveType)'). Refusing to scan removable/network media."
    exit 1
}

# ---- User-profile scope ------------------------------------------------
# User-profile categories apply ONLY when the user profile lives on $Drive.
# On Linux/macOS there is a single '/' drive, so any '/' scan is the user drive.
$isUserDrive = if ($script:IsWindows) {
    $env:USERPROFILE.StartsWith($Drive, [System.StringComparison]::OrdinalIgnoreCase)
} else {
    $Drive -eq '/'
}

# ---- Run directory -----------------------------------------------------
$runName = "$($driveLetter.ToUpperInvariant())-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
$runDir = Join-Path $OutDir $runName
New-Item -ItemType Directory -Force -Path $runDir | Out-Null

# ---- Pre-flight (exactly 3 key=value lines, parseable by verify-report) --
$baselineFree = (Get-Volume -DriveLetter $driveLetter).SizeRemaining
$totalBytes   = (Get-Volume -DriveLetter $driveLetter).Size
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
$scan = Get-JunkCandidates -RootPath "$Drive\" -IsUserDrive $isUserDrive -IncludeElevated $IncludeElevated.IsPresent -Categories $Categories

# ---- candidates.csv (header `Category|Risk|Path|SizeBytes|FileCount|Action`) --
$csv = New-Object System.Collections.Generic.List[string]
$csv.Add('Category|Risk|Path|SizeBytes|FileCount|Action')
foreach ($row in $scan.Rows) {
    $csv.Add(('{0}|{1}|{2}|{3}|{4}|{5}' -f $row.Category, $row.Risk, $row.Path, [int64]$row.SizeBytes, [int64]$row.FileCount, $row.Action))
}
[System.IO.File]::WriteAllLines((Join-Path $runDir 'candidates.csv'), $csv, (New-Object System.Text.UTF8Encoding($false)))

# ---- scan-report.json (per category: name, risk, candidates array) ------
$report = [ordered]@{}
foreach ($cat in $scan.Evaluated) {
    $rows = @($scan.Rows | Where-Object { $_.Category -eq $cat.name })
    $report[$cat.name] = @{
        name       = $cat.name
        risk       = $cat.risk
        candidates = @($rows)
    }
}
$json = ConvertTo-Json -InputObject $report -Depth 6
[System.IO.File]::WriteAllText((Join-Path $runDir 'scan-report.json'), $json, (New-Object System.Text.UTF8Encoding($false)))

Write-Output "SCAN COMPLETE: $($scan.Rows.Count) candidate(s) across $($scan.Evaluated.Count) category/categories."
Write-Output "OUTPUT: $runDir"
exit 0

# <end-main>
# =====================================================================
