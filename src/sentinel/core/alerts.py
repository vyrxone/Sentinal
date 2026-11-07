"""Persistent alert state, recovery and rate-limited notifications (no Qt; unit-testable).

* ``alert_state`` rows say which threshold alerts are currently active.
* On the next start any row still 'active' becomes 'interrupted' with an explicit audit event,
  because Sentinel cannot know what happened while it was not running.
* Notifications are optional and rate-limited per alert key; the audit log always has the event.
"""

from __future__ import annotations

import time
from typing import Callable

from ..storage.database import Database


class NotifyLimiter:
    def __init__(self, min_interval_s: float = 60.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.min_interval_s = float(min_interval_s)
        self._clock = clock
        self._last: dict[str, float] = {}

    def allow(self, key: str) -> bool:
        now = self._clock()
        last = self._last.get(key)
        if last is not None and now - last < self.min_interval_s:
            return False
        self._last[key] = now
        return True


class AlertState:
    def __init__(self, db: Database) -> None:
        self._db = db

    def open(self, key: str, severity: str, message: str) -> None:
        self._db.execute(
            "INSERT INTO alert_state(key, severity, message, opened_ts, state, closed_ts) "
            "VALUES(?,?,?,?, 'active', NULL) ON CONFLICT(key) DO UPDATE SET severity=excluded.severity, "
            "message=excluded.message, opened_ts=excluded.opened_ts, state='active', closed_ts=NULL",
            (key, severity, message, time.time()))

    def close(self, key: str, state: str = "recovered") -> bool:
        return self._db.execute_rowcount(
            "UPDATE alert_state SET state=?, closed_ts=? WHERE key=? AND state='active'",
            (state, time.time(), key)) > 0

    def active(self) -> list[dict]:
        return [dict(r) for r in self._db.query(
            "SELECT key, severity, message, opened_ts FROM alert_state WHERE state='active' ORDER BY opened_ts")]

    def history(self, limit: int = 50) -> list[dict]:
        return [dict(r) for r in self._db.query(
            "SELECT * FROM alert_state ORDER BY COALESCE(closed_ts, opened_ts) DESC LIMIT ?", (int(limit),))]

    def interrupt_stale(self) -> list[dict]:
        """Called once at start-up: alerts left 'active' by the previous run are marked interrupted."""
        stale = self.active()
        for row in stale:
            self.close(row["key"], "interrupted")
        return stale
