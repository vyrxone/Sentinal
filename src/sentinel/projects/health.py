"""Project health reports computed off the UI thread (V3).

Everything that can touch a slow or disconnected drive runs through :func:`bounded_call`, which
gives up after a timeout and leaves at most a few abandoned daemon threads, so an offline network
share can neither freeze the UI nor exhaust the worker pool. Git is read-only (see git_status).
Test and build status come ONLY from results the user recorded; Sentinel never runs project
commands and never invents a status.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Optional

from ..storage.database import Database
from . import git_status as git_mod
from .store import Project
from .tasks import TaskStore

log = logging.getLogger("sentinel.health")

PATH_TIMEOUT = 5.0
MAX_STUCK = 4
_lock = threading.Lock()
_stuck = 0

PATH_TEXT = {
    "unset": "No folder is set for this project.",
    "ok": "Folder is available.",
    "missing": "Folder not found (moved, renamed or drive disconnected).",
    "not_a_folder": "The path exists but is not a folder.",
    "unreadable": "Folder exists but cannot be read (permissions).",
    "unreachable": "The drive did not answer in time (offline network share?). It was not waited for.",
}


def bounded_call(fn: Callable[[], Any], timeout: float) -> tuple[str, Any]:
    """Run *fn* in a daemon thread. Returns ('ok', value) | ('error', exc) | ('timeout', None)."""
    global _stuck
    with _lock:
        if _stuck >= MAX_STUCK:
            return "timeout", None             # too many hung calls already: do not start another
    box: dict[str, Any] = {}

    def target() -> None:
        global _stuck
        try:
            box["v"] = fn()
        except BaseException as exc:           # noqa: BLE001 - reported to the caller
            box["e"] = exc
        with _lock:
            if box.get("abandoned"):
                _stuck -= 1
            box["done"] = True

    t = threading.Thread(target=target, daemon=True, name="sentinel-bounded")
    t.start()
    t.join(timeout)
    with _lock:
        if not box.get("done"):
            box["abandoned"] = True
            _stuck += 1
            return "timeout", None
    if "e" in box:
        return "error", box["e"]
    return "ok", box.get("v")


def stuck_calls() -> int:
    return _stuck


def inspect_path(path: str, timeout: float = PATH_TIMEOUT) -> str:
    if not path:
        return "unset"

    def probe() -> str:
        if not os.path.exists(path):
            return "missing"
        if not os.path.isdir(path):
            return "not_a_folder"
        try:
            with os.scandir(path) as it:
                next(it, None)
        except OSError:
            return "unreadable"
        return "ok"

    kind, val = bounded_call(probe, timeout)
    if kind == "ok":
        return val
    return "unreachable" if kind == "timeout" else "missing"


@dataclass
class HealthReport:
    project_id: int
    checked_ts: float
    path: str
    path_state: str
    git: Optional[dict] = None
    tasks: dict = field(default_factory=dict)
    last_note_ts: Optional[float] = None
    test: Optional[dict] = None
    build: Optional[dict] = None
    overall: str = "unknown"              # ok | attention | unavailable | unknown
    messages: list = field(default_factory=list)
    duration_ms: float = 0.0
    last_ok_ts: Optional[float] = None
    complete: bool = True                 # False if cancelled part-way

    def as_json(self) -> str:
        return json.dumps(asdict(self), default=str)


def path_transition(previous: Optional[str], current: str) -> Optional[str]:
    """Edge detection so 'unavailable'/'restored' are each recorded once, not on every check."""
    bad = {"missing", "not_a_folder", "unreadable", "unreachable"}
    if current in bad and previous not in bad:
        return "unavailable"
    if current == "ok" and previous in bad:
        return "restored"
    return None


def compute_health(project: Project, tasks: TaskStore, *, cancel: Optional[threading.Event] = None,
                   progress: Optional[Callable[[int, int, str], None]] = None, with_git: bool = True,
                   git_timeout: float = git_mod.GIT_TIMEOUT, path_timeout: float = PATH_TIMEOUT) -> HealthReport:
    t0 = time.perf_counter()
    cancelled = lambda: cancel is not None and cancel.is_set()  # noqa: E731
    rep = HealthReport(project.id, time.time(), project.path, "unset")
    if progress:
        progress(0, 3, "Checking folder")
    rep.path_state = inspect_path(project.path, path_timeout)
    rep.messages.append(PATH_TEXT[rep.path_state])
    rep.tasks = tasks.counts(project.id)
    rep.last_note_ts = tasks.last_note_ts(project.id)
    rep.test, rep.build = tasks.last_check_result(project.id, "test"), tasks.last_check_result(project.id, "build")
    if with_git and rep.path_state == "ok" and not cancelled():
        if progress:
            progress(1, 3, "Reading Git status")

        kind, val = bounded_call(lambda: git_mod.get_git_status(project.path, git_timeout), git_timeout * 2 + 2)
        if kind == "ok":
            g = val
            rep.git = {"git_available": g.git_available, "is_repo": g.is_repo, "branch": g.branch,
                       "clean": g.clean, "staged": g.staged, "modified": g.modified, "untracked": g.untracked,
                       "conflicted": g.conflicted, "ahead": g.ahead, "behind": g.behind,
                       "last_commit": g.last_commit, "error": g.error, "summary": g.summary(),
                       "changed_files": list(g.changed_files)}
        else:
            rep.git = {"git_available": True, "is_repo": False, "error": "Git did not answer in time.",
                       "summary": "Git did not answer in time."}
        rep.messages.append(rep.git["summary"])
    rep.complete = not cancelled()
    if rep.path_state == "unset":
        rep.overall = "unknown"
    elif rep.path_state != "ok":
        rep.overall = "unavailable"
    elif rep.git and (rep.git.get("error") or rep.git.get("conflicted")):
        rep.overall = "attention"
    elif rep.git and rep.git.get("is_repo") and not rep.git.get("clean"):
        rep.overall = "attention"
    else:
        rep.overall = "ok"
    if rep.tasks.get("overdue"):
        rep.messages.append(f"{rep.tasks['overdue']} overdue task(s).")
        if rep.overall == "ok":
            rep.overall = "attention"
    if rep.test is None:
        rep.messages.append("No test result recorded (Sentinel does not run your tests).")
    rep.duration_ms = (time.perf_counter() - t0) * 1000
    if progress:
        progress(3, 3, "")
    return rep


class HealthStore:
    """Caches the last report per project, including the last time everything was fine."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def previous_path_state(self, project_id: int) -> Optional[str]:
        r = self._db.query_one("SELECT report FROM project_health WHERE project_id=?", (project_id,))
        if not r:
            return None
        try:
            return json.loads(r["report"]).get("path_state")
        except ValueError:
            return None

    def save(self, rep: HealthReport) -> HealthReport:
        row = self._db.query_one("SELECT last_ok_ts FROM project_health WHERE project_id=?", (rep.project_id,))
        last_ok = rep.checked_ts if rep.overall == "ok" else (row["last_ok_ts"] if row else None)
        rep.last_ok_ts = last_ok
        self._db.execute(
            "INSERT INTO project_health(project_id, checked_ts, last_ok_ts, report) VALUES(?,?,?,?) "
            "ON CONFLICT(project_id) DO UPDATE SET checked_ts=excluded.checked_ts, "
            "last_ok_ts=excluded.last_ok_ts, report=excluded.report",
            (rep.project_id, rep.checked_ts, last_ok, rep.as_json()))
        return rep

    def load(self, project_id: int) -> Optional[HealthReport]:
        r = self._db.query_one("SELECT * FROM project_health WHERE project_id=?", (project_id,))
        if not r:
            return None
        try:
            d = json.loads(r["report"])
            rep = HealthReport(**{k: d.get(k) for k in HealthReport.__dataclass_fields__ if k in d})
        except (ValueError, TypeError):
            return None
        rep.last_ok_ts = r["last_ok_ts"]
        return rep
