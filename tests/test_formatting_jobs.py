import threading

from PySide6.QtCore import QThread

from sentinel.core.formatting import NA, freshness, human_bytes, human_duration, percent, short_hash
from sentinel.ui.jobs import JobRunner


def test_human_bytes_and_missing_values():
    assert human_bytes(None) == NA
    assert human_bytes(0) == "0 B"
    assert human_bytes(1536) == "1.5 KB"
    assert human_bytes(5 * 1024**3) == "5.0 GB"
    assert human_bytes(3 * 1024**5) == "3072.0 TB"
    assert percent(None) == NA and percent(12.34) == "12%" and percent(12.34, 1) == "12.3%"


def test_human_duration():
    assert human_duration(None) == NA
    assert human_duration(5) == "5s"
    assert human_duration(125) == "2m 5s"
    assert human_duration(3 * 3600 + 120) == "3h 2m"
    assert human_duration(2 * 86400 + 5 * 3600) == "2d 5h"
    assert human_duration(-4) == "0s"


def test_freshness_states():
    assert freshness(None, 2)[0] == "off"
    assert freshness(0.4, 2) == ("live", "live")
    assert freshness(3, 2)[0] == "live"
    state, text = freshness(30, 2)
    assert state == "stale" and "30s" in text
    assert short_hash("a" * 64) == "a" * 12 + "…" and short_hash(None) == NA


def test_job_runs_off_ui_thread_and_calls_back_on_ui_thread(qapp, pump):
    runner = JobRunner()
    ui_thread = QThread.currentThread()
    seen = {}

    def work(cancel, progress):
        seen["worker_thread"] = threading.current_thread().name
        progress(1, 2, "half")
        return 42

    def done(result):
        seen["result"] = result
        seen["cb_is_ui"] = QThread.currentThread() == ui_thread

    runner.submit(work, done, on_progress=lambda d, t, label: seen.setdefault("progress", (d, t, label)))
    assert pump(5, lambda: "result" in seen)
    assert seen["result"] == 42 and seen["cb_is_ui"] is True
    assert seen["worker_thread"] != threading.current_thread().name
    assert seen["progress"] == (1, 2, "half")
    assert runner.active_count == 0
    runner.shutdown()


def test_job_failure_is_reported_not_raised(qapp, pump):
    runner = JobRunner()
    errors = []

    def boom(cancel, progress):
        raise RuntimeError("disk on fire")

    runner.submit(boom, on_done=lambda r: errors.append("DONE?"), on_fail=errors.append)
    assert pump(5, lambda: bool(errors))
    assert errors == ["disk on fire"]
    runner.shutdown()


def test_job_cancel_and_shutdown_wait_for_workers(qapp, pump):
    runner = JobRunner(max_threads=1)
    started = threading.Event()
    finished = threading.Event()

    def slow(cancel, progress):
        started.set()
        cancel.wait(10)
        finished.set()

    runner.submit(slow)
    assert started.wait(3)
    runner.shutdown()                    # cancels and waits
    assert finished.is_set()
    assert runner.submit(lambda c, p: None).is_set()   # jobs submitted after shutdown never run
