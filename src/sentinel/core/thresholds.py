"""Warning-threshold rules. Pure Python so it can be unit-tested without Qt."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional

METRIC_LABELS = {"cpu": "CPU", "ram": "Memory", "disk": "System disk"}


@dataclass(frozen=True)
class Alert:
    metric: str
    kind: str  # "warn" when a limit is crossed, "clear" when it recovers
    value: float
    threshold: float
    message: str


class ThresholdEngine:
    """Raises a warning after *sustained* samples at/above a limit and a single
    "clear" once the value falls ``hysteresis`` points below it."""

    def __init__(self, cpu: float = 85, ram: float = 85, disk: float = 90,
                 sustained: int = 3, hysteresis: float = 5.0) -> None:
        self.limits = {"cpu": float(cpu), "ram": float(ram), "disk": float(disk)}
        self.sustained = max(1, int(sustained))
        self.hysteresis = float(hysteresis)
        self._over = {m: 0 for m in self.limits}
        self._active = {m: False for m in self.limits}

    def configure(self, *, cpu: Optional[float] = None, ram: Optional[float] = None,
                  disk: Optional[float] = None, sustained: Optional[int] = None) -> None:
        if cpu is not None:
            self.limits["cpu"] = float(cpu)
        if ram is not None:
            self.limits["ram"] = float(ram)
        if disk is not None:
            self.limits["disk"] = float(disk)
        if sustained is not None:
            self.sustained = max(1, int(sustained))

    def active_metrics(self) -> list[str]:
        return [m for m, on in self._active.items() if on]

    def _needed(self, metric: str) -> int:
        return 1 if metric == "disk" else self.sustained

    def evaluate(self, values: Mapping[str, Optional[float]]) -> list[Alert]:
        alerts: list[Alert] = []
        for metric, limit in self.limits.items():
            value = values.get(metric)
            if value is None:  # metric unavailable: leave state untouched
                continue
            label = METRIC_LABELS[metric]
            if value >= limit:
                self._over[metric] += 1
                if not self._active[metric] and self._over[metric] >= self._needed(metric):
                    self._active[metric] = True
                    alerts.append(Alert(metric, "warn", value, limit,
                                        f"{label} at {value:.0f}% (warning limit {limit:.0f}%)"))
            else:
                self._over[metric] = 0
                if self._active[metric] and value < limit - self.hysteresis:
                    self._active[metric] = False
                    alerts.append(Alert(metric, "clear", value, limit,
                                        f"{label} back to {value:.0f}% (limit {limit:.0f}%)"))
        return alerts


class SustainedLoad:
    """Detects sustained high CPU load so the UI can reduce effects."""

    def __init__(self, high: float = 90.0, low: float = 70.0, samples: int = 5) -> None:
        self.high, self.low, self.samples = high, low, max(1, samples)
        self._count = 0
        self.active = False

    def update(self, cpu: Optional[float]) -> Optional[str]:
        """Returns "entered", "exited" or None."""
        if cpu is None:
            return None
        if cpu >= self.high:
            self._count += 1
            if not self.active and self._count >= self.samples:
                self.active = True
                return "entered"
        else:
            self._count = 0
            if self.active and cpu < self.low:
                self.active = False
                return "exited"
        return None
