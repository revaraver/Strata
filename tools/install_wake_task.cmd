@echo off
schtasks /create /f /tn StrataWakeSnapshot /tr "cmd /c D:\git\Strata-latest\.venv\Scripts\python.exe D:\git\Strata-latest\tools\state_snapshot.py D:\git\Strata-latest\data\snapshots\wake-snapshot.json" /sc ONEVENT /ec System /ed "<QueryList><Query Id=\"0\" Path=\"System\"><Select Path=\"System\">*[System[Provider[@Name=\u0027Microsoft-Windows-Kernel-Power\u0027] and EventID=131]]</Select></Query></QueryList>"
schtasks /query /tn StrataWakeSnapshot /fo LIST
