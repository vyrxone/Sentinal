@echo off
rem Runs the automated test suite in a separate virtual environment (.venv-test).
setlocal EnableExtensions
cd /d "%~dp0"
if not exist ".venv-test\Scripts\python.exe" (
    py -3 -m venv .venv-test 2>nul || python -m venv .venv-test
    if errorlevel 1 ( echo Could not create the test environment. & pause & exit /b 1 )
)
".venv-test\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements-dev.txt
if errorlevel 1 ( echo Installing test dependencies failed. & pause & exit /b 1 )
set "QT_QPA_PLATFORM=offscreen"
".venv-test\Scripts\python.exe" -m pytest %*
pause
