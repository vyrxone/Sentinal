# AI Assistant setup

The AI Assistant is **optional and off by default**. Sentinel never needs it, a key or the internet.

## 1. Get a key directly from a provider
Create an account with the provider yourself and create an API key there. Sentinel never creates accounts and the project contains no keys.
* Google Gemini: https://aistudio.google.com/apikey
* Groq: https://console.groq.com/keys
* OpenRouter: https://openrouter.ai/keys
Free tiers, quotas and rate limits are set by the providers and can change. Read their terms; you are responsible for any charges.

## 2. Enter it in Sentinel
1. Open **AI Assistant → Providers**.
2. Tick *Use <provider>*, press **Set / replace key…**, paste the key. It is stored in the Windows Credential Manager (entry `VYRXONE Sentinel`). It is shown only as dots afterwards.
3. Enter a **model ID** (copy one from the provider's model list, or press *Fetch model list*). Sentinel hard-codes none.
4. Press **Test connection**. This sends no events, findings or project data; it only checks the key with a read-only request.
5. Choose the primary provider and up to two fallbacks. Tick **Enable the AI Assistant**.

If no secure credential store is available (the optional `keyring` package is missing) Sentinel refuses to store a key. Use an environment variable instead and restart Sentinel:

PowerShell (current user, persistent):
```
[Environment]::SetEnvironmentVariable("VYRXONE_GEMINI_API_KEY", "<your key>", "User")
```
Other names: `VYRXONE_GROQ_API_KEY`, `VYRXONE_OPENROUTER_API_KEY`. An environment variable overrides a stored key. Sentinel cannot delete it; remove it with `[Environment]::SetEnvironmentVariable("VYRXONE_GEMINI_API_KEY", $null, "User")`.

## 3. Use it
Pick what to ask about (a finding, events, a project…), press **Review & send…**, read the exact text, untick anything you do not want sent, and confirm. Cancel sends nothing.

## Remove a key
*Providers → Delete key*, or Windows *Credential Manager → Windows Credentials → VYRXONE Sentinel*. *Privacy & limits → Reset AI configuration…* resets settings and can also delete the keys.

## Disable AI completely
Leave *Enable the AI Assistant* unticked (the default), or untick it again. Nothing is sent and no AI worker runs. To remove everything: Reset AI configuration (delete keys), *Delete all history*.

## Commands (Windows)
PowerShell: `.\run_sentinel.bat`, `.\run_sentinel.bat --console`, `.\stop_sentinel.bat`, `.\run_tests.bat`, `.\diagnose_sentinel.bat`.
Batch/cmd: the same names without `.\`. `python -m sentinel --self-check` (inside `.venv`) prints whether secure key storage is available.


## V5: proxy, Credential Manager check
**Proxy (optional).** AI Assistant → Network. Direct connection is the default. Enable the proxy, enter host and port, tick
*The proxy requires a user name and password* if needed and press *Save sign-in* (stored in Windows Credential Manager as
`proxy-username` / `proxy-password` under `VYRXONE Sentinel`; the password is never displayed, logged or written to settings). Press *Test
proxy* — it opens the tunnel to your primary provider's host and completes the TLS handshake without sending any request. Switch AI on first: while AI is off Sentinel makes no network connection at all.
If the proxy is unreachable, refuses (403/502), or rejects the sign-in (407), the request fails with an explanation — it is never sent directly. Turn the proxy off to go direct.
Supported: HTTP proxy with CONNECT, Basic sign-in. Not supported: `https://` proxies, SOCKS, NTLM/Kerberos, PAC/WPAD, the Windows system proxy.
A TLS-inspecting proxy causes a *certificate could not be verified* error; Sentinel never disables certificate checks.

**Checking the Credential Manager (Windows).** `python -m sentinel --keyring-selftest` (or the EXE with the same flag) stores, reads,
replaces and deletes throw-away entries under a separate service name and confirms an unrelated entry is untouched.
Environment variables remain an advanced alternative: `VYRXONE_GEMINI_API_KEY`, `VYRXONE_GROQ_API_KEY`,
`VYRXONE_OPENROUTER_API_KEY`, `VYRXONE_PROXY_USERNAME`, `VYRXONE_PROXY_PASSWORD`.
