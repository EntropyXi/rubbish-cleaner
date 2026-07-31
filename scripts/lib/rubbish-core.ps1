# rubbish-core.ps1 - Safety function library for the rubbish-cleaner pipeline.
#
# DOT-SOURCED library: contains ONLY function definitions. There is no
# executable top-level code and no side effect at import time (no writes,
# no deletions). PowerShell 5.1-compatible syntax only.
#
# All item operations use -LiteralPath so that paths are never interpreted as
# wildcards. Bare -Recurse is never used because PowerShell 5.1 follows NTFS
# junctions when recursing; recursion is done manually and reparse-point
# children are skipped without descending.

# (a) Junction-aware recursive empty check.
# Returns $true ONLY when $Path contains no files and no non-junction subdirs,
# i.e. the whole tree beneath $Path (EXCLUDING reparse-point children) is
# empty. Non-junction subdirs are recursed into manually.
function Test-DirEmpty {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Container)) { return $false }

    foreach ($child in Get-ChildItem -LiteralPath $Path -Force) {
        # Never descend into reparse points (junctions / symlinks).
        if ($child.Attributes -band [System.IO.FileAttributes]::ReparsePoint) { continue }

        if ($child.PSIsContainer) {
            # Recurse into non-junction subdirs by hand.
            if (-not (Test-DirEmpty -Path $child.FullName)) { return $false }
        } else {
            # A file anywhere in the tree means the dir is not empty.
            return $false
        }
    }
    return $true
}

# (b) Returns $true when $Path is a reparse point (junction).
function Test-IsJunction {
    param([string]$Path)

    $item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    if ($null -eq $item) { return $false }
    return (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0)
}

# (c) Per-item safe removal. Removes one item with try/catch and records the
# outcome in the cleanup CSV: Disposition=OK on success, otherwise
# SKIP_LOCKED (IOException), SKIP_ACCESS_DENIED (UnauthorizedAccessException)
# or SKIP_NOT_FOUND (ItemNotFound), with the exception message attached.
function Invoke-SafeRemove {
    param(
        [string]$LiteralPath,
        [string]$Phase,
        [string]$CsvPath
    )

    try {
        Remove-Item -LiteralPath $LiteralPath -Force -ErrorAction Stop
        Write-CleanupCsv -CsvPath $CsvPath -Row @{
            Phase        = $Phase
            Action       = 'Remove'
            Path         = $LiteralPath
            ErrorMessage = ''
            Disposition  = 'OK'
        }
    } catch {
        # Classify the failure by walking the exception chain (PowerShell wraps
        # .NET exceptions). ItemNotFound surfaces as the outer PowerShell
        # exception and is checked first so it wins over any inner IOException.
        $disposition = 'SKIP_LOCKED'
        $ex = $_.Exception
        while ($null -ne $ex) {
            if ($ex -is [System.Management.Automation.ItemNotFoundException]) { $disposition = 'SKIP_NOT_FOUND'; break }
            if ($ex -is [System.UnauthorizedAccessException])               { $disposition = 'SKIP_ACCESS_DENIED'; break }
            if ($ex -is [System.IO.IOException])                            { $disposition = 'SKIP_LOCKED'; break }
            $ex = $ex.InnerException
        }
        Write-CleanupCsv -CsvPath $CsvPath -Row @{
            Phase        = $Phase
            Action       = 'Remove'
            Path         = $LiteralPath
            ErrorMessage = $_.Exception.Message
            Disposition  = $disposition
        }
    }
}

# (d) Move one item into the quarantine dir. NEVER deletes. Records
# Disposition=QUARANTINED on success, MOVE_FAILED + message on failure.
function Invoke-Quarantine {
    param(
        [string]$LiteralPath,
        [string]$QuarantineDir,
        [string]$Phase,
        [string]$CsvPath
    )

    try {
        New-Item -ItemType Directory -Force -Path $QuarantineDir | Out-Null
        Move-Item -LiteralPath $LiteralPath -Destination $QuarantineDir -Force -ErrorAction Stop
        Write-CleanupCsv -CsvPath $CsvPath -Row @{
            Phase        = $Phase
            Action       = 'Quarantine'
            Path         = $LiteralPath
            ErrorMessage = ''
            Disposition  = 'QUARANTINED'
        }
    } catch {
        Write-CleanupCsv -CsvPath $CsvPath -Row @{
            Phase        = $Phase
            Action       = 'Quarantine'
            Path         = $LiteralPath
            ErrorMessage = $_.Exception.Message
            Disposition  = 'MOVE_FAILED'
        }
    }
}

# (e) Append one row to the pipe-delimited cleanup CSV
# `Timestamp|Phase|Action|Path|ErrorMessage|Disposition` (Timestamp is
# ISO-8601). Creates the header line once, when the file does not exist yet.
function Write-CleanupCsv {
    param(
        [string]$CsvPath,
        [hashtable]$Row
    )

    if (-not (Test-Path -LiteralPath $CsvPath)) {
        Set-Content -LiteralPath $CsvPath -Value 'Timestamp|Phase|Action|Path|ErrorMessage|Disposition' -Encoding UTF8
    }

    $fields = @(
        (Get-Date -Format o),
        [string]$Row['Phase'],
        [string]$Row['Action'],
        [string]$Row['Path'],
        [string]$Row['ErrorMessage'],
        [string]$Row['Disposition']
    )
    # Flatten CR/LF inside field values so every record stays a single line.
    $line = ($fields | ForEach-Object { $_ -replace "[\r\n]+", ' ' }) -join '|'
    Add-Content -LiteralPath $CsvPath -Value $line -Encoding UTF8
}

# (f) Free space (bytes) remaining on the volume for the given drive letter.
function Get-DriveFreeSpace {
    param([string]$DriveLetter)

    return (Get-Volume -DriveLetter $DriveLetter).SizeRemaining
}

# (g) Fixed set of disposal outcomes used across the pipeline.
function Get-JunkDispositions {
    return @(
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
}

# (h) Returns $true when the file cannot be opened for shared read access
# (i.e. it is locked by another handle), $false when it opens cleanly
# (the handle is closed before returning). Used by tests to simulate
# locked files.
function Test-FileLocked {
    param([string]$LiteralPath)

    try {
        $handle = [System.IO.File]::Open($LiteralPath, 'Open', 'Read', 'None')
        $handle.Close()
        return $false
    } catch {
        # The open threw (file locked / not accessible) -> treat as locked.
        return $true
    }
}
