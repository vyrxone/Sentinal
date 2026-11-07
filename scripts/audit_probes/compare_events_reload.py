"""Events page reload cost on one machine, for any Sentinel tree:

    python -I compare_events_reload.py <path to that tree's src folder>

Run it once against the V2 tree and once against the V3 tree on the SAME machine in the SAME session;
numbers are only comparable under those conditions. Median of 7 reloads, 'All time', rows bulk-inserted.
"""
import os
import pathlib
import statistics
import sys
import tempfile
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, sys.argv[1])
tmp = pathlib.Path(tempfile.mkdtemp())
os.environ["SENTINEL_DATA_DIR"] = str(tmp / "d")
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])
from sentinel.controller import AppController  # noqa: E402
from sentinel.storage.database import Database  # noqa: E402
from sentinel.ui.pages.events import EventsPage  # noqa: E402

for n in (2000, 10000):
    db = Database(tmp / f"e{n}.db")
    c = AppController(db)
    with db.transaction() as conn:
        conn.executemany("INSERT INTO events(ts,category,event_type,severity,path,message) VALUES(?,?,?,?,?,?)",
                         [(time.time() - 5000 + i * 0.1, "app", "bench", "info", f"/a/b/{i}", f"message number {i}")
                          for i in range(n)])
    pg = EventsPage(c)
    pg.show()
    pg.range.setCurrentIndex(4)
    app.processEvents()
    pg.reload()
    ts = []
    for _ in range(7):
        t0 = time.perf_counter()
        pg.reload()
        ts.append((time.perf_counter() - t0) * 1000)
    rows = pg.model.rowCount() if hasattr(pg, "model") else pg.table.rowCount()
    print(f"{n} stored rows: median reload {statistics.median(ts):.1f} ms (min {min(ts):.1f}, max {max(ts):.1f}); rows rendered {rows}")
    c.shutdown()
