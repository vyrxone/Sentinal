# AI provider support

Status of each integration. "Unverified live" means the code follows the provider's published API and passes mocked/local-server
tests, but no real request was made (no API key was available).

| | Google Gemini | Groq | OpenRouter |
|---|---|---|---|
| Endpoint | `generativelanguage.googleapis.com/v1beta/models/{model}:generateContent` | `api.groq.com/openai/v1/chat/completions` | `openrouter.ai/api/v1/chat/completions` |
| Auth | `x-goog-api-key` header | `Authorization: Bearer` | `Authorization: Bearer` |
| Request shape | `contents` + `systemInstruction` + `generationConfig.maxOutputTokens` | OpenAI chat format, `max_completion_tokens` | OpenAI chat format, `max_tokens` |
| Key / connection test | `GET /v1beta/models?pageSize=1` | `GET /openai/v1/models` | `GET /api/v1/key` (`/models` is public, so it cannot check a key) |
| Model list (button) | `GET /v1beta/models` (generateContent models only) | `GET /openai/v1/models` | `GET /api/v1/models` |
| Usage reported | `usageMetadata` | `usage` | `usage` |
| Errors handled | HTTP status + `error.status` (`RESOURCE_EXHAUSTED`, `NOT_FOUND`, …), `retryDelay` | HTTP status + `error.code` (`model_not_found`, `invalid_api_key`, …), `retry-after` | HTTP status + `error.code`; errors inside HTTP 200 bodies; `finish_reason: error` |
| Truncation | `finishReason: MAX_TOKENS` | `finish_reason: length` | `finish_reason: length` |
| Refusal | `promptFeedback.blockReason`, safety finish reasons | `content_filter`, `message.refusal` | `content_filter`, `message.refusal` |
| Live status | **Unverified live** | **Unverified live** | **Unverified live** |

The three are not treated as identical: request building, error classification, key checks and model lists are per provider; only the
shared OpenAI-style parsing is reused by Groq and OpenRouter.

* **Model IDs are never hard-coded.** Enter one from the provider's own list (or use *Fetch model list*). Models change or are withdrawn; an unknown model is reported as "invalid model" and is not retried elsewhere.
* **Free tiers and rate limits change.** Nothing here promises free or unlimited use; check your provider account. HTTP 429 puts the provider on cooldown (Retry-After, capped at 5 minutes).
* Provider documentation used: Google AI for Developers (Gemini API reference), OpenRouter documentation (errors, API reference), Groq documentation (API reference, errors). Verify details there; they may have changed after this release.
* Adding a provider means adding one module in `src/sentinel/ai/providers/` and registering it; other providers are unaffected.


## V5 verification note (document comparison only — no live calls)
Compared with the providers' official documentation pages fetched during V5 development:
* **Gemini** — `POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent`; `generationConfig.maxOutputTokens/temperature`; `usageMetadata.{promptTokenCount,candidatesTokenCount,totalTokenCount}`; `finishReason` values STOP, MAX_TOKENS, SAFETY, RECITATION, LANGUAGE, OTHER, BLOCKLIST, PROHIBITED_CONTENT, SPII, MALFORMED_FUNCTION_CALL; `promptFeedback.blockReason`. Matches the implementation.
* **Groq** — `POST https://api.groq.com/openai/v1/chat/completions`, `max_completion_tokens` (`max_tokens` deprecated), `GET /models`, usage fields `prompt_tokens/completion_tokens/total_tokens`, `retry-after` in seconds on 429, error shape `{error:{message,type}}`. Matches.
* **OpenRouter** — errors `{error:{code,message,metadata}}`; errors may arrive inside an HTTP 200 body or as `finish_reason:"error"`; 402 = insufficient credits **except** `metadata.limit_source = openrouter_in_flight_budget`, which carries Retry-After (V4 got this wrong: fix A15); 429/503 may carry Retry-After. Matches after A15.
* `Retry-After` is now accepted as seconds or as an HTTP date (A13).
None of this was exercised against a live service; the three providers remain **unverified live** until you run the checks in V5_WINDOWS_VALIDATION.md (tests 22–25). Model IDs are never hard-coded.
