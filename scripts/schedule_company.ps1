param([switch]$Install, [switch]$Remove, [string]$Manifest, [switch]$Slack)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
$taskName = 'WayfinderCompanySync'
if ($Remove) { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false; exit }
if (-not $Install) { Write-Output 'Use -Install -Manifest <sandbox manifest> [-Slack], or -Remove.'; exit }
if (-not $Manifest) { throw 'Supply the explicit synthetic sandbox manifest.' }
$taskManifest = (Resolve-Path -LiteralPath $Manifest).Path
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
$taskArgs = '-m wayfinder.company --manifest "' + $taskManifest + '" sync'
if ($Slack) { $taskArgs += ' --slack' }
$taskAction = New-ScheduledTaskAction -Execute $taskPython -Argument $taskArgs -WorkingDirectory $taskRoot
$taskTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 10)
$taskSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 9) -StartWhenAvailable
$taskPrincipal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $taskName -Action $taskAction -Trigger $taskTrigger -Settings $taskSettings -Principal $taskPrincipal -Force
