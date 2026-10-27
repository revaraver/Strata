@echo off
call "D:\BuildTools\VS2022\VC\Auxiliary\Build\vcvars64.bat" >nul
set "CMAKE=D:\git\Strata-latest\.venv\Lib\site-packages\cmake\data\bin\cmake.exe"
set "NINJA=D:\git\Strata-main\.venv\Scripts\ninja.exe"
"%CMAKE%" -S D:/git/Strata-latest -B D:/git/Strata-latest/build-test -G Ninja ^
  -DCMAKE_MAKE_PROGRAM="%NINJA%" ^
  -DCMAKE_BUILD_TYPE=Release ^
  -DCMAKE_CUDA_ARCHITECTURES=89 ^
  -DCMAKE_CUDA_COMPILER=D:/BuildTools/Strata/cuda-13.0.2/bin/nvcc.exe ^
  -DCUDAToolkit_ROOT=D:/BuildTools/Strata/cuda-13.0.2 ^
  -DSTRATA_ENABLE_CUDA=ON -DSTRATA_ENABLE_HIP=OFF -DSTRATA_ENABLE_SYCL=OFF ^
  -DSTRATA_BUILD_TESTS=OFF -DSTRATA_BUILD_CONVERSATION_TESTS=OFF ^
  -DBUILD_SHARED_LIBS=OFF ^
  -DSTRATA_GGML_DIR=D:/git/Strata-main/build-huihui-q4/_deps/strata_llamacpp-src ^
  -DCMAKE_RUNTIME_OUTPUT_DIRECTORY=D:/git/Strata-latest/build-test-out
if errorlevel 1 exit /b 1
"%NINJA%" -C D:/git/Strata-latest/build-test strata.exe strata_kq.dll
