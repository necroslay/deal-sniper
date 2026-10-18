<#
.SYNOPSIS
  Register a Windows Task Scheduler job that runs the scrape (and deal alerts) daily.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\register_daily_scrape.ps1
  powershell -ExecutionPolicy Bypass -File scripts\register_daily_scrape.ps1 -Time 07:30 -Python C:\Python311\python.exe

  Remove with:  Unregister-ScheduledTask -TaskName DealSniperDailyScrape -Confirm:$false
#>
param(
    [string]$Time = "08:00",
    [string]$TaskName = "DealSniperDailyScrape",
    [string]$Python = (Get-Command python -ErrorAction Stop).Source
)

$repo = Split-Path -Parent $PSScriptRoot

# Run from the repo root so `-m app.scrape` and the .env file resolve.
$action = New-ScheduledTaskAction -Execute $Python -Argument "-m app.scrape" `
    -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
# StartWhenAvailable: if the PC was off at $Time, run as soon as it is back on.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "Deal Sniper: scrape listings and send deal alerts" `
    -Force | Out-Null

Write-Host "Registered '$TaskName' to run daily at $Time using $Python"
Write-Host "Logs: $repo\logs\scrape.log"
Write-Host "Test it now:  Start-ScheduledTask -TaskName $TaskName"
