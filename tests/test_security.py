import hashlib
import os
import threading
import time

import pytest

from sentinel.security.hashing import HashCancelled, sha256_file
from sentinel.security.integrity import BaselineStore, plan_folder_baseline
from sentinel.security.service import SecurityError, SecurityService
from sentinel.security.watcher import FolderWatcher


def wait_for(predicate, timeout=8.0, step=0.1):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(step)
    return False


# ------------------------------------------------------------------ hashing
def test_sha256_matches_hashlib_and_supports_cancel(tmp_path):
    f = tmp_path / "a.bin"
    data = os.urandom(3 * 1024 * 1024 + 17)
    f.write_bytes(data)
    assert sha256_file(f) == hashlib.sha256(data).hexdigest()
    ev = threading.Event()
    ev.set()
    with pytest.raises(HashCancelled):
        sha256_file(f, cancel=ev)
    with pytest.raises(OSError):
        sha256_file(tmp_path / "missing.bin")


# ------------------------------------------------------------ baseline logic
def test_baseline_detects_modified_missing_and_ok(db, tmp_path):
    a, b, c = (tmp_path / n for n in ("a.txt", "b.txt", "c.txt"))
    for p in (a, b, c):
        p.write_text("original " + p.name)
    store = BaselineStore(db)
    summary = store.add_files([str(a), str(b), str(c)])
    assert (summary.added, summary.updated, summary.failed) == (3, 0, [])
    assert store.count() == 3

    a.write_text("tampered")
    b.unlink()
    results = {os.path.basename(r.path): r for r in store.verify()}
    assert results["a.txt"].status == "modified"
    assert results["a.txt"].expected != results["a.txt"].actual
    assert results["b.txt"].status == "missing"
    assert results["c.txt"].status == "ok"
    # verification is read-only: the baseline hash was NOT silently replaced
    assert store.get(a).sha256 == hashlib.sha256(b"original a.txt").hexdigest()
    assert a.read_text() == "tampered"


def test_baseline_accept_current_state_and_remove(db, tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("v1")
    store = BaselineStore(db)
    store.add_file(f)
    f.write_text("v2")
    assert store.verify()[0].status == "modified"
    entry, was_update = store.add_file(f, replace=True)   # explicit, confirmed user action: accept new state
    assert was_update and entry.sha256 == hashlib.sha256(b"v2").hexdigest()
    assert store.verify()[0].status == "ok"
    assert store.remove(f) is True and store.remove(f) is False
    assert store.count() == 0


def test_baseline_handles_unreadable_and_non_files(db, tmp_path):
    store = BaselineStore(db)
    summary = store.add_files([str(tmp_path), str(tmp_path / "nope.txt")])
    assert summary.added == 0 and len(summary.failed) == 2
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        f = tmp_path / "locked.txt"
        f.write_text("x")
        store.add_file(f)
        os.chmod(f, 0)
        try:
            assert store.verify()[0].status == "error"
        finally:
            os.chmod(f, 0o600)


def test_baseline_progress_and_cancel(db, tmp_path):
    files = []
    for i in range(5):
        p = tmp_path / f"f{i}.txt"
        p.write_text(str(i))
        files.append(str(p))
    store = BaselineStore(db)
    ev = threading.Event()
    seen = []

    def progress(done, total, path):
        seen.append((done, total))
        if done == 2:
            ev.set()

    summary = store.add_files(files, cancel=ev, progress=progress)
    assert summary.cancelled and summary.added == 2
    assert seen[0] == (0, 5)


def test_plan_folder_baseline_respects_limits_and_skips_symlinks(tmp_path):
    for i in range(10):
        (tmp_path / f"f{i}.txt").write_text("x" * 100)
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "deep.txt").write_text("y")
    try:
        os.symlink(tmp_path / "f0.txt", tmp_path / "link.txt")
    except (OSError, NotImplementedError):
        pass
    full = plan_folder_baseline(tmp_path, max_files=100)
    assert len(full.files) == 11 and not full.truncated
    assert all(not os.path.islink(p) for p in full.files)
    capped = plan_folder_baseline(tmp_path, max_files=4)
    assert len(capped.files) == 4 and capped.truncated
    by_bytes = plan_folder_baseline(tmp_path, max_files=100, max_bytes=250)
    assert by_bytes.truncated and by_bytes.total_bytes <= 250


# -------------------------------------------------- folder validation (no I/O)
def test_validate_folder_errors(tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("x")
    for bad in ("", str(tmp_path / "nope"), str(f), os.path.abspath(os.sep)):
        with pytest.raises(SecurityError):
            SecurityService.validate_folder(bad)
    assert SecurityService.validate_folder(str(tmp_path)) == str(tmp_path)


# ------------------------------------------------------- real watcher + service
@pytest.fixture
def service(db, events, tmp_path):
    svc = SecurityService(db, events, BaselineStore(db), internal_dirs=[str(tmp_path / "appdata")])
    yield svc
    svc.stop()


def types(events):
    return [e.event_type for e in events.query(category="security", newest_first=False)]


def test_watcher_records_create_modify_delete(service, events, tmp_path):
    watched = tmp_path / "watched"
    watched.mkdir()
    service.add_folder(str(watched))
    service.start()
    time.sleep(0.5)
    f = watched / "report.txt"
    f.write_text("one")
    assert wait_for(lambda: "file_created" in types(events))
    time.sleep(1.2)                                   # outside the coalescing window
    f.write_text("two, longer")
    assert wait_for(lambda: "file_modified" in types(events))
    f.unlink()
    assert wait_for(lambda: "file_deleted" in types(events))

    created = events.query(event_type="file_created")[0]
    assert created.path == str(f) and created.category == "security"
    modified = events.query(event_type="file_modified")[0]
    assert "not by itself a sign of malware" in modified.message
    assert events.query(event_type="file_deleted")[0].severity == "notice"
    assert not f.exists()                              # only our own deletion happened


def test_baseline_mismatch_event_from_live_change(service, events, tmp_path, db):
    watched = tmp_path / "w"
    watched.mkdir()
    f = watched / "important.cfg"
    f.write_text("trusted")
    BaselineStore(db).add_file(f)
    service.add_folder(str(watched))
    service.start()
    time.sleep(0.5)
    f.write_text("changed by someone")
    assert wait_for(lambda: "baseline_mismatch" in types(events))
    ev = events.query(event_type="baseline_mismatch")[0]
    assert ev.severity == "warning" and ev.path == str(f)
    assert "does not by itself mean the file is malicious" in ev.message
    assert ev.details["expected_sha256"] != ev.details["actual_sha256"]
    f.unlink()
    assert wait_for(lambda: "baseline_missing" in types(events))


def test_verify_baseline_logs_explicit_scan(service, events, tmp_path, db):
    f = tmp_path / "v.txt"
    f.write_text("a")
    BaselineStore(db).add_file(f)
    f.write_text("b")
    results = service.verify_baseline()
    assert [r.status for r in results] == ["modified"]
    kinds = types(events)
    assert "baseline_mismatch" in kinds and "baseline_verified" in kinds
    assert events.query(event_type="baseline_verified")[0].severity == "warning"


def test_remove_folder_stops_watching(service, events, tmp_path):
    w = tmp_path / "gone"
    w.mkdir()
    service.add_folder(str(w))
    service.start()
    assert service.list_folders()[0].state == "watching"
    assert service.remove_folder(str(w)) is True
    assert service.list_folders() == []
    time.sleep(0.4)
    (w / "after.txt").write_text("x")
    time.sleep(1.5)
    assert "file_created" not in types(events)
    assert "watch_removed" in types(events)


def test_duplicate_and_internal_folders_rejected(service, tmp_path):
    w = tmp_path / "dup"
    w.mkdir()
    service.add_folder(str(w))
    with pytest.raises(SecurityError, match="already"):
        service.add_folder(str(w))
    (tmp_path / "appdata").mkdir(exist_ok=True)
    with pytest.raises(SecurityError, match="own data"):
        service.add_folder(str(tmp_path / "appdata"))


def test_service_ignores_its_own_data_dir_and_survives_missing_folder(db, events, tmp_path):
    data = tmp_path / "appdata"
    data.mkdir()
    watched = tmp_path / "parent"
    (watched / "appdata").mkdir(parents=True)
    svc = SecurityService(db, events, BaselineStore(db),
                          internal_dirs=[str(watched / "appdata")])
    svc.add_folder(str(watched))
    gone = tmp_path / "usb"
    gone.mkdir()
    svc.add_folder(str(gone))
    gone.rmdir()                                       # folder disappears before start
    svc.start()
    try:
        states = {os.path.basename(f.path): f.state for f in svc.list_folders()}
        assert states["usb"] == "unavailable" and states["parent"] == "watching"
        assert wait_for(lambda: "watch_unavailable" in types(events))
        time.sleep(0.4)
        (watched / "appdata" / "sentinel.db").write_text("internal")
        (watched / "visible.txt").write_text("hello")
        assert wait_for(lambda: any(e.path and e.path.endswith("visible.txt")
                                    for e in events.query(category="security")))
        time.sleep(1.0)
        assert not any((e.path or "").endswith("sentinel.db") for e in events.query())
    finally:
        svc.stop()


def test_rate_limit_and_summary(db, events, tmp_path):
    svc = SecurityService(db, events, BaselineStore(db), internal_dirs=[], rate_limit_per_min=5)
    from sentinel.security.watcher import FsEvent

    for i in range(12):
        svc._handle(FsEvent(time.time(), "file_created", str(tmp_path / f"f{i}"), None, False, str(tmp_path)))
    assert len(events.query(event_type="file_created")) == 5
    svc._flush_summary(force=True)
    summary = events.query(event_type="events_suppressed")[0]
    assert summary.details["suppressed"] == 7


def test_identical_burst_events_are_coalesced(db, events, tmp_path):
    svc = SecurityService(db, events, BaselineStore(db), internal_dirs=[])
    from sentinel.security.watcher import FsEvent

    for _ in range(20):
        svc._handle(FsEvent(time.time(), "file_modified", str(tmp_path / "busy.log"), None, False, str(tmp_path)))
    assert len(events.query(event_type="file_modified")) == 1


def test_queue_overflow_is_counted_not_fatal(db, events, tmp_path):
    svc = SecurityService(db, events, BaselineStore(db), internal_dirs=[], queue_size=3)
    from sentinel.security.watcher import FsEvent

    for i in range(10):
        svc._enqueue(FsEvent(time.time(), "file_created", f"/x/{i}", None, False, "/x"))
    assert svc.stats()["queued"] == 3 and svc.stats()["dropped"] == 7


def test_stop_joins_threads(db, events, tmp_path):
    before = threading.active_count()
    svc = SecurityService(db, events, BaselineStore(db), internal_dirs=[])
    w = tmp_path / "t"
    w.mkdir()
    svc.add_folder(str(w))
    svc.start()
    assert threading.active_count() > before
    svc.stop()
    assert wait_for(lambda: threading.active_count() == before, timeout=5)


def test_folder_watcher_rejects_missing_path(tmp_path):
    w = FolderWatcher(lambda ev: None)
    try:
        with pytest.raises(OSError):
            w.add(str(tmp_path / "does-not-exist"))
    finally:
        w.stop()
