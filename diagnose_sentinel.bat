@echo off
rem Prints a short, redacted support report (versions, screens, no personal data)
rem and saves it as sentinel-diagnostics.txt next to this file.
setlocal EnableExtensions
cd /d "%~dp0"
if not exist "%~dp0.venv\Scripts\python.exe" (
    echo Run run_sentinel.bat once first so the environment exists.
    pause
    exit /b 1
)
set "PYTHONPATH=%~dp0src"
"%~dp0.venv\Scripts\python.exe" -m sentinel --diagnostics > "%~dp0sentinel-diagnostics.txt"
type "%~dp0sentinel-diagnostics.txt"
echo.
echo Saved to sentinel-diagnostics.txt
pause
