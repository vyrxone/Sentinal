# VYRXONE Sentinel V4 — release notes ("Sentinel Intelligence")

V4 is **not a rewrite** of V3. It adds an optional AI Assistant. The roadmap is V1 → V2 → V3 → V4 → V5 (final); there is no V6.

## What changed from V3
* **AI Assistant page** (`Ctrl+9`): chat, context picker, review dialog, Stop, Retry, Copy, history, suggested safe questions, provider/model indicator, fallback notice.
* **AI layer** `src/sentinel/ai/`: `models`, `errors`, `router`, `config`, `privacy`, `context_builder`, `response_validator`, `conversation_store`, `usage`, `secrets`, `transport`, `service`, `prompts`, `knowledge`, `providers/{base,openai_compat,gemini,groq,openrouter}`.
* **Router:** primary + ordered fallbacks; transient errors (network, timeout, 5xx, rate limit, empty/malformed) fall back; permanent errors (bad key, permission, unknown model, bad request, too large) stop and are shown unless you enable *fallback on permanent errors*; a provider refusal is never forwarded; each provider is tried once per request (no retry loop); cooldown after rate limits (Retry-After, capped at 5 minutes) and exponential back-off after repeated failures; manual provider override; request limits per hour and per session; one request at a time.
* **Privacy:** off by default; review of the exact request every time (default); redaction of paths, user/machine names, e-mail, SIDs, IP/MAC and secrets; recorded text quoted as untrusted data; no file contents, notes, logs, environment variables or whole databases.
* **Keys:** Windows Credential Manager (`keyring`) or `VYRXONE_<PROVIDER>_API_KEY`; refused (not stored in plain text) when no secure store exists.
* **Settings added (all validated, none secret):** `ai_enabled`, `ai_primary`, `ai_fallback_order`, `ai_<provider>_enabled/model/timeout`, `ai_max_response`, `ai_max_requests_hour/session`, `ai_history_enabled`, `ai_history_messages`, `ai_consent_mode`, `ai_redact_paths/names/addresses`, `ai_fallback_on_permanent`, `ai_context_max_events`. An imported settings file can switch AI off but never on.
* **Database:** schema v5 (`ai_conversations`, `ai_messages`), additive, transactional, backed up once as `.pre-v4.bak`, rolled back on failure, newer schemas refused.
* **Performance page / diagnostics:** an "AI assistant" line (worker count, running, queued, requests, last latency; "unavailable" when unmeasured). No prompts, answers or keys are recorded.
* **Cross-page buttons:** *Ask AI…* on Findings, Events and Projects only opens the AI page with a pre-selection.
* **Fixed:** A8 Events page live refresh on alert changes (V3 defect). See V4_AUDIT.md.

## Install / launch (Windows)
Extract to a new folder; double-click `run_sentinel.bat` (PowerShell: `.\run_sentinel.bat`). Switches: `--console`, `--install`, `--panel`, `--background`. Exit: `Ctrl+Q`, tray → Exit, or `stop_sentinel.bat`; restore desktop `Ctrl+Shift+R`.

## Not done / not verified
See V4_KNOWN_LIMITATIONS.md. In short: no live provider call and no Windows run were possible; no streaming; no proxy support; the EXE was not built.
