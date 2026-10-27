# Installs the wake-forensics task: on Kernel-Power 131 (resume from sleep),
# run the state snapshot collector so the post-wake memory state is captured
# automatically. Re-runnable (-Force).
$ErrorActionPreference = 'Stop'
$trigger = New-CimInstance -ClassName MSFT_TaskEventTrigger `
  -Namespace Root/Microsoft/Windows/TaskScheduler -ClientOnly -Property @{
    Enabled = $true
    SubscriptionXML = '<QueryList><Query Id="0" Path="System"><Select Path="System">*[System[Provider[@Name=''Microsoft-Windows-Kernel-Power''] and EventID=131]]</Select></Query></QueryList>'
  }
$action = New-ScheduledTaskAction `
  -Execute 'D:\git\Strata-latest\.venv\Scripts\python.exe' `
  -Argument 'D:\git\Strata-latest\tools\state_snapshot.py D:\git\Strata-latest\data\snapshots\wake-snapshot.json' `
  -WorkingDirectory 'D:\git\Strata-latest'
Register-ScheduledTask -TaskName StrataWakeSnapshot -Trigger $trigger -Action $action `
  -Description 'Strata post-wake memory forensics snapshot' -Force | Out-Null
Get-ScheduledTask -TaskName StrataWakeSnapshot | Select-Object TaskName, State | Format-Table -AutoSize
Write-Output 'wake snapshot task installed: fires on Kernel-Power 131 (resume)'
