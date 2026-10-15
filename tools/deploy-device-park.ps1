$ErrorActionPreference = 'Stop'
$root = 'D:\git\Strata-latest'
$servers = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*serve*server.py*' -and $_.CommandLine -like '*strata-iq3_s.json*' })
# Only the actual listener is stopped, after its identity matches this repository/config.
$listeners = @(Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique)
foreach ($listener in $listeners) {
    if (-not ($servers | Where-Object ProcessId -eq $listener)) { throw "Port 8080 belongs to an unexpected process ($listener)." }
    & taskkill.exe /PID $listener /T /F
    if ($LASTEXITCODE -ne 0) { throw 'Failed to stop the previous local model.' }
}
# Termination can return before CUDA finishes releasing tens of GiB of registered pages.
$ending = @(Get-Process strata -ErrorAction SilentlyContinue | Where-Object Path -eq (Join-Path $root 'engine\strata.exe'))
foreach ($process in $ending) { if (-not $process.WaitForExit(15000)) { throw 'Previous engine is still releasing memory.' } }
$oldExe = Join-Path $root 'engine\strata.exe'
$backup = Join-Path $root 'engine\strata.before-device-park.exe'
if (-not (Test-Path $backup)) { Copy-Item $oldExe $backup }
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    try { Copy-Item (Join-Path $root 'build-0.1.44.1-out\strata.exe') $oldExe -Force; break }
    catch [System.IO.IOException] {
        if ($attempt -eq 29) { throw }
        Start-Sleep -Milliseconds 200
    }
}
Get-FileHash $oldExe -Algorithm SHA256 | Select-Object Algorithm,Hash | ConvertTo-Json -Compress
& (Join-Path $root 'tools\start-visible-iq3.ps1')
