"""V2 storage: fresh DB, V1 -> V2 upgrade, failed/interrupted migration, corrupt/locked/missing/future DB."""

import os
import sqlite3
import subprocess
import sys
import time

import pytest

from sentinel.storage import database, migrations
from sentinel.storage.database import Database, DatabaseError
from sentinel.storage.events_store import EventStore
from sentinel.storage.settings_store import DEFINITIONS, PRESETS, SettingError, SettingsStore


def make_v1_db(path, with_data=True):
    conn = sqlite3.connect(path)
    for v, fn in migrations.MIGRATIONS[:2]:
        fn(conn)
        conn.execute(f"PRAGMA user_version = {v}")
    if with_data:
        conn.execute("INSERT INTO events(ts,category,event_type,severity,path,message,details) "
                     "VALUES(1.0,'security','file_modified','warning','/a','old warning','{\"k\": 1}')")
        conn.execute("INSERT INTO settings VALUES('animation_fps','30',1.0)")
        conn.execute("INSERT INTO settings VALUES('cpu_warn_percent','77',1.0)")
        conn.execute("INSERT INTO watched_folders(path,path_key,recursive,enabled,added_ts) VALUES('/w','/w',1,1,1.0)")
        conn.execute("INSERT INTO baseline_files(path,path_key,sha256,size,mtime,added_ts) VALUES('/f','/f','ab',3,1.0,1.0)")
        conn.execute("INSERT INTO projects(name,created_ts,updated_ts) VALUES('proj',1.0,1.0)")
    conn.commit()
    conn.close()


def test_fresh_v2_database_has_latest_schema(tmp_path):
    db = Database(tmp_path / "f.db")
    assert db.user_version == migrations.LATEST_VERSION == 5      # V3 added migration 4
    cols = {r["name"] for r in db.query("PRAGMA table_info(events)")}
    assert {"acknowledged", "acked_ts", "archived"} <= cols
    assert {r["name"] for r in db.query("PRAGMA table_info(watched_folders)")} >= {"excludes"}
    assert db.query_one("SELECT name FROM sqlite_master WHERE name='alert_state'")
    assert db.journal_mode in ("wal", "default", "delete", "memory")
    db.close()


def test_v1_to_v2_upgrade_preserves_everything_and_backs_up(tmp_path):
    path = tmp_path / "v1.db"
    make_v1_db(path)
    db = Database(path)
    assert db.user_version == 5           # V1 -> latest (V3 schema v4)
    assert (tmp_path / "v1.db.pre-v2.bak").exists()               # backup before the risky step
    e = EventStore(db).query()[0]
    assert e.message == "old warning" and e.severity == "warning" and e.details == {"k": 1}
    assert e.acknowledged is False and e.archived is False
    s = SettingsStore(db)
    assert s.get("animation_fps") == 30 and s.get("cpu_warn_percent") == 77   # V1 preferences survive
    assert s.get("emblem_size") == 100                                          # V2 key -> default
    assert "emblem_size" in s.load_report["missing_v2"]
    assert db.query_one("SELECT COUNT(*) FROM watched_folders")[0] == 1
    assert db.query_one("SELECT COUNT(*) FROM baseline_files")[0] == 1
    assert db.query_one("SELECT COUNT(*) FROM projects")[0] == 1
    assert db.query_one("SELECT excludes FROM watched_folders")[0] == ""
    db.close()
    bak = sqlite3.connect(tmp_path / "v1.db.pre-v2.bak")                       # the backup is a real V1 DB
    assert bak.execute("PRAGMA user_version").fetchone()[0] == 2
    bak.close()


def test_failed_migration_rolls_back_and_reports_clearly(tmp_path, monkeypatch):
    path = tmp_path / "v1.db"
    make_v1_db(path)

    def broken(conn):
        conn.execute("ALTER TABLE events ADD COLUMN acknowledged INTEGER NOT NULL DEFAULT 0")
        raise sqlite3.OperationalError("simulated disk full")

    monkeypatch.setattr(migrations, "MIGRATIONS", migrations.MIGRATIONS[:2] + [(3, broken)])
    with pytest.raises(DatabaseError) as err:
        Database(path)
    assert "rolled back" in str(err.value) and "backup" in str(err.value)
    raw = sqlite3.connect(path)
    assert raw.execute("PRAGMA user_version").fetchone()[0] == 2               # still V1 schema
    assert "acknowledged" not in [r[1] for r in raw.execute("PRAGMA table_info(events)")]
    assert raw.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1       # data intact
    raw.close()
    monkeypatch.undo()
    assert Database(path).user_version == 5                                    # a later normal start upgrades fine


def test_killed_process_during_migration_leaves_a_recoverable_db(tmp_path):
    path = tmp_path / "v1.db"
    make_v1_db(path)
    code = ("import sqlite3,os,sys;c=sqlite3.connect(sys.argv[1],isolation_level=None);c.execute('BEGIN');"
            "c.execute('ALTER TABLE events ADD COLUMN acknowledged INTEGER NOT NULL DEFAULT 0');os._exit(1)")
    subprocess.run([sys.executable, "-c", code, str(path)])
    db = Database(path)                                                          # must open and finish the upgrade
    assert db.user_version == 5 and EventStore(db).count() == 1
    db.close()


def test_failed_open_does_not_leak_connection(tmp_path, monkeypatch):
    """Regression for V1 defect A1."""
    opened = []
    real = sqlite3.connect

    def spy(*a, **k):
        c = real(*a, **k)
        opened.append(c)
        return c

    monkeypatch.setattr(sqlite3, "connect", spy)
    monkeypatch.setattr(migrations, "migrate", lambda db: (_ for _ in ()).throw(DatabaseError("boom")))
    with pytest.raises(DatabaseError):
        Database(tmp_path / "x.db")
    for c in opened:
        with pytest.raises(sqlite3.ProgrammingError):
            c.execute("select 1")


def test_corrupt_database_gives_clear_error_and_is_not_deleted(tmp_path):
    path = tmp_path / "bad.db"
    path.write_bytes(b"this is not a sqlite database" * 50)
    with pytest.raises(DatabaseError) as err:
        Database(path)
    assert "damaged" in str(err.value) or "not a Sentinel" in str(err.value)
    assert path.exists() and path.read_bytes().startswith(b"this is not")      # never silently deleted


def test_locked_database_gives_clear_error(tmp_path, monkeypatch):
    path = tmp_path / "l.db"
    Database(path).close()
    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("PRAGMA journal_mode=DELETE")
    holder.execute("BEGIN EXCLUSIVE")
    real = sqlite3.connect
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: real(*a, **{**k, "timeout": 0.2}))
    try:
        with pytest.raises(DatabaseError) as err:
            Database(path)
        assert "locked" in str(err.value).lower() or "could not" in str(err.value).lower()
    finally:
        holder.execute("ROLLBACK")
        holder.close()


def test_future_schema_is_refused_and_never_downgraded(tmp_path):
    path = tmp_path / "f.db"
    Database(path).close()
    raw = sqlite3.connect(path)
    raw.execute("PRAGMA user_version = 99")
    raw.commit()
    raw.close()
    with pytest.raises(DatabaseError) as err:
        Database(path)
    assert "newer Sentinel" in str(err.value)
    raw = sqlite3.connect(path)
    assert raw.execute("PRAGMA user_version").fetchone()[0] == 99            # untouched
    raw.close()


def test_missing_or_inaccessible_data_dir(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(DatabaseError) as err:
        Database(blocker / "sub" / "x.db")
    assert "Could not open" in str(err.value)
    Database(tmp_path / "new" / "deep" / "x.db").close()                      # missing dirs are created


def test_settings_invalid_unknown_and_missing_v2_keys(db):
    db.execute("INSERT INTO settings VALUES('animation_fps','99',1.0)")
    db.execute("INSERT INTO settings VALUES('bogus_key','1',1.0)")
    db.execute("INSERT INTO settings VALUES('emblem_size','\"huge\"',1.0)")
    s = SettingsStore(db)
    assert s.get("animation_fps") == 24 and s.get("emblem_size") == 100         # defaults, no crash
    assert "animation_fps" in s.load_report["invalid"] and "emblem_size" in s.load_report["invalid"]
    assert s.load_report["unknown"] == ["bogus_key"]


@pytest.mark.parametrize("key,value", [("emblem_size", 10), ("emblem_size", 500), ("dimmer_percent", 90),
                                       ("monitor_mode", "everything"), ("particle_intensity", "extreme"),
                                       ("process_interval_s", 0), ("notify_min_interval_s", 1),
                                       ("selected_monitor", "x" * 500), ("selected_monitor", "a\nb"),
                                       ("reduced_motion", "yes")])
def test_v2_settings_validation_rejects_bad_values(settings, key, value):
    with pytest.raises(SettingError):
        settings.set(key, value)


def test_presets_apply_and_manual_change_becomes_custom(settings):
    for name, values in PRESETS.items():
        settings.apply_preset(name)
        assert settings.get("appearance_preset") == name
        for k, v in values.items():
            assert settings.get(k) == v
    settings.apply_preset("sentinel_core")
    settings.set_appearance("emblem_size", 120)
    assert settings.get("appearance_preset") == "custom"
    with pytest.raises(SettingError):
        settings.apply_preset("nope")


def test_v1_settings_export_schema_1_still_imports(settings, tmp_path):
    f = tmp_path / "v1.json"
    f.write_text('{"format":"vyrxone-sentinel-settings","schema":1,"settings":{"animation_fps":15}}')
    applied, rejected = settings.import_json(f)
    assert applied == ["animation_fps"] and rejected == {}
    out = settings.export_json(tmp_path / "out.json")
    assert '"schema": 2' in out.read_text()
