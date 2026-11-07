"""Optional HTTP proxy configuration for AI requests (V5).

Direct connection is the default. When enabled, requests go through an *HTTP proxy* using the standard
``CONNECT`` method: Sentinel opens a tunnel to the provider through the proxy and then performs the TLS
handshake with the provider itself, so

* the provider's certificate is still verified against the provider's host name (the proxy cannot read or
  alter the request, and no certificate check is ever disabled);
* the provider API key travels only inside the encrypted tunnel, never to the proxy;
* the proxy's own sign-in (``Proxy-Authorization``) is sent only to the proxy, only in the CONNECT request.

Not supported (documented in docs/V5_KNOWN_LIMITATIONS.md): a proxy that itself must be reached over TLS
(``https://proxy``), because the Python standard library cannot nest TLS inside TLS; SOCKS proxies; NTLM /
Kerberos / PAC auto-configuration. Sentinel does not read the Windows system proxy or HTTP(S)_PROXY variables:
a proxy is used only when the user enables it here.
"""

from __future__ import annotations

import base64
import ipaddress
import re
from dataclasses import dataclass, field

__all__ = ["ProxyConfigError", "ProxySettings", "validate_host", "validate_port", "validate_credentials",
           "basic_auth_header"]

_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_HOSTNAME = re.compile(rf"^{_LABEL}(?:\.{_LABEL})*$")


class ProxyConfigError(ValueError):
    """Invalid proxy setting. The message is safe to show (it never contains a password)."""


def validate_host(text: str) -> str:
    """A proxy host name or IP address: no scheme, port, path, user info or spaces."""
    host = (text or "").strip()
    if not host:
        raise ProxyConfigError("Enter the proxy host name or IP address.")
    if "://" in host or any(c in host for c in "/\\?#@ \t\r\n%"):
        raise ProxyConfigError("Enter only the proxy host name or IP address (no http://, port, path or user name).")
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if len(host) > 253 or any(ord(c) < 33 or ord(c) > 126 for c in host):
        raise ProxyConfigError("The proxy host name is not valid.")
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    if ":" in host or not _HOSTNAME.match(host):
        raise ProxyConfigError("The proxy host name is not valid (use letters, digits, dots and hyphens).")
    return host


def validate_port(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ProxyConfigError("The proxy port must be a number from 1 to 65535.")
    try:
        port = int(value)
    except ValueError:
        raise ProxyConfigError("The proxy port must be a number from 1 to 65535.") from None
    if not 1 <= port <= 65535:
        raise ProxyConfigError("The proxy port must be a number from 1 to 65535.")
    return port


def validate_credentials(username: str, password: str) -> tuple[str, str]:
    user = (username or "").strip()
    if not 1 <= len(user) <= 128 or ":" in user or any(ord(c) < 32 or ord(c) == 127 for c in user):
        raise ProxyConfigError("The proxy user name must be 1-128 characters and cannot contain ':' or line breaks.")
    if not 1 <= len(password or "") <= 256 or any(ord(c) < 32 or ord(c) == 127 for c in password):
        raise ProxyConfigError("The proxy password must be 1-256 characters with no line breaks.")
    try:
        (user + ":" + password).encode("utf-8")
    except UnicodeEncodeError:
        raise ProxyConfigError("The proxy credentials contain characters that cannot be encoded.") from None
    return user, password


def basic_auth_header(username: str, password: str) -> str:
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


@dataclass(frozen=True)
class ProxySettings:
    """A proxy ready to use. ``repr`` never shows the credentials."""
    host: str
    port: int
    username: str = field(default="", repr=False)
    password: str = field(default="", repr=False)

    @property
    def has_auth(self) -> bool:
        return bool(self.username)

    @property
    def label(self) -> str:
        return f"{self.host}:{self.port}"
