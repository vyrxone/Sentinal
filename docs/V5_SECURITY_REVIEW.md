# V5 Security Review

Sentinel is a local, read-only monitoring dashboard. It is **not** an antivirus or EDR product, and this review does not
claim it is free of vulnerabilities. Items marked *checked* were verified against the code or by an executed test in this
release; *not verified* items need Windows or third-party systems.

| Area | Finding | Evidence |
|---|---|---|
| AI off by default; no network when off | *checked.* V5 closes one gap (A11): connection test and model list also refuse while AI is off. | `test_a11…`, V4 `test_ai_is_off_by_default…` |
| No autonomous actions | *checked.* AI output is plain text, escaped, links disabled, never executed; suggestions need confirmation before becoming tasks. | V4 UI tests |
| Per-request review | *checked.* Preview equals the wire body (also through a proxy). Cancel → zero requests. | `test_full_request_through_the_proxy_matches_the_previewed_payload`, V4 tests |
| API keys | Credential Manager via `keyring` or env var; no plaintext fallback. *checked* with a fake store: keys never appear in SQLite, settings export, logs, errors. *not verified* on real Credential Manager. | `test_no_key_or_proxy_secret_is_ever_written…`, `--keyring-selftest` |
| Proxy credentials | Same store, separate entries (`proxy-username`, `proxy-password`), never in settings/logs/repr/errors; sent only to the proxy in CONNECT. *checked.* | `tests/test_v5_proxy.py` |
| Proxy cannot be forced by an imported file | `ai_proxy_enabled: true` in an import is rejected (like `ai_enabled`). *checked.* | `test_proxy_settings_are_validated_and_import_cannot_enable_the_proxy` |
| TLS | Certificate and host-name verification always on, TLS ≥ 1.2; a context that disables verification is refused. A bad certificate (also through the tunnel) is reported and nothing is sent. *checked* against a local TLS server with a throw-away CA. | proxy tests |
| Redirects | Never followed (provider or proxy), so credentials cannot reach another host. *checked.* | `test_provider_redirect_is_not_followed…`, `…refusals_and_redirects…` |
| Proxy fail-closed | A failing/misconfigured proxy never results in a direct connection. *checked.* | `test_dead_proxy_never_means_a_direct_request…` |
| Proxy sees no secrets | The key and body are inside the TLS tunnel; the test proxy relayed only ciphertext. *checked.* | `test_request_through_a_proxy…` |
| Prompt injection | Recorded text is quoted line-by-line as untrusted data, delimiters neutralised, system prompt forbids following it, answers validated. Cannot be eliminated; answers stay "unverified". | V4 privacy tests |
| SQL | Parameterised; no formatted SQL found in `storage/`, `projects/`, `ai/conversation_store.py` (grep). | audit grep |
| CSV/JSON export | Formula prefixes (`= + - @ TAB CR`) neutralised; atomic writes. | `storage/export.py`, V2/V3 tests |
| Symlinks | Integrity and project scans refuse/skip symlinks (`os.path.islink`). | `security/integrity.py`, `projects/overview.py` |
| Local IPC | `QLocalServer` with `UserAccessOption`; whitelist `ping/show/quit`; 64-byte read. Any process of the *same user* can ask Sentinel to show or quit — by design (used by `stop_sentinel.bat`). | `core/ipc.py` |
| Single-instance / force stop | Lock file; `/force` checks the process identity (token-based, V4 fix A6). | V3/V4 tests |
| Settings import | Size-capped, validated per key, unknown keys rejected. | `settings_store.py` |
| Release ZIP | Built by `scripts/package_zip.py` (fixed file list, secret and placeholder scan, integrity test). ZIP path traversal on *extraction* is the user's unzip tool's job; Sentinel never extracts archives. | package script |
| Dependencies | `pip-audit -r requirements.txt --no-deps` on Linux: no known vulnerabilities. `keyring` carries a Windows-only marker and may not have been resolved by that run. | executed |
| Secret scan | Automated test scans the tree for key-shaped text (one allow-listed file of obviously fake scrubber fixtures). | `test_repository_contains_no_key_shaped_text` |
| Updates / packaging integrity | No auto-update exists. The ZIP has no signature; the EXE (if you build it) is unsigned. | — |

## Remaining risks
* The AI provider receives whatever you approve in the review dialog; redaction is pattern-based and can miss things — read the preview.
* A malicious local process of your own account can use the IPC pipe or read Sentinel's data folder (no protection against the same user).
* A TLS-inspecting corporate proxy will cause certificate errors (Sentinel will not bypass them).
* Windows Credential Manager entries are readable by other programs running as you.
* Unsigned scripts/EXE can trigger SmartScreen/antivirus warnings.
