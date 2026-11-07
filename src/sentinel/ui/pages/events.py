from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Optional

from PySide6.QtCore import QAbstractTableModel, QItemSelectionModel, QModelIndex, Qt, QTimer
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QHeaderView,
                               QLineEdit, QMessageBox, QPlainTextEdit, QSplitter, QTableView, QVBoxLayout,
                               QWidget)

from ...security.info import EVENT_EXPLANATIONS
from ...storage.database import DatabaseError
from ...storage.events_store import CATEGORIES, SEVERITIES, Event
from ...storage.export import export_events_streamed
from .. import theme
from ..widgets import make_button, make_label, page_header

PAGE_SIZE = 500             # rows held and rendered at once; the rest stay in SQLite (V3: DB-backed paging)
SORT_KEYS = ("ts", "severity", "category", "event_type", "path", "message", None)   # None = derived column
STATUS_CHOICES = (("Not archived", "active"), ("Needs attention", "unacknowledged"),
                  ("Acknowledged", "acknowledged"), ("Archived", "archived"), ("Everything", "all"))
RANGES = (("Last hour", 3600), ("Last 24 hours", 86400), ("Last 7 days", 7 * 86400),
          ("Last 30 days", 30 * 86400), ("All time", None))


class EventsModel(QAbstractTableModel):
    """One page of events. Updates in place (dataChanged) when only flags changed, resets otherwise."""

    HEADERS = ("Time", "Severity", "Category", "Type", "Path", "Message", "State")

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[Event] = []
        self.states: list[str] = []

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.HEADERS[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self.rows)):
            return None
        e, c = self.rows[index.row()], index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            return (e.local_time, e.severity, e.category, e.event_type, e.path or "", e.message,
                    self.states[index.row()])[c]
        if role == Qt.ItemDataRole.UserRole:
            return e.id
        if role == Qt.ItemDataRole.ForegroundRole and c == 1:
            color = {"warning": theme.CRIMSON_BRIGHT, "notice": theme.GOLD}.get(e.severity)
            return QBrush(QColor(color)) if color else None
        if role == Qt.ItemDataRole.ToolTipRole and c in (4, 5):
            return e.path if c == 4 else e.message
        return None

    def event_at(self, row: int) -> Optional[Event]:
        return self.rows[row] if 0 <= row < len(self.rows) else None

    def row_of(self, event_id: int) -> int:
        for i, e in enumerate(self.rows):
            if e.id == event_id:
                return i
        return -1

    def set_rows(self, rows: list[Event], states: list[str]) -> str:
        """Returns 'same', 'changed' (in-place update) or 'reset' (rows added/removed/reordered)."""
        if [e.id for e in rows] == [e.id for e in self.rows]:
            changed = [i for i, (new, old) in enumerate(zip(rows, self.rows))
                       if (new.acknowledged, new.archived) != (old.acknowledged, old.archived)
                       or states[i] != self.states[i]]
            self.rows, self.states = rows, states
            for i in changed:
                self.dataChanged.emit(self.index(i, 0), self.index(i, len(self.HEADERS) - 1))
            return "changed" if changed else "same"
        self.beginResetModel()
        self.rows, self.states = rows, states
        self.endResetModel()
        return "reset"


class EventsPage(QWidget):
    title = "Events"

    def __init__(self, ctrl) -> None:
        super().__init__()
        self.ctrl = ctrl
        self.model = EventsModel()
        self._page = 0
        self._total = 0
        self._sort = 0               # column index
        self._desc = True
        self.last_load_ms = 0.0
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(10)
        root.addWidget(page_header("Events & audit log",
                                   "Everything Sentinel observed or did, stored locally. Filter, review and export."))
        bar = QHBoxLayout()
        self.range = QComboBox()
        for label, _ in RANGES:
            self.range.addItem(label)
        self.range.setCurrentIndex(1)
        self.category = QComboBox()
        self.category.addItem("All categories", None)
        for c in CATEGORIES:
            self.category.addItem(c.capitalize(), c)
        self.severity = QComboBox()
        self.severity.addItem("All severities", None)
        for s in SEVERITIES:
            self.severity.addItem(s.capitalize(), s)
        self.type = QComboBox()
        self.type.addItem("All types", None)
        self.type.setMinimumWidth(150)
        self.status = QComboBox()
        for label, key in STATUS_CHOICES:
            self.status.addItem(label, key)
        self.status.setToolTip("Needs attention = warnings and notices you have not acknowledged yet")
        self.text = QLineEdit()
        self.text.setPlaceholderText("Search message or path…")
        self.text.setClearButtonEnabled(True)
        self.text.setMinimumWidth(170)
        for w in (self.range, self.category, self.severity, self.type, self.status):
            bar.addWidget(w)
            w.currentIndexChanged.connect(self.reload)
        bar.addWidget(self.text, 1)
        self.live = QCheckBox("Live")
        self.live.setChecked(True)
        refresh = make_button("Refresh")
        refresh.clicked.connect(self.reload)
        export = make_button("Export…", primary=True)
        export.clicked.connect(self.export)
        for w in (self.live, refresh, export):
            bar.addWidget(w)
        root.addLayout(bar)
        manage = QHBoxLayout()
        self.b_ack = make_button("Acknowledge", tip="Mark the selected events as reviewed")
        self.b_unack = make_button("Unacknowledge", tip="Mark the selected events as not yet reviewed")
        self.b_archive = make_button("Archive", tip="Move the selected events out of the active list (kept on disk)")
        self.b_ack_all = make_button("Acknowledge all shown…")
        self.b_archive_all = make_button("Archive all shown…")
        self.b_delete = make_button("Delete archived…", danger=True,
                                    tip="Permanently remove events you have archived")
        self.b_ai = make_button("Ask AI…", tip="Open the AI Assistant with the selected events. Nothing is sent until you review it.")
        self.b_ai.clicked.connect(self._ask_ai)
        for b in (self.b_ack, self.b_unack, self.b_archive, self.b_ack_all, self.b_archive_all, self.b_delete, self.b_ai):
            manage.addWidget(b)
        manage.addStretch(1)
        root.addLayout(manage)
        self.b_ack.clicked.connect(lambda: self._ack_selected(True))
        self.b_unack.clicked.connect(lambda: self._ack_selected(False))
        self.b_archive.clicked.connect(self._archive_selected)
        self.b_ack_all.clicked.connect(self._ack_all)
        self.b_archive_all.clicked.connect(self._archive_all)
        self.b_delete.clicked.connect(self._delete_archived)
        pager = QHBoxLayout()
        self.count = make_label("", "muted")
        pager.addWidget(self.count, 1)
        self.b_prev = make_button("◀ Newer" , tip="Previous page")
        self.b_next = make_button("Older ▶", tip="Next page")
        self.page_label = make_label("", "muted")
        for w in (self.b_prev, self.page_label, self.b_next):
            pager.addWidget(w)
        self.b_prev.clicked.connect(lambda: self._goto_page(self._page - 1))
        self.b_next.clicked.connect(lambda: self._goto_page(self._page + 1))
        root.addLayout(pager)
        self.empty = make_label("", "muted", wrap=True)
        root.addWidget(self.empty)

        split = QSplitter(Qt.Orientation.Vertical)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(24)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.setSortingEnabled(False)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionsClickable(True)
        hdr.sectionClicked.connect(self._on_header)
        for i, w in enumerate((140, 78, 78, 140, 200, 300, 110)):
            self.table.setColumnWidth(i, w)
        self.table.selectionModel().selectionChanged.connect(lambda *_: self._show_detail())
        split.addWidget(self.table)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setPlaceholderText("Select an event to see its details.")
        split.addWidget(self.detail)
        split.setSizes([420, 150])
        root.addWidget(split, 1)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(300)
        self._search_timer.timeout.connect(self.reload)
        self.text.textChanged.connect(lambda _t: self._search_timer.start())
        self._live_timer = QTimer(self)
        self._live_timer.setSingleShot(True)
        self._live_timer.setInterval(700)
        self._live_timer.timeout.connect(self.reload)
        ctrl.event_added.connect(self._on_new_event)
        ctrl.alerts_changed.connect(self._on_new_event)

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        self.reload()

    def _on_new_event(self, _e=None) -> None:   # also connected to alerts_changed, which carries no argument
        if self.isVisible() and self.live.isChecked():
            self._live_timer.start()

    def _filters(self) -> dict:
        seconds = RANGES[self.range.currentIndex()][1]
        return dict(category=self.category.currentData(), severity=self.severity.currentData(),
                    event_type=self.type.currentData(), text=self.text.text().strip() or None,
                    status=self.status.currentData(), since=(time.time() - seconds) if seconds else None)

    @property
    def _events(self) -> list[Event]:
        return self.model.rows

    def _on_header(self, col: int) -> None:
        if SORT_KEYS[col] is None:
            return
        if col == self._sort:
            self._desc = not self._desc
        else:
            self._sort, self._desc = col, col in (0, 1)
        self.table.horizontalHeader().setSortIndicatorShown(True)
        self.table.horizontalHeader().setSortIndicator(
            col, Qt.SortOrder.DescendingOrder if self._desc else Qt.SortOrder.AscendingOrder)
        self._page = 0
        self.reload()

    def _goto_page(self, page: int) -> None:
        pages = max(1, -(-self._total // PAGE_SIZE))
        self._page = max(0, min(page, pages - 1))
        self.reload()

    def _selected_ids_set(self) -> set:
        return {self.model.data(i, Qt.ItemDataRole.UserRole) for i in self.table.selectionModel().selectedRows()}

    def reload(self, *_a) -> None:
        if not self.isVisible():
            return
        t0 = time.perf_counter()
        try:
            f = self._filters()
            total = self.ctrl.events.count(**f)
            pages = max(1, -(-total // PAGE_SIZE))
            self._page = min(self._page, pages - 1)
            rows = self.ctrl.events.query(limit=PAGE_SIZE, offset=self._page * PAGE_SIZE,
                                          sort=SORT_KEYS[self._sort] or "ts", newest_first=self._desc, **f)
            self._sync_types()
            active_keys = {a["key"] for a in self.ctrl.alert_state.active()}
        except DatabaseError as exc:
            self.count.setText(f"Could not read events: {exc}")
            return
        self._total = total
        keep = self._selected_ids_set()
        current = self._selected_id()
        outcome = self.model.set_rows(rows, [self._state_of(e, active_keys) for e in rows])
        if outcome == "reset" and keep:                      # stable selection across page/filter/live updates
            sm = self.table.selectionModel()
            for r, e in enumerate(rows):
                if e.id in keep:
                    sm.select(self.model.index(r, 0), QItemSelectionModel.SelectionFlag.Select
                              | QItemSelectionModel.SelectionFlag.Rows)
                    if e.id == current:
                        sm.setCurrentIndex(self.model.index(r, 0), QItemSelectionModel.SelectionFlag.NoUpdate)
        if outcome != "same":
            self._show_detail()
        shown = len(rows)
        start = self._page * PAGE_SIZE
        rng = f" Showing {start + 1}-{start + shown}." if shown else ""
        self.count.setText(f"{total} event(s) match.{rng}")
        self.page_label.setText(f"Page {self._page + 1} of {pages}")
        self.b_prev.setEnabled(self._page > 0)
        self.b_next.setEnabled(self._page + 1 < pages)
        if total == 0:
            self.empty.setText("Nothing matches these filters. "
                               + ("Events appear here as Sentinel observes things." if self.ctrl.events.count() == 0
                                  else "Try a wider time range or choose 'Everything'."))
        else:
            self.empty.setText("")
        self.last_load_ms = (time.perf_counter() - t0) * 1000

    @staticmethod
    def _state_of(e: Event, active_keys: set) -> str:
        if e.archived:
            return "Archived"
        if e.severity == "warning" and e.event_type.endswith("_warning") and e.event_type.split("_")[0] in active_keys:
            return "Current" + (" (acknowledged)" if e.acknowledged else "")
        if e.severity == "info":
            return "Info"
        return "Acknowledged" if e.acknowledged else "Needs attention"

    def _ask_ai(self) -> None:
        rows = [self.model.rows[r] for r in sorted(i.row() for i in self.table.selectionModel().selectedRows())
                if r < len(self.model.rows)]
        if rows:
            task = "integrity" if all(e.event_type.startswith("baseline") for e in rows) else "events"
            self.ctrl.ai.request_context(task, None, [e.id for e in rows])

    def _selected_ids(self) -> list[int]:
        rows = sorted(i.row() for i in self.table.selectionModel().selectedRows())
        return [self.model.rows[r].id for r in rows if r < len(self.model.rows)]

    def _ack_selected(self, ack: bool) -> None:
        n = self.ctrl.events.acknowledge(self._selected_ids(), ack)
        self.count.setText(f"{n} event(s) {'acknowledged' if ack else 'marked unacknowledged'}.")
        self.ctrl.alerts_changed.emit()

    def _archive_selected(self) -> None:
        n = self.ctrl.events.archive(self._selected_ids())
        self.ctrl.alerts_changed.emit()
        self.count.setText(f"{n} event(s) archived (still stored; choose 'Archived' to see them).")

    def _confirm(self, title: str, text: str) -> bool:
        return QMessageBox.question(self, title, text, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                    QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    def _ack_all(self) -> None:
        f = self._filters()
        n = self.ctrl.events.count(**f)
        if n and self._confirm("Acknowledge all", f"Mark all {n} event(s) matching the current filters as acknowledged?"):
            self.ctrl.events.acknowledge_matching(True, **f)
            self.ctrl.alerts_changed.emit()

    def _archive_all(self) -> None:
        f = self._filters()
        n = self.ctrl.events.count(**f)
        if n and self._confirm("Archive all", f"Archive all {n} event(s) matching the current filters?\n\n"
                                              "They are not deleted; you can still view and export them."):
            self.ctrl.events.archive_matching(**f)
            self.ctrl.log("app", "events_archived", f"{n} event(s) archived by the user.")
            self.ctrl.alerts_changed.emit()

    def _delete_archived(self) -> None:
        n = self.ctrl.events.count(status="archived")
        if not n:
            QMessageBox.information(self, "Delete archived", "There are no archived events.")
            return
        if self._confirm("Delete archived events", f"Permanently delete {n} archived event(s)?\n\nThis cannot be undone. "
                                                   "Export them first if you may need them."):
            removed = self.ctrl.events.delete_archived()
            self.ctrl.log("app", "events_deleted", f"{removed} archived event(s) permanently deleted by the user.",
                          severity="notice")
            self.ctrl.alerts_changed.emit()

    def _sync_types(self) -> None:
        types = self.ctrl.events.distinct_types()
        current = self.type.currentData()
        have = [self.type.itemData(i) for i in range(1, self.type.count())]
        if have != types:
            self.type.blockSignals(True)
            self.type.clear()
            self.type.addItem("All types", None)
            for t in types:
                self.type.addItem(t, t)
            idx = self.type.findData(current)
            self.type.setCurrentIndex(max(0, idx))
            self.type.blockSignals(False)

    def _selected_id(self) -> Optional[int]:
        idx = self.table.currentIndex()
        if not idx.isValid():
            return None
        e = self.model.event_at(idx.row())
        return e.id if e else None

    def _show_detail(self) -> None:
        eid = self._selected_id()
        event = next((e for e in self._events if e.id == eid), None)
        if event is None:
            self.detail.clear()
            return
        lines = [f"{event.local_time}   [{event.severity}]   {event.category} / {event.event_type}"]
        if event.acknowledged:
            lines.append("Acknowledged by you.")
        if event.path:
            lines.append(f"Path: {event.path}")
        lines += ["", event.message]
        hint = EVENT_EXPLANATIONS.get(event.event_type)
        if hint and hint not in event.message:
            lines += ["", hint]
        if event.details:
            lines += ["", "Details:", json.dumps(event.details, indent=2, ensure_ascii=False)]
        self.detail.setPlainText("\n".join(lines))

    def export(self) -> None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path, selected = QFileDialog.getSaveFileName(
            self, "Export events", f"sentinel-events-{stamp}.json", "JSON (*.json);;CSV (*.csv)")
        if not path:
            return
        fmt = "csv" if (path.lower().endswith(".csv") or "CSV" in selected and not path.lower().endswith(".json")) else "json"
        if not path.lower().endswith("." + fmt):
            path += "." + fmt
        filters = self._filters()
        events = self.ctrl.events

        def work(cancel, progress):
            total = events.count(**filters)
            return export_events_streamed(events.iter_batches(**filters), path, fmt, cancel=cancel,
                                          progress=progress, total=total)

        def done(n: int) -> None:
            self.ctrl.log("app", "events_exported", f"Exported {n} event(s) to {fmt.upper()}.", details={"file": path})
            QMessageBox.information(self, "Export complete", f"Exported {n} event(s) (current filters) to:\n{path}")

        def failed(message: str) -> None:
            QMessageBox.critical(self, "Export failed", message)

        self.ctrl.jobs.submit(work, done, failed, name="Events export", timeout_s=600.0)
