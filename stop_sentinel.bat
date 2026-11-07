@echo off
rem ============================================================================
rem  Stops VYRXONE Sentinel (this application only).
rem
rem    stop_sentinel.bat           ask Sentinel to exit cleanly: it stops its
rem                                threads, removes the wallpaper and restores
rem                                the normal desktop. Waits up to 8 seconds.
rem    stop_sentinel.bat /force    if it does not exit (hung), terminate THAT
rem                                process - but only after checking that the
rem                                process recorded in Sentinel's own lock file
rem                                really is VYRXONE Sentinel. No other Python
rem                                process is ever touched. Nothing is ever killed by name.
rem
rem  Safe to run when Sentinel is not running.
rem ============================================================================
setlocal EnableExtensions
cd /d "%~dp0"
set "FORCEARG="
if /I "%~1"=="/force" set "FORCEARG=--force"
if /I "%~1"=="--force" set "FORCEARG=--force"
if exist "%~dp0.venv\Scripts\python.exe" goto use_venv
if exist "%~dp0dist\VYRXONE-Sentinel\VYRXONE-Sentinel.exe" goto use_exe
echo Sentinel has not been set up in this folder, so there is nothing to stop.
exit /b 0

:use_venv
set "PYTHONPATH=%~dp0src"
"%~dp0.venv\Scripts\python.exe" -m sentinel --stop %FORCEARG%
exit /b %errorlevel%

:use_exe
"%~dp0dist\VYRXONE-Sentinel\VYRXONE-Sentinel.exe" --stop %FORCEARG%
exit /b %errorlevel%
