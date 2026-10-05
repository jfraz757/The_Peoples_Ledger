<#
Create (or remove) the "PeoplesLedger Monthly Link Check" scheduled task: on the 1st of
every month it runs `pipeline\ledger.py monthly` -- the link-status check, then regenerate
the business pages, then commit and push businesses/ so the new statuses go live.

    powershell -ExecutionPolicy Bypass -File install_monthly_links.ps1              # install / update
    powershell -ExecutionPolicy Bypass -File install_monthly_links.ps1 -Uninstall   # remove
    Start-ScheduledTask -TaskName 'PeoplesLedger Monthly Link Check'                # run it now

Output is appended to data\monthly_links.log.

Why this exists: the link check went unrun from August to October 2026 because it relied
on someone remembering it (and its .sh wrapper pointed at a Python that had moved). Python
is started through the `py` launcher, which lives in C:\Windows and finds whatever Python
is installed, so moving Python again cannot silently break the schedule.

Runs as you, only while you are logged in (git push needs your credentials). If the PC is
off or asleep at 10:00 on the 1st, StartWhenAvailable runs it as soon as it is back.
#>
param([switch]$Uninstall, [string]$Time = '10:00')

$ErrorActionPreference = 'Stop'
$TaskName = 'PeoplesLedger Monthly Link Check'

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    "Removed '$TaskName'."
    return
}

$py = (Get-Command py.exe -ErrorAction Stop).Source
$log = Join-Path $PSScriptRoot 'data\monthly_links.log'
# PYTHONUNBUFFERED keeps the log in order: buffered, the runner's step headers landed
# after the output of the steps they introduce.
$cmdLine = "set PYTHONIOENCODING=utf-8&& set PYTHONUNBUFFERED=1&& `"$py`" -3 pipeline\ledger.py monthly >> `"$log`" 2>&1"

# conhost --headless: no console window sitting open for the several minutes this takes.
$action = New-ScheduledTaskAction -Execute 'conhost.exe' `
    -Argument "--headless cmd.exe /c `"$cmdLine`"" `
    -WorkingDirectory $PSScriptRoot

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) -MultipleInstances IgnoreNew

# New-ScheduledTaskTrigger has no monthly option, and a hand-built MSFT_TaskMonthlyTrigger
# is rejected by Register-ScheduledTask ("The parameter is incorrect") on Windows
# PowerShell 5.1. So schtasks.exe creates the task with the monthly schedule (and a
# placeholder command), then the real action and settings are attached to it.
schtasks.exe /Create /F /TN $TaskName /SC MONTHLY /D 1 /ST $Time /RL LIMITED /IT /TR 'cmd.exe /c exit' | Out-Null
if ($LASTEXITCODE -ne 0) { throw "schtasks.exe could not create '$TaskName' (exit $LASTEXITCODE)." }

Set-ScheduledTask -TaskName $TaskName -Action $action -Settings $settings | Out-Null

$info = Get-ScheduledTaskInfo -TaskName $TaskName
"Installed '$TaskName': runs on the 1st of each month at $Time. Next run: $($info.NextRunTime)"
