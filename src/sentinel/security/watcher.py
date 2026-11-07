"""Thin wrapper around watchdog: one Observer thread for all watched folders."""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

log = logging.getLogger("sentinel.watcher")

_IGNORED = {"opened", "closed", "closed_no_write"}  # read-only access, not changes


@dataclass(frozen=True)
class FsEvent:
    ts: float
    event_type: str       # file_created | file_modified | file_deleted | file_moved | dir_*
    path: str
    dest_path: Optional[str]
    is_directory: bool
    watch_root: str


class _Handler(FileSystemEventHandler):
    def __init__(self, root: str, sink: Callable[[FsEvent], None]) -> None:
        super().__init__()
        self._root = root
        self._sink = sink

    def on_any_event(self, event: FileSystemEvent) -> None:
        kind = event.event_type
        if kind in _IGNORED:
            return
        is_dir = bool(event.is_directory)
        if is_dir and kind == "modified":
            return  # directory mtime churn: the interesting child event is reported separately
        if kind not in ("created", "modified", "deleted", "moved"):
            return
        prefix = "dir" if is_dir else "file"
        dest = getattr(event, "dest_path", None) or None
        try:
            self._sink(FsEvent(time.time(), f"{prefix}_{kind}", os.fsdecode(event.src_path),
                               os.fsdecode(dest) if dest else None, is_dir, self._root))
        except Exception:
            log.exception("watch sink failed")


class FolderWatcher:
    def __init__(self, sink: Callable[[FsEvent], None]) -> None:
        self._sink = sink
        self._observer: Optional[Observer] = None
        self._watches: dict[str, object] = {}
        self._lock = threading.Lock()

    def _ensure_observer(self) -> Observer:
        if self._observer is None:
            self._observer = Observer()
            self._observer.daemon = True
            self._observer.start()
        return self._observer

    def add(self, path: str, recursive: bool = True) -> None:
        """Start watching; raises OSError if the folder cannot be watched."""
        key = os.path.normcase(os.path.abspath(path))
        with self._lock:
            if key in self._watches:
                return
            observer = self._ensure_observer()
            try:
                watch = observer.schedule(_Handler(path, self._sink), path, recursive=recursive)
            except OSError:
                raise
            except Exception as exc:  # watchdog raises assorted types for bad paths
                raise OSError(str(exc)) from exc
            self._watches[key] = watch

    def remove(self, path: str) -> None:
        key = os.path.normcase(os.path.abspath(path))
        with self._lock:
            watch = self._watches.pop(key, None)
            if watch is not None and self._observer is not None:
                try:
                    self._observer.unschedule(watch)
                except Exception:
                    log.exception("unschedule failed")

    def is_watching(self, path: str) -> bool:
        with self._lock:
            return os.path.normcase(os.path.abspath(path)) in self._watches

    def stop(self, timeout: float = 5.0) -> None:
        with self._lock:
            observer, self._observer = self._observer, None
            self._watches.clear()
        if observer is not None:
            try:
                observer.stop()
                observer.join(timeout)
            except Exception:
                log.exception("observer shutdown failed")
