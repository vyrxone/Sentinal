# V5 Windows Validation

**Status: NO Windows environment and NO provider API key were available to the author. Every manual test below is
recorded as NOT TESTED. Nothing here is a pass.** Linux results are in V5_TEST_REPORT.md and prove only the platform-independent logic.

How to use: run the step, then fill date / Windows build / hardware / actual result and change the last column to PASS or FAIL.
Leave NOT TESTED if you did not execute it. Do not tick a result you did not observe.

| # Test | Date | Windows build | Hardware | Steps run | Actual result | Status |
|---|---|---|---|---|---|---|
| 1 Fresh installation | | | | | | **NOT TESTED** |
| 2 Upgrade from V4 | | | | | | **NOT TESTED** |
| 3 First launch | | | | | | **NOT TESTED** |
| 4 Second launch | | | | | | **NOT TESTED** |
| 5 Normal exit | | | | | | **NOT TESTED** |
| 6 Forced-stop safeguards | | | | | | **NOT TESTED** |
| 7 Wallpaper behind icons | | | | | | **NOT TESTED** |
| 8 Normal-window fallback | | | | | | **NOT TESTED** |
| 9 Explorer restart | | | | | | **NOT TESTED** |
| 10 Sleep/resume | | | | | | **NOT TESTED** |
| 11 Lock/unlock | | | | | | **NOT TESTED** |
| 12 Multiple displays | | | | | | **NOT TESTED** |
| 13 Mixed DPI | | | | | | **NOT TESTED** |
| 14 Display unplug/replug | | | | | | **NOT TESTED** |
| 15 Restore desktop | | | | | | **NOT TESTED** |
| 16 Tray | | | | | | **NOT TESTED** |
| 17 Start with Windows | | | | | | **NOT TESTED** |
| 18 Monitoring and alerts | | | | | | **NOT TESTED** |
| 19 File-integrity checks | | | | | | **NOT TESTED** |
| 20 Findings and event export | | | | | | **NOT TESTED** |
| 21 AI disabled and offline | | | | | | **NOT TESTED** |
| 22 Gemini live connection | | | | | | **NOT TESTED** |
| 23 Groq live connection | | | | | | **NOT TESTED** |
| 24 OpenRouter live connection | | | | | | **NOT TESTED** |
| 25 Provider fallback | | | | | | **NOT TESTED** |
| 26 Cancelled request | | | | | | **NOT TESTED** |
| 27 Proxy connection (if configured) | | | | | | **NOT TESTED** |
| 28 Credential Manager storage/deletion (`--keyring-selftest` + Providers/Network tabs) | | | | | | **NOT TESTED** |
| 29 EXE launch and shutdown | | | | | | **NOT TESTED** |
| 30 Celeron J3355 performance | | | | | | **NOT TESTED** |

## Steps
1. **Fresh install / first / second launch / exit:** follow V5_INSTALLATION.md; `.\run_sentinel.bat`; run it again (panel comes forward); `Ctrl+Q`; `.\stop_sentinel.bat`; `.\stop_sentinel.bat /force` while an unrelated python.exe runs must refuse it.
2. **Upgrade from V4:** extract V5 to a new folder with a copy of a real V4 data folder; confirm events/projects/conversations present, AI off, no extra `.bak`.
3. **Desktop (7–17):** use the existing sections B–H of WINDOWS_TEST_CHECKLIST.md (embedding behind icons, fallback, Explorer restart via `taskkill /f /im explorer.exe && explorer`, sleep, `Win+L`, second monitor left/above primary, 125%/150% DPI mix, unplug/replug, tray, autostart). V5 changed no desktop code, so these are regression checks of unverified V1–V4 behaviour.
4. **28 Credential Manager:** `.\.venv\Scripts\python -m sentinel --keyring-selftest` (or `VYRXONE-Sentinel.exe --keyring-selftest`). Expected: five `PASS` lines and `RESULT: all checks passed`; it uses throw-away entries under a separate service name and checks an unrelated canary entry is unchanged. Then set a real key in AI Assistant → Providers, open *Control Panel → Credential Manager → Windows Credentials* and confirm a `VYRXONE Sentinel` entry; restart Sentinel (key still present); delete it in the app and confirm the entry is gone; confirm none of your other credentials changed.
5. **21 AI off and offline:** AI off (default), adapter disabled → every page works, no error dialogs.
6. **22–25 Providers:** enable AI, create a key in each provider's console, store it, *Fetch model list*, pick a model, *Test connection*, send one reviewed request. Fallback: wrong model on the primary + a working second provider. **Only you can do this: no key was supplied to the author.**
7. **26 Cancel:** press Stop during a slow request; the UI stays responsive; the next request works.
8. **27 Proxy:** AI Assistant → Network: host/port (and sign-in), *Test proxy*, send one request, then disable the proxy and confirm direct works. Also confirm a dead proxy produces a clear error and no direct connection.
9. **29 EXE:** `.\build_exe.bat`, launch `dist\VYRXONE-Sentinel\VYRXONE-Sentinel.exe`, run `--keyring-selftest`, store/restart/delete a key, exit cleanly (no leftover process in Task Manager).
10. **30 Celeron J3355 / 4 GB:** run `python scripts\benchmark_v5.py` and record Task Manager CPU/RAM with AI off, AI idle, and during one request, with and without *Lightweight mode*.
