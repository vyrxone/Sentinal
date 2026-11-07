@echo off
rem ============================================================================
rem  VYRXONE Sentinel V5 - one-click launcher (Windows 10/11, Python 3.10-3.14)
rem
rem    run_sentinel.bat              start Sentinel (creates .venv and installs
rem                                  dependencies automatically the first time)
rem    run_sentinel.bat --install    re-install / repair the dependencies
rem    run_sentinel.bat --console    run with a console window to see errors
rem    run_sentinel.bat --panel      start in Control Panel mode (no wallpaper)
rem    run_sentinel.bat --background start without opening the control panel
rem
rem  Needs internet for the one-time pip install. Sentinel makes NO network
rem  connection by itself: the optional AI Assistant is OFF by default and only
rem  contacts a provider after you enable it AND confirm a reviewed request.
rem  No administrator rights are used or requested.
rem
rem  V2 note: the flow below uses labels instead of parenthesised blocks, because
rem  cmd.exe expands %VARIABLES% inside a block when the block is PARSED, which
rem  breaks values set by "call" within the same block.
rem ============================================================================
setlocal EnableExtensions
cd /d "%~dp0"

set "VENV=%~dp0.venv"
set "VPY=%VENV%\Scripts\python.exe"
set "VPYW=%VENV%\Scripts\pythonw.exe"
set "FLAG=%VENV%\.sentinel_deps_ok"
set "PYEXE="
set "FORCE="
set "CONSOLE="
set "PASS="

:parse
if "%~1"=="" goto parsed
if /I "%~1"=="--install" set "FORCE=1" & shift & goto parse
if /I "%~1"=="--console" set "CONSOLE=1" & shift & goto parse
set PASS=%PASS% %1
shift
goto parse
:parsed

if exist "%VPY%" goto have_venv
call :find_python
if defined PYEXE goto make_venv
echo [Sentinel] Python 3.10 - 3.14 was not found.
echo            Install Python 3.12 from https://www.python.org/downloads/ and tick
echo            "Add python.exe to PATH", then run this file again.
pause
exit /b 1

:make_venv
echo [Sentinel] Creating virtual environment with %PYEXE% ...
%PYEXE% -m venv "%VENV%"
if errorlevel 1 goto venv_failed
set "FORCE=1"
goto have_venv

:venv_failed
echo [Sentinel] Could not create the virtual environment.
pause
exit /b 1

:have_venv
if not exist "%FLAG%" set "FORCE=1"
if not defined FORCE goto launch
echo [Sentinel] Installing dependencies from requirements.txt ...
"%VPY%" -m pip install --disable-pip-version-check -r "%~dp0requirements.txt"
if errorlevel 1 goto pip_failed
set "PYTHONPATH=%~dp0src"
"%VPY%" -m sentinel --self-check
if errorlevel 1 goto check_failed
echo ok> "%FLAG%"
goto launch

:pip_failed
echo [Sentinel] Dependency installation failed. Check your internet connection and try
echo            again with:  run_sentinel.bat --install
pause
exit /b 1

:check_failed
echo [Sentinel] Self-check failed - see the messages above.
pause
exit /b 1

:launch
set "PYTHONPATH=%~dp0src"
if defined CONSOLE goto launch_console
start "" "%VPYW%" "%~dp0launch_sentinel.pyw" %PASS%
exit /b 0

:launch_console
"%VPY%" -m sentinel %PASS%
exit /b %errorlevel%

:find_python
for %%V in (3.12 3.11 3.13 3.10 3.14) do call :try_py %%V
if defined PYEXE goto :eof
python -c "import sys; sys.exit(0 if (3,10)<=sys.version_info[:2]<=(3,14) else 1)" >nul 2>&1
if not errorlevel 1 set "PYEXE=python"
goto :eof

:try_py
if defined PYEXE goto :eof
py -%1 -c "import sys" >nul 2>&1
if not errorlevel 1 set "PYEXE=py -%1"
goto :eof
