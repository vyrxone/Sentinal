import csv
import json
import sqlite3

import pytest

from sentinel.storage import migrations
from sentinel.storage.database import Database, DatabaseError
from sentinel.storage.events_store import EventStore
from sentinel.storage.export import csv_safe, export_events
from sentinel.storage.settings_store import DEFS_BY_KEY, SettingError, SettingsStore


# ---------------------------------------------------------------- database
def test_database_initialises_schema(db):
    assert db.user_version == migrations.LATEST_VERSION
    tables = {r[0] for r in db.query("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"settings", "events", "watched_folders", "baseline_files", "projects"} <= tables


def test_reopen_keeps_data_and_does_not_remigrate(tmp_path):
    path = tmp_path / "persist.db"
    d1 = Database(path)
    d1.execute("INSERT INTO settings(key,value,updated_ts) VALUES('x','1',0)")
    d1.close()
    d2 = Database(path)
    assert d2.query_one("SELECT value FROM settings WHERE key='x'")["value"] == "1"
    d2.close()


def test_migration_from_v1_creates_backup_and_upgrades(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    migrations._m1_initial(conn)
    conn.execute("INSERT INTO events(ts,category,event_type,severity,message) "
                 "VALUES(1,'app','old','info','legacy row')")
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()

    database = Database(path)
    assert database.user_version == migrations.LATEST_VERSION
    assert database.query_one("SELECT message FROM events")["message"] == "legacy row"
    idx = {r[0] for r in database.query("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "idx_events_ts" in idx
    assert (tmp_path / "old.db.pre-v1.bak").exists()
    database.close()


def test_newer_schema_is_refused(tmp_path):
    path = tmp_path / "future.db"
    conn = sqlite3.connect(path)
    conn.execute(f"PRAGMA user_version = {migrations.LATEST_VERSION + 5}")
    conn.commit()
    conn.close()
    with pytest.raises(DatabaseError, match="newer"):
        Database(path)


def test_failed_migration_rolls_back(tmp_path, monkeypatch):
    def boom(conn):
        conn.execute("CREATE TABLE half_done(x)")
        raise RuntimeError("fail midway")

    monkeypatch.setattr(migrations, "MIGRATIONS", [(1, boom)])
    with pytest.raises(RuntimeError):
        Database(tmp_path / "bad.db")
    conn = sqlite3.connect(tmp_path / "bad.db")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    assert conn.execute("SELECT name FROM sqlite_master WHERE name='half_done'").fetchone() is None


def test_unusable_path_raises_database_error(tmp_path):
    blocker = tmp_path / "afile"
    blocker.write_text("x")
    with pytest.raises(DatabaseError):
        Database(blocker / "sub" / "x.db")


def test_closed_database_raises(tmp_path):
    d = Database(tmp_path / "c.db")
    d.close()
    with pytest.raises(DatabaseError):
        d.query("SELECT 1")


def test_backup_is_a_valid_database(db, tmp_path):
    db.execute("INSERT INTO settings(key,value,updated_ts) VALUES('k','\"v\"',0)")
    dest = db.backup_to(tmp_path / "bk" / "copy.db")
    copy = Database(dest)
    assert copy.query_one("SELECT value FROM settings WHERE key='k'")["value"] == '"v"'
    copy.close()


# ---------------------------------------------------------------- settings
def test_settings_defaults_and_persistence(tmp_path):
    d = Database(tmp_path / "s.db")
    s = SettingsStore(d)
    assert s.get("animation_fps") == 24
    assert s.get("cpu_warn_percent") == 85
    s.set("animation_fps", 15)
    s.set("cpu_warn_percent", 70)
    s.set("show_overlay", False)
    d.close()

    d2 = Database(tmp_path / "s.db")
    s2 = SettingsStore(d2)  # simulates an application restart
    assert s2.get("animation_fps") == 15
    assert s2.get("cpu_warn_percent") == 70
    assert s2.get("show_overlay") is False
    d2.close()


@pytest.mark.parametrize("key,value", [
    ("animation_fps", 60), ("animation_fps", "24"), ("animation_intensity", "ultra"),
    ("cpu_warn_percent", 5), ("cpu_warn_percent", 101), ("cpu_warn_percent", True),
    ("sample_interval_s", 0.1), ("sample_interval_s", float("nan")),
    ("show_overlay", "yes"), ("nonexistent", 1),
])
def test_settings_reject_invalid_values(settings, key, value):
    with pytest.raises(SettingError):
        settings.set(key, value)


def test_settings_listener_and_reset(settings):
    seen = []
    settings.subscribe(lambda k, v: seen.append((k, v)))
    settings.set("animation_intensity", "high")
    settings.set("animation_intensity", "high")  # unchanged: no second notification
    assert seen == [("animation_intensity", "high")]
    settings.reset("animation_intensity")
    assert settings.get("animation_intensity") == DEFS_BY_KEY["animation_intensity"].default


def test_settings_ignore_corrupt_stored_value(db):
    db.execute("INSERT INTO settings(key,value,updated_ts) VALUES('animation_fps','999',0)")
    db.execute("INSERT INTO settings(key,value,updated_ts) VALUES('show_overlay','not json',0)")
    s = SettingsStore(db)
    assert s.get("animation_fps") == 24
    assert s.get("show_overlay") is True


def test_settings_export_import_roundtrip_and_rejections(settings, tmp_path):
    settings.set("animation_fps", 30)
    out = settings.export_json(tmp_path / "cfg.json")
    settings.reset()
    applied, rejected = settings.import_json(out)
    assert "animation_fps" in applied and settings.get("animation_fps") == 30
    assert rejected == {}

    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"animation_fps": 99, "api_key": "x", "show_overlay": False}))
    applied, rejected = settings.import_json(bad)
    assert applied == ["show_overlay"]
    assert set(rejected) == {"animation_fps", "api_key"}

    notjson = tmp_path / "n.json"
    notjson.write_text("{nope")
    with pytest.raises(SettingError):
        settings.import_json(notjson)


def test_sample_config_file_is_valid(settings):
    from sentinel.core.paths import resource_path

    sample = resource_path("config", "sentinel.sample.json")
    applied, rejected = settings.import_json(sample)
    assert rejected == {}
    assert len(applied) == len(DEFS_BY_KEY)
    text = sample.read_text().lower()
    for word in ("api_key", "token", "password", "secret"):
        assert word not in text


# ---------------------------------------------------------------- events
def test_event_store_add_query_filter(events):
    events.add("system", "cpu_warn", "CPU high", severity="warning", ts=100)
    events.add("security", "file_created", "new file", path="/tmp/a.txt", ts=200)
    events.add("security", "file_modified", "changed 100%_x", path="/tmp/b.txt", ts=300,
               details={"watch_root": "/tmp"})
    assert events.count() == 3
    assert [e.event_type for e in events.query()] == ["file_modified", "file_created", "cpu_warn"]
    assert len(events.query(category="security")) == 2
    assert len(events.query(severity="warning")) == 1
    assert len(events.query(since=150, until=250)) == 1
    assert len(events.query(text="a.txt")) == 1
    assert len(events.query(text="100%_x")) == 1          # LIKE wildcards are escaped
    assert len(events.query(text="%")) == 1
    assert events.query(event_type="file_modified")[0].details == {"watch_root": "/tmp"}
    assert events.distinct_types() == ["cpu_warn", "file_created", "file_modified"]
    assert len(events.query(limit=1, offset=1)) == 1


def test_event_store_validates_severity_and_notifies(events):
    with pytest.raises(ValueError):
        events.add("app", "x", "m", severity="catastrophic")
    got = []
    events.subscribe(got.append)
    ev = events.add("app", "started", "hello")
    assert got == [ev] and ev.id > 0


def test_event_store_prunes_to_limit(db):
    store = EventStore(db, max_events=20)
    for i in range(60):
        store.add("app", "t", f"m{i}", ts=float(i))
    store.prune()
    assert store.count() == 20
    assert store.query(limit=1)[0].message == "m59"


def test_events_survive_restart(tmp_path):
    d = Database(tmp_path / "e.db")
    EventStore(d).add("app", "x", "kept")
    d.close()
    d2 = Database(tmp_path / "e.db")
    assert EventStore(d2).query()[0].message == "kept"
    d2.close()


# ---------------------------------------------------------------- export
def test_export_json_and_csv(events, tmp_path):
    events.add("security", "file_modified", "=HYPERLINK(\"http://x\")", path="+cmd|calc", ts=1)
    events.add("app", "note", "plain, with comma", ts=2, details={"k": "v"})
    rows = events.query(newest_first=False)

    j = export_events(rows, tmp_path / "out" / "ev.json")
    data = json.loads(j.read_text(encoding="utf-8"))
    assert data["count"] == 2 and data["events"][1]["details"] == {"k": "v"}
    assert data["app"] == "VYRXONE Sentinel"

    c = export_events(rows, tmp_path / "ev.csv")
    with open(c, newline="", encoding="utf-8-sig") as fh:
        table = list(csv.reader(fh))
    assert table[0][:3] == ["id", "time_utc", "category"]
    assert len(table) == 3
    assert table[1][5].startswith("'+") and table[1][6].startswith("'=")  # formula-injection guard
    assert table[2][6] == "plain, with comma"
    assert not list((tmp_path).glob("*.tmp"))


def test_export_rejects_unknown_format(events, tmp_path):
    with pytest.raises(ValueError):
        export_events([], tmp_path / "x.xml")


def test_csv_safe():
    assert csv_safe("=1+1") == "'=1+1"
    assert csv_safe("hello") == "hello"
    assert csv_safe(None) == ""
