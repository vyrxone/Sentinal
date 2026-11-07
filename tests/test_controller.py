import threading
import time

from PySide6.QtCore import QThread

from sentinel.monitoring.sampler import Sample
from sentinel.storage.database import DatabaseError


def sample(cpu=10.0, ram=20.0, disk=30.0, ts=None):
    return Sample(timestamp=ts or time.time(), cpu_percent=cpu, cpu_count=2, memory_total=8 * 2**30,
                  memory_used=2**30, memory_percent=ram, system_disk_percent=disk)


def types(ctrl, category=None):
    return [e.event_type for e in ctrl.events.query(category=category, newest_first=False)]


def test_sample_updates_history_and_emits(ctrl):
    got = []
    ctrl.sample_ready.connect(got.append)
    ctrl._handle_sample(sample(cpu=12))
    assert ctrl.last_sample.cpu_percent == 12 and list(ctrl.history.cpu) == [12]
    assert len(got) == 1


def test_threshold_alert_is_logged_once_and_recovery_logged(ctrl):
    ctrl.settings.set("cpu_warn_percent", 80)
    ctrl.settings.set("sustained_samples", 2)
    alerts = []
    ctrl.alert_raised.connect(alerts.append)
    ctrl._handle_sample(sample(cpu=95))
    assert types(ctrl, "system") == []                 # not sustained yet
    ctrl._handle_sample(sample(cpu=95))
    ctrl._handle_sample(sample(cpu=96))
    assert types(ctrl, "system") == ["cpu_warning"]    # logged once, not spammed
    assert ctrl.events.query(event_type="cpu_warning")[0].severity == "warning"
    assert [a.kind for a in alerts] == ["warn"]
    ctrl._handle_sample(sample(cpu=10))
    assert types(ctrl, "system") == ["cpu_warning", "cpu_recovered"]


def test_changing_threshold_setting_takes_effect_immediately(ctrl):
    ctrl.settings.set("sustained_samples", 1)
    ctrl.settings.set("ram_warn_percent", 50)
    ctrl._handle_sample(sample(ram=60))
    assert "ram_warning" in types(ctrl)


def test_sustained_load_reduces_quality_and_recovers(ctrl):
    changes = []
    ctrl.quality_changed.connect(lambda: changes.append(ctrl.quality))
    normal = ctrl.quality
    assert normal.particles > 0
    for _ in range(5):
        ctrl._handle_sample(sample(cpu=97))
    assert ctrl.load.active
    assert ctrl.quality.particles == 0 and ctrl.quality.fps <= 15 and not ctrl.quality.glow
    assert "sustained_load" in types(ctrl)
    assert "Consider a lower animation" in ctrl.events.query(event_type="sustained_load")[0].message
    ctrl._handle_sample(sample(cpu=20))
    assert not ctrl.load.active and ctrl.quality.particles == normal.particles
    assert "load_normal" in types(ctrl)
    ctrl.settings.set("reduce_under_load", False)
    for _ in range(5):
        ctrl._handle_sample(sample(cpu=97))
    assert ctrl.quality.particles == normal.particles   # user opted out of automatic reduction


def test_monitoring_toggle_persists_and_stops_thread(ctrl):
    before = threading.active_count()
    ctrl.set_monitoring(True)
    assert ctrl.monitoring_active and threading.active_count() == before + 1
    assert ctrl.settings.get("monitoring_enabled") is True
    ctrl.set_monitoring(False)
    assert not ctrl.monitoring_active and ctrl.settings.get("monitoring_enabled") is False
    assert threading.active_count() == before
    assert {"monitoring_started", "monitoring_stopped"} <= set(types(ctrl))


def test_real_sampler_delivers_to_ui_thread(ctrl, pump):
    ui = QThread.currentThread()
    where = []
    ctrl.sample_ready.connect(lambda s: where.append(QThread.currentThread() == ui))
    ctrl.settings.set("sample_interval_s", 1.0)
    ctrl.set_monitoring(True)
    assert pump(8, lambda: len(where) >= 2)
    assert all(where)                                   # never delivered on the sampler thread
    assert ctrl.last_sample.memory_percent is not None
    assert 0 < ctrl.data_age() < 5


def test_security_events_arrive_on_ui_thread(ctrl, tmp_path, pump):
    ui = QThread.currentThread()
    seen = []
    ctrl.event_added.connect(lambda e: seen.append((e.event_type, QThread.currentThread() == ui)))
    watched = tmp_path / "w"
    watched.mkdir()
    ctrl.security.add_folder(str(watched))
    ctrl.set_security(True)
    time.sleep(0.4)
    (watched / "n.txt").write_text("x")
    assert pump(8, lambda: any(t == "file_created" for t, _ in seen))
    assert all(on_ui for _, on_ui in seen)


def test_animation_pause_is_persisted(ctrl):
    ctrl.set_animation_paused(True)
    assert ctrl.animation_paused and ctrl.settings.get("animation_paused") is True
    ctrl.set_animation_paused(False)
    assert not ctrl.animation_paused


def test_mode_switching_uses_honest_window_fallback_off_windows(ctrl):
    modes = []
    ctrl.mode_changed.connect(modes.append)
    ctrl.set_mode("wallpaper")
    assert ctrl.mode == "wallpaper" and ctrl.host.scene is not None and ctrl.host.scene.isVisible()
    assert ctrl.desktop_kind == "window"                 # never claims "embedded" off Windows
    assert "not embedded" in ctrl.desktop_detail.lower()
    ctrl.restore_desktop()
    assert ctrl.mode == "panel" and ctrl.host.scene is None and ctrl.desktop_kind == "hidden"
    assert "desktop_restored" in types(ctrl)
    assert modes == ["wallpaper", "panel"]


def test_window_mode_setting_is_respected_and_close_returns_to_panel(ctrl, pump):
    ctrl.settings.set("desktop_integration", "window_only")
    ctrl.set_mode("wallpaper")
    assert "window mode was selected" in ctrl.desktop_detail.lower() or ctrl.desktop_kind == "window"
    ctrl.host.scene.close()                              # user closes the fallback window
    pump(0.2)
    assert ctrl.mode == "panel" and ctrl.host.scene is None


def test_invalid_mode_rejected(ctrl):
    import pytest

    with pytest.raises(ValueError):
        ctrl.set_mode("fullscreen")


def test_quit_sets_flag_and_emits(ctrl):
    fired = []
    ctrl.quit_requested.connect(lambda: fired.append(1))
    assert ctrl.quitting is False
    ctrl.quit()
    assert ctrl.quitting is True and fired == [1]


def test_shutdown_is_idempotent_and_closes_everything(db, qapp):
    from sentinel.controller import AppController

    before = threading.active_count()
    c = AppController(db)
    c.start()
    assert c.monitoring_active and c.security_active
    c.shutdown()
    c.shutdown()
    assert not c.monitoring_active and not c.security_active and not db.is_open
    deadline = time.time() + 5
    while threading.active_count() > before and time.time() < deadline:
        time.sleep(0.05)
    assert threading.active_count() == before


def test_log_never_raises_when_database_is_gone(db, qapp):
    from sentinel.controller import AppController

    c = AppController(db)
    db.close()
    assert c.log("app", "x", "m") is None               # swallowed: a dead DB must not crash the UI
