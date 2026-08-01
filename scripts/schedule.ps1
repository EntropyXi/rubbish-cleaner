# schedule.ps1 - Register / unregister / list scheduled runs of the rubbish-cleaner pipeline.
#
# Cross-platform scheduler integration:
#   Windows -> Task Scheduler (Register-ScheduledTask / Unregister-ScheduledTask)
#   Linux   -> cron (/etc/cron.d/rubbish-cleaner via sudo, or user crontab append)
#   macOS   -> launchd agent (~/Library/LaunchAgents/com.rubbish-cleaner.plist)
#
# Policy profiles are loaded from references/policies/<Policy>.json. Each profile
# carries a `Categories` array; the register command bakes that comma-joined list
# into the scheduled scan invocation.
#
# Usage:
#   schedule.ps1 -Action Register -Drive C: -Policy safe -Time 02:00
#   schedule.ps1 -Action List
#   schedule.ps1 -Action Unregister [-Drive C:]
#
# Notes:
#   - Windows registration requires an elevated (administrator) shell.
#   - Register NEVER runs a scan/clean itself; it only schedules them.
#   - A summary of each registration is written under
#     $HOME\.rubbish-cleaner\scheduled\<Drive>-<timestamp>\summary.md; on Windows an
#     Application event-log entry is also attempted (skipped if not permitted).

param(
    [Parameter(Mandatory)]
    [ValidateSet('Register', 'Unregister', 'List')]
    [string]$Action,

    [string]$Drive,             # e.g. 'C:' -- mandatory for Register (validated below)
    [string]$Policy = 'safe',   # name of a profile under references/policies/<Policy>.json
    [string]$Interval = 'daily',# schedule frequency (currently only 'daily' is implemented)
    [string]$Time = '02:00'     # HH:MM 24h start time
)

# ---- dot-source the cross-platform detection layer (platform.ps1) ----
. (Join-Path $PSScriptRoot 'lib\platform.ps1')

# =====================================================================
# Helpers
# =====================================================================

function Get-RepoRoot {
    return (Get-Item $PSScriptRoot).Parent.FullName
}

# Loads references/policies/<Name>.json and returns the parsed policy object.
# Exits 1 (clear error) if the file is missing or not valid JSON.
function Get-PolicyProfile {
    param([string]$Name)

    $policyPath = Join-Path (Join-Path (Get-RepoRoot) 'references\policies') "$Name.json"
    if (-not (Test-Path -LiteralPath $policyPath)) {
        Write-Error "Policy file not found: $policyPath"
        exit 1
    }
    try {
        $policy = Get-Content -LiteralPath $policyPath -Raw | ConvertFrom-Json
    } catch {
        Write-Error "Failed to parse policy file '$policyPath': $($_.Exception.Message)"
        exit 1
    }
    if (-not $policy.Categories) {
        Write-Error "Policy file '$policyPath' does not define a 'Categories' array."
        exit 1
    }
    return $policy
}

# Parses 'HH:MM' into hour/minute integers; exits 1 on malformed input.
function Get-TimeParts {
    param([string]$TimeString)

    $parts = @($TimeString -split ':')
    if ($parts.Count -ne 2) {
        Write-Error "Invalid -Time '$TimeString' (expected HH:MM, e.g. '02:00')."
        exit 1
    }
    $hour = 0
    $min  = 0
    if (-not [int]::TryParse($parts[0], [ref]$hour) -or -not [int]::TryParse($parts[1], [ref]$min)) {
        Write-Error "Invalid -Time '$TimeString' (expected numeric HH:MM)."
        exit 1
    }
    if ($hour -lt 0 -or $hour -gt 23 -or $min -lt 0 -or $min -gt 59) {
        Write-Error "Invalid -Time '$TimeString' (hour 0-23, minute 0-59)."
        exit 1
    }
    return @{ Hour = $hour; Minute = $min }
}

# Writes the registration summary markdown; returns the summary path.
function Write-RegistrationSummary {
    param(
        [string]$SummaryDrive,
        [string]$PolicyName,
        [string]$ScheduleLabel,
        [string]$CommandText
    )

    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $summaryDir = Join-Path (Join-Path $HOME '.rubbish-cleaner\scheduled') "$SummaryDrive-$stamp"
    New-Item -ItemType Directory -Path $summaryDir -Force | Out-Null
    $summaryPath = Join-Path $summaryDir 'summary.md'

    $lines = @(
        '# rubbish-cleaner scheduled task',
        '',
        "- Drive: $SummaryDrive",
        "- Policy: $PolicyName",
        "- Schedule: $ScheduleLabel",
        "- Registered: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')",
        '',
        '## Scheduled command',
        '',
        '```',
        $CommandText,
        '```'
    )
    $lines | Set-Content -LiteralPath $summaryPath -Encoding UTF8
    Write-Host "Summary written to $summaryPath"
    return $summaryPath
}

# Best-effort Windows Application event log entry. New-EventLog needs admin to
# create the source; both source-creation and write are skipped on failure so a
# non-elevated registration can never crash on the notification step.
function Write-WindowsEventLog {
    param(
        [string]$EventDrive,
        [string]$EventSummary
    )

    try {
        $source = 'rubbish-cleaner'
        if (-not [System.Diagnostics.EventLog]::SourceExists($source)) {
            New-EventLog -LogName Application -Source $source -ErrorAction Stop
        }
        $message = "rubbish-cleaner scheduled task registered for drive $EventDrive. Summary: $EventSummary"
        Write-EventLog -LogName Application -Source $source -EntryType Information -EventId 1001 -Message $message -ErrorAction Stop
    } catch {
        Write-Warning "Event log notification skipped: $($_.Exception.Message)"
    }
}

# Builds the platform-appropriate scheduled command line for a registered run.
# Windows: powershell.exe -Command "& scan; & clean"
# Linux/macOS: pwsh -Command "& scan; & clean"
function Get-ScheduledCommand {
    param([string]$CommandDrive, [string]$CategoryList)

    $repo    = Get-RepoRoot
    $scan    = Join-Path $repo 'scripts\scan-drive.ps1'
    $clean   = Join-Path $repo 'scripts\clean-drive.ps1'
    $shell   = if ($script:IsWin) { 'powershell.exe' } else { 'pwsh' }

    if ($script:IsWin) {
        return @{
            Shell       = $shell
            Argument    = "-NoProfile -ExecutionPolicy Bypass -Command `"& '$scan' -Drive $CommandDrive -Categories $CategoryList; & '$clean' -Drive $CommandDrive -Yes`""
            Display     = "& '$scan' -Drive $CommandDrive -Categories $CategoryList; & '$clean' -Drive $CommandDrive -Yes"
            CronLine    = ''
            CronComment = ''
        }
    }

    # Non-Windows: pwsh, no -ExecutionPolicy needed, but keep -NoProfile.
    return @{
        Shell       = $shell
        Argument    = "-NoProfile -Command `"& '$scan' -Drive $CommandDrive -Categories $CategoryList; & '$clean' -Drive $CommandDrive -Yes`""
        Display     = "& '$scan' -Drive $CommandDrive -Categories $CategoryList; & '$clean' -Drive $CommandDrive -Yes"
        CronLine    = "<MIN> <HOUR> * * * pwsh -NoProfile -File '$scan' -Drive '$CommandDrive' -Categories '$CategoryList' && pwsh -NoProfile -File '$clean' -Drive '$CommandDrive' -Yes"
        CronComment = ''
    }
}

# =====================================================================
# Action: Register
# =====================================================================
if ($Action -eq 'Register') {
    if (-not $Drive) {
        Write-Error 'The -Drive parameter is mandatory for -Action Register.'
        exit 1
    }

    # (a) Windows: enforce administrator privileges BEFORE touching the scheduler.
    if ($script:IsWin) {
        $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
        $isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
        if (-not $isAdmin) {
            Write-Error 'Scheduled task registration requires administrator privileges'
            exit 1
        }
    }

    # (b) Load the policy profile (fails with a clear error if missing/malformed).
    $policy = Get-PolicyProfile -Name $Policy
    $categoryList = (($policy.Categories | ForEach-Object { $_ }) -join ',')

    $timeParts = Get-TimeParts -TimeString $Time
    $scheduleLabel = "daily at $Time"

    if ($script:IsWin) {
        $taskName = "rubbish-cleaner-$Drive"
        $cmd = Get-ScheduledCommand -CommandDrive $Drive -CategoryList $categoryList
        $trigger = New-ScheduledTaskTrigger -Daily -At $Time
        $action  = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $cmd.Argument
        Register-ScheduledTask -TaskName $taskName -Trigger $trigger -Action $action -Force | Out-Null
        Write-Host "Registered scheduled task '$taskName' ($scheduleLabel)."
        $summaryPath = Write-RegistrationSummary -SummaryDrive $Drive -PolicyName $Policy -ScheduleLabel $scheduleLabel -CommandText $cmd.Display
        Write-WindowsEventLog -EventDrive $Drive -EventSummary $summaryPath
    }
    elseif ($script:IsLx) {
        $cmd = Get-ScheduledCommand -CommandDrive $Drive -CategoryList $categoryList
        $cronLine = $cmd.CronLine.Replace('<MIN>', "$($timeParts.Minute)").Replace('<HOUR>', "$($timeParts.Hour)")
        $cronTarget = '/etc/cron.d/rubbish-cleaner'
        $fullText = "# rubbish-cleaner scheduled job for drive $Drive`n$cronLine`n"
        try {
            $fullText | Set-Content -LiteralPath $cronTarget -Encoding UTF8 -ErrorAction Stop
            Write-Host "Wrote $cronTarget"
        } catch {
            # /etc/cron.d not writable (no root) -> append to the user crontab instead.
            $current = @(& crontab -l 2>$null)
            $filtered = @($current | Where-Object { $_ -notmatch "rubbish-cleaner.*-Drive '$Drive'" })
            $filtered += $cronLine
            $tmpCron = Join-Path $HOME '.rubbish-cleaner-crontab.tmp'
            $filtered | Set-Content -LiteralPath $tmpCron -Encoding UTF8
            & crontab $tmpCron
            Remove-Item -LiteralPath $tmpCron -Force
            Write-Host "Appended to user crontab for drive $Drive."
        }
        $summaryPath = Write-RegistrationSummary -SummaryDrive $Drive -PolicyName $Policy -ScheduleLabel $scheduleLabel -CommandText $cronLine
    }
    elseif ($script:IsMac) {
        $cmd = Get-ScheduledCommand -CommandDrive $Drive -CategoryList $categoryList
        $plistDir  = Join-Path $HOME 'Library\LaunchAgents'
        $plistPath = Join-Path $plistDir 'com.rubbish-cleaner.plist'
        New-Item -ItemType Directory -Path $plistDir -Force | Out-Null

        $plistXml = @"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>com.rubbish-cleaner</string>
	<key>ProgramArguments</key>
	<array>
		<string>pwsh</string>
		<string>-NoProfile</string>
		<string>-Command</string>
		<string>& $scan -Drive $Drive -Categories $categoryList; & $clean -Drive $Drive -Yes</string>
	</array>
	<key>StartCalendarInterval</key>
	<dict>
		<key>Hour</key>
		<integer>$($timeParts.Hour)</integer>
		<key>Minute</key>
		<integer>$($timeParts.Minute)</integer>
	</dict>
</dict>
</plist>
"@
        $scan  = Join-Path (Get-RepoRoot) 'scripts\scan-drive.ps1'
        $clean = Join-Path (Get-RepoRoot) 'scripts\clean-drive.ps1'
        $plistXml = $plistXml.Replace('& $scan', "& '$scan'").Replace('& $clean', "& '$clean'")
        $plistXml | Set-Content -LiteralPath $plistPath -Encoding UTF8
        & launchctl unload $plistPath 2>$null | Out-Null
        & launchctl load $plistPath
        Write-Host "Loaded launchd agent from $plistPath ($scheduleLabel)."
        $summaryPath = Write-RegistrationSummary -SummaryDrive $Drive -PolicyName $Policy -ScheduleLabel $scheduleLabel -CommandText $cmd.Display
    }
    else {
        Write-Error 'Unknown platform; scheduled registration is not supported here.'
        exit 1
    }

    exit 0
}

# =====================================================================
# Action: Unregister
# =====================================================================
if ($Action -eq 'Unregister') {
    if ($script:IsWin) {
        if ($Drive) {
            $tasks = @(Get-ScheduledTask -TaskName "rubbish-cleaner-$Drive" -ErrorAction SilentlyContinue)
        } else {
            $tasks = @(Get-ScheduledTask -TaskName 'rubbish-cleaner-*' -ErrorAction SilentlyContinue)
        }
        if ($tasks.Count -eq 0) {
            Write-Host 'No rubbish-cleaner scheduled tasks found.'
        } else {
            foreach ($t in $tasks) {
                Unregister-ScheduledTask -TaskName $t.TaskName -Confirm:$false
                Write-Host "Unregistered scheduled task '$($t.TaskName)'."
            }
        }
    }
    elseif ($script:IsLx) {
        $current = @(& crontab -l 2>$null)
        if ($Drive) {
            $filtered = @($current | Where-Object { $_ -notmatch "rubbish-cleaner.*-Drive '$Drive'" })
        } else {
            $filtered = @($current | Where-Object { $_ -notmatch 'rubbish-cleaner' })
        }
        if ($filtered.Count -eq $current.Count) {
            Write-Host 'No rubbish-cleaner cron entries found.'
        } else {
            $tmpCron = Join-Path $HOME '.rubbish-cleaner-crontab.tmp'
            $filtered | Set-Content -LiteralPath $tmpCron -Encoding UTF8
            & crontab $tmpCron
            Remove-Item -LiteralPath $tmpCron -Force
            Write-Host 'Removed rubbish-cleaner cron entrie(s).'
        }
    }
    elseif ($script:IsMac) {
        $plistPath = Join-Path (Join-Path $HOME 'Library\LaunchAgents') 'com.rubbish-cleaner.plist'
        if (Test-Path -LiteralPath $plistPath) {
            & launchctl unload $plistPath 2>$null | Out-Null
            Remove-Item -LiteralPath $plistPath -Force
            Write-Host "Removed launchd agent $plistPath."
        } else {
            Write-Host 'No rubbish-cleaner launchd agent found.'
        }
    }
    else {
        Write-Error 'Unknown platform; scheduled unregistration is not supported here.'
        exit 1
    }

    exit 0
}

# =====================================================================
# Action: List
# =====================================================================
if ($Action -eq 'List') {
    if ($script:IsWin) {
        $tasks = @(Get-ScheduledTask -TaskName 'rubbish-cleaner-*' -ErrorAction SilentlyContinue)
        if ($tasks.Count -eq 0) {
            Write-Host 'No rubbish-cleaner scheduled tasks registered.'
        } else {
            Write-Host 'rubbish-cleaner scheduled tasks:'
            foreach ($t in $tasks) {
                Write-Host "  $($t.TaskName)  State=$($t.State)"
            }
        }
    }
    elseif ($script:IsLx) {
        $current = @(& crontab -l 2>$null)
        $hits = @($current | Where-Object { $_ -match 'rubbish-cleaner' })
        if ($hits.Count -eq 0) {
            Write-Host 'No rubbish-cleaner cron entries found.'
        } else {
            Write-Host 'rubbish-cleaner cron entries:'
            $hits | ForEach-Object { Write-Host "  $_" }
        }
    }
    elseif ($script:IsMac) {
        $plistPath = Join-Path (Join-Path $HOME 'Library\LaunchAgents') 'com.rubbish-cleaner.plist'
        if (Test-Path -LiteralPath $plistPath) {
            Write-Host "Registered launchd agent: $plistPath"
        } else {
            Write-Host 'No rubbish-cleaner launchd agent found.'
        }
    }
    else {
        Write-Error 'Unknown platform; listing is not supported here.'
        exit 1
    }

    exit 0
}

# Unreachable for a valid -Action (ValidateSet guarantees one of the three above).
exit 0
