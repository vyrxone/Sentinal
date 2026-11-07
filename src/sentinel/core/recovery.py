"""Bounded retry policy for re-attaching to the desktop (pure Python)."""

from __future__ import annotations

from typing import Sequence

DEFAULT_DELAYS_MS = (800, 1500, 3000, 6000, 10000)


class RecoveryPolicy:
    """Gives out at most ``len(delays)`` retry delays, then reports exhaustion.

    Retrying forever would leave the user with an invisible or flickering wallpaper, so after the
    last attempt the caller must fall back to a normal window (or the safe hidden mode).
    """

    def __init__(self, delays_ms: Sequence[int] = DEFAULT_DELAYS_MS) -> None:
        self.delays_ms = tuple(int(d) for d in delays_ms)
        self.attempt = 0

    @property
    def exhausted(self) -> bool:
        return self.attempt >= len(self.delays_ms)

    def next_delay(self) -> int | None:
        if self.exhausted:
            return None
        d = self.delays_ms[self.attempt]
        self.attempt += 1
        return d

    def reset(self) -> None:
        self.attempt = 0
