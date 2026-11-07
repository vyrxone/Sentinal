# VYRXONE Sentinel V5 — Audit of V4

Scope: the delivered `VYRXONE-Sentinel-V4.zip`, extracted fresh. Baseline run before any change: the unmodified V4 suite
reported no failures (494 tests: 492 passed, 2 Windows-only skipped, counted from the run output). Environment: Linux
dev VM, Python 3.13, PySide6-Essentials 6.12.0, psutil 7.2.2, watchdog 6.0.0, pytest 9.1.1, offscreen Qt.
Nothing in this audit was run on Windows or against a live provider.

Each A finding below was reproduced by a test (or probe) on the **V4 code first**; the actual result is quoted.

## A — Confirmed, reproducible defects

### A11 — Connection test and model-list fetch ran while AI was switched off
* **Where:** `ai/service.py` (`test_connection`, `list_models`), `ui/pages/ai_settings.py` (button gating).
* **Reproduction:** AI off (the default), a key stored, press *Test connection* or *Fetch model list*.
  `tests/test_v5_regressions.py::test_a11_…` on V4: **FAILED** — the transport was reached (`unscripted request to
  https://generativelanguage.googleapis.com/v1beta/models?pageSize=1`).
* **Root cause:** only `send()` checked `ai_enabled`; the two helper calls went straight to the router.
* **Fix:** both refuse locally when AI is off (message explains that nothing is sent by turning AI on); the buttons are
  disabled with a tooltip. Applies equally to the new proxy test.
* **Result on V5:** passes. Three V4 tests (`test_flood_of_connection_tests_is_bounded`,
  `test_test_connection_button_sends_no_personal_data`, `test_model_list_fetch_fills_the_editable_combo`) assumed the old
  behaviour; each now switches AI on first. Their assertions were not changed.

### A12 — A Gemini model ID could be shown as "ready" yet always fail to send
* **Where:** `ai/providers/gemini.py`, `providers/base.py`.
* **Reproduction:** model `google/gemini-x`, `gemini-x:free` or `a/b/c` (valid for Groq/OpenRouter): `validate_configuration`
  returned no problems, but `build_request` raised. V4: **3 FAILED**.
* **Root cause:** the shared validator allows `/` and `:`; Gemini's path builder does not.
* **Fix:** Gemini overrides the check with its own rule (`models/` prefix still accepted). Groq/OpenRouter slash IDs
  still validate (tested).
* **Result on V5:** passes.

### A13 — `Retry-After` in HTTP-date form was ignored
* **Where:** `ai/transport.py`, `providers/base.py`.
* **Reproduction:** `Retry-After: <HTTP date 90 s ahead>` on a 429 → V4 parsed only numbers, so the provider got the
  default 60 s cooldown. V4: the new `parse_retry_after` does not exist → **FAILED**.
* **Fix:** one `parse_retry_after` (numbers or HTTP dates; past dates → 0; invalid/negative/NaN/inf/≥24 h → ignored),
  used everywhere. The router still caps cooldown at 300 s.
* **Result on V5:** passes.

### A14 — When the overall request deadline expired, the network call kept running
* **Where:** `ai/service.py` (`send`).
* **Reproduction:** a provider that never answers; deadline shrunk to 0.4 s in the test. The UI got TIMEOUT but the router
  stayed busy (V4: **FAILED** `assert … not router._busy.locked()`, "the in-flight request kept running").
* **Root cause:** the job runner's timeout only discards the result; the socket was never closed, so the single worker
  stayed occupied until the socket's own timeout and the next request would be refused as BUSY.
* **Fix:** the timeout path cancels the request's `CancelHandle` (closes the socket) before reporting.
* **Result on V5:** passes.

### A15 — OpenRouter "in-flight budget" 402 was treated as a billing error
* **Where:** `ai/providers/openrouter.py`.
* **Source:** OpenRouter's official errors page: a 402 whose `error.metadata.limit_source` is
  `openrouter_in_flight_budget` carries `Retry-After` and is a wait-and-retry case; other 402s mean exhausted credits.
* **Reproduction:** that response classified as `permission` on V4 (checked directly on the V4 code).
* **Fix:** classified as `rate_limit` with the Retry-After; other 402s stay `permission`. Also handled inside an HTTP 200 body.
* **Result on V5:** passes.

*Not defects, cleaned:* ~30 unused imports/variables in `src/` (pyflakes) were removed; remaining pyflakes notes are
three f-strings without placeholders, one unused local and an intentional availability probe (`watchdog`).

## B — Unverified risks (need real Windows / real credentials)
* Windows Credential Manager through `keyring` (store / read / replace / delete) — **never run on Windows**. V5 adds
  `--keyring-selftest` so you can run it (see V5_WINDOWS_VALIDATION.md).
* The packaged PyInstaller EXE (keyring backend bundling: hidden imports and metadata were added to `build_exe.py`) — not built here.
* Progman/WorkerW embedding, Windows 11 layouts, Explorer restart, sleep/resume, lock/unlock, multi-monitor, mixed DPI,
  display hot-plug, tray, autostart, wallpaper restore — all code unchanged since V4 and still unverified.
* Gemini, Groq and OpenRouter live behaviour — no key was available. Request/response formats were compared with the
  providers' official documentation pages (see AI_PROVIDER_SUPPORT.md); that is a document comparison, not a live test.
* Proxy behaviour against real corporate proxies (NTLM/Kerberos, TLS-inspecting proxies, PAC files) — only a local CONNECT proxy was tested.
* Celeron J3355 / 4 GB performance — never measured.

## C — Measured findings (dev VM, not the target PC)
* **Closed `MainWindow` objects are never freed** (12 windows → 12 stay alive, ≈12 MiB each; closures connected to
  controller signals hold them). *Not reachable in the shipped flow*: `app.py` creates one window per process and closing
  hides it to the tray. Not fixed in the final release to avoid a risky refactor of eleven pages; recorded in V5_KNOWN_LIMITATIONS.md.
* Threads: 1 → 1 after 15 panel open/close cycles and after 200 AI requests; jobs queue returns to 0.
* RSS after 200 AI requests: +2 MiB. Numbers in V5_PERFORMANCE_REPORT.md.

## D — Missing or incomplete features (status in V5)
| Item | Status |
|---|---|
| Optional HTTP proxy for AI requests | **Implemented** (CONNECT tunnel, secure credentials, test button, fail-closed). `https://` proxies, SOCKS, NTLM/Kerberos, PAC: not supported (documented). |
| Credential Manager verification | **Tooling added**, result unverified (needs Windows). |
| Lightweight mode for 4 GB PCs | **Implemented** (15 fps cap, fewest effects, sampling ≥ 4 s, settings preserved). |
| Automatic low-memory detection | **Not implemented** (manual switch only). |
| "Explain integrity changes" | Works from the Events page (select baseline events → Ask AI). No separate button on the Security page. |
| EXE build and launch | **Not done** (no Windows). Build script updated only. |
| Windows desktop reliability fixes | **None possible without Windows**; no evidence of a defect was found by code review either. |

## E — Working features that must be preserved (all covered by the retained V1–V4 tests, which still pass)
Wallpaper/desktop layer with labelled fallback, multi-monitor/DPI logic, Explorer-restart and sleep/resume recovery, restore
desktop, presets, monitoring, alerts, watcher recovery, SHA-256 baselines, findings lifecycle, correlation, paging, safe
exports, projects/Git/tasks/notes, the AI router and providers, privacy review, history, diagnostics, settings
import/export, single instance, clean shutdown, shortcuts. No test was deleted, skipped or weakened.
