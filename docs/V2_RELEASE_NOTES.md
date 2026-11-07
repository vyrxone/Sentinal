# VYRXONE Sentinel V2 — release notes

> **Superseded by V3** (see `docs/V3_RELEASE_NOTES.md`). Kept as history; statements about the Events table, Projects page and schema v3 describe V2.


V2 is a **reliability, multi-monitor, customization, alert-management and performance** release. It adds no AI,
no network code and nothing from V3–V5. Roadmap: V1 → V2 → V3 → V4 → V5 (there is no V6). Next milestone: V3.

Everything below was tested on Linux (headless Qt, fake Windows desktop). **Real-Windows behaviour is not verified**;
see "Needs real Windows verification" and `docs/WINDOWS_TEST_CHECKLIST.md`.

## Fixed (confirmed defects, evidence in `docs/V2_AUDIT.md`)
| # | Defect in V1 | V2 |
|---|---|---|
| A1 | Database connection stayed open when start-up/migration failed | closed on any failure |
| A2 | Event pruning could delete an unacknowledged warning | warnings are protected until acknowledged |
| A3 | Security events used the DB-write time, not the observation time | observation time preserved |
| A4 | `refresh_folders()` existed but was never called (removed/returned folders never recovered) | periodic off-thread health check recovers watches; one event per state change |
| A5 | An exception while embedding left a visible frameless full-screen window | cleaned up, falls back to one labelled window |
| (found by V2's own tests) | `QLockFile.getLockInfo` return shape differs between PySide6 versions → crash in the second-instance path | handled both shapes |

Also changed defensively (class B in the audit, not proven defects): lock-file single instance; `run_sentinel.bat` no longer
expands `%PYEXE%` inside a parenthesised block; stale Start-with-Windows path detection; native-pixel rectangles for
mixed-DPI monitors.

## New
* **Desktop integration**: status values *Embedded successfully*, *Running as a normal window*, *Desktop integration unavailable*,
  *Reconnecting after Explorer restart* with the actual method (`workerw_sibling` / `progman_child`). Event-driven recovery
  (TaskbarCreated, WM_DISPLAYCHANGE, WM_DPICHANGED, resume) plus a 10 s safety-net check; at most 5 retries (0.8, 1.5, 3, 6, 10 s)
  then one labelled normal window; at most 3 recoveries/minute. Refuses desktops not owned by `explorer.exe`. No injection.
  *Restore normal desktop* stops timers, detaches, closes, requests a desktop repaint and never touches your wallpaper setting.
* **Multi-monitor**: primary / selected / all, stable ids (manufacturer|model|serial, else name, duplicates suffixed),
  scenes reconciled on screen signals, no duplicate or orphan scenes, explained fallback when the selected monitor is gone.
  Window mode is always a single window.
* **Appearance**: presets (*Sentinel Core*, *Crimson Watch*, *Silent Guardian*) really change rendering (tested by pixel
  comparison); intensity, FPS 15/24/30, pause, overlay show/hide + compact/expanded, emblem size, particles, pulses,
  reduced motion (no frame timer), dimmer, clock/date. Changing a preset-controlled value switches to *Custom*.
* **Performance**: page with measured frames/s and paint time, Sentinel CPU/memory, history graphs, effects status and a
  redacted diagnostic report (`diagnose_sentinel.bat`, `--diagnostics`); process scan throttled (default 6 s, configurable);
  `scripts/benchmark.py`.
* **Alerts**: acknowledge/unacknowledge, archive, delete archived (confirm), filters by status/severity/category/time/text,
  current vs historical state, recovery events, optional rate-limited tray notifications, alert state saved and marked
  *interrupted* if Sentinel was not running when it ended. Nothing is silently discarded.
* **Security**: per-folder exclusions (names or glob patterns; excluded paths are not reported but are still checked by an
  explicit *Verify baseline*), watcher health recovery, exclusion count in the stats line. Still read-only.
* **Data**: schema v3 (events `acknowledged`/`acked_ts`/`archived`, `alert_state`, `watched_folders.excludes`), WAL with
  fallback, `quick_check` on open, backup `*.pre-v2.bak` before upgrading, per-step rollback, clear errors for corrupt, locked,
  newer or unusable databases. Settings: 13 new validated keys, invalid/unknown stored values reported in the audit log,
  export schema 2 (schema 1 still imports).
* **Lifecycle**: `QLockFile` guard; `stop_sentinel.bat` waits for exit, `/force` verifies the process before terminating it;
  tray has show/hide wallpaper; clear message when no tray exists; `diagnose_sentinel.bat`.

## Upgrading from V1
See README → *Upgrading from V1*. The database is upgraded in place after a backup; V1 cannot open it afterwards.

## Needs real Windows verification (not verified)
Embedding on Windows 10 / 11 ≤23H2 (`workerw_sibling`) and 24H2+ (`progman_child`, written from the observed layout);
multi-monitor embedding incl. negative coordinates and mixed DPI; delivery of TaskbarCreated/display/DPI/resume messages
to the hidden helper window; Explorer-restart recovery timing; restore leaving no black area; the `.bat` files; tray;
Start-with-Windows registry code; Celeron J3355 performance; any Windows `.exe` (only a Linux PyInstaller build has ever been run, for V1).

## Known limitations
* Not an antivirus; a changed file is not proof of malware.
* Events table is a `QTableWidget` (~110 ms to rebuild 2000 rows on the dev VM); Projects page still checks folder paths on the UI thread (a disconnected network drive can stall it).
* Each scene has its own frame timer (no shared clock). A brief visible frameless window can appear between `show()` and embedding.
* Exclusions are not applied to baseline verification (by design) and are matched by name/glob, not regular expressions.
* Monitor ids of two identical monitors without serials are position-ordered and may swap if the layout changes.
