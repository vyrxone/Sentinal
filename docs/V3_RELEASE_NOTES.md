> Superseded by V4 (see docs/V4_*.md). Kept as history.

# VYRXONE Sentinel V3 — release notes

V3 is a **defensive-analysis, correlation, project-intelligence and performance** release built on the V2 code base (nothing
was rewritten). It adds **no AI and no network code**; nothing from V4/V5 was started. Roadmap: V1 → V2 → **V3** → V4 → V5
(there is no V6). Next planned milestone: **V4 – optional AI explanations with privacy controls and explicit consent** (not started).

Everything below was tested on Linux (headless Qt, fake Windows desktop). **Real Windows behaviour is not verified**;
see `docs/WINDOWS_TEST_CHECKLIST.md`.

## What changed

### Security findings (new Findings page)
* Eight small, local, read-only, **explainable** rules over the audit log (`security/rules.py`): changed/missing baseline file,
  several baseline files changed together (correlation), same file changing repeatedly, unavailable watched folder, repeated
  resource warnings (kept as a separate *resource* category), monitoring failures, bursts of file activity, unavailable project folder.
* Each finding has a stable id, rule id + version, severity, title, explanation, evidence, observation times, affected resource,
  a plain-language certainty statement (no false precision), a recommended **safe** next step, related event ids and a status.
* Correlation is bounded (10-minute window from the first event), says *why* events were grouped, is deduplicated (a second run
  over the same events changes nothing), rate-limited (20 new findings/hour; existing ones still update), and deterministic.
* Lifecycle: acknowledge, resolve, dismiss (optional reason), reopen. A resolved finding **reopens** when new evidence arrives;
  a dismissed one stays dismissed unless the situation gets worse. Filter by status/severity/category/date, search, inspect
  evidence, export JSON/CSV **with event references** (CSV formula-injection safe, streamed, atomic).
* Evidence is never deleted: events referenced by an open finding are skipped by pruning, and each finding keeps a small snapshot
  of every referenced event.
* Settings: *Analyse the audit log* (on/off) and *Findings sensitivity* (low / normal / high thresholds).
* **Limits, stated in the app:** findings are not proof of malicious activity; Sentinel does not detect malware, ransomware or
  persistence, does not identify which program changed a file, and has no process-behaviour or network-anomaly rules.
  Sentinel never deletes, quarantines, kills, disables, uploads or changes system settings.

### Safety fix: force-stop identity (A6)
`stop_sentinel.bat /force` could have terminated an unrelated Python process whose command line merely contained the install folder name. Identification now uses the executable and `-m sentinel` / `launch_sentinel.pyw` arguments only.

### Integrity and watcher reliability (fixes A1–A4, see `docs/V3_AUDIT.md`)
Changed-during-hashing detection; no silent baseline replacement (confirmation required); symlinks refused and never followed;
cancelled/interrupted verification is reported as **incomplete**, never as success; per-status counts (unchanged / changed /
missing / inaccessible / unstable) in the verification event; "last verification" status derived from the audit log.
Verification is off the UI thread, streams files in 1 MiB chunks (bounded memory), and is cancellable.

### Project intelligence (Projects page)
* **Health** tab: folder availability, read-only Git (branch, clean/dirty, counts, first 20 changed files, last commit), task
  counts, last note, *last fully healthy* time, check duration. Test/build status appears **only** if you record it – Sentinel never
  runs your commands and never invents a status. Nothing is scanned automatically; only the cheap "is the folder reachable?" check
  repeats every two minutes (off the UI thread).
* **Tasks** (status, priority, optional due date, overdue marker, search/filter), **Notes** (timestamped timeline), recent
  **activity**, **Overview** (user-triggered file/language counts, README, test configuration; skips virtual environments,
  `node_modules`, build output, caches; limits: 20,000 files / 15 s / depth 12; cancellable; nothing leaves the PC).
* Disconnected drives can no longer freeze the app (fix A5): bounded call + timeout, at most 4 abandoned hung checks.

### Performance
* Events page is a `QTableView` with a paged model (500 rows/page), sortable columns, stable selection, in-place updates when only
  flags change, streamed cancellable export. Measured numbers: `docs/V3_AUDIT.md` §C and `docs/V3_TEST_REPORT.md`.
* Job runner: bounded queue (32), optional timeouts (cooperative cancel + discard late result), coalescing by key, history and
  slow-operation diagnostics.
* Performance page: monitoring status, last sample age (**"unavailable" instead of fake zeros**), folder-watching status, job status,
  slow operations, findings analysis time, abandoned folder checks. The diagnostic report includes the same (redacted).

### Database: schema v3 → v4
Additive migration (new tables `findings`, `finding_events`, `project_tasks`, `project_notes`, `project_health`, `project_activity`
plus indexes); V2 tables are untouched. A consistent backup `sentinel.db.pre-v3.bak` (a copy of the V2-schema database) is written
first; each step is one transaction and rolls back completely on failure; newer schemas are refused; no downgrade.
Two new settings keys default sensibly on upgrade. The settings export format stays schema 2.

## Install / upgrade
Extract the ZIP into a **new** folder, double-click `run_sentinel.bat` (details in README). An existing V2 data folder is reused and
upgraded in place after the backup. A V2 build refuses a v4 database, so keep the `.bak` if you may go back.

## Known limitations
* No Windows was available: embedding, multi-monitor, event delivery, scripts, tray, autostart, exe build and Celeron performance are unverified.
* Findings only cover what Sentinel observed while running; gaps (app closed, folder unavailable) are not observed.
* Job timeouts are cooperative: a worker that ignores cancel still occupies its slot until it returns (visible on the Performance page).
* Git cancellation is between commands; each Git call is bounded by an 8 s timeout.
* "Findings" is page 8 in the navigation (appended to keep existing shortcuts stable); `Ctrl+8` opens it.
