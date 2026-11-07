"""The animated wallpaper scene (plain QPainter, no 3D, no image/video assets).

Performance design
* The static background is rendered once into a cached pixmap.
* Each frame only repaints a small rectangle around the emblem; the clock and the
  overlay are repainted at most once per second / per sample.
* Animation is time-based, so changing the frame-rate cap never changes speed.
* Particles are computed analytically from the clock (no per-frame state).
* Under sustained load the controller lowers the frame rate and switches off
  particles, glow and pulses.
* V2: reduced-motion mode draws a static emblem (no frame timer at all); the dimmer, clock,
  emblem size, particles and pulse strength come from the Appearance settings; the scene measures
  its own painted frames per second and paint time for the Performance page.
"""

from __future__ import annotations

import math
import random
import time
from datetime import datetime
from typing import Optional

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QConicalGradient, QFont, QKeyEvent, QPainter,
                           QPaintEvent, QPen, QPixmap, QRadialGradient, QResizeEvent, QCloseEvent)
from PySide6.QtWidgets import QWidget

from .. import APP_NAME, RELEASE_LABEL
from ..core.formatting import NA, freshness, human_duration, percent
from . import theme
from .icons import draw_emblem

MAX_PARTICLES = 48


class WallpaperScene(QWidget):
    closed = Signal()  # user closed the window-mode fallback window

    def __init__(self, controller, parent: Optional[QWidget] = None, *, screen_id: str = "primary",
                 screen_name: str = "") -> None:
        super().__init__(parent)
        self.ctrl = controller
        self.screen_id = screen_id
        self.screen_name = screen_name
        self._scale_used = None
        self._frames = 0
        self._paint_s = 0.0
        self._period_start = time.monotonic()
        self.setWindowTitle(f"{APP_NAME} — Window Mode")
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setMinimumSize(480, 270)
        self.programmatic_close = False
        self.interactive = True      # False when embedded in the desktop layer

        rng = random.Random(7)
        self._particles = [(rng.uniform(0, math.tau), rng.uniform(0.0, 1.0), rng.uniform(-0.12, 0.12),
                            rng.uniform(0.9, 2.3), rng.uniform(0, math.tau), i % 3 == 0)
                           for i in range(MAX_PARTICLES)]
        self._bg: Optional[QPixmap] = None
        self._t0 = time.monotonic()
        self._t = 0.0
        self._geom_ready = False
        self._dyn = self._clock = self._overlay = QRect()
        self._cx = self._cy = self._R = 0.0
        self._s = 1.0

        self._frame = QTimer(self)
        self._frame.setTimerType(Qt.TimerType.CoarseTimer)
        self._frame.timeout.connect(self._tick)
        self._second = QTimer(self)
        self._second.setInterval(1000)
        self._second.timeout.connect(self._second_tick)

        self.ctrl.sample_ready.connect(self._on_sample)
        self.ctrl.state_changed.connect(self._on_state)
        self.ctrl.quality_changed.connect(self._on_state)
        self._connected = True
        self.apply_state()

    def shutdown(self) -> None:
        """Stop timers and drop controller connections (called before the scene is destroyed)."""
        self._frame.stop()
        self._second.stop()
        if self._connected:
            self._connected = False
            for sig, slot in ((self.ctrl.sample_ready, self._on_sample), (self.ctrl.state_changed, self._on_state),
                              (self.ctrl.quality_changed, self._on_state)):
                try:
                    sig.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
        self.ctrl.render_stats.forget(self.screen_id)

    # ----------------------------------------------------------- state / timers
    def apply_state(self) -> None:
        quality = self.ctrl.quality
        self._frame.setInterval(max(15, int(1000 / quality.fps)))
        scale = self.ctrl.appearance.emblem_scale
        if self._scale_used is not None and scale != self._scale_used and self._geom_ready:
            self._relayout()
        should_run = self.isVisible() and not self.ctrl.animation_paused and not quality.static
        if should_run and not self._frame.isActive():
            self._t0 = time.monotonic() - self._t
            self._frame.start()
        elif not should_run and self._frame.isActive():
            self._frame.stop()
        if self.isVisible() and not self._second.isActive():
            self._second.start()
        self.update()

    def _on_state(self) -> None:
        self.apply_state()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.apply_state()

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self._frame.stop()
        self._second.stop()

    def _tick(self) -> None:
        self._t = time.monotonic() - self._t0
        if self._geom_ready:
            self.update(self._dyn)

    def _second_tick(self) -> None:
        now = time.monotonic()
        span = now - self._period_start
        if span >= 1.0:
            self.ctrl.render_stats.report(self.screen_id, self._frames / span,
                                          (self._paint_s / self._frames * 1000.0) if self._frames else 0.0,
                                          self._frame.isActive())
            self._frames, self._paint_s, self._period_start = 0, 0.0, now
        if self._geom_ready:
            if self.ctrl.quality.static:          # no frame timer: refresh the animated region once a second
                self.update(self._dyn)
            self.update(self._clock)
            if self.ctrl.appearance.show_overlay:
                self.update(self._overlay)

    def _on_sample(self, _sample) -> None:
        if self._geom_ready:
            self.update(self._clock)
            self.update(self._overlay)

    # ------------------------------------------------------------------ geometry
    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._relayout()

    def _relayout(self) -> None:
        w, h = self.width(), self.height()
        app = self.ctrl.appearance
        self._scale_used = app.emblem_scale
        self._s = max(0.35, min(w, h) / 1080.0)
        self._cx, self._cy = w / 2.0, h * 0.40
        self._R = min(w * 0.30, h * 0.30) * app.emblem_scale
        r = self._R * 1.28
        self._dyn = QRect(int(self._cx - r), int(self._cy - r), int(2 * r), int(2 * r)).intersected(self.rect())
        top = int(self._cy + self._R * 1.22)
        cw = int(min(w * 0.7, 900 * self._s * 1.6))
        self._clock = QRect(int(self._cx - cw / 2), top, cw, int(250 * self._s)).intersected(self.rect())
        ow, oh = int(280 * self._s), int((250 if app.overlay_expanded else 186) * self._s)
        m = int(96 * self._s)    # keep clear of the HUD corner bracket
        self._overlay = QRect(w - ow - m, m, ow, oh)
        self._geom_ready = True
        self._build_background()
        self.update()

    def _build_background(self) -> None:
        dpr = self.devicePixelRatioF()
        w, h = self.width(), self.height()
        pm = QPixmap(max(1, int(w * dpr)), max(1, int(h * dpr)))
        pm.setDevicePixelRatio(dpr)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.fillRect(0, 0, w, h, QColor("#060608"))
        g = QRadialGradient(QPointF(self._cx, self._cy), max(w, h) * 0.75)
        g.setColorAt(0.0, QColor("#1d1015"))
        g.setColorAt(0.30, QColor("#101015"))
        g.setColorAt(1.0, QColor("#050507"))
        p.fillRect(0, 0, w, h, QBrush(g))
        s = self._s
        # faint gold hairline orbits
        p.setBrush(Qt.BrushStyle.NoBrush)
        for k, alpha in ((1.62, 22), (2.1, 14), (2.7, 9)):
            p.setPen(QPen(theme.color(theme.GOLD, alpha), 1.0))
            p.drawEllipse(QPointF(self._cx, self._cy), self._R * k, self._R * k)
        # HUD corner brackets
        m, ln = 34 * s, 46 * s
        p.setPen(QPen(theme.color(theme.GOLD, 110), max(1.0, 1.6 * s)))
        for (x, y, dx, dy) in ((m, m, 1, 1), (w - m, m, -1, 1), (m, h - m, 1, -1), (w - m, h - m, -1, -1)):
            p.drawLine(QPointF(x, y), QPointF(x + dx * ln, y))
            p.drawLine(QPointF(x, y), QPointF(x, y + dy * ln))
        # brand marks (static)
        p.setFont(theme.make_font(int(11 * s * 1.2), weight=QFont.Weight.DemiBold, spacing=3 * s))
        p.setPen(theme.color(theme.GOLD, 170))
        p.drawText(QRectF(m + 10 * s, h - m - 28 * s, 420 * s, 20 * s),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"{APP_NAME.upper()}  ·  {RELEASE_LABEL}")
        p.setPen(theme.color(theme.MUTED, 150))
        p.drawText(QRectF(w - m - 430 * s, h - m - 28 * s, 420 * s, 20 * s),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   "LOCAL ONLY · NO NETWORK ACCESS")
        p.end()
        self._bg = pm

    # -------------------------------------------------------------------- paint
    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        clip = event.rect()
        t_start = time.perf_counter()
        p = QPainter(self)
        if self._bg is not None:
            p.drawPixmap(clip, self._bg, QRect(int(clip.x() * self._bg.devicePixelRatio()),
                                               int(clip.y() * self._bg.devicePixelRatio()),
                                               int(clip.width() * self._bg.devicePixelRatio()),
                                               int(clip.height() * self._bg.devicePixelRatio())))
        else:
            p.fillRect(clip, QColor("#060608"))
        if not self._geom_ready:
            p.end()
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        app = self.ctrl.appearance
        if clip.intersects(self._dyn):
            self._paint_emblem(p)
        if app.show_clock and clip.intersects(self._clock):
            self._paint_clock(p)
        if app.show_overlay and clip.intersects(self._overlay):
            self._paint_overlay(p)
        if app.dimmer > 0:
            p.fillRect(clip, QColor(0, 0, 0, int(255 * app.dimmer)))
        p.end()
        if clip.intersects(self._dyn):
            self._frames += 1
            self._paint_s += time.perf_counter() - t_start

    def _values(self):
        """(cpu, ram, disk, state, text) - values are None whenever data is missing/stale."""
        sample = self.ctrl.last_sample
        interval = self.ctrl.settings.get("sample_interval_s")
        if not self.ctrl.monitoring_active:
            return None, None, None, "off", "MONITORING OFF"
        if sample is None:
            return None, None, None, "off", "WAITING FOR DATA"
        state, text = freshness(self.ctrl.data_age(), interval)
        if state == "stale":
            return None, None, None, "stale", "DATA STALE"
        return sample.cpu_percent, sample.memory_percent, sample.system_disk_percent, "live", "LIVE"

    def _paint_emblem(self, p: QPainter) -> None:
        q = self.ctrl.quality
        t, R, cx, cy, s = self._t, self._R, self._cx, self._cy, self._s
        cpu, ram, disk, state, _ = self._values()
        alert = bool(self.ctrl.thresholds.active_metrics())
        centre = QPointF(cx, cy)
        p.setBrush(Qt.BrushStyle.NoBrush)

        # soft crimson halo
        if q.glow:
            g = QRadialGradient(centre, R * 1.15)
            g.setColorAt(0.0, theme.color(theme.CRIMSON, 70 if alert else 38))
            g.setColorAt(1.0, theme.color(theme.CRIMSON, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(g))
            p.drawEllipse(centre, R * 1.15, R * 1.15)
            p.setBrush(Qt.BrushStyle.NoBrush)

        # rotating dashed gold outer ring
        p.save()
        p.translate(centre)
        p.rotate(t * 4.0)
        pen = QPen(theme.color(theme.GOLD, 175), max(1.0, 1.6 * s))
        pen.setDashPattern([26, 14, 3, 14])
        p.setPen(pen)
        p.drawEllipse(QPointF(0, 0), R, R)
        p.restore()

        # fine tick ring, counter-rotating
        if q.particles > 0:
            p.save()
            p.translate(centre)
            p.rotate(-t * 2.5)
            p.setPen(QPen(theme.color(theme.GOLD, 90), max(1.0, 1.2 * s)))
            r1, r2 = R * 0.93, R * 0.955
            for i in range(72):
                a = i * 5.0
                ln = r2 if i % 6 else R * 0.975
                rad = math.radians(a)
                p.drawLine(QPointF(math.cos(rad) * r1, math.sin(rad) * r1),
                           QPointF(math.cos(rad) * ln, math.sin(rad) * ln))
            p.restore()

        # crimson ring
        p.setPen(QPen(theme.color(theme.CRIMSON, 130), max(1.0, 1.4 * s)))
        p.drawEllipse(centre, R * 0.86, R * 0.86)

        # live metric arcs: CPU (crimson), memory (gold), system disk (dim gold)
        for frac, value, col, wdt in ((0.76, cpu, theme.CRIMSON_BRIGHT if alert else theme.CRIMSON, 9),
                                       (0.64, ram, theme.GOLD, 9), (0.52, disk, theme.GOLD_DIM, 6)):
            rad = R * frac
            rect = QRectF(cx - rad, cy - rad, 2 * rad, 2 * rad)
            width = max(2.0, wdt * s)
            p.setPen(QPen(theme.color("#ffffff", 16), width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawEllipse(rect)
            if value is not None:
                span = int(-max(0.0, min(100.0, value)) * 3.6 * 16)
                if q.glow:
                    p.setPen(QPen(theme.color(col, 55), width * 2.3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                    p.drawArc(rect, 90 * 16, span)
                p.setPen(QPen(theme.color(col), width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawArc(rect, 90 * 16, span)

        # radar sweep
        if q.glow and state == "live":
            cg = QConicalGradient(centre, -(t * 36.0) % 360)
            cg.setColorAt(0.0, theme.color(theme.CRIMSON, 64))
            cg.setColorAt(0.12, theme.color(theme.CRIMSON, 0))
            cg.setColorAt(1.0, theme.color(theme.CRIMSON, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(cg))
            p.drawEllipse(centre, R * 0.46, R * 0.46)
            p.setBrush(Qt.BrushStyle.NoBrush)

        # energy pulses
        if q.pulses:
            period = 3.4
            for k in range(2):
                phase = ((t / period) + k * 0.5) % 1.0
                rr = R * (0.18 + 0.82 * phase)
                p.setPen(QPen(theme.color(theme.CRIMSON_BRIGHT, int(min(255, 95 * self.ctrl.appearance.pulse_strength) * (1.0 - phase) ** 1.6)),
                              max(1.0, 1.6 * s)))
                p.drawEllipse(centre, rr, rr)

        # particles
        if q.particles > 0:
            p.setPen(Qt.PenStyle.NoPen)
            for a0, rf, speed, size, phase, crimson in self._particles[: q.particles]:
                ang = a0 + speed * t
                rr = R * (0.34 + rf * 0.88) + 5 * s * math.sin(t * 0.7 + phase)
                alpha = int(55 + 110 * (0.5 + 0.5 * math.sin(t * 1.3 + phase)))
                p.setBrush(theme.color(theme.CRIMSON_BRIGHT if crimson else theme.GOLD, alpha))
                px, py = cx + math.cos(ang) * rr, cy + math.sin(ang) * rr
                rad = size * max(0.7, s * 1.4)
                p.drawEllipse(QPointF(px, py), rad, rad)

        # emblem core
        draw_emblem(p, cx, cy, R * 0.40, pupil_scale=1.0 + 0.07 * math.sin(t * 1.6), bright=alert)

    def _paint_clock(self, p: QPainter) -> None:
        s, r = self._s, self._clock
        now = datetime.now()
        top = float(r.top())
        w = float(r.width())
        x = float(r.left())
        p.setPen(theme.color(theme.TEXT, 240))
        p.setFont(theme.make_font(int(104 * s), weight=QFont.Weight.Light, spacing=2 * s))
        p.drawText(QRectF(x, top, w, 118 * s), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                   now.strftime("%H:%M"))
        p.setPen(theme.color(theme.GOLD, 230))
        p.setFont(theme.make_font(int(19 * s), weight=QFont.Weight.DemiBold, spacing=5 * s))
        p.drawText(QRectF(x, top + 118 * s, w, 30 * s), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                   now.strftime("%A  ·  %d %B %Y").upper())
        # live status line
        cpu, ram, disk, state, text = self._values()
        col = {"live": theme.GOLD, "stale": theme.CRIMSON_BRIGHT, "off": theme.MUTED}[state]
        if self.ctrl.thresholds.active_metrics() and state == "live":
            col, text = theme.CRIMSON_BRIGHT, "THRESHOLD WARNING"
        p.setFont(theme.make_font(int(14 * s), weight=QFont.Weight.Medium, spacing=2.5 * s))
        line = f"CPU {percent(cpu)}     MEM {percent(ram)}     DISK {percent(disk)}"
        fm = p.fontMetrics()
        lw = fm.horizontalAdvance(line)
        dot_r = 4.5 * s
        total = lw + 28 * s + fm.horizontalAdvance(text) + dot_r * 2
        y = top + 164 * s
        sx = x + (w - total) / 2
        p.setPen(theme.color(theme.TEXT, 190))
        p.drawText(QRectF(sx, y, lw + 4, 26 * s), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, line)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.color(col))
        p.drawEllipse(QPointF(sx + lw + 28 * s, y + 13 * s), dot_r, dot_r)
        p.setPen(theme.color(col))
        p.drawText(QRectF(sx + lw + 28 * s + dot_r * 2 + 8 * s, y, 400 * s, 26 * s),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)

    def _paint_overlay(self, p: QPainter) -> None:
        s, r = self._s, self._overlay
        cpu, ram, disk, state, text = self._values()
        sample = self.ctrl.last_sample
        p.setPen(QPen(theme.color(theme.GOLD_DIM, 150), 1.0))
        p.setBrush(theme.color("#0d0d11", 215))
        p.drawRoundedRect(QRectF(r), 8 * s, 8 * s)
        pad = 16 * s
        x, y, w = r.left() + pad, r.top() + pad - 2 * s, r.width() - 2 * pad
        p.setFont(theme.make_font(int(11 * s), weight=QFont.Weight.DemiBold, spacing=3 * s))
        p.setPen(theme.color(theme.GOLD))
        p.drawText(QRectF(x, y, w, 16 * s), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "SYSTEM")
        col = {"live": theme.GOLD, "stale": theme.CRIMSON_BRIGHT, "off": theme.MUTED}[state]
        p.setPen(theme.color(col))
        p.drawText(QRectF(x, y, w, 16 * s), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, text)
        y += 26 * s
        p.setFont(theme.make_font(int(13 * s), weight=QFont.Weight.Medium, spacing=1 * s))
        for label, value, bar_col in (("CPU", cpu, theme.CRIMSON), ("MEM", ram, theme.GOLD), ("DISK", disk, theme.GOLD_DIM)):
            p.setPen(theme.color(theme.TEXT, 220))
            p.drawText(QRectF(x, y, 50 * s, 20 * s), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)
            p.drawText(QRectF(x, y, w, 20 * s), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, percent(value))
            by = y + 21 * s
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.color("#ffffff", 20))
            p.drawRoundedRect(QRectF(x, by, w, 4 * s), 2 * s, 2 * s)
            if value is not None:
                p.setBrush(theme.color(bar_col))
                p.drawRoundedRect(QRectF(x, by, w * max(0.0, min(100.0, value)) / 100.0, 4 * s), 2 * s, 2 * s)
            y += 34 * s
        p.setPen(theme.color(theme.MUTED))
        p.setFont(theme.make_font(int(11 * s), spacing=1 * s))
        uptime = human_duration(sample.uptime_s) if sample and sample.uptime_s is not None else NA
        p.drawText(QRectF(x, y - 4 * s, w, 16 * s), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"UPTIME {uptime}")
        if self.ctrl.appearance.overlay_expanded:
            y += 16 * s
            procs = sample.processes_total if sample is not None and sample.processes_total else None
            active = len(self.ctrl.thresholds.active_metrics())
            p.drawText(QRectF(x, y - 4 * s, w, 16 * s), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       f"PROCESSES {procs if procs is not None else NA}")
            y += 16 * s
            p.drawText(QRectF(x, y - 4 * s, w, 16 * s), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       f"ACTIVE WARNINGS {active}")

    # --------------------------------------------------- window-mode interaction
    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if not self.interactive:
            return
        if event.key() == Qt.Key.Key_F11:
            self.setWindowState(self.windowState() ^ Qt.WindowState.WindowFullScreen)
        elif event.key() == Qt.Key.Key_Escape and self.isFullScreen():
            self.showNormal()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self.shutdown()
        if not self.programmatic_close:
            self.closed.emit()
        super().closeEvent(event)
