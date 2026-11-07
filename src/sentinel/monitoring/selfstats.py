"""Sentinel's own resource use and measured render statistics (nothing here is estimated)."""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from typing import Optional

import psutil


class RenderStats:
    """Real painted-frames-per-second and paint time reported by each wallpaper scene."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._scenes: dict[str, tuple[float, float, bool, float]] = {}

    def report(self, scene_id: str, fps: float, paint_ms: float, animating: bool) -> None:
        with self._lock:
            self._scenes[scene_id] = (fps, paint_ms, animating, time.monotonic())

    def forget(self, scene_id: str) -> None:
        with self._lock:
            self._scenes.pop(scene_id, None)

    def snapshot(self) -> dict:
        with self._lock:
            rows = {k: v for k, v in self._scenes.items() if time.monotonic() - v[3] < 5}
        if not rows:
            return {"scenes": 0, "fps": None, "paint_ms": None, "animating": False}
        return {"scenes": len(rows), "fps": sum(v[0] for v in rows.values()),
                "paint_ms": max(v[1] for v in rows.values()), "animating": any(v[2] for v in rows.values())}


class SelfMetrics:
    """CPU % and resident memory of the Sentinel process itself, with a short bounded history."""

    def __init__(self, history: int = 120) -> None:
        self._proc: Optional[psutil.Process] = None
        try:
            self._proc = psutil.Process(os.getpid())
            self._proc.cpu_percent(None)
        except Exception:
            self._proc = None
        self.cpu: deque[float] = deque(maxlen=history)
        self.rss: deque[int] = deque(maxlen=history)

    def poll(self) -> tuple[Optional[float], Optional[int]]:
        if self._proc is None:
            return None, None
        try:
            cpu = self._proc.cpu_percent(None) / max(1, psutil.cpu_count() or 1)
            rss = int(self._proc.memory_info().rss)
        except Exception:
            return None, None
        self.cpu.append(cpu)
        self.rss.append(rss)
        return cpu, rss
