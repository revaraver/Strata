$ErrorActionPreference = 'Continue'
$G = 1GB
$os = Get-CimInstance Win32_OperatingSystem
$used = ($os.TotalVisibleMemorySize - $os.FreePhysicalMemory) / 1MB
Write-Output ("RAM total {0:N1} GB, in use {1:N1} GB" -f ($os.TotalVisibleMemorySize/1MB), $used)

$all = Get-Process -ErrorAction SilentlyContinue
$sum = ($all | Measure-Object WorkingSet64 -Sum).Sum / $G
Write-Output ("sum of ALL process working sets (shared double-counted): {0:N1} GB" -f $sum)

Write-Output "--- top 20 by working set ---"
$all | Sort-Object WorkingSet64 -Descending | Select-Object -First 20 Name,Id,
  @{n='WS_GB';e={[math]::Round($_.WorkingSet64/$G,2)}},
  @{n='Commit_GB';e={[math]::Round($_.PrivateMemorySize64/$G,2)}} |
  Format-Table -AutoSize

Write-Output "--- WSL / VM related ---"
Get-Process vmmem,vmmemWSL,vmmemc,vmwp,docker*,WslService -ErrorAction SilentlyContinue |
  Select-Object Name,Id,@{n='WS_GB';e={[math]::Round($_.WorkingSet64/$G,2)}} | Format-Table -AutoSize

Write-Output "--- kernel pools / cache ---"
foreach ($n in @('Pool Nonpaged Bytes','Pool Paged Bytes','Cache Bytes','Standby Cache Normal Resident Bytes')) {
  try {
    $v = (Get-Counter "\Memory\$n" -ErrorAction Stop).CounterSamples[0].CookedValue
    Write-Output ($n + ': ' + [math]::Round($v/$G,1) + ' GB')
  } catch {
    Write-Output ($n + ': unavailable')
  }
}
