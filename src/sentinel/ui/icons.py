"""The Sentinel emblem, drawn with QPainter so no binary asset is required at runtime."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap,
                           QRadialGradient)

from . import theme


def draw_emblem(p: QPainter, cx: float, cy: float, radius: float, *, pupil_scale: float = 1.0,
                bright: bool = False) -> None:
    """Gold ring + crimson 'eye' core. Used by the icon and the wallpaper centre."""
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    # outer gold ring
    p.setPen(QPen(theme.color(theme.GOLD), max(1.0, radius * 0.07)))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QPointF(cx, cy), radius, radius)
    # lens (eye) shape
    w, h = radius * 0.86, radius * 0.50
    lens = QPainterPath()
    lens.moveTo(cx - w, cy)
    lens.quadTo(cx, cy - h * 2, cx + w, cy)
    lens.quadTo(cx, cy + h * 2, cx - w, cy)
    p.setPen(QPen(theme.color(theme.GOLD, 220), max(1.0, radius * 0.045)))
    p.setBrush(QBrush(theme.color("#0c0c10")))
    p.drawPath(lens)
    # iris
    ir = radius * 0.34
    grad = QRadialGradient(QPointF(cx, cy), ir)
    grad.setColorAt(0.0, QColor(theme.CRIMSON_BRIGHT if bright else theme.CRIMSON))
    grad.setColorAt(0.75, QColor("#7a0a1c"))
    grad.setColorAt(1.0, QColor("#3a0610"))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(grad))
    p.drawEllipse(QPointF(cx, cy), ir, ir)
    # pupil
    pr = ir * 0.42 * pupil_scale
    p.setBrush(QBrush(QColor("#050506")))
    p.drawEllipse(QPointF(cx, cy), pr, pr)
    p.setPen(QPen(theme.color(theme.GOLD, 200), max(1.0, radius * 0.02)))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QPointF(cx, cy), ir, ir)


def make_pixmap(size: int) -> QPixmap:
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setBrush(QBrush(QColor(theme.BG)))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(QRectF(0, 0, size, size), size * 0.22, size * 0.22)
    draw_emblem(p, size / 2, size / 2, size * 0.40)
    p.end()
    return QPixmap.fromImage(img)


def make_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(make_pixmap(size))
    return icon
