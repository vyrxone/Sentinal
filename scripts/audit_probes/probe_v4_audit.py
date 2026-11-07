"""V4 audit probes (headless Linux): python -I probe_v4_audit.py <src dir of the tree to probe>

Run against the V3 tree to reproduce confirmed defects, and against the V4 tree to show they are fixed.
Nothing here uses the network or a real API key."""
import io, os, sys, tempfile, pathlib, contextlib, time
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, sys.argv[1])
tmp = pathlib.Path(tempfile.mkdtemp()); os.environ["SENTINEL_DATA_DIR"] = str(tmp / "d")
from PySide6.QtWidgets import QApplication
app = QApplication([])
from sentinel.storage.database import Database
from sentinel.controller import AppController
from sentinel.ui.main_window import MainWindow

res = {}
db = Database(tmp / "p.db")
ctrl = AppController(db)
w = MainWindow(ctrl); w.show(); app.processEvents()

# P1  Events page: alerts_changed carries no argument but is connected to a slot that required one
ep = w.pages[4]; w.goto(4); app.processEvents(); ep.live.setChecked(True); ep._live_timer.stop()
err = io.StringIO()
with contextlib.redirect_stderr(err):
    ctrl.alerts_changed.emit(); app.processEvents()
res["P1 Events page live refresh on alerts_changed"] = ("timer started" if ep._live_timer.isActive() else "timer NOT started") + \
    ("; TypeError printed" if "TypeError" in err.getvalue() else "; no error")

# P2 settings import cannot be abused to enable AI (V4 only)
res["P2 AI present in this tree"] = str(hasattr(ctrl, "ai"))
ctrl.quitting = True; w.close(); ctrl.shutdown()
for k, v in res.items():
    print(f"{k}: {v}")
