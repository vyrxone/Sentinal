"""Project tasks, timeline notes and recent activity (all local SQLite, V3)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from ..storage.database import Database
from ..storage.events_store import _like
from .store import ProjectError

TASK_STATUSES = ("open", "in_progress", "done")
TASK_PRIORITIES = ("low", "normal", "high")
MAX_TITLE = 200
MAX_NOTE = 5000
MAX_ACTIVITY_PER_PROJECT = 200
_PRIORITY_ORDER = {"high": 0, "normal": 1, "low": 2}


@dataclass(frozen=True)
class Task:
    id: int
    project_id: int
    title: str
    status: str
    priority: str
    due_ts: Optional[float]
    created_ts: float
    updated_ts: float
    done_ts: Optional[float]

    @property
    def overdue(self) -> bool:
        return self.status != "done" and self.due_ts is not None and self.due_ts < time.time()

    @property
    def due_text(self) -> str:
        return "" if self.due_ts is None else datetime.fromtimestamp(self.due_ts).strftime("%Y-%m-%d")


@dataclass(frozen=True)
class Note:
    id: int
    project_id: int
    body: str
    created_ts: float


def parse_due(text: str) -> Optional[float]:
    """'YYYY-MM-DD' (local end of day) -> timestamp; '' -> None. Raises ProjectError if malformed."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        day = datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        raise ProjectError("Due date must look like 2026-12-31 (or be left empty).") from None
    return day.replace(hour=23, minute=59, second=59).timestamp()


def _task(r) -> Task:
    return Task(r["id"], r["project_id"], r["title"], r["status"], r["priority"], r["due_ts"],
                r["created_ts"], r["updated_ts"], r["done_ts"])


def _clean_title(title: str) -> str:
    title = " ".join((title or "").split())
    if not title:
        raise ProjectError("A task needs a title.")
    if len(title) > MAX_TITLE:
        raise ProjectError(f"Task titles can be at most {MAX_TITLE} characters.")
    return title


class TaskStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    def _require_project(self, project_id: int) -> None:
        if self._db.query_one("SELECT 1 FROM projects WHERE id=?", (int(project_id),)) is None:
            raise ProjectError("That project no longer exists.")

    # ------------------------------------------------------------------ tasks
    def add_task(self, project_id: int, title: str, priority: str = "normal",
                 due_ts: Optional[float] = None) -> Task:
        self._require_project(project_id)
        if priority not in TASK_PRIORITIES:
            raise ProjectError(f"Priority must be one of {TASK_PRIORITIES}.")
        now = time.time()
        tid = self._db.execute(
            "INSERT INTO project_tasks(project_id,title,priority,due_ts,created_ts,updated_ts) VALUES(?,?,?,?,?,?)",
            (project_id, _clean_title(title), priority, due_ts, now, now))
        self.log(project_id, "task_added", _clean_title(title))
        return self.get_task(tid)  # type: ignore[return-value]

    def get_task(self, task_id: int) -> Optional[Task]:
        row = self._db.query_one("SELECT * FROM project_tasks WHERE id=?", (int(task_id),))
        return _task(row) if row else None

    def update_task(self, task_id: int, *, title: Optional[str] = None, status: Optional[str] = None,
                    priority: Optional[str] = None, due_ts: Optional[float] = None,
                    clear_due: bool = False) -> Task:
        cur = self.get_task(task_id)
        if cur is None:
            raise ProjectError("That task no longer exists.")
        if status is not None and status not in TASK_STATUSES:
            raise ProjectError(f"Status must be one of {TASK_STATUSES}.")
        if priority is not None and priority not in TASK_PRIORITIES:
            raise ProjectError(f"Priority must be one of {TASK_PRIORITIES}.")
        new_title = _clean_title(title) if title is not None else cur.title
        new_status = status or cur.status
        new_due = None if clear_due else (cur.due_ts if due_ts is None else due_ts)
        now = time.time()
        done_ts = (cur.done_ts or now) if new_status == "done" else None
        self._db.execute(
            "UPDATE project_tasks SET title=?, status=?, priority=?, due_ts=?, updated_ts=?, done_ts=? WHERE id=?",
            (new_title, new_status, priority or cur.priority, new_due, now, done_ts, task_id))
        if new_status != cur.status:
            self.log(cur.project_id, "task_" + new_status, new_title)
        return self.get_task(task_id)  # type: ignore[return-value]

    def delete_task(self, task_id: int) -> bool:
        cur = self.get_task(task_id)
        ok = self._db.execute_rowcount("DELETE FROM project_tasks WHERE id=?", (int(task_id),)) > 0
        if ok and cur:
            self.log(cur.project_id, "task_deleted", cur.title)
        return ok

    def list_tasks(self, project_id: Optional[int] = None, *, status: Optional[str] = None,
                   priority: Optional[str] = None, text: Optional[str] = None,
                   hide_done: bool = False, limit: int = 500) -> list[Task]:
        clauses, params = [], []
        if project_id is not None:
            clauses.append("project_id=?")
            params.append(int(project_id))
        if status and status != "all":
            clauses.append("status=?")
            params.append(status)
        if hide_done:
            clauses.append("status != 'done'")
        if priority:
            clauses.append("priority=?")
            params.append(priority)
        if text:
            clauses.append("title LIKE ? ESCAPE '\\'")
            params.append(_like(text))
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self._db.query(f"SELECT * FROM project_tasks{where} LIMIT ?", params + [int(limit) * 4])
        tasks = [_task(r) for r in rows]
        tasks.sort(key=lambda t: (t.status == "done", t.due_ts is None, t.due_ts or 0,
                                  _PRIORITY_ORDER[t.priority], t.id))
        return tasks[:int(limit)]

    def counts(self, project_id: int) -> dict:
        out = {"open": 0, "in_progress": 0, "done": 0, "overdue": 0}
        for r in self._db.query("SELECT status, COUNT(*) c FROM project_tasks WHERE project_id=? GROUP BY status",
                                (int(project_id),)):
            out[r["status"]] = r["c"]
        row = self._db.query_one("SELECT COUNT(*) FROM project_tasks WHERE project_id=? AND status!='done' "
                                 "AND due_ts IS NOT NULL AND due_ts < ?", (int(project_id), time.time()))
        out["overdue"] = int(row[0]) if row else 0
        return out

    # ------------------------------------------------------------------ notes
    def add_note(self, project_id: int, body: str) -> Note:
        self._require_project(project_id)
        body = (body or "").strip()
        if not body:
            raise ProjectError("A note cannot be empty.")
        if len(body) > MAX_NOTE:
            raise ProjectError(f"Notes can be at most {MAX_NOTE} characters.")
        nid = self._db.execute("INSERT INTO project_notes(project_id, body, created_ts) VALUES(?,?,?)",
                               (project_id, body, time.time()))
        self.log(project_id, "note_added", body.splitlines()[0][:100])
        return self.get_note(nid)  # type: ignore[return-value]

    def get_note(self, note_id: int) -> Optional[Note]:
        r = self._db.query_one("SELECT * FROM project_notes WHERE id=?", (int(note_id),))
        return Note(r["id"], r["project_id"], r["body"], r["created_ts"]) if r else None

    def list_notes(self, project_id: int, limit: int = 100) -> list[Note]:
        rows = self._db.query("SELECT * FROM project_notes WHERE project_id=? ORDER BY created_ts DESC, id DESC LIMIT ?",
                              (int(project_id), int(limit)))
        return [Note(r["id"], r["project_id"], r["body"], r["created_ts"]) for r in rows]

    def delete_note(self, note_id: int) -> bool:
        return self._db.execute_rowcount("DELETE FROM project_notes WHERE id=?", (int(note_id),)) > 0

    def last_note_ts(self, project_id: int) -> Optional[float]:
        r = self._db.query_one("SELECT MAX(created_ts) FROM project_notes WHERE project_id=?", (int(project_id),))
        return r[0] if r and r[0] is not None else None

    # --------------------------------------------------------------- activity
    def log(self, project_id: int, kind: str, text: str) -> None:
        self._db.execute("INSERT INTO project_activity(project_id, ts, kind, text) VALUES(?,?,?,?)",
                         (project_id, time.time(), kind, text[:300]))
        self._db.execute_rowcount(
            "DELETE FROM project_activity WHERE project_id=? AND id NOT IN "
            "(SELECT id FROM project_activity WHERE project_id=? ORDER BY id DESC LIMIT ?)",
            (project_id, project_id, MAX_ACTIVITY_PER_PROJECT))

    def recent_activity(self, project_id: int, limit: int = 20) -> list[tuple[float, str, str]]:
        rows = self._db.query("SELECT ts, kind, text FROM project_activity WHERE project_id=? "
                              "ORDER BY id DESC LIMIT ?", (int(project_id), int(limit)))
        return [(r["ts"], r["kind"], r["text"]) for r in rows]

    # ----------------------------------------------- explicitly recorded results
    def record_check_result(self, project_id: int, kind: str, status: str, summary: str = "") -> None:
        """User-recorded or imported test/build outcome. Sentinel never runs project commands itself."""
        if kind not in ("test", "build"):
            raise ProjectError("kind must be 'test' or 'build'.")
        if status not in ("passed", "failed", "unknown"):
            raise ProjectError("status must be passed, failed or unknown.")
        self._require_project(project_id)
        self.log(project_id, f"{kind}_result", f"{status}|{(summary or '').strip()[:200]}")

    def last_check_result(self, project_id: int, kind: str) -> Optional[dict]:
        r = self._db.query_one("SELECT ts, text FROM project_activity WHERE project_id=? AND kind=? "
                               "ORDER BY id DESC LIMIT 1", (int(project_id), f"{kind}_result"))
        if not r:
            return None
        status, _, summary = r["text"].partition("|")
        return {"ts": r["ts"], "status": status, "summary": summary}
