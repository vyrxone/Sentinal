# V5 Known Limitations

Nothing here is hidden behind a green tick: these are the limits of this release.

**Not verified (need a real Windows PC or real credentials)** — see V5_WINDOWS_VALIDATION.md: wallpaper embedding behind
icons, Windows 11 layouts, Explorer restart, sleep/resume, lock/unlock, multi-monitor, mixed DPI, hot-plug, tray, autostart,
Windows Credential Manager, the PyInstaller EXE, Celeron J3355 performance, and every live call to Gemini, Groq and OpenRouter.

**Proxy:** HTTP proxies via CONNECT only (Basic sign-in optional). Not supported: a proxy you must reach over TLS
(`https://proxy`, the stdlib cannot nest TLS), SOCKS, NTLM/Kerberos/Negotiate, PAC/WPAD auto-config, automatic use of the
Windows system proxy or `HTTP(S)_PROXY`. A proxy that inspects TLS will fail certificate verification, by design.
Loopback test servers are never proxied.

**AI:** no streaming; answers can be wrong; prompt injection cannot be fully prevented; redaction is pattern-based; token
counts are shown only when the provider reports them and no prices are estimated; model IDs are typed by you (not hard-coded).
Free tiers and rate limits belong to the providers and change.

**Application**
* Closed control-panel windows are not garbage-collected (≈12 MiB each in a synthetic open/close loop). The shipped app
  creates one panel per run and hides it to the tray, so this is not reachable in normal use.
* Lightweight mode is a manual switch; there is no automatic low-memory detection.
* "Explain integrity changes" is reached through the Events page, not a button on the Security page.
* Findings are rule-based hints, not malware detection. File contents are never inspected.
* The desktop-layer technique is undocumented by Microsoft and may break with a Windows update; the labelled normal-window fallback remains.
* No code signing, no installer, no auto-update.
* The settings page for the optional proxy and keys works on any OS, but secure key storage requires the Windows Credential Manager (or env vars).
