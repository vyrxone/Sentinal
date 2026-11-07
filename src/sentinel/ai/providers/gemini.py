"""Google Gemini API (generativelanguage.googleapis.com, v1beta ``generateContent``).

Authentication: the ``x-goog-api-key`` header (the key is never put in the URL, so it cannot end up in logs).
Gemini has its own request/response shape (``contents``/``parts``/``candidates``), not the OpenAI format.
"""

from __future__ import annotations

import re
from typing import Any

from ..errors import AIError, ErrorKind
from ..models import AIRequest, AIResponse, Usage
from .base import Provider, int_or_none, looks_like_model_problem, short_detail

_PATH_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,99}$")
_REFUSAL = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION", "LANGUAGE"}


class GeminiProvider(Provider):
    provider_id = "gemini"
    display_name = "Google Gemini"
    base_url = "https://generativelanguage.googleapis.com"
    help_url = "https://aistudio.google.com/apikey"

    def auth_headers(self, key: str) -> dict[str, str]:
        return {"x-goog-api-key": key}

    def validate_configuration(self, cfg, has_key):
        problems = super().validate_configuration(cfg, has_key)
        name = cfg.model[len("models/"):] if cfg.model.startswith("models/") else cfg.model
        if cfg.model and not problems and not _PATH_MODEL.match(name):
            problems.append("A Gemini model ID has no '/' or ':' (for example the name shown by 'Fetch model list').")
        return problems

    @staticmethod
    def _model_path(model: str) -> str:
        name = model[len("models/"):] if model.startswith("models/") else model
        if not _PATH_MODEL.match(name):
            raise AIError(ErrorKind.NOT_CONFIGURED, "model ID has unsupported characters", provider_id="gemini")
        return name

    def build_request(self, req: AIRequest, model: str) -> tuple[str, dict]:
        name = self._model_path(model)
        contents = [{"role": "model" if m.role == "assistant" else "user", "parts": [{"text": m.content}]}
                    for m in req.messages]
        body: dict = {"contents": contents,
                      "generationConfig": {"maxOutputTokens": req.max_tokens, "temperature": req.temperature}}
        if req.system:
            body["systemInstruction"] = {"parts": [{"text": req.system}]}
        return f"{self.base_url}/v1beta/models/{name}:generateContent", body

    def normalize_response(self, payload: Any, model: str, latency_s: float) -> AIResponse:
        if not isinstance(payload, dict):
            raise self._bad(ErrorKind.MALFORMED, "unexpected response shape")
        feedback = payload.get("promptFeedback")
        block = feedback.get("blockReason") if isinstance(feedback, dict) else None
        cands = payload.get("candidates")
        if not cands:
            if block and block != "BLOCK_REASON_UNSPECIFIED":
                raise self._bad(ErrorKind.REFUSED, f"prompt blocked: {block}")
            raise self._bad(ErrorKind.EMPTY, "no candidates returned")
        cand = cands[0] if isinstance(cands, list) and cands and isinstance(cands[0], dict) else None
        if cand is None:
            raise self._bad(ErrorKind.MALFORMED, "unexpected candidate shape")
        finish = str(cand.get("finishReason") or "")
        content = cand.get("content")
        parts = content.get("parts") if isinstance(content, dict) else None
        text = ""
        if isinstance(parts, list):
            text = "".join(p["text"] for p in parts
                           if isinstance(p, dict) and isinstance(p.get("text"), str) and not p.get("thought"))
        if not text.strip():
            if finish in _REFUSAL:
                raise self._bad(ErrorKind.REFUSED, f"answer blocked: {finish}")
            raise self._bad(ErrorKind.EMPTY, f"finish reason {finish or 'unknown'}")
        usage = payload.get("usageMetadata")
        u = Usage()
        if isinstance(usage, dict):
            u = Usage(int_or_none(usage.get("promptTokenCount")), int_or_none(usage.get("candidatesTokenCount")),
                      int_or_none(usage.get("totalTokenCount")))
        return AIResponse(text=text, provider_id=self.provider_id, provider_name=self.display_name, model=model,
                          finish_reason=finish, truncated=finish == "MAX_TOKENS", usage=u, latency_s=latency_s)

    def classify_error(self, status: int, headers: dict[str, str], payload: Any) -> AIError:
        detail = short_detail(payload)
        base = self._generic_status(status, headers, payload)
        err = payload.get("error") if isinstance(payload, dict) else None
        gstatus = str(err.get("status", "")) if isinstance(err, dict) else ""
        low = detail.lower()
        kw = dict(provider_id=self.provider_id, http_status=status)
        if status == 400 and ("api key not valid" in low or "api_key_invalid" in low or "api key expired" in low):
            return AIError(ErrorKind.AUTH, detail, **kw)
        if status in (400, 404) and (gstatus == "NOT_FOUND" or looks_like_model_problem(detail)):
            return AIError(ErrorKind.INVALID_MODEL, detail, **kw)
        if status == 400 and gstatus == "FAILED_PRECONDITION":
            return AIError(ErrorKind.PERMISSION, detail, **kw)       # e.g. free tier not available in the region
        if status == 429 or gstatus == "RESOURCE_EXHAUSTED":
            retry = base.retry_after
            if retry is None and isinstance(err, dict):
                for d in err.get("details") or []:
                    delay = d.get("retryDelay") if isinstance(d, dict) else None
                    m = re.fullmatch(r"(\d+(?:\.\d+)?)s", delay or "")
                    if m:
                        retry = float(m.group(1))
            return AIError(ErrorKind.RATE_LIMIT, detail, retry_after=retry, **kw)
        return base

    def connection_request(self) -> tuple[str, bool]:
        return f"{self.base_url}/v1beta/models?pageSize=1", True

    def model_list_request(self) -> tuple[str, bool]:
        return f"{self.base_url}/v1beta/models?pageSize=200", True

    def parse_model_ids(self, payload: Any) -> list[str]:
        out = []
        for m in (payload.get("models") if isinstance(payload, dict) else None) or []:
            if not isinstance(m, dict) or not isinstance(m.get("name"), str):
                continue
            methods = m.get("supportedGenerationMethods")
            if isinstance(methods, list) and "generateContent" not in methods:
                continue
            out.append(m["name"].removeprefix("models/"))
        return out
