> Superseded by V4 (see docs/V4_*.md). Kept as history.

# VYRXONE Sentinel V3 — test report

All results below were produced on a Linux development VM, never on Windows.

## Environment
Linux 6.18, Python 3.13.17, PySide6-Essentials 6.12.0, psutil 7.2.2, watchdog 6.0.0, pytest 9.1.1, 2 logical CPUs,
headless Qt (`QT_QPA_PLATFORM=offscreen`). Command: `python -m pytest -p no:cacheprovider tests`.

## (A) Executed and passed
**323 passed, 2 skipped in about 124 s** (development tree, final run).
* 233 V1 + V2 tests (none deleted or weakened) + 90 new V3 tests (89 + the A6 regression test).
* New by area: storage/migration v4 (8), findings and rules (19), integrity hardening (10), projects/health/Git/overview (21),
  jobs (7), UI (18), desktop (6), plus the A6 regression test (1).
* The same suite was re-run from the **extracted release ZIP in a clean virtual environment** with pinned requirements
  (see "Packaging verification" below), and `python -m sentinel --self-check` passed (schema v4).

## (B) Skipped
2 tests, both Windows-only (`tests/test_v2_lifecycle.py`, startup-registration/`os.startfile` paths). They skip off Windows by design.

## (C) Manual Windows tests (NOT executed)
Everything in `docs/WINDOWS_TEST_CHECKLIST.md`, section I (V3) and the earlier sections. Real wallpaper embedding on both Explorer
layouts, multi-monitor/DPI, sleep/resume, real network shares and removable drives, Windows services, and the Celeron J3355
target PC are **unverified**. The Windows layer is tested only against `FakeWin32` mocks.

## Existing tests modified (assertions otherwise identical)
| Test | Change | Why |
|---|---|---|
| test_app_smoke, test_v2_lifecycle | V2→V3 label/version, schema v4 | version and schema bump |
| test_v2_storage | schema 3→4 (4 places) | schema bump |
| test_v2_appearance_perf | report heading V3, "Database schema: v4" | version and schema bump |
| test_security | `add_file(f, replace=True)` | V3 refuses silent baseline replacement (A2) |
| test_ui | nav count 7→8; Events accessors via model; projects path note waited asynchronously | new Findings page; Events is model/view; path check is async (A5) |

## Defects found by V3's own testing
* A6: force-stop identity check was too loose (could terminate an unrelated process) — fixed, regression test added.
* A7: first monitoring error suppressed when the monotonic clock was < 5 min (fresh boot) — fixed; the test is deterministic.
* Stale project-health display after a path change — fixed during development.
See `docs/V3_AUDIT.md`.

## Benchmarks (`scripts/benchmark.py`, software rendering on 2 vCPUs, not the target PC)
Events reload (median of 7, interleaved runs): V2 renders up to 2,000 rows: 60–66 ms (2k stored) and 65–73 ms (10k stored);
V3 renders a 500-row page: 3.4–6.0 ms and 10–18 ms. Part of this gain is simply fewer rows per page.
At 10k stored rows: filtered search 5.0 ms, sort 5.3 ms, page change 7.3 ms. Findings analysis: 44 ms for 10,000 events
(5,000 examined), 11 ms for 2,000. 1,000 findings: query 4.7 ms, JSON export 55 ms. Overview of 5,000 files: 26 ms.
300 trivial jobs: 38.6 ms. Wallpaper at 1366x768: 13.55% own CPU, 96.9 MiB RSS, 26.6 fps (software rendering).
These numbers do not predict behaviour on a Celeron J3355.

## Packaging verification
See the final section of the delivery message; sizes and results are recorded there from the actual final ZIP.
