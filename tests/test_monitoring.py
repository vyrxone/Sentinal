import threading
import time

import psutil
import pytest

from sentinel.monitoring import sampler as sm
from sentinel.monitoring.history import MetricHistory
from sentinel.monitoring.sampler import Sample, Sampler, collect_sample, get_system_info


def test_collect_sample_returns_real_measurements():
    psutil.cpu_percent(interval=None)
    time.sleep(0.2)
    s = collect_sample(collect_processes=False)
    assert 0.0 <= s.cpu_percent <= 100.0
    assert s.cpu_count >= 1
    assert s.memory_total == psutil.virtual_memory().total
    assert 0.0 < s.memory_percent <= 100.0
    assert 0 < s.memory_used <= s.memory_total
    assert s.system_disk_percent is None or 0.0 <= s.system_disk_percent <= 100.0
    assert s.processes is None            # not requested -> not fabricated
    assert s.timestamp == pytest.approx(time.time(), abs=5)
    assert s.duration_ms >= 0


def test_collect_processes_priming_does_not_invent_cpu_values():
    first = collect_sample(collect_processes=True, processes_primed=False)
    assert first.processes
    assert all(p.cpu_percent is None for p in first.processes)    # warming up: unknown, not 0
    second = collect_sample(collect_processes=True, processes_primed=True)
    assert all(p.cpu_percent is None or 0 <= p.cpu_percent <= 100 for p in second.processes)
    assert any(p.rss for p in second.processes)
    assert second.processes_total >= len(second.processes) > 0


def test_each_section_fails_independently(monkeypatch):
    def broken(*a, **k):
        raise PermissionError("denied")

    monkeypatch.setattr(sm.psutil, "virtual_memory", broken)
    monkeypatch.setattr(sm.psutil, "disk_partitions", broken)
    monkeypatch.setattr(sm.psutil, "disk_usage", broken)
    monkeypatch.setattr(sm.psutil, "sensors_battery", broken, raising=False)
    monkeypatch.setattr(sm.psutil, "sensors_temperatures", broken, raising=False)
    s = collect_sample()
    assert s.memory_percent is None and s.memory_total is None      # unavailable, not faked
    assert s.system_disk_percent is None and s.disks == ()
    assert s.cpu_percent is not None                                  # unaffected section survives
    assert s.battery_percent is None and s.temperature_c is None
    assert any("memory unavailable" in e for e in s.errors)
    assert any("system disk usage unavailable" in e for e in s.errors)


def test_missing_optional_sensors_are_none(monkeypatch):
    monkeypatch.delattr(sm.psutil, "sensors_battery", raising=False)
    monkeypatch.delattr(sm.psutil, "sensors_temperatures", raising=False)
    s = collect_sample()
    assert s.battery_percent is None and s.temperature_c is None
    assert s.cpu_percent is not None


def test_process_access_denied_is_counted(monkeypatch):
    class FakeProc:
        def __init__(self, info):
            self.info = info

    fakes = [
        FakeProc({"pid": 1, "name": "ok.exe", "cpu_percent": 10.0,
                  "memory_info": type("M", (), {"rss": 1000})()}),
        FakeProc({"pid": 2, "name": "protected.exe", "cpu_percent": None, "memory_info": None}),
        FakeProc({"pid": 0, "name": "System Idle Process", "cpu_percent": 99.0, "memory_info": None}),
    ]
    monkeypatch.setattr(sm.psutil, "process_iter", lambda attrs=None: iter(fakes))
    procs, total, restricted = sm._collect_processes(cpu_count=2, primed=True)
    assert total == 2 and restricted == 1
    by_name = {p.name: p for p in procs}
    assert by_name["ok.exe"].cpu_percent == pytest.approx(5.0)        # normalised by core count
    assert by_name["protected.exe"].rss is None and by_name["protected.exe"].cpu_percent is None
    assert "System Idle Process" not in by_name


def test_history_is_bounded():
    h = MetricHistory(maxlen=5)
    for i in range(50):
        h.append(Sample(timestamp=i, cpu_percent=float(i), memory_percent=float(i), system_disk_percent=None))
    assert len(h) == 5
    assert list(h.cpu) == [45.0, 46.0, 47.0, 48.0, 49.0]
    assert list(h.disk) == [None] * 5
    h.clear()
    assert len(h) == 0


def test_sampler_runs_in_one_thread_and_stops_cleanly():
    got: list[Sample] = []
    done = threading.Event()

    def cb(sample):
        got.append(sample)
        if len(got) >= 2:
            done.set()

    before = threading.active_count()
    s = Sampler(cb, interval=0.5)
    s.start()
    s.start()                                    # second start must not spawn another thread
    assert threading.active_count() == before + 1
    assert done.wait(5)
    assert s.stop(timeout=3) is True
    assert not s.is_running
    assert threading.active_count() == before
    count = len(got)
    time.sleep(0.8)
    assert len(got) == count                     # really stopped
    assert s.last_sample is got[-1]


def test_sampler_survives_collection_and_callback_errors(monkeypatch):
    calls = {"n": 0}

    def flaky(collect, primed):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("psutil exploded")
        return Sample(timestamp=time.time(), cpu_percent=1.0)

    monkeypatch.setattr(sm, "collect_sample", flaky)
    seen = []
    done = threading.Event()

    def cb(sample):
        seen.append(sample)
        if len(seen) == 2:
            done.set()
        raise ValueError("bad listener")           # must not kill the loop

    s = Sampler(cb, interval=0.5)
    s.start()
    assert done.wait(5)
    s.stop()
    assert seen[0].errors and "psutil exploded" in seen[0].errors[0]
    assert seen[1].cpu_percent == 1.0


def test_sampler_process_toggle_resets_priming():
    s = Sampler(lambda x: None, interval=1)
    s.set_collect_processes(True)
    s._procs_primed = True
    s.set_collect_processes(False)
    assert s._procs_primed is False


def test_system_info_has_expected_keys():
    info = get_system_info()
    assert {"Operating system", "CPU cores", "Installed memory", "Python"} <= set(info)
