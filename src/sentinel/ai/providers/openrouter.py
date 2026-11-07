"""OpenRouter API (OpenAI-compatible chat completions at /api/v1). Bearer-token authentication.

OpenRouter can report a failure inside an HTTP 200 body (``error`` / ``finish_reason: "error"``);
the shared parser checks for that. The key is verified with ``GET /key``, because ``/models`` is public."""

from __future__ import annotations

from ..errors import AIError, ErrorKind
from ..transport import parse_retry_after
from .base import short_detail
from .openai_compat import OpenAICompatProvider


class OpenRouterProvider(OpenAICompatProvider):
    provider_id = "openrouter"
    display_name = "OpenRouter"
    base_url = "https://openrouter.ai/api/v1"
    help_url = "https://openrouter.ai/keys"
    token_param = "max_tokens"

    def connection_request(self) -> tuple[str, bool]:
        return f"{self.base_url}/key", True

    def model_list_request(self) -> tuple[str, bool]:
        return f"{self.base_url}/models", False

    def parse_model_ids(self, payload):
        # /key returns {"data": {...key info...}} (a dict), /models returns {"data": [ ... ]}
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, dict):
            return []
        return super().parse_model_ids(payload)

    def classify_error(self, status, headers, payload):
        """A 402 whose ``error.metadata.limit_source`` is ``openrouter_in_flight_budget`` is a temporary budget limit
        (the provider sends Retry-After): treat it as a rate limit. Any other 402 means credits are exhausted."""
        err = payload.get("error") if isinstance(payload, dict) else None
        meta = err.get("metadata") if isinstance(err, dict) else None
        if status == 402 and isinstance(meta, dict) and meta.get("limit_source") == "openrouter_in_flight_budget":
            return AIError(ErrorKind.RATE_LIMIT, short_detail(payload), provider_id=self.provider_id, http_status=402,
                           retry_after=parse_retry_after((headers or {}).get("retry-after")))
        return super().classify_error(status, headers, payload)
