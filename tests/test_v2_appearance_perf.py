"""Appearance settings/presets really change rendering; performance features; diagnostics."""

import os
import subprocess
import sys
import threading
import time

import pytest
from PySide6.QtGui import QImage

from sentinel.core.appearance import appearance_from
from sentinel.core.performance import effective_quality
from sentinel.diagnostics import build_report, redact
from sentinel.monitoring.selfstats import RenderStats, SelfMetrics
from sentinel.storage.settings_store import PRESETS
from sentinel.ui.wallpaper import WallpaperScene


def render(ctrl, w=960, h=540):
    scene = WallpaperScene(ctrl)
    scene.resize(w, h)
    scene.show()
    scene._frame.stop()
    scene._t = 3.0                                   # fixed animation time: renders are deterministic
    img = scene.grab().toImage().convertToFormat(QImage.Format.Format_RGB32)
    scene.shutdown()
    scene.close()
    return img


def mean_luma(img):
    total = 0
    px = img.width() * img.height()
    ptr = img.constBits()
    data = bytes(ptr)[: px * 4]
    for i in range(0, len(data), 4 * 7):
        total += data[i] + data[i + 1] + data[i + 2]
    return total / (len(data) / (4 * 7)) / 3


def diff_pixels(a, b):
    da, db = bytes(a.constBits()), bytes(b.constBits())
    return sum(1 for i in range(0, min(len(da), len(db)), 4 * 5) if da[i:i + 3] != db[i:i + 3])


def test_dimmer_darkens(ctrl):
    base = mean_luma(render(ctrl))
    ctrl.settings.set("dimmer_percent", 60)
    assert mean_luma(render(ctrl)) < base * 0.6


def test_show_clock_toggle_changes_the_clock_region(ctrl):
    on = render(ctrl)
    ctrl.settings.set("show_clock", False)
    off = render(ctrl)
    assert diff_pixels(on, off) > 300


def test_emblem_size_particles_pulses_overlay_change_rendering(ctrl):
    ctrl.settings.set("particle_intensity", "high")
    ctrl.settings.set("pulse_intensity", "high")
    ref = render(ctrl)
    ctrl.settings.set("emblem_size", 60)
    assert diff_pixels(ref, render(ctrl)) > 500
    ctrl.settings.set("emblem_size", 100)
    ctrl.settings.set("particle_intensity", "off")
    assert diff_pixels(ref, render(ctrl)) > 50
    ctrl.settings.set("particle_intensity", "high")
    ctrl.settings.set("pulse_intensity", "off")
    assert diff_pixels(ref, render(ctrl)) > 20
    ctrl.settings.set("pulse_intensity", "high")
    ctrl.settings.set("show_overlay", False)
    assert diff_pixels(ref, render(ctrl)) > 200
    ctrl.settings.set("show_overlay", True)
    compact = render(ctrl)
    ctrl.settings.set("overlay_mode", "expanded")
    assert diff_pixels(compact, render(ctrl)) > 100


def test_each_preset_changes_rendering_and_settings(ctrl):
    images = {}
    for name in PRESETS:
        ctrl.settings.apply_preset(name)
        a = ctrl.appearance
        assert a.emblem_scale == PRESETS[name]["emblem_size"] / 100
        images[name] = render(ctrl)
    names = list(PRESETS)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            assert diff_pixels(images[names[i]], images[names[j]]) > 500, (names[i], names[j])
    assert mean_luma(images["silent_guardian"]) < mean_luma(images["sentinel_core"])    # dimmed, calmer


def test_appearance_persists_via_settings_system(db):
    from sentinel.storage.settings_store import SettingsStore

    s = SettingsStore(db)
    s.apply_preset("crimson_watch")
    s.set_appearance("dimmer_percent", 20)
    s2 = SettingsStore(db)
    assert s2.get("appearance_preset") == "custom" and s2.get("dimmer_percent") == 20 and s2.get("emblem_size") == 115
    assert appearance_from(s2).overlay_expanded is True


def test_reduced_motion_stops_the_frame_timer_and_draws_static(ctrl, pump):
    scene = WallpaperScene(ctrl)
    scene.resize(640, 360)
    scene.show()
    assert scene._frame.isActive()
    ctrl.settings.set("reduced_motion", True)
    assert ctrl.quality.static and not scene._frame.isActive()
    assert scene._second.isActive()                                      # data/clock still refresh once a second
    a = scene.grab().toImage()
    pump(0.3)
    b = scene.grab().toImage()
    assert diff_pixels(a.convertToFormat(QImage.Format.Format_RGB32), b.convertToFormat(QImage.Format.Format_RGB32)) < 30
    ctrl.settings.set("reduced_motion", False)
    assert scene._frame.isActive()
    scene.shutdown()
    scene.close()


def test_quality_mapping_and_load_reduction():
    q = effective_quality(30, "high", False, True, particle_count=0, pulse_strength=0.0)
    assert q.particles == 0 and not q.pulses and q.fps == 30
    q = effective_quality(30, "high", True, True, particle_count=48, pulse_strength=1.0)
    assert q.fps == 15 and q.particles == 0 and not q.glow                  # sustained load wins
    assert effective_quality(24, "medium", False, True).particles == 28     # V1 behaviour unchanged


def test_scene_reports_measured_fps_and_paint_time(ctrl, pump):
    scene = WallpaperScene(ctrl)
    scene.resize(800, 450)
    scene.show()
    assert pump(4, lambda: ctrl.render_stats.snapshot()["fps"] is not None)
    snap = ctrl.render_stats.snapshot()
    assert snap["scenes"] == 1 and 1 <= snap["fps"] <= 40 and snap["paint_ms"] > 0
    scene.shutdown()
    scene.close()
    assert ctrl.render_stats.snapshot()["scenes"] == 0


def test_render_stats_expire_and_are_bounded():
    rs = RenderStats()
    rs.report("a", 20.0, 3.0, True)
    assert rs.snapshot()["fps"] == 20.0
    rs.forget("a")
    assert rs.snapshot()["fps"] is None
    sm = SelfMetrics(history=5)
    for _ in range(20):
        sm.poll()
    assert len(sm.cpu) == 5 and len(sm.rss) == 5 and sm.rss[-1] > 0


def test_process_scan_is_throttled_by_interval(monkeypatch):
    from sentinel.monitoring import sampler as sm

    calls = []
    monkeypatch.setattr(sm, "collect_sample", lambda collect, primed: calls.append(collect) or sm.Sample(timestamp=time.time()))
    got = []
    s = sm.Sampler(got.append, interval=0.5, collect_processes=True)
    s.set_process_interval(1.5)
    s._interval = 0.5
    s.start()
    time.sleep(3.0)
    s.stop()
    assert calls.count(True) <= 3 and calls.count(False) >= 1 and len(calls) >= 4


def test_performance_page_renders_real_values_and_stops_timer_when_hidden(ctrl, pump):
    from sentinel.ui.pages.performance import PerformancePage

    page = PerformancePage(ctrl)
    page.show()
    pump(0.3)
    assert page._tick.isActive() and "VYRXONE Sentinel V5" in page.report.toPlainText()
    assert page.rows["Frame-rate cap"].text().startswith("24 fps")
    assert page.t_fps.isVisible()
    page.hide()
    assert not page._tick.isActive()
    page.close()


def test_diagnostic_report_is_redacted_and_useful(ctrl, monkeypatch, tmp_path):
    monkeypatch.setenv("USERNAME", "AlexExample")
    monkeypatch.setenv("USER", "AlexExample")
    text = build_report(ctrl)
    for needle in ("Windows build" if sys.platform == "win32" else "Python:", "Screens:", "Database schema: v5",
                   "Desktop status:", "Render (measured)", "Monitoring interval"):
        assert needle in text
    assert "AlexExample" not in redact("C:\\Users\\AlexExample\\x /home/AlexExample") and "<user>" in redact("hi AlexExample")
    home = os.path.expanduser("~")
    assert home not in redact(f"{home}/Documents/x") or len(home) <= 2            # the home folder is removed
    assert "password" not in text.lower() and "token" not in text.lower()


def test_diagnostics_cli_works_headless(tmp_path):
    from sentinel.core.paths import resource_path

    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", SENTINEL_DATA_DIR=str(tmp_path / "d"),
               PYTHONPATH=str(resource_path("src")))
    out = subprocess.run([sys.executable, "-m", "sentinel", "--diagnostics"], env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and "diagnostic report" in out.stdout
