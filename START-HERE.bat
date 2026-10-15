@echo off
rem Strata for Windows: the first run installs everything and starts the model; later runs just start it.
rem Needs only an NVIDIA or AMD graphics driver. Python is installed for your user account if it is missing (no admin needed).
setlocal
title Strata
cd /d "%~dp0"
call :findpy
if defined PY goto run
echo.
echo  Python 3.10 or newer is not installed. Installing Python 3.12 for your user account ...
where winget >nul 2>nul
if errorlevel 1 goto pyorg
winget install -e --id Python.Python.3.12 --scope user --silent --source winget --accept-package-agreements --accept-source-agreements --disable-interactivity
call :findpy
if defined PY goto run
:pyorg
echo  Downloading the Python installer from python.org ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol='Tls12'; Invoke-WebRequest -UseBasicParsing https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe -OutFile \"$env:TEMP\strata-python-setup.exe\""
if exist "%TEMP%\strata-python-setup.exe" "%TEMP%\strata-python-setup.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_test=0
call :findpy
if defined PY goto run
echo.
echo  Python could not be installed automatically.
echo  Install 64-bit Python 3.12 from https://www.python.org/downloads/ ("Add python.exe to PATH"),
echo  then double-click START-HERE.bat again.
pause
exit /b 1

:run
%PY% -X utf8 setup.py %*
set "STRATA_EXIT_CODE=%errorlevel%"
if not "%STRATA_EXIT_CODE%"=="0" pause
exit /b %STRATA_EXIT_CODE%

:findpy
rem Use the same system Python as the model launch scripts, then try other installed interpreters.
set "PY="
if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" (
  set "PY="%LOCALAPPDATA%\Programs\Python\Python313\python.exe""
  goto :eof
)
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) and sys.maxsize > 2**32 else 1)" >nul 2>nul
if not errorlevel 1 (
  set "PY=py -3"
  goto :eof
)
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) and sys.maxsize > 2**32 else 1)" >nul 2>nul
if not errorlevel 1 (
  set "PY=python"
  goto :eof
)
for %%V in (313 312 311 310) do if exist "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe" set "PY="%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe"" & goto :eof
goto :eof
