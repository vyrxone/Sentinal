"""Usage and performance bookkeeping. Nothing here stores prompts, answers or keys.

Token counts are shown only when a provider reported them. Prices and costs are never estimated."""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional

from .errors import AIError, ErrorKind
from .models import Usage

HOUR = 3600.0


@dataclass
class ProviderUsage:
    requests: int = 0
    succeeded: int = 0
    failed: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    requests_with_usage: int = 0
    latency_total_s: float = 0.0


@dataclass(frozen=True)
class UsageRecord:
    ts: float
    provider_id: str
    model: str
    outcome: str
    seconds: float
    usage: Usage


class UsageTracker:
    def __init__(self, clock=time.time) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self.session_start = clock()
        self.by_provider: dict[str, ProviderUsage] = {}
        self.recent: deque[UsageRecord] = deque(maxlen=50)
        self._stamps: deque[float] = deque(maxlen=1000)     # one stamp per outbound generation attempt
        self.session_requests = 0
        # live gauges for the Performance page
        self.active = 0
        self.queue_depth = 0
        self.workers = 1
        self.last_latency_s: Optional[float] = None

    # ------------------------------------------------------------ limits
    def check_limit(self, max_per_hour: int, max_per_session: int, provider_id: str = "") -> None:
        now = self._clock()
        with self._lock:
            in_hour = sum(1 for t in self._stamps if now - t < HOUR)
            if self.session_requests >= max_per_session:
                raise AIError(ErrorKind.LIMIT, f"session limit of {max_per_session} requests reached",
                              provider_id=provider_id)
            if in_hour >= max_per_hour:
                raise AIError(ErrorKind.LIMIT, f"hourly limit of {max_per_hour} requests reached",
                              provider_id=provider_id)

    def count_attempt(self) -> None:
        with self._lock:
            self._stamps.append(self._clock())
            self.session_requests += 1

    def requests_last_hour(self) -> int:
        now = self._clock()
        with self._lock:
            return sum(1 for t in self._stamps if now - t < HOUR)

    # ------------------------------------------------------------ records
    def record(self, provider_id: str, model: str, outcome: str, seconds: float, usage: Optional[Usage] = None) -> None:
        usage = usage or Usage()
        with self._lock:
            pu = self.by_provider.setdefault(provider_id, ProviderUsage())
            pu.requests += 1
            pu.latency_total_s += seconds
            if outcome == "ok":
                pu.succeeded += 1
                self.last_latency_s = seconds
            else:
                pu.failed += 1
            if usage.known:
                pu.requests_with_usage += 1
                pu.prompt_tokens += usage.prompt_tokens or 0
                pu.completion_tokens += usage.completion_tokens or 0
                pu.total_tokens += usage.total_tokens if usage.total_tokens is not None else \
                    (usage.prompt_tokens or 0) + (usage.completion_tokens or 0)
            self.recent.append(UsageRecord(self._clock(), provider_id, model, outcome, seconds, usage))

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "session_requests": self.session_requests,
                "last_hour": sum(1 for t in self._stamps if self._clock() - t < HOUR),
                "providers": {k: vars(v).copy() for k, v in self.by_provider.items()},
                "active": self.active, "queue_depth": self.queue_depth, "workers": self.workers,
                "last_latency_s": self.last_latency_s,
            }

    def reset(self) -> None:
        with self._lock:
            self.by_provider.clear()
            self.recent.clear()
            self._stamps.clear()
            self.session_requests = 0
            self.last_latency_s = None
