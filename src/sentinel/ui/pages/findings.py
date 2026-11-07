"""Security findings (V3): rule-based observations from the local audit log, with evidence and lifecycle."""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QInputDialog,
                               QLineEdit, QMessageBox, QPlainTextEdit, QSplitter, QTableView, QVBoxLayout, QWidget)

from ...security.findings import CATEGORIES, SEVERITIES, Finding
from ...security.rules import LIMITATIONS, RULES
from ...storage.database import DatabaseError
from .. import theme
from ..widgets import make_button, make_label, page_header

PAGE_SIZE = 500
STATUS_CHOICES = (("Open (new + acknowledged)", "open"), ("New", "new"), ("Acknowledged", "acknowledged"),
                  ("Resolved", "resolved"), ("Dismissed", "dismissed"), ("Everything", "all"))
RANGES = (("Last 24 hours", 86400), ("Last 7 days", 7 * 86400), ("Last 30 days", 30 * 86400), ("All time", None))
SEV_COLORS = {"high": theme.CRIMSON_BRIGHT, "medium": theme.GOLD}


class FindingsModel(QAbstractTableModel):
    HEADERS = ("Severity", "Status", "Title", "Resource", "Last observed", "Seen", "Rule")

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[Finding] = []

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
        f, c = self.rows[index.row()], index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            return (f.severity, f.status + (" (reopened)" if f.reopened and f.status == "new" else ""), f.title,
                    f.resource, f.local_time, f.occurrences, f"{f.rule_id} v{f.rule_version}")[c]
        if role == Qt.ItemDataRole.UserRole:
            return f.id
        if role == Qt.ItemDataRole.ForegroundRole and c == 0 and f.severity in SEV_COLORS:
            return QBrush(QColor(SEV_COLORS[f.severity]))
        if role == Qt.ItemDataRole.ToolTipRole and c in (2, 3):
            return f.title if c == 2 else f.resource
        return None

    def set_rows(self, rows: list[Finding]) -> bool:
        same = [(f.id, f.status, f.last_ts, f.occurrences) for f in rows] == \
               [(f.id, f.status, f.last_ts, f.occurrences) for f in self.rows]
        if same:
            return False
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()
        return True


class FindingsPage(QWidget):
    title = "Findings"

    def __init__(self, ctrl) -> None:
        super().__init__()
        self.ctrl = ctrl
        self.model = FindingsModel()
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(10)
        root.addWidget(page_header(
            "Security findings",
            "Rule-based observations from Sentinel's local audit log. A finding is not proof of malicious "
            "activity; it says what was recorded, why it was grouped, and a safe next step."))
        bar = QHBoxLayout()
        self.status = QComboBox()
        for label, key in STATUS_CHOICES:
            self.status.addItem(label, key)
        self.severity = QComboBox()
        self.severity.addItem("All severities", None)
        for s in SEVERITIES:
            self.severity.addItem(s.capitalize(), s)
        self.category = QComboBox()
        self.category.addItem("All categories", None)
        for c in CATEGORIES:
            self.category.addItem(c.capitalize(), c)
        self.range = QComboBox()
        for label, _ in RANGES:
            self.range.addItem(label)
        self.range.setCurrentIndex(2)
        self.text = QLineEdit()
        self.text.setPlaceholderText("Search title, resource or rule…")
        self.text.setClearButtonEnabled(True)
        for w in (self.status, self.severity, self.category, self.range):
            bar.addWidget(w)
            w.currentIndexChanged.connect(self.reload)
        bar.addWidget(self.text, 1)
        self.b_analyze = make_button("Analyze now", primary=True,
                                     tip="Run the rules over the last 24 hours of the audit log")
        self.b_export = make_button("Export…", tip="Save the findings shown, with their event references")
        self.b_rules = make_button("What findings can't tell you…")
        for b in (self.b_analyze, self.b_export, self.b_rules):
            bar.addWidget(b)
        root.addLayout(bar)

        acts = QHBoxLayout()
        self.b_ack = make_button("Acknowledge")
        self.b_resolve = make_button("Mark resolved")
        self.b_dismiss = make_button("Dismiss…", tip="Dismiss with an optional reason")
        self.b_reopen = make_button("Reopen")
        self.b_ai = make_button("Ask AI…", tip="Open the AI Assistant with this finding selected. Nothing is sent until you review it.")
        self.b_ai.clicked.connect(self._ask_ai)
        for b in (self.b_ack, self.b_resolve, self.b_dismiss, self.b_reopen, self.b_ai):
            acts.addWidget(b)
        acts.addStretch(1)
        self.fresh = make_label("", "muted")
        acts.addWidget(self.fresh)
        root.addLayout(acts)
        self.count = make_label("", "muted")
        root.addWidget(self.count)
        self.empty = make_label("", "muted", wrap=True)
        root.addWidget(self.empty)

        split = QSplitter(Qt.Orientation.Vertical)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for i, w in enumerate((80, 130, 300, 220, 140, 50, 90)):
            self.table.setColumnWidth(i, w)
        self.table.selectionModel().selectionChanged.connect(lambda *_: self._show_detail())
        split.addWidget(self.table)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setPlaceholderText("Select a finding to see its explanation and evidence.")
        split.addWidget(self.detail)
        split.setSizes([300, 260])
        root.addWidget(split, 1)

        self.b_analyze.clicked.connect(self.analyze_now)
        self.b_export.clicked.connect(self.export)
        self.b_rules.clicked.connect(self.show_rules)
        self.b_ack.clicked.connect(lambda: self._act("acknowledge"))
        self.b_resolve.clicked.connect(lambda: self._act("resolve"))
        self.b_dismiss.clicked.connect(self._dismiss)
        self.b_reopen.clicked.connect(lambda: self._act("reopen"))

        self._search = QTimer(self)
        self._search.setSingleShot(True)
        self._search.setInterval(300)
        self._search.timeout.connect(self.reload)
        self.text.textChanged.connect(lambda _t: self._search.start())
        self._live = QTimer(self)
        self._live.setSingleShot(True)
        self._live.setInterval(500)
        self._live.timeout.connect(self.reload)
        ctrl.findings_changed.connect(lambda: self._live.start() if self.isVisible() else None)

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        self.reload()

    # -------------------------------------------------------------- data
    def _filters(self) -> dict:
        seconds = RANGES[self.range.currentIndex()][1]
        return dict(status=self.status.currentData(), severity=self.severity.currentData(),
                    category=self.category.currentData(), text=self.text.text().strip() or None,
                    since=(time.time() - seconds) if seconds else None)

    def reload(self, *_a) -> None:
        if not self.isVisible():
            return
        try:
            f = self._filters()
            total = self.ctrl.findings.count(**f)
            rows = self.ctrl.findings.query(limit=PAGE_SIZE, **f)
            overall = self.ctrl.findings.count()
        except DatabaseError as exc:
            self.count.setText(f"Could not read findings: {exc}")
            return
        keep = self._selected_ids()
        if self.model.set_rows(rows):
            for r, fnd in enumerate(rows):
                if fnd.id in keep:
                    self.table.selectRow(r)
            self._show_detail()
        shown = len(rows)
        self.count.setText(f"{total} finding(s) match" + (f" (showing the newest {shown})" if total > shown else "") + ".")
        res = self.ctrl.engine.last_result
        if not self.ctrl.findings_enabled:
            self.fresh.setText("Analysis is turned off in Settings.")
        elif res is None or self.ctrl.engine.last_run_ts is None:
            self.fresh.setText("Not analysed yet this session.")
        else:
            at = datetime.fromtimestamp(self.ctrl.engine.last_run_ts).strftime("%H:%M:%S")
            self.fresh.setText(f"Last analysis {at}: {res.events_examined} event(s) examined in "
                               f"{res.duration_ms:.0f} ms ({self.ctrl.engine.sensitivity} sensitivity).")
        if total == 0:
            self.empty.setText(
                "No findings match. " + (
                    "Sentinel's rules found nothing in the last 24 hours of the audit log. That does not prove "
                    "nothing happened: it only covers what Sentinel observed while it was running."
                    if overall == 0 else "Try a wider time range or choose 'Everything'."))
        else:
            self.empty.setText("")

    def _selected_ids(self) -> list[int]:
        rows = sorted(i.row() for i in self.table.selectionModel().selectedRows())
        return [self.model.rows[r].id for r in rows if r < len(self.model.rows)]

    def _ask_ai(self) -> None:
        f = self.selected_finding()
        if f is not None:
            self.ctrl.ai.request_context("finding", f.id)

    def selected_finding(self) -> Optional[Finding]:
        ids = self._selected_ids()
        return self.ctrl.findings.get(ids[0]) if ids else None

    def _show_detail(self) -> None:
        f = self.selected_finding()
        if f is None:
            self.detail.clear()
            return
        iso = lambda t: datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S")  # noqa: E731
        lines = [f"{f.title}", f"[{f.severity.upper()}]  status: {f.status}" + (" (reopened after new evidence)" if f.reopened else ""),
                 f"Rule {f.rule_id} v{f.rule_version}  ·  finding id {f.finding_key}",
                 f"First observed {iso(f.first_ts)}  ·  last observed {iso(f.last_ts)}  ·  {f.occurrences} related event(s)",
                 f"Affected resource: {f.resource or '(none)'}", "",
                 "Why this was raised", f.explanation, "",
                 "How certain this is", f.confidence, "",
                 "Suggested safe next step", f.next_step]
        if f.dismiss_reason:
            lines += ["", f"Dismissed because: {f.dismiss_reason}"]
        lines += ["", "Evidence", json.dumps(f.evidence, indent=2, ensure_ascii=False), "",
                  f"Related events ({len(f.events)} stored reference(s))"]
        for r in f.events[:100]:
            lines.append(f"  #{r.event_id}  {datetime.fromtimestamp(r.ts).strftime('%Y-%m-%d %H:%M:%S')}  "
                         f"{r.event_type}  {r.summary}")
        if len(f.events) > 100:
            lines.append(f"  … {len(f.events) - 100} more (all are included in exports)")
        self.detail.setPlainText("\n".join(lines))

    # -------------------------------------------------------------- actions
    def analyze_now(self) -> None:
        self.b_analyze.setEnabled(False)
        self.fresh.setText("Analysing…")
        engine = self.ctrl.engine

        def work(cancel, _p):
            return engine.analyze(cancel=cancel)

        def done(res) -> None:
            self.b_analyze.setEnabled(True)
            self.ctrl._analysis_done(res)
            self.reload()

        def failed(msg: str) -> None:
            self.b_analyze.setEnabled(True)
            self.fresh.setText(f"Analysis failed: {msg}")

        self.ctrl.jobs.submit(work, done, failed, name="Findings analysis", timeout_s=30.0, key="findings-analysis")

    def _act(self, action: str) -> None:
        ids = self._selected_ids()
        if not ids:
            QMessageBox.information(self, "Findings", "Select one or more findings first.")
            return
        self.ctrl.finding_action(ids, action)
        self.reload()

    def _dismiss(self) -> None:
        ids = self._selected_ids()
        if not ids:
            QMessageBox.information(self, "Findings", "Select one or more findings first.")
            return
        reason, ok = QInputDialog.getText(self, "Dismiss finding", "Why is this not a concern? (optional)")
        if not ok:
            return
        self.ctrl.finding_action(ids, "dismiss", reason)
        self.reload()

    def show_rules(self) -> None:
        text = "\n\n".join(LIMITATIONS) + "\n\nRules in this version:\n" + "\n".join(
            f"• {r.rule_id} v{r.version} - {r.title}: {r.description} Limitation: {r.limitation}" for r in RULES)
        QMessageBox.information(self, "What findings can and cannot tell you", text)

    def export(self) -> None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path, selected = QFileDialog.getSaveFileName(self, "Export findings", f"sentinel-findings-{stamp}.json",
                                                     "JSON (*.json);;CSV (*.csv)")
        if not path:
            return
        fmt = "csv" if path.lower().endswith(".csv") or ("CSV" in selected and not path.lower().endswith(".json")) else "json"
        if not path.lower().endswith("." + fmt):
            path += "." + fmt
        filters = self._filters()
        store = self.ctrl.findings

        def work(_c, _p):
            return store.export(path, fmt, **filters)[1]

        def done(n: int) -> None:
            self.ctrl.log("app", "findings_exported", f"Exported {n} finding(s) to {fmt.upper()}.", details={"file": path})
            QMessageBox.information(self, "Export complete", f"Exported {n} finding(s) with event references to:\n{path}")

        self.ctrl.jobs.submit(work, done, lambda m: QMessageBox.critical(self, "Export failed", m),
                              name="Findings export", timeout_s=300.0)
