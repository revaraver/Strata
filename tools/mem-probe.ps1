$ErrorActionPreference='SilentlyContinue'
$vm = Get-CimInstance Win32_OperatingSystem
"OS total GiB {0:N1} free GiB {1:N1}" -f ($vm.TotalVisibleMemorySize/1MB), ($vm.FreePhysicalMemory/1MB)
$cs = Get-CimInstance Win32_PageFileUsage
if ($cs) { $cs | ForEach-Object { "pagefile {0} alloc {1} MB peak {2} MB" -f $_.Name, $_.AllocatedBaseSize, $_.PeakUsage } }
Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First 12 Id,ProcessName,@{n='WS_GiB';e={[math]::Round($_.WorkingSet64/1GB,2)}},@{n='Priv_GiB';e={[math]::Round($_.PrivateMemorySize64/1GB,2)}},@{n='Share_GiB';e={[math]::Round($_.SharedMemorySize64/1GB,2)}} | Format-Table -AutoSize | Out-String -Width 200
