# VYRXONE Sentinel V4 — audit of V3

Method: the delivered `VYRXONE-Sentinel-V3.zip` was extracted and used as the starting point. Findings are classed **A** (confirmed
defect, reproduced), **B** (risk that needs Windows or hardware to confirm), **C** (measured performance), **D** (missing feature).
An unverified risk is never listed as a confirmed bug.

## Baseline
* V3 delivery verification (previous session, clean virtual environment, pinned requirements): **323 passed, 2 skipped**.
* In this session the unchanged V3 suite was re-run on the V4 tree before any AI test existed: everything passed except the tests that
  pin the version label, schema number or page count, which were updated (listed in V4_TEST_REPORT.md). No assertion was removed or weakened.
* Environment: Linux 6.18, Python 3.13.17, PySide6-Essentials 6.12.0, psutil 7.2.2, watchdog 6.0.0, pytest 9.1.1, 2 logical CPUs, headless Qt.

## A — confirmed defects
| # | Sev | Where | Reproduction (probe `scripts/audit_probes/probe_v4_audit.py`) | Fix | Regression test |
|---|-----|-------|------------------------------|-----|-----------------|
| A8 | Med | ui/pages/events | On the **V3 tree**: with the Events page visible and *Live* on, `ctrl.alerts_changed.emit()` prints `TypeError: EventsPage._on_new_event() missing 1 required positional argument` and the live-refresh timer is **not** started. (`alerts_changed` has no argument but was connected to a slot that required one.) The V4 tree starts the timer and prints no error. | `_on_new_event(self, _e=None)` | `test_events_page_live_refresh_on_alert_change_regression` |
| A9 | Med | ai/service (new V4 code, found by V4's own test) | Deleting the conversation that is currently open, then sending another message with history on, failed with a foreign-key error and the exchange was silently not saved. | `exists()` check; a new stored conversation is started; `delete_conversation()` resets the open one | `test_history_persist_reload_delete_and_clear` |
| A10 | Low | ai/privacy (new V4 code, found by V4's own smoke test) | The first IPv6 pattern matched clock times such as `12:30:45` and replaced them with `<ip>`. | IPv6 now needs `::` or eight groups | `test_redaction_keeps_harmless_numbers_times_and_versions` |

A9 and A10 existed only in unreleased V4 code and are listed for completeness, not as V3 defects.

## B — risks that need Windows or hardware (NOT proven here)
* Everything listed in V1–V3 audits is unchanged: real embedding on both Explorer layouts, multi-monitor/DPI, sleep/resume, `TaskbarCreated`, real network shares, the Celeron J3355 target.
* Windows Credential Manager through `keyring` 25.7.0 has never run here (the dev VM has no secure backend; the "no backend" path is tested, the "backend present" path is tested with an in-memory stand-in).
* PyInstaller bundling of the `keyring` Windows backend (`--hidden-import keyring.backends.Windows --copy-metadata keyring`) has never been built or launched.
* TLS to the three providers uses Python's `ssl` default context (Windows certificate store). Not exercised against the real hosts.
* **No HTTP/HTTPS proxy support** (standard-library `http.client` is used directly). On a network that requires a proxy the AI Assistant will report "could not reach the provider". This is a design limit, not yet reproduced on such a network.
* Live provider behaviour (real error bodies, rate-limit headers, model availability, free-tier terms) is **unverified**: no API key was available. Provider request/response handling follows the providers' published documentation and is tested only against mocked and local-server HTTP.

## C — measured performance (same VM; software rendering; not the target PC)
| Item | Result |
|---|---|
| Controller + main window construction, median of 5 | V3 77 ms → V4 90 ms (+13 ms) |
| Process RSS after construction | V3 108 MiB → V4 122 MiB (+14 MiB; includes the AI modules and the 9th page) |
| Importing the AI package (includes PySide-dependent service module) | 518 ms, +21.6 MiB measured in a bare interpreter (the app has most of this loaded already) |
| Build context from 50 events (redact + quote) | 3.8 ms median |
| Router round trip with an in-memory transport (provider code, validation, usage) | 0.13 ms |
| Request size for 50 events | 11,668 characters |
| AI disabled | no worker thread running, no timer, no network (asserted by tests) |
These do not predict Celeron J3355 behaviour. Network latency of real providers was not measured.

## D — missing features (scope of V4, implemented)
Provider-independent router, three providers, key storage, redaction, consent dialog, context builder, response validation,
conversation history, usage tracking, AI page and settings, performance/diagnostics lines, migration v5, documentation.

## E — preserved
V1–V3 behaviour and shortcuts (`Ctrl+1…8` unchanged; `Ctrl+9` added for the new page). Existing settings and their defaults are unchanged.
No existing test was deleted or weakened.
