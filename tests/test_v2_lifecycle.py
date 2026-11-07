"""Lifecycle: single instance, stop/force-stop safety, repeated start/stop leaks, scripts, tray, docs."""

import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil
import pytest

from sentinel.core import instance, paths
from sentinel.core.paths import resource_path

ROOT = Path(resource_path("README.md")).parent


def env_for(tmp_path, name):
    return dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONPATH=str(resource_path("src")),
                SENTINEL_DATA_DIR=str(tmp_path / "data"), SENTINEL_INSTANCE_NAME=name)


# -------------------------------------------------------------- single instance
def test_lock_is_exclusive_and_released(tmp_path, qapp):
    first, other = instance.acquire(100)
    assert first is not None and other is None
    second, pid = instance.acquire(100)
    assert second is None and pid == os.getpid()
    first.unlock()
    third, _ = instance.acquire(100)
    assert third is not None
    third.unlock()


def test_simultaneous_starts_leave_exactly_one_instance(tmp_path):
    env = env_for(tmp_path, f"race-{os.getpid()}")
    procs = [subprocess.Popen([sys.executable, "-m", "sentinel", "--background", "--panel", "--quit-after", "9"],
                              env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(6)]
    time.sleep(6)
    alive = [p for p in procs if p.poll() is None]
    outputs = []
    for p in procs:
        try:
            out, err = p.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            p.kill()
            out, err = p.communicate()
        outputs.append(out + err)
    assert len(alive) == 1
    assert sum("already running" in o.lower() for o in outputs) == 5
    assert not any("Traceback" in o for o in outputs)


def test_stale_lock_from_a_dead_process_does_not_block_start(tmp_path):
    env = env_for(tmp_path, "stale-lock")
    code = ("import os,sys;sys.path.insert(0,sys.argv[1]);from sentinel.core import instance;"
            "from PySide6.QtCore import QCoreApplication;a=QCoreApplication([]);l,_=instance.acquire(100);os._exit(0)")
    subprocess.run([sys.executable, "-c", code, str(resource_path("src"))], env=env, check=True)
    res = subprocess.run([sys.executable, "-m", "sentinel", "--panel", "--background", "--quit-after", "1"],
                         env=env, capture_output=True, text=True, timeout=60)
    assert res.returncode == 0 and "Traceback" not in res.stderr


# ------------------------------------------------------------------- force stop
def test_force_stop_refuses_unrelated_python_process():
    victim = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        msg = instance.force_stop(victim.pid)
        assert "NOT stopped" in msg and victim.poll() is None            # an unrelated Python process survives
        assert not instance.looks_like_sentinel(victim.pid)
    finally:
        victim.kill()


def test_force_stop_terminates_a_verified_sentinel_process(tmp_path):
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)  # -m sentinel", "-m", "sentinel"])
    try:
        assert instance.looks_like_sentinel(p.pid)
        assert "was stopped" in instance.force_stop(p.pid)
        assert p.wait(5) is not None
    finally:
        if p.poll() is None:
            p.kill()


def test_force_stop_edge_cases():
    assert "nothing to force-stop" in instance.force_stop(None)
    assert "Refusing" in instance.force_stop(os.getpid())
    assert "NOT stopped" in instance.force_stop(2 ** 22 + 12345)         # no such process -> not Sentinel


def test_stop_command_waits_for_exit_and_reports(tmp_path):
    env = env_for(tmp_path, "stopwait")
    first = subprocess.Popen([sys.executable, "-m", "sentinel", "--panel", "--background", "--quit-after", "60"], env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        time.sleep(4)
        stop = subprocess.run([sys.executable, "-m", "sentinel", "--stop"], env=env, capture_output=True, text=True, timeout=30)
        assert "asked to exit" in stop.stdout and "has exited" in stop.stdout and stop.returncode == 0
        assert first.wait(10) == 0
    finally:
        if first.poll() is None:
            first.kill()


# ------------------------------------------------------------- start/stop leaks
def test_repeated_start_stop_cycles_do_not_leak_threads_or_widgets(tmp_path, qapp):
    from sentinel.controller import AppController
    from sentinel.storage.database import Database
    from sentinel.ui.wallpaper_host import WallpaperHost
    from sentinel.ui.wallpaper import WallpaperScene
    from PySide6.QtCore import QEvent

    before = threading.active_count()
    for i in range(6):
        db = Database(tmp_path / f"d{i}.db")
        c = AppController(db)
        c.host = WallpaperHost(c)
        c.start()
        c.set_mode("wallpaper")
        qapp.processEvents()
        c.set_monitoring(False)
        c.set_monitoring(True)
        c.shutdown()
        c.shutdown()                                                  # idempotent
    qapp.processEvents()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    time.sleep(0.5)
    assert threading.active_count() <= before + 1
    assert [w for w in qapp.allWidgets() if isinstance(w, WallpaperScene)] == []


def test_set_monitoring_toggle_does_not_stack_sampler_threads(ctrl):
    ctrl.start()
    for _ in range(10):
        ctrl.set_monitoring(False)
        ctrl.set_monitoring(True)
    names = [t.name for t in threading.enumerate()]
    assert names.count("sentinel-sampler") == 1
    ctrl.set_monitoring(False)
    time.sleep(0.2)
    assert "sentinel-sampler" not in [t.name for t in threading.enumerate()]


def test_settings_signal_connections_are_not_duplicated(ctrl):
    n = ctrl.receivers("2setting_changed(QString,PyObject)") if hasattr(ctrl, "receivers") else 0
    for _ in range(3):
        ctrl.host.show_wallpaper()
        ctrl.host.hide_wallpaper()
    m = ctrl.receivers("2setting_changed(QString,PyObject)") if hasattr(ctrl, "receivers") else 0
    assert m == n


# ------------------------------------------------------------------- scripts/docs
def read(name):
    return (ROOT / name).read_text(encoding="utf-8")


def test_stop_script_targets_only_sentinel():
    text = read("stop_sentinel.bat").lower()
    for bad in ("taskkill", "wmic", "pskill", "stop-process", "python.exe /f", "/im"):
        assert bad not in text
    assert "-m sentinel --stop" in text and "--force" in text


def test_run_script_has_no_parse_time_expansion_trap():
    """%PYEXE% is set by 'call :find_python'; it must not be expanded inside a parenthesised block."""
    text = read("run_sentinel.bat")
    depth, offenders = 0, []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("rem"):
            continue
        if depth > 0 and re.search(r"%(PYEXE|PASS)%", line):
            offenders.append(line)
        depth += line.count("(") - line.count(")") if not stripped.lower().startswith(("echo", "set ")) else 0
        depth = max(depth, 0)
    assert offenders == []
    assert "%PYEXE% -m venv" in text and "--self-check" in text


def test_scripts_use_crlf_ready_content_and_no_admin():
    for name in ("run_sentinel.bat", "stop_sentinel.bat", "run_tests.bat", "build_exe.bat", "diagnose_sentinel.bat"):
        t = read(name).lower()
        assert "runas" not in t and "start-process -verb" not in t and "net session" not in t


def test_docs_and_version_are_consistent():
    from sentinel import RELEASE_LABEL, __version__

    assert RELEASE_LABEL == "V5" and __version__.startswith("5.")
    for name in ("README.md", "docs/ARCHITECTURE.md"):
        assert "V6" not in read(name).replace("never a V6", "").replace("no V6", "").replace("There is no V6", "")


# ---------------------------------------------------------------------- tray
def test_tray_unavailable_gives_visible_feedback(ctrl, qapp):
    from sentinel.ui.main_window import MainWindow
    from sentinel.ui.tray import TrayController

    w = MainWindow(ctrl)
    tray = TrayController(ctrl, w)                                          # offscreen has no system tray
    assert ctrl.tray_available is False
    w._update_status()
    assert "No tray icon" in w.status_left.text()
    ctrl.quitting = True                      # closing without a tray would otherwise open the exit dialog
    w.close()


def test_tray_menu_has_all_required_entries(ctrl, qapp, monkeypatch):
    from PySide6.QtWidgets import QSystemTrayIcon

    from sentinel.ui.main_window import MainWindow
    from sentinel.ui.tray import TrayController

    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: True))
    w = MainWindow(ctrl)
    tray = TrayController(ctrl, w)
    tray._sync()
    texts = " | ".join(a.text() for a in tray.menu.actions() if a.text())
    for expected in ("Open control panel", "wallpaper", "animation", "monitoring", "Restore normal desktop", "Exit Sentinel"):
        assert expected.lower() in texts.lower(), (expected, texts)
    tray.hide()
    ctrl.quitting = True
    w.close()


# ----------------------------------------------------------------- autostart
def test_autostart_classification_detects_stale_registration():
    from sentinel.platform.windows import autostart

    cmd = 'C:\\x\\pythonw.exe C:\\Sentinel\\launch_sentinel.pyw --background'
    assert autostart.classify(None, cmd) == "off" and autostart.classify("", cmd) == "off"
    assert autostart.classify(cmd, cmd) == "on" and autostart.classify(cmd.upper(), cmd) == "on"
    assert autostart.classify('C:\\old\\pythonw.exe C:\\Old\\launch_sentinel.pyw --background', cmd) == "stale"
    assert autostart.status.__name__ == "status" and autostart.registered_command() is None   # off Windows


# ------------------------------------------------------------ windows-only
@pytest.mark.skipif(sys.platform != "win32", reason="needs real Windows user32/kernel32 and Explorer")
def test_windows_real_api_loads_and_finds_progman():
    from sentinel.platform.windows import desktop_layer

    desktop_layer.set_api(None)
    api = desktop_layer._api()
    assert api.find_window("Progman") != 0
    assert desktop_layer.taskbar_created_message() != 0
    assert api.process_image(api.find_window("Progman")) in ("explorer.exe", None)


@pytest.mark.skipif(sys.platform != "win32", reason="needs real Windows and a desktop session")
def test_windows_embed_and_detach_roundtrip():
    from PySide6.QtWidgets import QWidget

    from sentinel.platform.windows import desktop_layer

    w = QWidget()
    w.show()
    res = desktop_layer.attach(int(w.winId()), (0, 0, 400, 300))
    try:
        assert res.method in ("workerw_sibling", "progman_child", "not_found", "error") and isinstance(res.success, bool)
    finally:
        desktop_layer.detach(int(w.winId()))
        w.close()


# --------------------------------------------------- V3 audit A6 regression (identity check by tokens)
def test_force_stop_identity_uses_argument_tokens_not_path_substrings(tmp_path):
    """V2 matched 'vyrxone-sentinel' anywhere in the command line, so an unrelated script run from a folder
    with that name (e.g. with the project's own venv) was treated as Sentinel and would have been terminated."""
    from sentinel.core.instance import _is_sentinel_cmdline

    folder = tmp_path / "VYRXONE-Sentinel-V3"
    folder.mkdir()
    script = folder / "other_tool.py"
    script.write_text("import time; time.sleep(60)")
    victim = subprocess.Popen([sys.executable, str(script)], cwd=str(folder))
    try:
        assert not instance.looks_like_sentinel(victim.pid)
        assert "NOT stopped" in instance.force_stop(victim.pid) and victim.poll() is None
    finally:
        victim.kill()
    assert not _is_sentinel_cmdline(["C:\\Tools\\VYRXONE-Sentinel-V3\\.venv\\Scripts\\python.exe", "backup.py"])
    assert not _is_sentinel_cmdline([])
    assert not _is_sentinel_cmdline(["python", "-m", "sentinelx"])
    assert _is_sentinel_cmdline(["C:\\x\\.venv\\Scripts\\python.exe", "-m", "sentinel", "--panel"])
    assert _is_sentinel_cmdline(["C:\\x\\.venv\\Scripts\\pythonw.exe", "C:\\x\\launch_sentinel.pyw"])
    assert _is_sentinel_cmdline(["C:\\x\\dist\\VYRXONE-Sentinel\\VYRXONE-Sentinel.exe"])
