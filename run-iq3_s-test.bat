@echo off
title Strata IQ3_S [TEST branch 0.1.50-merge]
cd /d "D:\git\Strata-latest"
echo [TEST] engine-test\strata.exe - upstream 0.1.50 merge, same params as production
echo [TEST] port 8080 - stop the production instance first, do NOT run both
rem  the translation monitor (:8097) is a thread inside serve now (config "translation_monitor") - no separate window
rem  new builds land as *.exe.new (the running engine locks the .exe); a restart swaps them in
if exist "%~dp0engine-test\strata.exe.new" (
  move /y "%~dp0engine-test\strata.exe.new" "%~dp0engine-test\strata.exe" >nul || echo [TEST] swap FAILED: close the old window first
  if not exist "%~dp0engine-test\strata.exe.new" echo [TEST] swapped in the new strata.exe
)
if exist "%~dp0engine-test\strata-vision.exe.new" (
  move /y "%~dp0engine-test\strata-vision.exe.new" "%~dp0engine-test\strata-vision.exe" >nul || echo [TEST] swap FAILED: close the old window first
  if not exist "%~dp0engine-test\strata-vision.exe.new" echo [TEST] swapped in the new strata-vision.exe
)
echo.
"%~dp0.venv\Scripts\python.exe" "D:\git\Strata-latest\serve\server.py" "--engine" "strata" "--config" "D:\git\Strata-latest\strata-iq3_s-test.json" "--port" "8080" "--open"
if errorlevel 1 pause
