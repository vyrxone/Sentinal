"""Test doubles for the AI layer. No real API key or network is ever used by the automated tests."""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable, Optional

from sentinel.ai.errors import AIError, ErrorKind
from sentinel.ai.transport import CancelHandle, HttpResponse, Transport

KEY = "TESTKEY-aaaaaaaa-bbbbbbbb-cccccccc-1234"        # looks like a secret but is not one
KEY2 = "TESTKEY-zzzzzzzz-yyyyyyyy-xxxxxxxx-9876"


def resp(status: int = 200, payload: Any = None, headers: Optional[dict] = None, raw: Optional[bytes] = None) -> HttpResponse:
    body = raw if raw is not None else json.dumps(payload if payload is not None else {}).encode()
    return HttpResponse(status, {k.lower(): v for k, v in (headers or {}).items()}, body)


def gemini_ok(text="Gemini says hello", finish="STOP", usage=True):
    p = {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": finish}]}
    if usage:
        p["usageMetadata"] = {"promptTokenCount": 11, "candidatesTokenCount": 7, "totalTokenCount": 18}
    return p


def openai_ok(text="Hello from the model", finish="stop", usage=True, model="m-1"):
    p = {"model": model, "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                                      "finish_reason": finish}]}
    if usage:
        p["usage"] = {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17}
    return p


class FakeTransport(Transport):
    """Scripted transport. ``script`` maps a substring of the URL to a list of outcomes (HttpResponse, AIError, or a
    callable(call) -> outcome). The last outcome repeats. Every call is recorded."""

    def __init__(self, script: Optional[dict] = None, default: Optional[Callable] = None, delay: float = 0.0) -> None:
        self.script = {k: list(v) if isinstance(v, list) else [v] for k, v in (script or {}).items()}
        self.default = default
        self.delay = delay
        self.calls: list[dict] = []
        self.lock = threading.Lock()

    def request(self, method, url, *, headers, body, timeout, cancel: Optional[CancelHandle] = None, provider_id=""):
        call = {"method": method, "url": url, "headers": dict(headers), "body": body,
                "json": json.loads(body) if body else None, "timeout": timeout, "provider": provider_id}
        with self.lock:
            self.calls.append(call)
        if self.delay:
            end = time.monotonic() + self.delay
            while time.monotonic() < end:
                if cancel is not None and cancel.cancelled:
                    raise AIError(ErrorKind.CANCELLED, provider_id=provider_id)
                time.sleep(0.01)
        outcome = None
        for frag, seq in self.script.items():
            if frag in url:
                outcome = seq.pop(0) if len(seq) > 1 else seq[0]
                break
        else:
            if self.default is None:
                raise AssertionError(f"unscripted request to {url}")
            outcome = self.default
        if callable(outcome):
            outcome = outcome(call)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    @property
    def count(self) -> int:
        return len(self.calls)


class FakeKeyring:
    """Stands in for the Windows Credential Manager backend."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service, user):
        return self.store.get((service, user))

    def set_password(self, service, user, value):
        self.store[(service, user)] = value

    def delete_password(self, service, user):
        self.store.pop((service, user), None)
