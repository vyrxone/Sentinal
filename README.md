# VYRXONE Sentinel — V5 (Final Edition)

An animated desktop wallpaper that doubles as a lightweight **system monitor** and a personal
**defensive dashboard** (folder watching + file-integrity baselines), plus a small project
command center. Black / charcoal / crimson / restrained gold. Free, local, no telemetry, no accounts. **V4 added an optional AI Assistant that is OFF by default** (Gemini, Groq, OpenRouter; every request is reviewed and confirmed by you first).

> **Roadmap:** V1 → V2 → V3 → V4 (optional multi-provider AI) → **V5 (this release: final edition)**. There is no V6.

> **Honest status of V5:** all automated tests that can run off Windows pass (see [docs/V5_TEST_REPORT.md](docs/V5_TEST_REPORT.md)). **Nothing was run on real Windows and no live provider (Gemini/Groq/OpenRouter) was called**, so wallpaper embedding, the Credential Manager, the EXE and every live AI connection are *unverified*: [docs/V5_WINDOWS_VALIDATION.md](docs/V5_WINDOWS_VALIDATION.md) lists every manual test as NOT TESTED. No software is bug-free; see [docs/V5_KNOWN_LIMITATIONS.md](docs/V5_KNOWN_LIMITATIONS.md).

## What's new in V5 (notes: [docs/V5_RELEASE_NOTES.md](docs/V5_RELEASE_NOTES.md), audit: [docs/V5_AUDIT.md](docs/V5_AUDIT.md), security: [docs/V5_SECURITY_REVIEW.md](docs/V5_SECURITY_REVIEW.md))
* **Optional proxy for AI requests** (AI Assistant → Network). Direct connection stays the default. HTTP proxy via CONNECT, optional sign-in stored in the Credential Manager, *Test proxy*, never falls back to a direct connection, provider certificates always verified.
* **Lightweight mode** (Settings → Animation) for 4 GB / integrated graphics.
* **`--keyring-selftest`** checks the Windows Credential Manager with throw-away entries.
* **Five V4 defects fixed** (A11–A15, each with a regression test), including: connection tests no longer run while AI is off.
* Schema unchanged (v5); no migration needed from V4. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## What's new in V4 (details: [docs/V4_RELEASE_NOTES.md](docs/V4_RELEASE_NOTES.md), audit: [docs/V4_AUDIT.md](docs/V4_AUDIT.md))
* **AI Assistant page (new, `Ctrl+9`):** explain a finding, summarize selected events, explain correlation, explain a baseline difference, summarize project health, suggest tasks, explain settings/diagnostics, answer questions about Sentinel. **Off by default. Sentinel works fully without AI, keys or internet.**
* **Three providers behind one router:** Google Gemini, Groq, OpenRouter. Primary + ordered fallbacks, per-provider model ID and timeout, cooldown/back-off, Retry-After, cancellation, health indicators. You type the model ID (nothing is hard-coded). Live connections are **unverified** in this release (no API keys were available): see [docs/AI_PROVIDER_SUPPORT.md](docs/AI_PROVIDER_SUPPORT.md).
* **Privacy first:** you choose the context; a review dialog shows the provider, the exact text and the fields before anything is sent; paths, user/machine names, IPs and secrets are redacted by default; recorded text is quoted as untrusted data. See [docs/AI_PRIVACY.md](docs/AI_PRIVACY.md).
* **Keys:** stored only in the Windows Credential Manager (or an environment variable), never in settings, the database, exports, logs or diagnostics. Setup: [docs/AI_SETUP.md](docs/AI_SETUP.md).
* **Fix:** the Events page did not live-refresh on alert changes (a V3 defect, A8 in the audit).
* Database schema v5 (additive; optional local conversation history).

## What was new in V3 (details: [docs/V3_RELEASE_NOTES.md](docs/V3_RELEASE_NOTES.md), audit: [docs/V3_AUDIT.md](docs/V3_AUDIT.md))
* **Findings page (new):** eight small, explainable, read-only rules turn your local audit log into *findings* with evidence, a stated level of certainty, a safe next step and related event ids; correlation explains why events were grouped; acknowledge / resolve / dismiss (with reason) / reopen; filter, search, export JSON/CSV with event references. **A finding is not proof of malicious activity** and Sentinel does not detect malware, ransomware or persistence.
* **Integrity fixes:** a file that changes while being hashed is never baselined; replacing a baseline needs confirmation; symlinks are refused; a cancelled verification is reported as *incomplete*, never as success.
* **Projects:** health report (folder, read-only Git, tasks, last healthy time), tasks with priority/due date, timeline notes, activity, user-triggered repository overview. Test/build status only if *you* record it. Disconnected drives no longer freeze the app.
* **Performance:** paged, sortable Events table (500 rows/page); job runner with bounded queue, timeouts and slow-operation diagnostics; Performance page shows monitoring, jobs and analysis status ("unavailable" instead of fake zeros).
* **Data:** schema v4 migration (additive) with backup `sentinel.db.pre-v3.bak`, rollback and newer-schema refusal.

## What was new in V2 (details: [docs/V2_RELEASE_NOTES.md](docs/V2_RELEASE_NOTES.md))
* **Desktop integration:** honest status (*Embedded successfully / Running as a normal window / Desktop integration unavailable / Reconnecting after Explorer restart*) with the method shown; event-driven recovery from Explorer restarts, display/DPI changes and sleep/resume with bounded retries; refuses third-party shells; exception-safe.
* **Multi-monitor:** primary only / one selected monitor / all monitors, stable monitor ids, negative coordinates, explained fallbacks. *New and unverified on real multi-monitor hardware.*
* **Appearance:** presets *Sentinel Core*, *Crimson Watch*, *Silent Guardian*; emblem size, particles, pulses, dimmer, clock, compact/expanded overlay, reduced motion.
* **Performance page:** measured frames/s and paint time, Sentinel's own CPU/memory, history graphs, redacted diagnostic report; throttled process scan; `scripts/benchmark.py`.
* **Alerts:** acknowledge/unacknowledge, archive, delete-archived (with confirmation), status/severity/category/time/text filters, recovery events, rate-limited optional notifications, alert state that is consistent after a restart. Unacknowledged warnings are never pruned.
* **Security:** per-folder exclusions, watcher health check and automatic recovery (removed drives), observation timestamps preserved.
* **Data:** schema v3 migration with backup + rollback, integrity check, clear errors for corrupt/locked/newer databases. **V1 data is upgraded in place** (see *Upgrading from V1*).
* **Lifecycle:** lock-file single instance (no start-up race), `stop_sentinel.bat /force` that verifies the process is Sentinel, fixed `run_sentinel.bat` expansion trap.

## What it does

| Area | Features |
|---|---|
| **Wallpaper** | Full-screen animated scene: rotating rings, live CPU / memory / disk arcs, energy pulses, subtle particles, clock + date, optional compact overlay. Frame-rate cap 15 / 24 / 30 FPS, three intensity levels, pause, automatic reduction under sustained CPU load. |
| **Desktop integration** | On Windows, embeds in Explorer's wallpaper layer **behind your desktop icons** (undocumented technique, isolated in one module, with automatic fallback to a normal window). |
| **Monitor** | Real CPU, memory, disk, uptime, battery/temperature where available, process list (CPU %, memory), bounded history graphs, visible data freshness (LIVE / STALE / OFF). Missing values show `—`, never invented numbers. |
| **Security center** | Watch folders you choose (watchdog); SHA-256 integrity baseline for files you choose; events for created / modified / deleted / moved files and baseline mismatches; read-only — nothing is ever deleted, moved, quarantined or uploaded. |
| **Projects** | Add / edit / remove your own projects; open the folder; read-only Git status; V3: health report, tasks, notes, activity, repository overview (all local, all on request). |
| **Findings** | V3: rule-based findings from the audit log with evidence, correlation, lifecycle and export. Read-only: nothing is deleted, quarantined, killed or uploaded. |
| **Events & settings** | SQLite audit log with filters, JSON/CSV export, backup, warning thresholds, optional *Start with Windows* (off by default). |
| **AI Assistant** | V4: optional, off by default; explains and summarizes only what you select and confirm. Answers are unverified and never change findings or run anything. |
| **Control panel + tray** | Overview · Monitor · Security · Projects · Events · Settings · Performance · Findings · AI Assistant; tray menu (open panel, show/hide wallpaper, pause animation, start/stop monitoring, restore desktop, exit); keyboard shortcuts. |

### What it is **not**
Not an antivirus, EDR or malware detector. A changed file is **not** automatically malicious. It observes and records only.

## Requirements
* Windows 11 64-bit (Windows 10 should work). Developed/tested logic is cross-platform; the desktop-layer embedding is Windows-only.
* **Python 3.11 or 3.12 recommended** (3.10 – 3.14 are accepted by all dependencies). Get it from python.org and tick *Add python.exe to PATH*.
* Dependencies (pinned in `requirements.txt`): PySide6-Essentials 6.12.0, psutil 7.2.2, watchdog 6.0.0; on Windows also `keyring` 25.7.0 (optional: AI key storage in the Credential Manager; Sentinel starts without it). The AI HTTP client uses the Python standard library.
* Git is optional (only for project Git status / health).
* Internet is needed for `pip install`. Sentinel makes **no network connection by itself**; only the optional AI Assistant contacts a provider, and only after you enable it and confirm a reviewed request. Disable it completely: leave *Enable AI Assistant* off (default) or press *Reset AI configuration…* in *AI Assistant → Privacy & limits* (optionally deleting stored keys). Proxy setup is in *AI Assistant → Network* ([docs/AI_SETUP.md](docs/AI_SETUP.md)).

## Install and first launch (Windows)
1. Extract the ZIP anywhere you can write to (e.g. `C:\Tools\VYRXONE-Sentinel-V5`). Avoid `Program Files`.
2. Double-click **`run_sentinel.bat`**. The first run creates `.venv`, installs the dependencies, runs a self-check and starts Sentinel.
3. Sentinel opens its **control panel** and (by default) **Wallpaper mode**. Look for the tray icon (it may be in the `^` overflow).
4. Next time just double-click `run_sentinel.bat` again — if Sentinel is already running it simply brings up the panel.

Useful switches: `--console` (show errors in a window) · `--install` (repair dependencies) · `--panel` (no wallpaper) · `--background` (no panel).

## Leaving wallpaper mode / exiting / restoring the desktop
* **Instantly restore the desktop:** control panel → *Restore normal desktop* (`Ctrl+Shift+R`) or tray → *Restore normal desktop*.
* **Switch mode:** *Switch mode* button / `F9` / tray menu.
* **Exit:** panel → *Exit Sentinel* (`Ctrl+Q`), tray → *Exit Sentinel*, or double-click **`stop_sentinel.bat`** (asks Sentinel to exit cleanly and waits up to 8 s). If Sentinel is hung, `stop_sentinel.bat /force` terminates **only** the process recorded in Sentinel's own lock file, and only after checking that it really is Sentinel.
* If Sentinel ever crashes, Windows removes its window with it and your normal wallpaper returns.

### Keyboard shortcuts (control panel)
`Ctrl+1…8` pages (Ctrl+6 Settings, Ctrl+7 Performance, Ctrl+8 Findings) · `Ctrl+M` monitoring on/off · `Ctrl+P` pause animation · `F9` switch mode · `Ctrl+E` export events · `Ctrl+Shift+R` restore desktop · `F5` refresh · `Ctrl+Q` exit.

## Wallpaper placement — honest notes
* Embedding uses an **undocumented** Explorer mechanism. If it cannot be done, Sentinel shows the scene in a **normal window** and says so in the status bar and event log — it never claims to be embedded when it is not.
* Choose *Always use a normal window* in Settings → Wallpaper and window to disable embedding entirely.
* The embedded wallpaper sits below the icon layer, so it does **not** receive mouse clicks (your icons stay fully usable). Interact through the tray and the control panel.
* V2 can cover **several monitors** (Settings → Wallpaper and window → Monitors). That path and the Windows 11 24H2+ code path was written from the documented window layout and **has not been verified on real hardware** — see [docs/WINDOWS_TEST_CHECKLIST.md](docs/WINDOWS_TEST_CHECKLIST.md).

## Performance (low-end PCs)
Defaults: 24 FPS cap, medium intensity, sampling every 2 s. Only a small rectangle around the emblem is redrawn per frame; the clock/overlay redraw once per second. Under sustained load (CPU ≥ 90 % for 5 samples) particles, glow and pulses switch off and the frame rate drops to ≤ 15 FPS (you can turn this off). Choose *Low* intensity and 15 FPS on very weak hardware. **The Celeron J3355 target has not been measured.** V2 measured on a 2-core Linux VM (software rendering, not the target): animated region 6–15 ms/frame at 1080p depending on preset, Sentinel ≈14 % of total CPU at 26 fps / 1366x768 with the default preset, 96 MiB RSS (see [docs/V2_TEST_REPORT.md](docs/V2_TEST_REPORT.md)). V3 measured the Events page at 3–18 ms per reload for 2,000–10,000 stored rows on the same VM (V2: 60–73 ms; V3 shows 500 rows per page, see docs/V3_AUDIT.md §C). Use *Silent Guardian*, reduced motion, or 15 FPS on weak hardware, and run `python scripts/benchmark.py` on the target PC.

## Data, privacy and backups
* Data folder: `%LOCALAPPDATA%\VYRXONE\Sentinel` (database `sentinel.db`, `logs\`). Settings → *Open data folder*.
* Settings → *Back up database…* writes a consistent copy; *Export/Import settings* handles preferences (`config/sentinel.sample.json` shows the format).
* No telemetry, analytics or remote content. API keys are never written to the database, settings files, exports, logs or diagnostics (Windows Credential Manager or an environment variable only). If AI is enabled, requests you confirm are sent to the provider you chose and processed under that provider's policies; conversation history is stored locally only if you turn it on.

## Optional executable build
(**Not tested by the author — no Windows machine.**) `build_exe.bat` builds `dist\VYRXONE-Sentinel\VYRXONE-Sentinel.exe` (PyInstaller `--onedir`, in a private `.venv-build`). The bundle is large (~170 MB on Linux; expect similar on Windows). Unsigned executables may trigger SmartScreen/antivirus warnings — building from source is the safer route.

## Tests
```
run_tests.bat                                   (Windows, creates .venv-test)
python -m pip install -r requirements-dev.txt   (any OS)
QT_QPA_PLATFORM=offscreen python -m pytest      (set the variable with `set` on Windows cmd)
```
566 passing + 2 Windows-only (skipped off Windows) tests (see docs/V5_TEST_REPORT.md for the executed / skipped / manual-Windows split) cover storage/migrations, settings, events, export, thresholds, monitoring, file integrity, live folder watching, projects/Git, single-instance IPC, the controller and the UI (offscreen), plus launch/stop smoke tests. See [docs/V5_TEST_REPORT.md](docs/V5_TEST_REPORT.md) for what was and was not verified; earlier reports are kept as history. No automated test uses a real API key or the network.

## Troubleshooting
| Symptom | Try |
|---|---|
| Nothing appears | `run_sentinel.bat --console` shows errors; check `%LOCALAPPDATA%\VYRXONE\Sentinel\logs\sentinel.log`. |
| "Python not found" | Install Python 3.12 from python.org with *Add to PATH*. |
| pip install fails | Check internet/proxy, then `run_sentinel.bat --install`. |
| Wallpaper is a normal window | See the status bar message; Windows may have changed the desktop layout. Use the window or switch to Control Panel mode. |
| Wallpaper vanished after Explorer restarted | Sentinel reacts to Explorer's TaskbarCreated broadcast, shows *Reconnecting…*, retries a bounded number of times (~21 s total) and otherwise falls back to a labelled window. Check Performance → diagnostic report. |
| Sentinel hung | `stop_sentinel.bat /force` (verifies the process before terminating it). |
| No tray icon | Check the `^` overflow; with no tray, closing the panel asks before exiting. |
| Already running message | Intended — only one instance runs. Use `run_sentinel.bat` to bring the panel forward. |
| Process list shows `—` | Windows protects some processes; Sentinel never asks for administrator rights. |

## Upgrading from V4
Extract V5 into a **new** folder and run `.\run_sentinel.bat`. The database schema is unchanged (v5): no migration, no new backup; your data and AI settings are kept (AI stays off until you turn it on). V4 can still open the database. Details: [docs/V5_INSTALLATION.md](docs/V5_INSTALLATION.md).

## Upgrading from V3
Extract V4 into a **new** folder and run `run_sentinel.bat`. The database is first copied to `sentinel.db.pre-v4.bak` (the V3-schema database) and upgraded to schema v5 by an additive migration; a failure is rolled back. A V3 build refuses the v5 database, so keep the `.bak` if you may go back. AI starts **off**.

## Upgrading from V2
Extract V3 into a **new** folder and run `run_sentinel.bat`. Your data folder is reused: the database is first copied to `sentinel.db.pre-v3.bak` (the V2-schema database), then upgraded to schema v4 by an additive migration (events, alerts, settings, watched folders, baselines, acknowledgements, archive state and projects/notes are preserved). If the upgrade fails it is rolled back and the app explains why. A V2 build refuses the v4 database, so keep the `.bak` if you may go back. Turn *Start with Windows* off and on again so it points at the V3 folder.

## Upgrading from V1
Extract V3 into a **new** folder (do not merge over V1), run `run_sentinel.bat`. Your V1 data folder (`%LOCALAPPDATA%\VYRXONE\Sentinel`) is reused: on first start the database is backed up to `sentinel.db.pre-v2.bak` and upgraded (V3 then continues to schema v4) (events, settings, watched folders, baselines and projects are preserved; new V2 options use defaults). Downgrading is not supported: a V1 build refuses the upgraded database, so keep the `.bak` file if you may go back. If *Start with Windows* was on, turn it off and on again so it points at the V3 folder (Settings shows a warning when the registered path is stale).

## Windows diagnostics
`diagnose_sentinel.bat` (or `python -m sentinel --diagnostics`) prints a short report: versions, Windows build, screens, desktop-integration method and measured performance. Your user name is removed; no file names or folders are included.

## Uninstall
1. `stop_sentinel.bat`, and turn off *Start with Windows* in Settings if you enabled it.
2. Delete the extracted folder (this removes `.venv`).
3. Optionally delete `%LOCALAPPDATA%\VYRXONE\Sentinel` (your events, projects and settings).

## Project layout
```
src/sentinel/            app.py · controller.py
  core/  storage/  monitoring/  security/  projects/    (no Qt except core/ipc.py)
  ai/                    models · providers/ (gemini, groq, openrouter) · router · privacy · context_builder · secrets · service
  platform/windows/      desktop_layer.py · shell_events.py · autostart.py
  ui/                    wallpaper · main window · pages/ · tray · theme
tests/  docs/  config/  assets/  scripts/
run_sentinel.bat  stop_sentinel.bat  diagnose_sentinel.bat  build_exe.bat  run_tests.bat
```

## Screenshots
None are included: no real screenshots of the Windows desktop were available, and none are invented.

## Proxy configuration (AI requests only)
AI Assistant → Network: tick *Send AI requests through an HTTP proxy*, enter host and port (and the sign-in if required, stored in the Credential Manager), press *Test proxy* (needs AI switched on; it only opens the tunnel and TLS handshake). Unsupported: `https://` proxies, SOCKS, NTLM/Kerberos, PAC. If the proxy fails, requests fail; they are never sent directly. Environment alternative: `VYRXONE_PROXY_USERNAME` / `VYRXONE_PROXY_PASSWORD`.
