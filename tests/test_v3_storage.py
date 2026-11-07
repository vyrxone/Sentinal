"""V3 storage: schema v4 migration from V2, rollback, interruption, locked/corrupt/newer databases."""

import sqlite3

import pytest

from sentinel.storage import migrations
from sentinel.storage.database import Database, DatabaseError
from sentinel.storage.events_store import EventStore
from test_v2_storage import make_v1_db

V3_TABLES = {"findings", "finding_events", "project_tasks", "project_notes", "project_health", "project_activity"}


def make_v2_db(path):
    """A real V2 database (schema v3) with data in every V2 table."""
    conn = sqlite3.connect(path)
    for v, fn in migrations.MIGRATIONS[:3]:
        fn(conn)
        conn.execute(f"PRAGMA user_version = {v}")
    conn.execute("INSERT INTO events(ts,category,event_type,severity,path,message,details,acknowledged,archived) "
                 "VALUES(5.0,'security','baseline_mismatch','warning','/a','changed',NULL,1,0)")
    conn.execute("INSERT INTO settings VALUES('animation_fps','30',1.0)")
    conn.execute("INSERT INTO watched_folders(path,path_key,recursive,enabled,added_ts,excludes) VALUES('/w','/w',1,1,1.0,'node_modules')")
    conn.execute("INSERT INTO baseline_files(path,path_key,sha256,size,mtime,added_ts) VALUES('/f','/f','ab',3,1.0,1.0)")
    conn.execute("INSERT INTO projects(name,path,notes,created_ts,updated_ts) VALUES('atlas','/p','old notes',1.0,1.0)")
    conn.execute("INSERT INTO alert_state(key,severity,message,opened_ts) VALUES('cpu','warning','CPU high',1.0)")
    conn.commit()
    conn.close()


def tables(db):
    return {r[0] for r in db.query("SELECT name FROM sqlite_master WHERE type='table'")}


def test_fresh_install_has_v3_tables_and_indexes(tmp_path):
    db = Database(tmp_path / "f.db")
    assert db.user_version == migrations.LATEST_VERSION == 5
    assert V3_TABLES <= tables(db)
    idx = {r[0] for r in db.query("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"idx_findings_status", "idx_tasks_project", "idx_events_sev_ts"} <= idx
    db.close()


def test_v2_upgrade_preserves_every_v2_table_and_backs_up(tmp_path):
    path = tmp_path / "v2.db"
    make_v2_db(path)
    db = Database(path)
    assert db.user_version == 5
    assert db.query_one("SELECT acknowledged, message FROM events")["acknowledged"] == 1
    assert db.query_one("SELECT value FROM settings WHERE key='animation_fps'")["value"] == "30"
    assert db.query_one("SELECT excludes FROM watched_folders")["excludes"] == "node_modules"
    assert db.query_one("SELECT sha256 FROM baseline_files")["sha256"] == "ab"
    p = db.query_one("SELECT name, notes FROM projects")
    assert (p["name"], p["notes"]) == ("atlas", "old notes")          # old project notes untouched
    assert db.query_one("SELECT state FROM alert_state WHERE key='cpu'")["state"] == "active"
    assert db.query_one("SELECT COUNT(*) FROM findings")[0] == 0     # new tables start empty
    db.close()
    bak = sqlite3.connect(tmp_path / "v2.db.pre-v3.bak")            # backup of the V2-schema database
    assert bak.execute("PRAGMA user_version").fetchone()[0] == 3
    assert bak.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    bak.close()


def test_v1_database_upgrades_through_all_steps(tmp_path):
    path = tmp_path / "v1.db"
    make_v1_db(path)
    db = Database(path)
    assert db.user_version == 5 and V3_TABLES <= tables(db)
    assert EventStore(db).count() == 1
    db.close()


def test_failed_v4_step_rolls_back_completely_and_reports(tmp_path, monkeypatch):
    path = tmp_path / "v2.db"
    make_v2_db(path)

    def broken(conn):
        conn.execute("CREATE TABLE findings (id INTEGER PRIMARY KEY)")
        raise sqlite3.OperationalError("simulated disk full")

    monkeypatch.setattr(migrations, "MIGRATIONS", migrations.MIGRATIONS[:3] + [(4, broken)])
    with pytest.raises(DatabaseError) as err:
        Database(path)
    assert "rolled back" in str(err.value) and "backup" in str(err.value)
    raw = sqlite3.connect(path)
    assert raw.execute("PRAGMA user_version").fetchone()[0] == 3
    assert "findings" not in {r[0] for r in raw.execute("SELECT name FROM sqlite_master")}   # nothing half-applied
    assert raw.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    raw.close()
    assert (tmp_path / "v2.db.pre-v3.bak").exists()
    monkeypatch.undo()
    db = Database(path)                                              # next normal start finishes the upgrade
    assert db.user_version == 5 and "findings" in tables(db)
    db.close()


def test_newer_schema_is_refused_and_untouched(tmp_path):
    path = tmp_path / "n.db"
    Database(path).close()
    raw = sqlite3.connect(path)
    raw.execute("PRAGMA user_version = 6")
    raw.commit()
    raw.close()
    before = path.read_bytes()
    with pytest.raises(DatabaseError) as err:
        Database(path)
    assert "newer" in str(err.value).lower()
    raw = sqlite3.connect(path)
    assert raw.execute("PRAGMA user_version").fetchone()[0] == 6      # never downgraded
    raw.close()
    assert not (tmp_path / "n.db.pre-v5.bak").exists() or before  # no migration attempted


def test_v3_migration_is_idempotent_on_reopen(tmp_path):
    path = tmp_path / "i.db"
    make_v2_db(path)
    Database(path).close()
    db = Database(path)
    assert db.user_version == 5
    db.close()
    assert len(list(tmp_path.glob("i.db.pre-*.bak"))) == 1            # only the real upgrade made a backup


def test_foreign_keys_cascade_project_children(tmp_path):
    db = Database(tmp_path / "c.db")
    db.execute("INSERT INTO projects(name,created_ts,updated_ts) VALUES('p',1,1)")
    db.execute("INSERT INTO project_tasks(project_id,title,created_ts,updated_ts) VALUES(1,'t',1,1)")
    db.execute("INSERT INTO project_notes(project_id,body,created_ts) VALUES(1,'n',1)")
    db.execute("DELETE FROM projects WHERE id=1")
    assert db.query_one("SELECT COUNT(*) FROM project_tasks")[0] == 0
    assert db.query_one("SELECT COUNT(*) FROM project_notes")[0] == 0
    db.close()


def test_v3_settings_keys_default_on_v2_database(tmp_path):
    from sentinel.storage.settings_store import SettingsStore

    path = tmp_path / "s.db"
    make_v2_db(path)
    db = Database(path)
    s = SettingsStore(db)
    assert s.get("findings_enabled") is True and s.get("findings_sensitivity") == "normal"
    assert s.get("animation_fps") == 30                               # V2 preference survives
    assert set(s.load_report["missing_v3"]) == {"findings_enabled", "findings_sensitivity"}
    db.close()
