"""Reproducible performance benchmark.

    python scripts/benchmark.py [--seconds 20] [--json out.json]

Run it on the machine you care about (for example the Celeron J3355 PC) and keep the output next to the
hardware description: numbers from one machine say nothing certain about another. On Linux/offscreen it
measures software rendering only; it does NOT measure the Windows compositor or the desktop layer.

Measures
  1. paint time of the animated region per preset and resolution (QPainter, offscreen grab)
  2. one monitoring sample, with and without the process scan
  3. Sentinel's own CPU % and memory while a wallpaper window animates for --seconds
  4. Events page (V3 QTableView + paged model): reload at 2,000 and 10,000 stored rows, filtered search,
     sort toggle and page change at 10,000 rows (median of 5, same method as scripts/audit_probes/probe_v3_audit.py P6
     so it can be compared with a V2 run on the SAME machine)
  5. Findings engine: analysis over 2,000 / 10,000 audit events; query + export of 1,000 findings
  6. Project health: path check, health report, repository overview of a 5,000-file tree, hung-folder behaviour
  7. Job runner: throughput of 300 trivial background jobs

Use --quick to skip the animated-wallpaper CPU measurement (sections 1-3 still run).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen" if sys.platform != "win32" else "windows")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--json")
    ap.add_argument("--quick", action="store_true", help="shorter wallpaper CPU measurement (3 s)")
    args = ap.parse_args()
    if args.quick:
        args.seconds = min(args.seconds, 3.0)
    data = tempfile.mkdtemp(prefix="sentinel-bench-")
    os.environ["SENTINEL_DATA_DIR"] = data

    import psutil
    from PySide6.QtWidgets import QApplication

    app = QApplication([])
    from sentinel.controller import AppController
    from sentinel.monitoring.sampler import collect_sample
    from sentinel.storage.database import Database
    from sentinel.storage.settings_store import PRESETS
    from sentinel.ui.pages.events import EventsPage
    from sentinel.ui.wallpaper import WallpaperScene

    db = Database(Path(data) / "bench.db")
    ctrl = AppController(db)
    out: dict = {"machine": {"platform": platform.platform(), "python": platform.python_version(),
                             "cpu_logical": psutil.cpu_count(), "ram_gib": round(psutil.virtual_memory().total / 2**30, 1),
                             "qt_platform": app.platformName()}, "results": {}}

    # 1. paint time per preset/resolution
    paint = {}
    for preset in PRESETS:
        ctrl.settings.apply_preset(preset)
        for w, h in ((1366, 768), (1920, 1080)):
            scene = WallpaperScene(ctrl)
            scene.resize(w, h)
            scene.show()
            scene._frame.stop()
            app.processEvents()
            times = []
            for i in range(40):
                scene._t = i * 0.05
                t0 = time.perf_counter()
                scene.repaint(scene._dyn)
                times.append((time.perf_counter() - t0) * 1000)
            paint[f"{preset} {w}x{h}"] = {"median_ms": round(statistics.median(times), 2),
                                          "p95_ms": round(sorted(times)[int(len(times) * 0.95) - 1], 2)}
            scene.shutdown()
            scene.close()
    out["results"]["paint_animated_region"] = paint

    # 2. sampling cost
    for label, procs in (("without_processes", False), ("with_processes", True)):
        collect_sample(procs, False)
        ts = []
        for _ in range(10):
            t0 = time.perf_counter()
            collect_sample(procs, True)
            ts.append((time.perf_counter() - t0) * 1000)
        out["results"].setdefault("sample_ms", {})[label] = {"median": round(statistics.median(ts), 2), "max": round(max(ts), 2)}

    # 3. own CPU / memory while animating
    ctrl.settings.apply_preset("sentinel_core")
    ctrl.start()
    scene = WallpaperScene(ctrl)
    scene.resize(1366, 768)
    scene.show()
    me = psutil.Process(os.getpid())
    me.cpu_percent(None)
    end = time.time() + args.seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)
    cpu = me.cpu_percent(None) / (psutil.cpu_count() or 1)
    snap = ctrl.render_stats.snapshot()
    out["results"]["running_wallpaper"] = {"seconds": args.seconds, "own_cpu_percent_of_total": round(cpu, 2),
                                           "rss_mib": round(me.memory_info().rss / 2**20, 1),
                                           "measured_fps": round(snap["fps"] or 0, 1),
                                           "paint_ms": round(snap["paint_ms"] or 0, 2)}
    scene.shutdown()
    scene.close()

    # 4. events page at 2k / 10k rows
    def med(fn, n=5):
        ts = []
        for _ in range(n):
            t0 = time.perf_counter()
            fn()
            ts.append((time.perf_counter() - t0) * 1000)
        return round(statistics.median(ts), 1)

    ev_res = {}
    for n_rows in (2000, 10000):
        d2 = Database(Path(data) / f"ev{n_rows}.db")
        c2 = AppController(d2)
        with d2.transaction() as conn:
            conn.executemany(
                "INSERT INTO events(ts,category,event_type,severity,path,message) VALUES(?,?,?,?,?,?)",
                [(time.time() - 5000 + i * 0.1, "app", "bench", "info" if i % 7 else "warning",
                  f"/a/b/{i}", f"message number {i}") for i in range(n_rows)])
        pg = EventsPage(c2)
        pg.show()
        pg.range.setCurrentIndex(4)
        app.processEvents()
        row = {"reload_ms": med(pg.reload), "rows_rendered": pg.model.rowCount()}
        pg.text.setText("number 99")
        row["filtered_search_ms"] = med(pg.reload)
        pg.text.setText("")
        pg._on_header(1)
        row["sort_by_severity_ms"] = med(lambda: pg._on_header(1))
        pg._page = 0
        row["next_page_ms"] = med(lambda: (pg._goto_page(1), pg._goto_page(0)))
        ev_res[f"{n_rows}_rows_stored"] = row
        pg.close()
        c2.shutdown()
    out["results"]["events_page"] = ev_res

    # 5. findings engine
    from sentinel.security.findings import FindingStore
    from sentinel.security.rules import FindingsEngine
    fnd = {}
    for n_rows in (2000, 10000):
        d3 = Database(Path(data) / f"fi{n_rows}.db")
        from sentinel.storage.events_store import EventStore
        es = EventStore(d3)
        now = time.time()
        with d3.transaction() as conn:
            conn.executemany(
                "INSERT INTO events(ts,category,event_type,severity,path,message) VALUES(?,?,?,?,?,?)",
                [(now - 3000 + i * (3000 / n_rows), "security", "file_modified", "info", f"/w/f{i % 200}", "m")
                 for i in range(n_rows)])
        eng = FindingsEngine(es, FindingStore(d3))
        t0 = time.perf_counter()
        r1 = eng.analyze(now)
        first = (time.perf_counter() - t0) * 1000
        t0 = time.perf_counter()
        r2 = eng.analyze(now)
        fnd[f"{n_rows}_events"] = {"first_analysis_ms": round(first, 1), "repeat_analysis_ms": round((time.perf_counter() - t0) * 1000, 1),
                                   "events_examined": r1.events_examined, "findings_created": r1.created,
                                   "repeat_created": r2.created}
        d3.close()
    d4 = Database(Path(data) / "fx.db")
    fs = FindingStore(d4)
    from sentinel.security.findings import Candidate, EventRef
    for i in range(1000):
        fs.record(Candidate(key=f"K{i}", rule_id="BASE-001", rule_version=1, severity="medium", category="integrity",
                            title=f"t{i}", explanation="e", resource=f"/r/{i}", confidence="c", next_step="n",
                            events=[EventRef(i * 10 + j, 1.0, "baseline_mismatch", "s") for j in range(5)]), 1.0)
    t0 = time.perf_counter()
    fs.query(limit=500)
    q_ms = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    fs.export(Path(data) / "f.json")
    fnd["1000_findings"] = {"query_500_ms": round(q_ms, 1), "export_json_ms": round((time.perf_counter() - t0) * 1000, 1)}
    d4.close()
    out["results"]["findings_engine"] = fnd

    # 6. project health + overview
    from sentinel.projects.health import compute_health, inspect_path
    from sentinel.projects.overview import scan_overview
    from sentinel.projects.tasks import TaskStore
    from sentinel.projects.store import ProjectStore
    tree = Path(data) / "tree"
    for d in range(50):
        (tree / f"pkg{d}").mkdir(parents=True)
        for f in range(100):
            (tree / f"pkg{d}" / f"m{f}.py").write_text("x = 1\n")
    d5 = Database(Path(data) / "pr.db")
    proj = ProjectStore(d5).add("bench", str(tree))
    ph = {"path_check_ms": med(lambda: inspect_path(str(tree))),
          "health_no_git_ms": med(lambda: compute_health(proj, TaskStore(d5), with_git=False)),
          "health_with_git_ms (not a repo or git missing)": med(lambda: compute_health(proj, TaskStore(d5)), 3)}
    t0 = time.perf_counter()
    ov = scan_overview(str(tree))
    ph["overview_5000_files_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    ph["overview_files_counted"] = ov.files
    t0 = time.perf_counter()
    state = inspect_path("Z:\\definitely\\not\\there", timeout=2)
    ph["missing_drive_check_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    ph["missing_drive_state"] = state
    d5.close()
    out["results"]["project_intelligence"] = ph

    # 7. job runner throughput
    done = []
    t0 = time.perf_counter()
    for _ in range(300):
        while ctrl.jobs.active_count >= ctrl.jobs.max_pending - 1:
            app.processEvents()
        ctrl.jobs.submit(lambda c, p: 1, done.append, name="bench")
    while len(done) < 300:
        app.processEvents()
    out["results"]["job_runner"] = {"300_trivial_jobs_ms": round((time.perf_counter() - t0) * 1000, 1)}
    ctrl.shutdown()

    text = json.dumps(out, indent=2)
    print(text)
    if args.json:
        Path(args.json).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
