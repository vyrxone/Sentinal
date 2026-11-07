"""V3 integrity hardening: change-during-hash, confirmed replacement, symlinks, incomplete verification."""

import hashlib
import os
import threading

import pytest

from sentinel.security import integrity
from sentinel.security.integrity import (BaselineConflict, BaselineStore, FileChangedDuringHash,
                                         UnsafeBaselinePath)
from sentinel.security.service import SecurityService
from sentinel.storage.events_store import EventStore


def grow_while_hashing(monkeypatch, victim):
    real = integrity.sha256_file
    calls = {"n": 0}

    def hashing(path, cancel=None, **kw):
        calls["n"] += 1
        digest = real(path, cancel, **kw)
        if os.path.abspath(path) == str(victim):
            with open(path, "ab") as fh:                      # the file is written while being read
                fh.write(b"more" * calls["n"])
        return digest

    monkeypatch.setattr(integrity, "sha256_file", hashing)
    return calls


def test_file_changing_during_hashing_is_not_recorded(db, tmp_path, monkeypatch):
    f = tmp_path / "busy.log"
    f.write_bytes(b"x" * 1000)
    grow_while_hashing(monkeypatch, f)
    store = BaselineStore(db)
    with pytest.raises(FileChangedDuringHash):
        store.add_file(f)
    assert store.count() == 0                                  # nothing half-trusted was stored
    summary = store.add_files([str(f)])
    assert summary.added == 0 and "changing" in summary.failed[0][1].lower() or "changed" in summary.failed[0][1].lower()


def test_transient_change_is_retried_once(db, tmp_path, monkeypatch):
    f = tmp_path / "t.txt"
    f.write_bytes(b"a" * 100)
    real = integrity.sha256_file
    n = {"c": 0}

    def once(path, cancel=None, **kw):
        n["c"] += 1
        d = real(path, cancel, **kw)
        if n["c"] == 1:
            with open(path, "ab") as fh:
                fh.write(b"b")
        return d

    monkeypatch.setattr(integrity, "sha256_file", once)
    entry, _ = BaselineStore(db).add_file(f)
    assert entry.sha256 == hashlib.sha256(b"a" * 100 + b"b").hexdigest() and n["c"] == 2


def test_replacing_a_baseline_needs_explicit_confirmation(db, tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("v1")
    store = BaselineStore(db)
    store.add_file(f)
    f.write_text("v2-tampered")
    with pytest.raises(BaselineConflict):
        store.add_file(f)
    assert store.get(f).sha256 == hashlib.sha256(b"v1").hexdigest()      # old trusted hash kept
    summary = store.add_files([str(f)])
    assert summary.conflicts == [str(f)] and summary.updated == 0 and not summary.failed
    assert store.existing_among([str(f), str(tmp_path / "other")]) == [str(f)]
    entry, was_update = store.add_file(f, replace=True)
    assert was_update and entry.sha256 == hashlib.sha256(b"v2-tampered").hexdigest()


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="no symlinks")
def test_symlinks_are_refused_and_not_followed(db, tmp_path):
    target = tmp_path / "outside.txt"
    target.write_text("secret")
    link = tmp_path / "link.txt"
    try:
        os.symlink(target, link)
    except OSError:
        pytest.skip("symlink creation not permitted")
    store = BaselineStore(db)
    with pytest.raises(UnsafeBaselinePath):
        store.add_file(link)
    assert store.count() == 0
    real = tmp_path / "real.txt"
    real.write_text("ok")
    store.add_file(real)
    real.unlink()
    os.symlink(target, real)                                  # file replaced by a link later
    res = store.verify()[0]
    assert res.status == "error" and "symbolic link" in res.message


def test_permission_failure_is_reported_per_file(db, tmp_path, monkeypatch):
    good, bad = tmp_path / "g.txt", tmp_path / "b.txt"
    good.write_text("g")
    bad.write_text("b")
    real = integrity.sha256_file

    def deny(path, cancel=None, **kw):
        if path.endswith("b.txt"):
            raise PermissionError(13, "Access is denied")
        return real(path, cancel, **kw)

    monkeypatch.setattr(integrity, "sha256_file", deny)
    s = BaselineStore(db).add_files([str(good), str(bad)])
    assert s.added == 1 and len(s.failed) == 1 and "denied" in s.failed[0][1].lower()


def test_check_file_reports_unstable_when_file_keeps_changing(db, tmp_path, monkeypatch):
    f = tmp_path / "u.txt"
    f.write_text("1")
    store = BaselineStore(db)
    store.add_file(f)
    grow_while_hashing(monkeypatch, f)
    assert store.verify()[0].status == "unstable"


def make_service(db, tmp_path, n=6):
    events = EventStore(db)
    store = BaselineStore(db)
    files = []
    for i in range(n):
        p = tmp_path / f"f{i}.txt"
        p.write_text(str(i))
        files.append(str(p))
    store.add_files(files)
    return SecurityService(db, events, store), events, store, files


def test_cancelled_verification_is_never_reported_as_success(db, tmp_path):
    svc, events, store, files = make_service(db, tmp_path)
    cancel = threading.Event()

    def progress(done, total, path):
        if done == 2:
            cancel.set()

    results = svc.verify_baseline(cancel, progress)
    assert len(results) < 6 and not svc.last_report.complete
    assert events.count(event_type="baseline_verified") == 0
    inc = events.query(event_type="baseline_verification_incomplete")
    assert len(inc) == 1 and "NOT verified" in inc[0].message and inc[0].details["total"] == 6
    last = svc.last_verification()
    assert last["complete"] is False


def test_complete_verification_reports_counts_and_last_status(db, tmp_path):
    svc, events, store, files = make_service(db, tmp_path, 4)
    (tmp_path / "f1.txt").write_text("changed")
    os.unlink(tmp_path / "f2.txt")
    svc.verify_baseline()
    ev = events.query(event_type="baseline_verified")[0]
    assert ev.severity == "warning" and ev.details["modified"] == 1 and ev.details["missing"] == 1
    assert ev.details["checked"] == ev.details["total"] == 4
    assert svc.last_report.complete
    last = svc.last_verification()
    assert last["complete"] and last["differences"] == 2
    assert SecurityService(db, events, store).last_verification()["complete"]      # survives restart (from audit log)
    assert "not proof" in events.query(event_type="baseline_mismatch")[0].message.lower()


def test_verification_with_no_cancel_event_and_unreadable_file_is_complete_but_flagged(db, tmp_path, monkeypatch):
    svc, events, store, files = make_service(db, tmp_path, 2)
    real = integrity.sha256_file
    monkeypatch.setattr(integrity, "sha256_file", lambda p, c=None, **k: (_ for _ in ()).throw(PermissionError(13, "denied"))
                        if p.endswith("f0.txt") else real(p, c, **k))
    svc.verify_baseline()
    assert events.count(event_type="baseline_error") == 1 and svc.last_report.complete
    assert events.query(event_type="baseline_verified")[0].details["error"] == 1


def test_verification_memory_is_bounded_by_streaming(db, tmp_path):
    big = tmp_path / "big.bin"
    big.write_bytes(os.urandom(16 * 1024 * 1024))
    import tracemalloc

    store = BaselineStore(db)
    tracemalloc.start()
    store.add_file(big)
    store.verify()
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert peak < 4 * 1024 * 1024                              # a few chunks, far below the 16 MiB file
