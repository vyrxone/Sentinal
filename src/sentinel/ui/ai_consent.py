"""The per-request review dialog: nothing leaves the PC until the user confirms exactly what is shown here."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QListWidget, QListWidgetItem, QPlainTextEdit, QTabWidget,
                               QVBoxLayout)

from ..ai.context_builder import ContextItem, redaction_summary, summarize_fields
from ..ai.models import AIRequest
from .widgets import make_button, make_label


@dataclass
class ReviewResult:
    selected: set
    include_history: bool


class ConsentDialog(QDialog):
    """``compose(selected, include_history)`` -> AIRequest; ``describe(request)`` -> (readable, exact_json)."""

    def __init__(self, parent, *, plan: list[tuple[str, str]], items: list[ContextItem], history_count: int,
                 compose: Callable[[set, bool], AIRequest], describe: Callable[[AIRequest], tuple[str, str]],
                 preselected: Optional[set] = None, redaction_note: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("Review before sending")
        self.setModal(True)
        self.resize(760, 640)
        self.items, self._compose, self._describe = items, compose, describe
        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        lay.addWidget(make_label("Review before anything leaves this PC", "title"))
        first_name, first_model = plan[0]
        route = " → ".join(f"{n} ({m})" for n, m in plan)
        self.banner = make_label(
            f"Provider: {first_name} · Model: {first_model}\n"
            "The text in the preview below will be sent over the internet to this provider and processed under the "
            "provider's own terms, privacy policy and retention rules, which Sentinel does not control."
            + (f"\nIf it cannot answer, the SAME request may be sent to the next provider(s): {route}." if len(plan) > 1 else ""),
            "banner", wrap=True)
        lay.addWidget(self.banner)
        if redaction_note:
            lay.addWidget(make_label(redaction_note, "muted", wrap=True))
        lay.addWidget(make_label("CONTEXT ITEMS (uncheck anything you do not want to send)", "section"))
        self.checks = QListWidget()
        self.checks.setMaximumHeight(130)
        pre = preselected if preselected is not None else {i.item_id for i in items if i.default_on}
        for it in items:
            row = QListWidgetItem(f"{it.label}  ·  {it.size} characters")
            row.setData(Qt.ItemDataRole.UserRole, it.item_id)
            row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            row.setCheckState(Qt.CheckState.Checked if it.item_id in pre else Qt.CheckState.Unchecked)
            self.checks.addItem(row)
        lay.addWidget(self.checks)
        self.history = QCheckBox(f"Include earlier messages of this conversation ({history_count})")
        self.history.setEnabled(history_count > 0)
        self.history.setChecked(history_count > 0)
        lay.addWidget(self.history)
        self.fields = make_label("", "muted", wrap=True)
        lay.addWidget(self.fields)
        self.tabs = QTabWidget()
        self.readable = QPlainTextEdit(); self.readable.setReadOnly(True)
        self.exact = QPlainTextEdit(); self.exact.setReadOnly(True)
        self.tabs.addTab(self.readable, "Readable preview")
        self.tabs.addTab(self.exact, "Exact request body")
        lay.addWidget(self.tabs, 1)
        lay.addWidget(make_label("The API key is added by Sentinel as a separate header and is not part of this text. "
                                 "File contents, notes, logs and environment variables are never included.", "muted", wrap=True))
        row = QHBoxLayout()
        row.addStretch(1)
        self.cancel = make_button("Cancel - send nothing")
        self.send = make_button(f"Send to {first_name}", primary=True)
        row.addWidget(self.cancel); row.addWidget(self.send)
        lay.addLayout(row)
        self.cancel.setDefault(True)                 # Enter never sends by accident
        self.cancel.clicked.connect(self.reject)
        self.send.clicked.connect(self.accept)
        self.checks.itemChanged.connect(lambda *_: self.refresh())
        self.history.toggled.connect(lambda *_: self.refresh())
        self.refresh()

    def selected(self) -> set:
        return {self.checks.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.checks.count())
                if self.checks.item(i).checkState() == Qt.CheckState.Checked}

    def result_value(self) -> ReviewResult:
        return ReviewResult(self.selected(), self.history.isChecked() and self.history.isEnabled())

    def refresh(self) -> None:
        sel = self.selected()
        req = self._compose(sel, self.history.isChecked() and self.history.isEnabled())
        readable, exact = self._describe(req)
        self.readable.setPlainText(readable)
        self.exact.setPlainText(exact)
        fields = summarize_fields(self.items, sel)
        red = redaction_summary(self.items, sel)
        self.fields.setText("Fields included: " + (", ".join(fields) if fields else "none (only your question)")
                            + ("\nRemoved before sending: " + ", ".join(f"{n} {k}" for k, n in red.items()) if red else ""))
