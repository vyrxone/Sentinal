# V5 Test Report

## Environment (exact)
Linux 6.18 dev VM, 2 logical CPUs, Python 3.13.17, PySide6-Essentials 6.12.0, psutil 7.2.2, watchdog 6.0.0, pytest 9.1.1,
`QT_QPA_PLATFORM=offscreen`, `openssl` CLI (for test certificates). Venv `/home/claude/venv-test` and a clean venv for the extracted ZIP.

## Commands and results (executed)
| Check | Command | Result |
|---|---|---|
| V4 baseline (before any change) | `python -m pytest` on the extracted V4 ZIP | no failures; 494 tests (492 passed + 2 Windows-only skipped, counted from the run output) |
| Full suite, source tree | `python -m pytest -rs` | **566 passed, 2 skipped** (3 min) |
| Full suite, extracted V5 ZIP in a clean venv (pinned `requirements.txt` + pytest 9.1.1) | `python -m pytest -rs` | **566 passed, 2 skipped** (3 min) — run on the ZIP built from this exact source; the ZIP was then rebuilt once, changing only this report |
| Syntax | `python -m compileall -q src tests scripts` | OK |
| Static | `python -m pyflakes src` | 4 minor notes (3 f-strings without placeholders, 1 unused local) plus an intentional availability import |
| Dependencies | `pip-audit -r requirements.txt --no-deps` | no known vulnerabilities (Linux resolution; Windows-only `keyring` may not have been included) |
| Secret scan | `test_repository_contains_no_key_shaped_text` + `package_zip.py` scan | clean (one allow-listed file of fake scrubber fixtures) |
| Self-check (extracted ZIP) | `python -m sentinel --self-check` | passed (schema v5; AI key storage reported *not available* on Linux, as expected) |
| `--version`, `--keyring-selftest` (extracted ZIP, Linux) | | `VYRXONE Sentinel V5 (5.0.0)`; self-test reports no secure store and exits 1 (correct off Windows; **not** a Windows result) |
| Audit probe | `scripts/audit_probes/probe_v5_audit.py` | 8 passed (A11–A15 reproductions) |
| ZIP integrity | `python -m zipfile -t` | OK; 174 files; verified by `package_zip.py` (required files, no caches/venvs/DBs/logs/secrets/placeholders) |
| AI local cost | `scripts/benchmark_ai.py` | context build 4.1 ms (50 events), router round trip 0.22 ms (dev VM) |
| Benchmarks | `scripts/benchmark_v5.py`, `scripts/benchmark_ai.py` | V5_PERFORMANCE_REPORT.md (dev VM only) |

## New tests (74, all mock/local — no live API, no real key, no real proxy)
* `tests/test_v5_regressions.py` (14): A11–A15, keyring self-test, lightweight mode, settings partition.
* `tests/test_v5_proxy.py` (52): **MOCK INFRASTRUCTURE** — local TLS server with a throw-away CA and a local CONNECT proxy. Covers direct
  connection, proxy success, proxy never seeing key/body, proxy sign-in (success, wrong, missing), proxy offline, timeout,
  403/502/302 refusals, injected data, certificate validation (unknown CA, wrong host, through the tunnel), no redirect
  following, cancellation, no credential leakage, probe, host/port/credential validation, import cannot enable the proxy,
  secure-store behaviour incl. unrelated entries untouched, preview == wire body through the proxy, fail-closed, reset.
  A mutation check (bypassing the proxy in the transport) made 17 of these fail, confirming they test real behaviour.
* `tests/test_v5_ui_db_privacy.py` (8): Network tab, buttons disabled while AI off, V4 database opens in V5 with no backup and no new tables across repeated starts, no secrets in SQLite/export/logs, source-tree secret scan, AI-off idleness, repeated requests (threads/jobs bounded), shutdown during a request.
Additionally executed by hand (not automated): a database written by the real V4 code opened by V5 twice (schema 5, data intact, no `.bak`), and a V5-written database opened by V4 code (opens; unknown V5 settings ignored).

## Existing tests modified (no assertion weakened)
Version pins (`V4`→`V5`, `4.`→`5.`) in `test_app_smoke`, `test_v2_appearance_perf`, `test_v2_lifecycle`; the sample config gained the five new
keys (`test_sample_config_file_is_valid` counts them); three tests that assumed connection tests work while AI is off now switch AI on first.

## Skipped
2 tests that need real Windows user32/kernel32 and an Explorer session (`test_v2_lifecycle.py:242, :253`).

## Not executed (no Windows / no keys)
All of V5_WINDOWS_VALIDATION.md (30 manual tests: NOT TESTED), live Gemini/Groq/OpenRouter, real proxies, the PyInstaller EXE,
`.bat` scripts (static review only), the Celeron J3355. Migration paths V1/V2/V3 → current are covered by the retained V2/V3/V4
tests; V4 → V5 needs no migration (schema unchanged) and was checked as described above.
