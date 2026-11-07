"""V3 audit probes: python -I probe_v3_audit.py <V2 src dir>"""
import os, sys, time, tempfile, threading, pathlib, hashlib
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, sys.argv[1])
tmp = pathlib.Path(tempfile.mkdtemp()); os.environ["SENTINEL_DATA_DIR"] = str(tmp/"d")
from PySide6.QtWidgets import QApplication
app = QApplication([])
from sentinel.storage.database import Database
from sentinel.security import hashing
from sentinel.security.integrity import BaselineStore
from sentinel.security.service import SecurityService
from sentinel.storage.events_store import EventStore
db = Database(tmp/"p.db"); es = EventStore(db); bs = BaselineStore(db)
res = {}

# P1 baseline records stat taken BEFORE hashing while file grows
f = tmp/"grow.bin"; f.write_bytes(os.urandom(8*1024*1024))
stop = threading.Event()
def writer():
    while not stop.is_set():
        with open(f, "ab") as h: h.write(b"x"*65536)
        time.sleep(0.001)
t = threading.Thread(target=writer); t.start()
orig = hashing.CHUNK_SIZE
entry, _ = bs.add_file(f)
stop.set(); t.join()
res["P1 baseline of a file that changed during hashing"] = f"stored size {entry.size} vs final size {f.stat().st_size}; accepted silently={entry.size != f.stat().st_size}"

# P2 re-adding silently overwrites the baseline hash
g = tmp/"cfg.txt"; g.write_text("original"); bs.add_file(g); h0 = bs.get(g).sha256
g.write_text("tampered"); summary = bs.add_files([str(g)])
res["P2 add_files over existing baseline"] = f"hash replaced without confirmation={bs.get(g).sha256 != h0}, summary.updated={summary.updated}"

# P3 symlink out of scope accepted
outside = tmp/"outside.txt"; outside.write_text("secret")
link = tmp/"link.txt"
try:
    os.symlink(outside, link); bs.add_file(link); res["P3 explicit symlink baselined"] = f"accepted={bs.get(link) is not None}"
except OSError as e: res["P3 explicit symlink baselined"] = f"skipped (symlink unsupported: {e})"

# P4 cancelled verification logged as successful
svc = SecurityService(db, es, bs, internal_dirs=[])
for i in range(30): (tmp/f"v{i}.txt").write_text(str(i)); bs.add_file(tmp/f"v{i}.txt")
ev = threading.Event(); n = {"c": 0}
def prog(d, t, p):
    n["c"] += 1
    if n["c"] == 5: ev.set()
r = svc.verify_baseline(ev, prog)
last = es.query(event_type="baseline_verified")[0]
res["P4 cancelled verify event"] = f"checked {len(r)} of {bs.count()}; event severity={last.severity!r} message={last.message!r}"

# P5 projects page path check on UI thread with a slow filesystem
from sentinel.projects import store
real = os.path.exists
def slow(p): time.sleep(0.5); return real(p)
os.path.exists = slow
from sentinel.controller import AppController
from sentinel.ui.pages.projects import ProjectsPage
c = AppController(db)
for i in range(4): c.projects.add(f"p{i}", str(tmp/f"net{i}"))
pg = ProjectsPage(c); pg.show()
t0 = time.perf_counter(); pg.reload() if hasattr(pg, "reload") else pg.refresh(); dt = time.perf_counter()-t0
os.path.exists = real
res["P5 projects page reload, 4 projects on a 0.5 s/call filesystem"] = f"UI thread blocked {dt:.2f} s"

# P6 events table cost 2k / 10k
from sentinel.ui.pages.events import EventsPage
for n_ in (2000, 10000):
    d2 = Database(tmp/f"e{n_}.db"); c2 = AppController(d2)
    d2._connection().execute("BEGIN")
    for i in range(n_): c2.events.add("app","t",f"message number {i}", path=f"/a/b/{i}")
    d2._connection().execute("COMMIT")
    pg = EventsPage(c2); pg.show(); pg.range.setCurrentIndex(4); app.processEvents()
    t0 = time.perf_counter(); pg.reload(); res[f"P6 events reload, {n_} rows stored"] = f"{(time.perf_counter()-t0)*1000:.0f} ms (display limit 2000)"
    c2.shutdown()
for k, v in res.items(): print(f"{k}: {v}")
