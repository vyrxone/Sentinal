"""V5: Network tab, V4->V5 database behaviour (no schema change), secret-leak scans, idle/thread behaviour."""

import json
import re
import sqlite3
import threading
from pathlib import Path

import pytest

from ai_fakes import KEY, FakeKeyring, FakeTransport, gemini_ok, resp
from sentinel.ai import context_builder as cb
from sentinel.ai.conversation_store import ConversationStore
from sentinel.ai.errors import AIError, ErrorKind
from sentinel.storage import migrations
from sentinel.storage.database import Database
from test_v4_ui import window  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parents[1]
PW = "Pr0xy-Secret-Pass-77"


# ------------------------------------------------------------------ Network tab
def test_network_tab_validates_saves_and_never_shows_the_password(ctrl, window, pump, dialogs):
    ctrl.ai.secrets._backend = FakeKeyring()
    tab = window.pages[8].network
    assert tab.enabled.isChecked() is False and ctrl.settings.get("ai_proxy_enabled") is False
    tab.host.setText("http://bad host"); tab.enabled.setChecked(True)
    assert ctrl.settings.get("ai_proxy_enabled") is False and dialogs["warning"]           # refused with an explanation
    tab.host.setText("proxy.corp.example"); tab.enabled.setChecked(True)
    assert ctrl.settings.get("ai_proxy_enabled") is True and ctrl.settings.get("ai_proxy_host") == "proxy.corp.example"
    tab.port.setValue(3128); tab.port.editingFinished.emit()
    assert ctrl.settings.get("ai_proxy_port") == 3128
    tab.user.setText("alice"); tab.password.setText(PW); tab._save()
    assert ctrl.ai.secrets.proxy_credentials() == ("alice", PW) and tab.password.text() == ""
    assert PW not in tab.cred_state.text() and "Credential Manager" in tab.cred_state.text()
    assert tab.password.echoMode().name == "Password"
    tab._delete()                                                                          # dialogs answer Yes
    assert ctrl.ai.secrets.proxy_credentials() is None


def test_connection_buttons_are_disabled_while_ai_is_off(ctrl, window, pump):
    ctrl.ai.secrets._backend = FakeKeyring()
    ctrl.settings.set("ai_gemini_enabled", True); ctrl.settings.set("ai_gemini_model", "m")
    ctrl.ai.set_key("gemini", KEY)
    ctrl.settings.set("ai_enabled", False); pump(0.05)
    page = window.pages[8]
    assert not page.providers.boxes["gemini"].b_test.isEnabled() and not page.network.b_test.isEnabled()
    ctrl.settings.set("ai_enabled", True); pump(0.05)
    assert page.providers.boxes["gemini"].b_test.isEnabled()


# ------------------------------------------------------------------ V4 database -> V5 (no schema change, no extra backups)
def test_v4_database_opens_in_v5_unchanged_with_no_backup_and_no_new_tables(tmp_path):
    path = tmp_path / "sentinel.db"
    db = Database(path)
    store = ConversationStore(db)
    cid = store.create("kept")
    store.add_message(cid, "user", "q", "q")
    store.add_message(cid, "assistant", "a", "a", provider="groq", model="m")
    tables_before = {r[0] for r in db.query("SELECT name FROM sqlite_master WHERE type='table'")}
    db.close()
    for _ in range(3):                                                                    # repeated start-ups
        db = Database(path)
        assert db.query("PRAGMA user_version")[0][0] == migrations.LATEST_VERSION == 5
        assert {r[0] for r in db.query("SELECT name FROM sqlite_master WHERE type='table'")} == tables_before
        assert [m.content for m in ConversationStore(db).messages(cid)] == ["q", "a"]
        db.close()
    assert not list(tmp_path.glob("*.bak"))


def test_no_key_or_proxy_secret_is_ever_written_to_sqlite_settings_export_or_logs(ctrl, tmp_path, pump, caplog):
    import logging
    caplog.set_level(logging.DEBUG)
    ctrl.ai.secrets._backend = FakeKeyring()
    ctrl.ai.router.transport = FakeTransport({"generateContent": resp(200, gemini_ok("ok"))})
    ctrl.settings.set("ai_gemini_enabled", True); ctrl.settings.set("ai_gemini_model", "m"); ctrl.settings.set("ai_enabled", True)
    ctrl.settings.set("ai_history_enabled", True)
    ctrl.ai.set_key("gemini", KEY)
    ctrl.ai.secrets.set_proxy_credentials("alice", PW)
    out = {}
    ctrl.ai.send(cb.compose_request("chat", "hi", []), lambda r: out.update(ok=r), lambda e: out.update(e=e), confirmed=True)
    assert pump(8, lambda: out)
    ctrl.ai.record_exchange("hi", "hi", out["ok"], set())
    export = tmp_path / "export.json"
    ctrl.settings.export_json(export)
    db_dump = "\n".join(ctrl.db.conn.iterdump()) if hasattr(ctrl.db, "conn") else ""
    blobs = [export.read_text(), caplog.text, db_dump]
    for f in Path(ctrl.db.path).parent.glob("*"):
        if f.is_file() and f.suffix in (".db", ".log", ".json", ".bak", ""):
            blobs.append(f.read_bytes().decode("latin-1"))
    for blob in blobs:
        assert KEY not in blob and PW not in blob and "alice" not in blob.replace("alice@", "")


# ------------------------------------------------------------------ source tree scan
KEY_SHAPES = [re.compile(p) for p in (r"AIza[0-9A-Za-z_\-]{30,}", r"gsk_[0-9A-Za-z]{30,}", r"sk-or-[0-9A-Za-z\-_]{20,}",
                                      r"sk-[0-9A-Za-z]{32,}", r"Bearer\s+[A-Za-z0-9._\-]{30,}")]


# The one file that deliberately contains obviously fake, sequential key-shaped strings to test the scrubber.
SCRUBBER_FIXTURES = {"tests/test_v4_router_secrets_privacy.py"}


def test_repository_contains_no_key_shaped_text():
    hits = []
    for f in ROOT.rglob("*"):
        if not f.is_file() or "__pycache__" in f.parts or f.suffix in (".png", ".ico", ".pyc", ".zip", ".db"):
            continue
        if ".venv" in f.parts or ".pytest_cache" in f.parts:
            continue
        if f.relative_to(ROOT).as_posix() in SCRUBBER_FIXTURES:
            continue
        text = f.read_text(errors="ignore")
        for rx in KEY_SHAPES:
            if rx.search(text):
                hits.append(f"{f.relative_to(ROOT)}: {rx.pattern}")
    assert not hits, hits


# ------------------------------------------------------------------ idle, threads, bounded behaviour
def test_ai_disabled_is_completely_idle_no_threads_no_requests(ctrl, pump):
    ctrl.ai.router.transport = FakeTransport()
    before = threading.active_count()
    pump(0.3)
    assert ctrl.ai.jobs.active_count == 0 and ctrl.ai.router.transport.count == 0
    assert threading.active_count() <= before + 1
    snap = ctrl.ai.status_snapshot()
    assert snap["enabled"] is False and snap["active"] == 0 and snap["queue_depth"] == 0


def test_repeated_requests_do_not_grow_threads_or_job_tables(ctrl, pump):
    ai = ctrl.ai
    ai.secrets._backend = FakeKeyring()
    ai.router.transport = FakeTransport({"generateContent": resp(200, gemini_ok("ok"))})
    ctrl.settings.set("ai_gemini_enabled", True); ctrl.settings.set("ai_gemini_model", "m"); ctrl.settings.set("ai_enabled", True)
    ai.set_key("gemini", KEY)
    ai.send(cb.compose_request("chat", "warm up", []), lambda r: None, lambda e: None, confirmed=True)
    pump(3, lambda: not ai.busy)
    threads = threading.active_count()
    for i in range(40):
        done = []
        ai.send(cb.compose_request("chat", f"q{i}", []), done.append, done.append, confirmed=True)
        assert pump(5, lambda: done)
    assert threading.active_count() <= threads + 1
    assert ai.jobs.active_count == 0 and len(ai.jobs.history) <= 50 and not ai.busy
    assert len(ai.conversation.entries) <= 60


def test_shutdown_during_a_request_releases_the_worker(ctrl, pump):
    ai = ctrl.ai
    ai.secrets._backend = FakeKeyring()
    ai.router.transport = FakeTransport(default=resp(200, gemini_ok()), delay=30)
    ctrl.settings.set("ai_gemini_enabled", True); ctrl.settings.set("ai_gemini_model", "m"); ctrl.settings.set("ai_enabled", True)
    ai.set_key("gemini", KEY)
    ai.send(cb.compose_request("chat", "slow", []), lambda r: None, lambda e: None, confirmed=True)
    pump(0.3)
    ai.shutdown()
    assert pump(3, lambda: not ai.router._busy.locked())
