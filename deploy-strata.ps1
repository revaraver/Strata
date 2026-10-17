$ErrorActionPreference = 'Continue'
$dst = 'D:\git\Strata-latest\engine\strata.exe'
$src = 'D:\git\Strata-latest\build-0.1.44.1-out\strata.exe'
for ($i = 0; $i -lt 60; $i++) {
    Get-Process strata -ErrorAction SilentlyContinue | Stop-Process -Force
    try {
        Copy-Item -Force $src $dst -ErrorAction Stop
        Write-Output "COPY_OK after $i retries"
        exit 0
    } catch {
        Start-Sleep -Milliseconds 250
    }
}
Write-Output 'COPY_FAILED after 60 retries'
exit 1
