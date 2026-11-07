"""Locations of Sentinel's local data. Nothing here touches the network."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def data_dir() -> Path:
    """Persistent per-user data directory (never the temp directory).

    Override with the SENTINEL_DATA_DIR environment variable or --data-dir.
    """
    override = os.environ.get("SENTINEL_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(root) / "VYRXONE" / "Sentinel"
    root = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(root) / "vyrxone-sentinel"


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return data_dir() / "sentinel.db"


def log_dir() -> Path:
    return data_dir() / "logs"


def backup_dir() -> Path:
    return data_dir() / "backups"


def resource_path(*parts: str) -> Path:
    """Path to a bundled resource, in source checkouts and PyInstaller builds."""
    base = getattr(sys, "_MEIPASS", None)
    root = Path(base) if base else Path(__file__).resolve().parents[3]
    return root.joinpath(*parts)


def is_inside(path: str | os.PathLike, folder: str | os.PathLike) -> bool:
    """True if *path* is *folder* or lies below it (case-insensitive on Windows)."""
    try:
        p = os.path.normcase(os.path.abspath(path))
        f = os.path.normcase(os.path.abspath(folder))
    except (OSError, ValueError):
        return False
    return p == f or p.startswith(f.rstrip(os.sep) + os.sep)
