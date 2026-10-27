@echo off
title Strata IQ3_S [TEST branch 0.1.50-merge]
cd /d "D:\git\Strata-latest"
echo [TEST] engine-test\strata.exe - upstream 0.1.50 merge, same params as production
echo [TEST] port 8080 - stop the production instance first, do NOT run both
netstat -an | find ":8097 " >nul || start /min "" "%~dp0.venv\Scripts\python.exe" "%~dp0tools\translation_monitor.py"
echo.
"%~dp0.venv\Scripts\python.exe" "D:\git\Strata-latest\serve\server.py" "--engine" "strata" "--config" "D:\git\Strata-latest\strata-iq3_s-test.json" "--port" "8080" "--open"
if errorlevel 1 pause
