import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def env_for(tmp_path, name):
    env = dict(os.environ)
    env.update(QT_QPA_PLATFORM="offscreen", SENTINEL_DATA_DIR=str(tmp_path / "data"),
               SENTINEL_INSTANCE_NAME=name, PYTHONPATH=str(ROOT / "src"))
    return env


def run(args, env, timeout=60):
    return subprocess.run([sys.executable, "-m", "sentinel", *args], env=env, capture_output=True,
                          text=True, timeout=timeout, cwd=str(ROOT))


def events_of(tmp_path):
    con = sqlite3.connect(tmp_path / "data" / "sentinel.db")
    try:
        return [r[0] for r in con.execute("SELECT event_type FROM events ORDER BY id")]
    finally:
        con.close()


def test_version_and_self_check(tmp_path):
    env = env_for(tmp_path, "t-selfcheck")
    assert "V5" in run(["--version"], env).stdout
    out = run(["--self-check"], env)
    assert out.returncode == 0 and "Self-check passed" in out.stdout
    assert "[ok] PySide6" in out.stdout and "schema v5" in out.stdout


@pytest.mark.parametrize("mode", ["--panel", "--wallpaper"])
def test_app_starts_runs_and_exits_cleanly(tmp_path, mode):
    env = env_for(tmp_path, f"t-start{mode}")
    res = run([mode, "--quit-after", "3"], env)
    assert res.returncode == 0, res.stderr
    assert "Traceback" not in res.stderr
    kinds = events_of(tmp_path)
    assert kinds[0] == "app_started" and kinds[-1] == "app_stopped"


def test_second_instance_is_refused_and_stop_command_exits_first(tmp_path):
    env = env_for(tmp_path, f"t-single-{os.getpid()}")
    first = subprocess.Popen([sys.executable, "-m", "sentinel", "--panel"], env=env, cwd=str(ROOT),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            probe = run(["--show"], env)
            if "asked to open" in probe.stdout:
                break
            time.sleep(0.4)
        else:
            pytest.fail("first instance never became reachable")
        second = run(["--panel"], env)
        assert second.returncode == 0 and "already running" in second.stdout     # no duplicate instance
        stop = run(["--stop"], env)
        assert "asked to exit" in stop.stdout
        assert first.wait(timeout=30) == 0                                       # clean exit, no kill needed
    finally:
        if first.poll() is None:
            first.kill()
    assert events_of(tmp_path)[-1] == "app_stopped"
    assert "not running" in run(["--stop"], env).stdout                          # idempotent


def test_unwritable_data_dir_gives_clear_error_not_traceback(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    env = env_for(tmp_path, "t-baddata")
    env["SENTINEL_DATA_DIR"] = str(blocker / "sub")
    res = run(["--panel", "--quit-after", "1"], env)
    assert res.returncode == 2 and "cannot start" in res.stderr and "Traceback" not in res.stderr
