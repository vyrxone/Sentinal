"""Measures the local cost of the V4 AI layer (no network, no keys): python scripts/benchmark_ai.py
Reports measured values only. Provider/network latency is not measured here (it is not local work)."""
import os, sys, time, tempfile, pathlib, statistics, importlib
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tests"))
import psutil
proc = psutil.Process()
rss0 = proc.memory_info().rss
t0 = time.perf_counter()
for m in ("sentinel.ai.router", "sentinel.ai.service", "sentinel.ai.context_builder", "sentinel.ai.providers"):
    importlib.import_module(m)
t_import = time.perf_counter() - t0
rss1 = proc.memory_info().rss
from types import SimpleNamespace
from sentinel.ai import context_builder as cb
from sentinel.ai.config import AIConfig
from sentinel.ai.privacy import Redactor
from sentinel.ai.providers.base import ProviderConfig
from sentinel.ai.router import AIRouter
from sentinel.ai.secrets import SecretStore
from sentinel.ai.usage import UsageTracker
from ai_fakes import FakeKeyring, FakeTransport, gemini_ok, resp, KEY

def timeit(fn, n=30):
    xs = []
    for _ in range(n):
        t = time.perf_counter(); fn(); xs.append((time.perf_counter() - t) * 1000)
    return statistics.median(xs)

evs = [SimpleNamespace(id=i, ts=1.7e9 + i, category="security", event_type="baseline_changed", severity="warning",
                       path=rf"C:\Users\someone\Documents\project\file{i}.txt", message="Baseline file changed from 10.0.0.%d" % (i % 250),
                       details=None) for i in range(50)]
b = cb.ContextBuilder(Redactor(user_names={"someone"}, host_names={"HOST-1"}), 50)
t_build = timeit(lambda: b.events(evs))
items = b.events(evs)
t_compose = timeit(lambda: cb.compose_request("events", "summarize", items))
sec = SecretStore(FakeKeyring(), environ={}); sec.set_key("gemini", KEY)
cfg = AIConfig(enabled=True, providers={"gemini": ProviderConfig(True, "m", 10)})
router = AIRouter(lambda: cfg, sec, UsageTracker(), transport=FakeTransport({"generateContent": resp(200, gemini_ok())}))
req = cb.compose_request("events", "summarize", items)
router.usage.session_requests = 0
def one():
    router.usage._stamps.clear(); router.usage.session_requests = 0; router.route(req)
t_route = timeit(one)
print(f"import of the AI package: {t_import*1000:.0f} ms, +{(rss1-rss0)/1048576:.1f} MiB RSS")
print(f"redact + quote 50 events (context build): {t_build:.1f} ms (median of 30)")
print(f"compose request from 50 items: {t_compose:.2f} ms")
print(f"router round trip with an in-memory transport (provider code, validation, usage): {t_route:.2f} ms")
print(f"request size for 50 events: {sum(len(m.content) for m in req.messages)} characters")
