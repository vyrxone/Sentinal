"""Thread-safe wrapper around one SQLite connection (standard library only)."""

from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

log = logging.getLogger("sentinel.db")


class DatabaseError(Exception):
    """Raised for any storage failure, with a message safe to show to the user."""


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        self.journal_mode = "unknown"
        self._open()
        try:
            from . import migrations  # local import avoids a cycle

            self._check_integrity()
            migrations.migrate(self)
        except BaseException:
            self.close()      # never leak the connection when startup fails (V1 defect A1)
            raise

    def _check_integrity(self) -> None:
        """A corrupt file gives a clear error instead of an obscure SQL failure later."""
        try:
            row = self._connection().execute("PRAGMA quick_check").fetchone()
        except sqlite3.DatabaseError as exc:
            msg = str(exc).lower()
            if "locked" in msg or "busy" in msg:
                raise DatabaseError("The local database is locked by another program. Close any other "
                                    f"Sentinel or database tool using it and try again. ({exc})") from exc
            raise DatabaseError(f"The local database file is damaged or not a Sentinel database ({exc}). "
                                "Move or rename it to start fresh; nothing was deleted.") from exc
        if row is None or str(row[0]).lower() != "ok":
            raise DatabaseError(f"The local database failed its integrity check ({row[0] if row else '?'}). "
                                "Move or rename it to start fresh; nothing was deleted.")

    # -- connection lifecycle -------------------------------------------------
    def _open(self) -> None:
        conn = None
        try:
            if self.path != ":memory:":
                Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            # isolation_level=None -> autocommit; transactions are explicit.
            conn = sqlite3.connect(self.path, check_same_thread=False,
                                   timeout=10, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA synchronous=NORMAL")
            if self.path != ":memory:":
                try:                      # WAL is faster and more crash-safe; fall back silently if unsupported
                    row = conn.execute("PRAGMA journal_mode=WAL").fetchone()
                    self.journal_mode = str(row[0]).lower() if row else "unknown"
                except sqlite3.Error:
                    self.journal_mode = "default"
        except (sqlite3.Error, OSError) as exc:
            if conn is not None:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass
            low = str(exc).lower()
            if "not a database" in low or "malformed" in low:
                raise DatabaseError(f"The local database file is damaged or not a Sentinel database ({exc}). "
                                    "Move or rename it to start fresh; nothing was deleted.") from exc
            if "locked" in low or "busy" in low:
                raise DatabaseError("The local database is locked by another program. Close any other "
                                    f"Sentinel or database tool using it and try again. ({exc})") from exc
            hint = " The data folder may be missing, read-only or locked." if isinstance(exc, (OSError, sqlite3.OperationalError)) else ""
            raise DatabaseError(f"Could not open the local database ({self.path}): {exc}.{hint}") from exc
        self._conn = conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except sqlite3.Error:
                    log.exception("error closing database")
                self._conn = None

    @property
    def is_open(self) -> bool:
        return self._conn is not None

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            raise DatabaseError("The local database is closed.")
        return self._conn

    # -- queries ----------------------------------------------------------------
    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        """Run one write statement (autocommit). Returns lastrowid."""
        with self._lock:
            try:
                return self._connection().execute(sql, params).lastrowid or 0
            except sqlite3.Error as exc:
                raise DatabaseError(f"Database write failed: {exc}") from exc

    def execute_rowcount(self, sql: str, params: Sequence[Any] = ()) -> int:
        with self._lock:
            try:
                return self._connection().execute(sql, params).rowcount
            except sqlite3.Error as exc:
                raise DatabaseError(f"Database write failed: {exc}") from exc

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            try:
                return self._connection().execute(sql, params).fetchall()
            except sqlite3.Error as exc:
                raise DatabaseError(f"Database read failed: {exc}") from exc

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = self._connection()
            try:
                conn.execute("BEGIN")
            except sqlite3.Error as exc:
                raise DatabaseError(f"Could not start a transaction: {exc}") from exc
            try:
                yield conn
            except BaseException:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
            else:
                try:
                    conn.execute("COMMIT")
                except sqlite3.Error as exc:
                    raise DatabaseError(f"Could not commit: {exc}") from exc

    # -- schema version -------------------------------------------------------
    @property
    def user_version(self) -> int:
        row = self.query_one("PRAGMA user_version")
        return int(row[0]) if row else 0

    # -- backup ---------------------------------------------------------------
    def backup_to(self, dest: str | Path) -> Path:
        """Consistent copy of the live database using SQLite's backup API."""
        dest = Path(dest)
        with self._lock:
            conn = self._connection()
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                target = sqlite3.connect(str(dest))
                try:
                    conn.backup(target)
                finally:
                    target.close()
            except (sqlite3.Error, OSError) as exc:
                raise DatabaseError(f"Backup failed: {exc}") from exc
        return dest
