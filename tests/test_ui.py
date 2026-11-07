import os
import time

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFileDialog, QMessageBox

from sentinel.monitoring.sampler import Sample
from sentinel.ui.main_window import MainWindow
from sentinel.ui.wallpaper import WallpaperScene


def live_sample(**kw):
    base = dict(timestamp=time.time(), cpu_percent=40.0, cpu_count=2, memory_total=8 * 2**30,
                memory_used=2**30, memory_percent=25.0, system_disk_percent=50.0, uptime_s=3600)
    base.update(kw)
    return Sample(**base)


@pytest.fixture
def window(ctrl, pump):
    w = MainWindow(ctrl)
    w.show()
    pump(0.2)
    yield w
    ctrl.quitting = True
    w.close()


# ------------------------------------------------------------------ wallpaper scene
def test_scene_renders_real_content(ctrl, pump):
    ctrl._handle_sample(live_sample())
    scene = WallpaperScene(ctrl)
    scene.resize(1280, 720)
    scene.show()
    pump(0.3)
    img = scene.grab().toImage()
    assert (img.width(), img.height()) == (1280, 720)
    colours = {img.pixelColor(x, y).rgb() for x in range(0, 1280, 40) for y in range(0, 720, 40)}
    assert len(colours) > 20                                    # not a blank rectangle
    iris = QColor(img.pixelColor(640 + 22, 288))          # just outside the black pupil, inside the iris
    assert iris.red() > 60 and iris.red() > 2 * iris.blue()   # crimson core, not grey/black
    scene.close()


def test_scene_never_shows_values_it_does_not_have(ctrl):
    scene = WallpaperScene(ctrl)
    assert scene._values()[3:] == ("off", "MONITORING OFF")      # monitoring not running
    ctrl.sampler.start()
    try:
        assert scene._values()[:3] == (None, None, None)         # running but no sample yet
        ctrl.last_sample = live_sample()
        assert scene._values()[:3] == (40.0, 25.0, 50.0)
        ctrl.last_sample = live_sample(timestamp=time.time() - 60)
        cpu, ram, disk, state, text = scene._values()
        assert (cpu, ram, disk, state, text) == (None, None, None, "stale", "DATA STALE")
    finally:
        ctrl.sampler.stop()
    scene.close()


def test_scene_pause_resume_and_load_reduce_fps(ctrl, pump):
    scene = WallpaperScene(ctrl)
    scene.resize(800, 450)
    scene.show()
    pump(0.1)
    assert scene._frame.isActive() and scene._frame.interval() == int(1000 / 24)
    ctrl.set_animation_paused(True)
    pump(0.1)
    assert not scene._frame.isActive()
    ctrl.set_animation_paused(False)
    pump(0.1)
    assert scene._frame.isActive()
    for _ in range(5):
        ctrl._handle_sample(live_sample(cpu_percent=99.0))
    pump(0.1)
    assert scene._frame.interval() >= int(1000 / 15)            # frame rate capped under load
    ctrl.settings.set("animation_fps", 30)
    ctrl._handle_sample(live_sample(cpu_percent=5.0))
    pump(0.1)
    assert scene._frame.interval() == int(1000 / 30)
    scene.close()


def test_scene_frame_cost_is_small(ctrl, pump):
    """Sanity check on the dirty-rectangle design (generous bound; the CI box is not a Celeron)."""
    ctrl._handle_sample(live_sample())
    ctrl.settings.set("animation_intensity", "high")
    scene = WallpaperScene(ctrl)
    scene.resize(1920, 1080)
    scene.show()
    pump(0.2)
    t0 = time.perf_counter()
    for _ in range(20):
        scene.repaint(scene._dyn)
    per_frame_ms = (time.perf_counter() - t0) / 20 * 1000
    print(f"\nrepaint of the animated region: {per_frame_ms:.1f} ms/frame (1920x1080, high intensity)")
    assert per_frame_ms < 80
    assert scene._dyn.width() * scene._dyn.height() < 0.4 * 1920 * 1080   # far less than a full-screen repaint
    scene.close()


def test_scene_overlay_toggle_and_resize_keep_geometry_valid(ctrl, pump):
    scene = WallpaperScene(ctrl)
    for size in ((640, 360), (1920, 1080), (1024, 768), (3840, 2160)):
        scene.resize(*size)
        scene.show()
        pump(0.05)
        assert scene.rect().contains(scene._dyn) and scene.rect().contains(scene._overlay)
    ctrl.settings.set("show_overlay", False)
    scene.grab()
    scene.close()


# --------------------------------------------------------------------- main window
def test_window_has_all_pages_and_navigation(window, pump):
    assert [p.title for p in window.pages] == ["Overview", "Monitor", "Security", "Projects", "Events", "Settings", "Performance", "Findings", "AI Assistant"]   # V3 appends Findings, V4 appends AI Assistant
    assert window.nav.count() == 9
    for i in range(9):
        window.goto(i)
        pump(0.05)
        assert window.stack.currentIndex() == i


def test_toolbar_actions_actually_work(window, ctrl, pump):
    ctrl.set_monitoring(True)
    window.act_monitor.trigger()
    assert not ctrl.monitoring_active and "OFF" in window.act_monitor.text()
    window.act_monitor.trigger()
    assert ctrl.monitoring_active
    window.act_anim.trigger()
    assert ctrl.animation_paused and window.act_anim.text() == "Resume animation"
    window.act_anim.trigger()
    assert not ctrl.animation_paused
    window.act_mode.trigger()
    pump(0.1)
    assert ctrl.mode == "wallpaper" and window.act_restore.isEnabled()
    window.act_restore.trigger()
    assert ctrl.mode == "panel" and not window.act_restore.isEnabled()
    ctrl.set_monitoring(False)


def test_keyboard_shortcuts_are_registered(window):
    keys = {a.shortcut().toString() for a in window.actions()}
    for expected in ("Ctrl+M", "Ctrl+P", "F9", "Ctrl+E", "Ctrl+Shift+R", "Ctrl+Q", "F5", "Ctrl+1", "Ctrl+6"):
        assert expected in keys


def test_exit_requires_confirmation(window, ctrl, dialogs):
    fired = []
    ctrl.quit_requested.connect(lambda: fired.append(1))
    dialogs["answer"] = QMessageBox.StandardButton.No
    window.act_exit.trigger()
    assert fired == [] and not ctrl.quitting
    dialogs["answer"] = QMessageBox.StandardButton.Yes
    window.act_exit.trigger()
    assert fired == [1] and ctrl.quitting


def test_close_goes_to_tray_when_available_else_asks(window, ctrl, dialogs):
    ctrl.tray_available = True
    window.close()
    assert not window.isVisible() and not ctrl.quitting and dialogs["question"] == []
    window.show()
    ctrl.tray_available = False
    dialogs["answer"] = QMessageBox.StandardButton.No
    window.close()
    assert window.isVisible() and len(dialogs["question"]) == 1      # no tray: closing must ask before exiting
    ctrl.quitting = True
    window.close()
    assert not window.isVisible()                                     # an exit request is never vetoed


def test_overview_shows_dashes_without_data_then_live_values(window, ctrl, pump):
    window.goto(0)
    pump(0.1)
    ov = window.pages[0]
    assert ov.cpu.value.text() == "—"
    ctrl.sampler.start()
    ctrl._handle_sample(live_sample())
    pump(0.1)
    assert ov.cpu.value.text() == "40%" and ov.ram.value.text() == "25%" and ov.disk.value.text() == "50%"
    ctrl.sampler.stop()


def test_monitor_page_collects_processes_only_while_visible(window, ctrl, pump):
    window.goto(1)
    pump(0.1)
    assert ctrl.sampler._collect_processes is True
    window.goto(0)
    pump(0.1)
    assert ctrl.sampler._collect_processes is False


def test_monitor_page_lists_processes_and_flags_restricted(window, ctrl, pump):
    from sentinel.monitoring.sampler import ProcInfo

    window.goto(1)
    procs = (ProcInfo(10, "alpha.exe", 12.5, 50_000_000), ProcInfo(11, "protected.exe", None, None))
    ctrl._handle_sample(live_sample(processes=procs, processes_total=5, processes_restricted=1))
    pump(0.1)
    t = window.pages[1].proc_table
    assert t.rowCount() == 2
    texts = {t.item(r, 0).text(): (t.item(r, 2).text(), t.item(r, 3).text()) for r in range(2)}
    assert texts["alpha.exe"] == ("12.5%", "47.7 MB")
    assert texts["protected.exe"] == ("—", "—")
    assert "1 with restricted" in window.pages[1].proc_info.text()
    window.pages[1].filter.setText("alpha")
    assert t.rowCount() == 1


# ----------------------------------------------------------------------- settings page
def test_settings_page_persists_and_audits_changes(window, ctrl, pump):
    window.goto(5)
    page = window.pages[5]
    pump(0.1)
    page._apply("animation_fps", 15)
    assert ctrl.settings.get("animation_fps") == 15
    ev = ctrl.events.query(event_type="setting_changed")[0]
    assert ev.details["key"] == "animation_fps" and ev.details["new"] == 15
    ctrl.settings.set("cpu_warn_percent", 60)                       # changed elsewhere -> UI follows
    pump(0.1)
    assert page.findChildren(type(page.autostart))                   # page alive
    spins = [w for w in page.findChildren(__import__("PySide6.QtWidgets", fromlist=["QSpinBox"]).QSpinBox)]
    assert 60 in [s.value() for s in spins]


def test_settings_page_reset_and_clear_events_need_confirmation(window, ctrl, dialogs):
    page = window.pages[5]
    ctrl.settings.set("animation_fps", 30)
    dialogs["answer"] = QMessageBox.StandardButton.No
    page._reset()
    assert ctrl.settings.get("animation_fps") == 30
    dialogs["answer"] = QMessageBox.StandardButton.Yes
    page._reset()
    assert ctrl.settings.get("animation_fps") == 24
    ctrl.log("app", "x", "y")
    dialogs["answer"] = QMessageBox.StandardButton.No
    page._clear_events()
    assert ctrl.events.count() > 0
    dialogs["answer"] = QMessageBox.StandardButton.Yes
    page._clear_events()
    assert ctrl.events.count() == 1 and ctrl.events.query()[0].event_type == "events_cleared"


def test_settings_backup_and_import_export(window, ctrl, tmp_path, monkeypatch, dialogs):
    page = window.pages[5]
    target = tmp_path / "bk" / "backup.db"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(target), "")))
    page._backup()
    assert target.exists() and dialogs["information"]
    cfg = tmp_path / "cfg.json"
    ctrl.settings.set("animation_intensity", "high")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(cfg), "")))
    page._export_settings()
    ctrl.settings.reset()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(cfg), "")))
    page._import_settings()
    assert ctrl.settings.get("animation_intensity") == "high"


# ----------------------------------------------------------------------- projects page
def test_projects_page_add_edit_remove(window, ctrl, tmp_path, dialogs, pump):
    window.goto(3)
    page = window.pages[3]
    pump(0.1)
    page._new()
    page.name.setText("Atlas")
    page.path.setText(str(tmp_path))
    page.desc.setText("Backend")
    page.status.setCurrentText("Paused")
    page.notes.setPlainText("remember the milk")
    page._save()
    saved = ctrl.projects.list()[0]
    assert (saved.name, saved.status, saved.notes) == ("Atlas", "Paused", "remember the milk")
    assert page.list.count() == 1 and page._current_id == saved.id
    page.name.setText("Atlas v2")
    page._save()
    assert ctrl.projects.get(saved.id).name == "Atlas v2"
    page.path.setText(str(tmp_path / "gone"))
    assert pump(3, lambda: "not found" in page.path_note.text().lower())      # V3: checked in the background
    assert not page.open_btn.isEnabled() and not page.git_btn.isEnabled()
    dialogs["answer"] = QMessageBox.StandardButton.No
    page._delete()
    assert ctrl.projects.get(saved.id) is not None
    dialogs["answer"] = QMessageBox.StandardButton.Yes
    page._delete()
    assert ctrl.projects.list() == [] and tmp_path.exists()
    assert {"project_added", "project_updated", "project_removed"} <= {e.event_type for e in ctrl.events.query()}


def test_projects_page_validation_and_git_check(window, ctrl, tmp_path, dialogs, pump):
    import shutil
    import subprocess

    page = window.pages[3]
    window.goto(3)
    page._new()
    page.name.setText("   ")
    page._save()
    assert dialogs["warning"] and ctrl.projects.list() == []
    page.name.setText("Repo")
    page.path.setText(str(tmp_path))
    page._save()
    if shutil.which("git"):
        subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    page._git()
    assert pump(8, lambda: page.git_label.text() not in ("", "Checking Git status…"))
    expected = "Branch" if shutil.which("git") else "Git is not installed"
    assert expected in page.git_label.text()


# ------------------------------------------------------------------------ events page
def test_events_page_filters_and_exports(window, ctrl, tmp_path, monkeypatch, dialogs, pump):
    import json

    ctrl.log("security", "file_created", "fresh file", path="/x/a.txt")
    ctrl.log("system", "cpu_warning", "hot", severity="warning")
    window.goto(4)
    page = window.pages[4]
    pump(0.2)
    n_all = page.model.rowCount()
    assert n_all >= 2
    page.category.setCurrentIndex(page.category.findData("security"))
    assert {page.model.data(page.model.index(r, 2)) for r in range(page.model.rowCount())} == {"security"}
    page.category.setCurrentIndex(0)
    page.text.setText("hot")
    pump(0.6)
    assert page.model.rowCount() == 1 and page.model.data(page.model.index(0, 1)) == "warning"
    page.table.selectRow(0)
    assert "hot" in page.detail.toPlainText()
    dest = tmp_path / "out.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(dest), "JSON (*.json)")))
    page.export()
    assert pump(5, dest.exists) and pump(1, lambda: bool(dialogs["information"]))
    data = json.loads(dest.read_text(encoding="utf-8"))
    assert data["count"] == 1 and data["events"][0]["message"] == "hot"      # exports the filtered set


def test_events_page_live_refresh(window, ctrl, pump):
    window.goto(4)
    page = window.pages[4]
    pump(0.2)
    before = page.model.rowCount()
    ctrl.log("app", "note", "brand new event")
    assert pump(3, lambda: page.model.rowCount() == before + 1)


# ----------------------------------------------------------------------- security page
def test_security_page_folders_and_baseline_flow(window, ctrl, tmp_path, monkeypatch, dialogs, pump):
    watched = tmp_path / "docs"
    watched.mkdir()
    f = watched / "keep.txt"
    f.write_text("original")
    window.goto(2)
    page = window.pages[2]
    pump(0.1)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(watched)))
    page._add_folder()
    assert page.folder_table.rowCount() == 1 and page.folder_table.item(0, 0).text() == str(watched)
    page._add_folder()                                              # duplicate -> friendly warning
    assert dialogs["warning"]

    monkeypatch.setattr(QFileDialog, "getOpenFileNames", staticmethod(lambda *a, **k: ([str(f)], "")))
    page._add_files()
    assert pump(8, lambda: page.base_table.rowCount() == 1)
    assert ctrl.baseline.count() == 1

    f.write_text("tampered")
    page._verify()
    assert pump(8, lambda: bool(dialogs["warning"][1:]))            # "Differences found" dialog
    assert any("baseline_mismatch" == e.event_type for e in ctrl.events.query())
    assert f.read_text() == "tampered"                               # Sentinel never modifies files

    page.base_table.selectRow(0)
    page._remove()
    assert ctrl.baseline.count() == 0 and f.exists()

    page.folder_table.selectRow(0)
    page._remove_folder()
    assert ctrl.security.list_folders() == [] and watched.exists()


def test_security_page_add_directory_requires_confirmation(window, ctrl, tmp_path, monkeypatch, dialogs, pump):
    d = tmp_path / "bulk"
    d.mkdir()
    for i in range(3):
        (d / f"f{i}.txt").write_text(str(i))
    window.goto(2)
    page = window.pages[2]
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(d)))
    dialogs["answer"] = QMessageBox.StandardButton.No
    page._add_dir()
    assert pump(5, lambda: bool(dialogs["question"]))
    assert ctrl.baseline.count() == 0                               # declined -> nothing was hashed
    dialogs["answer"] = QMessageBox.StandardButton.Yes
    page._add_dir()
    assert pump(8, lambda: ctrl.baseline.count() == 3)


def test_security_page_toggle_watching(window, ctrl, pump):
    window.goto(2)
    page = window.pages[2]
    page._toggle_watching()
    assert ctrl.security_active and ctrl.settings.get("security_enabled")
    page._toggle_watching()
    assert not ctrl.security_active
    pump(0.1)
    assert "stopped" in page.watch_state.text().lower()
