"""AI configuration read from the settings store (non-secret values only)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .privacy import RedactionOptions
from .providers import PROVIDER_IDS, ProviderConfig

AI_SETTING_KEYS = (
    "ai_enabled", "ai_primary", "ai_fallback_order", "ai_max_response", "ai_max_requests_hour",
    "ai_max_requests_session", "ai_history_enabled", "ai_history_messages", "ai_consent_mode",
    "ai_redact_paths", "ai_redact_names", "ai_redact_addresses", "ai_fallback_on_permanent",
    "ai_context_max_events", "ai_proxy_enabled", "ai_proxy_host", "ai_proxy_port", "ai_proxy_auth",
) + tuple(f"ai_{p}_{f}" for p in PROVIDER_IDS for f in ("enabled", "model", "timeout"))

MAX_REQUEST_CHARS = 24000      # whole outgoing request (system + messages); larger requests are refused locally


def parse_order(text: str, primary: str) -> tuple[str, ...]:
    out: list[str] = []
    for part in (text or "").replace(" ", "").split(","):
        if part in PROVIDER_IDS and part != primary and part not in out:
            out.append(part)
    return tuple(out)


@dataclass(frozen=True)
class AIConfig:
    enabled: bool = False
    primary: str = "gemini"
    fallbacks: tuple[str, ...] = ()
    providers: dict[str, ProviderConfig] = field(default_factory=dict)
    max_tokens: int = 700
    max_requests_hour: int = 20
    max_requests_session: int = 100
    history_enabled: bool = False
    history_messages: int = 6
    consent_mode: str = "every_request"
    redaction: RedactionOptions = field(default_factory=RedactionOptions)
    fallback_on_permanent: bool = False
    context_max_events: int = 20

    @property
    def order(self) -> tuple[str, ...]:
        return (self.primary,) + self.fallbacks

    def provider(self, provider_id: str) -> ProviderConfig:
        return self.providers.get(provider_id, ProviderConfig())


def load_config(settings: Any) -> AIConfig:
    g = settings.get
    primary = g("ai_primary")
    providers = {p: ProviderConfig(bool(g(f"ai_{p}_enabled")), str(g(f"ai_{p}_model")).strip(),
                                   float(g(f"ai_{p}_timeout"))) for p in PROVIDER_IDS}
    return AIConfig(
        enabled=bool(g("ai_enabled")), primary=primary, fallbacks=parse_order(g("ai_fallback_order"), primary),
        providers=providers, max_tokens=int(g("ai_max_response")), max_requests_hour=int(g("ai_max_requests_hour")),
        max_requests_session=int(g("ai_max_requests_session")), history_enabled=bool(g("ai_history_enabled")),
        history_messages=int(g("ai_history_messages")), consent_mode=g("ai_consent_mode"),
        redaction=RedactionOptions(bool(g("ai_redact_paths")), bool(g("ai_redact_names")),
                                   bool(g("ai_redact_addresses"))),
        fallback_on_permanent=bool(g("ai_fallback_on_permanent")), context_max_events=int(g("ai_context_max_events")))
