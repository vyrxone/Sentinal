"""V5 regression tests for defects found while auditing V4 (see docs/V5_AUDIT.md, section A).

All provider traffic here is mocked (FakeTransport); no live API call is ever made.
"""

import pytest

from ai_fakes import KEY, FakeKeyring, FakeTransport, gemini_ok, openai_ok, resp
from sentinel.ai import context_builder as cb
from sentinel.ai.errors import AIError, ErrorKind
from sentinel.ai.providers import make_provider
from sentinel.ai.providers.base import ProviderConfig


@pytest.fixture
def ai(ctrl):
    svc = ctrl.ai
    svc.secrets._backend = FakeKeyring()
    svc.router.transport = FakeTransport({"generateContent": resp(200, gemini_ok("answer one")),
                                          "api.groq.com": resp(200, openai_ok("groq answer"))})
    for p in ("gemini", "groq"):
        ctrl.settings.set(f"ai_{p}_enabled", True)
        ctrl.settings.set(f"ai_{p}_model", f"{p}-model")
        svc.set_key(p, KEY)
    ctrl.settings.set("ai_fallback_order", "groq")
    return svc


def request(text="hello"):
    return cb.compose_request("chat", text, [])


# ---------------------------------------------------------------- A11: connection test / model list while AI is off
def test_a11_connection_test_and_model_list_make_no_request_while_ai_is_off(ai, ctrl, pump):
    assert ctrl.settings.get("ai_enabled") is False
    t = ai.router.transport
    got = {}
    ai.test_connection("gemini", lambda r: got.update(test=r))
    ai.list_models("gemini", lambda r: got.update(models=r), lambda m: got.update(models_err=m))
    assert pump(2, lambda: "test" in got and ("models" in got or "models_err" in got))
    assert t.count == 0
    assert got["test"].ok is False and got["test"].kind == "disabled"
    assert "switched off" in got["test"].message.lower()


# ---------------------------------------------------------------- A12: Gemini model ID accepted by validation but refused when sending
@pytest.mark.parametrize("model", ["google/gemini-x", "gemini-x:free", "a/b/c"])
def test_a12_gemini_configuration_check_rejects_ids_the_request_builder_refuses(model):
    p = make_provider("gemini")
    problems = p.validate_configuration(ProviderConfig(True, model, 30.0), has_key=True)
    assert problems, "validation must agree with build_request, otherwise the provider is shown as ready but fails"
    with pytest.raises(AIError):
        p.build_request(request(), model)


def test_a12_gemini_accepts_plain_and_prefixed_ids():
    p = make_provider("gemini")
    for model in ("gemini-model-1", "models/gemini-model-1"):
        assert p.validate_configuration(ProviderConfig(True, model, 30.0), has_key=True) == []


def test_a12_openrouter_and_groq_still_accept_slash_ids():
    for pid, model in (("openrouter", "vendor/model-x:free"), ("groq", "vendor/model-x")):
        assert make_provider(pid).validate_configuration(ProviderConfig(True, model, 30.0), True) == []


# ---------------------------------------------------------------- A13: Retry-After in HTTP-date form is valid but was ignored
def test_a13_retry_after_http_date_is_honoured_and_bounded():
    import email.utils
    import time

    from sentinel.ai.transport import HttpResponse, parse_retry_after

    future = email.utils.formatdate(time.time() + 90, usegmt=True)
    secs = parse_retry_after(future)
    assert secs is not None and 80 <= secs <= 91
    assert parse_retry_after(email.utils.formatdate(time.time() - 500, usegmt=True)) == 0.0   # past date: no wait
    assert parse_retry_after("12") == 12.0
    assert parse_retry_after("12.5") == 12.5
    for bad in ("", "abc", "-5", "nan", "inf", "99999999", None):
        assert parse_retry_after(bad) is None
    r = HttpResponse(429, {"retry-after": future}, b"{}")
    assert r.retry_after() is not None
    err = make_provider("groq").classify_error(429, {"retry-after": future}, {"error": {"message": "slow"}})
    assert err.kind == ErrorKind.RATE_LIMIT and err.retry_after and 80 <= err.retry_after <= 91


# ---------------------------------------------------------------- A14: watchdog timeout left the network call running
def test_a14_budget_timeout_cancels_the_in_flight_request_and_frees_the_worker(ai, ctrl, pump):
    ctrl.settings.set("ai_enabled", True)
    t = FakeTransport(default=resp(200, gemini_ok()), delay=30)       # a provider that never answers
    ai.router.transport = t
    original = ai.jobs.submit

    def quick(*a, **kw):                                              # shrink the overall budget so the test is fast
        kw["timeout_s"] = 0.4
        return original(*a, **kw)

    ai.jobs.submit = quick
    out = {}
    ai.send(request(), lambda r: out.update(ok=r), lambda e: out.update(err=e), confirmed=True)
    assert pump(5, lambda: out)
    assert out["err"].kind == ErrorKind.TIMEOUT
    # the socket must have been closed, so the single worker and the router are free again
    assert pump(3, lambda: not ai.router._busy.locked()), "the in-flight request kept running after the timeout"
    assert not ai.busy


# ---------------------------------------------------------------- V5 features (lightweight mode, keyring self-test)
def test_keyring_selftest_passes_with_a_correct_store_and_leaves_nothing_behind():
    from sentinel.ai import keyring_selftest as kst
    kr = FakeKeyring()
    kr.store[("SomeOtherApp", "user")] = "unrelated"
    lines = []
    assert kst.run(kr, lines.append) is True
    assert kr.store == {("SomeOtherApp", "user"): "unrelated"}
    assert any(l.startswith("RESULT: all checks passed") for l in lines)
    assert "PASS  an unrelated entry was not modified" in lines


def test_keyring_selftest_fails_clearly_without_a_secure_store_or_with_a_broken_one():
    from sentinel.ai import keyring_selftest as kst

    lines = []
    assert kst.run(None, lines.append) is False and lines[0].startswith("FAIL  no secure credential store")

    class Broken(FakeKeyring):
        def get_password(self, service, user):      # a store that forgets what it was given
            return None

    lines = []
    kr = Broken()
    assert kst.run(kr, lines.append) is False and kr.store == {}
    assert any(l.startswith("FAIL  read it back") for l in lines)
    joined = "\n".join(lines)
    assert "first-" not in joined and "second-" not in joined and "canary-value" not in joined   # no values printed


def test_keyring_selftest_flag_is_registered():
    from sentinel.app import build_parser
    assert build_parser().parse_args(["--keyring-selftest"]).keyring_selftest is True


def test_lightweight_mode_lowers_rendering_and_sampling_without_changing_user_settings(ctrl):
    s = ctrl.settings
    s.set("animation_fps", 30); s.set("animation_intensity", "high"); s.set("sample_interval_s", 1.0)
    normal = ctrl.quality
    s.set("lightweight_mode", True)
    q = ctrl.quality
    assert q.fps <= 15 and q.particles <= 6 and q.pulses is False and q.fps < normal.fps
    assert ctrl.sampler._interval >= 4.0
    assert s.get("animation_fps") == 30 and s.get("animation_intensity") == "high"   # the user's own values are kept
    s.set("lightweight_mode", False)
    assert ctrl.quality.fps == normal.fps and ctrl.sampler._interval == 1.0


def test_settings_definitions_partition_is_consistent():
    from sentinel.storage.settings_store import DEFINITIONS, V2_END, V3_END, V4_END
    assert V2_END < V3_END < V4_END < len(DEFINITIONS)
    assert DEFINITIONS[V3_END].key == "ai_enabled" and DEFINITIONS[V4_END].key == "ai_proxy_enabled"
    assert DEFINITIONS[-1].key == "lightweight_mode" and len(DEFINITIONS) == V4_END + 5


# ---------------------------------------------------------------- A15: OpenRouter 402 "in-flight budget" is a wait-and-retry limit
def test_a15_openrouter_402_in_flight_budget_is_a_rate_limit_but_other_402_is_not():
    p = make_provider("openrouter")
    temp = {"error": {"code": 402, "message": "budget", "metadata": {"limit_source": "openrouter_in_flight_budget"}}}
    err = p.classify_error(402, {"retry-after": "12"}, temp)
    assert err.kind == ErrorKind.RATE_LIMIT and err.retry_after == 12.0
    assert p.classify_error(402, {}, {"error": {"code": 402, "message": "Insufficient credits"}}).kind == ErrorKind.PERMISSION
    # the same error delivered inside an HTTP 200 body
    with pytest.raises(AIError) as e:
        p.normalize_response(temp, "m", 0.1)
    assert e.value.kind == ErrorKind.RATE_LIMIT
