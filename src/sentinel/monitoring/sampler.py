"""System sampling with psutil.

* One background thread (``Sampler``) - never one thread per sample.
* Every metric that cannot be read is ``None`` and an explanation is added to
  ``Sample.errors``; nothing is estimated or faked.
"""

from __future__ import annotations

import logging
import os
import platform
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import psutil

log = logging.getLogger("sentinel.monitor")

PROCESS_LIMIT = 30  # top-N by CPU plus top-N by memory are kept
MAX_DISKS = 12


@dataclass(frozen=True)
class DiskInfo:
    mountpoint: str
    fstype: str
    total: int
    used: int
    percent: float


@dataclass(frozen=True)
class ProcInfo:
    pid: int
    name: str
    cpu_percent: Optional[float]  # share of total CPU capacity; None while warming up
    rss: Optional[int]            # resident memory in bytes; None if access denied


@dataclass(frozen=True)
class Sample:
    timestamp: float
    cpu_percent: Optional[float] = None
    cpu_count: Optional[int] = None
    memory_total: Optional[int] = None
    memory_used: Optional[int] = None
    memory_percent: Optional[float] = None
    disks: tuple[DiskInfo, ...] = ()
    system_disk_percent: Optional[float] = None
    processes: Optional[tuple[ProcInfo, ...]] = None  # None = not collected this cycle
    processes_total: int = 0
    processes_restricted: int = 0  # processes whose details were access-denied
    battery_percent: Optional[float] = None
    battery_plugged: Optional[bool] = None
    temperature_c: Optional[float] = None
    uptime_s: Optional[float] = None
    errors: tuple[str, ...] = field(default_factory=tuple)
    duration_ms: float = 0.0


def system_root() -> str:
    if os.name == "nt":
        return os.environ.get("SystemDrive", "C:") + "\\"
    return "/"


def get_system_info() -> dict[str, str]:
    """Static facts about this machine; unavailable items are reported as such."""
    info: dict[str, str] = {
        "Operating system": platform.platform(),
        "Machine": platform.machine() or "unavailable",
        "Python": platform.python_version(),
    }
    try:
        logical = psutil.cpu_count(logical=True)
        physical = psutil.cpu_count(logical=False)
        info["CPU cores"] = f"{physical or '?'} physical / {logical or '?'} logical"
    except Exception:
        info["CPU cores"] = "unavailable"
    try:
        info["Installed memory"] = f"{psutil.virtual_memory().total / 2**30:.1f} GiB"
    except Exception:
        info["Installed memory"] = "unavailable"
    try:
        from datetime import datetime

        info["Last boot"] = datetime.fromtimestamp(psutil.boot_time()).strftime("%Y-%m-%d %H:%M")
    except Exception:
        info["Last boot"] = "unavailable"
    return info


def _collect_disks(errors: list[str]) -> tuple[tuple[DiskInfo, ...], Optional[float]]:
    disks: list[DiskInfo] = []
    seen: set[str] = set()
    try:
        parts = psutil.disk_partitions(all=False)
    except Exception as exc:
        errors.append(f"disk list unavailable: {exc}")
        parts = []
    for part in parts:
        opts = (part.opts or "").lower()
        if "cdrom" in opts or not part.fstype or part.mountpoint in seen:
            continue
        seen.add(part.mountpoint)
        try:
            u = psutil.disk_usage(part.mountpoint)
        except (OSError, PermissionError):
            continue  # empty card reader, locked volume, ...
        disks.append(DiskInfo(part.mountpoint, part.fstype, u.total, u.used, float(u.percent)))
        if len(disks) >= MAX_DISKS:
            break
    system_percent: Optional[float] = None
    root = system_root()
    try:
        system_percent = float(psutil.disk_usage(root).percent)
    except Exception as exc:
        errors.append(f"system disk usage unavailable: {exc}")
        if disks:
            system_percent = disks[0].percent
    return tuple(disks), system_percent


def _collect_processes(cpu_count: int, primed: bool) -> tuple[tuple[ProcInfo, ...], int, int]:
    rows: list[ProcInfo] = []
    total = restricted = 0
    ncpu = max(1, cpu_count)
    for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info"]):
        info = proc.info
        pid = info.get("pid")
        name = info.get("name") or f"pid {pid}"
        if pid == 0 or name == "System Idle Process":
            continue
        total += 1
        mem = info.get("memory_info")
        cpu = info.get("cpu_percent")
        if mem is None or cpu is None:
            restricted += 1
        cpu_share = None
        if primed and cpu is not None:
            cpu_share = max(0.0, min(100.0, cpu / ncpu))
        rows.append(ProcInfo(pid, name, cpu_share, mem.rss if mem is not None else None))
    by_cpu = sorted(rows, key=lambda r: (r.cpu_percent or 0.0, r.rss or 0), reverse=True)
    by_mem = sorted(rows, key=lambda r: r.rss or 0, reverse=True)
    picked: dict[int, ProcInfo] = {}
    for row in by_cpu[:PROCESS_LIMIT] + by_mem[:PROCESS_LIMIT]:
        picked[row.pid] = row
    final = sorted(picked.values(), key=lambda r: (r.cpu_percent or 0.0, r.rss or 0), reverse=True)
    return tuple(final), total, restricted


def collect_sample(collect_processes: bool = False, processes_primed: bool = True) -> Sample:
    """Take one measurement. Every section is isolated so one failure cannot lose the rest."""
    t0 = time.perf_counter()
    errors: list[str] = []
    kwargs: dict = {"timestamp": time.time()}

    try:
        kwargs["cpu_percent"] = float(psutil.cpu_percent(interval=None))
        kwargs["cpu_count"] = psutil.cpu_count(logical=True) or 1
    except Exception as exc:
        errors.append(f"CPU unavailable: {exc}")

    try:
        vm = psutil.virtual_memory()
        kwargs.update(memory_total=int(vm.total), memory_used=int(vm.total - vm.available),
                      memory_percent=float(vm.percent))
    except Exception as exc:
        errors.append(f"memory unavailable: {exc}")

    disks, system_percent = _collect_disks(errors)
    kwargs.update(disks=disks, system_disk_percent=system_percent)

    if collect_processes:
        try:
            procs, total, restricted = _collect_processes(
                kwargs.get("cpu_count") or psutil.cpu_count() or 1, processes_primed)
            kwargs.update(processes=procs, processes_total=total, processes_restricted=restricted)
        except Exception as exc:
            errors.append(f"process list unavailable: {exc}")

    battery_fn = getattr(psutil, "sensors_battery", None)
    if battery_fn:
        try:
            bat = battery_fn()
            if bat is not None:
                kwargs.update(battery_percent=float(bat.percent), battery_plugged=bat.power_plugged)
        except Exception:
            pass  # optional sensor
    temp_fn = getattr(psutil, "sensors_temperatures", None)  # not provided on Windows
    if temp_fn:
        try:
            temps = temp_fn()
            readings = [t.current for group in temps.values() for t in group if t.current]
            if readings:
                kwargs["temperature_c"] = float(max(readings))
        except Exception:
            pass  # optional sensor

    try:
        kwargs["uptime_s"] = max(0.0, time.time() - psutil.boot_time())
    except Exception:
        pass

    kwargs["errors"] = tuple(errors)
    kwargs["duration_ms"] = (time.perf_counter() - t0) * 1000.0
    return Sample(**kwargs)


class Sampler:
    """Runs ``collect_sample`` on a single daemon thread at a fixed interval."""

    def __init__(self, callback: Callable[[Sample], None], interval: float = 2.0,
                 collect_processes: bool = False) -> None:
        self._callback = callback
        self._interval = max(0.5, float(interval))
        self._collect_processes = collect_processes
        self._process_interval = 6.0       # V2: the expensive process scan runs at most this often
        self._last_proc_scan = 0.0
        self._procs_primed = False
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self.last_sample: Optional[Sample] = None

    @property
    def interval(self) -> float:
        return self._interval

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def set_interval(self, seconds: float) -> None:
        self._interval = max(0.5, float(seconds))

    def set_process_interval(self, seconds: float) -> None:
        self._process_interval = max(1.0, float(seconds))

    def set_collect_processes(self, enabled: bool) -> None:
        with self._lock:
            if enabled != self._collect_processes:
                self._collect_processes = enabled
                if not enabled:
                    self._procs_primed = False

    def start(self) -> None:
        if self.is_running:
            return
        self._stop.clear()
        psutil.cpu_percent(interval=None)  # prime: the first reading is always meaningless
        self._thread = threading.Thread(target=self._run, name="sentinel-sampler", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> bool:
        """Stop the thread. Returns True if it has terminated."""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
            if thread.is_alive():
                return False
        self._thread = None
        return True

    def _run(self) -> None:
        if self._stop.wait(0.6):  # let the first CPU interval elapse
            return
        while not self._stop.is_set():
            started = time.monotonic()
            with self._lock:
                collect = self._collect_processes
                primed = self._procs_primed
            if collect and started - self._last_proc_scan < self._process_interval and self._last_proc_scan:
                collect = False                 # throttled: reuse the previous process list in the UI
            elif collect:
                self._last_proc_scan = started
            try:
                sample = collect_sample(collect, primed)
            except Exception as exc:  # defensive: the loop must survive anything
                log.exception("sampling failed")
                sample = Sample(timestamp=time.time(), errors=(f"sampling failed: {exc}",))
            if collect:
                with self._lock:
                    self._procs_primed = True
            self.last_sample = sample
            try:
                self._callback(sample)
            except Exception:
                log.exception("sample callback failed")
            remaining = self._interval - (time.monotonic() - started)
            if self._stop.wait(max(0.2, remaining)):
                break
