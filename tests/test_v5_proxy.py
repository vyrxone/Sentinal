"""V5 optional proxy support. MOCK INFRASTRUCTURE: a local TLS server (throw-away test CA) and a local CONNECT
proxy on 127.0.0.1. No real provider, proxy or API key is involved."""

import json
import logging
import ssl
import threading
import time

import pytest

from ai_fakes import KEY, FakeKeyring
from proxy_fakes import OPENSSL, ConnectProxy, TlsProvider, closed_port, make_pki
from sentinel.ai import context_builder as cb
from sentinel.ai.errors import AIError, ErrorKind
from sentinel.ai.providers.gemini import GeminiProvider
from sentinel.ai.proxy import (ProxyConfigError, ProxySettings, basic_auth_header, validate_credentials,
                               validate_host, validate_port)
from sentinel.ai.secrets import SERVICE, SecretStore
from sentinel.ai.transport import CancelHandle, Transport
from sentinel.storage.settings_store import SettingError

pytestmark = pytest.mark.skipif(OPENSSL is None, reason="the openssl command line tool is needed to make test certificates")

PROXY_SECRET = "pr0xy-Pa55word!-unique"


@pytest.fixture(scope="module")
def pki(tmp_path_factory):
    return make_pki(tmp_path_factory.mktemp("pki"))


def trusting(pki):
    ctx = ssl.create_default_context(cafile=pki["ca"])
    return ctx


@pytest.fixture
def server(pki):
    s = TlsProvider(pki["good"])
    yield s
    s.close()


@pytest.fixture
def proxies():
    made = []

    def make(*a, **kw):
        p = ConnectProxy(*a, **kw)
        made.append(p)
        return p

    yield make
    for p in made:
        p.close()


def get(transport, url, **kw):
    return transport.request("GET", url, headers={"Accept": "application/json"}, body=None,
                             timeout=kw.pop("timeout", 5), **kw)


def kind(exc_info) -> ErrorKind:
    return exc_info.value.kind


# ------------------------------------------------------------------ direct connection (default) and TLS checks
def test_direct_connection_with_verified_tls_is_the_default(server, pki):
    t = Transport(ssl_context=trusting(pki))
    r = get(t, server.url + "/ping")
    assert r.status == 200 and len(server.requests) == 1


def test_certificate_signed_by_an_unknown_ca_is_refused_not_ignored(server):
    t = Transport()                                              # system trust store: knows nothing of the test CA
    with pytest.raises(AIError) as e:
        get(t, server.url + "/ping")
    assert kind(e) == ErrorKind.CERTIFICATE and not server.requests
    assert "never turns" in e.value.message


def test_certificate_for_the_wrong_host_name_is_refused(pki):
    s = TlsProvider(pki["wrong"])
    try:
        with pytest.raises(AIError) as e:
            get(Transport(ssl_context=trusting(pki)), s.url + "/ping")
        assert kind(e) == ErrorKind.CERTIFICATE and not s.requests
    finally:
        s.close()


def test_tls_verification_cannot_be_disabled_through_the_transport():
    weak = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    weak.check_hostname = False
    weak.verify_mode = ssl.CERT_NONE
    with pytest.raises(ValueError):
        Transport(ssl_context=weak)
    ctx = Transport()._context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    assert ctx.minimum_version >= ssl.TLSVersion.TLSv1_2


# ------------------------------------------------------------------ proxy success
def test_request_through_a_proxy_reaches_the_server_and_the_proxy_never_sees_the_key(server, pki, proxies):
    p = proxies("ok")
    t = Transport(lambda: ProxySettings("127.0.0.1", p.port), trusting(pki))
    r = t.request("POST", server.url + "/v1beta/models/m:generateContent",
                  headers={"x-goog-api-key": KEY, "Content-Type": "application/json"},
                  body=b'{"secret-body-marker": 1}', timeout=5)
    assert r.status == 200
    got = server.requests[0]
    assert got["headers"]["x-goog-api-key"] == KEY and b"secret-body-marker" in got["body"]
    assert "proxy-authorization" not in {k.lower() for k in got["headers"]}
    assert len(p.connects) == 1 and p.connects[0].startswith(f"CONNECT localhost:{server.port} HTTP/1.1")
    assert KEY.encode() not in bytes(p.relayed) and b"secret-body-marker" not in bytes(p.relayed)   # encrypted
    assert KEY not in p.connects[0]


def test_proxy_credentials_go_only_to_the_proxy(server, pki, proxies):
    p = proxies("auth", "alice", PROXY_SECRET)
    t = Transport(lambda: ProxySettings("127.0.0.1", p.port, "alice", PROXY_SECRET), trusting(pki))
    assert get(t, server.url + "/x", ).status == 200
    assert "Proxy-Authorization: " + basic_auth_header("alice", PROXY_SECRET) in p.connects[0]
    assert all("proxy-authorization" not in k.lower() for k in server.requests[0]["headers"])
    assert PROXY_SECRET.encode() not in bytes(p.relayed)


# ------------------------------------------------------------------ proxy failures
def test_wrong_or_missing_proxy_credentials_are_reported_as_proxy_auth(server, pki, proxies):
    p = proxies("auth", "alice", PROXY_SECRET)
    for settings in (ProxySettings("127.0.0.1", p.port, "alice", "wrong-password"), ProxySettings("127.0.0.1", p.port)):
        with pytest.raises(AIError) as e:
            get(Transport(lambda s=settings: s, trusting(pki)), server.url + "/x")
        assert kind(e) == ErrorKind.PROXY_AUTH and e.value.http_status == 407
    assert not server.requests


def test_proxy_offline_fails_and_never_falls_back_to_a_direct_connection(server, pki):
    t = Transport(lambda: ProxySettings("127.0.0.1", closed_port()), trusting(pki))
    with pytest.raises(AIError) as e:
        get(t, server.url + "/x")
    assert kind(e) == ErrorKind.PROXY and not server.requests


def test_proxy_timeout(server, pki, proxies):
    p = proxies("silent")
    t = Transport(lambda: ProxySettings("127.0.0.1", p.port), trusting(pki))
    t0 = time.monotonic()
    with pytest.raises(AIError) as e:
        get(t, server.url + "/x", timeout=0.7)
    assert kind(e) == ErrorKind.PROXY and time.monotonic() - t0 < 3 and not server.requests


@pytest.mark.parametrize("mode,status", [("status:403", 403), ("status:502", 502), ("status:302", 302)])
def test_proxy_refusals_and_redirects_are_not_followed(server, pki, proxies, mode, status):
    p = proxies(mode)
    t = Transport(lambda: ProxySettings("127.0.0.1", p.port), trusting(pki))
    with pytest.raises(AIError) as e:
        get(t, server.url + "/x")
    assert kind(e) == ErrorKind.PROXY and e.value.http_status == status
    assert len(p.connects) == 1 and not server.requests


def test_data_injected_after_the_proxy_answer_is_refused(server, pki, proxies):
    p = proxies("trailing")
    t = Transport(lambda: ProxySettings("127.0.0.1", p.port), trusting(pki))
    with pytest.raises(AIError) as e:
        get(t, server.url + "/x")
    assert kind(e) == ErrorKind.PROXY and not server.requests


def test_certificate_is_still_verified_through_the_tunnel(pki, proxies):
    s = TlsProvider(pki["wrong"])
    p = proxies("ok")
    try:
        t = Transport(lambda: ProxySettings("127.0.0.1", p.port), trusting(pki))
        with pytest.raises(AIError) as e:
            get(t, s.url + "/x")
        assert kind(e) == ErrorKind.CERTIFICATE and not s.requests
    finally:
        s.close()


def test_provider_redirect_is_not_followed_so_the_key_cannot_reach_another_host(pki, proxies):
    s = TlsProvider(pki["good"], redirect=True)
    p = proxies("ok")
    try:
        t = Transport(lambda: ProxySettings("127.0.0.1", p.port), trusting(pki))
        r = t.request("GET", s.url + "/x", headers={"x-goog-api-key": KEY}, body=None, timeout=5)
        assert r.status == 302 and len(s.requests) == 1 and len(p.connects) == 1
    finally:
        s.close()


def test_cancel_during_the_proxy_stage_returns_quickly(server, pki, proxies):
    p = proxies("silent")
    t = Transport(lambda: ProxySettings("127.0.0.1", p.port), trusting(pki))
    handle = CancelHandle()
    threading.Timer(0.3, handle.cancel).start()
    t0 = time.monotonic()
    with pytest.raises(AIError) as e:
        get(t, server.url + "/x", timeout=20, cancel=handle)
    assert kind(e) == ErrorKind.CANCELLED and time.monotonic() - t0 < 3


def test_proxy_credentials_never_appear_in_errors_logs_or_repr(server, pki, proxies, caplog):
    caplog.set_level(logging.DEBUG)
    p = proxies("auth", "alice", PROXY_SECRET)
    s = ProxySettings("127.0.0.1", p.port, "alice", "not-the-password-xyz")
    assert "not-the-password-xyz" not in repr(s) and "alice" not in repr(s)
    with pytest.raises(AIError) as e:
        get(Transport(lambda: s, trusting(pki)), server.url + "/x")
    blob = str(e.value) + e.value.message + e.value.detail + caplog.text
    assert "not-the-password-xyz" not in blob and PROXY_SECRET not in blob
    assert basic_auth_header("alice", "not-the-password-xyz").split()[1] not in blob


def test_probe_checks_the_tunnel_without_sending_an_http_request(server, pki, proxies):
    p = proxies("ok")
    t = Transport(lambda: ProxySettings("127.0.0.1", p.port), trusting(pki))
    assert t.probe(server.url, 5) == "proxy"
    assert len(p.connects) == 1 and not server.requests                      # handshake only: nothing was sent
    assert Transport(ssl_context=trusting(pki)).probe(server.url, 5) == "direct"
    with pytest.raises(AIError) as e:
        Transport(lambda: ProxySettings("127.0.0.1", closed_port()), trusting(pki)).probe(server.url, 2)
    assert kind(e) == ErrorKind.PROXY


# ------------------------------------------------------------------ validation
@pytest.mark.parametrize("bad", ["", " ", "http://proxy", "proxy:8080", "user@proxy", "pro xy", "proxy/path", "-bad.example",
                                 "a" * 300, "proxy\r\nHost: evil", "bad_underscore.example"])
def test_invalid_proxy_hosts_are_rejected(bad):
    with pytest.raises(ProxyConfigError):
        validate_host(bad)


@pytest.mark.parametrize("good", ["proxy", "proxy.corp.example", "10.0.0.5", "::1", "[::1]", "127.0.0.1"])
def test_valid_proxy_hosts_are_accepted(good):
    assert validate_host(good)


@pytest.mark.parametrize("bad", [0, 65536, -1, "x", "", None, True, 1.5])
def test_invalid_ports_are_rejected(bad):
    with pytest.raises(ProxyConfigError):
        validate_port(bad)


def test_valid_ports_and_credentials():
    assert validate_port(8080) == 8080 and validate_port("3128") == 3128
    assert validate_credentials(" bob ", "p w") == ("bob", "p w")
    for u, pw in (("", "x"), ("a:b", "x"), ("a", ""), ("a", "x\ny"), ("a" * 200, "x"), ("a", "x" * 300)):
        with pytest.raises(ProxyConfigError):
            validate_credentials(u, pw)


def test_proxy_settings_are_validated_and_import_cannot_enable_the_proxy(settings, tmp_path):
    assert settings.get("ai_proxy_enabled") is False and settings.get("ai_proxy_port") == 8080
    with pytest.raises(SettingError):
        settings.set("ai_proxy_host", "http://evil.example")
    with pytest.raises(SettingError):
        settings.set("ai_proxy_port", 70000)
    settings.set("ai_proxy_host", "proxy.corp.example")
    f = tmp_path / "in.json"
    f.write_text(json.dumps({"ai_proxy_enabled": True, "ai_proxy_host": "attacker.example", "ai_proxy_port": 3128}))
    applied, rejected = settings.import_json(f)
    assert "ai_proxy_enabled" in rejected and settings.get("ai_proxy_enabled") is False
    assert settings.get("ai_proxy_host") == "attacker.example"                  # harmless while the proxy stays off
    f.write_text(json.dumps({"ai_proxy_enabled": False}))
    assert settings.import_json(f)[1] == {}


# ------------------------------------------------------------------ secret storage of proxy sign-in
def test_proxy_credentials_use_the_secure_store_only_and_never_touch_unrelated_entries():
    kr = FakeKeyring()
    kr.store[("SomeOtherApp", "proxy-password")] = "unrelated-secret-1"
    kr.store[(SERVICE, "gemini")] = KEY
    before = dict(kr.store)
    s = SecretStore(backend=kr, environ={})
    assert s.proxy_credentials() is None and s.proxy_source() == "none"
    s.set_proxy_credentials("alice", PROXY_SECRET)
    assert s.proxy_credentials() == ("alice", PROXY_SECRET) and s.proxy_source() == "credential_manager"
    s.set_proxy_credentials("alice", "replacement-pass-2")                           # explicit replacement
    assert s.proxy_credentials() == ("alice", "replacement-pass-2")
    assert s.delete_proxy_credentials() is True and s.proxy_credentials() is None   # explicit deletion
    assert kr.store == before                                                       # nothing else was modified


def test_no_plaintext_fallback_for_proxy_credentials_and_env_override():
    from sentinel.ai.errors import SecureStorageUnavailable
    s = SecretStore(backend=None, environ={})
    with pytest.raises(SecureStorageUnavailable):
        s.set_proxy_credentials("alice", PROXY_SECRET)
    env = {"VYRXONE_PROXY_USERNAME": "envuser", "VYRXONE_PROXY_PASSWORD": "env-password-1"}
    s = SecretStore(backend=None, environ=env)
    assert s.proxy_credentials() == ("envuser", "env-password-1") and s.proxy_source() == "environment"


# ------------------------------------------------------------------ service level (real transport, local servers)
@pytest.fixture
def svc(ctrl, server, pki):
    ai = ctrl.ai
    ai.secrets._backend = FakeKeyring()
    ai.router.transport = Transport(ai.proxy_settings, trusting(pki))
    ai.router._providers["gemini"] = GeminiProvider(server.url)
    ctrl.settings.set("ai_gemini_enabled", True)
    ctrl.settings.set("ai_gemini_model", "mock-1")
    ai.set_key("gemini", KEY)
    ctrl.settings.set("ai_enabled", True)
    return ai


def run(svc, pump, text="hello"):
    out = {}
    svc.send(cb.compose_request("chat", text, []), lambda r: out.update(ok=r), lambda e: out.update(err=e),
             confirmed=True)
    assert pump(10, lambda: out)
    return out


def test_full_request_through_the_proxy_matches_the_previewed_payload(svc, ctrl, server, proxies, pump):
    p = proxies("auth", "alice", PROXY_SECRET)
    svc.secrets.set_proxy_credentials("alice", PROXY_SECRET)
    for key, val in (("ai_proxy_host", "127.0.0.1"), ("ai_proxy_port", p.port), ("ai_proxy_auth", True),
                     ("ai_proxy_enabled", True)):
        ctrl.settings.set(key, val)
    req = cb.compose_request("chat", "what is baseline?", [])
    preview = GeminiProvider(server.url).build_request(req, "mock-1")[1]
    out = {}
    svc.send(req, lambda r: out.update(ok=r), lambda e: out.update(err=e), confirmed=True)
    assert pump(10, lambda: out) and "ok" in out and out["ok"].response.text == "mock provider answer"
    assert json.loads(server.requests[0]["body"]) == preview and len(p.connects) == 1


def test_enabled_proxy_without_stored_credentials_fails_closed_and_sends_nothing(svc, ctrl, server, proxies, pump):
    p = proxies("auth", "alice", PROXY_SECRET)
    for key, val in (("ai_proxy_host", "127.0.0.1"), ("ai_proxy_port", p.port), ("ai_proxy_auth", True),
                     ("ai_proxy_enabled", True)):
        ctrl.settings.set(key, val)
    out = run(svc, pump)
    assert out["err"].kind == ErrorKind.PROXY_AUTH and not p.connects and not server.requests


def test_dead_proxy_never_means_a_direct_request_and_the_app_recovers(svc, ctrl, server, proxies, pump):
    for key, val in (("ai_proxy_host", "127.0.0.1"), ("ai_proxy_port", closed_port()), ("ai_proxy_enabled", True)):
        ctrl.settings.set(key, val)
    out = run(svc, pump)
    assert out["err"].kind == ErrorKind.PROXY and not server.requests and not svc.busy
    ctrl.settings.set("ai_proxy_enabled", False)                                    # switching it off: direct works
    assert run(svc, pump)["ok"].response.text == "mock provider answer"


def test_test_proxy_button_logic(svc, ctrl, server, proxies, pump):
    p = proxies("ok")
    got = []
    svc.test_proxy(got.append)                                                      # proxy not enabled
    assert pump(5, lambda: got) and got[0].ok is False and got[0].kind == "not_configured"
    got.clear()
    for key, val in (("ai_proxy_host", "127.0.0.1"), ("ai_proxy_port", p.port), ("ai_proxy_enabled", True)):
        ctrl.settings.set(key, val)
    svc.test_proxy(got.append)
    assert pump(8, lambda: got) and got[0].ok is True and not server.requests
    got.clear()
    ctrl.settings.set("ai_enabled", False)                                          # AI off: no connection at all
    n = len(p.connects)
    svc.test_proxy(got.append)
    assert pump(3, lambda: got) and got[0].kind == "disabled" and len(p.connects) == n


def test_reset_configuration_can_remove_proxy_credentials(svc, ctrl):
    svc.secrets.set_proxy_credentials("alice", PROXY_SECRET)
    ctrl.settings.set("ai_proxy_host", "proxy.example")
    svc.reset_configuration(delete_keys=False)
    assert svc.secrets.proxy_credentials() is not None and ctrl.settings.get("ai_proxy_host") == ""
    svc.reset_configuration(delete_keys=True)
    assert svc.secrets.proxy_credentials() is None
