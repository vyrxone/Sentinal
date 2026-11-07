> **Historical document.** This is the V1 report, kept unchanged for the record. It does not describe V2; see [V2_RELEASE_NOTES.md](V2_RELEASE_NOTES.md) and [V2_TEST_REPORT.md](V2_TEST_REPORT.md).

# VYRXONE Sentinel V1 — completion report

Built and tested in a Linux cloud container (Python 3.13, PySide6 6.12.0, psutil 7.2.2, watchdog 6.0.0).
**Nothing in this report was run on Windows.** "Tested" means an automated test or a command that was
actually executed; "unverified" means written but never run on the target platform.

## 1. Implemented (all V1 scope; nothing from V2–V5)
* Animated wallpaper scene (rings, live metric arcs, pulses, particles, clock/date, optional overlay), FPS cap 15/24/30, three intensity levels, pause, automatic reduction under sustained load.
* Windows desktop-layer embedding (Progman/WorkerW, ctypes only) isolated in `platform/windows/desktop_layer.py`, with verification, detach, Explorer-restart watch, display-change refit, and an honest normal-window fallback.
* System monitor (psutil): CPU, memory, disks, uptime, battery/temperature when present, process list, bounded history, data-freshness display, `—` for anything unavailable.
* Security center: user-selected folder watching (watchdog), SHA-256 baseline, event coalescing + rate limit, own-data-folder exclusion, cancellable explicit scans, read-only by design, clear limitations text.
* Project command center with safe read-only Git status.
* SQLite storage with versioned migrations (+ pre-migration backup), validated settings, audit log, filters, JSON/CSV export (spreadsheet-injection-safe), backup, settings import/export, sample config.
* Control panel (6 pages), tray menu, keyboard shortcuts, confirmations for consequential actions, single-instance guard, clean-stop channel, optional reversible Start-with-Windows (off by default, HKCU only).
* Scripts: `run_sentinel.bat`, `stop_sentinel.bat`, `build_exe.bat`, `run_tests.bat`; `requirements*.txt`; PyInstaller build script; ZIP packager/verifier.

## 2. Tested (executed here)
| What | How | Result |
|---|---|---|
| Whole suite | `pytest` in a **fresh virtual environment** created from `requirements-dev.txt`, from a freshly **extracted copy of the ZIP** | 135 passed, repeated 4× with no flakiness |
| Storage, migrations (incl. rollback, newer-schema refusal), settings persistence/validation, events, export | unit tests | pass |
| Threshold rules, sustained-load logic, quality mapping | unit tests | pass |
| Real psutil sampling; failure isolation; missing sensors; access-denied processes; bounded history; single sampler thread | unit tests | pass |
| Live folder watching (create/modify/delete), baseline mismatch from live change, verify, rate-limit, coalescing, overflow, thread shutdown | tests with real watchdog (inotify) | pass |
| Projects CRUD/validation, real `git` repositories, git missing/timeout/errors, no-shell argument lists | tests | pass |
| Controller, thread→UI delivery, shutdown order/idempotence, dead-database tolerance | tests | pass |
| UI: all pages, toolbar actions, shortcuts, exit/close confirmation, settings, projects, events filter/export, security flows, wallpaper rendering/pause/load behaviour | offscreen Qt tests driving real widgets (dialogs stubbed) | pass |
| Launch in panel and wallpaper mode, second-instance refusal, `--stop` round trip, bad data folder, `--self-check` | subprocess tests and manual runs | pass |
| Visual review | offscreen screenshots of wallpaper and all pages; layout defects found and fixed | reviewed |
| PyInstaller `--onedir` build + run of the frozen app | built and run **on Linux** | works (173 MB) |
| ZIP | built, `testzip`, required files present, no caches/venvs/databases/logs/secrets/placeholders | pass |

Measured: repainting the animated region at 1920×1080, High intensity, software rendering on the dev VM ≈ 11 ms/frame.

## 3. Unverified — needs a real Windows PC (see `WINDOWS_TEST_CHECKLIST.md`)
1. **Desktop-layer embedding** (both the Win10/11-pre-24H2 and the 24H2 `progman_child` code paths): only the "unsupported platform" branch and pure logic ran. This is the highest-risk item. The fallback window is tested.
2. `run_sentinel.bat`, `stop_sentinel.bat`, `build_exe.bat`, `run_tests.bat`: reviewed, never executed.
3. Tray icon and menu (no tray exists in the offscreen platform; the no-tray branch is tested).
4. Start-with-Windows registry code (only the command-line builder is tested).
5. Multi-DPI/resolution/monitor changes, Explorer restart, sleep/resume, sign-out behaviour.
6. watchdog on Windows (ReadDirectoryChangesW) and Windows process-permission behaviour.
7. **Performance on the Celeron J3355 / 4 GB**: not measured. Defaults are conservative; use Low + 15 FPS if needed.
8. Windows fonts (Segoe UI) — layouts were reviewed with a Linux fallback font and may differ slightly.
9. The Windows `.exe` build (only a Linux build was run).

## 4. Known limitations
* Primary monitor only; embedded wallpaper receives no mouse input (by design: icons stay usable).
* Undocumented Explorer behaviour can change with Windows updates; the fallback exists for that reason.
* File watching misses events while Sentinel is not running; network/cloud folders may be unreliable.
* Not an antivirus or malware detector.

## 5. Next milestone
**V2** — improved desktop integration (multi-monitor), reliability, appearance settings, performance tuning, alert management. Not started.
