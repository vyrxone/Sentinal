# VYRXONE Sentinel V2 — test report

> **Superseded by V3** (see `docs/V3_TEST_REPORT.md`). These are the V2 results; the V3 suite includes all of these tests.


Environment: Linux container, Python 3.13.17, PySide6-Essentials 6.12.0, psutil 7.2.2, watchdog 6.0.0, pytest 9.1.1,
`QT_QPA_PLATFORM=offscreen`, 2 logical CPUs, 7.8 GiB RAM. **No test or measurement here was run on Windows.**

## A. Linux-runnable automated tests (executed)
`QT_QPA_PLATFORM=offscreen python -m pytest` → **233 passed, 2 skipped in ≈71 s** (the 2 skips are section B).
135 are the V1 tests; 98 are new (`tests/test_v2_*.py` and `tests/fakes.py`). No V1 test was removed, skipped or weakened.
Four V1 assertions were updated because the behaviour they pinned legitimately changed: version label `V1`→`V2`,
self-check `schema v2`→`schema v3`, navigation list/count 6→7 (new Performance page).

| Area | What the new tests prove |
|---|---|
| Storage / migration | fresh v3 DB; V1→V2 upgrade keeps events, settings, folders, baselines, projects and writes `.pre-v2.bak`; failed migration rolls back with a clear error; process killed mid-migration is recoverable; connection closed on failure (A1); corrupt, locked, newer-schema and unusable-folder databases give clear errors and are never deleted/downgraded; settings with missing V2 keys, invalid values and unknown keys; 13 new validated settings; presets; schema-1 import |
| Alerts | prune protects unacknowledged warnings (A2); ack/unack/archive/delete-archived; status/severity/category/time/text filters; persistence across reopen; threshold alert → recovery event; rate-limited and disableable notifications; restart marks active alerts *interrupted* |
| Security | observation timestamps (A3); exclusions as documented; health check detects removed folder (one event), recovers when it returns and the new watch really reports files (A4); health check runs off the UI thread |
| Desktop (fake Win32) | both Explorer layouts; third-party shell refused; attach failures never raise and restore style; negative coordinates; shell-message classification; bounded recovery; Explorer restart recovery after retries; exhaustion → exactly one labelled window; restore during recovery cancels retries; storm limit; attach exception leaves no visible fullscreen window (A5); repeated show/hide leaks no scenes |
| Multi-monitor | stable ids, duplicate disambiguation, native-pixel rectangles, selection modes and explained fallbacks, one scene per monitor, no duplicates on repeated display events, unplug/replug, resolution change, partial failure reported |
| Appearance | dimmer, clock, emblem size, particles, pulses, overlay on/off/expanded and each preset change pixels (deterministic render at fixed time); reduced motion stops the frame timer; settings persist; effective quality under load |
| Performance | measured FPS/paint time reported and cleared; bounded self-metrics; process scan throttled; Performance page values and timer stop when hidden; redacted diagnostics (also via CLI) |
| Lifecycle | lock exclusivity/stale lock; **6 simultaneous processes → exactly 1 survives**; force-stop refuses an unrelated Python process and terminates a verified Sentinel-looking one; `--stop` waits for exit; repeated start/stop leaks no threads/scenes; monitoring toggles do not stack sampler threads; no duplicate signal connections; stop script cannot kill by name; run script has no parse-time-expansion trap (static check); tray entries and tray-unavailable feedback; stale autostart classification |

A bug in V2's own code was found by these tests while writing them: the second-instance path crashed on this PySide6's
`getLockInfo()` return shape (fixed, covered by the race test).

## B. Windows-only automated tests (written, **not executed**)
`test_windows_real_api_loads_and_finds_progman`, `test_windows_embed_and_detach_roundtrip` in `tests/test_v2_lifecycle.py`
are skipped off Windows (`skipif sys.platform != "win32"`). Run `run_tests.bat` on Windows and record the result here: ____

## C. Manual Windows tests (not executed)
`docs/WINDOWS_TEST_CHECKLIST.md` sections B–H. Results: ____ (Windows build ____ , displays ____ , method ____ ).

## Audit probes (executed against the V1 baseline)
`scripts/audit_probes/probe_audit.py <src>` reproduced A1–A5. `probe_race.py <src> 5` (3 runs) left exactly one survivor on V1 as
well, so the simultaneous-start race stayed class B and the lock file is defensive.

## Benchmark (`scripts/benchmark.py`, executed on the dev VM only)
Software rendering, offscreen, 2 CPUs. **This is not the Celeron J3355 and says nothing certain about it.**
| Measurement | Result |
|---|---|
| Animated-region paint, 1366x768 (median / p95) | Sentinel Core 7.0 / 8.6 ms; Crimson Watch 9.6 / 10.2 ms; Silent Guardian 3.6 / 4.3 ms |
| Animated-region paint, 1920x1080 | Sentinel Core 11.2 / 13.0 ms; Crimson Watch 15.4 / 16.4 ms; Silent Guardian 6.2 / 6.9 ms |
| One monitoring sample | 0.27 ms without process scan; 4.3 ms (max 9.8) with |
| Whole app animating 20 s at 1366x768, default preset | 13.7 % of total CPU, 95.7 MiB RSS, 26.0 fps measured, 11.4 ms paint |
| Events page, 2000 rows reload | ≈109 ms |
The 13.7 % figure includes offscreen software rendering plus the benchmark's busy event loop; the idle cost on Windows will differ.
To reproduce: `python scripts/benchmark.py --seconds 20 --json result.json`, then run it on the target PC and add the numbers here: ____

## Not verified (honest list)
Everything in "Needs real Windows verification" in `V2_RELEASE_NOTES.md`, plus: the Windows `.exe` build, SmartScreen behaviour, GPU/driver effects, and any claim of smooth animation on a Celeron J3355.
