<#
Create (or remove) the "PeoplesLedger Pending Alert" scheduled task that runs
notify_pending.ps1 every 10 minutes and at logon.

    powershell -ExecutionPolicy Bypass -File install_pending_alert.ps1              # install / update
    powershell -ExecutionPolicy Bypass -File install_pending_alert.ps1 -Uninstall   # remove

Runs only while you're logged in (a notification needs a desktop to appear on). Won't wake a
sleeping PC; StartWhenAvailable makes it check as soon as the PC wakes instead.
Launched through pythonw.exe + run_hidden.pyw so no window flashes up on each run. The
previous `conhost.exe --headless powershell.exe` launch still flashed a window on Windows 11.
#>
param([switch]$Uninstall, [int]$Minutes = 10)

$TaskName = 'PeoplesLedger Pending Alert'

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    "Removed '$TaskName'."
    return
}

$script = Join-Path $PSScriptRoot 'notify_pending.ps1'

# pythonw.exe has no console of its own; run_hidden.pyw starts PowerShell with CREATE_NO_WINDOW.
$pythonw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $pythonw) { $pythonw = 'C:\Python314\pythonw.exe' }
if (-not (Test-Path $pythonw)) { throw "pythonw.exe not found; install Python or edit this path." }
$launcher = Join-Path $PSScriptRoot 'run_hidden.pyw'

$action = New-ScheduledTaskAction -Execute $pythonw `
    -Argument "`"$launcher`" `"$script`"" `
    -WorkingDirectory $PSScriptRoot

$triggers = @(
    (New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes $Minutes)),
    (New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME")
)

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 2) -MultipleInstances IgnoreNew

$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers -Settings $settings `
    -Principal $principal -Description "Windows notification when The People's Ledger has a new pending submission. See notify_pending.ps1." `
    -Force | Out-Null

"Installed '$TaskName': checks every $Minutes minutes and at logon."
