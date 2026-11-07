"""Reusable widgets for the control panel."""

from __future__ import annotations

from typing import Optional, Sequence

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from ..core.formatting import NA
from . import theme


def repolish(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def make_label(text: str = "", role: Optional[str] = None, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    if role:
        label.setProperty("role", role)
    label.setWordWrap(wrap)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def make_button(text: str, *, primary: bool = False, danger: bool = False, tip: str = "") -> QPushButton:
    button = QPushButton(text)
    if primary:
        button.setProperty("primary", True)
    if danger:
        button.setProperty("danger", True)
    if tip:
        button.setToolTip(tip)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button


def set_state(label: QLabel, state: str) -> None:
    label.setProperty("state", state)
    repolish(label)


class Card(QFrame):
    def __init__(self, title: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setProperty("card", True)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(16, 14, 16, 14)
        self.body.setSpacing(8)
        if title:
            self.body.addWidget(make_label(title.upper(), "section"))

    def add(self, widget: QWidget, stretch: int = 0) -> QWidget:
        self.body.addWidget(widget, stretch)
        return widget


class StatTile(QFrame):
    """Title, one big value and a caption. Never shows a number it does not have."""

    def __init__(self, title: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setProperty("card", True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(2)
        self.title = make_label(title.upper(), "section")
        self.value = make_label(NA, "big")
        self.caption = make_label("", "muted")
        for w in (self.title, self.value, self.caption):
            lay.addWidget(w)

    def set(self, value: str, caption: str = "", state: str = "ok") -> None:
        self.value.setText(value)
        self.caption.setText(caption)
        set_state(self.value, state)


class SparkGraph(QWidget):
    """Bounded-history line graph (0..ymax). ``None`` values leave a visible gap."""

    def __init__(self, color: str = theme.CRIMSON, ymax: float = 100.0, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._color = QColor(color)
        self._ymax = ymax
        self._values: list[Optional[float]] = []
        self.setMinimumHeight(86)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)

    def set_values(self, values: Sequence[Optional[float]]) -> None:
        self._values = list(values)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(1, 4, -1, -4)
        p.setPen(QPen(QColor(theme.BORDER), 1))
        for frac in (0.0, 0.5, 1.0):
            y = r.top() + r.height() * frac
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
        vals = self._values
        if len(vals) < 2:
            p.setPen(QColor(theme.MUTED))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, "collecting data…")
            return
        n = max(len(vals), 2)
        step = r.width() / (self.maxlen_hint(n) - 1)
        x0 = r.right() - step * (len(vals) - 1)

        def pt(i: int, v: float) -> QPointF:
            return QPointF(x0 + i * step, r.bottom() - r.height() * max(0.0, min(self._ymax, v)) / self._ymax)

        segment: list[QPointF] = []
        segments: list[list[QPointF]] = []
        for i, v in enumerate(vals):
            if v is None:
                if len(segment) > 0:
                    segments.append(segment)
                segment = []
            else:
                segment.append(pt(i, v))
        if segment:
            segments.append(segment)
        for seg in segments:
            if len(seg) < 2:
                continue
            path = QPainterPath(seg[0])
            for q in seg[1:]:
                path.lineTo(q)
            fill = QPainterPath(path)
            fill.lineTo(QPointF(seg[-1].x(), r.bottom()))
            fill.lineTo(QPointF(seg[0].x(), r.bottom()))
            fill.closeSubpath()
            grad = QLinearGradient(0, r.top(), 0, r.bottom())
            top = QColor(self._color)
            top.setAlpha(90)
            bottom = QColor(self._color)
            bottom.setAlpha(0)
            grad.setColorAt(0, top)
            grad.setColorAt(1, bottom)
            p.fillPath(fill, grad)
            p.setPen(QPen(self._color, 1.8))
            p.drawPath(path)

    @staticmethod
    def maxlen_hint(n: int) -> int:
        from ..monitoring.history import DEFAULT_LENGTH

        return max(n, DEFAULT_LENGTH)


class NumItem(QTableWidgetItem):
    """Table item that sorts by a numeric key (missing values sort last)."""

    def __init__(self, text: str, key: Optional[float]) -> None:
        super().__init__(text)
        self._key = key
        self.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    def __lt__(self, other: QTableWidgetItem) -> bool:
        a = self._key if self._key is not None else -1.0
        b = getattr(other, "_key", None)
        b = b if b is not None else -1.0
        return a < b


def page_header(title: str, subtitle: str = "") -> QWidget:
    box = QWidget()
    lay = QVBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 4)
    lay.setSpacing(2)
    lay.addWidget(make_label(title, "title"))
    if subtitle:
        lay.addWidget(make_label(subtitle, "muted", wrap=True))
    return box


def hbox(*widgets: QWidget, stretch_last: bool = False, spacing: int = 8) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setSpacing(spacing)
    for w in widgets:
        lay.addWidget(w)
    if stretch_last:
        lay.addStretch(1)
    return lay
