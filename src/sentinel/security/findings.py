"""Security findings: stored, explainable conclusions drawn from the local audit log.

A finding is *not* a verdict. It says "these recorded events, within this time window, match
this rule" together with the evidence and a safe next step. Findings never delete, quarantine
or modify anything; the evidence events they reference are never removed by Sentinel (event
pruning skips events linked to an open finding) and a small snapshot of each referenced event is
stored with the finding so the evidence stays readable even if the original row is later deleted.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from .. import APP_NAME, __version__
from ..storage.database import Database
from ..storage.events_store import _like
from ..storage.export import csv_safe

log = logging.getLogger("sentinel.findings")

STATUSES = ("new", "acknowledged", "resolved", "dismissed")
OPEN_STATUSES = ("new", "acknowledged")
SEVERITIES = ("info", "low", "medium", "high")
SEVERITY_RANK = {s: i for i, s in enumerate(SEVERITIES)}
CATEGORIES = ("integrity", "watcher", "activity", "resource", "service", "project")
MAX_REFS_PER_FINDING = 50
MAX_REASON = 500
MAX_TEXT = 4000


class FindingError(ValueError):
    pass


@dataclass(frozen=True)
class EventRef:
    event_id: int
    ts: float
    event_type: str
    summary: str


@dataclass
class Candidate:
    """What a rule produces. Turned into / merged with a stored :class:`Finding`."""
    key: str                       # stable finding id: rule + resource (+ window for bursts)
    rule_id: str
    rule_version: int
    severity: str
    category: str
    title: str
    explanation: str
    resource: str
    confidence: str                # plain-language certainty, deliberately not a percentage
    next_step: str
    evidence: dict = field(default_factory=dict)
    events: list[EventRef] = field(default_factory=list)
    last_ts: float = 0.0
    first_ts: float = 0.0


@dataclass
class Finding:
    id: int
    finding_key: str
    rule_id: str
    rule_version: int
    severity: str
    category: str
    title: str
    explanation: str
    evidence: dict
    resource: str
    confidence: str
    next_step: str
    status: str
    dismiss_reason: str
    first_ts: float
    last_ts: float
    occurrences: int
    status_ts: float
    reopened: bool
    events: list[EventRef] = field(default_factory=list)

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES

    @property
    def local_time(self) -> str:
        return datetime.fromtimestamp(self.last_ts).strftime("%Y-%m-%d %H:%M:%S")

    def as_dict(self) -> dict[str, Any]:
        iso = lambda t: datetime.fromtimestamp(t, tz=timezone.utc).isoformat(timespec="seconds")  # noqa: E731
        return {
            "id": self.id, "finding_id": self.finding_key, "rule_id": self.rule_id,
            "rule_version": self.rule_version, "severity": self.severity, "category": self.category,
            "title": self.title, "explanation": self.explanation, "resource": self.resource,
            "confidence": self.confidence, "recommended_next_step": self.next_step,
            "status": self.status, "dismiss_reason": self.dismiss_reason or None,
            "first_observed_utc": iso(self.first_ts), "last_observed_utc": iso(self.last_ts),
            "occurrences": self.occurrences, "reopened": self.reopened,
            "evidence": self.evidence,
            "related_events": [{"event_id": r.event_id, "time_utc": iso(r.ts),
                                "event_type": r.event_type, "summary": r.summary}
                               for r in self.events],
        }


def _row(row, refs: Optional[list[EventRef]] = None) -> Finding:
    try:
        evidence = json.loads(row["evidence"]) if row["evidence"] else {}
    except ValueError:
        evidence = {"raw": row["evidence"]}
    return Finding(row["id"], row["finding_key"], row["rule_id"], row["rule_version"], row["severity"],
                   row["category"], row["title"], row["explanation"], evidence, row["resource"],
                   row["confidence"], row["next_step"], row["status"], row["dismiss_reason"],
                   row["first_ts"], row["last_ts"], row["occurrences"], row["status_ts"],
                   bool(row["reopened"]), refs or [])


class FindingStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    # ------------------------------------------------------------------ reading
    @staticmethod
    def _where(status, severity, category, since, until, text, rule_id=None) -> tuple[str, list]:
        clauses: list[str] = []
        params: list[Any] = []
        if status == "open":
            clauses.append("status IN ('new','acknowledged')")
        elif status and status != "all":
            if status not in STATUSES:
                raise FindingError(f"status must be one of {STATUSES + ('open', 'all')}")
            clauses.append("status = ?")
            params.append(status)
        if severity:
            clauses.append("severity = ?")
            params.append(severity)
        if category:
            clauses.append("category = ?")
            params.append(category)
        if rule_id:
            clauses.append("rule_id = ?")
            params.append(rule_id)
        if since is not None:
            clauses.append("last_ts >= ?")
            params.append(since)
        if until is not None:
            clauses.append("last_ts <= ?")
            params.append(until)
        if text:
            pat = _like(text)
            clauses.append("(title LIKE ? ESCAPE '\\' OR resource LIKE ? ESCAPE '\\' "
                           "OR explanation LIKE ? ESCAPE '\\' OR rule_id LIKE ? ESCAPE '\\')")
            params.extend([pat] * 4)
        return (" WHERE " + " AND ".join(clauses)) if clauses else "", params

    def query(self, *, status=None, severity=None, category=None, since=None, until=None, text=None,
              rule_id=None, limit: int = 500, offset: int = 0) -> list[Finding]:
        where, params = self._where(status, severity, category, since, until, text, rule_id)
        rows = self._db.query(
            f"SELECT * FROM findings{where} ORDER BY last_ts DESC, id DESC LIMIT ? OFFSET ?",
            params + [int(limit), int(offset)])
        return [_row(r) for r in rows]

    def count(self, **filters) -> int:
        where, params = self._where(filters.get("status"), filters.get("severity"), filters.get("category"),
                                    filters.get("since"), filters.get("until"), filters.get("text"),
                                    filters.get("rule_id"))
        row = self._db.query_one(f"SELECT COUNT(*) FROM findings{where}", params)
        return int(row[0]) if row else 0

    def get(self, finding_id: int, with_events: bool = True) -> Optional[Finding]:
        row = self._db.query_one("SELECT * FROM findings WHERE id=?", (int(finding_id),))
        if row is None:
            return None
        return _row(row, self.refs(row["id"]) if with_events else None)

    def get_by_key(self, key: str) -> Optional[Finding]:
        row = self._db.query_one("SELECT * FROM findings WHERE finding_key=?", (key,))
        return _row(row, self.refs(row["id"])) if row else None

    def refs(self, finding_id: int) -> list[EventRef]:
        rows = self._db.query("SELECT event_id, ts, event_type, summary FROM finding_events "
                              "WHERE finding_id=? ORDER BY ts, event_id", (int(finding_id),))
        return [EventRef(r["event_id"], r["ts"], r["event_type"], r["summary"]) for r in rows]

    def open_count(self) -> int:
        return self.count(status="open")

    def high_open_count(self) -> int:
        row = self._db.query_one(
            "SELECT COUNT(*) FROM findings WHERE status='new' AND severity IN ('medium','high')")
        return int(row[0]) if row else 0

    # ------------------------------------------------------------------ recording
    def record(self, cand: Candidate, now: Optional[float] = None) -> tuple[Optional[Finding], str]:
        """Insert or merge one candidate. Returns (finding, action).

        action: ``created`` | ``updated`` | ``reopened`` | ``unchanged``. Idempotent: running the
        same analysis twice over the same events changes nothing the second time (dedup).
        """
        now = time.time() if now is None else now
        if cand.severity not in SEVERITIES:
            raise FindingError(f"severity must be one of {SEVERITIES}")
        refs = cand.events[-MAX_REFS_PER_FINDING:]
        evidence = json.dumps(cand.evidence, default=str, ensure_ascii=False)[:MAX_TEXT * 4]
        existing = self._db.query_one("SELECT * FROM findings WHERE finding_key=?", (cand.key,))
        if existing is None:
            fid = self._db.execute(
                "INSERT INTO findings(finding_key, rule_id, rule_version, severity, category, title, "
                "explanation, evidence, resource, confidence, next_step, status, first_ts, last_ts, "
                "occurrences, status_ts) VALUES(?,?,?,?,?,?,?,?,?,?,?, 'new', ?,?,?,?)",
                (cand.key, cand.rule_id, cand.rule_version, cand.severity, cand.category, cand.title,
                 cand.explanation[:MAX_TEXT], evidence, cand.resource[:1024], cand.confidence,
                 cand.next_step, cand.first_ts or now, cand.last_ts or now, max(1, len(cand.events)), now))
            self._link(fid, refs)
            return self.get(fid), "created"
        known = {r["event_id"] for r in self._db.query(
            "SELECT event_id FROM finding_events WHERE finding_id=?", (existing["id"],))}
        fresh = [r for r in cand.events if r.event_id not in known]
        if not fresh:
            return _row(existing), "unchanged"
        old_rank = SEVERITY_RANK.get(existing["severity"], 0)
        new_severity = cand.severity if SEVERITY_RANK[cand.severity] > old_rank else existing["severity"]
        escalated = SEVERITY_RANK[new_severity] > old_rank
        status, reopened, status_ts = existing["status"], existing["reopened"], existing["status_ts"]
        action = "updated"
        # Condition reappeared with NEW evidence: a resolved finding reopens; a dismissed one stays
        # dismissed (the user decided) unless the situation got worse (severity escalated).
        if status == "resolved" or (status == "dismissed" and escalated):
            status, reopened, status_ts, action = "new", 1, now, "reopened"
        self._db.execute(
            "UPDATE findings SET severity=?, explanation=?, evidence=?, last_ts=?, occurrences=occurrences+?, "
            "status=?, reopened=?, status_ts=?, rule_version=? WHERE id=?",
            (new_severity, cand.explanation[:MAX_TEXT], evidence, max(existing["last_ts"], cand.last_ts or now),
             len(fresh), status, reopened, status_ts, cand.rule_version, existing["id"]))
        self._link(existing["id"], fresh[-MAX_REFS_PER_FINDING:])
        return self.get(existing["id"]), action

    def _link(self, finding_id: int, refs: list[EventRef]) -> None:
        if not refs:
            return
        with self._db.transaction() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO finding_events(finding_id, event_id, ts, event_type, summary) "
                "VALUES(?,?,?,?,?)",
                [(finding_id, r.event_id, r.ts, r.event_type, r.summary[:300]) for r in refs])

    def created_since(self, since: float) -> int:
        row = self._db.query_one("SELECT COUNT(*) FROM findings WHERE first_ts >= ?", (since,))
        return int(row[0]) if row else 0

    # ------------------------------------------------------------------ lifecycle
    def _set_status(self, ids: list[int], status: str, reason: str = "") -> int:
        if status not in STATUSES:
            raise FindingError(f"status must be one of {STATUSES}")
        reason = (reason or "").strip()[:MAX_REASON]
        if any(ord(c) < 32 and c not in "\n\t" for c in reason):
            raise FindingError("The reason must be plain text.")
        now = time.time()
        n = 0
        for i in range(0, len(ids), 500):
            part = [int(x) for x in ids[i:i + 500]]
            marks = ",".join("?" * len(part))
            n += self._db.execute_rowcount(
                f"UPDATE findings SET status=?, status_ts=?, dismiss_reason=? WHERE id IN ({marks})",
                [status, now, reason if status == "dismissed" else "", *part])
        return n

    def acknowledge(self, ids: list[int]) -> int:
        return self._set_status(ids, "acknowledged")

    def resolve(self, ids: list[int]) -> int:
        return self._set_status(ids, "resolved")

    def dismiss(self, ids: list[int], reason: str = "") -> int:
        return self._set_status(ids, "dismissed", reason)

    def reopen(self, ids: list[int]) -> int:
        return self._set_status(ids, "new")

    def open_event_ids(self) -> list[int]:
        rows = self._db.query(
            "SELECT DISTINCT fe.event_id FROM finding_events fe JOIN findings f ON f.id=fe.finding_id "
            "WHERE f.status IN ('new','acknowledged')")
        return [r[0] for r in rows]

    # ------------------------------------------------------------------ export
    def iter_for_export(self, batch: int = 200, **filters) -> Iterator[Finding]:
        offset = 0
        while True:
            chunk = self.query(limit=batch, offset=offset, **filters)
            if not chunk:
                return
            for f in chunk:
                f.events = self.refs(f.id)
                yield f
            offset += batch

    def export(self, dest: str | Path, fmt: Optional[str] = None, **filters) -> tuple[Path, int]:
        """Atomic, streamed export (bounded memory) with event references. CSV is formula-safe."""
        dest = Path(dest)
        fmt = (fmt or dest.suffix.lstrip(".")).lower()
        if fmt not in ("json", "csv"):
            raise FindingError("Export format must be 'json' or 'csv'.")
        tmp = dest.with_name(dest.name + ".tmp")
        dest.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        try:
            if fmt == "json":
                with open(tmp, "w", encoding="utf-8") as fh:
                    head = {"app": APP_NAME, "version": __version__,
                            "exported_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                            "note": "Findings are rule-based observations from the local audit log, "
                                    "not proof of malicious activity."}
                    fh.write(json.dumps(head, ensure_ascii=False)[:-1] + ', "findings": [')
                    for f in self.iter_for_export(**filters):
                        fh.write(("," if count else "") + "\n" + json.dumps(f.as_dict(), ensure_ascii=False))
                        count += 1
                    fh.write("\n], \"count\": %d}\n" % count)
            else:
                with open(tmp, "w", newline="", encoding="utf-8-sig") as fh:
                    w = csv.writer(fh)
                    w.writerow(["id", "finding_id", "rule_id", "rule_version", "severity", "category",
                                "status", "title", "resource", "first_observed_utc", "last_observed_utc",
                                "occurrences", "confidence", "recommended_next_step", "dismiss_reason",
                                "related_event_ids"])
                    for f in self.iter_for_export(**filters):
                        d = f.as_dict()
                        w.writerow([f.id, csv_safe(f.finding_key), f.rule_id, f.rule_version, f.severity,
                                    f.category, f.status, csv_safe(f.title), csv_safe(f.resource),
                                    d["first_observed_utc"], d["last_observed_utc"], f.occurrences,
                                    csv_safe(f.confidence), csv_safe(f.next_step),
                                    csv_safe(f.dismiss_reason),
                                    " ".join(str(r.event_id) for r in f.events)])
                        count += 1
            os.replace(tmp, dest)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
        return dest, count
