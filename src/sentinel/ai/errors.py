"""Error taxonomy for the AI layer. Messages are safe to show and log: they never contain keys or prompts."""

from __future__ import annotations

from enum import Enum
from typing import Optional


class ErrorKind(str, Enum):
    NOT_CONFIGURED = "not_configured"
    DISABLED = "disabled"
    AUTH = "auth"                 # invalid / revoked key
    PERMISSION = "permission"     # key valid but not allowed (or billing / quota policy)
    INVALID_MODEL = "invalid_model"
    BAD_REQUEST = "bad_request"
    TOO_LARGE = "too_large"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    NETWORK = "network"
    CERTIFICATE = "certificate"   # the provider's TLS certificate could not be verified (never bypassed)
    PROXY = "proxy"               # the configured proxy could not be reached or refused the tunnel
    PROXY_AUTH = "proxy_auth"     # the proxy asked for (other) credentials
    SERVER = "server"
    EMPTY = "empty"
    MALFORMED = "malformed"
    REFUSED = "refused"           # the provider blocked the request or answer (safety / content filter)
    CANCELLED = "cancelled"
    LIMIT = "limit"               # Sentinel's own request limit
    BUSY = "busy"
    CONSENT = "consent"
    UNKNOWN = "unknown"


# Eligible for automatic fallback to the next provider: temporary conditions at one provider.
TRANSIENT = frozenset({ErrorKind.RATE_LIMIT, ErrorKind.TIMEOUT, ErrorKind.NETWORK, ErrorKind.SERVER,
                       ErrorKind.EMPTY, ErrorKind.MALFORMED})
# Never retried across providers unless the user explicitly configures it: they need the user's attention.
PERMANENT = frozenset({ErrorKind.AUTH, ErrorKind.PERMISSION, ErrorKind.INVALID_MODEL, ErrorKind.BAD_REQUEST,
                       ErrorKind.TOO_LARGE})

USER_HINTS = {
    ErrorKind.NOT_CONFIGURED: "Add an API key for this provider (AI Assistant > Providers).",
    ErrorKind.DISABLED: "This provider is switched off in the AI settings.",
    ErrorKind.AUTH: "The provider rejected the API key. Replace the key in AI Assistant > Providers.",
    ErrorKind.PERMISSION: "The key is not allowed to use this model or service (permissions, billing or quota).",
    ErrorKind.INVALID_MODEL: "The provider does not know this model ID. Check the model ID in the provider settings.",
    ErrorKind.BAD_REQUEST: "The provider rejected the request as invalid.",
    ErrorKind.TOO_LARGE: "The request is too large for this model. Remove context items or earlier messages.",
    ErrorKind.RATE_LIMIT: "The provider is rate limiting requests. Wait and retry, or choose another provider.",
    ErrorKind.TIMEOUT: "The provider did not answer in time.",
    ErrorKind.NETWORK: "Could not reach the provider. Check the internet connection.",
    ErrorKind.CERTIFICATE: ("The provider's security certificate could not be verified, so nothing was sent. A proxy "
                            "or security product that inspects HTTPS traffic can cause this. Sentinel never turns "
                            "certificate checks off."),
    ErrorKind.PROXY: ("Could not use the configured proxy. Check the proxy host and port in AI Assistant > Network, "
                      "or turn the proxy off to connect directly. Sentinel does not fall back to a direct "
                      "connection by itself."),
    ErrorKind.PROXY_AUTH: ("The proxy needs a valid user name and password (AI Assistant > Network). Nothing was sent."),
    ErrorKind.SERVER: "The provider reported a temporary server problem.",
    ErrorKind.EMPTY: "The provider returned an empty answer.",
    ErrorKind.MALFORMED: "The provider returned a response Sentinel could not understand.",
    ErrorKind.REFUSED: "The provider declined to answer (its safety or content filter).",
    ErrorKind.CANCELLED: "The request was cancelled.",
    ErrorKind.LIMIT: "Sentinel's own request limit was reached (AI Assistant > Privacy & limits).",
    ErrorKind.BUSY: "A request is already running.",
    ErrorKind.CONSENT: "The request was not confirmed, so nothing was sent.",
    ErrorKind.UNKNOWN: "An unexpected error occurred.",
}


class AIError(Exception):
    """Base error. ``detail`` is a short, already-scrubbed provider message (never a key or a prompt)."""

    def __init__(self, kind: ErrorKind, detail: str = "", *, provider_id: str = "",
                 retry_after: Optional[float] = None, http_status: Optional[int] = None) -> None:
        self.kind = kind
        self.detail = (detail or "")[:300]
        self.provider_id = provider_id
        self.retry_after = retry_after
        self.http_status = http_status
        super().__init__(self.message)

    @property
    def transient(self) -> bool:
        return self.kind in TRANSIENT

    @property
    def permanent(self) -> bool:
        return self.kind in PERMANENT

    @property
    def message(self) -> str:
        text = USER_HINTS.get(self.kind, USER_HINTS[ErrorKind.UNKNOWN])
        if self.kind == ErrorKind.RATE_LIMIT and self.retry_after:
            text += f" Suggested wait: {self.retry_after:.0f} s."
        if self.detail:
            text += f" ({self.detail})"
        return text

    def __str__(self) -> str:  # never include anything but the safe message
        return self.message


class SecureStorageUnavailable(Exception):
    """No secure credential store is available; Sentinel refuses to store a key in plain text."""


class InvalidKey(ValueError):
    """The text entered is not an acceptable API key (the key itself is never echoed)."""
