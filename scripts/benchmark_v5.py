"""V5 development-VM benchmarks (NOT the target PC): python scripts/benchmark_v5.py
Prints measured values only. Run it on the Celeron J3355 PC to get real target numbers; this repository contains none."""
import gc, os, sys, threading, time, tempfile, pathlib, statistics
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "tests"))
os.environ["SENTINEL_DATA_DIR"] = tempfile.mkdtemp(prefix="sentinel-bench-")
import psutil
from PySide6.QtWidgets import QApplication
proc = psutil.Process()
mib = lambda: proc.memory_info().rss / 1048576
t0 = time.perf_counter()
app = QApplication.instance() or QApplication([])
from sentinel.controller import AppController
from sentinel.storage.database import Database
from sentinel.ui.wallpaper_host import WallpaperHost
from sentinel.ui.main_window import MainWindow
db = Database(pathlib.Path(os.environ["SENTINEL_DATA_DIR"]) / "sentinel.db")
ctrl = AppController(db); ctrl.host = WallpaperHost(ctrl)
win = MainWindow(ctrl); win.show(); app.processEvents()
print(f"startup (controller + window, offscreen): {(time.perf_counter()-t0)*1000:.0f} ms; RSS {mib():.0f} MiB; threads {threading.active_count()}")

def pump(sec):
    end = time.time() + sec
    while time.time() < end:
        app.processEvents(); time.sleep(0.005)
pump(1.0)
c0 = proc.cpu_times(); w0 = time.time(); pump(5.0)
c1 = proc.cpu_times(); busy = (c1.user + c1.system) - (c0.user + c0.system)
print(f"idle CPU over 5 s, AI off (all cores of this VM, one core = 100%): {100*busy/(time.time()-w0):.1f}%")
times = []
for _ in range(3):
    for i in range(len(win.pages)):
        t = time.perf_counter(); win.goto(i); app.processEvents(); times.append((time.perf_counter() - t) * 1000)
print(f"page switch: median {statistics.median(times):.1f} ms, worst {max(times):.1f} ms ({len(times)} switches)")
ctrl.quitting = True
base_threads = threading.active_count(); base = mib()
for _ in range(15):
    w = MainWindow(ctrl); w.show(); app.processEvents(); w.close(); w.deleteLater(); app.processEvents()
gc.collect(); pump(0.3)
print(f"after 15 open/close cycles of the panel: threads {base_threads} -> {threading.active_count()}, RSS {base:.0f} -> {mib():.0f} MiB")
from ai_fakes import FakeKeyring, FakeTransport, gemini_ok, resp, KEY
from sentinel.ai import context_builder as cb
ai = ctrl.ai; ai.secrets._backend = FakeKeyring(); ai.router.transport = FakeTransport({"generateContent": resp(200, gemini_ok("ok"))})
ctrl.settings.set("ai_gemini_enabled", True); ctrl.settings.set("ai_gemini_model", "m"); ctrl.settings.set("ai_enabled", True)
ctrl.settings.set("ai_max_requests_hour", 200); ctrl.settings.set("ai_max_requests_session", 1000)
ai.set_key("gemini", KEY)
lat = []; m0 = mib(); th0 = threading.active_count()
for i in range(200):
    done = []; t = time.perf_counter()
    ai.send(cb.compose_request("chat", f"q{i}", []), done.append, done.append, confirmed=True)
    while not done: app.processEvents(); time.sleep(0.001)
    lat.append((time.perf_counter() - t) * 1000)
    ai.usage._stamps.clear() if hasattr(ai.usage, "_stamps") else None
gc.collect()
print(f"AI request overhead with an in-memory provider (queue + worker + router + validation): median {statistics.median(lat):.1f} ms, p95 {sorted(lat)[190]:.1f} ms")
print(f"after 200 AI requests: RSS {m0:.0f} -> {mib():.0f} MiB, threads {th0} -> {threading.active_count()}, queued jobs {ai.jobs.active_count}")
ctrl.settings.set("ai_enabled", False)
ctrl.shutdown(); db.close()
print("note: these are development-VM numbers; no Celeron J3355 measurement is claimed.")
