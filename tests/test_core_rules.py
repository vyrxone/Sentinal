from sentinel.core.performance import LOAD_FPS_CAP, effective_quality
from sentinel.core.thresholds import SustainedLoad, ThresholdEngine


def test_threshold_requires_sustained_samples_then_clears_with_hysteresis():
    eng = ThresholdEngine(cpu=80, ram=90, disk=95, sustained=3, hysteresis=5)
    assert eng.evaluate({"cpu": 85}) == []
    assert eng.evaluate({"cpu": 85}) == []
    alerts = eng.evaluate({"cpu": 86})
    assert [(a.metric, a.kind) for a in alerts] == [("cpu", "warn")]
    assert "80%" in alerts[0].message
    assert eng.evaluate({"cpu": 99}) == []          # already active: no repeat spam
    assert eng.evaluate({"cpu": 78}) == []          # below limit but inside hysteresis band
    cleared = eng.evaluate({"cpu": 70})
    assert [(a.metric, a.kind) for a in cleared] == [("cpu", "clear")]
    assert eng.active_metrics() == []


def test_threshold_interrupted_streak_resets():
    eng = ThresholdEngine(cpu=80, sustained=3)
    eng.evaluate({"cpu": 90})
    eng.evaluate({"cpu": 90})
    eng.evaluate({"cpu": 10})
    assert eng.evaluate({"cpu": 90}) == []
    assert eng.evaluate({"cpu": 90}) == []
    assert len(eng.evaluate({"cpu": 90})) == 1


def test_threshold_disk_is_immediate_and_none_is_ignored():
    eng = ThresholdEngine(disk=90, sustained=5)
    assert eng.evaluate({"disk": None, "cpu": None, "ram": None}) == []
    assert [a.metric for a in eng.evaluate({"disk": 91})] == ["disk"]


def test_threshold_reconfigure():
    eng = ThresholdEngine(cpu=95, sustained=1)
    assert eng.evaluate({"cpu": 90}) == []
    eng.configure(cpu=85)
    assert len(eng.evaluate({"cpu": 90})) == 1


def test_sustained_load_enter_and_exit():
    load = SustainedLoad(high=90, low=70, samples=3)
    assert [load.update(95), load.update(95), load.update(95)] == [None, None, "entered"]
    assert load.active and load.update(95) is None
    assert load.update(80) is None and load.active
    assert load.update(50) == "exited" and not load.active
    assert load.update(None) is None


def test_quality_follows_intensity_and_load():
    normal = effective_quality(24, "medium", under_load=False, reduce_under_load=True)
    assert normal.fps == 24 and normal.particles > 0 and normal.glow and normal.pulses
    low = effective_quality(24, "low", False, True)
    high = effective_quality(24, "high", False, True)
    assert low.particles < normal.particles < high.particles
    stressed = effective_quality(30, "high", under_load=True, reduce_under_load=True)
    assert stressed.fps == LOAD_FPS_CAP and stressed.particles == 0 and not stressed.glow
    ignored = effective_quality(30, "high", under_load=True, reduce_under_load=False)
    assert ignored.fps == 30 and ignored.particles == high.particles
    assert effective_quality(15, "high", True, True).fps == 15  # never raised above user cap
