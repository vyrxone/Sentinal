"""Security service: watches user-selected folders and logs what it observes.

Everything here is read-only with respect to the user's files. Work happens on
one worker thread fed by a bounded queue so the UI and the OS notification
thread are never blocked by hashing or database writes.
"""

from __future__ import annotations

import fnmatch
import logging
import os
import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

from ..core import paths
from ..storage.database import Database, DatabaseError
from ..storage.events_store import EventStore
from .info import EVENT_EXPLANATIONS
from .integrity import BaselineResult, BaselineStore, Progress, key_for
from .watcher import FolderWatcher, FsEvent

log = logging.getLogger("sentinel.security")

COALESCE_SECONDS = 1.0
RATE_WINDOW_SECONDS = 60.0
HASH_RETRIES = 3


class SecurityError(Exception):
    """User-presentable problem (bad folder, already watched, ...)."""


@dataclass(frozen=True)
class WatchedFolder:
    id: int
    path: str
    recursive: bool
    enabled: bool
    state: str  # watching | unavailable | stopped
    excludes: tuple = ()


def _is_drive_root(path: str) -> bool:
    drive, tail = os.path.splitdrive(path)
    return tail in ("\\", "/") or os.path.dirname(path) == path


class SecurityService:
    def __init__(self, db: Database, events: EventStore, baseline: BaselineStore, *,
                 internal_dirs: Optional[Iterable[str]] = None, rate_limit_per_min: int = 300,
                 queue_size: int = 1000,
                 watcher_factory: Callable[[Callable[[FsEvent], None]], FolderWatcher] = FolderWatcher
                 ) -> None:
        self._db = db
        self._events = events
        self._baseline = baseline
        self._internal = [str(d) for d in (internal_dirs if internal_dirs is not None
                                           else [paths.data_dir()])]
        self._rate_limit = rate_limit_per_min
        self._queue: queue.Queue[FsEvent] = queue.Queue(maxsize=queue_size)
        self._watcher_factory = watcher_factory
        self._watcher: Optional[FolderWatcher] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._last_seen: dict[tuple[str, str], float] = {}
        self._window_start = time.monotonic()
        self._window_count = 0
        self._suppressed = 0
        self.dropped = 0
        self.last_report = None          # most recent VerifyReport (None until a verification ran)
        self.processed = 0
        self.excluded = 0
        self._unavailable: set[str] = set()     # watch roots already reported as unavailable
        self._exclude_cache: dict[str, tuple] = {}

    # ------------------------------------------------------------------ folders
    @staticmethod
    def validate_folder(path: str) -> str:
        if not path or "\0" in path:
            raise SecurityError("Please choose a folder.")
        full = os.path.abspath(os.path.expanduser(path))
        if not os.path.exists(full):
            raise SecurityError(f"The folder does not exist:\n{full}")
        if not os.path.isdir(full):
            raise SecurityError(f"That path is not a folder:\n{full}")
        if _is_drive_root(full):
            raise SecurityError("Watching an entire drive is not allowed. "
                                "Please choose a specific folder.")
        try:
            with os.scandir(full):
                pass
        except OSError as exc:
            raise SecurityError(f"Sentinel cannot read this folder ({exc.strerror or exc}).\n{full}")
        return full

    def list_folders(self) -> list[WatchedFolder]:
        out = []
        for row in self._db.query("SELECT * FROM watched_folders ORDER BY path_key"):
            path = row["path"]
            if self._watcher is not None and self._watcher.is_watching(path):
                state = "watching"
            elif not os.path.isdir(path):
                state = "unavailable"
            else:
                state = "stopped"
            out.append(WatchedFolder(row["id"], path, bool(row["recursive"]),
                                     bool(row["enabled"]), state, parse_excludes(row["excludes"])))
        return out

    def add_folder(self, path: str, recursive: bool = True) -> WatchedFolder:
        full = self.validate_folder(path)
        if self._db.query_one("SELECT 1 FROM watched_folders WHERE path_key=?", (key_for(full),)):
            raise SecurityError("This folder is already being watched.")
        if paths_overlap_data(full, self._internal):
            raise SecurityError("This folder contains Sentinel's own data and cannot be watched.")
        if self.is_running and self._watcher is not None:
            try:
                self._watcher.add(full, recursive)
            except OSError as exc:
                raise SecurityError(f"Could not start watching this folder: {exc}")
        self._db.execute(
            "INSERT INTO watched_folders(path, path_key, recursive, enabled, added_ts) "
            "VALUES(?,?,?,?,?)", (full, key_for(full), int(recursive), 1, time.time()))
        self._events.add("security", "watch_added",
                         f"Started watching folder ({'including subfolders' if recursive else 'top level only'}).",
                         path=full)
        self._reload_excludes()
        return next(f for f in self.list_folders() if key_for(f.path) == key_for(full))

    def set_excludes(self, path: str, patterns: Iterable[str]) -> tuple:
        """User-chosen exclusions for one watched folder (file or folder names / glob patterns)."""
        clean = parse_excludes("\n".join(patterns))
        n = self._db.execute_rowcount("UPDATE watched_folders SET excludes=? WHERE path_key=?",
                                      ("\n".join(clean), key_for(path)))
        if n:
            self._reload_excludes()
            self._events.add("security", "watch_excludes_changed",
                             f"Exclusions for this folder were changed ({len(clean)} pattern(s)). "
                             "Excluded files are not reported.", path=path, details={"patterns": list(clean)})
        return clean

    def _reload_excludes(self) -> None:
        cache = {}
        for row in self._db.query("SELECT path, excludes FROM watched_folders"):
            cache[os.path.normcase(os.path.abspath(row["path"]))] = parse_excludes(row["excludes"])
        self._exclude_cache = cache

    def is_excluded(self, watch_root: str, path: str) -> bool:
        patterns = self._exclude_cache.get(os.path.normcase(os.path.abspath(watch_root)), ())
        if not patterns:
            return False
        try:
            rel = os.path.relpath(path, watch_root)
        except ValueError:
            return False
        parts = [q for q in rel.replace("\\", "/").split("/") if q and q != "."]
        rel_posix = "/".join(parts)
        for pat in patterns:
            if fnmatch.fnmatch(rel_posix.lower(), pat.lower()):
                return True
            if any(fnmatch.fnmatch(part.lower(), pat.lower()) for part in parts):
                return True
        return False

    def remove_folder(self, path: str) -> bool:
        if self._watcher is not None:
            self._watcher.remove(path)
        removed = self._db.execute_rowcount(
            "DELETE FROM watched_folders WHERE path_key=?", (key_for(path),)) > 0
        if removed:
            self._events.add("security", "watch_removed", "Stopped watching folder.", path=path)
            self._unavailable.discard(os.path.normcase(os.path.abspath(path)))
            self._reload_excludes()
        return removed

    # ---------------------------------------------------------------- lifecycle
    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.is_running:
            return
        self._stop.clear()
        self._reload_excludes()
        self._watcher = self._watcher_factory(self._enqueue)
        for folder in self.list_folders():
            if not folder.enabled:
                continue
            try:
                self._watcher.add(folder.path, folder.recursive)
            except OSError as exc:
                log.warning("cannot watch %s: %s", folder.path, exc)
                self._unavailable.add(os.path.normcase(os.path.abspath(folder.path)))
                self._safe_event("watch_unavailable",
                                 f"Folder could not be watched ({exc}). It may be disconnected "
                                 "or permission was removed.", path=folder.path, severity="notice")
        self._thread = threading.Thread(target=self._worker, name="sentinel-security", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._watcher is not None:
            self._watcher.stop()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3.0)
        self._thread = None
        self._watcher = None

    def refresh_folders(self) -> None:
        """Retry folders that were unavailable (e.g. a drive was reconnected)."""
        self.check_health()

    def check_health(self) -> dict[str, str]:
        """Detect removed/returned folders and dead watches; recover where possible.

        Called periodically by the controller (cheap: one isdir per watched folder, run off the
        UI thread). Each state change is recorded once in the audit log, never repeated.
        Returns {path: state}.
        """
        watcher = self._watcher
        if watcher is None:
            return {}
        states: dict[str, str] = {}
        for folder in self.list_folders():
            if not folder.enabled:
                continue
            key = os.path.normcase(os.path.abspath(folder.path))
            exists = os.path.isdir(folder.path)
            if not exists:
                if watcher.is_watching(folder.path):
                    watcher.remove(folder.path)            # the OS watch is dead; drop it
                if key not in self._unavailable:
                    self._unavailable.add(key)
                    self._safe_event("watch_unavailable",
                                     "Watched folder is no longer available (removed, renamed or the drive "
                                     "was disconnected). Sentinel will resume automatically if it returns.",
                                     path=folder.path, severity="notice")
                states[folder.path] = "unavailable"
                continue
            if not watcher.is_watching(folder.path):
                try:
                    watcher.add(folder.path, folder.recursive)
                except OSError as exc:
                    states[folder.path] = "unavailable"
                    if key not in self._unavailable:
                        self._unavailable.add(key)
                        self._safe_event("watch_unavailable", f"Folder cannot be watched ({exc}).",
                                         path=folder.path, severity="notice")
                    continue
                if key in self._unavailable:
                    self._unavailable.discard(key)
                    self._safe_event("watch_restored", "Watched folder is available again; watching resumed. "
                                     "Changes made while it was unavailable were not observed.",
                                     path=folder.path, severity="info")
            states[folder.path] = "watching"
        return states

    def stats(self) -> dict[str, int]:
        return {"queued": self._queue.qsize(), "processed": self.processed,
                "dropped": self.dropped, "suppressed": self._suppressed, "excluded": self.excluded}

    # --------------------------------------------------------------- event flow
    def _enqueue(self, ev: FsEvent) -> None:
        try:
            self._queue.put_nowait(ev)
        except queue.Full:
            self.dropped += 1

    def _safe_event(self, event_type: str, message: str, **kw) -> None:
        try:
            self._events.add("security", event_type, message, **kw)
        except DatabaseError:
            log.warning("could not store event %s (database unavailable)", event_type)

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                ev = self._queue.get(timeout=0.5)
            except queue.Empty:
                self._flush_summary(force=False)
                continue
            try:
                self._handle(ev)
            except Exception:
                log.exception("failed to process file event")
            self.processed += 1

    def _is_internal(self, *paths_: Optional[str]) -> bool:
        return any(p and any(paths.is_inside(p, d) for d in self._internal) for p in paths_)

    def _handle(self, ev: FsEvent) -> None:
        if self._is_internal(ev.path, ev.dest_path):
            return
        if self.is_excluded(ev.watch_root, ev.path):
            self.excluded += 1
            return
        now = time.monotonic()
        key = (ev.event_type, os.path.normcase(ev.path))
        last = self._last_seen.get(key)
        self._last_seen[key] = now
        if len(self._last_seen) > 4000:
            self._last_seen = {k: t for k, t in self._last_seen.items() if now - t < 5}
        if last is not None and now - last < COALESCE_SECONDS:
            return  # bursts of identical events for one path are merged
        self._baseline_check(ev)
        if not self._allow(now):
            return
        message = EVENT_EXPLANATIONS.get(ev.event_type, ev.event_type)
        if ev.dest_path:
            message += f" New location: {ev.dest_path}"
        details = {"watch_root": ev.watch_root}
        if ev.dest_path:
            details["destination"] = ev.dest_path
        self._safe_event(ev.event_type, message, path=ev.path, details=details, ts=ev.ts,
                         severity="notice" if ev.event_type.endswith("deleted") else "info")

    def _allow(self, now: float) -> bool:
        if now - self._window_start >= RATE_WINDOW_SECONDS:
            self._flush_summary(force=True)
            self._window_start, self._window_count = now, 0
        if self._window_count < self._rate_limit:
            self._window_count += 1
            return True
        self._suppressed += 1
        return False

    def _flush_summary(self, force: bool) -> None:
        now = time.monotonic()
        if self._suppressed and (force or now - self._window_start >= RATE_WINDOW_SECONDS):
            count, self._suppressed = self._suppressed, 0
            self._safe_event("events_suppressed",
                             f"{count} additional file events were not recorded because more than "
                             f"{self._rate_limit} events arrived in one minute.",
                             severity="notice", details={"suppressed": count})
            self._window_start, self._window_count = now, 0

    def _baseline_check(self, ev: FsEvent) -> None:
        if ev.is_directory:
            return
        entry = self._baseline.get(ev.path)
        if entry is None:
            return
        if ev.event_type in ("file_deleted", "file_moved"):
            where = f" (moved to {ev.dest_path})" if ev.dest_path else ""
            self._safe_event("baseline_missing",
                             f"A file in your integrity baseline is no longer at its recorded "
                             f"location{where}.", path=ev.path, severity="warning", ts=ev.ts,
                             details={"expected_sha256": entry.sha256})
            return
        result: Optional[BaselineResult] = None
        for attempt in range(HASH_RETRIES):
            result = self._baseline.check_file(entry)
            if result.status != "error" or self._stop.wait(0.5):
                break  # a file still being written can be briefly unreadable; retry
        if result is None or result.status in ("ok",):
            return
        if result.status == "modified":
            self._safe_event(
                "baseline_mismatch",
                "The file's SHA-256 differs from the baseline you recorded. This means the "
                "content changed; it does not by itself mean the file is malicious.",
                path=ev.path, severity="warning", ts=ev.ts,
                details={"expected_sha256": result.expected, "actual_sha256": result.actual})
        elif result.status == "missing":
            self._safe_event("baseline_missing", "Baseline file no longer exists.",
                             path=ev.path, severity="warning")
        else:
            self._safe_event("baseline_error", result.message, path=ev.path, severity="notice")

    # ------------------------------------------------------------ explicit scans
    def verify_baseline(self, cancel: Optional[threading.Event] = None,
                        progress: Optional[Progress] = None) -> list[BaselineResult]:
        """User-triggered: re-hash every baseline file and log differences.

        A cancelled or cut-short run is recorded as ``baseline_verification_incomplete`` and is
        never reported as a successful verification (V2 defect A4).
        """
        report = self._baseline.verify_report(cancel, progress)
        self.last_report = report
        bad = 0
        counts = {"modified": 0, "missing": 0, "error": 0, "unstable": 0}
        for res in report.results:
            if res.status == "modified":
                bad += 1
                counts["modified"] += 1
                self._safe_event("baseline_mismatch",
                                 "Hash differs from the baseline. The content changed; this is not "
                                 "proof of malware.", path=res.path, severity="warning",
                                 details={"expected_sha256": res.expected,
                                          "actual_sha256": res.actual})
            elif res.status == "missing":
                bad += 1
                counts["missing"] += 1
                self._safe_event("baseline_missing", "Baseline file is missing.",
                                 path=res.path, severity="warning")
            elif res.status == "error":
                bad += 1
                counts["error"] += 1
                self._safe_event("baseline_error", res.message, path=res.path, severity="notice")
            elif res.status == "unstable":
                bad += 1
                counts["unstable"] += 1
                self._safe_event("baseline_unstable", res.message, path=res.path, severity="notice")
        details = {"checked": len(report.results), "total": report.total, "differences": bad, **counts}
        if not report.complete:
            self._safe_event(
                "baseline_verification_incomplete",
                f"Verification was stopped before it finished: {len(report.results)} of {report.total} "
                f"baseline file(s) were checked ({bad} need review). The remaining files were NOT "
                "verified, so this is not a clean result.",
                severity="warning" if bad else "notice", details={**details, "cancelled": True})
        else:
            self._safe_event(
                "baseline_verified",
                f"Verified {len(report.results)} baseline file(s): "
                f"{len(report.results) - bad} unchanged, {bad} need review.",
                severity="warning" if bad else "info", details=details)
        return report.results

    def last_verification(self) -> Optional[dict]:
        """Most recent verification outcome (from the audit log), or None if never run."""
        rows = self._events.query(event_type="baseline_verified", limit=1, status="all") \
            + self._events.query(event_type="baseline_verification_incomplete", limit=1, status="all")
        if not rows:
            return None
        ev = max(rows, key=lambda e: e.ts)
        return {"ts": ev.ts, "complete": ev.event_type == "baseline_verified",
                "differences": (ev.details or {}).get("differences", 0), "message": ev.message}


def parse_excludes(raw) -> tuple:
    """Normalise a user's exclusion list (newline or comma separated) into clean patterns."""
    if not raw:
        return ()
    items = []
    for part in str(raw).replace(",", "\n").splitlines():
        part = part.strip().replace("\\", "/").strip("/")
        if part and "\0" not in part and part not in items and len(part) <= 200:
            items.append(part)
    return tuple(items[:100])


def paths_overlap_data(folder: str, internal_dirs: Iterable[str]) -> bool:
    """True if *folder* is inside, or equal to, an internal data directory."""
    return any(paths.is_inside(folder, d) for d in internal_dirs)
