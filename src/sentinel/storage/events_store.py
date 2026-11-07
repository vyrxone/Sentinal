"""Audit / security event log stored in SQLite."""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from .database import Database, DatabaseError

log = logging.getLogger("sentinel.events")

SEVERITIES = ("info", "notice", "warning")
CATEGORIES = ("system", "security", "project", "settings", "app")
PRUNE_EVERY = 250
STATUSES = ("active", "unacknowledged", "acknowledged", "archived", "all")
ATTENTION = ("warning",)       # severities that are never pruned while unacknowledged


@dataclass
class Event:
    id: int
    ts: float
    category: str
    event_type: str
    severity: str
    path: Optional[str]
    message: str
    details: Optional[dict] = field(default=None)
    acknowledged: bool = False
    acked_ts: Optional[float] = None
    archived: bool = False

    @property
    def iso(self) -> str:
        return datetime.fromtimestamp(self.ts, tz=timezone.utc).isoformat(timespec="seconds")

    @property
    def local_time(self) -> str:
        return datetime.fromtimestamp(self.ts).strftime("%Y-%m-%d %H:%M:%S")

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "time_utc": self.iso,
            "category": self.category,
            "event_type": self.event_type,
            "severity": self.severity,
            "path": self.path,
            "message": self.message,
            "details": self.details,
            "acknowledged": self.acknowledged,
            "archived": self.archived,
        }


def _row_to_event(row) -> Event:
    details = None
    if row["details"]:
        try:
            details = json.loads(row["details"])
        except ValueError:
            details = {"raw": row["details"]}
    keys = row.keys()
    return Event(row["id"], row["ts"], row["category"], row["event_type"], row["severity"],
                 row["path"], row["message"], details,
                 bool(row["acknowledged"]) if "acknowledged" in keys else False,
                 row["acked_ts"] if "acked_ts" in keys else None,
                 bool(row["archived"]) if "archived" in keys else False)


SORT_COLUMNS = {
    "ts": "ts",
    "severity": "CASE severity WHEN 'warning' THEN 2 WHEN 'notice' THEN 1 ELSE 0 END",
    "category": "category", "event_type": "event_type", "path": "path", "message": "message",
}


def _like(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


class EventStore:
    def __init__(self, db: Database, max_events: int = 50000) -> None:
        self._db = db
        self.max_events = max_events
        self._listeners: list[Callable[[Event], None]] = []
        self._added = 0
        self.pruned_total = 0
        self._lock = threading.Lock()

    def subscribe(self, listener: Callable[[Event], None]) -> None:
        """Listeners may be called from background threads."""
        self._listeners.append(listener)

    def add(self, category: str, event_type: str, message: str, *, severity: str = "info",
            path: Optional[str] = None, details: Optional[dict] = None,
            ts: Optional[float] = None) -> Event:
        if severity not in SEVERITIES:
            raise ValueError(f"severity must be one of {SEVERITIES}")
        ts = time.time() if ts is None else ts
        details_json = json.dumps(details, default=str) if details else None
        new_id = self._db.execute(
            "INSERT INTO events(ts, category, event_type, severity, path, message, details) "
            "VALUES(?,?,?,?,?,?,?)",
            (ts, category, event_type, severity, path, message, details_json),
        )
        event = Event(new_id, ts, category, event_type, severity, path, message, details or None)
        with self._lock:
            self._added += 1
            do_prune = self._added % PRUNE_EVERY == 0
        if do_prune:
            try:
                self.prune()
            except DatabaseError:
                log.exception("event pruning failed")
        for listener in list(self._listeners):
            try:
                listener(event)
            except Exception:
                log.exception("event listener failed")
        return event

    @staticmethod
    def _where(category, event_type, severity, since, until, text, status=None) -> tuple[str, list]:
        clauses: list[str] = []
        params: list[Any] = []
        if status == "active":
            clauses.append("archived = 0")
        elif status == "unacknowledged":
            clauses.append("archived = 0 AND acknowledged = 0 AND severity != 'info'")
        elif status == "acknowledged":
            clauses.append("archived = 0 AND acknowledged = 1")
        elif status == "archived":
            clauses.append("archived = 1")
        elif status not in (None, "all"):
            raise ValueError(f"status must be one of {STATUSES}")
        if category:
            clauses.append("category = ?")
            params.append(category)
        if event_type:
            clauses.append("event_type = ?")
            params.append(event_type)
        if severity:
            clauses.append("severity = ?")
            params.append(severity)
        if since is not None:
            clauses.append("ts >= ?")
            params.append(since)
        if until is not None:
            clauses.append("ts <= ?")
            params.append(until)
        if text:
            pat = _like(text)
            clauses.append("(message LIKE ? ESCAPE '\\' OR path LIKE ? ESCAPE '\\' "
                           "OR event_type LIKE ? ESCAPE '\\')")
            params.extend([pat, pat, pat])
        return (" WHERE " + " AND ".join(clauses)) if clauses else "", params

    def query(self, *, category=None, event_type=None, severity=None, since=None, until=None,
              text=None, status=None, limit: int = 1000, offset: int = 0,
              newest_first: bool = True, sort: str = "ts") -> list[Event]:
        """One page of events. *sort* is checked against an allow-list (never interpolated from input)."""
        if sort not in SORT_COLUMNS:
            raise ValueError(f"sort must be one of {tuple(SORT_COLUMNS)}")
        where, params = self._where(category, event_type, severity, since, until, text, status)
        order = "DESC" if newest_first else "ASC"
        sql = (f"SELECT * FROM events{where} ORDER BY {SORT_COLUMNS[sort]} {order}, id {order} "
               "LIMIT ? OFFSET ?")
        rows = self._db.query(sql, params + [int(limit), int(offset)])
        return [_row_to_event(r) for r in rows]

    def iter_batches(self, *, batch: int = 1000, max_rows: int = 2_000_000, **filters):
        """Yield lists of events matching *filters* in id order using keyset paging.

        Bounded memory (one batch at a time) and stable while new events arrive (no OFFSET skew).
        """
        where, params = self._where(filters.get("category"), filters.get("event_type"), filters.get("severity"),
                                    filters.get("since"), filters.get("until"), filters.get("text"),
                                    filters.get("status"))
        last, seen = 0, 0
        glue = " AND " if where else " WHERE "
        while seen < max_rows:
            rows = self._db.query(f"SELECT * FROM events{where}{glue}id > ? ORDER BY id ASC LIMIT ?",
                                  params + [last, int(min(batch, max_rows - seen))])
            if not rows:
                return
            last = rows[-1]["id"]
            seen += len(rows)
            yield [_row_to_event(r) for r in rows]

    def query_types(self, event_types: tuple[str, ...], since: float, limit: int = 5000) -> list[Event]:
        """Events of the given types since *since*, oldest first. Bounded: the newest *limit* rows."""
        marks = ",".join("?" * len(event_types))
        rows = self._db.query(
            f"SELECT * FROM events WHERE ts >= ? AND event_type IN ({marks}) "
            "ORDER BY ts DESC, id DESC LIMIT ?", [since, *event_types, int(limit)])
        return [_row_to_event(r) for r in reversed(rows)]

    def count(self, *, category=None, event_type=None, severity=None, since=None, until=None,
              text=None, status=None) -> int:
        where, params = self._where(category, event_type, severity, since, until, text, status)
        row = self._db.query_one(f"SELECT COUNT(*) FROM events{where}", params)
        return int(row[0]) if row else 0

    def distinct_types(self) -> list[str]:
        return [r[0] for r in self._db.query("SELECT DISTINCT event_type FROM events ORDER BY 1")]

    def prune(self, max_events: Optional[int] = None) -> int:
        """Keep at most *max_events* rows, deleting the oldest first.

        Unacknowledged warnings, and events referenced by an open security finding (V3), are never
        pruned (evidence must not vanish silently); if only protected rows remain the table is
        allowed to exceed the cap.
        """
        keep = int(max_events or self.max_events)
        total = self.count()
        excess = total - keep
        if excess <= 0:
            return 0
        removed = self._db.execute_rowcount(
            "DELETE FROM events WHERE id IN (SELECT id FROM events WHERE NOT "
            "(severity = 'warning' AND acknowledged = 0 AND archived = 0) "
            "AND id NOT IN (SELECT fe.event_id FROM finding_events fe JOIN findings f "
            "ON f.id = fe.finding_id WHERE f.status IN ('new','acknowledged')) "
            "ORDER BY id ASC LIMIT ?)", (excess,))
        if removed:
            self.pruned_total += removed        # surfaced in diagnostics; the cap itself is user-configured
            log.info("pruned %d old event(s); unacknowledged warnings were kept", removed)
        return removed

    # ---- V2 alert management --------------------------------------------------
    def acknowledge(self, ids: list[int], acknowledged: bool = True) -> int:
        if not ids:
            return 0
        ts = time.time() if acknowledged else None
        n = 0
        for chunk in range(0, len(ids), 500):
            part = [int(i) for i in ids[chunk:chunk + 500]]
            marks = ",".join("?" * len(part))
            n += self._db.execute_rowcount(
                f"UPDATE events SET acknowledged=?, acked_ts=? WHERE id IN ({marks})",
                [1 if acknowledged else 0, ts, *part])
        return n

    def acknowledge_matching(self, acknowledged: bool = True, **filters) -> int:
        filters.pop("limit", None)
        where, params = self._where(filters.get("category"), filters.get("event_type"),
                                    filters.get("severity"), filters.get("since"), filters.get("until"),
                                    filters.get("text"), filters.get("status") or "active")
        ts = time.time() if acknowledged else None
        return self._db.execute_rowcount(f"UPDATE events SET acknowledged=?, acked_ts=?{where}",
                                         [1 if acknowledged else 0, ts, *params])

    def archive(self, ids: list[int], archived: bool = True) -> int:
        if not ids:
            return 0
        n = 0
        for chunk in range(0, len(ids), 500):
            part = [int(i) for i in ids[chunk:chunk + 500]]
            marks = ",".join("?" * len(part))
            n += self._db.execute_rowcount(f"UPDATE events SET archived=? WHERE id IN ({marks})",
                                           [1 if archived else 0, *part])
        return n

    def archive_matching(self, **filters) -> int:
        where, params = self._where(filters.get("category"), filters.get("event_type"),
                                    filters.get("severity"), filters.get("since"), filters.get("until"),
                                    filters.get("text"), "active")
        return self._db.execute_rowcount(f"UPDATE events SET archived=1{where}", params)

    def delete_archived(self) -> int:
        """Permanently remove archived events. The UI must ask for confirmation first."""
        return self._db.execute_rowcount("DELETE FROM events WHERE archived = 1")

    def unacknowledged_count(self) -> int:
        return self.count(status="unacknowledged")
