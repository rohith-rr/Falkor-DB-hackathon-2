param([switch]$Install, [switch]$Remove, [string]$TaskName = 'Wayfinder Expertise v2')
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $repoRoot '.venv\Scripts\python.exe'
$refreshPath = Join-Path $repoRoot 'wayfinder\expertise_refresh.py'
if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    return
}
if (-not $Install) {
    Write-Output 'Use -Install to register hourly refresh, or -Remove to unregister it.'
    return
}
if (-not (Test-Path -LiteralPath $pythonPath) -or -not (Test-Path -LiteralPath $refreshPath)) {
    throw 'Python executable or refresh script missing.'
}
$action = New-ScheduledTaskAction -Execute $pythonPath -Argument ('"' + $refreshPath + '"') -WorkingDirectory $repoRoot
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5) -RepetitionInterval (New-TimeSpan -Hours 1)
$taskSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $taskSettings -Principal $principal -Force
