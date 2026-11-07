"""AIService (background worker, consent gate, cancellation, history), the real HTTP transport against a local server,
offline behaviour and the v5 migration."""

import json
import socket
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ai_fakes import KEY, FakeKeyring, FakeTransport, gemini_ok, openai_ok, resp
from sentinel.ai import context_builder as cb
from sentinel.ai.errors import AIError, ErrorKind
from sentinel.ai.models import AIRequest, ChatMessage
from sentinel.ai.providers import make_provider
from sentinel.ai.providers.base import ProviderConfig
from sentinel.ai.secrets import SecretStore
from sentinel.ai.transport import CancelHandle, Transport
from sentinel.storage import migrations
from sentinel.storage.database import Database, DatabaseError
from sentinel.storage.events_store import EventStore
from test_v3_storage import make_v2_db, tables
from test_v2_storage import make_v1_db


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


def run(svc, pump, req=None, **kw):
    out = {}
    svc.send(req or request(), lambda r: out.update(ok=r), lambda e: out.update(err=e), confirmed=True, **kw)
    assert pump(8, lambda: out)
    return out


# ------------------------------------------------------------------ gate: disabled, unconfirmed
def test_ai_is_off_by_default_and_disabled_means_zero_network(ctrl):
    assert ctrl.settings.get("ai_enabled") is False
    t = FakeTransport(); ctrl.ai.router.transport = t
    with pytest.raises(AIError) as e:
        ctrl.ai.send(request(), lambda r: None, lambda e: None, confirmed=True)
    assert e.value.kind == ErrorKind.DISABLED and t.count == 0 and not ctrl.ai.busy


def test_unconfirmed_request_is_never_sent(ai, ctrl):
    ctrl.settings.set("ai_enabled", True)
    t = ai.router.transport
    with pytest.raises(AIError) as e:
        ai.send(request(), lambda r: None, lambda e: None, confirmed=False)
    assert e.value.kind == ErrorKind.CONSENT and t.count == 0


def test_no_provider_configured_fails_without_network(ctrl):
    ctrl.settings.set("ai_enabled", True)
    t = FakeTransport(); ctrl.ai.router.transport = t
    with pytest.raises(AIError) as e:
        ctrl.ai.send(request(), lambda r: None, lambda e: None, confirmed=True)
    assert e.value.kind == ErrorKind.NOT_CONFIGURED and t.count == 0


# ------------------------------------------------------------------ success, fallback, failure
def test_success_runs_off_the_ui_thread_and_exact_preview_equals_wire_body(ai, ctrl, pump):
    ctrl.settings.set("ai_enabled", True)
    seen = {}
    real = ai.router.transport.request
    def spy(*a, **k):
        seen["thread"] = threading.current_thread()
        return real(*a, **k)
    ai.router.transport.request = spy
    items = ai.builder().events([])
    req = cb.compose_request("chat", "what is Sentinel?", items)
    _, preview_body = make_provider("gemini").build_request(req, "gemini-model")   # what the preview dialog shows
    out = run(ai, pump, req)
    assert out["ok"].response.text == "answer one"
    assert seen["thread"] is not threading.main_thread()
    assert ai.router.transport.calls[-1]["json"] == preview_body


def test_fallback_is_reported_and_recorded(ai, ctrl, pump):
    ctrl.settings.set("ai_enabled", True)
    ai.router.transport.script["generateContent"] = [resp(503, {})]
    out = run(ai, pump)
    assert out["ok"].response.provider_id == "groq" and "gemini (server)" in out["ok"].fallback_note()


def test_all_failed_returns_error_and_ui_thread_callback(ai, ctrl, pump):
    ctrl.settings.set("ai_enabled", True)
    ai.router.transport.script["generateContent"] = [resp(503, {})]
    ai.router.transport.script["api.groq.com"] = [resp(500, {})]
    out = run(ai, pump)
    assert out["err"].kind == ErrorKind.SERVER and ai.last_error is out["err"] and not ai.busy


def test_ui_stays_responsive_during_a_slow_request(ai, ctrl, pump):
    from PySide6.QtCore import QTimer
    ctrl.settings.set("ai_enabled", True)
    ai.router.transport.delay = 0.8
    gaps, last = [], [time.monotonic()]
    def tick():
        now = time.monotonic(); gaps.append(now - last[0]); last[0] = now
    timer = QTimer(); timer.setInterval(10); timer.timeout.connect(tick); timer.start()
    run(ai, pump)
    timer.stop()
    assert len(gaps) > 20 and max(gaps) < 0.25, max(gaps)


def test_duplicate_and_queue_are_bounded(ai, ctrl, pump):
    ctrl.settings.set("ai_enabled", True)
    ai.router.transport.delay = 0.4
    out = {}
    ai.send(request(), lambda r: out.update(ok=r), lambda e: out.update(err=e), confirmed=True)
    with pytest.raises(AIError) as e:
        ai.send(request(), lambda r: None, lambda e: None, confirmed=True)
    assert e.value.kind == ErrorKind.BUSY
    snap = ai.status_snapshot()
    assert snap["workers"] == 1 and snap["queue_depth"] <= 2 and snap["active"] == 1
    assert pump(5, lambda: out) and ai.status_snapshot()["active"] == 0


def test_flood_of_connection_tests_is_bounded(ai, ctrl, pump):
    ctrl.settings.set("ai_enabled", True)      # V5: connection tests make no request while AI is off
    ai.router.transport = FakeTransport(default=resp(200, {"models": []}), delay=0.3)
    done = []
    for i in range(10):
        ai.test_connection("gemini" if i % 2 else "groq", done.append)
    assert ai.jobs.pending_count <= 2 and ai.jobs.counts["rejected"] + ai.jobs.counts["coalesced"] >= 5
    pump(3, lambda: ai.jobs.active_count == 0)


def test_cancel_stops_the_request_and_discards_a_late_answer(ai, ctrl, pump):
    ctrl.settings.set("ai_enabled", True)
    ai.router.transport.delay = 5
    out = {}
    ai.send(request(), lambda r: out.update(ok=r), lambda e: out.update(err=e), confirmed=True)
    time.sleep(0.2); t0 = time.monotonic()
    ai.cancel()
    assert pump(3, lambda: out) and "ok" not in out and out["err"].kind == ErrorKind.CANCELLED
    assert time.monotonic() - t0 < 2.5 and not ai.busy
    ai.router.transport.delay = 0
    assert run(ai, pump)["ok"].response.text            # a new request works right after


def test_switching_ai_off_cancels_a_running_request(ai, ctrl, pump):
    ctrl.settings.set("ai_enabled", True)
    ai.router.transport.delay = 5
    out = {}
    ai.send(request(), lambda r: out.update(ok=r), lambda e: out.update(err=e), confirmed=True)
    time.sleep(0.2); ctrl.settings.set("ai_enabled", False)
    assert pump(3, lambda: out) and out["err"].kind == ErrorKind.CANCELLED


def test_ui_page_manual_override_provider(ai, ctrl, pump):
    ctrl.settings.set("ai_enabled", True)
    assert run(ai, pump, override="groq")["ok"].response.provider_id == "groq"


# ------------------------------------------------------------------ conversations
def test_conversation_is_memory_only_unless_history_is_enabled(ai, ctrl, pump):
    ctrl.settings.set("ai_enabled", True)
    res = run(ai, pump)["ok"]
    ai.record_exchange("hello", "SENT hello", res, set())
    assert len(ai.conversation.entries) == 2 and ai.store.count() == 0
    ctrl.settings.set("ai_history_enabled", True)
    ai.record_exchange("again", "SENT again", res, {"event:1"})
    assert ai.store.count() == 1 and len(ai.store.messages(ai.conversation.id)) == 2


def test_history_persist_reload_delete_and_clear(ai, ctrl, pump):
    ctrl.settings.set("ai_enabled", True); ctrl.settings.set("ai_history_enabled", True)
    res = run(ai, pump)["ok"]
    ai.record_exchange("q1", "SENT q1", res, set())
    cid = ai.conversation.id
    ai.new_conversation()
    assert not ai.conversation.entries and ai.load_conversation(cid)
    assert [e.role for e in ai.conversation.entries] == ["user", "assistant"] and ai.conversation.entries[1].provider == "gemini"
    assert ai.store.delete(cid) and ai.store.count() == 0
    ai.record_exchange("q2", "SENT q2", res, set()); ai.new_conversation(); ai.record_exchange("q3", "SENT q3", res, set())
    assert ai.store.count() == 2 and ai.clear_history() == 2 and ai.store.count() == 0 and not ai.conversation.entries


def test_history_sent_to_the_provider_is_limited_and_starts_with_a_user_turn(ai, ctrl, pump):
    ctrl.settings.set("ai_enabled", True); ctrl.settings.set("ai_history_messages", 3)
    res = run(ai, pump)["ok"]
    for i in range(5):
        ai.record_exchange(f"q{i}", f"SENT {i}", res, set())
    h = ai.history_for_request()
    assert len(h) <= 3 and h[0].role == "user"
    ctrl.settings.set("ai_history_messages", 0)
    assert ai.history_for_request() == []


def test_review_required_default_and_new_context_only_mode(ai, ctrl, pump):
    ctrl.settings.set("ai_enabled", True)
    assert ai.review_required(set()) is True
    ctrl.settings.set("ai_consent_mode", "new_context_only")
    assert ai.review_required({"event:1"}) is True                    # first request of a conversation: always reviewed
    res = run(ai, pump)["ok"]
    ai.record_exchange("q", "SENT", res, {"event:1"})
    assert ai.review_required({"event:1"}) is False and ai.review_required({"event:1", "event:2"}) is True
    ai.new_conversation()
    assert ai.review_required({"event:1"}) is True                    # approval does not carry into a new conversation


def test_conversation_store_limits_and_cascade(db):
    from sentinel.ai.conversation_store import MAX_CONVERSATIONS, MAX_MESSAGES, ConversationStore
    s = ConversationStore(db)
    first = s.create("first")
    for i in range(MAX_MESSAGES + 20):
        s.add_message(first, "user", f"m{i}")
    assert len(s.messages(first)) == MAX_MESSAGES
    for i in range(MAX_CONVERSATIONS + 5):
        s.create(f"c{i}")
    assert s.count() == MAX_CONVERSATIONS
    assert db.query_one("SELECT COUNT(*) FROM ai_messages WHERE conversation_id NOT IN (SELECT id FROM ai_conversations)")[0] == 0
    with pytest.raises(ValueError):
        s.add_message(s.list()[0].id, "system", "x")


def test_reset_configuration_restores_defaults_and_optionally_removes_keys(ai, ctrl):
    ctrl.settings.set("ai_enabled", True); ctrl.settings.set("ai_max_response", 999)
    ai.reset_configuration(delete_keys=False)
    assert ctrl.settings.get("ai_enabled") is False and ctrl.settings.get("ai_max_response") == 700 and ai.secrets.has_key("gemini")
    ai.reset_configuration(delete_keys=True)
    assert not ai.secrets.has_key("gemini") and not ai.secrets.has_key("groq")


# ------------------------------------------------------------------ settings validation and export
def test_ai_settings_are_validated_and_import_cannot_switch_ai_on(settings, tmp_path):
    from sentinel.storage.settings_store import SettingError
    for key, bad in (("ai_max_response", 10), ("ai_max_response", 99999), ("ai_gemini_timeout", 1), ("ai_primary", "openai"),
                     ("ai_consent_mode", "never"), ("ai_enabled", "yes"), ("ai_max_requests_hour", 0)):
        with pytest.raises(SettingError):
            settings.set(key, bad)
    f = tmp_path / "s.json"
    f.write_text(json.dumps({"ai_enabled": True, "ai_gemini_model": "m", "ai_groq_api_key": "x"}))
    applied, rejected = settings.import_json(f)
    assert settings.get("ai_enabled") is False and "ai_enabled" in rejected and applied == ["ai_gemini_model"]
    assert "ai_groq_api_key" in rejected
    f.write_text(json.dumps({"ai_enabled": False})); assert settings.import_json(f)[0] == ["ai_enabled"]


def test_settings_load_report_for_a_v3_database_lists_missing_ai_settings(db):
    from sentinel.storage.settings_store import SettingsStore
    assert "ai_enabled" in SettingsStore(db).load_report["missing_v4"]


# ------------------------------------------------------------------ the real transport against a local server
class H(BaseHTTPRequestHandler):
    mode = "ok"
    seen: list = []

    def log_message(self, *a): pass

    def do_POST(self): self._go()
    def do_GET(self): self._go()

    def _go(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        H.seen.append((self.command, self.path, dict(self.headers), body))
        if H.mode == "slow":
            time.sleep(3)
        if H.mode == "redirect":
            self.send_response(302); self.send_header("Location", "http://127.0.0.1:1/evil"); self.end_headers(); return
        if H.mode == "429":
            self.send_response(429); self.send_header("Retry-After", "9"); self.send_header("Content-Type", "application/json")
            self.end_headers(); self.wfile.write(b'{"error":{"message":"slow","code":"rate_limit_exceeded"}}'); return
        data = b"x" * (3 * 1024 * 1024) if H.mode == "huge" else json.dumps(openai_ok("real http answer")).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)


@pytest.fixture
def server():
    H.seen = []; H.mode = "ok"
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown(); srv.server_close()


def groq_at(base):
    return make_provider("groq", base + "/openai/v1")


REQ = AIRequest("sys", (ChatMessage("user", "q"),), 100)


def test_real_transport_roundtrip_headers_and_body(server):
    r = groq_at(server).generate(REQ, ProviderConfig(True, "m", 5), KEY, Transport())
    assert r.text == "real http answer"
    cmd, path, headers, body = H.seen[0]
    assert cmd == "POST" and path == "/openai/v1/chat/completions" and headers["Authorization"] == f"Bearer {KEY}"
    assert json.loads(body)["messages"][1]["content"] == "q" and KEY.encode() not in body and KEY not in path


def test_real_transport_rate_limit_and_retry_after(server):
    H.mode = "429"
    with pytest.raises(AIError) as e:
        groq_at(server).generate(REQ, ProviderConfig(True, "m", 5), KEY, Transport())
    assert e.value.kind == ErrorKind.RATE_LIMIT and e.value.retry_after == 9.0


def test_real_transport_timeout(server):
    H.mode = "slow"
    t0 = time.monotonic()
    with pytest.raises(AIError) as e:
        groq_at(server).generate(REQ, ProviderConfig(True, "m", 5), KEY, Transport()) if False else \
            Transport().request("GET", server + "/x", headers={}, body=None, timeout=0.4)
    assert e.value.kind == ErrorKind.TIMEOUT and time.monotonic() - t0 < 2


def test_real_transport_cancel_closes_the_connection_quickly(server):
    H.mode = "slow"
    h = CancelHandle(); threading.Timer(0.3, h.cancel).start()
    t0 = time.monotonic()
    with pytest.raises(AIError) as e:
        Transport().request("GET", server + "/x", headers={}, body=None, timeout=10, cancel=h)
    assert e.value.kind == ErrorKind.CANCELLED and time.monotonic() - t0 < 2


def test_redirects_are_not_followed_and_oversized_bodies_are_refused(server):
    H.mode = "redirect"
    r = Transport().request("GET", server + "/x", headers={}, body=None, timeout=5)
    assert r.status == 302 and len(H.seen) == 1
    H.mode = "huge"
    with pytest.raises(AIError) as e:
        Transport().request("GET", server + "/x", headers={}, body=None, timeout=5)
    assert e.value.kind == ErrorKind.MALFORMED


def test_https_only_except_loopback_and_connection_failure_is_a_safe_network_error():
    with pytest.raises(AIError) as e:
        Transport().request("GET", "http://example.com/x", headers={}, body=None, timeout=1)
    assert e.value.kind == ErrorKind.BAD_REQUEST
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    with pytest.raises(AIError) as e:
        Transport().request("GET", f"http://127.0.0.1:{port}/x", headers={"Authorization": f"Bearer {KEY}"}, body=None, timeout=1)
    assert e.value.kind == ErrorKind.NETWORK and KEY not in str(e.value) and "127.0.0.1" not in str(e.value)


# ------------------------------------------------------------------ offline: the app never needs the network
def test_app_is_fully_usable_offline_and_never_touches_the_network_by_default(ctrl, pump, monkeypatch):
    def boom(*a, **k): raise AssertionError("network access attempted")
    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    ctrl.events.add("security", "baseline_missing", "gone", severity="warning", path="/x")
    ctrl.run_analysis(); pump(0.5)
    assert ctrl.ai.status_snapshot()["enabled"] is False and ctrl.ai.usage.session_requests == 0
    assert ctrl.events.count() >= 1


# ------------------------------------------------------------------ schema v5 migration
V4_TABLES = {"ai_conversations", "ai_messages"}


def make_v3_db(path):
    make_v2_db(path)
    conn = sqlite3.connect(path)
    for v, fn in migrations.MIGRATIONS[:4]:
        if v > 3:
            fn(conn); conn.execute(f"PRAGMA user_version = {v}")
    conn.execute("INSERT INTO findings(finding_key,rule_id,rule_version,severity,category,title,explanation,confidence,"
                 "next_step,first_ts,last_ts,status_ts) VALUES('k','FILE-002',1,'medium','integrity','T','E','c','n',1,2,2)")
    conn.commit(); conn.close()


def test_v3_database_upgrades_to_v5_preserving_data_with_one_backup(tmp_path):
    path = tmp_path / "v3.db"
    make_v3_db(path)
    db = Database(path)
    assert db.user_version == 5 and V4_TABLES <= tables(db)
    assert db.query_one("SELECT COUNT(*) FROM findings")[0] == 1 and EventStore(db).count() == 1
    assert db.query_one("SELECT value FROM settings WHERE key='animation_fps'")[0] == "30"
    db.close()
    assert len(list(tmp_path.glob("v3.db.pre-v*.bak"))) == 1 and (tmp_path / "v3.db.pre-v4.bak").exists()
    Database(path).close(); Database(path).close()
    assert len(list(tmp_path.glob("v3.db.pre-v*.bak"))) == 1            # reopening never creates repeated backups
    raw = sqlite3.connect(tmp_path / "v3.db.pre-v4.bak")
    assert raw.execute("PRAGMA user_version").fetchone()[0] == 4 and "ai_messages" not in {
        r[0] for r in raw.execute("SELECT name FROM sqlite_master")}


def test_v1_and_v2_databases_reach_v5(tmp_path):
    p1, p2 = tmp_path / "a.db", tmp_path / "b.db"
    make_v1_db(p1); make_v2_db(p2)
    for p in (p1, p2):
        d = Database(p); assert d.user_version == 5 and V4_TABLES <= tables(d); d.close()


def test_failed_v5_step_rolls_back_completely(tmp_path, monkeypatch):
    path = tmp_path / "f.db"
    make_v3_db(path)
    real = migrations._m5_ai_conversations
    def broken(conn):
        real(conn); raise sqlite3.OperationalError("simulated failure after creating tables")
    monkeypatch.setattr(migrations, "MIGRATIONS", migrations.MIGRATIONS[:4] + [(5, broken)])
    with pytest.raises(DatabaseError) as e:
        Database(path)
    assert "rolled back" in str(e.value)
    raw = sqlite3.connect(path)
    assert raw.execute("PRAGMA user_version").fetchone()[0] == 4
    assert not V4_TABLES & {r[0] for r in raw.execute("SELECT name FROM sqlite_master")}
    assert raw.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 1
    raw.close()
    monkeypatch.undo()
    d = Database(path); assert d.user_version == 5; d.close()          # the next normal start completes the upgrade


def test_corrupt_and_locked_databases_fail_safely(tmp_path):
    bad = tmp_path / "bad.db"; bad.write_bytes(b"this is not a database" * 50)
    with pytest.raises(DatabaseError):
        Database(bad)
    assert bad.read_bytes().startswith(b"this is not")
    path = tmp_path / "l.db"; make_v3_db(path)
    lock = sqlite3.connect(path, timeout=0.1); lock.execute("BEGIN EXCLUSIVE")
    try:
        with pytest.raises(DatabaseError):
            Database(path)
    finally:
        lock.rollback(); lock.close()
    assert sqlite3.connect(path).execute("PRAGMA user_version").fetchone()[0] == 4      # untouched
