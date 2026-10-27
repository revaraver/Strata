Get-Process strata,python -ErrorAction SilentlyContinue |
  Select-Object Name,Id,
    @{n='WorkingSetGB';e={[math]::Round($_.WorkingSet64/1GB,1)}},
    @{n='CommitGB';e={[math]::Round($_.PrivateMemorySize64/1GB,1)}} |
  Format-Table -AutoSize
$cs = (Get-Counter '\Memory\Committed Bytes').CounterValue / 1GB
$ls = (Get-Counter '\Memory\Commit Limit').CounterValue / 1GB
Write-Output ('CommitUsed ' + [math]::Round($cs,1) + ' GB / Limit ' + [math]::Round($ls,1) + ' GB')
