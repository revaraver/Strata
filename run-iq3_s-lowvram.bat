@echo off

title Strata IQ3_S LowVRAM

cd /d "D:\git\Strata-latest"

"%~dp0.venv\Scripts\python.exe" "D:\git\Strata-latest\serve\server.py" "--engine" "strata" "--config" "D:\git\Strata-latest\strata-iq3_s-lowvram.json" "--port" "8080" "--open"

if errorlevel 1 pause

