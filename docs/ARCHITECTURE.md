# Architecture and roadmap

## Layers
* **storage/** SQLite via the standard library. `migrations.py` uses `PRAGMA user_version`, runs `quick_check` on open, takes a `.pre-vN.bak` copy before upgrading, rolls a failed step back, and refuses databases from a newer Sentinel (never downgrades). V2 = schema v3 (acknowledge/archive columns, `alert_state`, folder exclusions). **V3 = schema v4**: additive tables `findings`, `finding_events` (evidence snapshots), `project_tasks`, `project_notes`, `project_health`, `project_activity` and extra indexes; the backup of a V2 database is `<db>.pre-v3.bak` (named after the schema it copies). WAL mode with silent fallback. Settings are validated against `DEFINITIONS`; events are pruned to a configurable maximum.
* **monitoring/** `Sampler` is one daemon thread; every metric is optional (`None` + a note) so failures never fabricate data. History buffers are bounded deques.
* **security/** `FolderWatcher` (watchdog) → bounded queue → one worker thread that coalesces bursts (1 s), rate-limits (300/min, with a summary event), ignores Sentinel's own data folder, and checks baseline files. Explicit scans (verify baseline, add folder) run only on user action and are cancellable.
* **security/findings.py + rules.py (V3)** `FindingStore` (lifecycle, filters, streamed export, evidence links) and `FindingsEngine` (eight pure rule functions over a bounded 24 h window of audit events; deterministic, idempotent, rate-limited). Rules only read events; nothing in this layer can act on files, processes or system settings. The controller schedules analysis (4 s debounce + every 5 min) as a coalesced background job. `integrity.py` (V3) detects change-during-hash, refuses symlinks, requires `replace=True` to overwrite a baseline and returns a `VerifyReport` whose `complete` flag drives `baseline_verified` vs `baseline_verification_incomplete`.
* **projects/** `tasks.py` (tasks, notes, activity, user-recorded test/build results), `health.py` (`bounded_call` daemon-thread timeout with a cap on abandoned calls, `compute_health`, cached `HealthStore`, edge detection so unavailable/restored are logged once), `overview.py` (bounded, cancellable, excludes generated folders). Original: user-entered records; Git status uses argument lists, `shell=False`, `--no-optional-locks`, `core.fsmonitor=false`, an 8 s timeout, and never overrides Git's `safe.directory` protection.
* **platform/windows/** `desktop_layer.py` (ctypes/user32+kernel32 behind one injectable `RealApi`; Progman/WorkerW embedding with verification, Explorer-ownership check and detach), `shell_events.py` (native event filter for TaskbarCreated, WM_DISPLAYCHANGE, WM_DPICHANGED, resume; `classify` is pure) and `autostart.py` (HKCU Run key with stale-path detection). All import safely and report "unsupported" on other systems. Tests inject a fake Win32 (`tests/fakes.py`).
* **core/ (V2)** `alerts.py` (persistent alert state, rate-limited notifications), `appearance.py`, `monitors.py` (stable ids, native rectangles, selection), `recovery.py` (bounded retry policy), `instance.py` (lock file, verified force-stop). `diagnostics.py` builds the redacted report.
* **controller.py** the only object the UI talks to. Background threads emit private signals connected to bound methods of this main-thread `QObject`, so all widget work happens on the UI thread.
* **ui/** `wallpaper.py` (QPainter scene), `wallpaper_host.py` (one scene per monitor keyed by stable id; embedding / single labelled window fallback / event-driven recovery with `DesktopStatus`; screen-signal reconcile, debounced), `pages/`, `tray.py`, `jobs.py` (bounded `QThreadPool` + V3 bounded queue, cooperative timeouts, key coalescing, history/slow-op status), `pages/findings.py`, `pages/project_tabs.py`, and a model/view `pages/events.py`.

## Lifecycle
`app.py` → lock file (`QLockFile`, closes the simultaneous-start race) → ping check → single-instance channel (local socket; whitelisted commands `ping|show|quit`) → database → controller → host → window/tray. Exit (`quit`) sets a `quitting` flag so no window can veto it, then `shutdown()` stops the host, sampler, watcher and job pool and closes the database, in that order.

## Roadmap (five versions only — there is no V6)
| Version | Scope |
|---|---|
| **V1** | Animated wallpaper, real system monitor, local event log, basic folder monitoring and integrity baselines, project manager, settings, packaging. |
| **V2** | Better desktop integration and multi-monitor handling, reliability, appearance settings, performance tuning, alert management. |
| **V3** (this) | Richer security analysis (explainable rules, correlation, lifecycle), stronger integrity/watcher reliability, project intelligence, faster event history. |
| V4 | Optional AI explanations / natural-language queries through user-configured providers, with explicit consent before any external data sharing. (V1 has no AI code and no network code.) |
| V5 | Final integrated release: polished UX, installer/portable distribution, comprehensive tests, stable release process. |

Extension points prepared for later versions: the `EventStore` listener API and event `details` JSON (rules/reports), the `SettingsStore` schema (appearance, alert policy), the `WallpaperHost` (multi-monitor), and the controller's service boundaries (an AI module can be added without touching storage or UI code).

## V2 data flow for alerts
Sampler thread → `controller._handle_sample` (UI thread) → `ThresholdEngine` → audit event + `alert_state` row + optional rate-limited tray message. On the next start, rows still `active` become `interrupted` with an explicit event. Security warnings reach the user the same way. Events carry `acknowledged` / `archived`; `prune` never removes unacknowledged warnings.

## V3 data flow for findings
Audit events (security, system, project) → `controller._handle_event` schedules one debounced job → `FindingsEngine.analyze` reads the last 24 h (≤5,000 relevant events, indexed) → rules produce `Candidate`s → `FindingStore.record` inserts or merges (dedup by stable key; new evidence only) → controller logs `finding_opened` (a *notice*, never a *warning*, and not an input to any rule) → `findings_changed` refreshes the page. Performance warnings (RES-004) are a separate *resource* category and never feed integrity findings. Pruning skips events referenced by open findings, and each finding stores snapshots of its event references.

## V3 background work
Everything that can block (path checks, Git, overview, hashing, verification, analysis, exports) runs in `JobRunner` jobs: 2 worker threads, 32 queued jobs max, timeouts (cooperative cancel + late results discarded), coalescing by key. Path checks use `health.bounded_call` so a dead network drive cannot hold a worker: at most 4 abandoned daemon threads, then immediate "unreachable". The Performance page shows job counts, slow operations (>0.5 s) and abandoned checks.

## V4 AI layer (optional, off by default)
`src/sentinel/ai/` is independent of the UI except `service.py` (a QObject owning a one-worker `JobRunner`, max 2 pending).
Flow: the user selects context on the AI page → `context_builder` (redact with `privacy.Redactor`, quote as untrusted data) →
`compose_request` → **review dialog** (provider/model, exact text, fields; Cancel is the default) → `AIService.send(confirmed=True)` →
worker thread → `AIRouter.route` → provider (`gemini`, `groq`, `openrouter`; each builds its own request and normalises its own
response) → `transport.Transport` (stdlib `http.client`, HTTPS only, no redirects, size cap, cancel closes the socket) →
`response_validator` → UI (plain text, never markup). Keys live only in `secrets.SecretStore` (keyring or environment variable)
and are read inside the worker. `AIService.send` refuses when AI is off or the request is not confirmed, so there is no code path
that sends data automatically. Persistence: `conversation_store` (schema v5) only when history is enabled.
Failure isolation: a provider exception is caught by the router; permanent errors stop the request; a refusal is never forwarded.

## V5 additions (final edition)
* `ai/proxy.py` validates proxy host/port/credentials; `ai/transport.py` opens a CONNECT tunnel itself (stage-precise errors: proxy / proxy sign-in / certificate / network), then negotiates TLS with the provider using a verifying context. The proxy is looked up per request through `AIService.proxy_settings()`, which raises (fail closed) rather than returning "direct" when a proxy is enabled but unusable.
* `ai/secrets.py` stores the proxy sign-in beside the provider keys; `ai/keyring_selftest.py` backs `--keyring-selftest`.
* Every AI network entry point (send, test connection, model list, test proxy) is gated on `ai_enabled` in `AIService`.
* `controller.quality` applies Lightweight mode on top of the user's settings; the sampler interval floor is 4 s in that mode.
* Schema unchanged (v5). Settings partitions: `V2_END=30`, `V3_END=32`, `V4_END=55`, V5 keys last.

## Known structural limits
Each wallpaper scene still owns its own frame timer (no shared clock). Job timeouts are cooperative (a worker that ignores cancel keeps its slot). Git calls are bounded by timeouts, not interruptible mid-command. Real Windows behaviour of the desktop layer is unverified. See docs/V3_AUDIT.md.
