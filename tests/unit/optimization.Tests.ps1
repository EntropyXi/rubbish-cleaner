# optimization.Tests.ps1 - Pester 5 unit suite covering the todo-12 feature set.
#
# One Describe per sandbox suite in tests/sandbox/run-sandbox-tests.ps1 (the
# same five features, mirrored for the Pester branch of the dual-mode runner):
#   InvokeParallelForEach - Invoke-ParallelForEach (scripts/lib/rubbish-core.ps1)
#   CheckpointResume      - scan checkpoint production + partial resume + the
#                           -Resume skip message (scan-drive.ps1)
#   PlatformDetection     - scripts/lib/platform.ps1 detection layer
#   ScheduleParams        - scripts/schedule.ps1 List/Register + policy JSONs
#   MultiDrive            - scan-drive.ps1 -Drives batch mode
#
# Pester 5 syntax ONLY (BeforeAll / It / Should -Be / -BeTrue / -BeGreaterThan /
# Set-ItResult); no Pester-3-only patterns. Temp fixtures live under
# <temp>\rubbish-cleaner-tests\<pid>\optimization (temp root resolved via
# [System.IO.Path]::GetTempPath(), the cross-platform temp root)
# and are removed in AfterAll.
#
# scan-drive.ps1 validates a real fixed volume (Get-Volume), so its
# classification logic is consumed through the same <begin-classification> /
# <end-classification> seam used by scan.Tests.ps1; the multi-drive and resume
# subprocess checks run against real fixed drives read-only with a tiny
# category filter.

BeforeAll {
    # Fixture plumbing lives in BeforeAll (Pester 5: top-level $script: vars
    # set during discovery are NULL in the run phase, so paths built here).
    $script:SuiteRoot      = Join-Path ([System.IO.Path]::GetTempPath()) ("rubbish-cleaner-tests\{0}\optimization" -f $PID)
    $script:ScanDrivePath  = Join-Path $PSScriptRoot '..\..\scripts\scan-drive.ps1'
    $script:SchedulePath   = Join-Path $PSScriptRoot '..\..\scripts\schedule.ps1'
    $script:PolicyDir      = Join-Path $PSScriptRoot '..\..\references\policies'

    # Helper defined INSIDE BeforeAll: Pester 5 runs the file's top level during
    # DISCOVERY in a scope discarded before the run phase, so functions must be
    # created in the run phase to be visible to It blocks.
    function New-TestDir {
        param([string]$Name)
        $p = Join-Path $script:SuiteRoot $Name
        New-Item -ItemType Directory -Path $p -Force | Out-Null
        return $p
    }

    . (Join-Path $PSScriptRoot '..\..\scripts\lib\rubbish-core.ps1')
    . (Join-Path $PSScriptRoot '..\..\scripts\lib\platform.ps1')

    # ---- extract + dot-source the classification seam (as scan.Tests.ps1) --
    # NOTE: match the marker WITH the '# ' comment prefix (as the sandbox
    # harness does) so the extracted block starts on a comment line; matching
    # the bare '<...>' token makes the first line `<begin-classification>`,
    # which PowerShell parses as a command -> "The term '<' is not recognized".
    $scanSource = [System.IO.File]::ReadAllText($script:ScanDrivePath)
    $startIdx = $scanSource.IndexOf('# <begin-classification>')
    $endIdx   = $scanSource.IndexOf('# <end-classification>')
    if ($startIdx -lt 0 -or $endIdx -lt 0) {
        throw 'scan-drive.ps1 classification markers (# <begin-classification>/# <end-classification>) not found'
    }
    $classBlock = $scanSource.Substring($startIdx, $endIdx - $startIdx)
    . ([scriptblock]::Create($classBlock))
}

Describe 'InvokeParallelForEach' {
    It 'processes all 10 inputs with ThrottleLimit 2' {
        $base = New-TestDir 'parallel-tl2'
        $dirs = @()
        for ($i = 0; $i -lt 10; $i++) {
            $dirs += (Join-Path $base ("p{0}" -f $i))
            New-Item -ItemType Directory -Path $dirs[-1] -Force | Out-Null
        }
        $results = @(Invoke-ParallelForEach -InputObject $dirs -ScriptBlock {
            param($x) [System.IO.Path]::GetFileName($x)
        } -ThrottleLimit 2)
        $results.Count | Should -Be 10
    }

    It 'produces the same result set with ThrottleLimit 1' {
        $base = New-TestDir 'parallel-tl1'
        $dirs = @()
        for ($i = 0; $i -lt 10; $i++) {
            $dirs += (Join-Path $base ("q{0}" -f $i))
            New-Item -ItemType Directory -Path $dirs[-1] -Force | Out-Null
        }
        $r1 = @(Invoke-ParallelForEach -InputObject $dirs -ScriptBlock { param($x) [System.IO.Path]::GetFileName($x) } -ThrottleLimit 1)
        $r2 = @(Invoke-ParallelForEach -InputObject $dirs -ScriptBlock { param($x) [System.IO.Path]::GetFileName($x) } -ThrottleLimit 2)
        (($r1 | Sort-Object) -join ',') | Should -Be (($r2 | Sort-Object) -join ',')
    }

    It 'renames all 10 directories in parallel and leaves 0 background jobs' {
        $base = New-TestDir 'parallel-rename'
        $dirs = @()
        for ($i = 0; $i -lt 10; $i++) {
            $dirs += (Join-Path $base ("r{0}" -f $i))
            New-Item -ItemType Directory -Path $dirs[-1] -Force | Out-Null
        }
        Invoke-ParallelForEach -InputObject $dirs -ScriptBlock {
            param($p) Rename-Item -LiteralPath $p -NewName ((Split-Path $p -Leaf) + '-done')
        } -ThrottleLimit 2 | Out-Null
        $renamed = 0
        for ($i = 0; $i -lt 10; $i++) {
            if (Test-Path -LiteralPath (Join-Path $base ("r{0}-done" -f $i))) { $renamed++ }
        }
        $renamed | Should -Be 10
        @(Get-Job).Count | Should -Be 0
    }
}

Describe 'CheckpointResume' {
    It 'writes a scan-checkpoint.json marking both scanned categories complete' {
        $root = New-TestDir 'cp-fake'
        $tempDir = Join-Path $root 'Temp'
        New-Item -ItemType Directory -Path $tempDir -Force | Out-Null
        $aTmp = Join-Path $tempDir 'a.tmp'
        Set-Content -LiteralPath $aTmp -Value 'old'
        [System.IO.File]::SetLastWriteTime($aTmp, (Get-Date).AddDays(-10))
        $bootLog = Join-Path $root 'boot.log'
        Set-Content -LiteralPath $bootLog -Value 'boot'
        [System.IO.File]::SetLastWriteTime($bootLog, (Get-Date).AddDays(-10))

        $cpPath = Join-Path (New-TestDir 'cp-files') 'scan-checkpoint.json'
        $state = New-ScanCheckpointState -Path $cpPath -Drive 'C:'
        Get-JunkCandidates -RootPath $root -IsUserDrive $false -IncludeElevated $false -Categories @('root-temps', 'root-logs') -Checkpoint $state | Out-Null

        Test-Path -LiteralPath $cpPath -PathType Leaf | Should -BeTrue
        $cp = Get-Content -LiteralPath $cpPath -Raw | ConvertFrom-Json
        $cp.drive | Should -Be 'C:'
        @($cp.completedCategories) -contains 'root-temps' | Should -BeTrue
        @($cp.completedCategories) -contains 'root-logs' | Should -BeTrue
    }

    It 'partial resume skips the completed category and resumes the current category at lastPath' {
        $root = New-TestDir 'cp-resume'
        $aLog = Join-Path $root 'a.log'
        Set-Content -LiteralPath $aLog -Value 'x'
        [System.IO.File]::SetLastWriteTime($aLog, (Get-Date).AddDays(-10))
        $bLog = Join-Path $root 'b.log'
        Set-Content -LiteralPath $bLog -Value 'y'
        [System.IO.File]::SetLastWriteTime($bLog, (Get-Date).AddDays(-10))
        $tempDir = Join-Path $root 'Temp'
        New-Item -ItemType Directory -Path $tempDir -Force | Out-Null
        $skipTmp = Join-Path $tempDir 'skip.tmp'
        Set-Content -LiteralPath $skipTmp -Value 'z'
        [System.IO.File]::SetLastWriteTime($skipTmp, (Get-Date).AddDays(-10))

        $resume = @{
            CompletedCategories = @('root-temps')
            CurrentCategory     = 'root-logs'
            LastPath            = $bLog
        }
        $r = Get-JunkCandidates -RootPath $root -IsUserDrive $false -IncludeElevated $false -Categories @('root-temps', 'root-logs') -ResumeState $resume
        $rows = @($r.Rows.ToArray())
        $eval = @($r.Evaluated.ToArray())

        $eval.Count | Should -Be 1
        $eval[0].name | Should -Be 'root-logs'
        @($rows | Where-Object { $_.Category -eq 'root-temps' }).Count | Should -Be 0
        @($rows | Where-Object { $_.Path -eq $aLog }).Count | Should -Be 0
        @($rows | Where-Object { $_.Path -eq $bLog }).Count | Should -Be 1
    }

    It '-Resume subprocess prints the skip/resume message' {
        $outDir = Join-Path $script:SuiteRoot 'cp-out'
        New-Item -ItemType Directory -Path $outDir -Force | Out-Null
        $runDir = Join-Path $outDir ("C-{0}" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
        New-Item -ItemType Directory -Path $runDir -Force | Out-Null
        $cpJson = [ordered]@{
            drive               = 'C:'
            completedCategories = @('root-temps')
            currentCategory     = 'root-logs'
            lastPath            = ''
            totalBytesSoFar     = 0
            timestamp           = (Get-Date).ToString('o')
        }
        [System.IO.File]::WriteAllText((Join-Path $runDir 'scan-checkpoint.json'),
            (ConvertTo-Json -InputObject $cpJson), (New-Object System.Text.UTF8Encoding($false)))

        $subOut = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $script:ScanDrivePath -Drive C: -OutDir $outDir -Resume -Categories root-temps,root-logs
        $LASTEXITCODE | Should -Be 0
        (($subOut | Out-String) -match 'RESUME: resuming from') | Should -BeTrue
    }
}

Describe 'PlatformDetection' {
    It 'detects Windows on this machine' {
        $script:IsWin | Should -BeTrue
    }

    It 'reports at least one fixed drive letter' {
        @(Get-FixedDriveLetters).Count | Should -BeGreaterThan 0
    }

    It 'resolves a non-empty user cache directory' {
        [string]::IsNullOrWhiteSpace((Get-UserCacheDir)) | Should -BeFalse
    }

    It 'resolves non-empty system temp and documents directories' {
        [string]::IsNullOrWhiteSpace((Get-SystemTempDir)) | Should -BeFalse
        [string]::IsNullOrWhiteSpace((Get-UserDocumentsDir)) | Should -BeFalse
    }
}

Describe 'ScheduleParams' {
    It '-Action List exits 0' {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $script:SchedulePath -Action List | Out-Null
        $LASTEXITCODE | Should -Be 0
    }

    It 'non-elevated -Action Register -Drive C: -Policy safe exits 1 with an admin-required error' {
        $p = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
        if ($p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
            Set-ItResult -Skipped -Because 'running elevated; registration would succeed'
            return
        }
        $errFile = Join-Path $script:SuiteRoot 'register-err.txt'
        $outFile = Join-Path $script:SuiteRoot 'register-out.txt'
        $proc = Start-Process -FilePath 'powershell.exe' `
            -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $script:SchedulePath, '-Action', 'Register', '-Drive', 'C:', '-Policy', 'safe') `
            -Wait -PassThru -NoNewWindow -RedirectStandardError $errFile -RedirectStandardOutput $outFile
        $proc.ExitCode | Should -Be 1
        $errText = if (Test-Path -LiteralPath $errFile) { [System.IO.File]::ReadAllText($errFile) } else { '' }
        ($errText -match 'administrator') | Should -BeTrue
    }

    It 'policy profiles under references/policies parse with a Categories array' {
        $safe = Get-Content -LiteralPath (Join-Path $script:PolicyDir 'safe.json') -Raw | ConvertFrom-Json
        $aggr = Get-Content -LiteralPath (Join-Path $script:PolicyDir 'aggressive.json') -Raw | ConvertFrom-Json
        $safe.name | Should -Be 'safe'
        @($safe.Categories).Count | Should -BeGreaterThan 0
        $aggr.name | Should -Be 'aggressive'
        @($aggr.Categories).Count | Should -BeGreaterThan 0
        $aggr.includeElevated | Should -BeTrue
    }
}

Describe 'MultiDrive' {
    It '-Drives scans two fixed drives into two per-drive run dirs + a combined drives.csv' {
        $driveLetters = @(Get-Volume | Where-Object { $_.DriveType -eq 'Fixed' -and $_.DriveLetter } | Select-Object -First 2 -ExpandProperty DriveLetter)
        if ($driveLetters.Count -lt 2) {
            Set-ItResult -Skipped -Because 'fewer than 2 fixed drives'
            return
        }
        $drivesArg = (($driveLetters | ForEach-Object { $_.ToString() + ':' }) -join ',')
        $outDir = Join-Path $script:SuiteRoot 'md-out'
        New-Item -ItemType Directory -Path $outDir -Force | Out-Null

        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $script:ScanDrivePath -Drives $drivesArg -OutDir $outDir -Categories root-temps,root-logs | Out-Null
        $LASTEXITCODE | Should -Be 0

        $multi = @(Get-ChildItem -LiteralPath $outDir -Directory -Filter 'multidrive-*' | Sort-Object LastWriteTime -Descending | Select-Object -First 1)
        $multi.Count | Should -Be 1
        $csv = Join-Path $multi[0].FullName 'drives.csv'
        Test-Path -LiteralPath $csv -PathType Leaf | Should -BeTrue
        $lines = @([System.IO.File]::ReadAllLines($csv))
        $lines.Count | Should -Be 3
        $runDirs = @()
        for ($i = 1; $i -lt $lines.Count; $i++) {
            $cols = @($lines[$i] -split '\|')
            $cols[3] | Should -Be 'OK'
            [string]::IsNullOrWhiteSpace($cols[1]) | Should -BeFalse
            $runDirs += $cols[1]
        }
        @($runDirs | Select-Object -Unique).Count | Should -Be 2
        foreach ($letter in $driveLetters) {
            @(Get-ChildItem -LiteralPath $outDir -Directory -Filter "$letter-*").Count | Should -BeGreaterThan 0
        }
    }

    It '-Parallel produces the same two per-drive run dirs' {
        $driveLetters = @(Get-Volume | Where-Object { $_.DriveType -eq 'Fixed' -and $_.DriveLetter } | Select-Object -First 2 -ExpandProperty DriveLetter)
        if ($driveLetters.Count -lt 2) {
            Set-ItResult -Skipped -Because 'fewer than 2 fixed drives'
            return
        }
        $drivesArg = (($driveLetters | ForEach-Object { $_.ToString() + ':' }) -join ',')
        $outDir = Join-Path $script:SuiteRoot 'md-out-par'
        New-Item -ItemType Directory -Path $outDir -Force | Out-Null

        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $script:ScanDrivePath -Drives $drivesArg -OutDir $outDir -Categories root-temps,root-logs -Parallel | Out-Null
        $LASTEXITCODE | Should -Be 0

        $multi = @(Get-ChildItem -LiteralPath $outDir -Directory -Filter 'multidrive-*' | Sort-Object LastWriteTime -Descending | Select-Object -First 1)
        $multi.Count | Should -Be 1
        $csv = Join-Path $multi[0].FullName 'drives.csv'
        Test-Path -LiteralPath $csv -PathType Leaf | Should -BeTrue
        $lines = @([System.IO.File]::ReadAllLines($csv))
        $lines.Count | Should -Be 3
        $runDirs = @()
        for ($i = 1; $i -lt $lines.Count; $i++) {
            $cols = @($lines[$i] -split '\|')
            $cols[3] | Should -Be 'OK'
            [string]::IsNullOrWhiteSpace($cols[1]) | Should -BeFalse
            $runDirs += $cols[1]
        }
        @($runDirs | Select-Object -Unique).Count | Should -Be 2
        foreach ($letter in $driveLetters) {
            @(Get-ChildItem -LiteralPath $outDir -Directory -Filter "$letter-*").Count | Should -BeGreaterThan 0
        }
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
