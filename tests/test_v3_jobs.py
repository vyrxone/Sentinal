"""V3 job runner: bounded queue, timeouts, coalescing, cancellation, status and clean shutdown."""

import threading
import time

from sentinel.ui.jobs import JobRunner


def test_timeout_cancels_cooperatively_and_discards_late_result(qapp, pump):
    jr = JobRunner(max_threads=1)
    out = {"fail": [], "done": []}

    def slow(cancel, progress):
        cancel.wait(5)                       # cooperative: wakes when cancelled
        return "late"

    jr.submit(slow, out["done"].append, out["fail"].append, name="slowjob", timeout_s=0.3)
    assert pump(3, lambda: out["fail"])
    assert "timed out" in out["fail"][0] and "slowjob" in out["fail"][0]
    pump(0.5)
    assert out["done"] == [] and jr.counts["timeout"] == 1          # the late result never reaches the caller
    assert jr.active_count == 0
    jr.shutdown()


def test_queue_is_bounded_and_rejections_are_reported(qapp, pump):
    jr = JobRunner(max_threads=1, max_pending=3)
    gate = threading.Event()
    fails = []
    for i in range(3):
        jr.submit(lambda c, p: gate.wait(5), name=f"j{i}")
    jr.submit(lambda c, p: None, on_fail=fails.append, name="overflow")
    assert pump(2, lambda: fails) and "Too many" in fails[0]
    assert jr.counts["rejected"] == 1 and jr.active_count == 3
    gate.set()
    assert pump(3, lambda: jr.active_count == 0)
    jr.shutdown()


def test_same_key_is_coalesced(qapp, pump):
    jr = JobRunner(max_threads=1)
    gate, runs = threading.Event(), []

    def work(c, p):
        runs.append(1)
        gate.wait(5)
        return 1

    c1 = jr.submit(work, name="a", key="same")
    c2 = jr.submit(work, name="a", key="same")
    assert c1 is c2 and jr.counts["coalesced"] == 1
    gate.set()
    assert pump(3, lambda: jr.active_count == 0) and len(runs) == 1
    jr.submit(work, name="a", key="same")                            # allowed again after completion
    assert pump(3, lambda: len(runs) == 2)
    jr.shutdown()


def test_status_reports_running_pending_and_slow_operations(qapp, pump, monkeypatch):
    from sentinel.ui import jobs

    monkeypatch.setattr(jobs, "SLOW_JOB_S", 0.05)
    jr = JobRunner(max_threads=1)
    gate = threading.Event()
    jr.submit(lambda c, p: gate.wait(5), name="first")
    jr.submit(lambda c, p: time.sleep(0.1), name="second")
    pump(0.3)
    st = jr.status()
    assert st["running"] == 1 and st["pending"] == 1 and st["threads"] == 1
    gate.set()
    assert pump(3, lambda: jr.active_count == 0)
    slow = jr.status()["slow"]
    assert any(name == "second" for name, _, _ in slow)
    assert jr.counts["done"] == 2
    jr.shutdown()


def test_failure_and_cancel_are_counted(qapp, pump):
    jr = JobRunner()
    errs = []
    jr.submit(lambda c, p: 1 / 0, on_fail=errs.append, name="boom")
    ev = jr.submit(lambda c, p: c.wait(0.2), name="stopme")
    ev.set()
    assert pump(3, lambda: jr.active_count == 0 and errs)
    assert "division" in errs[0] and jr.counts["failed"] == 1 and jr.counts["cancelled"] == 1
    jr.shutdown()


def test_shutdown_cancels_running_jobs_and_refuses_new_ones(qapp):
    jr = JobRunner(max_threads=2)
    seen = []

    def loop(cancel, progress):
        while not cancel.is_set():
            time.sleep(0.01)
        seen.append("stopped")

    jr.submit(loop, name="loop")
    time.sleep(0.1)
    jr.shutdown()
    assert seen == ["stopped"] and jr.active_count == 0
    ev = jr.submit(lambda c, p: seen.append("ran"), name="late")
    assert ev.is_set() and "ran" not in seen
    assert not jr._watchdog.isActive()


def test_callback_exception_does_not_break_runner(qapp, pump):
    jr = JobRunner()
    ok = []
    jr.submit(lambda c, p: 1, on_done=lambda r: 1 / 0, name="bad-callback")
    jr.submit(lambda c, p: 2, on_done=ok.append, name="good")
    assert pump(3, lambda: ok == [2])
    jr.shutdown()
