<#
Show a Windows notification when there are pending submissions you haven't been told about.

Run by the "PeoplesLedger Pending Alert" scheduled task every 10 minutes (set up by
install_pending_alert.ps1). Each run makes one small request to Supabase and exits.

Each pending submission alerts once. The IDs already announced are kept in
data/.notify_pending_state.json (data/ is gitignored); approving or rejecting a submission
drops it from that list. Clicking the notification opens admin.html.

Reads SUPABASE_URL and the service-role key out of admin.html, same as backup_supabase.py,
so the key lives in exactly one place on disk. The anon key cannot read submissions.

Failures are appended to data/notify_pending.log; nothing is shown on screen for them.

    powershell -ExecutionPolicy Bypass -File notify_pending.ps1          # normal run
    powershell -ExecutionPolicy Bypass -File notify_pending.ps1 -Test    # show a sample notification
#>
param([switch]$Test)

$ErrorActionPreference = 'Stop'

$BaseDir   = $PSScriptRoot
$AdminFile = Join-Path $BaseDir 'admin.html'
$DataDir   = Join-Path $BaseDir 'data'
$StateFile = Join-Path $DataDir '.notify_pending_state.json'
$LogFile   = Join-Path $DataDir 'notify_pending.log'

# Windows PowerShell's registered app ID. Toasts need one; borrowing this avoids registering our own.
$AppId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'

function Write-Log($msg) {
    if (-not (Test-Path $DataDir)) { New-Item -ItemType Directory -Path $DataDir | Out-Null }
    Add-Content -Path $LogFile -Value ("{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg) -Encoding utf8
}

function Show-Toast($title, $body) {
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
    [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null

    # Business names are submitter-controlled text, so they must be XML-escaped.
    $t = [System.Security.SecurityElement]::Escape($title)
    $b = [System.Security.SecurityElement]::Escape($body)
    $launch = [System.Security.SecurityElement]::Escape(([System.Uri]$AdminFile).AbsoluteUri)

    $xml = New-Object Windows.Data.Xml.Dom.XmlDocument
    $xml.LoadXml(@"
<toast activationType="protocol" launch="$launch" scenario="reminder">
  <visual><binding template="ToastGeneric"><text>$t</text><text>$b</text></binding></visual>
  <actions>
    <action content="Open admin page" activationType="protocol" arguments="$launch"/>
    <action content="Dismiss" activationType="system" arguments="dismiss"/>
  </actions>
</toast>
"@)
    $toast = New-Object Windows.UI.Notifications.ToastNotification $xml
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($AppId).Show($toast)
}

try {
    if ($Test) {
        Show-Toast 'New submission: TEST Bakery' 'This is a test notification. Click to open the admin page.'
        return
    }

    if (-not (Test-Path $AdminFile)) { throw "admin.html not found at $AdminFile" }
    $html = Get-Content -Path $AdminFile -Raw -Encoding utf8
    $url = [regex]::Match($html, 'SUPABASE_URL\s*=\s*"([^"]+)"').Groups[1].Value
    $key = [regex]::Match($html, 'SUPABASE_ADMIN_KEY\s*=\s*"([^"]+)"').Groups[1].Value
    if (-not $url -or -not $key) { throw 'Could not parse SUPABASE_URL / SUPABASE_ADMIN_KEY from admin.html' }

    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    # PowerShell 5.1's Invoke-RestMethod hands back a JSON array as ONE object, so an empty
    # result wrapped in @() becomes a one-item list holding an empty array -- which reads as a
    # nameless pending submission. Piping through ForEach-Object unrolls it properly.
    $pending = @(Invoke-RestMethod -TimeoutSec 30 `
        -Uri "$url/rest/v1/submissions?status=eq.pending&select=id,business_name,submission_type&order=submitted_at.asc" `
        -Headers @{ apikey = $key; Authorization = "Bearer $key" } | ForEach-Object { $_ })

    $announced = @()
    if (Test-Path $StateFile) { $announced = @((Get-Content $StateFile -Raw | ConvertFrom-Json).announced | Where-Object { $_ -ne $null }) }

    $fresh = @($pending | Where-Object { $announced -notcontains $_.id })

    if ($fresh.Count -gt 0) {
        $names = ($fresh | ForEach-Object { if ($_.business_name) { $_.business_name } else { '(no name)' } }) -join ', '
        if ($fresh.Count -eq 1) {
            $kind  = if ($fresh[0].submission_type -eq 'update') { 'correction' } else { 'submission' }
            $title = "New $kind`: $names"
            $body  = "$($pending.Count) pending in total. Click to review."
        } else {
            $title = "$($fresh.Count) new submissions"
            $body  = "$names`n$($pending.Count) pending in total. Click to review."
        }
        Show-Toast $title $body
    }

    # Keep only IDs that are still pending, so the file never grows and a resolved ID is forgotten.
    if (-not (Test-Path $DataDir)) { New-Item -ItemType Directory -Path $DataDir | Out-Null }
    ConvertTo-Json -InputObject @{ announced = @($pending | ForEach-Object { $_.id }) } | Set-Content -Path $StateFile -Encoding utf8
}
catch {
    Write-Log "ERROR: $($_.Exception.Message)"
    exit 1
}
