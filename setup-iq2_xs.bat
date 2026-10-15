@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"

set "GGUFDIR=R:\data\llm\SC117\Qwen3.8-Flash-Next-GSQ-RCO-abliterated-GGUF"

if not exist "%~dp0.venv\Scripts\python.exe" (
  echo Strata venv Python not found: %~dp0.venv\Scripts\python.exe
  pause
  exit /b 1
)
if not exist "%GGUFDIR%" (
  echo GGUF folder not found: %GGUFDIR%
  pause
  exit /b 1
)

echo Official Strata setup - SC117 GSQ-RCO abliterated IQ2_XS
echo   GGUF folder: %GGUFDIR%
echo.
echo This runs the official setup.py. It builds the pack index and the tokenizer
echo from the GGUF you already have, then writes strata-*.json and a run-*.bat.
echo It does not re-quantize the model and it does not download the GGUF again.
echo.

"%~dp0.venv\Scripts\python.exe" -u "%~dp0setup.py" --setup --family qwen --model IQ2_XS --gguf-dir "%GGUFDIR%"
set "SETUP_EXIT=%ERRORLEVEL%"

echo.
if "%SETUP_EXIT%"=="0" (
  echo Setup finished. Start the model with the run-*.bat it wrote.
) else (
  echo Setup did not finish successfully - review the output above.
)
pause
exit /b %SETUP_EXIT%
