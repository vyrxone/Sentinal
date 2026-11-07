from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from ...core.formatting import NA, freshness, human_bytes, percent
from ...monitoring.sampler import get_system_info
from .. import theme
from ..widgets import Card, SparkGraph, StatTile, make_label, page_header


class OverviewPage(QWidget):
    title = "Overview"

    def __init__(self, ctrl) -> None:
        super().__init__()
        self.ctrl = ctrl
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)
        root.addWidget(page_header("Overview", "Live status of this PC and of Sentinel."))

        tiles = QHBoxLayout()
        tiles.setSpacing(12)
        self.cpu, self.ram, self.disk, self.feed = (StatTile(t) for t in ("CPU", "Memory", "System disk", "Data feed"))
        for t in (self.cpu, self.ram, self.disk, self.feed):
            tiles.addWidget(t)
        root.addLayout(tiles)

        mid = QGridLayout()
        mid.setSpacing(12)
        graphs = Card("Last few minutes")
        graphs.add(make_label("CPU %", "muted"))
        self.cpu_graph = SparkGraph(theme.CRIMSON)
        graphs.add(self.cpu_graph, 1)
        graphs.add(make_label("Memory %", "muted"))
        self.ram_graph = SparkGraph(theme.GOLD)
        graphs.add(self.ram_graph, 1)
        graphs.setMinimumHeight(300)
        mid.addWidget(graphs, 0, 0, 2, 1)

        self.status_card = Card("Sentinel status")
        self.status_labels = {}
        for key in ("Mode", "Wallpaper", "Monitoring", "Folder watching", "Animation", "Load", "Alerts"):
            row = QHBoxLayout()
            name = make_label(key, "muted")
            name.setFixedWidth(120)
            value = make_label("", wrap=True)
            row.addWidget(name, 0, Qt.AlignmentFlag.AlignTop)
            row.addWidget(value, 1)
            self.status_card.body.addLayout(row)
            self.status_labels[key] = value
        self.status_card.setMinimumHeight(265)
        mid.addWidget(self.status_card, 0, 1)

        self.info_card = Card("This PC")
        self.info_labels = []
        for key, value in get_system_info().items():
            row = QHBoxLayout()
            name = make_label(key, "muted")
            name.setFixedWidth(120)
            row.addWidget(name)
            row.addWidget(make_label(value, wrap=True), 1)
            self.info_card.body.addLayout(row)
        self.info_card.setMinimumHeight(215)
        mid.addWidget(self.info_card, 1, 1)
        mid.setColumnStretch(0, 3)
        mid.setColumnStretch(1, 2)
        root.addLayout(mid)

        recent = Card("Recent activity")
        self.recent = QListWidget()
        self.recent.setMinimumHeight(150)
        self.recent.setMaximumHeight(190)
        self.recent.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        recent.add(self.recent)
        root.addWidget(recent)
        root.addStretch(1)

        self._tick = QTimer(self)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self.refresh)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(600)
        self._debounce.timeout.connect(self.refresh_recent)
        ctrl.sample_ready.connect(self._on_sample)
        ctrl.state_changed.connect(self.refresh)
        ctrl.alerts_changed.connect(self.refresh)
        ctrl.event_added.connect(lambda _e: self._debounce.start())

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        self._tick.start()
        self.refresh()
        self.refresh_recent()

    def hideEvent(self, e) -> None:  # noqa: N802
        super().hideEvent(e)
        self._tick.stop()

    def _on_sample(self, _s) -> None:
        if self.isVisible():
            self.refresh()

    def refresh(self) -> None:
        if not self.isVisible():
            return
        c = self.ctrl
        s = c.last_sample
        interval = c.settings.get("sample_interval_s")
        fstate, ftext = freshness(c.data_age(), interval) if c.monitoring_active else ("off", "monitoring is off")
        live = fstate == "live" and s is not None
        warn = set(c.thresholds.active_metrics())
        self.cpu.set(percent(s.cpu_percent) if live else NA,
                     f"{s.cpu_count} logical cores" if live and s.cpu_count else "no live reading",
                     "warn" if "cpu" in warn else ("ok" if live else "off"))
        self.ram.set(percent(s.memory_percent) if live else NA,
                     f"{human_bytes(s.memory_used)} of {human_bytes(s.memory_total)}" if live else "no live reading",
                     "warn" if "ram" in warn else ("ok" if live else "off"))
        self.disk.set(percent(s.system_disk_percent) if live else NA,
                      "system drive" if live else "no live reading",
                      "warn" if "disk" in warn else ("ok" if live else "off"))
        self.feed.set(fstate.upper(), ftext, {"live": "ok", "stale": "warn", "off": "off"}[fstate])
        self.cpu_graph.set_values(list(c.history.cpu))
        self.ram_graph.set_values(list(c.history.ram))

        L = self.status_labels
        L["Mode"].setText("Wallpaper mode" if c.mode == "wallpaper" else "Control panel mode")
        L["Wallpaper"].setText(f"{c.desktop_label}. {c.desktop_detail}" if c.mode == "wallpaper" else c.desktop_detail)
        unacked = c.events.unacknowledged_count()
        active = len(c.alert_state.active())
        L["Alerts"].setText(f"{active} active threshold alert(s); {unacked} unacknowledged warning/notice event(s)"
                            if (active or unacked) else "No active alerts")
        L["Monitoring"].setText("Running" if c.monitoring_active else "Stopped")
        L["Folder watching"].setText(
            f"Running - {len(c.security.list_folders())} folder(s)" if c.security_active else "Stopped")
        q = c.quality
        L["Animation"].setText("Paused" if c.animation_paused else f"{q.fps} FPS cap, {c.settings.get('animation_intensity')} intensity")
        L["Load"].setText("Sustained high load - effects reduced" if c.load.active and c.settings.get("reduce_under_load")
                          else ("Sustained high load" if c.load.active else "Normal"))

    def refresh_recent(self) -> None:
        if not self.isVisible():
            return
        self.recent.clear()
        for e in self.ctrl.events.query(limit=8):
            item = QListWidgetItem(f"{e.local_time}   {e.event_type}   {e.message[:110]}")
            if e.severity == "warning":
                item.setForeground(QBrush(QColor(theme.CRIMSON_BRIGHT)))
            self.recent.addItem(item)
        if self.recent.count() == 0:
            self.recent.addItem("No events recorded yet.")
