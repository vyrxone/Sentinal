"""Schema creation and forward-only migrations tracked with PRAGMA user_version.

Existing databases are copied to ``<db>.pre-v<N>.bak`` before any migration runs.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Callable

from .database import Database, DatabaseError

log = logging.getLogger("sentinel.db")


def _m1_initial(conn: sqlite3.Connection) -> None:
    for stmt in (
        """CREATE TABLE settings (
               key TEXT PRIMARY KEY,
               value TEXT NOT NULL,
               updated_ts REAL NOT NULL)""",
        """CREATE TABLE events (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               ts REAL NOT NULL,
               category TEXT NOT NULL,
               event_type TEXT NOT NULL,
               severity TEXT NOT NULL DEFAULT 'info',
               path TEXT,
               message TEXT NOT NULL,
               details TEXT)""",
        """CREATE TABLE watched_folders (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               path TEXT NOT NULL,
               path_key TEXT NOT NULL UNIQUE,
               recursive INTEGER NOT NULL DEFAULT 1,
               enabled INTEGER NOT NULL DEFAULT 1,
               added_ts REAL NOT NULL)""",
        """CREATE TABLE baseline_files (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               path TEXT NOT NULL,
               path_key TEXT NOT NULL UNIQUE,
               sha256 TEXT NOT NULL,
               size INTEGER NOT NULL,
               mtime REAL,
               added_ts REAL NOT NULL,
               verified_ts REAL)""",
        """CREATE TABLE projects (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               name TEXT NOT NULL,
               path TEXT NOT NULL DEFAULT '',
               description TEXT NOT NULL DEFAULT '',
               status TEXT NOT NULL DEFAULT 'Active',
               notes TEXT NOT NULL DEFAULT '',
               created_ts REAL NOT NULL,
               updated_ts REAL NOT NULL)""",
    ):
        conn.execute(stmt)


def _m2_indexes(conn: sqlite3.Connection) -> None:
    for stmt in (
        "CREATE INDEX idx_events_ts ON events(ts)",
        "CREATE INDEX idx_events_category_ts ON events(category, ts)",
        "CREATE INDEX idx_events_type ON events(event_type)",
        "CREATE INDEX idx_projects_name ON projects(name)",
    ):
        conn.execute(stmt)


def _m3_alert_management(conn: sqlite3.Connection) -> None:
    """V2: acknowledge/archive for events, persistent alert state, watch-folder exclusions."""
    for stmt in (
        "ALTER TABLE events ADD COLUMN acknowledged INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE events ADD COLUMN acked_ts REAL",
        "ALTER TABLE events ADD COLUMN archived INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE watched_folders ADD COLUMN excludes TEXT NOT NULL DEFAULT ''",
        """CREATE TABLE alert_state (
               key TEXT PRIMARY KEY,
               severity TEXT NOT NULL,
               message TEXT NOT NULL,
               opened_ts REAL NOT NULL,
               state TEXT NOT NULL DEFAULT 'active',
               closed_ts REAL)""",
        "CREATE INDEX idx_events_status ON events(archived, acknowledged, severity)",
    ):
        conn.execute(stmt)


def _m4_findings_and_projects(conn: sqlite3.Connection) -> None:
    """V3: security findings (+ evidence snapshots), project tasks/notes/health, extra indexes.

    Purely additive: no V2 table is rewritten, so V2 data is preserved byte-for-byte.
    """
    for stmt in (
        """CREATE TABLE findings (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               finding_key TEXT NOT NULL UNIQUE,
               rule_id TEXT NOT NULL,
               rule_version INTEGER NOT NULL,
               severity TEXT NOT NULL,
               category TEXT NOT NULL,
               title TEXT NOT NULL,
               explanation TEXT NOT NULL,
               evidence TEXT NOT NULL DEFAULT '{}',
               resource TEXT NOT NULL DEFAULT '',
               confidence TEXT NOT NULL,
               next_step TEXT NOT NULL,
               status TEXT NOT NULL DEFAULT 'new',
               dismiss_reason TEXT NOT NULL DEFAULT '',
               first_ts REAL NOT NULL,
               last_ts REAL NOT NULL,
               occurrences INTEGER NOT NULL DEFAULT 1,
               status_ts REAL NOT NULL,
               reopened INTEGER NOT NULL DEFAULT 0)""",
        """CREATE TABLE finding_events (
               finding_id INTEGER NOT NULL REFERENCES findings(id) ON DELETE CASCADE,
               event_id INTEGER NOT NULL,
               ts REAL NOT NULL,
               event_type TEXT NOT NULL,
               summary TEXT NOT NULL,
               PRIMARY KEY (finding_id, event_id))""",
        "CREATE INDEX idx_findings_status ON findings(status, severity, last_ts)",
        "CREATE INDEX idx_findings_rule ON findings(rule_id, last_ts)",
        "CREATE INDEX idx_finding_events_event ON finding_events(event_id)",
        """CREATE TABLE project_tasks (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
               title TEXT NOT NULL,
               status TEXT NOT NULL DEFAULT 'open',
               priority TEXT NOT NULL DEFAULT 'normal',
               due_ts REAL,
               created_ts REAL NOT NULL,
               updated_ts REAL NOT NULL,
               done_ts REAL)""",
        "CREATE INDEX idx_tasks_project ON project_tasks(project_id, status)",
        """CREATE TABLE project_notes (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
               body TEXT NOT NULL,
               created_ts REAL NOT NULL)""",
        "CREATE INDEX idx_notes_project ON project_notes(project_id, created_ts)",
        """CREATE TABLE project_health (
               project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
               checked_ts REAL NOT NULL,
               last_ok_ts REAL,
               report TEXT NOT NULL)""",
        """CREATE TABLE project_activity (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
               ts REAL NOT NULL,
               kind TEXT NOT NULL,
               text TEXT NOT NULL)""",
        "CREATE INDEX idx_activity_project ON project_activity(project_id, ts)",
        "CREATE INDEX idx_events_sev_ts ON events(severity, ts)",
    ):
        conn.execute(stmt)


def _m5_ai_conversations(conn: sqlite3.Connection) -> None:
    """V4: optional local AI conversation history (only written when the user enables it). Purely additive.

    No API key, provider secret or credential is ever stored in the database."""
    for stmt in (
        """CREATE TABLE ai_conversations (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               title TEXT NOT NULL,
               created_ts REAL NOT NULL,
               updated_ts REAL NOT NULL)""",
        """CREATE TABLE ai_messages (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               conversation_id INTEGER NOT NULL REFERENCES ai_conversations(id) ON DELETE CASCADE,
               role TEXT NOT NULL,
               content TEXT NOT NULL,
               display TEXT NOT NULL DEFAULT '',
               ts REAL NOT NULL,
               provider TEXT NOT NULL DEFAULT '',
               model TEXT NOT NULL DEFAULT '',
               meta TEXT NOT NULL DEFAULT '{}')""",
        "CREATE INDEX idx_ai_messages_conv ON ai_messages(conversation_id, id)",
        "CREATE INDEX idx_ai_conv_updated ON ai_conversations(updated_ts)",
    ):
        conn.execute(stmt)


MIGRATIONS: list[tuple[int, Callable[[sqlite3.Connection], None]]] = [
    (1, _m1_initial),
    (2, _m2_indexes),
    (3, _m3_alert_management),
    (4, _m4_findings_and_projects),
    (5, _m5_ai_conversations),
]
LATEST_VERSION = MIGRATIONS[-1][0]


def migrate(db: Database) -> None:
    current = db.user_version
    if current > LATEST_VERSION:
        raise DatabaseError(
            f"This database (schema v{current}) was created by a newer Sentinel "
            f"(this build understands up to v{LATEST_VERSION}). Update Sentinel or "
            "choose another data folder."
        )
    pending = [(v, fn) for v, fn in MIGRATIONS if v > current]
    if not pending:
        return
    if current > 0 and db.path != ":memory:":
        backup = f"{db.path}.pre-v{current}.bak"
        db.backup_to(backup)
        log.info("pre-migration backup written to %s", backup)
    for version, fn in pending:
        log.info("applying schema migration %d", version)
        try:
            with db.transaction() as conn:       # a failure rolls this step back completely
                fn(conn)
                conn.execute(f"PRAGMA user_version = {int(version)}")
        except (sqlite3.Error, DatabaseError) as exc:
            raise DatabaseError(
                f"Upgrading the database to schema v{version} failed and was rolled back "
                f"(still at v{db.user_version}): {exc}. Your data was not changed"
                + (f"; a backup is at {db.path}.pre-v{current}.bak." if current > 0 else ".")) from exc
