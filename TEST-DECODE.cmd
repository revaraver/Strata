@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0.venv\Scripts\python.exe" goto missing_python
if not "%~1"=="" goto run_args

set "BENCH_CONTEXT=14096"
set "BENCH_OUTPUT=4096"
set "BENCH_ROUNDS=3"
set "BENCH_WARMUP=1"
set "BENCH_LABEL=manual"
echo Strata HTTP decode benchmark - GPU settings are never changed.
echo Endpoint: http://127.0.0.1:8080   API key: 123
echo Token lengths accept 4096, 4k, 32k, etc. k = 1024.
echo.
set /p "BENCH_CONTEXT=Input context tokens [%BENCH_CONTEXT%]: "
set /p "BENCH_OUTPUT=Maximum output tokens [%BENCH_OUTPUT%]: "
set /p "BENCH_ROUNDS=Measured rounds [3]: "
set /p "BENCH_WARMUP=Warmup rounds [1]: "
set /p "BENCH_LABEL=Result label [manual]: "
"%~dp0.venv\Scripts\python.exe" -X utf8 "tools\benchmark_decode.py" --context "%BENCH_CONTEXT%" --output "%BENCH_OUTPUT%" --rounds "%BENCH_ROUNDS%" --warmup "%BENCH_WARMUP%" --label "%BENCH_LABEL%"
set "BENCH_EXIT=%errorlevel%"
echo.
pause
exit /b %BENCH_EXIT%

:run_args
"%~dp0.venv\Scripts\python.exe" -X utf8 "tools\benchmark_decode.py" %*
exit /b %errorlevel%

:missing_python
echo Missing %~dp0.venv\Scripts\python.exe.
pause
exit /b 1
