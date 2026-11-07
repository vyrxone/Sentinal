# VYRXONE Sentinel V4 — known limitations and features not completed

## Not verified (could not be tested in this environment)
* Any real Gemini, Groq or OpenRouter request. No API key was available. Provider code is tested against mocked responses and a local HTTP server only; real error bodies, headers, model availability and free-tier rules may differ. Treat each provider as **unverified live** until you run checklist J14.
* Any behaviour on real Windows: wallpaper embedding, Credential Manager, TLS on the Windows certificate store, tray, sleep/resume, multi-monitor, the Celeron J3355 PC (checklist section J).
* The PyInstaller executable (not built).

## Design limits
* **No proxy support** *(superseded in V5: optional HTTP-proxy support was added; see V5_KNOWN_LIMITATIONS.md)*: requests go straight to the provider with Python's standard library.
* **No streaming**: the answer appears when complete (a plain loading state is shown; nothing is faked).
* **Prompt injection cannot be eliminated.** Recorded text is quoted and the system prompt says to treat it as data, but a model may still be influenced. Answers are never executed, never open links and never change findings; read them critically.
* Redaction is pattern based. Unusual identifiers (project code names, custom file names, names that look like normal words) can pass through; that is why the exact text is shown before sending. A name appearing as a normal word of 3+ letters equal to your user/machine name is redacted too.
* Token counts appear only if the provider reports them; prices and costs are never estimated.
* Model IDs must be typed or picked from the provider's own list; Sentinel hard-codes none, so a provider is unconfigured until you choose one.
* One AI request at a time; a request stuck in a name lookup is bounded by the provider timeout.
* A key from an environment variable cannot be deleted by Sentinel.
* AI answers can be wrong, and the assistant is not an antivirus or malware detector.
* Conversation history, when enabled, is stored unencrypted in the local database (the text sent was already redacted, answers are as received).

## Roadmap items not in V4
Anything beyond V4 belongs to V5 (final stabilization, optimization, Windows validation, security review, packaging, documentation). Nothing beyond V5 exists.
