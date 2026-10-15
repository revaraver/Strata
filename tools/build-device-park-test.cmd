@echo off
setlocal
cd /d "%~dp0.."
call "D:\BuildTools\VS2022\VC\Auxiliary\Build\vcvars64.bat"
if errorlevel 1 exit /b 1
set "CUDA_PATH=D:\BuildTools\Strata\cuda-13.0.2"
set "PATH=%CUDA_PATH%\bin;%PATH%"
"C:\Users\revar\AppData\Local\Programs\Python\Python313\Scripts\cmake.exe" --build build-0.1.44.1 --target device_park_test strata --parallel 24
if errorlevel 1 exit /b 1
build-0.1.44.1-out\device_park_test.exe
exit /b %errorlevel%
