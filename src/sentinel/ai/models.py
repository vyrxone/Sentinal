"""Shared internal types, identical for every provider."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Health(str, Enum):
    AVAILABLE = "Available"
    UNCONFIGURED = "Unconfigured"
    DISABLED = "Disabled"
    RATE_LIMITED = "Rate limited"
    AUTH_ERROR = "Authentication error"
    OFFLINE = "Offline"
    UNKNOWN = "Unknown"


@dataclass(frozen=True)
class ChatMessage:
    role: str            # "user" | "assistant"
    content: str


@dataclass(frozen=True)
class AIRequest:
    """What is sent: a trusted system prompt plus messages. This is exactly what the preview shows."""
    system: str
    messages: tuple[ChatMessage, ...]
    max_tokens: int = 700
    temperature: float = 0.2
    task: str = "chat"


@dataclass(frozen=True)
class Usage:
    """Token counts exactly as reported by the provider; ``None`` means the provider did not report it."""
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None

    @property
    def known(self) -> bool:
        return any(v is not None for v in (self.prompt_tokens, self.completion_tokens, self.total_tokens))


@dataclass(frozen=True)
class AIResponse:
    text: str
    provider_id: str
    provider_name: str
    model: str
    finish_reason: str = ""
    truncated: bool = False
    usage: Usage = field(default_factory=Usage)
    latency_s: float = 0.0
    notes: tuple[str, ...] = ()          # e.g. "text shortened by Sentinel"


@dataclass(frozen=True)
class Attempt:
    provider_id: str
    model: str
    outcome: str          # "ok" or an ErrorKind value
    detail: str = ""
    seconds: float = 0.0
    retry_after: Optional[float] = None


@dataclass(frozen=True)
class RouteResult:
    response: AIResponse
    attempts: tuple[Attempt, ...]
    skipped: tuple[tuple[str, str], ...] = ()     # (provider id, reason) not tried: cooldown, not configured...

    @property
    def fallback_used(self) -> bool:
        return len(self.attempts) > 1

    def fallback_note(self) -> str:
        failed = [a for a in self.attempts if a.outcome != "ok"]
        if not failed:
            return ""
        parts = [f"{a.provider_id} ({a.outcome.replace('_', ' ')})" for a in failed]
        return "Fallback: " + ", ".join(parts) + f" did not answer, so {self.response.provider_name} answered."


@dataclass(frozen=True)
class ConnectionResult:
    ok: bool
    provider_id: str
    message: str
    kind: str = "ok"
    model_count: Optional[int] = None
