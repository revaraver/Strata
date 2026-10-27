# Installs the wake-forensics task via a full XML task definition (the CIM
# event-trigger route trips MismatchPSTypeName on this box). Fires the state
# snapshot collector on Kernel-Power 131 (resume from sleep). Re-runnable.
$ErrorActionPreference = 'Stop'
$sub = '<QueryList><Query Id="0" Path="System"><Select Path="System">*[System[Provider[@Name=''Microsoft-Windows-Kernel-Power''] and EventID=131]]</Select></Query></QueryList>'
$subEsc = $sub -replace '<', '&lt;' -replace '>', '&gt;'
$xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers>
    <EventTrigger>
      <Enabled>true</Enabled>
      <Subscription>$subEsc</Subscription>
    </EventTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>$env:USERNAME</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <ExecutionTimeLimit>PT1H</ExecutionTimeLimit>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>D:\git\Strata-latest\.venv\Scripts\python.exe</Command>
      <Arguments>D:\git\Strata-latest\tools\state_snapshot.py D:\git\Strata-latest\data\snapshots\wake-snapshot.json</Arguments>
      <WorkingDirectory>D:\git\Strata-latest</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@
Register-ScheduledTask -TaskName StrataWakeSnapshot -Xml $xml -Force | Out-Null
Get-ScheduledTask -TaskName StrataWakeSnapshot | Select-Object TaskName, State | Format-Table -AutoSize
Write-Output 'wake snapshot task installed: fires on Kernel-Power 131 (resume)'
