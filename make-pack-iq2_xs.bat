@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"

set "GGUF=R:\data\llm\SC117\Qwen3.8-Flash-Next-GSQ-RCO-abliterated-GGUF\Qwen3.8-Flash-Next-GSQ-RCO-abliterated-IQ2_XS-00001-of-00002.gguf"
set "PACK=D:\git\Strata-data\packs\iq2_xs"

if not exist "%~dp0.venv\Scripts\python.exe" (
  echo Strata venv Python not found: %~dp0.venv\Scripts\python.exe
  pause
  exit /b 1
)
if not exist "%GGUF%" (
  echo GGUF not found: %GGUF%
  pause
  exit /b 1
)

echo Building the Strata pack index for SC117 IQ2_XS
echo   GGUF: %GGUF%
echo   Pack: %PACK%
echo This writes an index only - it does not re-quantize the model.
echo.

"%~dp0.venv\Scripts\python.exe" -u "%~dp0tools\iq_pack.py" --gguf "%GGUF%" --out "%PACK%"
set "PACK_EXIT=%ERRORLEVEL%"
if not "%PACK_EXIT%"=="0" (
  echo.
  echo iq_pack.py failed with %PACK_EXIT%
  pause
  exit /b %PACK_EXIT%
)

if not exist "%PACK%\tokenizer\vocab.json" (
  echo.
  echo Building the tokenizer
  "%~dp0.venv\Scripts\python.exe" -u "%~dp0tools\strata_tokenizer.py" --gguf "%GGUF%" --out "%PACK%"
  set "TOK_EXIT=%ERRORLEVEL%"
  if not "%TOK_EXIT%"=="0" (
    echo strata_tokenizer.py failed with %TOK_EXIT%
    pause
    exit /b %TOK_EXIT%
  )
)

echo.
echo Pack ready: %PACK%
pause
exit /b 0
