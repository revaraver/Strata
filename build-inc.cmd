@echo off
setlocal
cd /d "D:\git\Strata-latest"
call "D:\BuildTools\VS2022\VC\Auxiliary\Build\vcvars64.bat" >nul
if errorlevel 1 exit /b 1
set "CUDA_PATH=D:\BuildTools\Strata\cuda-13.0.2"
set "PATH=%CUDA_PATH%\bin;%PATH%"
"C:\Users\revar\AppData\Local\Programs\Python\Python313\Scripts\cmake.exe" --build "D:\git\Strata-latest\build-0.1.44.1" --target strata --parallel 24
exit /b %errorlevel%
