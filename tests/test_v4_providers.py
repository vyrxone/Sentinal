"""Provider unit tests with mocked HTTP: request building, normalisation and error classification."""

import json

import pytest

from ai_fakes import KEY, FakeTransport, gemini_ok, openai_ok, resp
from sentinel.ai.errors import AIError, ErrorKind
from sentinel.ai.models import AIRequest, ChatMessage
from sentinel.ai.providers import make_provider
from sentinel.ai.providers.base import ProviderConfig, valid_model_id

REQ = AIRequest("SYSTEM TEXT", (ChatMessage("user", "hi"), ChatMessage("assistant", "yo"), ChatMessage("user", "q")), 321, 0.3)


def gen(pid, transport, model="some-model"):
    return make_provider(pid).generate(REQ, ProviderConfig(True, model, 10), KEY, transport)


# ------------------------------------------------------------------ Gemini
def test_gemini_request_shape_and_key_in_header_only():
    p = make_provider("gemini")
    url, body = p.build_request(REQ, "models/some-model")
    assert url == "https://generativelanguage.googleapis.com/v1beta/models/some-model:generateContent"
    assert body["systemInstruction"] == {"parts": [{"text": "SYSTEM TEXT"}]}
    assert [c["role"] for c in body["contents"]] == ["user", "model", "user"]
    assert body["generationConfig"] == {"maxOutputTokens": 321, "temperature": 0.3}
    assert p.auth_headers(KEY) == {"x-goog-api-key": KEY}
    assert KEY not in url and KEY not in json.dumps(body)


def test_gemini_success_normalised_with_usage():
    t = FakeTransport({"generateContent": resp(200, gemini_ok())})
    r = gen("gemini", t)
    assert (r.text, r.provider_id, r.model, r.truncated) == ("Gemini says hello", "gemini", "some-model", False)
    assert (r.usage.prompt_tokens, r.usage.completion_tokens, r.usage.total_tokens) == (11, 7, 18)
    assert t.calls[0]["headers"]["x-goog-api-key"] == KEY and t.calls[0]["method"] == "POST"


def test_gemini_missing_usage_stays_unknown_not_zero():
    r = gen("gemini", FakeTransport({"generateContent": resp(200, gemini_ok(usage=False))}))
    assert r.usage.known is False and r.usage.total_tokens is None


def test_gemini_truncated_answer_is_flagged():
    r = gen("gemini", FakeTransport({"generateContent": resp(200, gemini_ok(finish="MAX_TOKENS"))}))
    assert r.truncated and r.finish_reason == "MAX_TOKENS"


@pytest.mark.parametrize("payload,kind", [
    ({"candidates": []}, ErrorKind.EMPTY),
    ({}, ErrorKind.EMPTY),
    ({"promptFeedback": {"blockReason": "SAFETY"}}, ErrorKind.REFUSED),
    ({"candidates": [{"finishReason": "SAFETY", "content": {"parts": []}}]}, ErrorKind.REFUSED),
    ({"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "   "}]}}]}, ErrorKind.EMPTY),
    ({"candidates": ["bad"]}, ErrorKind.MALFORMED),
    ([1, 2], ErrorKind.MALFORMED),
])
def test_gemini_bad_responses(payload, kind):
    with pytest.raises(AIError) as e:
        gen("gemini", FakeTransport({"generateContent": resp(200, payload)}))
    assert e.value.kind == kind


def test_gemini_thought_parts_are_not_shown():
    p = {"candidates": [{"content": {"parts": [{"text": "hidden", "thought": True}, {"text": "visible"}]},
                         "finishReason": "STOP"}]}
    assert gen("gemini", FakeTransport({"generateContent": resp(200, p)})).text == "visible"


@pytest.mark.parametrize("status,payload,kind", [
    (400, {"error": {"code": 400, "message": "API key not valid. Please pass a valid API key.", "status": "INVALID_ARGUMENT"}}, ErrorKind.AUTH),
    (401, {"error": {"message": "unauthenticated"}}, ErrorKind.AUTH),
    (403, {"error": {"message": "denied", "status": "PERMISSION_DENIED"}}, ErrorKind.PERMISSION),
    (404, {"error": {"message": "models/x is not found for API version v1beta", "status": "NOT_FOUND"}}, ErrorKind.INVALID_MODEL),
    (400, {"error": {"message": "bad field", "status": "INVALID_ARGUMENT"}}, ErrorKind.BAD_REQUEST),
    (429, {"error": {"message": "quota", "status": "RESOURCE_EXHAUSTED"}}, ErrorKind.RATE_LIMIT),
    (503, {"error": {"message": "overloaded", "status": "UNAVAILABLE"}}, ErrorKind.SERVER),
    (504, {}, ErrorKind.TIMEOUT),
    (413, {}, ErrorKind.TOO_LARGE),
])
def test_gemini_error_classification(status, payload, kind):
    with pytest.raises(AIError) as e:
        gen("gemini", FakeTransport({"generateContent": resp(status, payload)}))
    assert e.value.kind == kind and e.value.http_status == status


def test_gemini_retry_delay_from_error_details_and_header():
    body = {"error": {"status": "RESOURCE_EXHAUSTED", "message": "q",
                      "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "12s"}]}}
    with pytest.raises(AIError) as e:
        gen("gemini", FakeTransport({"generateContent": resp(429, body)}))
    assert e.value.retry_after == 12.0
    with pytest.raises(AIError) as e:
        gen("gemini", FakeTransport({"generateContent": resp(429, {}, {"Retry-After": "7"})}))
    assert e.value.retry_after == 7.0


def test_gemini_model_listing_filters_generate_content_only():
    p = make_provider("gemini")
    ids = p.parse_model_ids({"models": [
        {"name": "models/a", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/emb", "supportedGenerationMethods": ["embedContent"]}]})
    assert ids == ["a"]


@pytest.mark.parametrize("bad", ["", "../etc", "a b", "x/y?z=1", "a" * 200, "..", "m\nodel"])
def test_model_ids_are_validated(bad):
    assert not valid_model_id(bad)
    with pytest.raises(AIError) as e:
        gen("gemini", FakeTransport(default=resp(200, gemini_ok())), model=bad)
    assert e.value.kind == ErrorKind.NOT_CONFIGURED


def test_gemini_path_injection_rejected_even_when_regex_allows_slash():
    with pytest.raises(AIError):
        make_provider("gemini").build_request(REQ, "vendor/model")       # OpenRouter-style IDs are not valid in a path


# ------------------------------------------------------------------ Groq
def test_groq_request_and_success():
    t = FakeTransport({"/chat/completions": resp(200, openai_ok())})
    r = gen("groq", t, "llama-x")
    call = t.calls[0]
    assert call["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert call["headers"]["Authorization"] == f"Bearer {KEY}"
    b = call["json"]
    assert b["model"] == "llama-x" and b["max_completion_tokens"] == 321 and "max_tokens" not in b
    assert b["messages"][0] == {"role": "system", "content": "SYSTEM TEXT"} and b["stream"] is False
    assert r.provider_id == "groq" and r.text == "Hello from the model" and r.usage.total_tokens == 17
    assert KEY not in json.dumps(b) and KEY not in call["url"]


@pytest.mark.parametrize("status,payload,hdr,kind", [
    (401, {"error": {"message": "Invalid API Key", "type": "invalid_request_error", "code": "invalid_api_key"}}, {}, ErrorKind.AUTH),
    (404, {"error": {"message": "The model `x` does not exist", "code": "model_not_found"}}, {}, ErrorKind.INVALID_MODEL),
    (400, {"error": {"message": "The model `x` has been decommissioned", "code": "model_decommissioned"}}, {}, ErrorKind.INVALID_MODEL),
    (429, {"error": {"message": "Rate limit reached", "code": "rate_limit_exceeded"}}, {"retry-after": "7"}, ErrorKind.RATE_LIMIT),
    (413, {"error": {"message": "too large"}}, {}, ErrorKind.TOO_LARGE),
    (500, {"error": {"message": "oops"}}, {}, ErrorKind.SERVER),
    (503, {}, {}, ErrorKind.SERVER),
    (400, {"error": {"message": "bad param"}}, {}, ErrorKind.BAD_REQUEST),
])
def test_groq_error_classification(status, payload, hdr, kind):
    with pytest.raises(AIError) as e:
        gen("groq", FakeTransport({"/chat/completions": resp(status, payload, hdr)}))
    assert e.value.kind == kind
    if kind == ErrorKind.RATE_LIMIT:
        assert e.value.retry_after == 7.0 and "7" in e.value.message


@pytest.mark.parametrize("payload,kind", [
    ({"choices": []}, ErrorKind.EMPTY),
    ({"choices": [{"message": {"content": ""}, "finish_reason": "stop"}]}, ErrorKind.EMPTY),
    ({"choices": [{"message": {"content": None}, "finish_reason": "length"}]}, ErrorKind.EMPTY),
    ({"choices": [{"message": {"content": ["x"]}, "finish_reason": "stop"}]}, ErrorKind.MALFORMED),
    ({"choices": [{"message": "nope"}]}, ErrorKind.MALFORMED),
    ({"choices": ["x"]}, ErrorKind.MALFORMED),
    ({"choices": [{"message": {"content": "", "refusal": "no"}, "finish_reason": "stop"}]}, ErrorKind.REFUSED),
    ({"choices": [{"message": {"content": ""}, "finish_reason": "content_filter"}]}, ErrorKind.REFUSED),
])
def test_openai_style_bad_responses(payload, kind):
    with pytest.raises(AIError) as e:
        gen("groq", FakeTransport({"/chat/completions": resp(200, payload)}))
    assert e.value.kind == kind


def test_groq_truncation_flag_and_non_json_body():
    r = gen("groq", FakeTransport({"/chat/completions": resp(200, openai_ok(finish="length"))}))
    assert r.truncated
    with pytest.raises(AIError) as e:
        gen("groq", FakeTransport({"/chat/completions": resp(200, raw=b"<html>not json</html>")}))
    assert e.value.kind == ErrorKind.MALFORMED


# ------------------------------------------------------------------ OpenRouter
def test_openrouter_request_success_reports_actual_model():
    t = FakeTransport({"/chat/completions": resp(200, openai_ok(model="vendor/actual-model:free"))})
    r = gen("openrouter", t, "vendor/asked-model:free")
    call = t.calls[0]
    assert call["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert call["json"]["max_tokens"] == 321 and "max_completion_tokens" not in call["json"]
    assert r.model == "vendor/actual-model:free" and r.provider_name == "OpenRouter"


def test_openrouter_error_inside_http_200():
    body = {"error": {"code": 429, "message": "Provider returned error"}}
    with pytest.raises(AIError) as e:
        gen("openrouter", FakeTransport({"/chat/completions": resp(200, body)}))
    assert e.value.kind == ErrorKind.RATE_LIMIT
    partial = {"choices": [{"message": {"content": "part"}, "finish_reason": "error",
                            "error": {"code": 502, "message": "upstream died"}}]}
    with pytest.raises(AIError) as e:
        gen("openrouter", FakeTransport({"/chat/completions": resp(200, partial)}))
    assert e.value.kind == ErrorKind.SERVER
    with pytest.raises(AIError) as e:
        gen("openrouter", FakeTransport({"/chat/completions": resp(200, {"choices": [
            {"message": {"content": ""}, "finish_reason": "error"}]})}))
    assert e.value.kind == ErrorKind.SERVER


@pytest.mark.parametrize("status,payload,hdr,kind", [
    (401, {"error": {"code": 401, "message": "No auth credentials found"}}, {}, ErrorKind.AUTH),
    (402, {"error": {"code": 402, "message": "Insufficient credits"}}, {}, ErrorKind.PERMISSION),
    (403, {"error": {"code": 403, "message": "moderation"}}, {}, ErrorKind.PERMISSION),
    (400, {"error": {"code": 400, "message": "x is not a valid model ID"}}, {}, ErrorKind.INVALID_MODEL),
    (404, {"error": {"code": 404, "message": "No endpoints found for x"}}, {}, ErrorKind.INVALID_MODEL),
    (408, {}, {}, ErrorKind.TIMEOUT),
    (429, {"error": {"code": 429, "message": "slow down"}}, {"Retry-After": "3"}, ErrorKind.RATE_LIMIT),
    (502, {"error": {"code": 502, "message": "model down"}}, {}, ErrorKind.SERVER),
    (503, {"error": {"code": 503, "message": "no provider"}}, {}, ErrorKind.SERVER),
])
def test_openrouter_error_classification(status, payload, hdr, kind):
    with pytest.raises(AIError) as e:
        gen("openrouter", FakeTransport({"/chat/completions": resp(status, payload, hdr)}))
    assert e.value.kind == kind


# ------------------------------------------------------------------ connection tests and model lists
def test_connection_tests_send_no_user_data_and_use_read_only_endpoints():
    for pid, frag in (("gemini", "/v1beta/models"), ("groq", "/models"), ("openrouter", "/key")):
        t = FakeTransport(default=resp(200, {"data": [], "models": []}))
        r = make_provider(pid).test_connection(KEY, 5, t)
        assert r.ok and t.calls[0]["method"] == "GET" and t.calls[0]["body"] is None and frag in t.calls[0]["url"]


def test_connection_test_reports_bad_key_and_missing_key():
    t = FakeTransport(default=resp(401, {"error": {"message": "bad"}}))
    r = make_provider("groq").test_connection(KEY, 5, t)
    assert not r.ok and r.kind == "auth"
    t2 = FakeTransport()
    r = make_provider("groq").test_connection(None, 5, t2)
    assert not r.ok and r.kind == "not_configured" and t2.count == 0


def test_model_options_come_only_from_the_provider_listing():
    t = FakeTransport(default=resp(200, {"data": [{"id": "b"}, {"id": "a"}, {"nope": 1}]}))
    assert make_provider("groq").get_model_options(KEY, 5, t) == ["a", "b"]
    t = FakeTransport(default=resp(401, {}))
    with pytest.raises(AIError):
        make_provider("groq").get_model_options(KEY, 5, t)


def test_provider_validate_configuration_messages():
    p = make_provider("groq")
    assert p.validate_configuration(ProviderConfig(True, "m", 30), True) == []
    msgs = p.validate_configuration(ProviderConfig(True, "", 1), False)
    assert len(msgs) == 3
