"""Monitor identity and selection (pure Python so it is unit-testable without displays)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class ScreenInfo:
    id: str                      # stable across reconnects: manufacturer|model|serial, else the OS name
    name: str
    x: int                       # native (physical) virtual-desktop position; may be negative
    y: int
    width_px: int                # physical pixels
    height_px: int
    dpr: float
    primary: bool

    @property
    def rect_px(self) -> tuple[int, int, int, int]:
        return (self.x, self.y, self.width_px, self.height_px)

    def label(self) -> str:
        return f"{self.name} ({self.width_px}x{self.height_px}{' primary' if self.primary else ''})"


def stable_id(manufacturer: str, model: str, serial: str, name: str) -> str:
    """Prefer EDID-style metadata; the OS name (\\\\.\\DISPLAY1) can change between boots."""
    parts = [p.strip() for p in (manufacturer, model, serial)]
    if any(parts[:2]) and parts[2]:
        return "|".join(parts)
    return (name or "unknown").strip() or "unknown"


def disambiguate(infos: list[ScreenInfo]) -> list[ScreenInfo]:
    """Two identical monitors without serials would share an id; suffix them in position order."""
    seen: dict[str, int] = {}
    out = []
    for info in sorted(infos, key=lambda i: (i.x, i.y)):
        n = seen.get(info.id, 0)
        seen[info.id] = n + 1
        out.append(info if n == 0 else ScreenInfo(f"{info.id}#{n + 1}", info.name, info.x, info.y,
                                                    info.width_px, info.height_px, info.dpr, info.primary))
    return out


def native_rect(geometry_xy: tuple[int, int], logical_size: tuple[int, int], dpr: float) -> tuple[int, int, int, int]:
    """Qt 6 reports a screen's top-left in native pixels and its size in device-independent pixels.
    Only the size is scaled (V1 also scaled the position, which is expected to be wrong on mixed-DPI layouts; unverified on Windows)."""
    (x, y), (w, h) = geometry_xy, logical_size
    return (int(x), int(y), max(1, round(w * dpr)), max(1, round(h * dpr)))


def select_screens(infos: Iterable[ScreenInfo], mode: str, selected_id: str = "") -> tuple[list[ScreenInfo], str]:
    """Returns (screens, note). *note* is non-empty whenever the request could not be honoured."""
    infos = list(infos)
    if not infos:
        return [], "No display was detected."
    primary = next((i for i in infos if i.primary), infos[0])
    if mode == "all":
        return infos, ""
    if mode == "selected":
        for i in infos:
            if i.id == selected_id:
                return [i], ""
        return [primary], ("The selected monitor is not connected; the primary monitor is used instead."
                           if selected_id else "No monitor is selected yet; the primary monitor is used.")
    return [primary], ""
