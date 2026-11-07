"""Performance page: real measurements of Sentinel's own cost, plus a redacted diagnostic report."""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QMessageBox, QPlainTextEdit, QVBoxLayout, QWidget)

from ...core.formatting import NA, human_bytes, percent
from ...diagnostics import build_report
from .. import theme
from ..widgets import Card, SparkGraph, StatTile, make_button, make_label, page_header


def ai_summary(c) -> str:
    """Measured AI worker facts only (no prompts, answers or keys)."""
    s = c.ai.status_snapshot()
    if not s["enabled"]:
        return "off (default): no worker is running and nothing is sent"
    lat = f"{s['last_latency_s']:.2f} s" if s["last_latency_s"] is not None else "unavailable"
    return (f"{s['workers']} worker, {s['active']} running, {s['queue_depth']} queued; {s['session_requests']} request(s) "
            f"this session, last answer {lat}")


class PerformancePage(QWidget):
    title = "Performance"

    def __init__(self, ctrl) -> None:
        super().__init__()
        self.ctrl = ctrl
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)
        root.addWidget(page_header(
            "Performance",
            "What Sentinel itself costs on this PC. Values are measured while this page is open; "
            "nothing is estimated."))

        tiles = QHBoxLayout()
        tiles.setSpacing(12)
        self.t_fps = StatTile("Drawn frames / s")
        self.t_paint = StatTile("Paint time / frame")
        self.t_cpu = StatTile("Sentinel CPU")
        self.t_mem = StatTile("Sentinel memory")
        for t in (self.t_fps, self.t_paint, self.t_cpu, self.t_mem):
            tiles.addWidget(t)
        root.addLayout(tiles)

        cfg = Card("Current settings and state")
        self.rows: dict[str, object] = {}
        for key in ("Frame-rate cap", "Monitoring interval", "Process list refresh", "Effects", "Wallpaper",
                    "Reduced motion"):
            row = QHBoxLayout()
            name = make_label(key, "muted")
            name.setFixedWidth(170)
            val = make_label("", wrap=True)
            row.addWidget(name)
            row.addWidget(val, 1)
            cfg.body.addLayout(row)
            self.rows[key] = val
        cfg.setMinimumHeight(220)
        root.addWidget(cfg)

        bg = Card("Monitoring and background work")
        for key in ("Monitoring", "Last sample", "Folder watching", "Background jobs", "Slow operations",
                    "Findings analysis", "Project folder checks", "AI assistant"):
            row = QHBoxLayout()
            name = make_label(key, "muted")
            name.setFixedWidth(170)
            val = make_label("", wrap=True)
            row.addWidget(name)
            row.addWidget(val, 1)
            bg.body.addLayout(row)
            self.rows[key] = val
        bg.setMinimumHeight(240)
        root.addWidget(bg)

        hist = Card("Recent resource history (this PC and Sentinel)")
        hist.add(make_label("System CPU %", "muted"))
        self.g_cpu = SparkGraph(theme.CRIMSON)
        hist.add(self.g_cpu, 1)
        hist.add(make_label("Sentinel's own CPU %", "muted"))
        self.g_self = SparkGraph(theme.GOLD, ymax=25.0)
        hist.add(self.g_self, 1)
        hist.setMinimumHeight(300)
        root.addWidget(hist)

        diag = Card("Diagnostic report")
        diag.add(make_label("Contains versions, screens, desktop-integration method and measured performance. "
                            "No file names, no folders, no personal data; your user name is removed.", "muted", wrap=True))
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        self.report.setMinimumHeight(170)
        diag.add(self.report)
        buttons = QHBoxLayout()
        b_make = make_button("Refresh report", primary=True)
        b_copy = make_button("Copy")
        b_save = make_button("Save…")
        for b in (b_make, b_copy, b_save):
            buttons.addWidget(b)
        buttons.addStretch(1)
        diag.body.addLayout(buttons)
        b_make.clicked.connect(self.make_report)
        b_copy.clicked.connect(self.copy_report)
        b_save.clicked.connect(self.save_report)
        root.addWidget(diag)
        root.addStretch(1)

        self._tick = QTimer(self)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self.refresh)

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        self._tick.start()
        self.refresh()
        if not self.report.toPlainText():
            self.make_report()

    def hideEvent(self, e) -> None:  # noqa: N802
        super().hideEvent(e)
        self._tick.stop()          # no timer runs while the page is not visible

    def refresh(self) -> None:
        c = self.ctrl
        rs = c.render_stats.snapshot()
        if rs["scenes"]:
            self.t_fps.set(f"{rs['fps']:.1f}", "measured, all scenes" + ("" if rs["animating"] else " (static)"), "ok")
            self.t_paint.set(f"{rs['paint_ms']:.1f} ms", "worst scene", "ok" if rs["paint_ms"] < 30 else "warn")
        else:
            self.t_fps.set(NA, "wallpaper not drawing", "off")
            self.t_paint.set(NA, "wallpaper not drawing", "off")
        cpu = c.self_metrics.cpu[-1] if c.self_metrics.cpu else None
        rss = c.self_metrics.rss[-1] if c.self_metrics.rss else None
        self.t_cpu.set(percent(cpu, 1) if cpu is not None else NA, "of total CPU", "ok" if cpu is not None else "off")
        self.t_mem.set(human_bytes(rss) if rss else NA, "resident", "ok" if rss else "off")
        q, s = c.quality, c.settings
        R = self.rows
        R["Frame-rate cap"].setText(f"{s.get('animation_fps')} fps set; {q.fps} fps in use"
                                    + (" (paused)" if c.animation_paused else ""))
        R["Monitoring interval"].setText(f"{s.get('sample_interval_s'):g} s" + ("" if c.monitoring_active else " (stopped)"))
        R["Process list refresh"].setText(f"every {s.get('process_interval_s')} s while the Monitor page is open")
        R["Effects"].setText("Reduced automatically (sustained high CPU)" if c.load.active and s.get("reduce_under_load")
                             else f"{s.get('animation_intensity')} intensity, {q.particles} particles"
                                  + (", glow" if q.glow else "") + (", pulses" if q.pulses else ""))
        R["Wallpaper"].setText(f"{c.desktop_label}" + (f" ({c.desktop_method})" if c.desktop_method else ""))
        R["Reduced motion"].setText("On - static emblem, no continuous animation" if c.appearance.reduced_motion else "Off")
        self._refresh_background()
        self.g_cpu.set_values(list(c.history.cpu))
        self.g_self.set_values(list(c.self_metrics.cpu))

    def _refresh_background(self) -> None:
        """Measured status only: where nothing was measured the page says 'unavailable', never a zero."""
        from ...projects.health import stuck_calls

        c, R = self.ctrl, self.rows
        R["Monitoring"].setText("Running" if c.monitoring_active else "Stopped")
        age = c.data_age()
        R["Last sample"].setText("unavailable (no sample yet)" if age is None else f"{age:.1f} s ago"
                                 + ("  - STALE" if c.monitoring_active and age > 4 * float(c.settings.get("sample_interval_s")) + 2 else ""))
        R["Folder watching"].setText(("Running" if c.security_active else "Stopped")
                                     + (f" - {c.security.stats()['processed']} event(s) processed, "
                                        f"{c.security.stats()['dropped']} dropped" if c.security_active else ""))
        js = c.jobs.status()
        names = c.jobs.running_names()
        R["Background jobs"].setText(
            f"{js['running']} running, {js['pending']} waiting (max {js['threads']} at once)"
            + (f": {', '.join(names[:3])}" if names else "")
            + f". Finished {js['done']}, failed {js['failed']}, timed out {js['timeout']}, "
              f"cancelled {js['cancelled']}, rejected {js['rejected']}.")
        R["Slow operations"].setText("; ".join(f"{n} {sec:.2f} s ({o})" for n, sec, o in js["slow"])
                                     if js["slow"] else "none recorded (threshold 0.5 s)")
        res, at = c.engine.last_result, c.engine.last_run_ts
        R["Findings analysis"].setText(
            "off (Settings)" if not c.findings_enabled else
            "not run yet" if res is None else
            f"{res.events_examined} event(s) examined in {res.duration_ms:.0f} ms; {c.findings.open_count()} open finding(s)")
        R["Project folder checks"].setText(
            f"{stuck_calls()} unresponsive folder check(s) abandoned" if stuck_calls() else "no unresponsive folders")
        R["AI assistant"].setText(ai_summary(c))

    def make_report(self) -> None:
        try:
            self.report.setPlainText(build_report(self.ctrl))
        except Exception as exc:
            self.report.setPlainText(f"Could not build the report: {exc}")

    def copy_report(self) -> None:
        QGuiApplication.clipboard().setText(self.report.toPlainText())

    def save_report(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save diagnostic report", "sentinel-diagnostics.txt", "Text (*.txt)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.report.toPlainText() + "\n")
        except OSError as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
