[CmdletBinding(SupportsShouldProcess = $true)]
param()

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Scheduler = Join-Path $Root "scripts\scheduler_tick.py"
$Config = Join-Path $Root "config\scheduler.yaml"
$TaskName = "JobFinderOS Scheduler"

if (-not (Test-Path $Python)) {
    throw "Python virtualenv not found: $Python"
}

if (-not (Test-Path $Scheduler)) {
    throw "Scheduler not found: $Scheduler"
}

$IntervalSeconds = 1800

if (Test-Path $Config) {
    $match = Select-String `
        -Path $Config `
        -Pattern '^\s*start_interval_seconds:\s*(\d+)\s*$' |
        Select-Object -First 1

    if ($match) {
        $IntervalSeconds = [int]$match.Matches[0].Groups[1].Value
    }
}

if ($env:SCHEDULER_INTERVAL_SECONDS) {
    $IntervalSeconds = [int]$env:SCHEDULER_INTERVAL_SECONDS
}

if ($IntervalSeconds -lt 60) {
    throw "Windows Task Scheduler requires an interval of at least 60 seconds."
}

if (($IntervalSeconds % 60) -ne 0) {
    throw "SCHEDULER_INTERVAL_SECONDS must be a whole number of minutes on Windows."
}

$Interval = New-TimeSpan -Minutes ($IntervalSeconds / 60)

$Action = New-ScheduledTaskAction `
    -Execute $Python `
    -Argument "`"$Scheduler`"" `
    -WorkingDirectory $Root

# A long-lived repeating trigger. Re-running this installer refreshes it.
$Trigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval $Interval `
    -RepetitionDuration (New-TimeSpan -Days 3650)

$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

$CurrentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

$Principal = New-ScheduledTaskPrincipal `
    -UserId $CurrentUser `
    -LogonType Interactive `
    -RunLevel Limited

if ($PSCmdlet.ShouldProcess($TaskName, "Register Windows scheduled task")) {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Description "Runs the JobFinderOS master scheduler." `
        -Action $Action `
        -Trigger $Trigger `
        -Settings $Settings `
        -Principal $Principal `
        -Force |
        Out-Null
}

Write-Host ""
Write-Host "JobFinderOS Windows scheduler"
Write-Host "  Task:     $TaskName"
Write-Host "  Python:   $Python"
Write-Host "  Scheduler:$Scheduler"
Write-Host "  Interval: $IntervalSeconds seconds"
Write-Host ""
Write-Host "Dry run:"
Write-Host "  `"$Python`" `"$Scheduler`" --dry-run"
Write-Host ""
Write-Host "Inspect task:"
Write-Host "  Get-ScheduledTask -TaskName `"$TaskName`""