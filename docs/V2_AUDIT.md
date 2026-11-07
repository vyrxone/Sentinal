# VYRXONE Sentinel V2 – Audit of V1

Method: the V1 source (not its README or completion report) was extracted and inspected.
The V1 suite (135 tests) was run first and passed. Suspected defects were then tried with
reproducible probe scripts (`scripts/audit_probes/`, run with `python -I probe_X.py <V1 src dir>`).
Only findings with probe evidence are class A. Everything else is class B or C.

## A – Confirmed defects (evidence from probes on Linux, headless)

| # | Sev | Module | Reproduction | Expected | Actual | Fix | Regression test |
|---|-----|--------|--------------|----------|--------|-----|-----------------|
| A1 | Med | storage/database | `probe_audit.py` P1: make `migrate` raise | connection closed | 1 connection left open | close in `__init__` on failure | test_db_robust |
| A2 | High | storage/events_store | P2: 1 warning then 10 info, `prune(5)` | unacknowledged warnings kept | warning deleted | prune protects unacknowledged warning/notice; logs count | test_alerts |
| A3 | Med | security/service | P3: source of `_handle` | event carries observation time | `ts` not passed; timestamp = DB write time | pass observed `ts` | test_security |
| A4 | Med | security/service | P4: grep | `refresh_folders` used after folder changes | never called | call after add/remove/health check | test_security |
| A5 | High | ui/wallpaper_host | P5: `attach` raises | no stray window; error reported | exception propagates, fullscreen frameless scene stays visible | try/except, hide + fall back | test_host |

Not confirmed by probes (kept out of class A): P6 events-page reload of 2000 rows took 54 ms on the dev VM, so it is only a
minor risk (B). The simultaneous-start race did not reproduce in 3 runs of 5 processes (`probe_race.py`): 1 survivor each
time. It stays class B; a QLockFile guard is added defensively.

## B – Potential risks, need validation (not proven)

- Windows 11 24H2 `progman_child` path was written from observed layout, never run on hardware.
- `run_sentinel.bat` uses `%VAR%` inside a parenthesized block after `call` (parse-time expansion). Reasoned, not executable here.
- UI-thread filesystem calls on disconnected network drives (Projects path checks).
- Stale Start-with-Windows registry path after moving the folder.
- `stop_sentinel.bat` has no fallback for a hung app.
- Behaviour on Explorer restart, display change, sleep/resume, mixed DPI: logic exists, untested on Windows.
- Low-end (Celeron J3355) performance: unmeasured.

## C – Cosmetic / structure

- Alerts/events cannot be acknowledged, archived or filtered by status.
- No wallpaper appearance controls beyond intensity/FPS/overlay.
- Settings import has no validation report.

## D – Working V1 behaviour to preserve

Navigation and pages, settings store, event store and export (with CSV formula guard), baselines, watcher, projects/Git status,
threshold hysteresis, IPC whitelist, tray, quality reduction under load, existing 135 tests.
