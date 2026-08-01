# platform.ps1 - Cross-platform detection layer for the rubbish-cleaner pipeline.
#
# DOT-SOURCED library: contains ONLY function definitions plus one-time
# module-scoped platform detection at import time (no disk writes, no
# deletions, no other side effects). PowerShell 5.1-compatible syntax only;
# gracefully degrades on pwsh 6+ / Linux / macOS.
#
# Exposes four module-scoped booleans, evaluated ONCE at dot-source time:
#   $script:IsWindows - $true on Windows (PS 5.1 Desktop or Core)
#   $script:IsLinux   - $true on Linux
#   $script:IsMacOS   - $true on macOS
#   $script:IsCoreCLR - $true when running under PowerShell Core (pwsh 6+)
#
# Detection: pwsh 6+ has built-in $IsWindows/$IsLinux/$IsMacOS; PS 5.1
# Desktop falls back to [System.Environment]::OSVersion.Platform (Win32NT =>
# Windows; Unix => disambiguate Linux vs macOS via `uname -s`).
# All path lookups go through environment variables / .NET APIs so nothing
# is written to disk at import time.

# --- One-time platform detection (module scope, no side effects) -----------

$script:IsWindows = $false
$script:IsLinux   = $false
$script:IsMacOS   = $false
$script:IsCoreCLR = ($PSVersionTable.PSEdition -eq 'Core')

if ($script:IsCoreCLR) {
    # PowerShell Core (pwsh 6+): built-in booleans are authoritative.
    $script:IsWindows = [bool]$IsWindows
    $script:IsLinux   = [bool]$IsLinux
    $script:IsMacOS   = [bool]$IsMacOS
} else {
    # PowerShell 5.1 Desktop: derive from the .NET platform identifier.
    $platform = [System.Environment]::OSVersion.Platform
    if ($platform -eq [System.PlatformID]::Win32NT) {
        $script:IsWindows = $true
    } elseif ($platform -eq [System.PlatformID]::Unix) {
        # `uname -s`: "Linux" => Linux; "Darwin" => macOS.
        $unameOut = $null
        try { $unameOut = (& uname -s) } catch { $unameOut = $null }
        if ($unameOut -match 'Darwin') {
            $script:IsMacOS = $true
        } else {
            $script:IsLinux = $true
        }
    }
}

# --- Helper functions ------------------------------------------------------

# Returns the fixed-drive root letters on Windows, e.g. @('C:\','D:\'),
# or @('/') on Linux/macOS.
function Get-FixedDriveLetters {
    if ($script:IsWindows) {
        return @(Get-PSDrive -PSProvider FileSystem |
            Where-Object { $_.Free -gt 0 } |
            ForEach-Object { $_.Root })
    }
    return @('/')
}

# Returns the user cache directory:
#   Windows -> $env:LOCALAPPDATA
#   macOS   -> $env:HOME/Library/Caches
#   Linux   -> $env:XDG_CACHE_HOME if set, else $env:HOME/.cache
function Get-UserCacheDir {
    if ($script:IsWindows) {
        return $env:LOCALAPPDATA
    }
    if ($script:IsMacOS) {
        return ($env:HOME + '/Library/Caches')
    }
    # Linux
    if ($env:XDG_CACHE_HOME) {
        return $env:XDG_CACHE_HOME
    }
    return ($env:HOME + '/.cache')
}

# Returns the system temp directory:
#   Windows -> $env:TEMP
#   Linux/macOS -> '/tmp'
function Get-SystemTempDir {
    if ($script:IsWindows) {
        return $env:TEMP
    }
    return '/tmp'
}

# Returns the user documents directory:
#   Windows -> [Environment]::GetFolderPath('MyDocuments')
#   Linux/macOS -> $env:HOME
function Get-UserDocumentsDir {
    if ($script:IsWindows) {
        return [System.Environment]::GetFolderPath('MyDocuments')
    }
    return $env:HOME
}
