# Manual test checklist — real Windows (V3; not verifiable in the Linux build environment)

**Status of every item below: UNVERIFIED.** The Linux build used mocks (a fake Win32 desktop, monkeypatched slow file systems); mocks prove logic, not real Explorer/OS behaviour.

Record Windows build (`winver`), GPU/driver, display scaling and results. Tick each item.

## A. Install and launch
- [ ] `run_sentinel.bat` on a clean extract creates `.venv`, installs, self-check passes, Sentinel starts.
- [ ] Second double-click of `run_sentinel.bat` does **not** start a second copy; the panel comes forward.
- [ ] `run_sentinel.bat --console` shows no errors.

## B. Desktop-layer embedding (the highest-risk area)
- [ ] Wallpaper mode: animation appears **behind** desktop icons; icons are visible, clickable, draggable; right-click desktop menu works.
- [ ] Status bar says "Embedded in the Windows desktop layer" (record `workerw_sibling` or `progman_child` from the Events log/`desktop` detail).
- [ ] If it says "normal window", note the reason shown — that is a valid fallback; report the Windows build.
- [ ] Win+D (show desktop) and returning keep the wallpaper; opening/closing apps never covers or flickers over them.
- [ ] Taskbar is unaffected; Sentinel has no taskbar button while embedded.
- [ ] Change display scaling (100/125/150 %): wallpaper refits within ~1 s.
- [ ] Change resolution; plug/unplug a second monitor; change the primary monitor: wallpaper stays on the primary display.
- [ ] Kill `explorer.exe` (Task Manager → Restart): wallpaper returns within ~10 s or falls back to a window with a logged notice.
- [ ] Lock/unlock (Win+L) and sleep/resume: animation continues.
- [ ] *Restore normal desktop* (button, `Ctrl+Shift+R`, tray) immediately shows your normal wallpaper; no black or stale area remains.
- [ ] Settings → *Always use a normal window* → Wallpaper mode shows a normal window; closing it returns to Control Panel mode.

## C. Lifecycle
- [ ] Exit from the panel, tray and `stop_sentinel.bat`: process ends (Task Manager), wallpaper restored, no leftover `pythonw.exe`.
- [ ] Closing the panel with the tray available hides it; tray icon reopens it.
- [ ] Windows sign-out/shutdown is not delayed or blocked by Sentinel.
- [ ] Settings → *Start with Windows* ON → sign out/in → Sentinel starts in the background; OFF removes it (check Task Manager → Startup).

## D. Monitoring
- [ ] CPU/memory/disk values match Task Manager within a few percent.
- [ ] Process list populated; protected processes show `—`; no UAC prompt appears.
- [ ] Stop monitoring (`Ctrl+M`): overlay shows MONITORING OFF and `—`; start again: LIVE.

## E. Security
- [ ] Add a test folder; create / edit / delete / rename files → events appear; Events page filters and exports work (open CSV in Excel).
- [ ] Baseline a file, edit it, *Verify baseline* → "Differences found"; file content unchanged by Sentinel.
- [ ] Removing the watched folder stops events. A folder on a removed USB drive shows "Folder unavailable".

## F. Performance (target: Celeron J3355 / 4 GB)
- [ ] Task Manager: Sentinel CPU and RAM at idle with default settings (record numbers: ____ % / ____ MB).
- [ ] Same with *Low* intensity / 15 FPS. Report if animation stutters at 24 FPS / medium.
- [ ] Sustained load (run a CPU stress tool): effects reduce automatically and a "sustained_load" event is logged.

## G. Projects / Git
- [ ] Add a project with a folder; *Open folder* opens Explorer; *Check Git status* works with and without Git installed.

## H. V2 additions
Run `diagnose_sentinel.bat` first and keep the output with your results.
- [ ] Status bar / Performance page show **Embedded successfully** with the method; if not, the exact reason and **Desktop integration unavailable**.
- [ ] Explorer restart (Task Manager → Restart): status shows **Reconnecting after Explorer restart**, then Embedded again (record seconds), or a single labelled window after ~21 s. No second copy of the wallpaper, no leftover `pythonw.exe`.
- [ ] Sleep/resume and Win+L: wallpaper correct afterwards; display change (resolution, scaling 100/125/150 %): refits without a black area.
- [ ] Multi-monitor (2+ displays): Monitors = All → one scene per display, correct on a display left of / above the primary (negative coordinates) and with mixed scaling; unplug/replug a display → scene removed/added, no duplicates; Monitors = Selected, then unplug that display → falls back to primary with the explanation shown.
- [ ] If multi-monitor embedding misbehaves, report build + layout; Primary only must still work.
- [ ] Restore normal desktop with 2 displays: your own wallpaper returns on all of them, no black rectangle.
- [ ] Window-mode fallback (Always use a normal window): one normal window, never fullscreen, never covering other apps.
- [ ] Desktop icons stay clickable/draggable, right-click works, taskbar and focus normal while embedded (all layouts).
- [ ] Presets: Sentinel Core / Crimson Watch / Silent Guardian visibly differ; reduced motion → emblem static, Task Manager CPU near zero; dimmer, clock, overlay compact/expanded work.
- [ ] Performance page: measured FPS/paint time plausible; **record on the Celeron J3355**: idle CPU/RAM for each preset, and `python scripts/benchmark.py` output.
- [ ] Alerts: stress CPU > threshold → warning event + (optional) tray notification, at most one per interval; stop stress → *recovered*; acknowledge, archive, delete archived (confirmations appear).
- [ ] Alert persistence: leave a warning active, kill Sentinel from Task Manager, restart → *alert_interrupted* event, not a fake recovery.
- [ ] Security: exclusions (e.g. `*.tmp`) hide matching events; remove/reinsert a USB drive with a watched folder → *watch_unavailable* once, *watch_restored* when it returns.
- [ ] Single instance: double-click `run_sentinel.bat` quickly 5 times → one Sentinel. `stop_sentinel.bat` exits it; `stop_sentinel.bat /force` against a hung Sentinel (e.g. suspend it in Process Explorer) terminates only Sentinel; other Python processes untouched.
- [ ] `run_sentinel.bat` on a clean extract with Python only available through the `py` launcher, and again with only `python` on PATH.
- [ ] Start with Windows: enable, move the folder, reopen Settings → stale-path warning appears.
- [ ] Upgrade: point V2 at a V1 data folder (copy it first) → `sentinel.db.pre-v2.bak` created, data intact.

## I. V3 additions (all unverified on Windows)
Collect first: `diagnose_sentinel.bat` output (Windows build, screens, DPI), and Performance page → *Save…* diagnostic report. Paths and your user name are redacted; review before sharing.
- [ ] Upgrade: copy your V2 data folder, start V3 → `sentinel.db.pre-v3.bak` exists, schema v4, all events/alerts (acked/archived state)/watched folders/baselines/projects intact; V3 starts. A second start makes no new backup.
- [ ] Findings: add a watched folder + a baselined file, edit it → within ~10 s a *Baseline file changed* finding appears with evidence and event ids; edit 3 baselined files within 10 minutes → *several baseline files changed together* (high) with the "Grouped because…" explanation.
- [ ] Findings lifecycle: acknowledge, mark resolved, dismiss with a reason, reopen; filter/search; export JSON and CSV; open the CSV in Excel (no formula executes; text starting with `=` is shown with a leading `'`).
- [ ] Unplug a USB drive holding a watched folder → *Watched folder is unavailable* finding; replug → finding mentions the gap; run *Verify baseline* afterwards.
- [ ] Integrity: baseline a log file that is being written (e.g. `ping -t host > log.txt`) → refused as "kept changing"; re-adding an existing baseline file asks for confirmation; baselining a shortcut/symlink is refused; start *Verify baseline* on many files and press Cancel → the result says **incomplete / NOT verified**.
- [ ] Projects with a **disconnected network drive** (map a share, add a project on it, disconnect/disable the network): the Projects page stays responsive; the folder shows unavailable within ~5 s; one *project folder unavailable* event; reconnect → *available again*. Task Manager: no growing thread count after repeating this 10 times.
- [ ] Project health on a real Git repo (with and without Git installed, a repo owned by another user → Git's safe.directory message is shown, not overridden). Tasks, notes, recorded test result, Overview on a large folder (cancel works; `node_modules`/`.venv` skipped).
- [ ] Events page with 10,000+ events (Settings → raise max events; generate by copying a large folder under a watched folder): paging, sorting, live refresh keeps the selection, export of the filtered set completes and can be cancelled.
- [ ] Performance page: monitoring status, last sample age, job status update; stop monitoring → "Stopped" and the sample age grows (no fake zeros).
- [ ] Wallpaper regression pass: run section B and H again – V3 changed no wallpaper code, but verify nothing regressed (especially Explorer restart during app exit, and repeated mode switching for 5 minutes: Task Manager memory flat).
- [ ] **Celeron J3355 / 4 GB:** record idle CPU/RAM with the Findings and Projects pages closed and open, and `python scripts/benchmark.py --quick` output.

## J. V4 additions (ALL UNVERIFIED: nothing below has been run on Windows; mark a line only after you actually did it)
Record result, Windows build, date and tester for each. Do not tick a box you did not execute.
- [ ] J1 Clean Windows install: extract V4 to a new folder, `run_sentinel.bat`, self-check prints `Self-check passed.` and the AI key storage line.
- [ ] J2 Launch, second launch (brings up the existing panel), clean exit (`Ctrl+Q`), `stop_sentinel.bat`, `stop_sentinel.bat /force` refuses an unrelated Python process.
- [ ] J3 Wallpaper behind desktop icons; J4 normal-window fallback; J5 Explorer restart; J6 sleep/resume; J7 Win+L; J8 multi-monitor and mixed DPI; J9 display unplug/replug; J10 Restore normal desktop (`Ctrl+Shift+R`); J11 tray and *Start with Windows* (re-check each V2/V3 item still works with V4 installed).
- [ ] J12 AI disabled (default) and the PC offline (adapter off): every page works, no error dialogs, AI page says AI is off, Performance page says no worker running.
- [ ] J13 Key storage: set a key for each provider; confirm it appears in Windows *Credential Manager → Windows Credentials* as `VYRXONE Sentinel`; confirm it is not in `%LOCALAPPDATA%\VYRXONE\Sentinel` (search the folder and `logs\` for the key text); delete it from the UI and confirm it is gone.
- [ ] J14 AI enabled with each provider separately (Gemini, Groq, OpenRouter): Test connection, fetch model list, enter a model ID, send one reviewed request, check the answer shows the right provider and model.
- [ ] J15 Review dialog: provider/model shown, exact text shown, unchecking an item removes it from the preview, Cancel sends nothing (watch the network with Resource Monitor).
- [ ] J16 Provider failure and fallback: wrong key on the primary (no fallback, clear error); disconnect network or use a bad model on the primary with a working fallback configured (fallback happens and is shown).
- [ ] J17 Cancelled request: press Stop during a slow request; UI stays responsive; a new request works afterwards.
- [ ] J18 Responsiveness while a provider is slow: window moves, pages switch, wallpaper keeps animating.
- [ ] J19 Celeron J3355 / 4 GB: record Task Manager CPU and RAM for Sentinel with AI idle (off) and during one request.
- [ ] J20 Upgrade V3 → V4 with a real V3 database: `.pre-v4.bak` exists, data intact, AI off.
- [ ] J21 Executable build (optional): `build_exe.bat`, launch the EXE, set a key (checks the keyring backend is bundled). Do not call the EXE working until you did this.

## K. V5 additions (ALL UNVERIFIED — see V5_WINDOWS_VALIDATION.md for the full 30-test table)
- [ ] K1 `--keyring-selftest` from source and from the built EXE: five PASS lines, no leftover entries, an unrelated credential unchanged.
- [ ] K2 Store a provider key and a proxy sign-in, restart Sentinel, confirm both survive; delete them and confirm they disappear from Credential Manager.
- [ ] K3 Proxy: *Test proxy* with a real proxy; dead proxy → clear error and no direct connection; sign-in rejected → proxy sign-in message.
- [ ] K4 Lightweight mode on the Celeron: record CPU/RAM with and without it.
- [ ] K5 Upgrade from V4 with a real data folder: nothing lost, no extra backup, AI still off.

### Reporting a problem
Attach: the diagnostic report, the Findings/Events export for the affected time (review paths first), and `%LOCALAPPDATA%\VYRXONE\Sentinel\logs\sentinel.log` (it contains file paths you watched). Sentinel never uploads anything; you send files yourself.
