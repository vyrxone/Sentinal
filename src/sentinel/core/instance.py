"""Single-instance guard (lock file) and safe forced stop.

The IPC ping alone has a start-up race: two processes launched at the same instant can both see "no
instance" before either listens. A QLockFile is taken first, so only one of them proceeds.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

import psutil
from PySide6.QtCore import QLockFile

from . import paths

log = logging.getLogger("sentinel.instance")


def lock_path() -> Path:
    d = paths.data_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / "sentinel.lock"


def acquire(timeout_ms: int = 300) -> tuple[Optional[QLockFile], Optional[int]]:
    """Returns (lock, None) when this process is the only instance, else (None, other_pid_or_None)."""
    lock = QLockFile(str(lock_path()))
    lock.setStaleLockTime(0)            # a lock whose owner process is gone is stale at once
    if lock.tryLock(timeout_ms):
        return lock, None
    return None, _owner_pid(lock)


def _owner_pid(lock: QLockFile) -> Optional[int]:
    """PySide6 versions differ in what getLockInfo returns ((ok, pid, host, app) or (pid, host, app))."""
    try:
        info = lock.getLockInfo()
    except Exception:
        return None
    try:
        if len(info) == 4:
            ok, pid = bool(info[0]), info[1]
            return int(pid) if ok and pid else None
        pid = info[0]
        return int(pid) if pid else None
    except (TypeError, ValueError):
        return None


def _is_sentinel_cmdline(args: list[str]) -> bool:
    """Decide from the argument *tokens*, never from a substring of the whole command line.

    A substring test would also match any unrelated program that merely lives in a folder called
    ``VYRXONE-Sentinel-V3`` (for example another script run with the project's own venv) - V3 audit A6.
    """
    if not args:
        return False
    norm = [a.replace("\\", "/") for a in args]
    exe = norm[0].rsplit("/", 1)[-1].lower()
    if exe in ("vyrxone-sentinel.exe", "vyrxone-sentinel"):          # the PyInstaller build
        return True
    for i, tok in enumerate(norm):
        low = tok.rsplit("/", 1)[-1].lower()
        if low == "launch_sentinel.pyw":
            return True
        if tok == "-m" and i + 1 < len(norm) and norm[i + 1].lower() == "sentinel":
            return True
    return False


def looks_like_sentinel(pid: int) -> bool:
    """True only for a process that is clearly this application (never an unrelated Python process)."""
    try:
        args = psutil.Process(pid).cmdline()
    except (psutil.Error, OSError):
        return False
    return _is_sentinel_cmdline(args)


def force_stop(pid: Optional[int], wait_s: float = 5.0) -> str:
    """Terminate the recorded instance only after verifying its identity. Returns a human message."""
    if not pid:
        return "No running Sentinel process was recorded; nothing to force-stop."
    if pid == psutil.Process().pid:
        return "Refusing to stop the current process."
    if not looks_like_sentinel(pid):
        return (f"Process {pid} does not look like VYRXONE Sentinel; it was NOT stopped. "
                "Close it manually if you are sure it is Sentinel.")
    try:
        proc = psutil.Process(pid)
        proc.terminate()
        try:
            proc.wait(wait_s)
        except psutil.TimeoutExpired:
            return f"Sentinel (process {pid}) did not exit within {wait_s:.0f}s after a terminate request."
    except psutil.NoSuchProcess:
        pass
    except psutil.Error as exc:
        return f"Could not stop process {pid}: {exc}"
    return f"Sentinel (process {pid}) was stopped."


def wait_until_gone(ping, timeout_s: float = 8.0) -> bool:
    """Poll *ping* (returns truthy while an instance answers) until it stops answering."""
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        if not ping():
            return True
        time.sleep(0.2)
    return False
