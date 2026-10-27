$ErrorActionPreference = 'Stop'
$root = 'D:\git\Strata-latest'
# 1) stop the listener on 8080 (the python server; its children die with it)
$listeners = @(Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique)
foreach ($p in $listeners) { & taskkill.exe /PID $p /T /F }
# 2) wait for the engine to actually release the exe
$deadline = [datetime]::Now.AddSeconds(20)
while ((Get-Process strata -ErrorAction SilentlyContinue | Where-Object Path -eq (Join-Path $root 'engine\strata.exe'))) {
    if ([datetime]::Now -gt $deadline) { throw 'engine still alive after 20s' }
    Start-Sleep -Milliseconds 500
}
# 3) swap in the staged exe (arg: stage file; default the 10-15 backup)
$src = if ($args[0]) { Join-Path $root $args[0] } else { Join-Path $root 'engine\stage\strata.exe' }
Copy-Item $src (Join-Path $root 'engine\strata.exe') -Force
& Get-FileHash (Join-Path $root 'engine\strata.exe') -Algorithm SHA256 | Select-Object -ExpandProperty Hash
# 4) start
& (Join-Path $root 'tools\start-visible-iq3.ps1')
