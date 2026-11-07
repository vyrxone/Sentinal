"""User-controlled SHA-256 integrity baseline (read-only: files are never altered)."""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

from ..storage.database import Database
from .hashing import HashCancelled, sha256_file

log = logging.getLogger("sentinel.integrity")

MAX_BASELINE_FILES = 2000
MAX_BASELINE_BYTES = 2 * 1024**3

Progress = Callable[[int, int, str], None]  # done, total, current path


def key_for(path: str | os.PathLike) -> str:
    return os.path.normcase(os.path.abspath(path))


@dataclass(frozen=True)
class BaselineEntry:
    id: int
    path: str
    sha256: str
    size: int
    mtime: Optional[float]
    added_ts: float
    verified_ts: Optional[float]


class BaselineConflict(OSError):
    """The file is already in the baseline and replacing it was not explicitly confirmed."""


class FileChangedDuringHash(OSError):
    """The file's size or modification time changed while it was being hashed."""


class UnsafeBaselinePath(OSError):
    """Symbolic link, or not a regular file: refused so the baseline cannot point out of scope."""


@dataclass(frozen=True)
class BaselineResult:
    path: str
    status: str  # ok | modified | missing | error | unstable
    expected: Optional[str] = None
    actual: Optional[str] = None
    message: str = ""


@dataclass
class AddSummary:
    added: int = 0
    updated: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)
    cancelled: bool = False
    conflicts: list[str] = field(default_factory=list)   # already in the baseline, not replaced


@dataclass
class VerifyReport:
    """Outcome of a verification run. ``complete`` is False if it was cancelled or cut short:
    an incomplete run is never reported as a successful verification."""
    results: list[BaselineResult]
    total: int
    cancelled: bool = False

    @property
    def complete(self) -> bool:
        return not self.cancelled and len(self.results) == self.total

    def count(self, status: str) -> int:
        return sum(1 for r in self.results if r.status == status)


@dataclass
class FolderPlan:
    files: list[str]
    total_bytes: int
    truncated: bool
    skipped: int


def plan_folder_baseline(folder: str | Path, max_files: int = MAX_BASELINE_FILES,
                         max_bytes: int = MAX_BASELINE_BYTES,
                         cancel: Optional[threading.Event] = None) -> FolderPlan:
    """List (without hashing) the files an explicit 'baseline this folder' would cover.

    Symlinks are not followed. The walk stops at the file or byte limit so a huge
    folder can never be hashed by accident; ``truncated`` tells the UI.
    """
    files: list[str] = []
    total = skipped = 0
    truncated = False
    for root, dirs, names in os.walk(folder, followlinks=False):
        if cancel is not None and cancel.is_set():
            truncated = True
            break
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
        for name in names:
            full = os.path.join(root, name)
            try:
                if os.path.islink(full):
                    skipped += 1
                    continue
                size = os.path.getsize(full)
            except OSError:
                skipped += 1
                continue
            if len(files) >= max_files or total + size > max_bytes:
                truncated = True
                break
            files.append(full)
            total += size
        if truncated:
            break
    return FolderPlan(files, total, truncated, skipped)


def _row_to_entry(row) -> BaselineEntry:
    return BaselineEntry(row["id"], row["path"], row["sha256"], row["size"], row["mtime"],
                         row["added_ts"], row["verified_ts"])


class BaselineStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    def list(self) -> list[BaselineEntry]:
        return [_row_to_entry(r) for r in
                self._db.query("SELECT * FROM baseline_files ORDER BY path_key")]

    def count(self) -> int:
        row = self._db.query_one("SELECT COUNT(*) FROM baseline_files")
        return int(row[0]) if row else 0

    def get(self, path: str | os.PathLike) -> Optional[BaselineEntry]:
        row = self._db.query_one("SELECT * FROM baseline_files WHERE path_key=?", (key_for(path),))
        return _row_to_entry(row) if row else None

    HASH_ATTEMPTS = 2

    @staticmethod
    def _signature(path: str) -> tuple[int, int]:
        st = os.stat(path)
        return st.st_size, st.st_mtime_ns

    def _hash_stable(self, path: str, cancel: Optional[threading.Event]) -> tuple[str, os.stat_result]:
        """Hash *path*; refuse the result if the file changed while it was being read."""
        for _ in range(self.HASH_ATTEMPTS):
            before = os.stat(path)
            digest = sha256_file(path, cancel)
            after = os.stat(path)
            if (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns):
                return digest, after
        raise FileChangedDuringHash(
            f"The file kept changing while it was being read ({path}); no baseline was recorded. "
            "Try again when the file is not being written.")

    def add_file(self, path: str | os.PathLike, cancel: Optional[threading.Event] = None, *,
                 replace: bool = False) -> tuple[BaselineEntry, bool]:
        """Hash the file now and store it. Returns (entry, was_update).

        An existing baseline entry is replaced only when ``replace=True`` (the UI asks the user
        first); otherwise :class:`BaselineConflict` is raised and nothing is changed.
        """
        full = os.path.abspath(path)
        if os.path.islink(full):
            raise UnsafeBaselinePath(f"Refused: {full} is a symbolic link (its target could be outside "
                                     "what you selected). Select the real file instead.")
        if not os.path.isfile(full):
            raise OSError(f"Not a regular file: {full}")
        existed = self.get(full) is not None
        if existed and not replace:
            raise BaselineConflict(f"{full} is already in the baseline; replacing it needs confirmation.")
        digest, st = self._hash_stable(full, cancel)
        now = time.time()
        self._db.execute(
            "INSERT INTO baseline_files(path, path_key, sha256, size, mtime, added_ts, verified_ts) "
            "VALUES(?,?,?,?,?,?,?) ON CONFLICT(path_key) DO UPDATE SET "
            "path=excluded.path, sha256=excluded.sha256, size=excluded.size, "
            "mtime=excluded.mtime, verified_ts=excluded.verified_ts",
            (full, key_for(full), digest, st.st_size, st.st_mtime, now, now))
        entry = self.get(full)
        assert entry is not None
        return entry, existed

    def existing_among(self, paths: Iterable[str]) -> list[str]:
        """Which of *paths* already have a baseline entry (so the UI can ask before replacing)."""
        return [p for p in paths if self.get(p) is not None]

    def add_files(self, paths: Iterable[str], cancel: Optional[threading.Event] = None,
                  progress: Optional[Progress] = None, *, replace: bool = False) -> AddSummary:
        items = list(paths)
        summary = AddSummary()
        for i, path in enumerate(items):
            if cancel is not None and cancel.is_set():
                summary.cancelled = True
                break
            if progress:
                progress(i, len(items), path)
            try:
                _, was_update = self.add_file(path, cancel, replace=replace)
                if was_update:
                    summary.updated += 1
                else:
                    summary.added += 1
            except HashCancelled:
                summary.cancelled = True
                break
            except BaselineConflict:
                summary.conflicts.append(path)
            except OSError as exc:
                summary.failed.append((path, exc.strerror or str(exc)))
        if progress:
            progress(len(items), len(items), "")
        return summary

    def remove(self, path: str | os.PathLike) -> bool:
        return self._db.execute_rowcount(
            "DELETE FROM baseline_files WHERE path_key=?", (key_for(path),)) > 0

    def check_file(self, entry: BaselineEntry,
                   cancel: Optional[threading.Event] = None) -> BaselineResult:
        """Compare one file with its stored hash (does not write anything)."""
        if os.path.islink(entry.path):
            return BaselineResult(entry.path, "error", entry.sha256, None,
                                  "The path is now a symbolic link; it was not followed.")
        if not os.path.exists(entry.path):
            return BaselineResult(entry.path, "missing", entry.sha256, None, "File no longer exists.")
        try:
            actual, _st = self._hash_stable(entry.path, cancel)
        except HashCancelled:
            raise
        except FileChangedDuringHash:
            return BaselineResult(entry.path, "unstable", entry.sha256, None,
                                  "The file changed while it was being checked; run the check again.")
        except OSError as exc:
            return BaselineResult(entry.path, "error", entry.sha256, None,
                                  f"Could not read file: {exc.strerror or exc}")
        if actual == entry.sha256:
            return BaselineResult(entry.path, "ok", entry.sha256, actual, "Matches baseline.")
        return BaselineResult(entry.path, "modified", entry.sha256, actual,
                              "Content differs from the baseline hash.")

    def verify(self, cancel: Optional[threading.Event] = None,
               progress: Optional[Progress] = None) -> list[BaselineResult]:
        return self.verify_report(cancel, progress).results

    def verify_report(self, cancel: Optional[threading.Event] = None,
                      progress: Optional[Progress] = None) -> VerifyReport:
        """Re-hash every baseline file (read-only apart from the 'verified' timestamp).

        Memory is bounded: files are streamed in chunks and only small result records are kept.
        """
        entries = self.list()
        report = VerifyReport([], len(entries))
        now = time.time()
        for i, entry in enumerate(entries):
            if cancel is not None and cancel.is_set():
                report.cancelled = True
                break
            if progress:
                progress(i, len(entries), entry.path)
            try:
                res = self.check_file(entry, cancel)
            except HashCancelled:
                report.cancelled = True
                break
            report.results.append(res)
            if res.status == "ok":
                self._db.execute("UPDATE baseline_files SET verified_ts=? WHERE id=?", (now, entry.id))
        if progress:
            progress(len(report.results), len(entries), "")
        return report
