@echo off
setlocal
title Strata 0.1.40.3 - Official calibration for all 3 models
cd /d "%~dp0"
"%~dp0.venv\Scripts\python.exe" -u "%~dp0local-upgrade\calibrate_all.py"
if errorlevel 1 (
  echo Calibration did not finish successfully. Check local-upgrade\official-calibration.
  pause
  exit /b 1
)
echo All three models calibrated and saved.
pause
