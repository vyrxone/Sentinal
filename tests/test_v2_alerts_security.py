"""V2 alert management and security-service behaviour."""

import os
import shutil
import time

import pytest

from sentinel.core.alerts import AlertState, NotifyLimiter
from sentinel.core.thresholds import Alert
from sentinel.monitoring.sampler import Sample
from sentinel.security.integrity import BaselineStore
from sentinel.security.service import SecurityService, parse_excludes
from sentinel.security.watcher import FsEvent


def wait_for(predicate, timeout=8.0, step=0.1):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(step)
    return False


# ----------------------------------------------------------------- event store
def test_prune_keeps_unacknowledged_warnings_regression_A2(events):
    events.add("security", "baseline_mismatch", "important", severity="warning")
    for i in range(30):
        events.add("app", "x", f"noise {i}")
    removed = events.prune(10)
    left = [e.message for e in events.query(limit=100)]
    assert "important" in left and removed == 21 and events.count() == 10
    assert events.pruned_total == 21


def test_prune_may_exceed_cap_only_with_protected_rows(events):
    for i in range(5):
        events.add("security", "baseline_mismatch", f"w{i}", severity="warning")
    assert events.prune(2) == 0 and events.count() == 5


def test_acknowledged_warnings_become_prunable(events):
    for i in range(5):
        events.add("security", "baseline_mismatch", f"w{i}", severity="warning")
    events.acknowledge([e.id for e in events.query()])
    assert events.prune(2) == 3 and events.count() == 2


def test_ack_unack_archive_filters_and_delete_archived(events):
    a = events.add("system", "cpu_warning", "cpu high", severity="warning")
    b = events.add("system", "ram_warning", "ram high", severity="notice")
    c = events.add("app", "app_started", "hello")
    assert events.count(status="unacknowledged") == 2                       # info never needs attention
    assert events.acknowledge([a.id]) == 1
    assert {e.id for e in events.query(status="acknowledged")} == {a.id}
    assert {e.id for e in events.query(status="unacknowledged")} == {b.id}
    assert events.acknowledge([a.id], False) == 1 and events.count(status="unacknowledged") == 2
    assert events.archive([b.id, c.id]) == 2
    assert {e.id for e in events.query(status="active")} == {a.id}
    assert {e.id for e in events.query(status="archived")} == {b.id, c.id}
    assert events.count() == 3                                              # archiving never deletes
    assert events.archive_matching(severity="warning") == 1
    assert events.delete_archived() == 3 and events.count() == 0
    with pytest.raises(ValueError):
        events.query(status="bogus")


def test_search_filters_severity_category_time_text(events):
    old = events.add("security", "file_created", "report.docx appeared", ts=time.time() - 7200)
    new = events.add("system", "cpu_warning", "cpu 95%", severity="warning")
    assert [e.id for e in events.query(text="report")] == [old.id]
    assert [e.id for e in events.query(severity="warning")] == [new.id]
    assert [e.id for e in events.query(since=time.time() - 3600)] == [new.id]
    assert [e.id for e in events.query(category="security")] == [old.id]


def test_ack_state_survives_reopening_the_database(tmp_path):
    from sentinel.storage.database import Database
    from sentinel.storage.events_store import EventStore

    db = Database(tmp_path / "r.db")
    es = EventStore(db)
    e = es.add("system", "cpu_warning", "cpu", severity="warning")
    es.acknowledge([e.id])
    es.archive([e.id])
    db.close()
    es2 = EventStore(Database(tmp_path / "r.db"))
    got = es2.query(status="archived")[0]
    assert got.acknowledged and got.archived and got.acked_ts is not None


# ------------------------------------------------------------- alert state / notify
def test_notify_limiter_rate_limits_per_key():
    t = [0.0]
    lim = NotifyLimiter(60, clock=lambda: t[0])
    assert lim.allow("a") and not lim.allow("a") and lim.allow("b")
    t[0] = 61
    assert lim.allow("a")


def test_alert_state_open_close_and_interrupt(db):
    st = AlertState(db)
    st.open("cpu", "warning", "CPU at 95%")
    assert [a["key"] for a in st.active()] == ["cpu"]
    assert st.close("cpu") and st.active() == []
    assert not st.close("cpu")                                          # already closed
    st.open("ram", "warning", "RAM 90%")
    stale = st.interrupt_stale()
    assert [a["key"] for a in stale] == ["ram"] and st.active() == []
    assert st.history()[0]["state"] in ("interrupted", "recovered")


def feed(ctrl, cpu, n=1):
    for _ in range(n):
        ctrl._handle_sample(Sample(timestamp=time.time(), cpu_percent=cpu, cpu_count=2, memory_percent=10.0,
                                   system_disk_percent=10.0))


def test_controller_alert_lifecycle_with_recovery_and_notifications(ctrl, pump):
    sent = []
    ctrl.tray_message = lambda title, text: sent.append((title, text))
    ctrl.settings.set("sustained_samples", 1)
    feed(ctrl, 95)
    assert [a["key"] for a in ctrl.alert_state.active()] == ["cpu"]
    assert any(e.event_type == "cpu_warning" and e.severity == "warning" for e in ctrl.events.query())
    assert len(sent) == 1
    feed(ctrl, 20)
    assert ctrl.alert_state.active() == []
    assert any(e.event_type == "cpu_recovered" for e in ctrl.events.query())
    assert len(sent) == 1 or len(sent) == 2
    feed(ctrl, 95)
    feed(ctrl, 20)
    feed(ctrl, 95)
    assert len(sent) <= 3                                               # rate limit stops a notification storm
    ctrl.settings.set("notifications_enabled", False)
    n = len(sent)
    feed(ctrl, 20)
    feed(ctrl, 95)
    assert len(sent) == n                                               # audit log still written, no popup
    assert ctrl.events.count(event_type="cpu_warning") >= 3


def test_restart_marks_previous_active_alerts_interrupted(db, qapp):
    from sentinel.controller import AppController

    c1 = AppController(db)
    c1.alert_state.open("cpu", "warning", "CPU at 97%")                # app "crashed" with this alert open
    c2 = AppController(db)
    c2._recover_interrupted_alerts()
    assert c2.alert_state.active() == []
    ev = c2.events.query(event_type="alert_interrupted")
    assert ev and "unknown" in ev[0].message and ev[0].severity == "notice"
    c1.shutdown()


# ------------------------------------------------------------------- security
@pytest.fixture
def svc(db, events, tmp_path):
    s = SecurityService(db, events, BaselineStore(db), internal_dirs=[str(tmp_path / "appdata")])
    yield s
    s.stop()


def test_observation_timestamp_is_preserved_regression_A3(svc, events, tmp_path):
    svc._internal = []
    watched = tmp_path / "w"
    watched.mkdir()
    svc.add_folder(str(watched))
    past = time.time() - 500
    svc._handle(FsEvent(past, "file_created", str(watched / "a.txt"), None, False, str(watched)))
    assert events.query(event_type="file_created")[0].ts == past


def test_parse_excludes_normalises():
    assert parse_excludes("node_modules, *.tmp\n\n .git/ \nnode_modules") == ("node_modules", "*.tmp", ".git")
    assert parse_excludes("") == () and parse_excludes(None) == ()


def test_exclusions_work_as_documented(svc, events, tmp_path):
    root = tmp_path / "proj"
    (root / "node_modules" / "pkg").mkdir(parents=True)
    root.mkdir(exist_ok=True)
    svc.add_folder(str(root))
    svc.set_excludes(str(root), ["node_modules", "*.tmp"])
    for rel in ("node_modules/pkg/index.js", "scratch.tmp", "sub/deep.tmp", "main.py"):
        svc._handle(FsEvent(time.time(), "file_created", str(root / rel), None, False, str(root)))
        svc._last_seen.clear()
    paths = [os.path.relpath(e.path, root) for e in events.query(event_type="file_created")]
    assert paths == ["main.py"] and svc.stats()["excluded"] == 3
    assert events.query(event_type="watch_excludes_changed")                # the change itself is audited
    assert svc.list_folders()[0].excludes == ("node_modules", "*.tmp")


def test_excludes_persist_and_unknown_folder_is_ignored(svc, db, tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    svc.add_folder(str(root))
    svc.set_excludes(str(root), ["a"])
    assert db.query_one("SELECT excludes FROM watched_folders")[0] == "a"
    assert svc.set_excludes(str(tmp_path / "other"), ["b"]) == ("b",)       # no row -> nothing changes
    assert db.query_one("SELECT COUNT(*) FROM watched_folders")[0] == 1


def test_watch_health_detects_removed_folder_and_recovers_when_it_returns(svc, events, tmp_path):
    """Regression for V1 defect A4 (refresh_folders was never called)."""
    root = tmp_path / "usb"
    root.mkdir()
    svc.add_folder(str(root))
    svc.start()
    assert svc.check_health()[str(root)] == "watching"
    shutil.rmtree(root)
    assert svc.check_health()[str(root)] == "unavailable"
    assert svc.check_health()[str(root)] == "unavailable"                   # repeated checks do not spam events
    assert len(events.query(event_type="watch_unavailable")) == 1
    assert svc.list_folders()[0].state == "unavailable"
    root.mkdir()
    assert svc.check_health()[str(root)] == "watching"
    assert len(events.query(event_type="watch_restored")) == 1
    f = root / "after.txt"
    time.sleep(0.5)
    f.write_text("hello")
    assert wait_for(lambda: events.count(event_type="file_created") >= 1)   # the new watch really works


def test_refresh_folders_is_a_health_check_and_safe_when_stopped(svc):
    svc.refresh_folders()                                                    # not running: no error
    assert svc.check_health() == {}


def test_controller_runs_health_check_off_the_ui_thread(ctrl, pump, tmp_path):
    root = tmp_path / "w"
    root.mkdir()
    ctrl.security.add_folder(str(root))
    ctrl.security.start()
    calls = []
    real = ctrl.security.check_health
    ctrl.security.check_health = lambda: calls.append(1) or real()
    ctrl._run_health_check()
    assert pump(5, lambda: bool(calls))
    ctrl.security.stop()
