"""Shared logic for OpenAI-compatible chat-completions endpoints (used by Groq and OpenRouter).

The two providers are separate classes: their error formats, token parameter names, model catalogues
and key-check endpoints differ, so they are not treated as interchangeable."""

from __future__ import annotations

from typing import Any

from ..errors import AIError, ErrorKind
from ..models import AIRequest, AIResponse, Usage
from .base import Provider, int_or_none, looks_like_model_problem, short_detail


class OpenAICompatProvider(Provider):
    token_param = "max_tokens"

    def auth_headers(self, key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {key}"}

    def build_request(self, req: AIRequest, model: str) -> tuple[str, dict]:
        messages = []
        if req.system:
            messages.append({"role": "system", "content": req.system})
        messages += [{"role": m.role, "content": m.content} for m in req.messages]
        body = {"model": model, "messages": messages, self.token_param: req.max_tokens,
                "temperature": req.temperature, "stream": False}
        return f"{self.base_url}{self.chat_path}", body

    chat_path = "/chat/completions"

    def _error_from_body(self, payload: dict) -> AIError:
        err = payload.get("error")
        code = err.get("code") if isinstance(err, dict) else None
        status = code if isinstance(code, int) else 500
        return self.classify_error(status, {}, payload)

    def normalize_response(self, payload: Any, model: str, latency_s: float) -> AIResponse:
        if not isinstance(payload, dict):
            raise self._bad(ErrorKind.MALFORMED, "unexpected response shape")
        choices = payload.get("choices")
        if not choices:
            if "error" in payload:                      # an error can arrive inside an HTTP 200 body
                raise self._error_from_body(payload)
            raise self._bad(ErrorKind.EMPTY, "no choices returned")
        choice = choices[0] if isinstance(choices, list) and isinstance(choices[0], dict) else None
        if choice is None:
            raise self._bad(ErrorKind.MALFORMED, "unexpected choice shape")
        finish = str(choice.get("finish_reason") or "")
        if isinstance(choice.get("error"), dict):
            raise self._error_from_body(choice)
        if finish == "error":
            raise self._bad(ErrorKind.SERVER, "the provider reported an error while generating")
        message = choice.get("message")
        if not isinstance(message, dict):
            raise self._bad(ErrorKind.MALFORMED, "missing message")
        if message.get("refusal"):
            raise self._bad(ErrorKind.REFUSED, "the model refused")
        content = message.get("content")
        if content is not None and not isinstance(content, str):
            raise self._bad(ErrorKind.MALFORMED, "message content is not text")
        if finish == "content_filter" and not (content or "").strip():
            raise self._bad(ErrorKind.REFUSED, "blocked by a content filter")
        if not (content or "").strip():
            raise self._bad(ErrorKind.EMPTY, f"finish reason {finish or 'unknown'}")
        usage = payload.get("usage")
        u = Usage()
        if isinstance(usage, dict):
            u = Usage(int_or_none(usage.get("prompt_tokens")), int_or_none(usage.get("completion_tokens")),
                      int_or_none(usage.get("total_tokens")))
        return AIResponse(text=content, provider_id=self.provider_id, provider_name=self.display_name,
                          model=str(payload.get("model") or model)[:120], finish_reason=finish,
                          truncated=finish == "length", usage=u, latency_s=latency_s)

    def classify_error(self, status: int, headers: dict[str, str], payload: Any) -> AIError:
        base = self._generic_status(status, headers, payload)
        detail = short_detail(payload)
        err = payload.get("error") if isinstance(payload, dict) else None
        code = str(err.get("code", "")) if isinstance(err, dict) else ""
        kw = dict(provider_id=self.provider_id, http_status=status, retry_after=base.retry_after)
        if code in ("model_not_found", "model_decommissioned", "model_terms_required") or \
                (status in (400, 404) and looks_like_model_problem(detail)):
            return AIError(ErrorKind.INVALID_MODEL, detail, **kw)
        if code in ("invalid_api_key",):
            return AIError(ErrorKind.AUTH, detail, **kw)
        if code in ("rate_limit_exceeded",) and status == 429:
            return AIError(ErrorKind.RATE_LIMIT, detail, **kw)
        if status == 422:
            return AIError(ErrorKind.BAD_REQUEST, detail, **kw)
        return base

    def parse_model_ids(self, payload: Any) -> list[str]:
        data = payload.get("data") if isinstance(payload, dict) else None
        return [m["id"] for m in data or [] if isinstance(m, dict) and isinstance(m.get("id"), str)]
