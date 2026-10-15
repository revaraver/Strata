@echo off
setlocal
title Strata Official Calibration - Swift IQ2_XS
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "CONFIG=%~dp0strata-swift-iq2_xs.json"
set "PACK=D:\git\Strata-data\packs\swift-iq2_xs"
set "MODEL_FIRST=R:\data\llm\huihui-ai\Huihui-Qwen3.8-Flash-Next-abliterated-GGUF\Swift-Qwen3.8-Flash-Next-GSQ-RCO-IQ2_XS-00001-of-00002.gguf"
set "MODEL_SECOND=R:\data\llm\huihui-ai\Huihui-Qwen3.8-Flash-Next-abliterated-GGUF\Swift-Qwen3.8-Flash-Next-GSQ-RCO-IQ2_XS-00002-of-00002.gguf"

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
if not exist "%MODEL_FIRST%" (
  echo Model shard not found: %MODEL_FIRST%
  pause
  exit /b 1
)
if not exist "%MODEL_SECOND%" (
  echo Model shard not found: %MODEL_SECOND%
  pause
  exit /b 1
)

if not exist "%PACK%\native_experts.txt" goto PREPARE_PACK
if not exist "%PACK%\tokenizer\vocab.json" goto PREPARE_PACK
goto CALIBRATE

:PREPARE_PACK
echo Preparing the Swift IQ2_XS pack for first use...
echo Pack: %PACK%
"%~dp0.venv\Scripts\python.exe" -u "%~dp0tools\iq_pack.py" --gguf "%MODEL_FIRST%" --out "%PACK%"
if errorlevel 1 (
  echo Pack preparation failed. Review the error above.
  pause
  exit /b 1
)
echo.

:CALIBRATE
echo Official Strata calibration: Swift IQ2_XS
echo Config: %CONFIG%
echo Close other Strata and NInfer model services before running calibration.
echo Run one calibration window at a time. Model reloads can take several minutes.
echo Selected settings are saved automatically. This script does not start the API server.
echo.

"%~dp0.venv\Scripts\python.exe" -u -c "import sys, shutil; from pathlib import Path; from datetime import datetime; import setup; p=Path(sys.argv[1]); stamp=datetime.now().isoformat(timespec='microseconds').replace(':','').replace('-',''); b=p.with_name(p.name+'.before-calibration-'+stamp); shutil.copy2(p,b); print('Config backup: '+str(b), flush=True); sys.exit(0 if setup.calibrate_config(p) else 1)" "%CONFIG%"
set "CALIBRATION_EXIT_CODE=%ERRORLEVEL%"
echo.
if "%CALIBRATION_EXIT_CODE%"=="0" (
  echo Calibration completed. Settings saved to strata-swift-iq2_xs.json.
) else (
  echo Calibration did not finish successfully. Review the error above.
)
pause
exit /b %CALIBRATION_EXIT_CODE%
