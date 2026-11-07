"""Provider registry. Adding a provider means adding one module here; no other provider is affected."""

from __future__ import annotations

from typing import Optional

from .base import Provider, ProviderConfig
from .gemini import GeminiProvider
from .groq import GroqProvider
from .openrouter import OpenRouterProvider

PROVIDER_CLASSES: dict[str, type[Provider]] = {
    "gemini": GeminiProvider, "groq": GroqProvider, "openrouter": OpenRouterProvider}
PROVIDER_IDS = tuple(PROVIDER_CLASSES)


def make_provider(provider_id: str, base_url: Optional[str] = None) -> Provider:
    return PROVIDER_CLASSES[provider_id](base_url)


__all__ = ["Provider", "ProviderConfig", "PROVIDER_CLASSES", "PROVIDER_IDS", "make_provider"]
