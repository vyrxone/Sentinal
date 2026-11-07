"""CRUD for user-entered projects. Nothing is discovered or scanned automatically."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Optional

from ..storage.database import Database

STATUSES = ("Planned", "Active", "Paused", "Done", "Archived")
MAX_NAME, MAX_PATH, MAX_DESC, MAX_NOTES = 120, 1024, 2000, 20000


class ProjectError(ValueError):
    """Validation problem with a message suitable for the user."""


@dataclass(frozen=True)
class Project:
    id: int
    name: str
    path: str
    description: str
    status: str
    notes: str
    created_ts: float
    updated_ts: float

    @property
    def path_state(self) -> str:
        return path_state(self.path)


def path_state(path: str) -> str:
    """unset | ok | missing | not_a_directory | inaccessible"""
    if not path:
        return "unset"
    try:
        if not os.path.exists(path):
            return "missing"
        if not os.path.isdir(path):
            return "not_a_directory"
        os.listdir(path)
        return "ok"
    except OSError:
        return "inaccessible"


PATH_STATE_TEXT = {
    "unset": "No folder set",
    "ok": "Folder available",
    "missing": "Folder not found (moved, deleted or drive disconnected)",
    "not_a_directory": "Path is not a folder",
    "inaccessible": "Folder cannot be read (permission denied)",
}


def _clean(name: str, path: str, description: str, status: str, notes: str) -> tuple:
    name = (name or "").strip()
    if not name:
        raise ProjectError("Please enter a project name.")
    if len(name) > MAX_NAME:
        raise ProjectError(f"The project name is too long (maximum {MAX_NAME} characters).")
    path = (path or "").strip()
    if "\0" in path or len(path) > MAX_PATH:
        raise ProjectError("The project path is not valid.")
    if path:
        path = os.path.abspath(os.path.expanduser(path))
    if status not in STATUSES:
        raise ProjectError(f"Status must be one of: {', '.join(STATUSES)}.")
    description = (description or "").strip()
    notes = notes or ""
    if len(description) > MAX_DESC:
        raise ProjectError(f"The description is too long (maximum {MAX_DESC} characters).")
    if len(notes) > MAX_NOTES:
        raise ProjectError(f"The notes are too long (maximum {MAX_NOTES} characters).")
    return name, path, description, status, notes


def _row(row) -> Project:
    return Project(row["id"], row["name"], row["path"], row["description"], row["status"],
                   row["notes"], row["created_ts"], row["updated_ts"])


class ProjectStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    def list(self) -> list[Project]:
        return [_row(r) for r in self._db.query("SELECT * FROM projects ORDER BY name COLLATE NOCASE")]

    def get(self, project_id: int) -> Optional[Project]:
        row = self._db.query_one("SELECT * FROM projects WHERE id=?", (project_id,))
        return _row(row) if row else None

    def add(self, name: str, path: str = "", description: str = "", status: str = "Active",
            notes: str = "") -> Project:
        name, path, description, status, notes = _clean(name, path, description, status, notes)
        now = time.time()
        new_id = self._db.execute(
            "INSERT INTO projects(name, path, description, status, notes, created_ts, updated_ts) "
            "VALUES(?,?,?,?,?,?,?)", (name, path, description, status, notes, now, now))
        project = self.get(new_id)
        assert project is not None
        return project

    def update(self, project_id: int, name: str, path: str = "", description: str = "",
               status: str = "Active", notes: str = "") -> Project:
        name, path, description, status, notes = _clean(name, path, description, status, notes)
        changed = self._db.execute_rowcount(
            "UPDATE projects SET name=?, path=?, description=?, status=?, notes=?, updated_ts=? "
            "WHERE id=?", (name, path, description, status, notes, time.time(), project_id))
        if not changed:
            raise ProjectError("That project no longer exists.")
        project = self.get(project_id)
        assert project is not None
        return project

    def remove(self, project_id: int) -> bool:
        """Removes only the Sentinel record - never the project's files."""
        return self._db.execute_rowcount("DELETE FROM projects WHERE id=?", (project_id,)) > 0
