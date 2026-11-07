import sys
import time

import pytest

from sentinel.core import ipc, paths
from sentinel.platform.windows import autostart, desktop_layer


def test_autostart_command_for_source_and_frozen_builds():
    cmd = autostart.build_command(frozen=False, executable=r"C:\Py\pythonw_nonexistent.exe",
                                  launcher=r"C:\Sentinel App\launch_sentinel.pyw")
    assert cmd.endswith("--background")
    assert '"C:\\Sentinel App\\launch_sentinel.pyw"' in cmd      # paths with spaces are quoted
    frozen = autostart.build_command(frozen=True, executable=r"C:\Program Files\Sentinel\Sentinel.exe")
    assert frozen == '"C:\\Program Files\\Sentinel\\Sentinel.exe" --background'


@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows behaviour")
def test_windows_only_modules_degrade_gracefully_elsewhere():
    assert not autostart.is_supported() and autostart.is_enabled() is False
    with pytest.raises(autostart.AutostartError):
        autostart.enable()
    autostart.disable()                                          # no-op, must not raise
    assert desktop_layer.find_desktop_host() is None
    res = desktop_layer.attach(1234, (0, 0, 100, 100))
    assert res.success is False and res.method == "unsupported"
    assert desktop_layer.is_attached(1, 2) is False
    desktop_layer.detach(1234)
    desktop_layer.refresh_desktop()


def test_no_machine_specific_absolute_paths_in_package():
    import re
    from pathlib import Path

    root = Path(paths.resource_path("src"))
    pattern = re.compile(r"[A-Za-z]:\\\\Users|/home/[a-z]+/|/Users/[A-Za-z]+/")
    offenders = [str(p) for p in root.rglob("*.py") if pattern.search(p.read_text(encoding="utf-8"))]
    assert offenders == []


def test_data_dir_override_and_is_not_temp(monkeypatch, tmp_path):
    monkeypatch.setenv("SENTINEL_DATA_DIR", str(tmp_path / "custom"))
    assert paths.data_dir() == tmp_path / "custom"
    monkeypatch.delenv("SENTINEL_DATA_DIR")
    default = paths.data_dir()
    assert "tmp" not in default.parts and "Temp" not in default.parts   # persistent, not temp
    assert "vyrxone" in str(default).lower()
    assert paths.db_path() == default / "sentinel.db"


def test_is_inside():
    assert paths.is_inside("/a/b/c", "/a/b")
    assert paths.is_inside("/a/b", "/a/b")
    assert not paths.is_inside("/a/bc", "/a/b")
    assert not paths.is_inside("/x", "/a/b")


# -------------------------------------------------------------- single instance
CLIENT_SNIPPET = """
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, sys.argv[1])
from PySide6.QtCore import QCoreApplication
from sentinel.core import ipc
app = QCoreApplication([])
print(",".join(str(ipc.send_command(cmd, sys.argv[2])) for cmd in ("ping", "show", "quit")))
"""


def test_ipc_ping_show_quit_from_a_separate_process_like_stop_script(tmp_path):
    """The stop script runs as its own process, so the client is a real subprocess here."""
    import subprocess

    from PySide6.QtCore import QCoreApplication

    app = QCoreApplication.instance() or QCoreApplication([])
    name = f"sentinel-test-{int(time.time() * 1000) % 10**8}"
    server = ipc.InstanceServer(name)
    received = []
    server.command_received.connect(received.append)
    assert server.listen()

    src = str(paths.resource_path("src"))
    proc = subprocess.Popen([sys.executable, "-I", "-c", CLIENT_SNIPPET, src, name],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    end = time.time() + 20
    while proc.poll() is None and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    out, err = proc.communicate(timeout=5)
    app.processEvents()
    assert out.strip() == "pong,ok,ok", err
    assert received == ["show", "quit"]

    with pytest.raises(ValueError):
        ipc.send_command("rm -rf /", name)                       # not a whitelisted command
    server.close()
    assert ipc.send_command("ping", name, timeout_ms=300) is None  # nothing listening any more


def test_ipc_no_instance_returns_none():
    from PySide6.QtCore import QCoreApplication

    QCoreApplication.instance() or QCoreApplication([])
    assert ipc.send_command("ping", "sentinel-nobody-home-xyz", timeout_ms=200) is None
