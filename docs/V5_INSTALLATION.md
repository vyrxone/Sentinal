# V5 Installation (Windows 10/11, PowerShell)

Requirements: Windows 11 64-bit (10 should work), Python 3.11 or 3.12 from python.org with "Add python.exe to PATH"
(3.10–3.14 are accepted by the dependencies), internet access for the first `pip install`. Git is optional.

## Fresh install
```powershell
# 1. Extract the ZIP to a NEW folder you can write to (not Program Files)
Expand-Archive -Path "$HOME\Downloads\VYRXONE-Sentinel-V5.zip" -DestinationPath C:\Tools
cd C:\Tools\VYRXONE-Sentinel-V5

# 2. First launch: creates .venv, installs dependencies, runs the self-check, starts Sentinel
.\run_sentinel.bat
```
Switches: `--console` (errors in a window), `--install` (repair dependencies), `--panel` (no wallpaper), `--background` (no panel).

## Launch again / exit
```powershell
.\run_sentinel.bat          # second launch just brings the panel forward
.\stop_sentinel.bat         # clean exit (waits up to 8 s)
.\stop_sentinel.bat /force  # only if hung: stops just the process recorded in Sentinel's lock file, after verifying it
```
In the app: `Ctrl+Q` exits, `Ctrl+Shift+R` restores the normal desktop. Check the installation: `python -m sentinel --self-check`
(from `.venv`), diagnostics: `.\diagnose_sentinel.bat`.

## Upgrade from V4
Extract V5 into a **new** folder and run `.\run_sentinel.bat`. V5 does **not** change the database schema (still v5), so no
backup or migration is needed; your data folder `%LOCALAPPDATA%\VYRXONE\Sentinel` is reused and AI stays off until you switch it
on. Five new settings (proxy ×4, lightweight mode) appear with defaults. If *Start with Windows* is on, switch it off and on
again so it points at the V5 folder. Going back to V4 is possible because the schema is unchanged.
Older databases (V1–V3) are upgraded automatically with a `.pre-v*.bak` backup as before.

## Optional executable
```powershell
.\build_exe.bat    # builds dist\VYRXONE-Sentinel\VYRXONE-Sentinel.exe in a private .venv-build
```
The EXE build was **not** tested by the author (no Windows machine). After building, run
`dist\VYRXONE-Sentinel\VYRXONE-Sentinel.exe --keyring-selftest` and follow V5_WINDOWS_VALIDATION.md before relying on it.

## AI, proxy and keys (all optional)
See AI_SETUP.md. AI is off by default; to disable it entirely leave *Enable AI Assistant* off, or AI Assistant → Privacy & limits →
*Reset AI configuration…* (optionally deleting stored keys).

## Backup and uninstall
```powershell
.\stop_sentinel.bat
Copy-Item "$env:LOCALAPPDATA\VYRXONE\Sentinel" "$HOME\Sentinel-backup" -Recurse      # backup of data (events, projects, settings)
# Settings → Back up database… also writes a consistent copy of sentinel.db
Remove-Item C:\Tools\VYRXONE-Sentinel-V5 -Recurse                                   # uninstall the program (turn Start with Windows off first)
Remove-Item "$env:LOCALAPPDATA\VYRXONE\Sentinel" -Recurse                           # optional: delete your data
```
Stored API keys/proxy sign-in live in Windows Credential Manager (Windows Credentials → `VYRXONE Sentinel`); delete them there
or via AI Assistant → Providers / Network before uninstalling.
