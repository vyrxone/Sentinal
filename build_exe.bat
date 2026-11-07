@echo off
rem ============================================================================
rem  Optional: build a Windows executable folder with PyInstaller (--onedir, so it
rem  starts quickly and triggers fewer antivirus false positives than --onefile).
rem  Output: dist\VYRXONE-Sentinel\VYRXONE-Sentinel.exe
rem  Uses its own throw-away virtual environment (.venv-build). Nothing outside
rem  this folder is modified and no administrator rights are needed.
rem ============================================================================
setlocal EnableExtensions
cd /d "%~dp0"
if not exist ".venv-build\Scripts\python.exe" (
    py -3 -m venv .venv-build 2>nul || python -m venv .venv-build
    if errorlevel 1 ( echo Could not create the build environment. & pause & exit /b 1 )
)
".venv-build\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements-dev.txt
if errorlevel 1 ( echo Installing build dependencies failed. & pause & exit /b 1 )
".venv-build\Scripts\python.exe" scripts\build_exe.py
if errorlevel 1 ( echo Build failed - see the messages above. & pause & exit /b 1 )
echo.
echo Build complete: dist\VYRXONE-Sentinel\VYRXONE-Sentinel.exe
pause
