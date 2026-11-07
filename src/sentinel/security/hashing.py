"""SHA-256 file hashing with cooperative cancellation."""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path
from typing import Optional

CHUNK_SIZE = 1024 * 1024


class HashCancelled(Exception):
    pass


def sha256_file(path: str | Path, cancel: Optional[threading.Event] = None,
                chunk_size: int = CHUNK_SIZE) -> str:
    """Hex SHA-256 of a file. Raises OSError (missing/denied) or HashCancelled."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            if cancel is not None and cancel.is_set():
                raise HashCancelled()
            block = fh.read(chunk_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()
