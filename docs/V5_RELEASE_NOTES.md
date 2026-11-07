# VYRXONE Sentinel V5 — Final Edition (5.0.0)

V5 is the last version of the roadmap (V1→V5). There is no V6.

## New
* **Optional proxy for AI requests** (AI Assistant → Network): HTTP proxy through a CONNECT tunnel, optional Basic sign-in kept in
  the Credential Manager, validation, *Test proxy* (TLS handshake only, no data), fail-closed (never falls back to direct),
  provider certificate always verified, import cannot switch the proxy on.
* **Credential Manager self-test:** `--keyring-selftest` (source or EXE) with throw-away entries and an unrelated canary.
* **Lightweight mode** for 4 GB / integrated graphics.
* New error kinds and messages: certificate, proxy, proxy sign-in.
* PyInstaller script bundles the keyring Windows backend modules and metadata (untested on Windows).

## Fixed (details and regression tests: V5_AUDIT.md)
A11 connection test/model list ran while AI was off · A12 Gemini model-ID validation mismatch · A13 Retry-After HTTP-date ·
A14 deadline did not cancel the in-flight request · A15 OpenRouter 402 in-flight budget · unused imports cleaned.

## Unchanged on purpose
Database schema (still v5 — no persistence change was needed), all V1–V4 behaviour and tests, desktop-layer code.

## Not done / not verified
See V5_KNOWN_LIMITATIONS.md and V5_WINDOWS_VALIDATION.md. In short: nothing was run on real Windows, no live provider call was
made, the EXE was not built, and the Celeron was not measured.

## Changelog V4 → V5
Version 5.0.0 · `ai/proxy.py`, `ai/keyring_selftest.py` added · `ai/transport.py` (proxy tunnel, `parse_retry_after`, certificate error)
· `ai/secrets.py` (proxy sign-in) · `ai/service.py` · `ai/router.py` · `providers/{gemini,openrouter,base}.py` · settings +5
(`ai_proxy_*`, `lightweight_mode`) · UI Network tab and lightweight checkbox · tests +73 · docs V5_*.
