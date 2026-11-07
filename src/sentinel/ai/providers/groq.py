"""Groq API (OpenAI-compatible endpoint under /openai/v1). Bearer-token authentication."""

from __future__ import annotations

from .openai_compat import OpenAICompatProvider


class GroqProvider(OpenAICompatProvider):
    provider_id = "groq"
    display_name = "Groq"
    base_url = "https://api.groq.com/openai/v1"
    help_url = "https://console.groq.com/keys"
    token_param = "max_completion_tokens"

    def connection_request(self) -> tuple[str, bool]:
        return f"{self.base_url}/models", True
