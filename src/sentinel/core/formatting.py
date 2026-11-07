"""Small text-formatting helpers (pure Python, unit-tested)."""

from __future__ import annotations

from typing import Optional

NA = "—"  # em dash: "not available" - used instead of inventing a value


def human_bytes(n: Optional[float]) -> str:
    if n is None:
        return NA
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(value) < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return NA  # pragma: no cover


def percent(value: Optional[float], digits: int = 0) -> str:
    return NA if value is None else f"{value:.{digits}f}%"


def human_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return NA
    s = int(max(0, seconds))
    days, s = divmod(s, 86400)
    hours, s = divmod(s, 3600)
    minutes, s = divmod(s, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {s}s"
    return f"{s}s"


def freshness(age_seconds: Optional[float], interval: float) -> tuple[str, str]:
    """(state, text) where state is 'live', 'stale' or 'off'."""
    if age_seconds is None:
        return "off", "no data yet"
    if age_seconds > max(3 * interval, 6.0):
        return "stale", f"stale ({human_duration(age_seconds)} old)"
    return "live", f"live ({age_seconds:.0f}s ago)" if age_seconds >= 1 else "live"


def short_hash(digest: Optional[str], n: int = 12) -> str:
    return NA if not digest else digest[:n] + "…"
