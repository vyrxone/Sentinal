"""Build the optional standalone executable folder with PyInstaller (--onedir, windowed).

Works on any OS so the build configuration can be tested away from Windows
(on Linux it produces a Linux binary; on Windows, VYRXONE-Sentinel.exe).
Outputs to ./dist and ./build inside this project only.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = "VYRXONE-Sentinel"


def keyring_args() -> list:
    """Bundle the Windows Credential Manager backend for AI key storage when keyring is installed (Windows)."""
    try:
        import keyring  # noqa: F401
    except Exception:
        return []
    return ["--hidden-import", "keyring.backends.Windows", "--collect-submodules", "keyring.backends",
            "--hidden-import", "win32ctypes.pywin32.win32cred", "--hidden-import", "win32ctypes.pywin32.pywintypes",
            "--copy-metadata", "keyring", "--copy-metadata", "pywin32-ctypes"]


def main() -> int:
    sep = os.pathsep  # ';' on Windows, ':' elsewhere - PyInstaller's --add-data separator
    if subprocess.call([sys.executable, str(ROOT / "scripts" / "make_assets.py")]) != 0:
        print("Could not generate icon assets.")
        return 1
    cmd = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--onedir",
        "--name", NAME,
        "--paths", str(ROOT / "src"),
        "--icon", str(ROOT / "assets" / "sentinel.ico"),
        "--add-data", f"{ROOT / 'config'}{sep}config",
        "--add-data", f"{ROOT / 'assets'}{sep}assets",
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT / "build"),
        *keyring_args(),
        "--exclude-module", "tkinter",
        "--exclude-module", "pytest",
        str(ROOT / "launch_sentinel.pyw"),
    ]
    print("Running:", " ".join(cmd))
    return subprocess.call(cmd, cwd=str(ROOT))


if __name__ == "__main__":
    sys.exit(main())
