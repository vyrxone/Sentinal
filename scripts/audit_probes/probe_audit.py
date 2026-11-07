"""Audit probes against a V1 source tree: python -I probe_audit.py <src_dir>"""
import os, sys, time, tempfile, sqlite3, subprocess, pathlib
os.environ["QT_QPA_PLATFORM"] = "offscreen"
src = sys.argv[1]; sys.path.insert(0, src)
tmp = pathlib.Path(tempfile.mkdtemp()); os.environ["SENTINEL_DATA_DIR"] = str(tmp/"d")
res = {}

# P1: DB init failure leaks connection
from sentinel.storage import database, migrations
opened = []
real = sqlite3.connect
def spy(*a, **k):
    c = real(*a, **k); opened.append(c); return c
sqlite3.connect = spy
orig = migrations.migrate
def boom(db): raise database.DatabaseError("boom")
migrations.migrate = boom
try: database.Database(tmp/"x.db")
except database.DatabaseError: pass
leaked = 0
for c in opened:
    try: c.execute("select 1"); leaked += 1
    except sqlite3.ProgrammingError: pass
res["P1 db init failure leaves connection open"] = f"open connections after failure: {leaked}"
migrations.migrate = orig; sqlite3.connect = real

# P2: prune deletes unacknowledged warnings
from sentinel.storage.events_store import EventStore
db = database.Database(tmp/"p2.db"); es = EventStore(db)
es.add("security","file_modified","important warning",severity="warning")
for i in range(10): es.add("app","x",f"i{i}")
es.prune(5)
left = [e.message for e in es.query(limit=100)]
res["P2 prune removes oldest warning"] = f"warning survived: {'important warning' in left}"

# P3: timestamp passed? service _handle
import inspect
from sentinel.security import service
s = inspect.getsource(service.SecurityService._handle)
res["P3 _handle passes observation ts"] = "ts=" in s
res["P4 refresh_folders called anywhere"] = any("refresh_folders" in p.read_text() for p in pathlib.Path(src).rglob("*.py") if p.name!="service.py")

# P5: show_wallpaper exception leaves visible frameless window
from PySide6.QtWidgets import QApplication
app = QApplication([])
from sentinel.controller import AppController
from sentinel.ui.wallpaper_host import WallpaperHost
from sentinel.platform.windows import desktop_layer
dbc = database.Database(tmp/"c.db"); c = AppController(dbc); c.host = WallpaperHost(c)
desktop_layer.is_supported = lambda: True
def bad(*a, **k): raise OSError("attach exploded")
desktop_layer.attach = bad
try: c.host.show_wallpaper(); err = None
except Exception as e: err = repr(e)
sc = c.host.scene
res["P5 exception in attach"] = f"raised={err}; scene visible={bool(sc and sc.isVisible())}; kind={c.host.kind}"
c.shutdown()

# P6: Events page reload cost with 2000 rows
from sentinel.ui.pages.events import EventsPage
db2 = database.Database(tmp/"e.db"); c2 = AppController(db2); es2 = c2.events
for i in range(2000): es2.add("app","t",f"message number {i}", path=f"/a/b/{i}")
pg = EventsPage(c2); pg.show(); t=time.perf_counter(); pg.range.setCurrentIndex(4); app.processEvents(); 
t=time.perf_counter(); pg.reload(); res["P6 events page reload 2000 rows"] = f"{(time.perf_counter()-t)*1000:.0f} ms"
c2.shutdown()

for k,v in res.items(): print(f"{k}: {v}")
