# AI privacy

**Using the AI Assistant sends data off your PC.** The provider you choose (Gemini, Groq or OpenRouter) processes it under its own
terms, privacy policy and retention rules, which Sentinel does not control. OpenRouter may forward a request to other model hosts.
Do not send anything you are not willing to share with that provider.

## What is sent
Only what you select, after you review it:
* the text of your question;
* the context items you picked: a finding (title, severity, rule id, certainty, explanation, evidence summary, timestamps and the rule's suggested next step), selected events (UTC timestamp, type, severity, category, message, file name), baseline differences (hash **prefixes** and sizes only), a project's health summary (name, status, health notes, task **counts**, Git branch and change counts), settings values, and optionally a redacted diagnostics report;
* earlier messages of the current conversation (a checkbox, on by default when there are any, limited by your setting);
* Sentinel's own fixed instructions and, for settings/feature questions, a static documentation excerpt.
## What is never sent
File contents, project notes and task titles, log files, the event database, environment variables, browser data, passwords, API keys, arbitrary folders, anything you did not select.

## Review every time
The review dialog shows the provider and model, the fallback providers that would receive the same request if the first fails, the exact text, the exact request body (the key is a separate header and is not part of it), the data fields, and what was redacted. You can untick items. **Cancel is the default button; Cancel sends nothing.**
A general one-time consent does not exist. The optional mode *Review only when new context is added* skips the dialog only for a follow-up in the same conversation whose context you already reviewed; the first request and any new context are always reviewed.

## Redaction (on by default; each category can be switched off with a visible warning)
Folder paths (the file name is kept), user names, machine name, e-mail addresses, Windows SIDs, IPv4/IPv6 and MAC addresses, URL credentials. Anything that looks like an API key is always removed. Redaction is pattern based and can miss unusual identifiers; read the preview.

## Untrusted data and prompt injection
Event messages, file names and Git text are quoted line by line as data, delimiter text inside them is neutralised, and the instructions tell the model never to follow them. This reduces but does not remove the risk. Consequences are limited by design: the assistant cannot run commands, change findings, open links or modify files; answers are plain text.

## Keys
Windows Credential Manager or an environment variable. Never in the database, settings, exports, logs, diagnostics, crash text or AI context. Log files pass a filter that removes key-like text, including inside tracebacks.

## History
Off by default. When on, conversations (redacted text as sent, and answers) are stored in the local database only, bounded to 50 conversations of 200 messages, and can be deleted individually or all at once. There is no telemetry and no background upload.

## Your responsibilities
Check each provider's terms for data use and retention, especially on free tiers; keep your keys private; do not rely on AI answers as proof of anything.


## V5 additions
* **Proxy:** if you enable a proxy, it can see the destination host name and traffic volume (the tunnel is the provider's TLS connection, verified by Sentinel); it cannot read the request, your provider key or the answer. The proxy sign-in is sent only to the proxy.
* Connection tests, model lists and the proxy test also make **no** network connection while AI is off (V5 fix A11).
* Nothing else changed: AI off by default, per-request review, default redaction, no file contents, keys never stored in files.
