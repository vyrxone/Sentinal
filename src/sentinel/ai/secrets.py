"""API key storage: Windows Credential Manager through ``keyring``, or an environment variable.

Keys are never written to SQLite, JSON, logs or exports. If no secure store is available Sentinel
says so and refuses to store the key (it never falls back to a plain-text file).
"""

from __future__ import annotations

import logging
import os
import re
import threading
from dataclasses import dataclass
from typing import Any, Optional

from .errors import InvalidKey, SecureStorageUnavailable
from .proxy import ProxyConfigError, validate_credentials
from .privacy import forget_secret, register_secret

log = logging.getLogger("sentinel.ai.secrets")

SERVICE = "VYRXONE Sentinel"
ENV_NAMES = {"gemini": "VYRXONE_GEMINI_API_KEY", "groq": "VYRXONE_GROQ_API_KEY",
             "openrouter": "VYRXONE_OPENROUTER_API_KEY"}
PROXY_USER, PROXY_PASS = "proxy-username", "proxy-password"     # entries in the same Sentinel-only service
PROXY_ENV = ("VYRXONE_PROXY_USERNAME", "VYRXONE_PROXY_PASSWORD")
_KEY_OK = re.compile(r"^[\x21-\x7e]{8,512}$")           # printable ASCII, no spaces/newlines (no header injection)
_BAD_BACKENDS = ("fail", "null", "plaintext", "chainer")  # chainer is checked separately below


@dataclass(frozen=True)
class KeyStatus:
    present: bool
    source: str          # "credential_manager" | "environment" | "none"
    storage_available: bool
    note: str

    @property
    def label(self) -> str:
        if self.source == "credential_manager":
            return "Key stored in Windows Credential Manager"
        if self.source == "environment":
            return "Key read from environment variable"
        return "No key set"


def validate_key_text(key: str) -> str:
    key = (key or "").strip()
    if not _KEY_OK.match(key):
        raise InvalidKey("An API key is 8-512 printable characters with no spaces or line breaks.")
    return key


def mask(key: str) -> str:
    """UI display only: bullets, plus the last 4 characters for long keys."""
    if not key:
        return ""
    return "•" * 12 + (key[-4:] if len(key) >= 20 else "")


def _default_backend() -> Any:
    try:
        import keyring  # optional dependency
    except Exception:
        return None
    try:
        backend = keyring.get_keyring()
    except Exception:
        return None
    name = f"{type(backend).__module__}.{type(backend).__name__}".lower()
    if any(bad in name for bad in ("fail", "null", "plaintext", "keyrings.alt")):
        return None
    return keyring


class SecretStore:
    def __init__(self, backend: Any = "auto", environ: Optional[dict[str, str]] = None) -> None:
        self._backend = _default_backend() if backend == "auto" else backend
        self._environ = os.environ if environ is None else environ
        self._present: dict[str, bool] = {}
        self._lock = threading.Lock()

    # ----------------------------------------------------------------- capability
    @property
    def storage_available(self) -> bool:
        return self._backend is not None

    def storage_note(self) -> str:
        if self.storage_available:
            return "Keys are kept in the Windows Credential Manager (never in Sentinel's database or settings files)."
        return ("No secure credential store is available (the optional 'keyring' package is missing or has no "
                "secure backend). Sentinel will not store keys in plain text. Install the packaged requirements, or set "
                "an environment variable such as VYRXONE_GEMINI_API_KEY and restart Sentinel.")

    # ----------------------------------------------------------------- access
    def _env_key(self, provider_id: str) -> Optional[str]:
        name = ENV_NAMES.get(provider_id)
        value = (self._environ.get(name, "") if name else "").strip()
        return value if value and _KEY_OK.match(value) else None

    def get_key(self, provider_id: str) -> Optional[str]:
        """Return the key (used only inside the request worker). Environment variable wins if set."""
        env = self._env_key(provider_id)
        if env:
            register_secret(env)
            return env
        if self._backend is None:
            return None
        try:
            value = self._backend.get_password(SERVICE, provider_id)
        except Exception as exc:
            log.warning("credential store read failed (%s)", type(exc).__name__)
            return None
        if value:
            register_secret(value)
        with self._lock:
            self._present[provider_id] = bool(value)
        return value or None

    def status(self, provider_id: str) -> KeyStatus:
        if self._env_key(provider_id):
            return KeyStatus(True, "environment", self.storage_available, ENV_NAMES[provider_id])
        if self._backend is not None:
            with self._lock:
                cached = self._present.get(provider_id)
            if cached is None:
                self.get_key(provider_id)
                with self._lock:
                    cached = self._present.get(provider_id, False)
            if cached:
                return KeyStatus(True, "credential_manager", True, "")
        return KeyStatus(False, "none", self.storage_available, "")

    def has_key(self, provider_id: str) -> bool:
        return self.status(provider_id).present

    def masked(self, provider_id: str) -> str:
        key = self.get_key(provider_id)
        return mask(key) if key else ""

    # ----------------------------------------------------------------- changes
    def set_key(self, provider_id: str, key: str) -> None:
        if provider_id not in ENV_NAMES:
            raise InvalidKey("Unknown provider.")
        key = validate_key_text(key)
        if self._backend is None:
            raise SecureStorageUnavailable(self.storage_note())
        old = None
        try:
            old = self._backend.get_password(SERVICE, provider_id)
        except Exception:
            pass
        try:
            self._backend.set_password(SERVICE, provider_id, key)
        except Exception as exc:
            raise SecureStorageUnavailable(
                f"The credential store refused to save the key ({type(exc).__name__}).") from None
        register_secret(key)
        if old and old != key:
            forget_secret(old)
        with self._lock:
            self._present[provider_id] = True

    def delete_key(self, provider_id: str) -> bool:
        """Remove the stored key. A key coming from an environment variable cannot be deleted by Sentinel."""
        if self._backend is None:
            return False
        old = None
        try:
            old = self._backend.get_password(SERVICE, provider_id)
            if old:
                self._backend.delete_password(SERVICE, provider_id)
        except Exception as exc:
            log.warning("credential store delete failed (%s)", type(exc).__name__)
            return False
        if old:
            forget_secret(old)
        with self._lock:
            self._present[provider_id] = False
        return bool(old)

    # ----------------------------------------------------------------- proxy sign-in (V5)
    def proxy_credentials(self) -> Optional[tuple[str, str]]:
        """(user, password) for the proxy, or None. Environment variables win over the credential store."""
        env_u, env_p = (self._environ.get(n, "") for n in PROXY_ENV)
        if env_u and env_p:
            try:
                creds = validate_credentials(env_u, env_p)
            except ProxyConfigError:
                return None
            register_secret(creds[1])
            return creds
        if self._backend is None:
            return None
        try:
            user = self._backend.get_password(SERVICE, PROXY_USER)
            pw = self._backend.get_password(SERVICE, PROXY_PASS)
        except Exception as exc:
            log.warning("credential store read failed (%s)", type(exc).__name__)
            return None
        if not user or not pw:
            return None
        register_secret(pw)
        return user, pw

    def proxy_source(self) -> str:
        env_u, env_p = (self._environ.get(n, "") for n in PROXY_ENV)
        if env_u and env_p:
            return "environment"
        return "credential_manager" if self.proxy_credentials() else "none"

    def set_proxy_credentials(self, username: str, password: str) -> None:
        try:
            user, pw = validate_credentials(username, password)
        except ProxyConfigError as exc:
            raise InvalidKey(str(exc)) from None
        if self._backend is None:
            raise SecureStorageUnavailable(self.storage_note())
        try:
            self._backend.set_password(SERVICE, PROXY_USER, user)
            self._backend.set_password(SERVICE, PROXY_PASS, pw)
        except Exception as exc:
            raise SecureStorageUnavailable(
                f"The credential store refused to save the proxy sign-in ({type(exc).__name__}).") from None
        register_secret(pw)

    def delete_proxy_credentials(self) -> bool:
        if self._backend is None:
            return False
        done = False
        for name in (PROXY_USER, PROXY_PASS):
            try:
                if self._backend.get_password(SERVICE, name):
                    self._backend.delete_password(SERVICE, name)
                    done = True
            except Exception as exc:
                log.warning("credential store delete failed (%s)", type(exc).__name__)
        return done
