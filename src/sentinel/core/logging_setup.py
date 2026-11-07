"""Application logging to a rotating local file. No secrets are ever logged."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from . import paths


from ..ai.privacy import ScrubFilter


def setup_logging(level: int = logging.INFO, to_file: bool = True) -> logging.Logger:
    logger = logging.getLogger("sentinel")
    if getattr(logger, "_sentinel_configured", False):
        return logger
    logger.setLevel(level)
    logger.propagate = False
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    if to_file:
        try:
            paths.ensure_dir(paths.log_dir())
            handler = RotatingFileHandler(
                paths.log_dir() / "sentinel.log",
                maxBytes=512 * 1024,
                backupCount=3,
                encoding="utf-8",
            )
            handler.setFormatter(fmt)
            handler.addFilter(ScrubFilter())
            logger.addHandler(handler)
        except OSError:
            pass  # logging must never stop the app from starting
    if sys.stderr is not None:  # pythonw.exe has no stderr
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(fmt)
        stream.addFilter(ScrubFilter())
        logger.addHandler(stream)
    logger._sentinel_configured = True  # type: ignore[attr-defined]
    return logger
