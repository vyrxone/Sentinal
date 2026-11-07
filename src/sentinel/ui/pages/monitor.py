from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLineEdit, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ...core.formatting import freshness, human_bytes, percent
from .. import theme
from ..widgets import Card, NumItem, SparkGraph, make_label, page_header


class MonitorPage(QWidget):
    title = "Monitor"

    def __init__(self, ctrl) -> None:
        super().__init__()
        self.ctrl = ctrl
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)
        root.addWidget(page_header("System monitor", "Real measurements from your PC. Anything Windows does not "
                                   "allow Sentinel to read is shown as —, never guessed."))
        self.freshness = make_label("", "muted")
        root.addWidget(self.freshness)

        graphs = QHBoxLayout()
        graphs.setSpacing(12)
        self.graphs = {}
        for key, title, col in (("cpu", "CPU %", theme.CRIMSON), ("ram", "Memory %", theme.GOLD),
                                ("disk", "System disk %", theme.GOLD_DIM)):
            card = Card(title)
            g = SparkGraph(col)
            card.add(g)
            graphs.addWidget(card)
            self.graphs[key] = g
        root.addLayout(graphs)

        disks = Card("Disks")
        self.disk_table = QTableWidget(0, 5)
        self.disk_table.setHorizontalHeaderLabels(["Drive", "Type", "Used", "Total", "Usage"])
        self._style_table(self.disk_table)
        self.disk_table.setMinimumHeight(110)
        self.disk_table.setMaximumHeight(150)
        disks.add(self.disk_table)
        root.addWidget(disks)

        procs = Card("Processes")
        top = QHBoxLayout()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter by name…")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self._fill_processes)
        top.addWidget(self.filter, 1)
        self.proc_info = make_label("", "muted")
        top.addWidget(self.proc_info)
        procs.body.addLayout(top)
        self.proc_table = QTableWidget(0, 4)
        self.proc_table.setHorizontalHeaderLabels(["Name", "PID", "CPU %", "Memory"])
        self._style_table(self.proc_table)
        self.proc_table.setMinimumHeight(240)
        self.proc_table.setSortingEnabled(True)
        self.proc_table.sortByColumn(3, Qt.SortOrder.DescendingOrder)   # memory: always known
        procs.add(self.proc_table, 1)
        procs.add(make_label("CPU % is the share of total CPU capacity. Processes Windows protects from Sentinel "
                             "(no administrator rights are requested) show —. CPU is unknown for the first "
                             "second after opening this page.", "muted", wrap=True))
        root.addWidget(procs, 1)
        ctrl.sample_ready.connect(self._on_sample)
        ctrl.state_changed.connect(self._refresh_header)

    @staticmethod
    def _style_table(t: QTableWidget) -> None:
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.verticalHeader().setVisible(False)
        t.setAlternatingRowColors(True)
        t.horizontalHeader().setStretchLastSection(True)
        t.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        self.ctrl.set_collect_processes(True)     # process list is only collected while visible
        self._refresh_all()

    def hideEvent(self, e) -> None:  # noqa: N802
        super().hideEvent(e)
        self.ctrl.set_collect_processes(False)

    def _on_sample(self, _s) -> None:
        if self.isVisible():
            self._refresh_all()

    def _refresh_header(self) -> None:
        if not self.isVisible():
            return
        c = self.ctrl
        if not c.monitoring_active:
            self.freshness.setText("Monitoring is stopped. Use the toolbar (Ctrl+M) to start it. "
                                   "Values below are the last readings, if any.")
        else:
            state, text = freshness(c.data_age(), c.settings.get("sample_interval_s"))
            s = c.last_sample
            extra = f" · last sample took {s.duration_ms:.0f} ms" if s else ""
            errs = f" · notes: {'; '.join(s.errors)}" if s and s.errors else ""
            self.freshness.setText(f"Data feed: {text}{extra}{errs}")

    def _refresh_all(self) -> None:
        self._refresh_header()
        h = self.ctrl.history
        self.graphs["cpu"].set_values(list(h.cpu))
        self.graphs["ram"].set_values(list(h.ram))
        self.graphs["disk"].set_values(list(h.disk))
        s = self.ctrl.last_sample
        self._fill_disks(s)
        self._fill_processes()

    def _fill_disks(self, s) -> None:
        t = self.disk_table
        disks = s.disks if s else ()
        t.setRowCount(len(disks))
        for r, d in enumerate(disks):
            for c, text in enumerate((d.mountpoint, d.fstype, human_bytes(d.used), human_bytes(d.total), percent(d.percent))):
                t.setItem(r, c, QTableWidgetItem(text))
        if not disks:
            t.setRowCount(1)
            t.setItem(0, 0, QTableWidgetItem("No disk information available"))

    def _fill_processes(self) -> None:
        s = self.ctrl.last_process_sample
        t = self.proc_table
        if s is None or s.processes is None:
            t.setRowCount(0)
            self.proc_info.setText("waiting for process data…" if self.ctrl.monitoring_active else "monitoring stopped")
            return
        needle = self.filter.text().strip().lower()
        rows = [p for p in s.processes if not needle or needle in p.name.lower()]
        t.setSortingEnabled(False)
        t.setRowCount(len(rows))
        for r, p in enumerate(rows):
            t.setItem(r, 0, QTableWidgetItem(p.name))
            t.setItem(r, 1, NumItem(str(p.pid), p.pid))
            t.setItem(r, 2, NumItem(percent(p.cpu_percent, 1), p.cpu_percent))
            t.setItem(r, 3, NumItem(human_bytes(p.rss), p.rss))
        t.setSortingEnabled(True)
        restricted = f", {s.processes_restricted} with restricted details" if s.processes_restricted else ""
        self.proc_info.setText(f"showing {len(rows)} of {s.processes_total} processes (top by CPU and memory){restricted}")
