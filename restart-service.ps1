# Restart the Strata service tree without touching RAM-backed unload semantics.
$ErrorActionPreference = "SilentlyContinue"
$roots = @(Get-CimInstance Win32_Process | Where-Object {
  $_.Name -eq 'python.exe' -and $_.CommandLine -like '*D:\git\Strata-latest\serve\server.py*'
} | Select-Object -ExpandProperty ProcessId)
foreach ($pid in $roots) {
  Write-Output ("taskkill service tree pid " + $pid)
  taskkill.exe /PID $pid /T /F | Out-Null
}
Get-Process strata, strata-vision -ErrorAction SilentlyContinue | ForEach-Object {
  Write-Output ("taskkill engine " + $_.Id)
  taskkill.exe /PID $_.Id /T /F | Out-Null
}
Start-Sleep -Seconds 3
Start-Process -FilePath "D:\git\Strata-latest\run-iq3_s.bat" -WorkingDirectory "D:\git\Strata-latest"
Write-Output "service relaunched"
