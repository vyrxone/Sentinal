"""Optional "Start with Windows" using the per-user Run registry key.

Disabled by default. Only ever changed by an explicit user action in Settings,
touches only HKCU (no administrator rights), and is fully reversible.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Optional

from ...core import paths

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "VYRXONE Sentinel"


class AutostartError(Exception):
    pass


def is_supported() -> bool:
    return sys.platform == "win32"


def build_command(frozen: Optional[bool] = None, executable: Optional[str] = None,
                  launcher: Optional[str] = None) -> str:
    """Command line stored in the Run key (pure function; unit-tested)."""
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    exe = executable or sys.executable
    if frozen:
        return subprocess.list2cmdline([exe, "--background"])
    pyw = Path(exe).with_name("pythonw.exe")
    interpreter = str(pyw) if pyw.exists() else exe
    script = launcher or str(paths.resource_path("launch_sentinel.pyw"))
    return subprocess.list2cmdline([interpreter, script, "--background"])


def is_enabled() -> bool:
    if not is_supported():
        return False
    import winreg  # type: ignore[import-not-found]

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, VALUE_NAME)
            return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise AutostartError(f"Could not read the startup setting: {exc}") from exc


def registered_command() -> Optional[str]:
    """The command currently stored in the Run key, or None."""
    if not is_supported():
        return None
    import winreg  # type: ignore[import-not-found]

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            return str(winreg.QueryValueEx(key, VALUE_NAME)[0])
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise AutostartError(f"Could not read the startup setting: {exc}") from exc


def classify(registered: Optional[str], expected: str) -> str:
    """'off' | 'on' | 'stale' (registered, but pointing at a different/moved copy). Pure; unit-tested."""
    if not registered:
        return "off"
    return "on" if registered.strip().lower() == expected.strip().lower() else "stale"


def status() -> str:
    return classify(registered_command(), build_command())


def enable() -> str:
    if not is_supported():
        raise AutostartError("Start with Windows is only available on Windows.")
    import winreg  # type: ignore[import-not-found]

    command = build_command()
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command)
    except OSError as exc:
        raise AutostartError(f"Could not enable start with Windows: {exc}") from exc
    return command


def disable() -> None:
    if not is_supported():
        return
    import winreg  # type: ignore[import-not-found]

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, VALUE_NAME)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise AutostartError(f"Could not disable start with Windows: {exc}") from exc
