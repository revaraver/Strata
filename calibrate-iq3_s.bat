@echo off
setlocal
title Strata Official Calibration - SC117 IQ3_S
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "CONFIG=%~dp0strata-iq3_s.json"

if not exist "%~dp0.venv\Scripts\python.exe" (
  echo Strata venv Python not found: %~dp0.venv\Scripts\python.exe
  pause
  exit /b 1
)
if not exist "%CONFIG%" (
  echo Config not found: %CONFIG%
  pause
  exit /b 1
)

echo Official Strata calibration: SC117 IQ3_S
echo Config: %CONFIG%
echo Close other Strata and NInfer model services before running calibration.
echo Run one calibration window at a time. Model reloads can take several minutes.
echo Selected settings are saved automatically. This script does not start the API server.
echo.

"%~dp0.venv\Scripts\python.exe" -u -c "import sys, shutil; from pathlib import Path; from datetime import datetime; import setup; p=Path(sys.argv[1]); stamp=datetime.now().isoformat(timespec='microseconds').replace(':','').replace('-',''); b=p.with_name(p.name+'.before-calibration-'+stamp); shutil.copy2(p,b); print('Config backup: '+str(b), flush=True); sys.exit(0 if setup.calibrate_config(p) else 1)" "%CONFIG%"
set "CALIBRATION_EXIT_CODE=%ERRORLEVEL%"
echo.
if "%CALIBRATION_EXIT_CODE%"=="0" (
  echo Calibration completed. Settings saved to strata-iq3_s.json.
) else (
  echo Calibration did not finish successfully. Review the error above.
)
pause
exit /b %CALIBRATION_EXIT_CODE%
