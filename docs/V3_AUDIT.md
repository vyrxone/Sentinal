> Superseded by V4 (see docs/V4_*.md). Kept as history.

# VYRXONE Sentinel V3 – Audit of V2

Method: the delivered `VYRXONE-Sentinel-V2.zip` was extracted into a clean folder and the **source** was read (not the
V2 README or reports). The V2 suite was run first as the baseline. Suspected defects were then tried with reproducible
probe scripts (`scripts/audit_probes/probe_v3_audit.py`, run as `python -I probe_v3_audit.py <V2 src dir>`). Only findings
with probe evidence are class A; unproven risks are class B; measured performance is class C. No unverified risk is
presented as a confirmed bug.

## Baseline (before any V3 change)
* Command: `QT_QPA_PLATFORM=offscreen python -m pytest -q -p no:cacheprovider` in the extracted V2 tree.
* Result: **233 passed, 2 skipped** (the 2 skips are Windows-only).
* Environment: Linux container (kernel 6.18), Python 3.13.17, PySide6-Essentials 6.12.0, psutil 7.2.2, watchdog 6.0.0,
  pytest 9.1.1, 2 logical CPUs, headless Qt (`offscreen`).

## A – Confirmed defects (reproduced by probe, headless Linux)

| # | Sev | Module | Probe result in V2 | Fix in V3 | Regression tests |
|---|-----|--------|--------------------|-----------|------------------|
| A1 | High | security/integrity | P1: a file that grew while being hashed was stored with the size read *before* hashing (stored 8,388,608 B, final 8,847,360 B) and accepted silently, so the baseline recorded a hash/size pair that never existed together | size+mtime read before **and** after hashing; one retry; otherwise `FileChangedDuringHash` and nothing is stored; verification reports such files as `unstable` | `test_file_changing_during_hashing_is_not_recorded`, `test_transient_change_is_retried_once`, `test_check_file_reports_unstable_…` |
| A2 | High | security/integrity | P2: `add_files` over an existing baseline replaced the trusted hash with no confirmation (`summary.updated=1`) – a tampered file could be re-baselined by accident | `replace=False` by default → `BaselineConflict`; the UI asks before replacing; "Accept current state" is the explicit path | `test_replacing_a_baseline_needs_explicit_confirmation` |
| A3 | Med | security/integrity | P3: an explicit symbolic link was accepted into the baseline (its target can be outside what the user selected) | links refused (`UnsafeBaselinePath`); a baselined file later replaced by a link is reported as an error and never followed | `test_symlinks_are_refused_and_not_followed` |
| A4 | High | security/service | P4: a **cancelled** verification (4 of 33 files checked) was logged as `baseline_verified`, severity *info*, "Verified 4 baseline file(s): 4 unchanged, 0 need review" – an incomplete scan presented as success | `baseline_verification_incomplete` event ("the remaining files were NOT verified, so this is not a clean result"); never `baseline_verified`; UI says "NOT a complete verification" | `test_cancelled_verification_is_never_reported_as_success` |
| A5 | Med | ui/pages/projects | P5: with a 0.5 s/call file system (stand-in for a slow/disconnected drive) the Projects page reload of 4 projects blocked the UI thread for 0.50 s, and the path field checked the disk synchronously on every keystroke | path checks run in a bounded background job with a timeout; the list reads cached health only; at most 4 hung checks are ever abandoned | `test_projects_page_never_blocks_on_a_hung_folder`, `test_disconnected_drive_does_not_block_and_hung_calls_are_capped` |
| A6 | High | core/instance | Found by V3's own clean-venv run of the V2 test `test_force_stop_refuses_unrelated_python_process`: `looks_like_sentinel` matched the substring `vyrxone-sentinel` anywhere in a command line, so an unrelated Python process started from a folder with that name (e.g. via the project's own venv) was treated as Sentinel and `--stop /force` terminated it | identity is decided from argument tokens only (`-m sentinel`, `launch_sentinel.pyw`, or the `VYRXONE-Sentinel(.exe)` executable name); folder names and other arguments never match | `test_force_stop_identity_uses_argument_tokens_not_path_substrings` (plus the V2 refusal test, now passing from any install folder) |

## B – Risks that need Windows or hardware validation (not proven here)
* Real embedding of the wallpaper on both Explorer layouts (WorkerW sibling / Windows 11 24H2 Progman child) and on multiple monitors.
* Delivery of `TaskbarCreated`, `WM_DISPLAYCHANGE`, `WM_DPICHANGED` and resume broadcasts to the native event filter.
* Behaviour on Explorer restart, display change, sleep/resume, mixed DPI. (V3 adds more mock-based lifecycle tests; mocks do not verify real embedding.)
* The `.bat` launch/stop scripts, tray, Start-with-Windows, the Windows executable build, and everything about the Celeron J3355 / 4 GB target.
* Real offline network-share behaviour (the hung-folder tests use a monkeypatched slow file system, not a real SMB share).

## C – Measured performance (same VM, same session, same method – `scripts/audit_probes/compare_events_reload.py`)
Events page reload ("All time", rows bulk-inserted, median of 7):

| Stored rows | V2 (QTableWidget, newest 2,000 rendered) | V3 (QTableView + paged model, 500 rendered) |
|---|---|---|
| 2,000 | 60.0 – 65.6 ms | 3.4 – 6.0 ms |
| 10,000 | 64.9 – 73.2 ms | 10.1 – 17.9 ms |

Two runs of each, interleaved; ranges are the medians of each run. Caveats: V3 renders 500 rows per page instead of
2,000 (so part of the gain is simply fewer rows per page – the rest of the history is reachable by paging and sorting, which
V2 could not do), V3 runs an additional `COUNT(*)`, and this is a 2-core Linux VM with software rendering, **not** the target PC.
V2 was "not a big problem" at these sizes; the V3 change is about bounded rendering and responsiveness at larger histories.

## D – Missing features (V3 scope)
Findings engine with rules/correlation/lifecycle/export; evidence preserved across pruning; project health report, tasks,
timeline notes, user-triggered repository overview; background-job timeouts, bounded queue, coalescing, status and slow-operation
diagnostics; Performance-page background status; DB-backed event paging and sorting; streamed, cancellable exports; schema v4.

## E – V2 behaviour that must be preserved (and is, by the 233 V2 tests)
All wallpaper features (presets, FPS cap, reduced motion, clock, overlay, tray, monitor modes, restore, Explorer recovery,
honest status/fallback), alert acknowledge/archive, performance page and diagnostics, security exclusions and watcher recovery,
settings import/export, single-instance lock and `stop_sentinel.bat /force`, V1→V2 data migration.

## A7 (found by the V3 test suite, introduced and fixed within V3)
`Controller._last_monitor_error` started at `0.0`, so on a machine whose monotonic clock was under 5 minutes (just after boot)
the first monitoring error was swallowed by the rate limiter. Now starts at `-inf`. Covered by
`test_monitoring_errors_are_logged_rate_limited_and_recovery_noted`, which failed deterministically on a freshly started VM.
