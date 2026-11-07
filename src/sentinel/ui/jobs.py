"""Run slow work (hashing, directory walks, exports, git, analysis) off the UI thread.

Results are delivered through signals connected to bound methods of a main-thread QObject, so
callbacks always run on the UI thread. V3 adds, on top of the V2 fixed-size pool:

* a bounded queue (``max_pending``): extra jobs are rejected with a clear failure, never piled up;
* optional per-job timeouts. Python threads cannot be killed, so a timeout is cooperative: the
  cancel event is set, the caller is told right away that the job timed out, and a late result
  is discarded. A job that ignores its cancel event still occupies a worker until it returns,
  which is visible in the status counters;
* coalescing by ``key``: asking for a job that is already pending or running returns the
  existing cancel event instead of starting a duplicate;
* bounded history of recent jobs (duration, outcome) for the Performance page and slow-operation
  diagnostics.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal, Slot

log = logging.getLogger("sentinel.jobs")

JobFn = Callable[[threading.Event, Callable[[int, int, str], None]], Any]

SLOW_JOB_S = 0.5          # jobs longer than this are listed as slow operations
HISTORY = 50
WATCHDOG_MS = 500


@dataclass(frozen=True)
class JobRecord:
    name: str
    seconds: float
    outcome: str          # done | failed | timeout | cancelled | rejected
    finished_ts: float


class _Signals(QObject):
    done = Signal(int, object)
    failed = Signal(int, str)
    progress = Signal(int, int, int, str)  # job id, done, total, label


class _Task(QRunnable):
    def __init__(self, job_id: int, fn: JobFn, cancel: threading.Event, signals: _Signals) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self.job_id, self.fn, self.cancel, self.signals = job_id, fn, cancel, signals
        self.t0: Optional[float] = None       # set when a worker actually starts it
        self.t1: Optional[float] = None

    def run(self) -> None:
        def progress(done: int, total: int, label: str) -> None:
            self.signals.progress.emit(self.job_id, done, total, label)

        self.t0 = time.perf_counter()
        try:
            result = self.fn(self.cancel, progress)
        except Exception as exc:
            self.t1 = time.perf_counter()
            log.exception("background job %d failed", self.job_id)
            self.signals.failed.emit(self.job_id, str(exc) or type(exc).__name__)
        else:
            self.t1 = time.perf_counter()
            self.signals.done.emit(self.job_id, result)


class JobRunner(QObject):
    def __init__(self, parent: Optional[QObject] = None, max_threads: int = 2, max_pending: int = 32) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(max_threads)
        self.max_threads = max_threads
        self.max_pending = max_pending
        self._signals = _Signals(self)
        self._signals.done.connect(self._on_done)
        self._signals.failed.connect(self._on_failed)
        self._signals.progress.connect(self._on_progress)
        self._next = 1
        self._jobs: dict[int, dict] = {}
        self._keys: dict[str, int] = {}
        self._closed = False
        self.history: deque[JobRecord] = deque(maxlen=HISTORY)
        self.counts = {"done": 0, "failed": 0, "timeout": 0, "cancelled": 0, "rejected": 0, "coalesced": 0}
        self._watchdog = QTimer(self)
        self._watchdog.setInterval(WATCHDOG_MS)
        self._watchdog.timeout.connect(self._check_deadlines)

    # ---------------------------------------------------------------- status
    @property
    def active_count(self) -> int:
        return len(self._jobs)

    @property
    def running_count(self) -> int:
        return sum(1 for j in self._jobs.values() if j["task"].t0 is not None and j["task"].t1 is None)

    @property
    def pending_count(self) -> int:
        return sum(1 for j in self._jobs.values() if j["task"].t0 is None)

    def status(self) -> dict:
        slow = sorted((r for r in self.history if r.seconds >= SLOW_JOB_S), key=lambda r: -r.seconds)[:5]
        return {"running": self.running_count, "pending": self.pending_count, **self.counts,
                "threads": self.max_threads, "slow": [(r.name, round(r.seconds, 2), r.outcome) for r in slow]}

    def running_names(self) -> list[str]:
        return [j["name"] for j in self._jobs.values()]

    # ---------------------------------------------------------------- submit
    def submit(self, fn: JobFn, on_done: Optional[Callable[[Any], None]] = None,
               on_fail: Optional[Callable[[str], None]] = None,
               on_progress: Optional[Callable[[int, int, str], None]] = None, *,
               name: str = "job", timeout_s: Optional[float] = None,
               key: Optional[str] = None) -> threading.Event:
        """Start *fn(cancel_event, progress)*. Returns the cancel event."""
        cancel = threading.Event()
        if self._closed:
            cancel.set()
            return cancel
        if key is not None and key in self._keys and self._keys[key] in self._jobs:
            self.counts["coalesced"] += 1
            return self._jobs[self._keys[key]]["cancel"]
        if len(self._jobs) >= self.max_pending:
            self.counts["rejected"] += 1
            self.history.append(JobRecord(name, 0.0, "rejected", time.time()))
            cancel.set()
            if on_fail:
                QTimer.singleShot(0, lambda m="Too many background jobs are waiting; try again in a moment.":
                                  self._call(on_fail, m))
            return cancel
        job_id = self._next
        self._next += 1
        task = _Task(job_id, fn, cancel, self._signals)
        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        self._jobs[job_id] = dict(task=task, done=on_done, fail=on_fail, progress=on_progress, name=name,
                                  cancel=cancel, key=key, deadline=deadline, timeout_s=timeout_s,
                                  submitted=time.perf_counter())
        if key is not None:
            self._keys[key] = job_id
        if deadline is not None and not self._watchdog.isActive():
            self._watchdog.start()
        self._pool.start(task)
        return cancel

    # --------------------------------------------------------------- results
    def _finish(self, job_id: int, outcome: str) -> Optional[dict]:
        job = self._jobs.pop(job_id, None)
        if job is None:
            return None
        if job["key"] is not None and self._keys.get(job["key"]) == job_id:
            del self._keys[job["key"]]
        task = job["task"]
        if outcome == "timeout":
            seconds = time.perf_counter() - job["submitted"]
        elif task.t0 is not None and task.t1 is not None:
            seconds = task.t1 - task.t0
        else:
            seconds = 0.0
        if outcome == "done" and job["cancel"].is_set():
            outcome = "cancelled"
        self.counts[outcome] = self.counts.get(outcome, 0) + 1
        self.history.append(JobRecord(job["name"], seconds, outcome, time.time()))
        if seconds >= SLOW_JOB_S:
            log.info("slow background operation %r took %.2fs (%s)", job["name"], seconds, outcome)
        if not any(j["deadline"] is not None for j in self._jobs.values()):
            self._watchdog.stop()
        return job

    @Slot(int, object)
    def _on_done(self, job_id: int, result: Any) -> None:
        job = self._finish(job_id, "done")
        if job and job["done"]:
            self._call(job["done"], result)

    @Slot(int, str)
    def _on_failed(self, job_id: int, message: str) -> None:
        job = self._finish(job_id, "failed")
        if job and job["fail"]:
            self._call(job["fail"], message)

    @Slot(int, int, int, str)
    def _on_progress(self, job_id: int, done: int, total: int, label: str) -> None:
        job = self._jobs.get(job_id)
        if job and job["progress"]:
            self._call(job["progress"], done, total, label)

    def _check_deadlines(self) -> None:
        now = time.monotonic()
        for job_id, job in list(self._jobs.items()):
            if job["deadline"] is not None and now >= job["deadline"]:
                job["cancel"].set()                      # ask the worker to stop; discard its late result
                self._finish(job_id, "timeout")
                if job["fail"]:
                    self._call(job["fail"], f"{job['name']} timed out after {job['timeout_s']:g} s and was cancelled.")

    @staticmethod
    def _call(fn: Callable, *args: Any) -> None:
        try:
            fn(*args)
        except Exception:
            log.exception("job callback failed")

    def shutdown(self, timeout_ms: int = 4000) -> None:
        """Cancel everything and wait for the worker threads to finish."""
        self._closed = True
        self._watchdog.stop()
        for job in list(self._jobs.values()):
            job["cancel"].set()
        self._pool.waitForDone(timeout_ms)
        self._jobs.clear()
        self._keys.clear()
