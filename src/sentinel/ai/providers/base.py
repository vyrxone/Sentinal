"""Provider interface. Each provider is isolated: it only builds a request, calls the shared transport and
normalises the answer. It never touches the database, the UI or another provider."""

from __future__ import annotations

import json
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional

from ..errors import AIError, ErrorKind
from ..models import AIRequest, AIResponse, ConnectionResult
from ..privacy import scrub
from ..transport import CancelHandle, HttpResponse, Transport, parse_retry_after

MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/\-]{0,119}$")


@dataclass(frozen=True)
class ProviderConfig:
    enabled: bool = False
    model: str = ""
    timeout_s: float = 30.0


def valid_model_id(model: str) -> bool:
    return bool(MODEL_ID_RE.match(model or "")) and ".." not in model


def short_detail(payload: Any) -> str:
    """A short provider message for the user, scrubbed of anything key-like."""
    msg = ""
    if isinstance(payload, dict):
        err = payload.get("error", payload)
        if isinstance(err, dict):
            msg = str(err.get("message") or err.get("status") or "")
        elif isinstance(err, str):
            msg = err
    return scrub(" ".join(msg.split()))[:200]


def int_or_none(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


class Provider(ABC):
    provider_id: str = ""
    display_name: str = ""
    base_url: str = ""
    help_url: str = ""

    def __init__(self, base_url: Optional[str] = None) -> None:
        if base_url:
            self.base_url = base_url.rstrip("/")

    # ---------------------------------------------------------------- configuration
    def validate_configuration(self, cfg: ProviderConfig, has_key: bool) -> list[str]:
        problems = []
        if not has_key:
            problems.append("No API key is set.")
        if not cfg.model:
            problems.append("No model ID is set (enter a model ID from the provider's own model list).")
        elif not valid_model_id(cfg.model):
            problems.append("The model ID contains unsupported characters.")
        if not (3 <= cfg.timeout_s <= 120):
            problems.append("The timeout must be between 3 and 120 seconds.")
        return problems

    # ---------------------------------------------------------------- request building (pure, testable)
    @abstractmethod
    def auth_headers(self, key: str) -> dict[str, str]: ...

    @abstractmethod
    def build_request(self, req: AIRequest, model: str) -> tuple[str, dict]:
        """Return (url, JSON body). Contains no key: the preview shows exactly this body."""

    @abstractmethod
    def normalize_response(self, payload: Any, model: str, latency_s: float) -> AIResponse: ...

    @abstractmethod
    def classify_error(self, status: int, headers: dict[str, str], payload: Any) -> AIError: ...

    @abstractmethod
    def connection_request(self) -> tuple[str, bool]:
        """(url, needs_key) of a read-only endpoint that sends no user data (used by Test connection)."""

    @abstractmethod
    def parse_model_ids(self, payload: Any) -> list[str]: ...

    # ---------------------------------------------------------------- network calls
    def _call(self, method: str, url: str, key: Optional[str], body: Optional[dict], timeout: float,
              transport: Transport, cancel: Optional[CancelHandle]) -> HttpResponse:
        headers = {"Accept": "application/json", "User-Agent": "VYRXONE-Sentinel"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        if key:
            headers.update(self.auth_headers(key))
        return transport.request(method, url, headers=headers, body=data, timeout=timeout, cancel=cancel,
                                 provider_id=self.provider_id)

    def _json(self, resp: HttpResponse) -> Any:
        try:
            return resp.json()
        except (ValueError, UnicodeDecodeError):
            return None

    def generate(self, req: AIRequest, cfg: ProviderConfig, key: str, transport: Transport,
                 cancel: Optional[CancelHandle] = None) -> AIResponse:
        if not valid_model_id(cfg.model):
            raise AIError(ErrorKind.NOT_CONFIGURED, "model ID missing or invalid", provider_id=self.provider_id)
        url, body = self.build_request(req, cfg.model)
        t0 = time.monotonic()
        resp = self._call("POST", url, key, body, cfg.timeout_s, transport, cancel)
        latency = time.monotonic() - t0
        payload = self._json(resp)
        if not 200 <= resp.status < 300:
            raise self.classify_error(resp.status, resp.headers, payload)
        if payload is None:
            raise AIError(ErrorKind.MALFORMED, "the answer was not valid JSON", provider_id=self.provider_id)
        return self.normalize_response(payload, cfg.model, latency)

    def test_connection(self, key: Optional[str], timeout: float, transport: Transport,
                        cancel: Optional[CancelHandle] = None) -> ConnectionResult:
        """Contacts a read-only endpoint with no user data. Never sends events, findings or project data."""
        url, needs_key = self.connection_request()
        if needs_key and not key:
            return ConnectionResult(False, self.provider_id, "No API key is set.", "not_configured")
        try:
            resp = self._call("GET", url, key if needs_key else None, None, timeout, transport, cancel)
        except AIError as exc:
            return ConnectionResult(False, self.provider_id, exc.message, exc.kind.value)
        payload = self._json(resp)
        if not 200 <= resp.status < 300:
            err = self.classify_error(resp.status, resp.headers, payload)
            return ConnectionResult(False, self.provider_id, err.message, err.kind.value)
        count = len(self.parse_model_ids(payload)) if payload is not None else None
        return ConnectionResult(True, self.provider_id, "Connection and key accepted.", "ok", count or None)

    def get_model_options(self, key: Optional[str], timeout: float, transport: Transport,
                          cancel: Optional[CancelHandle] = None) -> list[str]:
        """Model IDs as reported by the provider's own listing endpoint (user-triggered; no user data sent)."""
        url, needs_key = self.model_list_request()
        resp = self._call("GET", url, key if needs_key else None, None, timeout, transport, cancel)
        payload = self._json(resp)
        if not 200 <= resp.status < 300:
            raise self.classify_error(resp.status, resp.headers, payload)
        return sorted(set(self.parse_model_ids(payload)))[:500]

    def model_list_request(self) -> tuple[str, bool]:
        return self.connection_request()

    # ---------------------------------------------------------------- shared helpers
    def _bad(self, kind: ErrorKind, detail: str = "") -> AIError:
        return AIError(kind, detail, provider_id=self.provider_id)

    def _generic_status(self, status: int, headers: dict[str, str], payload: Any) -> AIError:
        detail = short_detail(payload)
        retry = parse_retry_after(headers.get("retry-after"))
        kw = dict(provider_id=self.provider_id, retry_after=retry, http_status=status)
        if status == 401:
            return AIError(ErrorKind.AUTH, detail, **kw)
        if status in (402, 403):
            return AIError(ErrorKind.PERMISSION, detail, **kw)
        if status == 404:
            return AIError(ErrorKind.INVALID_MODEL, detail, **kw)
        if status == 413:
            return AIError(ErrorKind.TOO_LARGE, detail, **kw)
        if status == 429:
            return AIError(ErrorKind.RATE_LIMIT, detail, **kw)
        if status in (408, 504):
            return AIError(ErrorKind.TIMEOUT, detail, **kw)
        if status >= 500:
            return AIError(ErrorKind.SERVER, detail, **kw)
        if 400 <= status < 500:
            return AIError(ErrorKind.BAD_REQUEST, detail, **kw)
        return AIError(ErrorKind.UNKNOWN, detail, **kw)


def looks_like_model_problem(message: str) -> bool:
    m = message.lower()
    return "model" in m and any(t in m for t in ("not found", "not a valid", "does not exist", "decommission",
                                                  "no endpoints", "unknown model", "not supported", "invalid model"))
