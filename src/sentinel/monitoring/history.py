"""Bounded history buffers for graphs."""

from __future__ import annotations

from collections import deque
from typing import Optional

from .sampler import Sample

DEFAULT_LENGTH = 120  # 120 samples = 4 minutes at the default 2 s interval


class MetricHistory:
    def __init__(self, maxlen: int = DEFAULT_LENGTH) -> None:
        self.maxlen = maxlen
        self.cpu: deque[Optional[float]] = deque(maxlen=maxlen)
        self.ram: deque[Optional[float]] = deque(maxlen=maxlen)
        self.disk: deque[Optional[float]] = deque(maxlen=maxlen)

    def append(self, sample: Sample) -> None:
        self.cpu.append(sample.cpu_percent)
        self.ram.append(sample.memory_percent)
        self.disk.append(sample.system_disk_percent)

    def clear(self) -> None:
        self.cpu.clear()
        self.ram.clear()
        self.disk.clear()

    def __len__(self) -> int:
        return len(self.cpu)
