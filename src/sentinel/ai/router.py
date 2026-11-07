"""Provider-independent router: primary provider, ordered fallbacks, cooldowns, limits, cancellation.

``route`` is blocking and is always called from a background worker (never the Qt UI thread).
Rules (see docs/AI_PROVIDER_SUPPORT.md):
* each provider is tried at most once per request - there is no retry loop;
* transient failures (network, timeout, 5xx, rate limit, empty/malformed answer) fall through to the next provider;
* permanent failures (bad key, no permission, unknown model, bad request, too large) stop the request and are shown,
  unless the user enabled "fallback on permanent errors";
* a provider refusal (safety/content filter) is never forwarded to another provider;
* ``Retry-After`` puts a provider on cooldown (capped at 5 minutes); repeated failures use exponential back-off.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from .config import MAX_REQUEST_CHARS, AIConfig
from .errors import PERMANENT, TRANSIENT, AIError, ErrorKind
from .models import AIRequest, Attempt, ConnectionResult, Health, RouteResult
from .providers import PROVIDER_IDS, Provider, make_provider
from .response_validator import validate_answer
from .secrets import SecretStore
from .transport import CancelHandle, Transport
from .usage import UsageTracker
from dataclasses import replace

log = logging.getLogger("sentinel.ai.router")

MAX_COOLDOWN_S = 300.0
BASE_BACKOFF_S = 60.0
FAILS_BEFORE_BACKOFF = 3


class RouteFailed(AIError):
    """Every eligible provider failed (or the first permanent error stopped the request)."""

    def __init__(self, last: AIError, attempts: tuple[Attempt, ...], skipped: tuple[tuple[str, str], ...] = ()) -> None:
        self.attempts = attempts
        self.skipped = skipped
        self.last = last
        super().__init__(last.kind, last.detail, provider_id=last.provider_id, retry_after=last.retry_after,
                         http_status=last.http_status)

    @property
    def message(self) -> str:  # type: ignore[override]
        text = self.last.message
        if len(self.attempts) > 1:
            text += " Providers tried: " + ", ".join(f"{a.provider_id} ({a.outcome.replace('_', ' ')})"
                                                      for a in self.attempts) + "."
        return text

    def __str__(self) -> str:
        return self.message


@dataclass
class _State:
    failures: int = 0
    cooldown_until: float = 0.0
    last_kind: str = ""
    last_ok: float = 0.0


class AIRouter:
    def __init__(self, config_fn: Callable[[], AIConfig], secrets: SecretStore, usage: UsageTracker,
                 transport: Optional[Transport] = None,
                 provider_factory: Callable[[str], Provider] = make_provider,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._config = config_fn
        self.secrets = secrets
        self.usage = usage
        self.transport = transport or Transport()
        self._factory = provider_factory
        self._clock = clock
        self._providers: dict[str, Provider] = {}
        self._state: dict[str, _State] = {p: _State() for p in PROVIDER_IDS}
        self._lock = threading.Lock()
        self._busy = threading.Lock()

    def provider(self, provider_id: str) -> Provider:
        if provider_id not in self._providers:
            self._providers[provider_id] = self._factory(provider_id)
        return self._providers[provider_id]

    # ------------------------------------------------------------------ readiness and health
    def problems(self, provider_id: str, cfg: Optional[AIConfig] = None) -> list[str]:
        cfg = cfg or self._config()
        return self.provider(provider_id).validate_configuration(cfg.provider(provider_id),
                                                                 self.secrets.has_key(provider_id))

    def health(self, provider_id: str) -> Health:
        cfg = self._config()
        if not cfg.provider(provider_id).enabled:
            return Health.DISABLED
        if self.problems(provider_id, cfg):
            return Health.UNCONFIGURED
        with self._lock:
            st = self._state[provider_id]
            now = self._clock()
            if st.last_kind == ErrorKind.RATE_LIMIT.value and st.cooldown_until > now:
                return Health.RATE_LIMITED
            if st.last_kind in (ErrorKind.AUTH.value, ErrorKind.PERMISSION.value):
                return Health.AUTH_ERROR
            if st.last_kind in (ErrorKind.NETWORK.value, ErrorKind.TIMEOUT.value, ErrorKind.PROXY.value,
                                ErrorKind.PROXY_AUTH.value, ErrorKind.CERTIFICATE.value):
                return Health.OFFLINE
            if st.last_ok and not st.last_kind:
                return Health.AVAILABLE
        return Health.UNKNOWN

    def cooldown_remaining(self, provider_id: str) -> float:
        with self._lock:
            return max(0.0, self._state[provider_id].cooldown_until - self._clock())

    def provider_changed(self, provider_id: Optional[str] = None) -> None:
        """Forget failure history (call after a key, model or enable change)."""
        with self._lock:
            for pid in ([provider_id] if provider_id else list(self._state)):
                self._state[pid] = _State()

    def _note_failure(self, pid: str, err: AIError) -> None:
        with self._lock:
            st = self._state[pid]
            st.last_kind = err.kind.value
            if err.kind in (ErrorKind.AUTH, ErrorKind.PERMISSION):
                st.failures += 1
                return
            if err.kind == ErrorKind.RATE_LIMIT:
                st.failures += 1
                wait = err.retry_after if err.retry_after else BASE_BACKOFF_S
                st.cooldown_until = self._clock() + min(wait, MAX_COOLDOWN_S)
            elif err.kind in TRANSIENT:
                st.failures += 1
                if st.failures >= FAILS_BEFORE_BACKOFF:
                    st.cooldown_until = self._clock() + min(
                        BASE_BACKOFF_S * 2 ** (st.failures - FAILS_BEFORE_BACKOFF), MAX_COOLDOWN_S)

    def _note_success(self, pid: str) -> None:
        with self._lock:
            self._state[pid] = _State(last_ok=self._clock())

    # ------------------------------------------------------------------ chain
    def plan(self, cfg: AIConfig, override: Optional[str] = None) -> tuple[list[str], list[tuple[str, str]]]:
        """Providers that will be tried, in order, and the ones skipped (with the reason)."""
        chain: list[str] = []
        skipped: list[tuple[str, str]] = []
        candidates = [override] if override else list(cfg.order)
        now = self._clock()
        for pid in candidates:
            if pid not in PROVIDER_IDS:
                continue
            if not cfg.provider(pid).enabled:
                skipped.append((pid, "disabled"))
                continue
            probs = self.problems(pid, cfg)
            if probs:
                skipped.append((pid, "not configured"))
                continue
            with self._lock:
                cooling = self._state[pid].cooldown_until > now
            if cooling and not override:
                skipped.append((pid, "cooling down after errors"))
                continue
            chain.append(pid)
        return chain, skipped

    # ------------------------------------------------------------------ request
    def route(self, req: AIRequest, cancel: Optional[CancelHandle] = None, override: Optional[str] = None) -> RouteResult:
        cfg = self._config()
        cancel = cancel or CancelHandle()
        if not cfg.enabled:
            raise AIError(ErrorKind.DISABLED, "AI is switched off")
        if sum(len(req.system) + len(m.content) for m in req.messages) > MAX_REQUEST_CHARS:
            raise AIError(ErrorKind.TOO_LARGE, "the request exceeds Sentinel's size limit")
        if not self._busy.acquire(blocking=False):
            raise AIError(ErrorKind.BUSY)
        try:
            return self._route(req, cfg, cancel, override)
        finally:
            self._busy.release()

    def _route(self, req: AIRequest, cfg: AIConfig, cancel: CancelHandle, override: Optional[str]) -> RouteResult:
        chain, skipped = self.plan(cfg, override)
        if not chain:
            why = "; ".join(f"{p}: {r}" for p, r in skipped) or "no provider is selected"
            raise AIError(ErrorKind.NOT_CONFIGURED, why)
        attempts: list[Attempt] = []
        last: Optional[AIError] = None
        for pid in chain:
            if cancel.cancelled:
                raise AIError(ErrorKind.CANCELLED)
            pcfg = cfg.provider(pid)
            request = replace(req, max_tokens=min(req.max_tokens, cfg.max_tokens))
            t0 = time.monotonic()
            try:
                self.usage.check_limit(cfg.max_requests_hour, cfg.max_requests_session, pid)
                key = self.secrets.get_key(pid)
                if not key:
                    raise AIError(ErrorKind.NOT_CONFIGURED, "no API key", provider_id=pid)
                self.usage.count_attempt()
                resp = self.provider(pid).generate(request, pcfg, key, self.transport, cancel)
                text, notes = validate_answer(resp.text, provider_id=pid)
                resp = replace(resp, text=text, notes=resp.notes + notes)
            except AIError as err:
                secs = time.monotonic() - t0
                if err.kind == ErrorKind.CANCELLED or cancel.cancelled:
                    raise AIError(ErrorKind.CANCELLED, provider_id=pid) from None
                if err.kind == ErrorKind.LIMIT:
                    raise
                attempts.append(Attempt(pid, pcfg.model, err.kind.value, err.detail, secs, err.retry_after))
                self.usage.record(pid, pcfg.model, err.kind.value, secs)
                self._note_failure(pid, err)
                last = err
                log.info("provider %s failed: %s", pid, err.kind.value)
                eligible = err.kind in TRANSIENT or (err.kind in PERMANENT and cfg.fallback_on_permanent)
                if not eligible or override:
                    raise RouteFailed(err, tuple(attempts), tuple(skipped)) from None
                continue
            except Exception as exc:       # a provider bug must not break the router
                log.exception("provider %s raised an unexpected error", pid)
                err = AIError(ErrorKind.UNKNOWN, type(exc).__name__, provider_id=pid)
                attempts.append(Attempt(pid, pcfg.model, err.kind.value, err.detail, time.monotonic() - t0))
                self._note_failure(pid, err)
                last = err
                continue
            secs = time.monotonic() - t0
            resp = replace(resp, latency_s=secs)
            attempts.append(Attempt(pid, pcfg.model, "ok", "", secs))
            self.usage.record(pid, resp.model, "ok", secs, resp.usage)
            self._note_success(pid)
            return RouteResult(resp, tuple(attempts), tuple(skipped))
        assert last is not None
        raise RouteFailed(last, tuple(attempts), tuple(skipped))

    # ------------------------------------------------------------------ user-triggered helpers
    def test_connection(self, provider_id: str, cancel: Optional[CancelHandle] = None) -> ConnectionResult:
        """Contacts a read-only endpoint; never sends events, findings, projects or any conversation text."""
        cfg = self._config()
        p = self.provider(provider_id)
        result = p.test_connection(self.secrets.get_key(provider_id), cfg.provider(provider_id).timeout_s,
                                   self.transport, cancel)
        if result.ok:
            self._note_success(provider_id)
        elif result.kind in ("auth", "permission", "network", "timeout", "proxy", "proxy_auth", "certificate"):
            with self._lock:
                self._state[provider_id].last_kind = result.kind
        return result

    def test_proxy(self, cancel: Optional[CancelHandle] = None) -> ConnectionResult:
        """Tunnel through the configured proxy to the primary provider's host and finish the TLS handshake.
        No HTTP request is made: no key, event or conversation text leaves the PC."""
        cfg = self._config()
        pid = cfg.primary
        url = self.provider(pid).base_url
        host = url.split("//", 1)[-1].split("/", 1)[0]
        try:
            how = self.transport.probe(url, cfg.provider(pid).timeout_s, cancel, pid)
        except AIError as exc:
            return ConnectionResult(False, "proxy", exc.message, exc.kind.value)
        if how != "proxy":
            return ConnectionResult(False, "proxy", "No proxy is enabled; this was a direct connection test.",
                                    "not_configured")
        return ConnectionResult(True, "proxy", f"The proxy tunnel and the secure handshake with {host} worked.", "ok")

    def list_models(self, provider_id: str, cancel: Optional[CancelHandle] = None) -> list[str]:
        cfg = self._config()
        return self.provider(provider_id).get_model_options(
            self.secrets.get_key(provider_id), cfg.provider(provider_id).timeout_s, self.transport, cancel)
