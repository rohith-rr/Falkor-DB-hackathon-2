param([switch]$Install, [switch]$Remove, [string]$Manifest, [string]$Graph, [switch]$Slack, [switch]$GitHub, [int]$IntervalMinutes = 10)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
$taskName = 'WayfinderCompanySync'
if ($Remove) { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false; exit }
if (-not $Install) { Write-Output 'Use -Install -Manifest <sandbox manifest> -Graph <graph name> [-Slack] [-GitHub] [-IntervalMinutes 10], or -Remove.'; exit }
if (-not $Manifest) { throw 'Supply the explicit synthetic sandbox manifest.' }
# Without an explicit graph the CLI default (wayfinder_company_demo) would receive live connector records.
if (-not $Graph) { throw 'Supply the target graph, e.g. -Graph wayfinder_company_live_demo.' }
$taskManifest = (Resolve-Path -LiteralPath $Manifest).Path
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
# watch --iterations 1 runs one sync and appends its change set to data\sync-log-<graph>.jsonl.
$taskArgs = '-m wayfinder.company --graph "' + $Graph + '" --manifest "' + $taskManifest + '" watch --iterations 1'
if ($Slack) { $taskArgs += ' --slack' }
if ($GitHub) { $taskArgs += ' --github' }
$taskAction = New-ScheduledTaskAction -Execute $taskPython -Argument $taskArgs -WorkingDirectory $taskRoot
$taskTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)
$taskSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 9) -StartWhenAvailable
$taskPrincipal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $taskName -Action $taskAction -Trigger $taskTrigger -Settings $taskSettings -Principal $taskPrincipal -Force
