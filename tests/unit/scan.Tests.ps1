# scan.Tests.ps1 - Pester 5 unit suite for scan-drive.ps1 classification.
#
# Strategy (documented choice): scan-drive.ps1 is a CLI script that validates a
# real fixed drive via Get-Volume, which tests must not touch. The module
# deliberately keeps its classification logic between the
# <begin-classification> / <end-classification> markers "so tests (todos 8-9)
# can extract and invoke it directly against a fake tree without running the
# script's main validation/IO block" (scripts/scan-drive.ps1 lines 38-41).
# This suite uses exactly that testable seam: it dot-sources the extracted
# classification block together with scripts/lib/rubbish-core.ps1 and invokes
# Get-JunkCandidates against a fake tree under
# <temp>\rubbish-cleaner-tests\<pid>\scan (temp root resolved via
# [System.IO.Path]::GetTempPath(), the cross-platform temp root).
#
# Fake tree (mirrors the shared behavior matrix):
#   Temp\a.tmp          (>7 days old)           -> root-temps        (SAFE/delete)
#   tmp\b.log           (>7 days old)           -> root-temps        (SAFE/delete)
#   empty1\             (empty)                 -> empty-dirs        (SAFE/delete)
#   MyApp\cache\        (with files)            -> NOT classified    (negative control: not a known app-cache template)
#   archive.zip + archive\ (non-empty folder)   -> duplicate-archives(ASK/ask) - archive FILE only, never the folder
#   root-suspicious.dll                         -> root-suspicious   (CAUTION/quarantine)
#   keep\userfile.txt                           -> MUST NOT be classified
#
# The fake root is scanned with -IsUserDrive $false and no -IncludeElevated,
# so the evaluated category set is deterministic: root-temps, root-logs,
# duplicate-archives, empty-dirs, recycle-bin, root-suspicious, app-caches (7).

BeforeAll {
    # Fixture plumbing lives in BeforeAll (Pester 5: top-level $script: vars
    # set during discovery are NULL in the run phase, so paths built here).
    $script:SuiteRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("rubbish-cleaner-tests\{0}\scan" -f $PID)
    $script:ScanDrivePath = Join-Path $PSScriptRoot '..\..\scripts\scan-drive.ps1'

    . (Join-Path $PSScriptRoot '..\..\scripts\lib\rubbish-core.ps1')

    # ---- extract + dot-source the classification seam --------------------
    # NOTE: match the marker WITH the '# ' comment prefix (as the sandbox
    # harness does) so the extracted block starts on a comment line; matching
    # the bare '<...>' token makes the first line `<begin-classification>`,
    # which PowerShell parses as a command -> "The term '<' is not recognized".
    $scanSource = [System.IO.File]::ReadAllText($script:ScanDrivePath)
    $startIdx = $scanSource.IndexOf('# <begin-classification>')
    $endIdx = $scanSource.IndexOf('# <end-classification>')
    if ($startIdx -lt 0 -or $endIdx -lt 0) {
        throw 'scan-drive.ps1 classification markers (# <begin-classification>/# <end-classification>) not found'
    }
    $classBlock = $scanSource.Substring($startIdx, $endIdx - $startIdx)
    . ([scriptblock]::Create($classBlock))

    # ---- build the fake tree ---------------------------------------------
    $script:FakeRoot = Join-Path $script:SuiteRoot 'fake'
    New-Item -ItemType Directory -Path $script:FakeRoot -Force | Out-Null

    $tempDir = Join-Path $script:FakeRoot 'Temp'
    New-Item -ItemType Directory -Path $tempDir -Force | Out-Null
    $aTmp = Join-Path $tempDir 'a.tmp'
    Set-Content -LiteralPath $aTmp -Value 'old temp'
    [System.IO.File]::SetLastWriteTime($aTmp, (Get-Date).AddDays(-30))

    $tmpDir = Join-Path $script:FakeRoot 'tmp'
    New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null
    $bLog = Join-Path $tmpDir 'b.log'
    Set-Content -LiteralPath $bLog -Value 'old log in tmp'
    [System.IO.File]::SetLastWriteTime($bLog, (Get-Date).AddDays(-30))

    New-Item -ItemType Directory -Path (Join-Path $script:FakeRoot 'empty1') -Force | Out-Null

    $myAppCache = Join-Path $script:FakeRoot 'MyApp\cache'
    New-Item -ItemType Directory -Path $myAppCache -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $myAppCache 'data.bin') -Value 'cache payload'

    $arch = Join-Path $script:FakeRoot 'archive'
    New-Item -ItemType Directory -Path $arch -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $arch 'content.txt') -Value 'extracted content'
    Set-Content -LiteralPath (Join-Path $script:FakeRoot 'archive.zip') -Value 'zip payload'

    Set-Content -LiteralPath (Join-Path $script:FakeRoot 'root-suspicious.dll') -Value 'dll payload'

    $keep = Join-Path $script:FakeRoot 'keep'
    New-Item -ItemType Directory -Path $keep -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $keep 'userfile.txt') -Value 'user data'

    # ---- run the shared classifier (all categories, non-user drive) ------
    $result = Get-JunkCandidates -RootPath $script:FakeRoot -IsUserDrive $false -IncludeElevated $false
    # NOTE: Get-JunkCandidates returns generic List[object] collections, which
    # PS 5.1 cannot unroll with the @() operator (throws "argument type
    # mismatch"); .ToArray() -> object[] is portable across PS 5.1 and 7.
    $script:ScanRows = @($result.Rows.ToArray())
    $script:ScanEvaluated = @($result.Evaluated.ToArray())
}

Describe 'scan classification' {
    It 'classifies Temp\a.tmp and tmp\b.log as root-temps (SAFE -> delete)' {
        $rows = @($script:ScanRows | Where-Object { $_.Category -eq 'root-temps' })
        $rows.Count | Should -Be 2
        $paths = @($rows | ForEach-Object { $_.Path })
        $paths | Should -Contain (Join-Path $script:FakeRoot 'Temp\a.tmp')
        $paths | Should -Contain (Join-Path $script:FakeRoot 'tmp\b.log')
        foreach ($row in $rows) {
            $row.Risk | Should -Be 'SAFE'
            $row.Action | Should -Be 'delete'
            $row.FileCount | Should -Be 1
        }
    }

    It 'classifies the empty directory as empty-dirs (SAFE -> delete)' {
        $rows = @($script:ScanRows | Where-Object { $_.Category -eq 'empty-dirs' })
        $rows.Count | Should -Be 1
        $rows[0].Path | Should -Be (Join-Path $script:FakeRoot 'empty1')
        $rows[0].Risk | Should -Be 'SAFE'
        $rows[0].Action | Should -Be 'delete'
        $rows[0].SizeBytes | Should -Be 0
        $rows[0].FileCount | Should -Be 0
    }

    It 'classifies archive.zip (never the extracted folder) as duplicate-archives (ASK -> ask)' {
        $rows = @($script:ScanRows | Where-Object { $_.Category -eq 'duplicate-archives' })
        $rows.Count | Should -Be 1
        $rows[0].Path | Should -Be (Join-Path $script:FakeRoot 'archive.zip')
        $rows[0].Risk | Should -Be 'ASK'
        $rows[0].Action | Should -Be 'ask'
        $rows[0].FileCount | Should -Be 1
    }

    It 'classifies the root dll as root-suspicious (CAUTION -> quarantine)' {
        $rows = @($script:ScanRows | Where-Object { $_.Category -eq 'root-suspicious' })
        $rows.Count | Should -Be 1
        $rows[0].Path | Should -Be (Join-Path $script:FakeRoot 'root-suspicious.dll')
        $rows[0].Risk | Should -Be 'CAUTION'
        $rows[0].Action | Should -Be 'quarantine'
        $rows[0].FileCount | Should -Be 1
    }

    It 'does NOT classify keep\userfile.txt (user data) or MyApp\cache (unknown template)' {
        $paths = @($script:ScanRows | ForEach-Object { $_.Path })
        $paths -notcontains (Join-Path $script:FakeRoot 'keep\userfile.txt') | Should -BeTrue
        $paths -notcontains (Join-Path $script:FakeRoot 'keep') | Should -BeTrue
        $paths -notcontains (Join-Path $script:FakeRoot 'MyApp\cache') | Should -BeTrue
        $paths -notcontains (Join-Path $script:FakeRoot 'MyApp') | Should -BeTrue
        $paths -notcontains (Join-Path $script:FakeRoot 'archive') | Should -BeTrue
    }

    It 'produces exactly 5 candidate rows for the fake tree' {
        $script:ScanRows.Count | Should -Be 5
    }

    It 'evaluates the 7 non-user, non-elevated categories' {
        $names = @($script:ScanEvaluated | ForEach-Object { $_.name })
        $names.Count | Should -Be 7
        foreach ($n in @('root-temps', 'root-logs', 'duplicate-archives', 'empty-dirs', 'recycle-bin', 'root-suspicious', 'app-caches')) {
            $names -contains $n | Should -BeTrue
        }
    }

    It 'every candidate row carries the full CSV schema (Category|Risk|Path|SizeBytes|FileCount|Action) and a fixed risk->action mapping' {
        foreach ($row in $script:ScanRows) {
            $row.Category | Should -Not -BeNullOrEmpty
            $row.Risk | Should -Not -BeNullOrEmpty
            $row.Path | Should -Not -BeNullOrEmpty
            $row.SizeBytes | Should -Not -BeNullOrEmpty
            $row.FileCount | Should -Not -BeNullOrEmpty
            $row.Action | Should -Not -BeNullOrEmpty

            $expectedAction = switch ($row.Risk) {
                'SAFE'     { 'delete' }
                'CAUTION'  { 'quarantine' }
                'ASK'      { 'ask' }
                'ELEVATED' { 'report-only' }
                default    { $null }
            }
            $row.Action | Should -Be $expectedAction
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
