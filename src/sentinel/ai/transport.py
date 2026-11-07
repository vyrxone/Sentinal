"""Minimal HTTPS transport on the standard library (no extra dependency).

* HTTPS only (plain HTTP only to a loopback address, used by the tests against a local server).
* No redirects are followed (a redirect could carry the key to another host).
* Response size is capped. Timeouts are enforced by the socket; ``Cancel.cancel()`` closes the
  connection from another thread, which makes a blocked read fail immediately.
* The request headers (which carry the key) are never logged or stored.
* V5: optional HTTP proxy through a CONNECT tunnel (see ``proxy.py``). TLS is always negotiated with the provider
  itself with certificate and host-name verification on; if a configured proxy fails, the request fails - it is
  never silently sent directly.
"""

from __future__ import annotations

import http.client
import json
import socket
import ssl
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from typing import Callable, Optional

from .errors import AIError, ErrorKind
from .proxy import ProxySettings, basic_auth_header

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
MAX_RETRY_AFTER_S = 86400.0
MAX_PROXY_HEADER_BYTES = 16384
PROXY_CONNECT_TIMEOUT_S = 10.0


def parse_retry_after(raw: Optional[str]) -> Optional[float]:
    """Seconds to wait from a ``Retry-After`` header: delta-seconds or an HTTP date. Invalid values give None."""
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        try:
            when = parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError):
            return None
        if when is None or when.tzinfo is None:
            return None
        value = max(0.0, when.timestamp() - time.time())
    return value if 0 <= value < MAX_RETRY_AFTER_S else None


@dataclass
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> object:
        return json.loads(self.body.decode("utf-8"))

    def retry_after(self) -> Optional[float]:
        return parse_retry_after(self.headers.get("retry-after"))


@dataclass
class CancelHandle:
    """Cooperative cancellation: ``cancel()`` may be called from any thread."""
    event: threading.Event = field(default_factory=threading.Event)
    _conn: Optional[http.client.HTTPConnection] = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def cancelled(self) -> bool:
        return self.event.is_set()

    def attach(self, conn: Optional[http.client.HTTPConnection]) -> None:
        with self._lock:
            self._conn = conn
        if conn is not None and self.event.is_set():
            self._close(conn)

    def cancel(self) -> None:
        self.event.set()
        with self._lock:
            conn = self._conn
        if conn is not None:
            self._close(conn)

    @staticmethod
    def _close(conn: http.client.HTTPConnection) -> None:
        try:
            if conn.sock is not None:
                conn.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            conn.close()
        except OSError:
            pass


def _make_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


def _require_verifying(ctx: ssl.SSLContext) -> ssl.SSLContext:
    if ctx.verify_mode != ssl.CERT_REQUIRED or not ctx.check_hostname:
        raise ValueError("certificate and host-name verification cannot be disabled")
    return ctx


class Transport:
    """The only code in Sentinel that talks to the network for AI features."""

    def __init__(self, proxy_provider: Optional[Callable[[], Optional[ProxySettings]]] = None,
                 ssl_context: Optional[ssl.SSLContext] = None) -> None:
        # ``proxy_provider`` is called for every request, so a settings change applies immediately. It returns None
        # for a direct connection and raises AIError if a proxy is enabled but unusable (fail closed).
        self._proxy_provider = proxy_provider
        self._ctx = _require_verifying(ssl_context) if ssl_context is not None else None
        self._ctx_lock = threading.Lock()

    def _context(self) -> ssl.SSLContext:
        with self._ctx_lock:
            if self._ctx is None:
                self._ctx = _make_context()
            return self._ctx

    def _proxy_for(self, scheme: str, host: str) -> Optional[ProxySettings]:
        if self._proxy_provider is None or scheme != "https":   # plain-HTTP loopback (tests) is never proxied
            return None
        return self._proxy_provider()

    # ------------------------------------------------------------------ connection
    def _open(self, conn: http.client.HTTPConnection, scheme: str, host: str, port: int, timeout: float,
              proxy: Optional[ProxySettings], cancel: Optional[CancelHandle], provider_id: str) -> None:
        """Connect ``conn`` (directly or through the proxy tunnel). Raises AIError with a precise kind."""
        if proxy is None:
            return                                      # http.client connects lazily and verifies TLS itself
        try:
            sock = socket.create_connection((proxy.host, proxy.port), timeout=min(timeout, PROXY_CONNECT_TIMEOUT_S))
        except (socket.timeout, TimeoutError):
            raise AIError(ErrorKind.PROXY, "timed out connecting to the proxy", provider_id=provider_id) from None
        except OSError as exc:
            raise AIError(ErrorKind.PROXY, f"cannot reach the proxy ({type(exc).__name__})",
                          provider_id=provider_id) from None
        conn.sock = sock                                # lets Cancel close it
        if cancel is not None and cancel.cancelled:
            raise AIError(ErrorKind.CANCELLED, provider_id=provider_id)
        sock.settimeout(timeout)
        lines = [f"CONNECT {host}:{port} HTTP/1.1", f"Host: {host}:{port}", "User-Agent: VYRXONE-Sentinel"]
        if proxy.has_auth:
            lines.append("Proxy-Authorization: " + basic_auth_header(proxy.username, proxy.password))
        try:
            sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("ascii"))
            buf = b""
            while b"\r\n\r\n" not in buf:
                chunk = sock.recv(4096)
                if not chunk:
                    raise AIError(ErrorKind.PROXY, "the proxy closed the connection", provider_id=provider_id)
                buf += chunk
                if len(buf) > MAX_PROXY_HEADER_BYTES:
                    raise AIError(ErrorKind.PROXY, "the proxy answer is too large", provider_id=provider_id)
        except (socket.timeout, TimeoutError):
            raise AIError(ErrorKind.PROXY, "the proxy did not answer in time", provider_id=provider_id) from None
        except AIError:
            raise
        except OSError as exc:
            raise AIError(ErrorKind.PROXY, f"proxy connection failed ({type(exc).__name__})",
                          provider_id=provider_id) from None
        head, _, rest = buf.partition(b"\r\n\r\n")
        status_line = head.split(b"\r\n", 1)[0].decode("latin-1", "replace")
        parts = status_line.split(" ", 2)
        try:
            status = int(parts[1]) if len(parts) >= 2 and parts[0].startswith("HTTP/") else 0
        except ValueError:
            status = 0
        if status == 407:
            raise AIError(ErrorKind.PROXY_AUTH, provider_id=provider_id, http_status=407)
        if status != 200:
            raise AIError(ErrorKind.PROXY, f"the proxy refused the tunnel (HTTP {status or 'invalid answer'})",
                          provider_id=provider_id, http_status=status or None)
        if rest:                                         # nothing may precede the TLS handshake
            raise AIError(ErrorKind.PROXY, "unexpected data from the proxy", provider_id=provider_id)
        self._handshake(conn, sock, host, provider_id)

    def _handshake(self, conn: http.client.HTTPConnection, sock: socket.socket, host: str, provider_id: str) -> None:
        try:
            conn.sock = self._context().wrap_socket(sock, server_hostname=host)
        except ssl.SSLCertVerificationError as exc:
            raise AIError(ErrorKind.CERTIFICATE, type(exc).__name__, provider_id=provider_id) from None
        except (socket.timeout, TimeoutError):
            raise AIError(ErrorKind.TIMEOUT, "TLS handshake timed out", provider_id=provider_id) from None

    def probe(self, url: str, timeout: float, cancel: Optional[CancelHandle] = None, provider_id: str = "") -> str:
        """Connect (through the proxy if one is enabled) and finish the TLS handshake with the host in *url*.
        Sends no HTTP request, so no key and no user data leave the PC. Returns "proxy" or "direct"."""
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname or ""
        if parts.scheme != "https":
            raise AIError(ErrorKind.BAD_REQUEST, "only HTTPS endpoints are allowed", provider_id=provider_id)
        port = parts.port or 443
        proxy = self._proxy_for(parts.scheme, host)
        conn = http.client.HTTPSConnection(host, port, timeout=timeout, context=self._context())
        if cancel is not None:
            cancel.attach(conn)
        try:
            if proxy is not None:
                self._open(conn, parts.scheme, host, port, timeout, proxy, cancel, provider_id)
            else:
                conn.connect()
            return "proxy" if proxy is not None else "direct"
        except AIError:
            raise
        except ssl.SSLCertVerificationError as exc:
            raise AIError(ErrorKind.CERTIFICATE, type(exc).__name__, provider_id=provider_id) from None
        except (socket.timeout, TimeoutError):
            if cancel is not None and cancel.cancelled:
                raise AIError(ErrorKind.CANCELLED, provider_id=provider_id) from None
            raise AIError(ErrorKind.TIMEOUT, provider_id=provider_id) from None
        except (OSError, http.client.HTTPException) as exc:
            if cancel is not None and cancel.cancelled:
                raise AIError(ErrorKind.CANCELLED, provider_id=provider_id) from None
            raise AIError(ErrorKind.NETWORK, type(exc).__name__, provider_id=provider_id) from None
        finally:
            if cancel is not None:
                cancel.attach(None)
            try:
                conn.close()
            except OSError:
                pass

    def request(self, method: str, url: str, *, headers: dict[str, str], body: Optional[bytes],
                timeout: float, cancel: Optional[CancelHandle] = None, provider_id: str = "") -> HttpResponse:
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname or ""
        if parts.scheme != "https" and not (parts.scheme == "http" and host in LOOPBACK):
            raise AIError(ErrorKind.BAD_REQUEST, "only HTTPS endpoints are allowed", provider_id=provider_id)
        if cancel is not None and cancel.cancelled:
            raise AIError(ErrorKind.CANCELLED, provider_id=provider_id)
        target = parts.path + (f"?{parts.query}" if parts.query else "")
        proxy = self._proxy_for(parts.scheme, host)          # may raise AIError (proxy enabled but unusable)
        if parts.scheme == "https":
            port = parts.port or 443
            conn: http.client.HTTPConnection = http.client.HTTPSConnection(
                host, port, timeout=timeout, context=self._context())
        else:
            port = parts.port or 80
            conn = http.client.HTTPConnection(host, port, timeout=timeout)
        if cancel is not None:
            cancel.attach(conn)
        try:
            self._open(conn, parts.scheme, host, port, timeout, proxy, cancel, provider_id)
            conn.request(method, target, body=body, headers=headers)
            resp = conn.getresponse()
            data = resp.read(MAX_RESPONSE_BYTES + 1)
            if len(data) > MAX_RESPONSE_BYTES:
                raise AIError(ErrorKind.MALFORMED, "response too large", provider_id=provider_id)
            return HttpResponse(resp.status, {k.lower(): v for k, v in resp.getheaders()}, data)
        except AIError as exc:
            if exc.kind != ErrorKind.CANCELLED and cancel is not None and cancel.cancelled:
                raise AIError(ErrorKind.CANCELLED, provider_id=provider_id) from None
            raise
        except ssl.SSLCertVerificationError as exc:
            raise AIError(ErrorKind.CERTIFICATE, type(exc).__name__, provider_id=provider_id) from None
        except (socket.timeout, TimeoutError) as exc:
            if cancel is not None and cancel.cancelled:
                raise AIError(ErrorKind.CANCELLED, provider_id=provider_id) from None
            raise AIError(ErrorKind.TIMEOUT, provider_id=provider_id) from exc
        except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
            if cancel is not None and cancel.cancelled:
                raise AIError(ErrorKind.CANCELLED, provider_id=provider_id) from None
            # Only the exception *type* is reported: messages can echo URLs.
            raise AIError(ErrorKind.NETWORK, type(exc).__name__, provider_id=provider_id) from exc
        finally:
            if cancel is not None:
                cancel.attach(None)
            try:
                conn.close()
            except OSError:
                pass
