# run-tests.ps1 - dual-mode test runner for the rubbish-cleaner skill.
#
# MODE 0 (always, FIRST): syntax-check EVERY .ps1 under scripts/ and tests/
# via [System.Management.Automation.Language.Parser]::ParseFile. This validates
# the Pester-5 unit files even on machines where Pester 5 is NOT installed
# (this machine ships PS 5.1 with Pester 3.4). Any parse error is printed
# together with the offending file and the runner exits 1 before any test
# branch is taken.
#
# MODE 1 (branch after a clean parse check):
#   - Pester >= 5.0.0 available: re-import Pester 5 and run tests/unit via
#     Invoke-Pester -PassThru; exit 1 on any failed test, else exit 0.
#     Prints "BRANCH: PESTER".
#   - Otherwise: print "BRANCH: SANDBOX" and delegate to the zero-dependency
#     harness tests/sandbox/run-sandbox-tests.ps1 (todo 9), propagating its
#     exit code. If that file is not present yet (parallel todo-9 worker has
#     not landed), the parse-check portion has still passed -> print a clear
#     message and exit 0 for the parse-check portion only.

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot

# =====================================================================
# (0) parse-check every .ps1 under scripts/ and tests/ (FIRST)
# =====================================================================
$targets = @()
$targets += @(Get-ChildItem -LiteralPath (Join-Path $repoRoot 'scripts') -Recurse -Filter '*.ps1' -File -ErrorAction SilentlyContinue)
$targets += @(Get-ChildItem -LiteralPath $PSScriptRoot -Recurse -Filter '*.ps1' -File -ErrorAction SilentlyContinue)

$parseFailures = 0
foreach ($file in $targets) {
    $tokens = $null
    $errors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($file.FullName, [ref]$tokens, [ref]$errors)
    if ($errors.Count -gt 0) {
        $parseFailures++
        Write-Output "PARSE FAIL: $($file.FullName)"
        foreach ($err in $errors) {
            Write-Output ("  Line {0}, col {1}: {2}" -f $err.Extent.StartLineNumber, $err.Extent.StartColumnNumber, $err.Message)
        }
    } else {
        Write-Output "PARSE PASS: $($file.FullName)"
    }
}
Write-Output ("PARSE CHECK: {0} file(s), {1} with parse errors" -f $targets.Count, $parseFailures)
if ($parseFailures -gt 0) {
    Write-Output 'PARSE CHECK FAILED: aborting before any test branch.'
    exit 1
}

# =====================================================================
# (1) dual-mode branch
# =====================================================================
$pester = Get-Module -ListAvailable -Name Pester |
    Where-Object { $_.Version -ge [version]'5.0.0' } |
    Select-Object -First 1

if ($pester) {
    Remove-Module Pester -Force -ErrorAction SilentlyContinue
    Import-Module Pester -MinimumVersion 5.0 -Force
    $r = Invoke-Pester -Path "$PSScriptRoot\unit" -PassThru -Output Detailed
    Write-Output 'BRANCH: PESTER'
    if ($r.Result -ne 'Passed') { exit 1 } else { exit 0 }
} else {
    Write-Output 'BRANCH: SANDBOX'
    $sandbox = Join-Path $PSScriptRoot 'sandbox\run-sandbox-tests.ps1'
    if (Test-Path -LiteralPath $sandbox) {
        & $sandbox
        $sandboxExit = $LASTEXITCODE
        if ($null -eq $sandboxExit) { $sandboxExit = 1 }
        exit $sandboxExit
    } else {
        Write-Output "SANDBOX HARNESS NOT FOUND: $sandbox"
        Write-Output '(todo 9 parallel worker has not landed its file yet; the parse-check portion above is the only portion runnable here)'
        Write-Output 'PARSE CHECK PORTION: PASS (exit 0)'
        exit 0
    }
}
