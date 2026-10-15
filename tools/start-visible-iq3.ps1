$ErrorActionPreference = 'Stop'
$root = 'D:\git\Strata-latest'
$connections = @(Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue)
if ($connections.Count) { throw 'Port 8080 is already in use; close the existing Strata window before starting another.' }
$proc = Start-Process -FilePath $env:ComSpec -WorkingDirectory $root -ArgumentList '/d /c call "D:\git\Strata-latest\run-iq3_s.bat"' -WindowStyle Normal -PassThru
[pscustomobject]@{ ConsolePID=$proc.Id; StartedFromWindows=$true; Window='Strata IQ3_S'; Launcher=(Join-Path $root 'run-iq3_s.bat') } | ConvertTo-Json -Compress
