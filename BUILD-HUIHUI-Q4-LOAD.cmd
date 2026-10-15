@echo off
setlocal
cd /d "%~dp0"
call "D:\BuildTools\VS2022\VC\Auxiliary\Build\vcvars64.bat"
if errorlevel 1 exit /b 1
set "CUDA_PATH=D:\BuildTools\Strata\cuda-13.0.2"
set "PATH=%CUDA_PATH%\bin;C:\Users\revar\AppData\Local\Programs\Python\Python313\Scripts;%PATH%"
"C:\Users\revar\AppData\Local\Programs\Python\Python313\Scripts\cmake.exe" -S "%~dp0." -B "%~dp0build-huihui-q4" -G Ninja -DCMAKE_BUILD_TYPE=Release -DSTRATA_ENABLE_CUDA=ON -DSTRATA_PORTABLE=ON -DSTRATA_NATIVE_EXPERTS=ON -DCMAKE_CUDA_ARCHITECTURES=89 -DSTRATA_BUILD_TESTS=OFF -DSTRATA_MMQ_KQUANTS=OFF -DSTRATA_GGML_DIR="D:\git\Strata-main\build-huihui-q4\_deps\strata_llamacpp-src" -DCMAKE_RUNTIME_OUTPUT_DIRECTORY="%~dp0build-huihui-q4-load" -DCUDAToolkit_ROOT="%CUDA_PATH%" -DCMAKE_CUDA_COMPILER="%CUDA_PATH%\bin\nvcc.exe"
if errorlevel 1 exit /b 1
"C:\Users\revar\AppData\Local\Programs\Python\Python313\Scripts\cmake.exe" --build "%~dp0build-huihui-q4" --target strata --parallel 4
exit /b %errorlevel%
